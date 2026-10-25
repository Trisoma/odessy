// Package link owns the private bridge to the packaged Rust telemetry worker.
package link

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os/exec"
	"sync"
	"sync/atomic"
	"time"

	"github.com/odessy/odessy/services/monitoring/internal/protocol"
)

// Link is the only way Go reaches host identity, heartbeats and samples.
type Link interface {
	Inventory(context.Context) (protocol.Inventory, error)
	Heartbeat(context.Context, protocol.HostRef) (protocol.Heartbeat, error)
	Sample(context.Context, protocol.HostRef, []string) (protocol.Observation, error)
	Close() error
}

// Failure is a sanitized worker error.
type Failure struct {
	Code      string
	Message   string
	Retryable bool
}

func (f *Failure) Error() string { return f.Code + ": " + f.Message }

// IsRetryable reports whether a transport-level failure may be retried.
func IsRetryable(err error) bool {
	var f *Failure
	if errors.As(err, &f) {
		return f.Retryable
	}
	return false
}

// IsIdentityFailure reports failures that must wait for inventory/config change.
func IsIdentityFailure(err error) bool {
	var f *Failure
	if !errors.As(err, &f) {
		return false
	}
	switch f.Code {
	case "identity_mismatch", "generation_mismatch", "permission_denied", "not_paired", "revoked", "unknown_host":
		return true
	}
	return false
}

// ProcessLink speaks bounded JSONL to `link_binary telemetry worker`.
type ProcessLink struct {
	cmd      *exec.Cmd
	stdin    io.WriteCloser
	deadline time.Duration

	mu      sync.Mutex
	pending map[string]chan protocol.WorkerResponse
	nextID  atomic.Uint64
	closed  atomic.Bool
	readErr error

	wg sync.WaitGroup
}

// StartProcess launches the Rust worker without a shell and fixed arguments.
func StartProcess(ctx context.Context, binary string) (*ProcessLink, error) {
	if err := protocol.ValidateLinkBinary(binary); err != nil {
		return nil, err
	}
	cmd := exec.CommandContext(ctx, binary, "telemetry", "worker")
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	cmd.Stderr = &boundedWriter{limit: 4096}
	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("start telemetry worker: %w", err)
	}
	p := &ProcessLink{
		cmd:      cmd,
		stdin:    stdin,
		deadline: protocol.RequestDeadline,
		pending:  make(map[string]chan protocol.WorkerResponse),
	}
	p.wg.Add(1)
	go p.readLoop(stdout)
	return p, nil
}

// boundedWriter keeps only the last limit bytes of child stderr.
type boundedWriter struct {
	limit int
	buf   []byte
}

func (b *boundedWriter) Write(p []byte) (int, error) {
	b.buf = append(b.buf, p...)
	if len(b.buf) > b.limit {
		b.buf = b.buf[len(b.buf)-b.limit:]
	}
	return len(p), nil
}

func (p *ProcessLink) readLoop(r io.Reader) {
	defer p.wg.Done()
	scanner := bufio.NewScanner(r)
	scanner.Buffer(make([]byte, 4096), protocol.MaxWorkerFrameBytes)
	for scanner.Scan() {
		var resp protocol.WorkerResponse
		if err := json.Unmarshal(scanner.Bytes(), &resp); err != nil {
			continue
		}
		p.mu.Lock()
		ch, ok := p.pending[resp.ID]
		if ok {
			delete(p.pending, resp.ID)
		}
		p.mu.Unlock()
		if ok {
			select {
			case ch <- resp:
			default:
			}
		}
	}
	p.mu.Lock()
	p.readErr = scanner.Err()
	pending := p.pending
	p.pending = make(map[string]chan protocol.WorkerResponse)
	p.mu.Unlock()
	for _, ch := range pending {
		close(ch)
	}
}

