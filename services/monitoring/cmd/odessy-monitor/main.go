// Command odessy-monitor is the central Go monitoring service. It is started by
// Python with a private bootstrap frame on stdin and reports readiness once.
package main

import (
	"bufio"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/odessy/odessy/services/monitoring/internal/events"
	"github.com/odessy/odessy/services/monitoring/internal/fleet"
	"github.com/odessy/odessy/services/monitoring/internal/history"
	"github.com/odessy/odessy/services/monitoring/internal/httpapi"
	"github.com/odessy/odessy/services/monitoring/internal/link"
	"github.com/odessy/odessy/services/monitoring/internal/protocol"
	"github.com/odessy/odessy/services/monitoring/internal/runtime"
)

type systemClock struct{}

func (systemClock) Now() time.Time { return time.Now() }

func main() {
	if err := run(); err != nil {
		fmt.Fprintf(os.Stderr, "odessy-monitor: %v\n", errors.Unwrap(err))
		os.Exit(1)
	}
}

func run() error {
	frame, err := readBootstrap(os.Stdin)
	if err != nil {
		return fmt.Errorf("bootstrap rejected: %w", err)
	}
	settings, err := protocol.ValidateBootstrap(frame)
	if err != nil {
		return fmt.Errorf("bootstrap rejected: %w", err)
	}
	epoch, err := newEpoch()
	if err != nil {
		return err
	}

	clock := systemClock{}
	intervals := settings.Intervals()
	bus := events.New(epoch)
	memory := history.NewMemory(protocol.MemoryHorizon, protocol.MemoryRingBytes)

	var store *history.Store
	if frame.StateDir != "" {
		store, err = history.Open(frame.StateDir)
		if err == nil {
			defer store.Close()
		} else {
			store = nil
		}
	}

	var f *fleet.Fleet
	sink := func(ev fleet.Event) {
		bus.Publish(ev.Kind, ev.HostID, ev.Payload)
		if ev.Kind != fleet.EventHostMetrics || ev.HostID == "" {
			return
		}
		payload, ok := ev.Payload.(map[string]any)
		if !ok {
			return
		}
		sample, ok := payload["sample"].(protocol.Sample)
		if !ok {
			return
		}
		memory.Append(ev.HostID, sample)
		if store != nil {
			store.Enqueue(ev.HostID, sample)
		}
	}
	f = fleet.New(clock, settings, intervals, sink)
	if frame.StateDir != "" && store == nil {
		f.MarkDegraded("history database unavailable")
	}

	worker, err := link.StartProcess(context.Background(), frame.LinkBinary)
	if err != nil {
		return fmt.Errorf("telemetry worker unavailable: %w", err)
	}
	defer worker.Close()

	scheduler := fleet.NewScheduler(worker, f, clock, intervals)
	ingestor := runtime.New(f, clock.Now)

	server := httpapi.New(httpapi.Deps{
		Token:     frame.Token,
		Fleet:     f,
		Bus:       bus,
		Memory:    memory,
		Store:     store,
		Ingestor:  ingestor,
		Clock:     clock,
		Intervals: intervals,
	})

	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return fmt.Errorf("bind loopback failed: %w", err)
	}
	httpServer := &http.Server{
		Handler:           server.Handler(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       30 * time.Second,
		WriteTimeout:      0,
		MaxHeaderBytes:    64 << 10,
	}

	runCtx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go scheduler.Run(runCtx)
	go func() {
		if serveErr := httpServer.Serve(listener); serveErr != nil && !errors.Is(serveErr, http.ErrServerClosed) {
			cancel()
		}
	}()

	ready := protocol.ReadyFrame{SchemaVersion: protocol.SchemaVersion, Op: "ready", BaseURL: "http://" + listener.Addr().String(), Epoch: epoch}
	enc := json.NewEncoder(os.Stdout)
	if err := enc.Encode(ready); err != nil {
		return fmt.Errorf("readiness frame failed: %w", err)
	}

	signals := make(chan os.Signal, 1)
	signal.Notify(signals, os.Interrupt, syscall.SIGTERM)

	stdinClosed := make(chan struct{})
	go func() {
		_, _ = io.Copy(io.Discard, os.Stdin)
		close(stdinClosed)
	}()

	select {
	case <-signals:
	case <-stdinClosed:
	case <-runCtx.Done():
	}

	cancel()
	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer shutdownCancel()
	_ = httpServer.Shutdown(shutdownCtx)
	scheduler.Close()
	f.MarkDegraded("")
	return nil
}

func readBootstrap(r io.Reader) (protocol.BootstrapFrame, error) {
	scanner := bufio.NewScanner(r)
	scanner.Buffer(make([]byte, 4096), protocol.MaxBootstrapFrameBytes)
	if !scanner.Scan() {
		if err := scanner.Err(); err != nil {
			return protocol.BootstrapFrame{}, fmt.Errorf("%w: %v", protocol.ErrResourceLimit, err)
		}
		return protocol.BootstrapFrame{}, errors.New("no bootstrap frame on stdin")
	}
	var frame protocol.BootstrapFrame
	if err := protocol.DecodeBounded(scanner.Bytes(), protocol.MaxBootstrapFrameBytes, &frame); err != nil {
		return protocol.BootstrapFrame{}, err
	}
	return frame, nil
}

func newEpoch() (string, error) {
	buf := make([]byte, 8)
	if _, err := rand.Read(buf); err != nil {
		return "", err
	}
	return hex.EncodeToString(buf), nil
}
