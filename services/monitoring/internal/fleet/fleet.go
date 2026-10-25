// Package fleet owns identity projection, trust leases, liveness and scheduling.
package fleet

import (
	"errors"
	"fmt"
	"sort"
	"sync"
	"time"

	"github.com/odessy/odessy/services/monitoring/internal/link"
	"github.com/odessy/odessy/services/monitoring/internal/protocol"
)

// Event kinds published on host and monitor changes.
const (
	EventHostAdded               = "host.added"
	EventHostUpdated             = "host.updated"
	EventHostTrustChanged        = "host.trust_changed"
	EventHostConnectivityChanged = "host.connectivity_changed"
	EventHostMetrics             = "host.metrics"
	EventMonitorHealth           = "monitor.health"
	EventResyncRequired          = "resync_required"
)

// Event is one monitor change.
type Event struct {
	Kind    string `json:"kind"`
	HostID  string `json:"host_id,omitempty"`
	Payload any    `json:"payload,omitempty"`
}

// Sink receives monitor events.
type Sink func(Event)

// Clock is the injected time source.
type Clock interface{ Now() time.Time }

// Admission and lookup failures.
var (
	ErrUnknownHost        = errors.New("unknown host")
	ErrHostRevoked        = errors.New("host revoked")
	ErrGenerationMismatch = errors.New("generation mismatch")
	ErrTrustUnknown       = errors.New("trust lease expired")
	ErrAccessDenied       = errors.New("monitoring access denied")
	ErrOutOfOrder         = errors.New("out-of-order observation")
	ErrDuplicate          = errors.New("duplicate observation")
	ErrRuntimeAttribution = errors.New("runtime attribution rejected")
	ErrNotMonitored       = errors.New("host is not monitored")
)

// HostView is the immutable API projection of one host.
type HostView struct {
	HostID               string                  `json:"host_id"`
	DisplayName          string                  `json:"display_name"`
	Platform             *protocol.Platform      `json:"platform"`
	Trust                protocol.TrustState     `json:"trust"`
	Connectivity         protocol.Connectivity   `json:"connectivity"`
	MonitoringAccess     protocol.Access         `json:"monitoring_access"`
	Generation           string                  `json:"generation"`
	CertificateExpiresAt *time.Time              `json:"certificate_expires_at"`
	Local                bool                    `json:"local"`
	Monitored            bool                    `json:"monitored"`
	Revoked              bool                    `json:"revoked"`
	LastSeenAt           *time.Time              `json:"last_seen_at"`
	LatencyMS            *float64                `json:"latency_ms"`
	Sample               *protocol.Sample        `json:"sample"`
	Services             []protocol.ServiceEntry `json:"services"`
	AI                   *protocol.AI            `json:"ai"`
	StaleGroups          []string                `json:"stale_groups"`
}

// Health is the monitor health block.
type Health struct {
	InventoryFresh     bool       `json:"inventory_fresh"`
	LastInventoryAt    *time.Time `json:"last_inventory_at"`
	LastInventoryError string     `json:"last_inventory_error,omitempty"`
	TrustLeaseValid    bool       `json:"trust_lease_valid"`
	Degraded           bool       `json:"degraded"`
	Reason             string     `json:"reason,omitempty"`
}

// Snapshot is a consistent view of the fleet.
type Snapshot struct {
	GeneratedAt time.Time  `json:"generated_at"`
	Hosts       []HostView `json:"hosts"`
	Health      Health     `json:"health"`
}

type hostState struct {
	identity     protocol.HostIdentity
	revoked      bool
	generation   string
	lastSeen     time.Time
	lastBeat     time.Time
	bootID       string
	lastSeq      uint64
	lastSampleID string
	sample       *protocol.Sample
	groupSeen    map[string]time.Time
	services     []protocol.ServiceEntry
	ai           *protocol.AI
	latencyMS    *float64
	blocked      bool
	blockReason  string
}

// Fleet is the concurrency-safe fleet projection.
type Fleet struct {
	mu        sync.RWMutex
	clock     Clock
	settings  protocol.Settings
	intervals protocol.Intervals
	sink      Sink

	hosts       map[string]*hostState
	leaseOK     bool
	lastRefresh time.Time
	lastErr     string
	tombstones  uint64
	degraded    bool
	degradeWhy  string
}

