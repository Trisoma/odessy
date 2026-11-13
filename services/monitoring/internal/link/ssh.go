package link

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"time"

	"github.com/odessy/odessy/services/monitoring/internal/protocol"
)

// SSHLink is the smallest transport adapter that lets the existing Go fleet
// scheduler, history and event bus collect one OpenSSH computer. It does not
// own polling, freshness or events; it only answers Inventory/Heartbeat/Sample.
type SSHLink struct {
	cfg      protocol.SSHConfig
	bootID   string
	runID    string
	sequence atomic.Uint64
	runFn    func(context.Context, string) (string, time.Duration, error)
}

// StartSSH validates a selected SSH transport. The alias and fingerprint were
// reviewed by the application; this adapter never accepts a host key.
func StartSSH(cfg protocol.SSHConfig) (*SSHLink, error) {
	if err := protocol.ValidateSSHConfig(cfg); err != nil {
		return nil, err
	}
	key := strings.Fields(cfg.HostKey)
	if len(key) != 2 || strings.ContainsAny(cfg.HostKey, "\r\n") {
		return nil, fmt.Errorf("SSH trusted public key is required")
	}
	blob, err := base64.StdEncoding.DecodeString(key[1])
	if err != nil {
		return nil, fmt.Errorf("invalid SSH public key")
	}
	if len(blob) < 4 || int(binary.BigEndian.Uint32(blob[:4])) > len(blob)-4 ||
		string(blob[4:4+int(binary.BigEndian.Uint32(blob[:4]))]) != key[0] ||
		!(key[0] == "ssh-ed25519" || key[0] == "ssh-rsa" || strings.HasPrefix(key[0], "ecdsa-sha2-")) {
		return nil, fmt.Errorf("unsupported SSH public key encoding")
	}
	digest := sha256.Sum256(blob)
	if "SHA256:"+base64.RawStdEncoding.EncodeToString(digest[:]) != cfg.FingerprintSHA256 {
		return nil, fmt.Errorf("SSH public key does not match reviewed fingerprint")
	}
	if cfg.Hostname == "" || cfg.User == "" || cfg.Port < 1 || cfg.Port > 65535 ||
		strings.ContainsAny(cfg.Hostname+cfg.User, " \t\r\n\"") || strings.HasPrefix(cfg.Hostname, "-") {
		return nil, fmt.Errorf("SSH endpoint is invalid")
	}
	var nonce [16]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		return nil, fmt.Errorf("SSH collector identity: %w", err)
	}
	link := &SSHLink{cfg: cfg, bootID: sshBootID(cfg.HostID), runID: hex.EncodeToString(nonce[:])}
	link.runFn = link.run
	return link, nil
}

func (s *SSHLink) Inventory(context.Context) (protocol.Inventory, error) {
	access := protocol.AccessDisabled
	if s.cfg.Monitoring {
		access = protocol.AccessAllowed
	}
	display := s.cfg.DisplayName
	if display == "" {
		display = s.cfg.Alias
	}
	host := protocol.HostIdentity{
		HostID:            s.cfg.HostID,
		InstallationID:    "ssh:" + s.cfg.Alias,
		FingerprintSHA256: s.cfg.FingerprintSHA256,
		Alias:             display,
		TrustState:        protocol.TrustSSH,
		MonitoringAccess:  access,
		Generation:        s.cfg.Generation,
	}
	return protocol.Inventory{Revision: s.cfg.Generation, Hosts: []protocol.HostIdentity{host}}, nil
}

func (s *SSHLink) Heartbeat(ctx context.Context, ref protocol.HostRef) (protocol.Heartbeat, error) {
	stdout, elapsed, err := s.runFn(ctx, "printf odessy-ssh-ok")
	if err != nil {
		return protocol.Heartbeat{}, err
	}
	if strings.TrimSpace(stdout) != "odessy-ssh-ok" {
		return protocol.Heartbeat{}, &Failure{Code: "ssh_unexpected_output", Message: "SSH probe returned unexpected output"}
	}
	now := time.Now().UTC()
	latency := float64(elapsed.Milliseconds())
	seq := s.sequence.Add(1)
	return protocol.Heartbeat{
		HostID: ref.HostID, Generation: ref.Generation, BootID: s.bootID,
		Sequence: seq, ObservedAt: now, ReceivedAt: now, LatencyMS: &latency,
	}, nil
}

