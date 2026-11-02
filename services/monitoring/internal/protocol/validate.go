package protocol

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strings"
	"time"
)

// ErrInvalid wraps every validation failure so callers can map it to
// INVALID_REQUEST without inspecting strings.
var ErrInvalid = errors.New("invalid monitoring payload")

// ErrUnsupportedVersion is returned for any non-monitoring-v1 frame.
var ErrUnsupportedVersion = errors.New("unsupported schema version")

func invalid(format string, args ...any) error {
	return fmt.Errorf("%w: %s", ErrInvalid, fmt.Sprintf(format, args...))
}

// DecodeBounded strictly decodes at most limit bytes into dst. Unknown fields,
// trailing data and oversized documents are rejected before allocation of
// further structure.
func DecodeBounded(data []byte, limit int, dst any) error {
	if len(data) == 0 {
		return invalid("empty payload")
	}
	if len(data) > limit {
		return fmt.Errorf("%w: payload %d bytes exceeds limit %d", ErrResourceLimit, len(data), limit)
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	if err := dec.Decode(dst); err != nil {
		return invalid("decode: %v", err)
	}
	if dec.More() {
		return invalid("trailing data after JSON document")
	}
	return nil
}

// ErrResourceLimit reports a bounded-resource rejection.
var ErrResourceLimit = errors.New("resource limit reached")

// ValidateHostID enforces the opaque `host_<sha256>` shape. Addresses and
// aliases are never identities.
func ValidateHostID(id string) error {
	if !strings.HasPrefix(id, "host_") {
		return invalid("host_id must start with host_")
	}
	sum := id[len("host_"):]
	if len(sum) != 64 {
		return invalid("host_id digest must be 64 lowercase hex characters")
	}
	for _, r := range sum {
		if (r < '0' || r > '9') && (r < 'a' || r > 'f') {
			return invalid("host_id digest must be lowercase hex")
		}
	}
	return nil
}

// ValidGroup reports whether a group name may be requested.
func ValidGroup(g string) bool {
	for _, known := range SampleGroups {
		if known == g {
			return true
		}
	}
	return false
}

func validUTC(t time.Time) bool {
	if t.IsZero() {
		return true
	}
	_, off := t.Zone()
	return off == 0
}

func checkTime(name string, t time.Time, required bool) error {
	if t.IsZero() {
		if required {
			return invalid("%s is required", name)
		}
		return nil
	}
	if !validUTC(t) {
		return invalid("%s must be UTC", name)
	}
	return nil
}

func checkOptionalTime(name string, t *time.Time) error {
	if t == nil {
		return nil
	}
	return checkTime(name, *t, true)
}

func validPercent(v *float64) bool {
	if v == nil {
		return true
	}
	return !math.IsNaN(*v) && !math.IsInf(*v, 0) && *v >= 0 && *v <= 100
}

func validCounter(v *float64) bool {
	if v == nil {
		return true
	}
	return !math.IsNaN(*v) && !math.IsInf(*v, 0) && *v >= 0 && math.Trunc(*v) == *v
}

func validRate(v *float64) bool {
	if v == nil {
		return true
	}
	return !math.IsNaN(*v) && !math.IsInf(*v, 0) && *v >= 0
}

func validSigned(v *float64) bool {
	return v == nil || (!math.IsNaN(*v) && !math.IsInf(*v, 0))
}

func validCollectorStatus(s CollectorStatus) bool {
	switch s {
	case CollectorOK, CollectorWarmingUp, CollectorUnsupported, CollectorPermissionDenied, CollectorError, CollectorStale:
		return true
	}
	return false
}

func validServiceHealth(s ServiceHealth) bool {
	switch s {
	case ServiceHealthy, ServiceUnhealthy, ServiceUnknown, ServiceUnsupported, ServiceDisabled:
		return true
	}
	return false
}

func validateGroupBase(name string, g Group) error {
	if !validCollectorStatus(g.Status) {
		return invalid("%s.status %q is not a known collector status", name, g.Status)
	}
	if err := checkOptionalTime(name+".observed_at", g.ObservedAt); err != nil {
		return err
	}
	if g.CollectionDurationMS != nil && !validRate(g.CollectionDurationMS) {
		return invalid("%s.collection_duration_ms invalid", name)
	}
	return nil
}

// ValidateHostIdentity validates one identity row.
func ValidateHostIdentity(h HostIdentity) error {
	if err := ValidateHostID(h.HostID); err != nil {
		return err
	}
	if h.InstallationID == "" || h.FingerprintSHA256 == "" {
		return invalid("installation_id and fingerprint_sha256 are required")
	}
	switch h.TrustState {
	case TrustLocal, TrustPaired, TrustRevoked, TrustUnknown:
	default:
		return invalid("trust_state %q invalid", h.TrustState)
	}
	switch h.MonitoringAccess {
	case AccessAllowed, AccessDenied, AccessUnsupported, AccessDisabled:
	default:
		return invalid("monitoring_access %q invalid", h.MonitoringAccess)
	}
	if h.Generation == "" {
		return invalid("generation is required")
	}
	if err := checkOptionalTime("certificate_expires_at", h.CertificateExpiresAt); err != nil {
		return err
	}
	return nil
}

// ValidateInventory validates a full inventory response.
func ValidateInventory(inv Inventory) error {
	if inv.Revision == "" {
		return invalid("revision is required")
	}
	if len(inv.Hosts) > MaxConfiguredHosts {
		return fmt.Errorf("%w: inventory lists %d hosts, cap %d", ErrResourceLimit, len(inv.Hosts), MaxConfiguredHosts)
	}
	seen := make(map[string]struct{}, len(inv.Hosts))
	for _, h := range inv.Hosts {
		if err := ValidateHostIdentity(h); err != nil {
			return err
		}
		if _, dup := seen[h.HostID]; dup {
			return invalid("duplicate host identity %q", h.HostID)
		}
		seen[h.HostID] = struct{}{}
	}
	return nil
}

// ValidateHeartbeat validates an authenticated heartbeat.
func ValidateHeartbeat(hb Heartbeat) error {
	if err := ValidateHostID(hb.HostID); err != nil {
		return err
	}
	if hb.Generation == "" {
		return invalid("generation is required")
	}
	if hb.BootID == "" {
		return invalid("boot_id is required")
	}
	if err := checkTime("observed_at", hb.ObservedAt, true); err != nil {
		return err
	}
	if err := checkTime("received_at", hb.ReceivedAt, false); err != nil {
		return err
	}
	if hb.LatencyMS != nil && !validRate(hb.LatencyMS) {
		return invalid("latency_ms invalid")
	}
	return nil
}

// ValidateObservation validates a sample observation, including array caps.
func ValidateObservation(o Observation) error {
	if err := ValidateHostID(o.HostID); err != nil {
		return err
	}
	if o.Generation == "" {
		return invalid("generation is required")
	}
	s := o.Sample
	if s.SchemaVersion != 0 && s.SchemaVersion != SchemaVersion {
		return ErrUnsupportedVersion
	}
	if s.ID == "" || s.BootID == "" {
		return invalid("sample id and boot_id are required")
	}
	if s.Scope != "host" {
		return invalid("sample scope %q must be host", s.Scope)
	}
	if s.IntervalMS <= 0 {
		return invalid("interval_ms must be positive")
	}
	if err := checkTime("sample.observed_at", s.ObservedAt, true); err != nil {
		return err
	}
	if err := checkTime("sample.received_at", s.ReceivedAt, false); err != nil {
		return err
	}
	if s.CPU != nil {
		if err := validateGroupBase("cpu", s.CPU.Group); err != nil {
			return err
		}
		if !validPercent(s.CPU.UtilizationPercent) {
			return invalid("cpu.utilization_percent outside 0..100")
		}
		for _, v := range []*float64{s.CPU.Load1, s.CPU.Load5, s.CPU.Load15, s.CPU.CoreCount} {
			if !validRate(v) {
				return invalid("cpu load/core values invalid")
			}
		}
	}
	if s.Memory != nil {
		if err := validateGroupBase("memory", s.Memory.Group); err != nil {
			return err
		}
		for _, v := range []*float64{s.Memory.TotalBytes, s.Memory.UsedBytes, s.Memory.AvailableBytes, s.Memory.SharedBytes} {
			if !validCounter(v) {
				return invalid("memory byte counters must be non-negative integers")
			}
		}
		if !validPercent(s.Memory.UtilizationPercent) {
			return invalid("memory.utilization_percent outside 0..100")
		}
	}
	if s.Network != nil {
		if err := validateGroupBase("network", s.Network.Group); err != nil {
			return err
		}
		if len(s.Network.Interfaces) > MaxInterfacesPerHost {
			if !s.Network.Truncated {
				return fmt.Errorf("%w: interfaces exceed %d without truncation", ErrResourceLimit, MaxInterfacesPerHost)
			}
			return fmt.Errorf("%w: interfaces exceed %d", ErrResourceLimit, MaxInterfacesPerHost)
		}
		for _, nic := range s.Network.Interfaces {
			if nic.ID == "" {
				return invalid("network interface id is required")
			}
			for _, v := range []*float64{nic.ReceiveBPS, nic.TransmitBPS, nic.ReceiveErrors, nic.TransmitErrors} {
				if !validRate(v) {
					return invalid("network counters invalid for %q", nic.ID)
				}
			}
		}
	}
	if s.Disks != nil {
		if err := validateGroupBase("disks", s.Disks.Group); err != nil {
			return err
		}
		if len(s.Disks.Disks) > MaxDisksPerHost {
			return fmt.Errorf("%w: disks exceed %d", ErrResourceLimit, MaxDisksPerHost)
		}
		for _, d := range s.Disks.Disks {
			if d.ID == "" {
				return invalid("disk id is required")
			}
			for _, v := range []*float64{d.TotalBytes, d.UsedBytes, d.FreeBytes} {
				if !validCounter(v) {
					return invalid("disk byte counters must be non-negative integers")
				}
			}
			if !validPercent(d.UtilizationPercent) {
				return invalid("disk utilization_percent outside 0..100")
			}
		}
	}
	if s.DiskIO != nil {
		if err := validateGroupBase("disk_io", s.DiskIO.Group); err != nil {
			return err
		}
		if len(s.DiskIO.Devices) > MaxDisksPerHost {
			return fmt.Errorf("%w: disk_io devices exceed %d", ErrResourceLimit, MaxDisksPerHost)
		}
		for _, d := range s.DiskIO.Devices {
			if d.ID == "" {
				return invalid("disk_io device id is required")
			}
			if !validRate(d.ReadBPS) || !validRate(d.WriteBPS) {
				return invalid("disk_io rates invalid for %q", d.ID)
			}
		}
	}
	if s.GPU != nil {
		if err := validateGroupBase("gpu", s.GPU.Group); err != nil {
			return err
		}
		if len(s.GPU.GPUs) > MaxGPUsPerHost {
			return fmt.Errorf("%w: gpus exceed %d", ErrResourceLimit, MaxGPUsPerHost)
		}
		for _, g := range s.GPU.GPUs {
			if g.ID == "" {
				return invalid("gpu id is required")
			}
			if !validPercent(g.UtilizationPercent) {
				return invalid("gpu utilization_percent outside 0..100")
			}
			for _, v := range []*float64{g.MemoryUsedBytes, g.MemoryTotalBytes, g.TemperatureCelsius} {
				if !validRate(v) {
					return invalid("gpu values invalid for %q", g.ID)
				}
			}
		}
	}
	if s.Thermal != nil {
		if err := validateGroupBase("thermal", s.Thermal.Group); err != nil {
			return err
		}
		if len(s.Thermal.Sensors) > MaxInterfacesPerHost {
			return fmt.Errorf("%w: thermal sensors exceed %d", ErrResourceLimit, MaxInterfacesPerHost)
		}
		for _, sensor := range s.Thermal.Sensors {
			if sensor.ID == "" || !validSigned(sensor.TemperatureCelsius) {
				return invalid("thermal sensor invalid")
			}
		}
	}
	if s.Power != nil {
		if err := validateGroupBase("power", s.Power.Group); err != nil {
			return err
		}
		if len(s.Power.Rails) > MaxInterfacesPerHost {
			return fmt.Errorf("%w: power rails exceed %d", ErrResourceLimit, MaxInterfacesPerHost)
		}
		for _, rail := range s.Power.Rails {
			if rail.ID == "" || rail.Domain == "" {
				return invalid("power rail id and measured domain are required")
			}
			if !validRate(rail.Watts) {
				return invalid("power watts invalid for %q", rail.ID)
			}
		}
	}
	return nil
}

func validateServices(services []ServiceEntry) error {
	if len(services) > 16 {
		return fmt.Errorf("%w: services exceed 16 entries", ErrResourceLimit)
	}
	seen := make(map[string]struct{}, len(services))
	for _, s := range services {
		switch s.Name {
		case "coordinator", "odessy_link", "mcp_server", "model_server", "stt", "tts":
		default:
			return invalid("service name %q not allowed", s.Name)
		}
		if _, dup := seen[s.Name]; dup {
			return invalid("duplicate service entry %q", s.Name)
		}
		seen[s.Name] = struct{}{}
		if !validServiceHealth(s.Status) {
			return invalid("service %q status %q invalid", s.Name, s.Status)
		}
		if err := checkOptionalTime("services.observed_at", s.ObservedAt); err != nil {
			return err
		}
	}
	return nil
}

// forbiddenKeys are never accepted in runtime telemetry. Secret-bearing or
// conversation-bearing structures are excluded before storage and SSE.
var forbiddenKeys = map[string]struct{}{
	"prompt": {}, "prompts": {}, "messages": {}, "message": {}, "transcript": {},
	"transcripts": {}, "conversation": {}, "conversations": {}, "arguments": {},
	"tool_arguments": {}, "result": {}, "results": {}, "tool_results": {},
	"content": {}, "contents": {}, "credentials": {}, "credential": {},
	"secret": {}, "secrets": {}, "token": {}, "tokens": {}, "api_key": {},
	"apikey": {}, "authorization": {}, "password": {}, "workspace": {},
	"workspace_path": {}, "workspacepath": {}, "path": {}, "paths": {},
	"peer_answer": {}, "answers": {}, "reasoning": {}, "raw": {},
}

// ScanForbiddenKeys rejects secret-bearing or conversation-bearing keys anywhere
// in a runtime document, before any typed interpretation.
func ScanForbiddenKeys(data []byte) error {
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.UseNumber()
	var doc any
	if err := dec.Decode(&doc); err != nil {
		return invalid("decode: %v", err)
	}
	return scanValue(doc, 0)
}

func scanValue(v any, depth int) error {
	if depth > 8 {
		return invalid("runtime document nesting exceeds 8 levels")
	}
	switch typed := v.(type) {
	case map[string]any:
		for k, child := range typed {
			if _, banned := forbiddenKeys[strings.ToLower(k)]; banned {
				return invalid("field %q is not permitted in runtime telemetry", k)
			}
			if err := scanValue(child, depth+1); err != nil {
				return err
			}
		}
	case []any:
		if len(typed) > 256 {
			return fmt.Errorf("%w: array exceeds 256 entries", ErrResourceLimit)
		}
		for _, child := range typed {
			if err := scanValue(child, depth+1); err != nil {
				return err
			}
		}
	}
	return nil
}

func validateCost(where string, c Cost) error {
	if !validRate(c.Estimated) {
		return invalid("%s.estimated invalid", where)
	}
	if c.Currency != "" && len(c.Currency) != 3 {
		return invalid("%s.currency must be a 3-letter code", where)
	}
	return nil
}

func validateAI(ai *AI) error {
	if ai == nil {
		return nil
	}
	if len(ai.Providers) > 32 {
		return fmt.Errorf("%w: provider buckets exceed 32", ErrResourceLimit)
	}
	for _, p := range ai.Providers {
		if p.Provider == "" || p.Model == "" {
			return invalid("provider bucket requires provider and model")
		}
		if !validRate(p.Requests) || !validRate(p.Errors) {
			return invalid("provider bucket counters invalid")
		}
		if err := validateCost("provider", p.Cost); err != nil {
			return err
		}
	}
	if ai.Latest != nil {
		l := ai.Latest
		if l.Model == "" {
			return invalid("ai.latest.model is required")
		}
		for _, v := range []*float64{
			l.RequestDurationMS, l.TimeToFirstTokenMS, l.GenerationTokensPerSecond,
			l.PromptTokens, l.CompletionTokens, l.Errors, l.Timeouts, l.ToolFailures, l.ActiveJobs,
		} {
			if !validRate(v) {
				return invalid("ai.latest counters invalid")
			}
		}
		if err := validateCost("ai.latest", l.Cost); err != nil {
			return err
		}
	}
	return nil
}

// ValidateRuntimeEnvelope validates the sanitized runtime observation.
func ValidateRuntimeEnvelope(env RuntimeEnvelope) error {
	if env.SchemaVersion != SchemaVersion {
		return ErrUnsupportedVersion
	}
	if err := ValidateHostID(env.HostID); err != nil {
		return err
	}
	if env.Generation == "" {
		return invalid("generation is required")
	}
	if env.SourceID == "" {
		return invalid("source_id is required")
	}
	if err := checkTime("observed_at", env.ObservedAt, true); err != nil {
		return err
	}
	if err := validateServices([]ServiceEntry(env.Services)); err != nil {
		return err
	}
	return validateAI(env.AI)
}

// ValidateBootstrap validates the private startup frame.
func ValidateBootstrap(f BootstrapFrame) (Settings, error) {
	if f.SchemaVersion != SchemaVersion {
		return Settings{}, ErrUnsupportedVersion
	}
	if f.Op != "configure" {
		return Settings{}, invalid("bootstrap op %q must be configure", f.Op)
	}
	if len(f.Token) < 32 {
		return Settings{}, invalid("internal token must be at least 32 characters")
	}
	if err := ValidateLinkBinary(f.LinkBinary); err != nil {
		return Settings{}, err
	}
	return f.Settings.Normalized()
}

// ValidateLinkBinary rejects anything that is not an explicit executable path:
// no shell metacharacters, no bare command names.
func ValidateLinkBinary(p string) error {
	if p == "" {
		return invalid("link_binary is required")
	}
	// A tilde is valid in Windows short paths (for example RUNNER~1).
	// The worker is started directly, so a tilde is never shell-expanded.
	if strings.ContainsAny(p, "|&;<>()$`\"'*?[]{}\n\r\t") {
		return invalid("link_binary must be a plain path without shell metacharacters")
	}
	if !strings.ContainsAny(p, "/\\") {
		return invalid("link_binary must be an absolute executable path")
	}
	return nil
}