// New builds an empty fleet with no trust established.
func New(clock Clock, settings protocol.Settings, intervals protocol.Intervals, sink Sink) *Fleet {
	return &Fleet{
		clock:     clock,
		settings:  settings,
		intervals: intervals,
		sink:      sink,
		hosts:     make(map[string]*hostState),
	}
}

func (f *Fleet) publish(ev Event) {
	if f.sink != nil {
		f.sink(ev)
	}
}

// ApplyInventory projects a successful authoritative inventory refresh.
func (f *Fleet) ApplyInventory(inv protocol.Inventory, at time.Time) error {
	if err := protocol.ValidateInventory(inv); err != nil {
		return err
	}
	f.mu.Lock()
	defer f.mu.Unlock()

	seen := make(map[string]struct{}, len(inv.Hosts))
	events := make([]Event, 0, len(inv.Hosts))

	for _, ident := range inv.Hosts {
		seen[ident.HostID] = struct{}{}
		existing, known := f.hosts[ident.HostID]
		if !known {
			h := &hostState{identity: ident, generation: ident.Generation, groupSeen: map[string]time.Time{}}
			f.hosts[ident.HostID] = h
			events = append(events, Event{Kind: EventHostAdded, HostID: ident.HostID})
			events = append(events, Event{Kind: EventHostTrustChanged, HostID: ident.HostID})
			continue
		}
		changed := existing.revoked || existing.identity.TrustState != ident.TrustState ||
			existing.identity.MonitoringAccess != ident.MonitoringAccess ||
			existing.generation != ident.Generation
		if existing.identity.FingerprintSHA256 != ident.FingerprintSHA256 {
			// A different certificate is a different host; never inherit history.
			events = append(events, Event{Kind: EventHostTrustChanged, HostID: ident.HostID})
		}
		existing.identity = ident
		if existing.revoked && ident.TrustState != protocol.TrustRevoked {
			existing.revoked = false
		}
		existing.generation = ident.Generation
		existing.blocked = false
		existing.blockReason = ""
		if changed {
			events = append(events, Event{Kind: EventHostTrustChanged, HostID: ident.HostID})
		} else {
			events = append(events, Event{Kind: EventHostUpdated, HostID: ident.HostID})
		}
	}

	for id, h := range f.hosts {
		if _, ok := seen[id]; ok {
			continue
		}
		if h.revoked {
			continue
		}
		// A previously known peer disappearing from a successful authoritative
		// inventory becomes a revoked tombstone; its older generation is dead.
		h.revoked = true
		h.identity.TrustState = protocol.TrustRevoked
		h.generation = fmt.Sprintf("%s#revoked-%d", h.identity.Generation, f.tombstones)
		h.identity.Generation = h.generation
		f.tombstones++
		events = append(events, Event{Kind: EventHostTrustChanged, HostID: id})
	}

	f.leaseOK = true
	f.lastRefresh = at
	f.lastErr = ""
	for _, ev := range events {
		f.publish(ev)
	}
	return nil
}

// MarkInventoryFailure records a failed refresh. A failure is never an empty
// successful inventory.
func (f *Fleet) MarkInventoryFailure(at time.Time, err error) {
	f.mu.Lock()
	msg := "inventory refresh failed"
	if err != nil {
		msg = err.Error()
	}
	f.lastErr = msg
	f.leaseOK = false
	f.mu.Unlock()
	f.publish(Event{Kind: EventMonitorHealth, Payload: map[string]any{"inventory_error": msg, "at": at}})
}

func (f *Fleet) trustState(h *hostState, now time.Time) protocol.TrustState {
	if h.revoked {
		return protocol.TrustRevoked
	}
	if h.identity.TrustState == protocol.TrustLocal {
		return protocol.TrustLocal
	}
	if !f.leaseOK || now.Sub(f.lastRefresh) > protocol.TrustLease {
		return protocol.TrustUnknown
	}
	return h.identity.TrustState
}

func (f *Fleet) connectivity(h *hostState, now time.Time) protocol.Connectivity {
	if h.revoked || f.trustState(h, now) == protocol.TrustUnknown {
		return protocol.ConnectivityUnknown
	}
	if h.lastBeat.IsZero() {
		return protocol.ConnectivityUnknown
	}
	age := now.Sub(h.lastBeat)
	hb := f.intervals.Heartbeat
	switch {
	case age <= 2*hb:
		return protocol.ConnectivityOnline
	case age < 6*hb:
		return protocol.ConnectivityStale
	default:
		return protocol.ConnectivityOffline
	}
}