func (s *SSHLink) Sample(ctx context.Context, ref protocol.HostRef, groups []string) (protocol.Observation, error) {
	stdout, elapsed, err := s.runFn(ctx, statusCommand)
	if err != nil {
		return protocol.Observation{}, err
	}
	status, err := parseSSHStatus(stdout)
	if err != nil {
		return protocol.Observation{}, err
	}
	now := time.Now().UTC()
	seq := s.sequence.Add(1)
	sample := protocol.Sample{
		SchemaVersion: protocol.SchemaVersion,
		ID:            fmt.Sprintf("ssh-%s-%d", s.runID, seq),
		BootID:        s.bootID,
		Sequence:      seq,
		ObservedAt:    now,
		ReceivedAt:    now,
		IntervalMS:    10000,
		Scope:         "host",
	}
	requested := make(map[string]bool, len(groups))
	for _, group := range groups {
		requested[group] = true
	}
	latency := float64(elapsed.Milliseconds())
	if requested[protocol.GroupCPU] {
		sample.CPU = &protocol.CPUGroup{
			Group: protocol.Group{Status: protocol.CollectorOK, ObservedAt: &now,
				CollectionDurationMS: &latency},
			Load1: &status.load1, Load5: &status.load5, Load15: &status.load15,
		}
	}
	if requested[protocol.GroupDisks] {
		total := float64(status.totalBytes)
		used := float64(status.usedBytes)
		available := float64(status.availableBytes)
		sample.Disks = &protocol.DiskGroup{
			Group: protocol.Group{Status: protocol.CollectorOK, ObservedAt: &now,
				CollectionDurationMS: &latency},
			Disks: []protocol.DiskEntry{{
				ID: "/", TotalBytes: &total, UsedBytes: &used,
				AvailableBytes:     &available,
				UtilizationPercent: &status.utilization,
			}},
		}
	}
	unsupported := protocol.Group{Status: protocol.CollectorUnsupported,
		Reason: "not collected over SSH", ObservedAt: &now}
	if requested[protocol.GroupMemory] {
		sample.Memory = &protocol.MemoryGroup{Group: unsupported}
	}
	if requested[protocol.GroupNetwork] {
		sample.Network = &protocol.NetworkGroup{Group: unsupported}
	}
	if requested[protocol.GroupDiskIO] {
		sample.DiskIO = &protocol.DiskIOGroup{Group: unsupported}
	}
	if requested[protocol.GroupGPU] {
		sample.GPU = &protocol.GPUGroup{Group: unsupported}
	}
	if requested[protocol.GroupThermal] {
		sample.Thermal = &protocol.ThermalGroup{Group: unsupported}
	}
	if requested[protocol.GroupPower] {
		sample.Power = &protocol.PowerGroup{Group: unsupported}
	}
	return protocol.Observation{HostID: ref.HostID, Generation: ref.Generation, Sample: sample}, nil
}

func (s *SSHLink) Close() error { return nil }

func (s *SSHLink) commandArgs(knownPath, keyAlias, remote string) []string {
	algorithm := strings.Fields(s.cfg.HostKey)[0]
	if algorithm == "ssh-rsa" {
		algorithm = "rsa-sha2-512,rsa-sha2-256"
	}
	args := []string{"-T",
		"-o", "HostName=" + s.cfg.Hostname, "-o", "User=" + s.cfg.User,
		"-p", strconv.Itoa(s.cfg.Port), "-o", "HostKeyAlias=" + keyAlias,
		"-o", "HostKeyAlgorithms=" + algorithm,
		"-o", "UserKnownHostsFile=\"" + filepath.ToSlash(knownPath) + "\"",
		"-o", "GlobalKnownHostsFile=none", "-o", "KnownHostsCommand=none", "-o", "VerifyHostKeyDNS=no",
		"-o", "CheckHostIP=no", "-o", "ControlPath=none",
		"-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
		"-o", "UpdateHostKeys=no", "-o", "ForwardAgent=no",
		"-o", "ClearAllForwardings=yes", "-o", "ControlMaster=no",
		"-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
		"-o", "ServerAliveCountMax=2", "-o", "LogLevel=ERROR",
	}
	if s.cfg.ConfigPath != "" {
		args = append([]string{"-F", s.cfg.ConfigPath}, args...)
	}
	return append(args, s.cfg.Alias, remote)
}

