package link

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/odessy/odessy/services/monitoring/internal/protocol"
)

var testSSHHostID = "host_" + strings.Repeat("a", 64)

func testSSHConfig() protocol.SSHConfig {
	return protocol.SSHConfig{
		Binary: "/usr/bin/ssh", Alias: "jetson", HostID: testSSHHostID,
		DisplayName: "Jetson", FingerprintSHA256: "SHA256:test",
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
		host.FingerprintSHA256 != "SHA256:test" || host.Generation == "" {
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

func TestBootstrapAcceptsSSHWithoutALinkBinary(t *testing.T) {
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
	if _, err := protocol.ValidateBootstrap(frame); err == nil {
		t.Fatal("link_binary and ssh must be mutually exclusive")
	}
}
