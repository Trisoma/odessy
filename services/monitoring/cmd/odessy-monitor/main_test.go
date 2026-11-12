package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/odessy/odessy/services/monitoring/internal/protocol"
)

func moduleRoot(t *testing.T) string {
	t.Helper()
	root, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	return filepath.Dir(filepath.Dir(root))
}

func buildBinary(t *testing.T, pkg, name string) string {
	t.Helper()
	out := filepath.Join(t.TempDir(), name)
	if strings.HasSuffix(out, filepath.Base(out)) && os.PathSeparator == '\\' && !strings.HasSuffix(out, ".exe") {
		out += ".exe"
	}
	cmd := exec.Command("go", "build", "-o", out, pkg)
	cmd.Dir = moduleRoot(t)
	if output, err := cmd.CombinedOutput(); err != nil {
		t.Fatalf("build %s: %v\n%s", pkg, err, output)
	}
	return out
}

func TestBinaryBootstrapSmokeWithFakeChild(t *testing.T) {
	if testing.Short() {
		t.Skip("binary smoke skipped in short mode")
	}
	workerName := "odessy-fakeworker-smoke"
	if os.PathSeparator == '\\' {
		workerName += ".exe"
	}
	monitor := buildBinary(t, "./cmd/odessy-monitor", "odessy-monitor")
	worker := buildBinary(t, "./internal/testsupport/fakeworker", strings.TrimSuffix(workerName, ".exe"))

	hostID := "host_" + strings.Repeat("a", 64)
	token := "smoketoken0123456789abcdef0123456789"

	stateDir := t.TempDir()
	cmd := exec.Command(monitor)
	cmd.Env = append(os.Environ(), "FAKEWORKER_HOST_ID="+hostID)
	stdin, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Start(); err != nil {
		t.Fatalf("start monitor: %v", err)
	}
	exited := make(chan error, 1)
	go func() { exited <- cmd.Wait() }()

	frame := protocol.BootstrapFrame{
		SchemaVersion: protocol.SchemaVersion,
		Op:            "configure",
		Token:         token,
		StateDir:      stateDir,
		LinkBinary:    worker,
		Settings: protocol.Settings{
			MonitoredHostIDs: []string{hostID},
			IncludeLocal:     true,
			Preset:           protocol.PresetDefault,
		},
	}
	raw, err := json.Marshal(frame)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := stdin.Write(append(raw, '\n')); err != nil {
		t.Fatalf("write bootstrap: %v", err)
	}

	reader := bufio.NewReader(stdout)
	line, err := reader.ReadString('\n')
	if err != nil {
		t.Fatalf("readiness frame missing: %v (stderr=%s)", err, stderr.String())
	}
	var ready protocol.ReadyFrame
	if err := json.Unmarshal([]byte(line), &ready); err != nil {
		t.Fatalf("readiness frame invalid: %v (%q)", err, line)
	}
	if ready.Op != "ready" || ready.SchemaVersion != protocol.SchemaVersion {
		t.Fatalf("unexpected readiness frame %+v", ready)
	}
	if !strings.HasPrefix(ready.BaseURL, "http://127.0.0.1:") {
		t.Fatalf("monitor must bind loopback only, got %q", ready.BaseURL)
	}
	if strings.Contains(line, token) {
		t.Fatal("readiness frame must never contain the token")
	}

	client := &http.Client{Timeout: 2 * time.Second}
	do := func(method, path, bearer, body string) (*http.Response, []byte) {
		var payload io.Reader
		if body != "" {
			payload = strings.NewReader(body)
		}
		req, err := http.NewRequest(method, ready.BaseURL+path, payload)
		if err != nil {
			t.Fatal(err)
		}
		if bearer != "" {
			req.Header.Set("Authorization", "Bearer "+bearer)
		}
		resp, err := client.Do(req)
		if err != nil {
			t.Fatalf("%s %s: %v", method, path, err)
		}
		defer resp.Body.Close()
		data, _ := io.ReadAll(resp.Body)
		return resp, data
	}

	if resp, _ := do(http.MethodGet, "/internal/v1/monitoring", "", ""); resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("wrong token must fail, got %d", resp.StatusCode)
	}
	if resp, _ := do(http.MethodGet, "/internal/v1/monitoring", "wrong-token", ""); resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("wrong token must fail, got %d", resp.StatusCode)
	}

	deadline := time.Now().Add(8 * time.Second)
	var hostsBody []byte
	for time.Now().Before(deadline) {
		resp, data := do(http.MethodGet, "/internal/v1/hosts", token, "")
		if resp.StatusCode != http.StatusOK {
			t.Fatalf("host list failed: %d %s", resp.StatusCode, data)
		}
		if bytes.Contains(data, []byte(hostID)) {
			hostsBody = data
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	if hostsBody == nil {
		t.Fatalf("host never appeared through the fake worker (stderr=%s)", stderr.String())
	}

	runtimeBody := fmt.Sprintf(`{"schema_version":1,"host_id":%q,"generation":"g1","source_id":"py-smoke","sequence":1,"observed_at":%q,"services":[{"name":"mcp_server","status":"healthy"}]}`,
		hostID, time.Now().UTC().Format(time.RFC3339))
	if resp, data := do(http.MethodPost, "/internal/v1/runtime-observations", token, runtimeBody); resp.StatusCode != http.StatusAccepted {
		t.Fatalf("runtime ingest failed: %d %s", resp.StatusCode, data)
	}

	resp, data := do(http.MethodGet, "/internal/v1/events", token, "")
	_ = resp
	_ = data
	req, _ := http.NewRequest(http.MethodGet, ready.BaseURL+"/internal/v1/events", nil)
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Last-Event-ID", "stale-epoch:9")
	sse, err := client.Do(req)
	if err != nil {
		t.Fatalf("sse: %v", err)
	}
	sseBody, _ := io.ReadAll(io.LimitReader(sse.Body, 4096))
	sse.Body.Close()
	if !bytes.Contains(sseBody, []byte("resync_required")) {
		t.Fatalf("expected resync_required for an unknown epoch, got %q", string(sseBody))
	}

	// Close stdin: the service must shut down and reap only its own child.
	_ = stdin.Close()
	select {
	case err := <-exited:
		if err != nil {
			t.Fatalf("monitor exited nonzero: %v (stderr=%s)", err, stderr.String())
		}
	case <-time.After(10 * time.Second):
		_ = cmd.Process.Kill()
		t.Fatalf("monitor did not shut down after stdin EOF (stderr=%s)", stderr.String())
	}

	if left := leftoverChildren(t, strings.TrimSuffix(workerName, ".exe")); left {
		t.Fatal("telemetry worker child survived shutdown")
	}
}

func TestBinaryBootstrapComposesPairedAndSSH(t *testing.T) {
	if testing.Short() {
		t.Skip("binary smoke skipped in short mode")
	}
	workerName := "odessy-fakeworker-compose"
	sshName := "odessy-fakessh-compose"
	if os.PathSeparator == '\\' {
		workerName += ".exe"
		sshName += ".exe"
	}
	monitor := buildBinary(t, "./cmd/odessy-monitor", "odessy-monitor")
	worker := buildBinary(t, "./internal/testsupport/fakeworker", strings.TrimSuffix(workerName, ".exe"))
	fakeSSH := buildBinary(t, "./internal/testsupport/fakessh", strings.TrimSuffix(sshName, ".exe"))

	pairedID := "host_" + strings.Repeat("a", 64)
	sshID := "host_" + strings.Repeat("b", 64)
	token := "composetoken0123456789abcdef0123456789"
	cmd := exec.Command(monitor)
	cmd.Env = append(os.Environ(), "FAKEWORKER_HOST_ID="+pairedID)
	stdin, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	exited := make(chan error, 1)
	go func() { exited <- cmd.Wait() }()

	sshConfig := protocol.SSHConfig{
		Binary: fakeSSH, Alias: "fixture-jetson", HostID: sshID,
		DisplayName: "Fixture Jetson", FingerprintSHA256: "SHA256:fixture",
		Generation: "ssh:SHA256:fixture", Monitoring: true,
	}
	frame := protocol.BootstrapFrame{
		SchemaVersion: protocol.SchemaVersion,
		Op:            "configure",
		Token:         token,
		StateDir:      t.TempDir(),
		LinkBinary:    worker,
		SSH:           &sshConfig,
		Settings: protocol.Settings{
			MonitoredHostIDs: []string{pairedID, sshID},
			IncludeLocal:     false,
			Preset:           protocol.PresetDefault,
		},
	}
	raw, _ := json.Marshal(frame)
	if _, err := stdin.Write(append(raw, '\n')); err != nil {
		t.Fatal(err)
	}
	reader := bufio.NewReader(stdout)
	line, err := reader.ReadString('\n')
	if err != nil {
		t.Fatalf("readiness: %v (stderr=%s)", err, stderr.String())
	}
	var ready protocol.ReadyFrame
	if err := json.Unmarshal([]byte(line), &ready); err != nil {
		t.Fatal(err)
	}
	client := &http.Client{Timeout: 2 * time.Second}
	deadline := time.Now().Add(8 * time.Second)
	var body []byte
	for time.Now().Before(deadline) {
		req, _ := http.NewRequest(http.MethodGet, ready.BaseURL+"/internal/v1/hosts", nil)
		req.Header.Set("Authorization", "Bearer "+token)
		resp, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		data, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if bytes.Contains(data, []byte(pairedID)) && bytes.Contains(data, []byte(sshID)) {
			body = data
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	if body == nil {
		t.Fatalf("both providers never shared inventory (stderr=%s)", stderr.String())
	}
	_ = stdin.Close()
	select {
	case err := <-exited:
		if err != nil {
			t.Fatalf("monitor exited nonzero: %v (stderr=%s)", err, stderr.String())
		}
	case <-time.After(10 * time.Second):
		_ = cmd.Process.Kill()
		t.Fatal("monitor did not shut down")
	}
}
func TestStartTelemetryKeepsWorkingProviderWhenOtherFails(t *testing.T) {
	sshName := "odessy-fakessh-fallback"
	if os.PathSeparator == '\\' {
		sshName += ".exe"
	}
	fakeSSH := buildBinary(t, "./internal/testsupport/fakessh", strings.TrimSuffix(sshName, ".exe"))
	sshID := "host_" + strings.Repeat("d", 64)
	sshConfig := protocol.SSHConfig{
		Binary: fakeSSH, Alias: "fixture", HostID: sshID,
		FingerprintSHA256: "SHA256:fixture", Generation: "ssh:fixture", Monitoring: true,
	}
	frame := protocol.BootstrapFrame{
		SchemaVersion: protocol.SchemaVersion, Op: "configure",
		Token: strings.Repeat("t", 32), LinkBinary: filepath.Join(t.TempDir(), "missing-link"),
		SSH: &sshConfig, Settings: protocol.DefaultSettings(),
	}
	worker, err := startTelemetry(frame)
	if err != nil {
		t.Fatal(err)
	}
	defer worker.Close()
	inventory, err := worker.Inventory(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(inventory.Hosts) != 1 || inventory.Hosts[0].HostID != sshID {
		t.Fatalf("SSH provider was lost: %+v", inventory)
	}
}

func TestStartTelemetryKeepsPairedWhenSSHConfigFails(t *testing.T) {
	workerName := "odessy-fakeworker-sshfail"
	if os.PathSeparator == '\\' {
		workerName += ".exe"
	}
	workerPath := buildBinary(t, "./internal/testsupport/fakeworker", strings.TrimSuffix(workerName, ".exe"))
	pairedID := "host_" + strings.Repeat("e", 64)
	sshConfig := protocol.SSHConfig{
		Binary: "/usr/bin/ssh", Alias: "fixture", HostID: "host_bad",
		FingerprintSHA256: "SHA256:fixture", Generation: "ssh:fixture", Monitoring: true,
	}
	frame := protocol.BootstrapFrame{
		SchemaVersion: protocol.SchemaVersion, Op: "configure",
		Token: strings.Repeat("t", 32), LinkBinary: workerPath,
		SSH: &sshConfig, Settings: protocol.DefaultSettings(),
	}
	t.Setenv("FAKEWORKER_HOST_ID", pairedID)
	worker, err := startTelemetry(frame)
	if err != nil {
		t.Fatal(err)
	}
	defer worker.Close()

	inventory, err := worker.Inventory(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(inventory.Hosts) != 1 || inventory.Hosts[0].HostID != pairedID {
		t.Fatalf("paired provider was lost: %+v", inventory)
	}
}

func leftoverChildren(t *testing.T, name string) bool {
	t.Helper()
	out, err := exec.Command("tasklist", "/FO", "CSV", "/NH").Output()
	if err != nil {
		return false
	}
	return bytes.Contains(bytes.ToLower(out), bytes.ToLower([]byte(name)))
}