func (s *SSHLink) run(ctx context.Context, remote string) (string, time.Duration, error) {
	// A private per-request trust file pins the reviewed key even if the alias
	// or the user's trust store changes. It contains only a public host key.
	known, err := os.CreateTemp("", "odessy-ssh-known-*")
	if err != nil {
		return "", 0, err
	}
	defer os.Remove(known.Name())
	keyAlias := "odessy-" + s.runID
	_, writeErr := fmt.Fprintln(known, keyAlias+" "+s.cfg.HostKey)
	closeErr := known.Close()
	if writeErr != nil {
		return "", 0, writeErr
	}
	if closeErr != nil {
		return "", 0, closeErr
	}
	args := s.commandArgs(known.Name(), keyAlias, remote)
	cmd := exec.CommandContext(ctx, s.cfg.Binary, args...)
	cmd.WaitDelay = time.Second
	stdout := &boundedBuffer{limit: 32 << 10}
	stderr := &boundedBuffer{limit: 4096}
	cmd.Stdout = stdout
	cmd.Stderr = stderr
	started := time.Now()
	err = cmd.Run()
	elapsed := time.Since(started)
	if ctx.Err() == context.DeadlineExceeded {
		return "", elapsed, &Failure{Code: "ssh_timeout", Message: "SSH request deadline exceeded", Retryable: true}
	}
	if stdout.overflow || stderr.overflow {
		return "", elapsed, &Failure{Code: "ssh_output_limit", Message: "SSH output exceeded limit"}
	}
	if err != nil {
		message := strings.TrimSpace(stderr.String())
		if message == "" {
			message = "SSH command failed"
		}
		if len(message) > 400 {
			message = message[:400]
		}
		return "", elapsed, &Failure{Code: "ssh_command_failed", Message: message, Retryable: true}
	}
	return stdout.String(), elapsed, nil
}

const statusCommand = "cat /proc/loadavg; printf '\\n---\\n'; df -P -k /; printf '\\n---\\n'; uname -s; uname -m"

type sshStatus struct {
	load1, load5, load15                  float64
	totalBytes, usedBytes, availableBytes int64
	utilization                           float64
	osName, architecture                  string
}

func parseSSHStatus(raw string) (sshStatus, error) {
	parts := strings.Split(raw, "---")
	if len(parts) < 3 {
		return sshStatus{}, &Failure{Code: "ssh_parse_failed", Message: "status output is missing a section"}
	}
	load := strings.Fields(parts[0])
	if len(load) < 3 {
		return sshStatus{}, &Failure{Code: "ssh_parse_failed", Message: "load average is missing"}
	}
	values := make([]float64, 3)
	for i := range values {
		value, err := strconv.ParseFloat(load[i], 64)
		if err != nil || value < 0 {
			return sshStatus{}, &Failure{Code: "ssh_parse_failed", Message: "load average is invalid"}
		}
		values[i] = value
	}
	disk := lastDataLine(parts[1])
	if disk == "" {
		return sshStatus{}, &Failure{Code: "ssh_parse_failed", Message: "disk usage is missing"}
	}
	fields := strings.Fields(disk)
	if len(fields) < 6 || !strings.HasSuffix(fields[len(fields)-2], "%") {
		return sshStatus{}, &Failure{Code: "ssh_parse_failed", Message: "disk usage has no capacity field"}
	}
	total, err1 := strconv.ParseInt(fields[len(fields)-5], 10, 64)
	used, err2 := strconv.ParseInt(fields[len(fields)-4], 10, 64)
	available, err3 := strconv.ParseInt(fields[len(fields)-3], 10, 64)
	percent, err4 := strconv.ParseFloat(stringSuffixPercent(fields[len(fields)-2]), 64)
	if err1 != nil || err2 != nil || err3 != nil || err4 != nil ||
		total < 0 || used < 0 || available < 0 || percent < 0 || percent > 100 {
		return sshStatus{}, &Failure{Code: "ssh_parse_failed", Message: "disk usage is invalid"}
	}
	platform := make([]string, 0, 2)
	for _, line := range strings.Split(parts[2], "\n") {
		if value := strings.TrimSpace(line); value != "" {
			platform = append(platform, value)
		}
	}
	if len(platform) < 2 {
		return sshStatus{}, &Failure{Code: "ssh_parse_failed", Message: "host platform is missing"}
	}
	return sshStatus{
		load1: values[0], load5: values[1], load15: values[2],
		totalBytes: total * 1024, usedBytes: used * 1024, availableBytes: available * 1024,
		utilization: percent, osName: platform[0], architecture: platform[1],
	}, nil
}

func lastDataLine(text string) string {
	lines := strings.Split(text, "\n")
	for i := len(lines) - 1; i >= 0; i-- {
		line := strings.TrimSpace(lines[i])
		if line == "" || strings.HasPrefix(strings.ToLower(line), "filesystem") {
			continue
		}
		return line
	}
	return ""
}

func stringSuffixPercent(value string) string {
	return strings.TrimSuffix(value, "%")
}

func sshBootID(hostID string) string {
	if len(hostID) > 21 {
		return "ssh-" + hostID[5:21]
	}
	return "ssh-" + hostID
}

// boundedBuffer keeps a fixed output ceiling; overflow is reported, never
// silently truncated into a different observation.
type boundedBuffer struct {
	limit    int
	buf      []byte
	overflow bool
}

func (b *boundedBuffer) Write(p []byte) (int, error) {
	if len(b.buf)+len(p) > b.limit {
		b.overflow = true
		return 0, fmt.Errorf("output exceeds limit")
	}
	b.buf = append(b.buf, p...)
	return len(p), nil
}

func (b *boundedBuffer) String() string { return string(b.buf) }