func (f *Fleet) admit(hostID, generation string, now time.Time) (*hostState, error) {
	h, ok := f.hosts[hostID]
	if !ok {
		return nil, ErrUnknownHost
	}
	if h.revoked {
		return nil, ErrHostRevoked
	}
	if f.trustState(h, now) == protocol.TrustUnknown {
		return nil, ErrTrustUnknown
	}
	if generation != "" && generation != h.generation {
		return nil, ErrGenerationMismatch
	}
	switch h.identity.MonitoringAccess {
	case protocol.AccessAllowed:
	case protocol.AccessUnsupported, protocol.AccessDisabled, protocol.AccessDenied:
		return nil, fmt.Errorf("%w: %s", ErrAccessDenied, h.identity.MonitoringAccess)
	default:
		return nil, fmt.Errorf("%w: unknown access", ErrAccessDenied)
	}
	return h, nil
}

// AcceptHeartbeat admits an authenticated heartbeat. Receiver time drives
// liveness; a heartbeat can establish online even when metrics fail.
func (f *Fleet) AcceptHeartbeat(ref protocol.HostRef, hb protocol.Heartbeat, at time.Time) error {
	f.mu.Lock()
	h, err := f.admit(ref.HostID, ref.Generation, at)
	if err != nil {
		f.mu.Unlock()
		return err
	}
	prev := f.connectivity(h, at)
	h.lastBeat = at
	h.lastSeen = at
	if hb.LatencyMS != nil {
		h.latencyMS = hb.LatencyMS
	}
	now := f.connectivity(h, at)
	f.mu.Unlock()
	if prev != now {
		f.publish(Event{Kind: EventHostConnectivityChanged, HostID: ref.HostID, Payload: map[string]any{"connectivity": now}})
	}
	return nil
}

// AcceptObservation admits one sample. Late, duplicated or out-of-generation
// samples are rejected instead of being recorded as fresh data.
func (f *Fleet) AcceptObservation(ref protocol.HostRef, obs protocol.Observation, at time.Time) error {
	if err := protocol.ValidateObservation(obs); err != nil {
		return err
	}
	f.mu.Lock()
	h, err := f.admit(ref.HostID, ref.Generation, at)
	if err != nil {
		f.mu.Unlock()
		return err
	}
	var ev Event
	defer func() {
		f.mu.Unlock()
		if ev.Kind != "" {
			f.publish(ev)
		}
	}()
	if obs.HostID != ref.HostID {
		return fmt.Errorf("%w: observation host differs from request host", ErrRuntimeAttribution)
	}
	if obs.Generation != "" && obs.Generation != h.generation {
		return ErrGenerationMismatch
	}
	if h.bootID != obs.BootID {
		h.bootID = obs.BootID
		h.lastSeq = 0
		h.lastSampleID = ""
	}
	if obs.ID == h.lastSampleID {
		return ErrDuplicate
	}
	if obs.Sequence <= h.lastSeq && h.lastSeq != 0 {
		return ErrOutOfOrder
	}
	h.lastSeq = obs.Sequence
	h.lastSampleID = obs.ID
	sample := obs.Sample
	mark := func(name string, present bool, observed *time.Time) {
		if !present {
			return
		}
		if observed != nil {
			h.groupSeen[name] = *observed
			return
		}
		h.groupSeen[name] = at
	}
	mark(protocol.GroupCPU, sample.CPU != nil, cpuObserved(sample))
	mark(protocol.GroupMemory, sample.Memory != nil, memoryObserved(sample))
	mark(protocol.GroupDiskIO, sample.DiskIO != nil, diskIOObserved(sample))
	mark(protocol.GroupNetwork, sample.Network != nil, networkObserved(sample))
	mark(protocol.GroupDisks, sample.Disks != nil, disksObserved(sample))
	mark(protocol.GroupGPU, sample.GPU != nil, gpuObserved(sample))
	mark(protocol.GroupThermal, sample.Thermal != nil, thermalObserved(sample))
	mark(protocol.GroupPower, sample.Power != nil, powerObserved(sample))
	// Public latest view merges groups, retaining each group's original time.
	// History receives only the new observation, so old gauges are not resampled.
	if prior := h.sample; prior != nil {
		if sample.CPU == nil {
			sample.CPU = prior.CPU
		}
		if sample.Memory == nil {
			sample.Memory = prior.Memory
		}
		if sample.Network == nil {
			sample.Network = prior.Network
		}
		if sample.Disks == nil {
			sample.Disks = prior.Disks
		}
		if sample.DiskIO == nil {
			sample.DiskIO = prior.DiskIO
		}
		if sample.GPU == nil {
			sample.GPU = prior.GPU
		}
		if sample.Thermal == nil {
			sample.Thermal = prior.Thermal
		}
		if sample.Power == nil {
			sample.Power = prior.Power
		}
	}
	h.sample = &sample
	h.lastSeen = at
	ev = Event{Kind: EventHostMetrics, HostID: ref.HostID, Payload: map[string]any{"sample": obs.Sample}}
	return nil
}

