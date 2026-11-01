package link

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/odessy/odessy/services/monitoring/internal/protocol"
)

func buildFakeWorker(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	dir = filepath.Join(dir, "profile~1")
	if err := os.Mkdir(dir, 0700); err != nil {
		t.Fatal(err)
	}
	out := filepath.Join(dir, "fakeworker")
	if strings.HasSuffix(strings.ToLower(out), "fakeworker") && os.PathSeparator == '\\' {
		out += ".exe"
	}
	cmd := exec.Command("go", "build", "-o", out, "./internal/testsupport/fakeworker")
	cmd.Dir = moduleRoot(t)
	if output, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("build fake worker: %v\n%s", err, output)
	}
	return out
}

func moduleRoot(t *testing.T) string {
	t.Helper()
	root, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	return filepath.Dir(filepath.Dir(root))
}

func TestProcessLinkRoundTrip(t *testing.T) {
	binary := buildFakeWorker(t)
	hostID := "host_" + strings.Repeat("a", 64)
	t.Setenv("FAKEWORKER_HOST_ID", hostID)
	t.Setenv("FAKEWORKER_GARBAGE_FIRST", "1")

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	l, err := StartProcess(ctx, binary)
	if err != nil {
		t.Fatalf("start worker: %v", err)
	}
	defer l.Close()

	inv, err := l.Inventory(context.Background())
	if err != nil {
		t.Fatalf("inventory: %v", err)
	}
	if inv.Revision == "" || len(inv.Hosts) != 1 || inv.Hosts[0].HostID != hostID {
		t.Fatalf("unexpected inventory %+v", inv)
	}

	ref := protocol.HostRef{HostID: hostID, Generation: "g1"}
	hb, err := l.Heartbeat(context.Background(), ref)
	if err != nil {
		t.Fatalf("heartbeat: %v", err)
	}
	if hb.HostID != hostID || hb.Generation != "g1" {
		t.Fatalf("unexpected heartbeat %+v", hb)
	}

	obs, err := l.Sample(context.Background(), ref, []string{protocol.GroupCPU, protocol.GroupMemory})
	if err != nil {
		t.Fatalf("sample: %v", err)
	}
	if obs.HostID != hostID || obs.CPU == nil {
		t.Fatalf("unexpected observation %+v", obs)
	}

	// Out-of-order replies are matched by id: interleave two calls.
	type result struct {
		obs protocol.Observation
		err error
	}
	one := make(chan result, 1)
	two := make(chan result, 1)
	go func() { o, e := l.Sample(context.Background(), ref, []string{protocol.GroupCPU}); one <- result{o, e} }()
	go func() {
		o, e := l.Sample(context.Background(), ref, []string{protocol.GroupDisks})
		two <- result{o, e}
	}()
	if r := <-one; r.err != nil {
		t.Fatalf("interleaved sample one: %v", r.err)
	}
	if r := <-two; r.err != nil {
		t.Fatalf("interleaved sample two: %v", r.err)
	}

	if _, err := l.Sample(context.Background(), ref, []string{"bogus"}); err == nil {
		t.Fatal("unknown group must be rejected before dispatch")
	}
}

func TestProcessLinkDeadlineDiscardsLateResult(t *testing.T) {
	binary := buildFakeWorker(t)
	t.Setenv("FAKEWORKER_DELAY_MS", "400")

	l, err := StartProcess(context.Background(), binary)
	if err != nil {
		t.Fatalf("start worker: %v", err)
	}
	defer l.Close()

	ref := protocol.HostRef{HostID: "host_" + strings.Repeat("a", 64), Generation: "g1"}
	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	_, err = l.Heartbeat(ctx, ref)
	if err == nil {
		t.Fatal("expected a deadline failure")
	}
	if !IsRetryable(err) {
		t.Fatalf("deadline failures are retryable transport failures, got %v", err)
	}
}

func TestProcessLinkRejectsUnsafeBinary(t *testing.T) {
	if _, err := StartProcess(context.Background(), "cmd /c calc"); err == nil {
		t.Fatal("shell-ish binary strings must be rejected")
	}
	if _, err := StartProcess(context.Background(), "odessy-link"); err == nil {
		t.Fatal("bare command names must be rejected")
	}
}
