package link

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/odessy/odessy/services/monitoring/internal/protocol"
)

var testSSHHostID = "host_" + strings.Repeat("a", 64)

func testSSHConfig() protocol.SSHConfig {
	return protocol.SSHConfig{
		Hostname: "192.0.2.10", User: "tester", Port: 22, HostKey: fixtureKey(),
		Binary: "/usr/bin/ssh", Alias: "jetson", HostID: testSSHHostID,
		DisplayName: "Jetson", FingerprintSHA256: fixtureFingerprint(),
		Generation: "ssh:SHA256:test", Monitoring: true,
	}
}

const testSSHStatus = "0.08 0.05 0.07 1/445 4705\n" +
	"---\n" +
	"Filesystem 1024-blocks Used Available Capacity Mounted on\n" +
	"/dev/nvme0n1p1 243521944 74926160 156152700 33% /\n" +
	"---\n" +
	"Linux\n" +
	"aarch64\n"

func TestSSHInventoryCarriesTheSelectedMethod(t *testing.T) {
	source, err := StartSSH(testSSHConfig())
	if err != nil {
		t.Fatal(err)
	}
	inventory, err := source.Inventory(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(inventory.Hosts) != 1 {
		t.Fatalf("hosts = %d", len(inventory.Hosts))
	}
	host := inventory.Hosts[0]
	if host.HostID != testSSHHostID || host.TrustState != protocol.TrustSSH ||
		host.MonitoringAccess != protocol.AccessAllowed ||
		host.FingerprintSHA256 != fixtureFingerprint() || host.Generation == "" {
		t.Fatalf("unexpected host: %+v", host)
	}

	connectionOnly := testSSHConfig()
	connectionOnly.Monitoring = false
	other, err := StartSSH(connectionOnly)
	if err != nil {
		t.Fatal(err)
	}
	inventory, _ = other.Inventory(context.Background())
	if inventory.Hosts[0].MonitoringAccess != protocol.AccessDisabled {
		t.Fatalf("connection-only access = %q", inventory.Hosts[0].MonitoringAccess)
	}
}

func TestSSHHeartbeatAndSampleUseTheFixedCommand(t *testing.T) {
	source, err := StartSSH(testSSHConfig())
	if err != nil {
		t.Fatal(err)
	}
	var commands []string
	source.runFn = func(_ context.Context, command string) (string, time.Duration, error) {
		commands = append(commands, command)
		if command == "printf odessy-ssh-ok" {
			return "odessy-ssh-ok", 5 * time.Millisecond, nil
		}
		return testSSHStatus, 7 * time.Millisecond, nil
	}
	ref := protocol.HostRef{HostID: testSSHHostID, Generation: "ssh:SHA256:test"}
	hb, err := source.Heartbeat(context.Background(), ref)
	if err != nil {
		t.Fatal(err)
	}
	if hb.HostID != ref.HostID || hb.Generation != ref.Generation || hb.BootID == "" ||
		hb.ObservedAt.IsZero() || hb.LatencyMS == nil {
		t.Fatalf("unexpected heartbeat: %+v", hb)
	}
	sample, err := source.Sample(context.Background(), ref,
		[]string{protocol.GroupCPU, protocol.GroupDisks, protocol.GroupMemory})
	if err != nil {
		t.Fatal(err)
	}
	if err := protocol.ValidateObservation(sample); err != nil {
		t.Fatalf("sample invalid: %v\n%+v", err, sample)
	}
	if sample.Sample.CPU == nil || sample.Sample.CPU.Load1 == nil ||
		*sample.Sample.CPU.Load1 != 0.08 {
		t.Fatalf("cpu = %+v", sample.Sample.CPU)
	}
	if sample.Sample.Disks == nil || len(sample.Sample.Disks.Disks) != 1 ||
		sample.Sample.Disks.Disks[0].UtilizationPercent == nil ||
		*sample.Sample.Disks.Disks[0].UtilizationPercent != 33 {
		t.Fatalf("disks = %+v", sample.Sample.Disks)
	}
	if sample.Sample.Memory == nil || sample.Sample.Memory.Status != protocol.CollectorUnsupported {
		t.Fatalf("memory = %+v", sample.Sample.Memory)
	}
	if len(commands) != 2 || !strings.Contains(commands[1], "df -P -k /") {
		t.Fatalf("commands = %q", commands)
	}
}

func TestSSHMalformedOrFailedTransportIsExplicit(t *testing.T) {
	source, err := StartSSH(testSSHConfig())
	if err != nil {
		t.Fatal(err)
	}
	source.runFn = func(_ context.Context, _ string) (string, time.Duration, error) {
		return "0.1 0.2 0.3\n", time.Millisecond, nil
	}
	if _, err := source.Sample(context.Background(),
		protocol.HostRef{HostID: testSSHHostID, Generation: "g"}, []string{protocol.GroupCPU}); err == nil {
		t.Fatal("malformed status must fail")
	}
	source.runFn = func(_ context.Context, _ string) (string, time.Duration, error) {
		return "", time.Millisecond, &Failure{Code: "ssh_timeout", Message: "timeout", Retryable: true}
	}
	if _, err := source.Heartbeat(context.Background(),
		protocol.HostRef{HostID: testSSHHostID, Generation: "g"}); err == nil {
		t.Fatal("transport failure must be returned")
	}
}

func TestSSHConfigRejectsInjectionAndInvalidHostIDs(t *testing.T) {
	for _, mutate := range []func(*protocol.SSHConfig){
		func(c *protocol.SSHConfig) { c.Alias = "-oProxyCommand=calc" },
		func(c *protocol.SSHConfig) { c.HostID = "host_ssh_test" },
		func(c *protocol.SSHConfig) { c.Binary = "ssh -oProxyCommand=calc" },
		func(c *protocol.SSHConfig) { c.FingerprintSHA256 = "SHA256:test\nother" },
	} {
		cfg := testSSHConfig()
		mutate(&cfg)
		if _, err := StartSSH(cfg); err == nil {
			t.Fatalf("invalid config accepted: %+v", cfg)
		}
	}
}

func TestBootstrapAcceptsSSHWithALinkBinary(t *testing.T) {
	cfg := testSSHConfig()
	frame := protocol.BootstrapFrame{
		SchemaVersion: protocol.SchemaVersion,
		Op:            "configure",
		Token:         strings.Repeat("t", 32),
		SSH:           &cfg,
		Settings:      protocol.DefaultSettings(),
	}
	if _, err := protocol.ValidateBootstrap(frame); err != nil {
		t.Fatalf("SSH bootstrap rejected: %v", err)
	}
	frame.LinkBinary = "/usr/bin/odessy-link"
	if _, err := protocol.ValidateBootstrap(frame); err != nil {
		t.Fatalf("coexisting link+SSH bootstrap rejected: %v", err)
	}
}

func TestSSHSampleIDsSurviveCollectorRestart(t *testing.T) {
	ids := make(map[string]bool)
	for i := 0; i < 2; i++ {
		source, err := StartSSH(testSSHConfig())
		if err != nil {
			t.Fatal(err)
		}
		source.runFn = func(context.Context, string) (string, time.Duration, error) {
			return testSSHStatus, time.Millisecond, nil
		}
		ref := protocol.HostRef{HostID: testSSHHostID, Generation: testSSHConfig().Generation}
		observation, err := source.Sample(context.Background(), ref, []string{protocol.GroupCPU})
		if err != nil {
			t.Fatal(err)
		}
		if ids[observation.Sample.ID] {
			t.Fatal("restarted collector reused persisted sample ID")
		}
		ids[observation.Sample.ID] = true
	}
}

func fixtureSSHKey() (string, string) {
	blob := append([]byte{0, 0, 0, 11}, []byte("ssh-ed25519")...)
	blob = append(blob, 0, 0, 0, 32)
	blob = append(blob, make([]byte, 32)...)
	sum := sha256.Sum256(blob)
	return "ssh-ed25519 " + base64.StdEncoding.EncodeToString(blob), "SHA256:" + base64.RawStdEncoding.EncodeToString(sum[:])
}

func fixtureKey() string         { key, _ := fixtureSSHKey(); return key }
func fixtureFingerprint() string { _, fp := fixtureSSHKey(); return fp }

func TestSSHRejectsKeyDifferentFromBootstrapFingerprint(t *testing.T) {
	cfg := testSSHConfig()
	cfg.FingerprintSHA256 = "SHA256:other-trusted-host"
	if _, err := StartSSH(cfg); err == nil {
		t.Fatal("mismatched bootstrap key accepted")
	}
}

func TestSSHPollPinsReviewedEndpointDespiteAliasChange(t *testing.T) {
	ssh, err := exec.LookPath("ssh")
	if err != nil {
		t.Skip("OpenSSH unavailable")
	}
	cfg := testSSHConfig()
	cfg.Binary = ssh
	cfg.ConfigPath = filepath.Join(t.TempDir(), "config")
	text := "Host jetson\n HostName 192.0.2.99\n User other\n Port 2223\n HostKeyAlias other-host\n"
	if err := os.WriteFile(cfg.ConfigPath, []byte(text), 0600); err != nil {
		t.Fatal(err)
	}
	source, err := StartSSH(cfg)
	if err != nil {
		t.Fatal(err)
	}
	known := filepath.Join(t.TempDir(), "known hosts")
	args := append([]string{"-G"}, source.commandArgs(known, "reviewed-key", "printf ok")...)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	output, err := exec.CommandContext(ctx, ssh, args...).Output()
	if err != nil {
		t.Fatal(err)
	}
	resolved := string(output)
	for _, want := range []string{"hostname 192.0.2.10", "user tester", "port 22",
		"hostkeyalias reviewed-key", "stricthostkeychecking true", "updatehostkeys false",
		"hostkeyalgorithms ssh-ed25519", "globalknownhostsfile none"} {
		if !strings.Contains(resolved, want) {
			t.Fatalf("missing pinned option %q", want)
		}
	}
	if !strings.Contains(resolved, filepath.ToSlash(known)) && !strings.Contains(resolved, known) {
		t.Fatal("private trust path was split or lost")
	}
}