func cpuObserved(s protocol.Sample) *time.Time {
	if s.CPU == nil {
		return nil
	}
	return s.CPU.ObservedAt
}

func memoryObserved(s protocol.Sample) *time.Time {
	if s.Memory == nil {
		return nil
	}
	return s.Memory.ObservedAt
}

func diskIOObserved(s protocol.Sample) *time.Time {
	if s.DiskIO == nil {
		return nil
	}
	return s.DiskIO.ObservedAt
}

func networkObserved(s protocol.Sample) *time.Time {
	if s.Network == nil {
		return nil
	}
	return s.Network.ObservedAt
}

func disksObserved(s protocol.Sample) *time.Time {
	if s.Disks == nil {
		return nil
	}
	return s.Disks.ObservedAt
}

func gpuObserved(s protocol.Sample) *time.Time {
	if s.GPU == nil {
		return nil
	}
	return s.GPU.ObservedAt
}

func thermalObserved(s protocol.Sample) *time.Time {
	if s.Thermal == nil {
		return nil
	}
	return s.Thermal.ObservedAt
}

func powerObserved(s protocol.Sample) *time.Time {
	if s.Power == nil {
		return nil
	}
	return s.Power.ObservedAt
}

// AcceptRuntime admits sanitized runtime telemetry for the local host only.
func (f *Fleet) AcceptRuntime(env protocol.RuntimeEnvelope, at time.Time) error {
	if err := protocol.ValidateRuntimeEnvelope(env); err != nil {
		return err
	}
	var ev Event
	err := func() error {
		f.mu.Lock()
		defer f.mu.Unlock()
		h, err := f.admit(env.HostID, env.Generation, at)
		if err != nil {
			return err
		}
		if h.identity.TrustState != protocol.TrustLocal {
			return fmt.Errorf("%w: remote runtime values stay unknown", ErrRuntimeAttribution)
		}
		if len(env.Services) > 0 {
			h.services = []protocol.ServiceEntry(env.Services)
			ev = Event{Kind: EventHostUpdated, HostID: env.HostID}
		}
		if env.AI != nil {
			h.ai = env.AI
			ev = Event{Kind: EventHostUpdated, HostID: env.HostID}
		}
		return nil
	}()
	if err != nil {
		return err
	}
	if ev.Kind != "" {
		f.publish(ev)
	}
	return nil
}

// Blocked reports whether a host waits for inventory/config change.
func (f *Fleet) Blocked(hostID string) bool {
	f.mu.RLock()
	defer f.mu.RUnlock()
	h, ok := f.hosts[hostID]
	return ok && h.blocked
}

// Block stops polling a host after a non-retryable identity/permission failure.
func (f *Fleet) Block(hostID, reason string) {
	f.mu.Lock()
	if h, ok := f.hosts[hostID]; ok {
		h.blocked = true
		h.blockReason = reason
	}
	f.mu.Unlock()
}

// MarkDegraded records a history/event degradation that must stay visible.
func (f *Fleet) MarkDegraded(reason string) {
	f.mu.Lock()
	f.degraded = reason != ""
	f.degradeWhy = reason
	f.mu.Unlock()
}

// Ref returns the host reference for scheduling, and whether it may be polled.
func (f *Fleet) Ref(hostID string) (protocol.HostRef, bool) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	h, ok := f.hosts[hostID]
	if !ok || h.revoked || h.blocked {
		return protocol.HostRef{}, false
	}
	return protocol.HostRef{HostID: hostID, Generation: h.generation}, true
}