func (p *ProcessLink) call(ctx context.Context, req protocol.WorkerRequest) (json.RawMessage, error) {
	if p.closed.Load() {
		return nil, &Failure{Code: "worker_closed", Message: "telemetry worker closed", Retryable: true}
	}
	req.SchemaVersion = protocol.SchemaVersion
	req.ID = fmt.Sprintf("req-%d", p.nextID.Add(1))
	line, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}
	if len(line)+1 > protocol.MaxWorkerFrameBytes {
		return nil, &Failure{Code: "frame_too_large", Message: "request frame exceeds limit"}
	}
	ch := make(chan protocol.WorkerResponse, 1)
	p.mu.Lock()
	p.pending[req.ID] = ch
	p.mu.Unlock()

	if _, err := p.stdin.Write(append(line, '\n')); err != nil {
		p.forget(req.ID)
		return nil, &Failure{Code: "transport", Message: "worker write failed", Retryable: true}
	}

	if _, ok := ctx.Deadline(); !ok {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, p.deadline)
		defer cancel()
	}
	select {
	case <-ctx.Done():
		p.forget(req.ID)
		p.sendCancel(req.ID)
		return nil, &Failure{Code: "deadline_exceeded", Message: "request deadline exceeded", Retryable: true}
	case resp, ok := <-ch:
		if !ok {
			return nil, &Failure{Code: "worker_down", Message: "telemetry worker stopped", Retryable: true}
		}
		if resp.SchemaVersion != protocol.SchemaVersion {
			return nil, &Failure{Code: "unsupported_version", Message: "worker schema version rejected"}
		}
		if !resp.OK {
			f := &Failure{Code: "worker_error", Message: "worker reported failure"}
			if resp.Error != nil {
				f.Code = resp.Error.Code
				f.Message = resp.Error.Message
				f.Retryable = resp.Error.Retryable
			}
			return nil, f
		}
		return resp.Result, nil
	}
}

func (p *ProcessLink) forget(id string) {
	p.mu.Lock()
	delete(p.pending, id)
	p.mu.Unlock()
}

func (p *ProcessLink) sendCancel(target string) {
	req := protocol.WorkerRequest{SchemaVersion: protocol.SchemaVersion, ID: fmt.Sprintf("ctl-%d", p.nextID.Add(1)), Op: protocol.OpCancel, TargetID: target}
	line, err := json.Marshal(req)
	if err != nil {
		return
	}
	_, _ = p.stdin.Write(append(line, '\n'))
}

// Inventory requests the authoritative registry view.
func (p *ProcessLink) Inventory(ctx context.Context) (protocol.Inventory, error) {
	raw, err := p.call(ctx, protocol.WorkerRequest{Op: protocol.OpInventory})
	if err != nil {
		return protocol.Inventory{}, err
	}
	var inv protocol.Inventory
	if err := protocol.DecodeBounded(raw, protocol.MaxWorkerFrameBytes, &inv); err != nil {
		return protocol.Inventory{}, err
	}
	if err := protocol.ValidateInventory(inv); err != nil {
		return protocol.Inventory{}, err
	}
	return inv, nil
}

// Heartbeat requests an authenticated liveness check for one host.
func (p *ProcessLink) Heartbeat(ctx context.Context, ref protocol.HostRef) (protocol.Heartbeat, error) {
	raw, err := p.call(ctx, protocol.WorkerRequest{Op: protocol.OpHeartbeat, HostID: ref.HostID, Generation: ref.Generation})
	if err != nil {
		return protocol.Heartbeat{}, err
	}
	var hb protocol.Heartbeat
	if err := protocol.DecodeBounded(raw, protocol.MaxWorkerFrameBytes, &hb); err != nil {
		return protocol.Heartbeat{}, err
	}
	if hb.HostID == "" {
		hb.HostID = ref.HostID
	}
	if hb.Generation == "" {
		hb.Generation = ref.Generation
	}
	if err := protocol.ValidateHeartbeat(hb); err != nil {
		return protocol.Heartbeat{}, err
	}
	return hb, nil
}

// Sample requests the fixed group set for one host.
func (p *ProcessLink) Sample(ctx context.Context, ref protocol.HostRef, groups []string) (protocol.Observation, error) {
	for _, g := range groups {
		if !protocol.ValidGroup(g) {
			return protocol.Observation{}, fmt.Errorf("%w: group %q", protocol.ErrInvalid, g)
		}
	}
	raw, err := p.call(ctx, protocol.WorkerRequest{Op: protocol.OpSample, HostID: ref.HostID, Generation: ref.Generation, Groups: groups})
	if err != nil {
		return protocol.Observation{}, err
	}
	var obs protocol.Observation
	if err := protocol.DecodeBounded(raw, protocol.MaxWorkerFrameBytes, &obs); err != nil {
		return protocol.Observation{}, err
	}
	if obs.HostID == "" {
		obs.HostID = ref.HostID
	}
	if obs.Generation == "" {
		obs.Generation = ref.Generation
	}
	if err := protocol.ValidateObservation(obs); err != nil {
		return protocol.Observation{}, err
	}
	return obs, nil
}

// Close drains the worker within a bounded grace period, then kills only the
// child this process owns.
func (p *ProcessLink) Close() error {
	if p.closed.Swap(true) {
		return nil
	}
	_ = p.stdin.Close()
	done := make(chan struct{})
	go func() {
		p.wg.Wait()
		_ = p.cmd.Wait()
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		if p.cmd.Process != nil {
			_ = p.cmd.Process.Kill()
		}
		<-done
	}
	return nil
}