// Pollable lists hosts that should be polled now.
func (f *Fleet) Pollable(now time.Time) []protocol.HostRef {
	f.mu.RLock()
	defer f.mu.RUnlock()
	refs := make([]protocol.HostRef, 0, len(f.hosts))
	ids := make([]string, 0, len(f.hosts))
	for id := range f.hosts {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	for _, id := range ids {
		h := f.hosts[id]
		if h.revoked || h.blocked {
			continue
		}
		local := h.identity.TrustState == protocol.TrustLocal
		if !f.settings.Monitors(id, local) {
			continue
		}
		if f.trustState(h, now) == protocol.TrustUnknown {
			continue
		}
		refs = append(refs, protocol.HostRef{HostID: id, Generation: h.generation})
	}
	return refs
}

// Host returns one projected host.
func (f *Fleet) Host(hostID string, now time.Time) (HostView, bool) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	h, ok := f.hosts[hostID]
	if !ok {
		return HostView{}, false
	}
	return f.view(h, now), true
}

func (f *Fleet) staleGroups(h *hostState, now time.Time) []string {
	intervals := map[string]time.Duration{
		protocol.GroupCPU:     f.intervals.Basic,
		protocol.GroupMemory:  f.intervals.Basic,
		protocol.GroupNetwork: f.intervals.Basic,
		protocol.GroupDiskIO:  f.intervals.Basic,
		protocol.GroupDisks:   f.intervals.Disk,
		protocol.GroupGPU:     f.intervals.GPU,
		protocol.GroupThermal: f.intervals.GPU,
		protocol.GroupPower:   f.intervals.GPU,
	}
	stale := make([]string, 0, len(intervals))
	for _, name := range protocol.SampleGroups {
		iv, ok := intervals[name]
		if !ok {
			continue
		}
		seen, have := h.groupSeen[name]
		if !have {
			continue
		}
		if now.Sub(seen) > 2*iv {
			stale = append(stale, name)
		}
	}
	sort.Strings(stale)
	return stale
}

func (f *Fleet) view(h *hostState, now time.Time) HostView {
	v := HostView{
		HostID:               h.identity.HostID,
		DisplayName:          h.identity.Alias,
		Platform:             h.identity.Platform,
		Trust:                f.trustState(h, now),
		Connectivity:         f.connectivity(h, now),
		MonitoringAccess:     h.identity.MonitoringAccess,
		Generation:           h.generation,
		CertificateExpiresAt: h.identity.CertificateExpiresAt,
		Local:                h.identity.TrustState == protocol.TrustLocal,
		Monitored:            f.settings.Monitors(h.identity.HostID, h.identity.TrustState == protocol.TrustLocal),
		Revoked:              h.revoked,
		Sample:               h.sample,
		Services:             h.services,
		AI:                   h.ai,
		StaleGroups:          f.staleGroups(h, now),
	}
	if !h.lastSeen.IsZero() {
		t := h.lastSeen
		v.LastSeenAt = &t
	}
	return v
}

// Snapshot returns a consistent view of the whole fleet.
func (f *Fleet) Snapshot(now time.Time) Snapshot {
	f.mu.RLock()
	defer f.mu.RUnlock()
	return f.snapshotLocked(now)
}

// SnapshotWithCursor preserves the existing fleet-to-event-bus lock order.
func (f *Fleet) SnapshotWithCursor(now time.Time, cursor func() string) (Snapshot, string) {
	f.mu.RLock()
	defer f.mu.RUnlock()
	snap := f.snapshotLocked(now)
	return snap, cursor()
}
func (f *Fleet) snapshotLocked(now time.Time) Snapshot {
	ids := make([]string, 0, len(f.hosts))
	for id := range f.hosts {
		ids = append(ids, id)
	}
	sort.Strings(ids)
	hosts := make([]HostView, 0, len(ids))
	for _, id := range ids {
		hosts = append(hosts, f.view(f.hosts[id], now))
	}
	health := Health{
		InventoryFresh:     f.leaseOK && now.Sub(f.lastRefresh) <= protocol.TrustLease,
		TrustLeaseValid:    f.leaseOK && now.Sub(f.lastRefresh) <= protocol.TrustLease,
		LastInventoryError: f.lastErr,
		Degraded:           f.degraded,
		Reason:             f.degradeWhy,
	}
	if !f.lastRefresh.IsZero() {
		t := f.lastRefresh
		health.LastInventoryAt = &t
	}
	return Snapshot{GeneratedAt: now, Hosts: hosts, Health: health}
}

// Link exposes the interface type for wiring.
type Link = link.Link
