package fleet

import (
	"errors"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/odessy/odessy/services/monitoring/internal/protocol"
)

var t0 = time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)

func TestPartialGroupSampleKeepsPreviousGroupsWithTheirOwnTimestamp(t *testing.T) {
	id := hostID('a')
	f, _ := newFleet(t, id)
	_ = f.ApplyInventory(inv("r1", identity(id, "g1", protocol.TrustPaired)), t0)
	first := sampleFor(id, "g1", "boot", "one", 1, t0)
	ref := protocol.HostRef{HostID: id, Generation: "g1"}
	if err := f.AcceptObservation(ref, first, t0); err != nil {
		t.Fatal(err)
	}
	next := sampleFor(id, "g1", "boot", "two", 2, t0.Add(time.Second))
	next.CPU = nil
	next.GPU = &protocol.GPUGroup{Group: protocol.Group{Status: protocol.CollectorUnsupported}}
	if err := f.AcceptObservation(ref, next, t0.Add(time.Second)); err != nil {
		t.Fatal(err)
	}
	host, _ := f.Host(id, t0.Add(time.Second))
	if host.Sample.CPU == nil || !host.Sample.CPU.ObservedAt.Equal(t0) {
		t.Fatal("partial poll erased/retimed CPU")
	}
}

func hostID(c byte) string { return "host_" + strings.Repeat(string(c), 64) }

func identity(id, generation string, trust protocol.TrustState) protocol.HostIdentity {
	return protocol.HostIdentity{
		HostID:            id,
		InstallationID:    "install-" + id,
		FingerprintSHA256: strings.Repeat("f", 64),
		Alias:             "alias",
		TrustState:        trust,
		MonitoringAccess:  protocol.AccessAllowed,
		Generation:        generation,
	}
}

func inv(rev string, hosts ...protocol.HostIdentity) protocol.Inventory {
	return protocol.Inventory{Revision: rev, Hosts: hosts}
}

func settingsFor(ids ...string) protocol.Settings {
	s := protocol.DefaultSettings()
	s.MonitoredHostIDs = ids
	return s
}

type recorder struct {
	mu     sync.Mutex
	events []Event
}

func (r *recorder) sink(ev Event) {
	r.mu.Lock()
	r.events = append(r.events, ev)
	r.mu.Unlock()
}

func (r *recorder) kinds(kind string) int {
	r.mu.Lock()
	defer r.mu.Unlock()
	n := 0
	for _, ev := range r.events {
		if ev.Kind == kind {
			n++
		}
	}
	return n
}

type fakeClock struct{}

func (fakeClock) Now() time.Time { return t0 }

func newFleet(t *testing.T, ids ...string) (*Fleet, *recorder) {
	t.Helper()
	rec := &recorder{}
	s := settingsFor(ids...)
	norm, err := s.Normalized()
	if err != nil {
		t.Fatalf("normalize settings: %v", err)
	}
	return New(fakeClock{}, norm, norm.Intervals(), rec.sink), rec
}

func TestInventoryProjectionAndRevocationTombstone(t *testing.T) {
	id := hostID('a')
	f, rec := newFleet(t, id)
	if err := f.ApplyInventory(inv("r1", identity(id, "g1", protocol.TrustPaired)), t0); err != nil {
		t.Fatalf("apply inventory: %v", err)
	}
	if rec.kinds(EventHostAdded) != 1 {
		t.Fatalf("expected one host.added event, got %d", rec.kinds(EventHostAdded))
	}

	ref := protocol.HostRef{HostID: id, Generation: "g1"}
	if err := f.AcceptHeartbeat(ref, protocol.Heartbeat{HostID: id, Generation: "g1", BootID: "b1", ObservedAt: t0}, t0); err != nil {
		t.Fatalf("heartbeat: %v", err)
	}
	if v, _ := f.Host(id, t0); v.Connectivity != protocol.ConnectivityOnline {
		t.Fatalf("expected online, got %s", v.Connectivity)
	}

	if err := f.ApplyInventory(inv("r2"), t0.Add(time.Second)); err != nil {
		t.Fatalf("apply empty inventory: %v", err)
	}
	v, ok := f.Host(id, t0.Add(time.Second))
	if !ok || !v.Revoked || v.Trust != protocol.TrustRevoked {
		t.Fatalf("expected revoked tombstone, got %+v", v)
	}

	// The generation authority changed: a delayed reply for the old binding must
	// not restore liveness or history.
	err := f.AcceptHeartbeat(ref, protocol.Heartbeat{HostID: id, Generation: "g1", BootID: "b1", ObservedAt: t0}, t0.Add(2*time.Second))
	if !errors.Is(err, ErrHostRevoked) {
		t.Fatalf("expected revoked rejection, got %v", err)
	}
}

func TestTrustLeaseExpiryMakesTrustUnknownAndBlocksAdmission(t *testing.T) {
	id := hostID('b')
	f, _ := newFleet(t, id)
	if err := f.ApplyInventory(inv("r1", identity(id, "g1", protocol.TrustPaired)), t0); err != nil {
		t.Fatal(err)
	}
	ref := protocol.HostRef{HostID: id, Generation: "g1"}

	late := t0.Add(protocol.TrustLease + time.Millisecond)
	if err := f.AcceptHeartbeat(ref, protocol.Heartbeat{HostID: id, Generation: "g1", BootID: "b1"}, late); !errors.Is(err, ErrTrustUnknown) {
		t.Fatalf("expected trust unknown after lease expiry, got %v", err)
	}
	v, _ := f.Host(id, late)
	if v.Trust != protocol.TrustUnknown || v.Connectivity != protocol.ConnectivityUnknown {
		t.Fatalf("expected unknown trust/connectivity, got %+v", v)
	}
}

func TestFailedRefreshIsNotAnEmptySuccess(t *testing.T) {
	id := hostID('c')
	f, _ := newFleet(t, id)
	if err := f.ApplyInventory(inv("r1", identity(id, "g1", protocol.TrustPaired)), t0); err != nil {
		t.Fatal(err)
	}
	f.MarkInventoryFailure(t0.Add(time.Second), errors.New("worker down"))
	snap := f.Snapshot(t0.Add(time.Second))
	if len(snap.Hosts) != 1 {
		t.Fatalf("failed refresh must keep known hosts, got %d", len(snap.Hosts))
	}
	if snap.Hosts[0].Revoked {
		t.Fatal("failed refresh must not revoke a host")
	}
	if snap.Health.LastInventoryError == "" {
		t.Fatal("failed refresh must report the error")
	}
}

func TestConnectivityBoundaries(t *testing.T) {
	id := hostID('d')
	f, _ := newFleet(t, id)
	if err := f.ApplyInventory(inv("r1", identity(id, "g1", protocol.TrustPaired)), t0); err != nil {
		t.Fatal(err)
	}
	ref := protocol.HostRef{HostID: id, Generation: "g1"}
	if err := f.AcceptHeartbeat(ref, protocol.Heartbeat{HostID: id, Generation: "g1", BootID: "b1"}, t0); err != nil {
		t.Fatal(err)
	}
	cases := []struct {
		offset time.Duration
		want   protocol.Connectivity
	}{
		{10 * time.Second, protocol.ConnectivityOnline},
		{10*time.Second + time.Millisecond, protocol.ConnectivityStale},
		{29*time.Second + time.Millisecond, protocol.ConnectivityStale},
		{30 * time.Second, protocol.ConnectivityOffline},
	}
	for _, tc := range cases {
		at := t0.Add(tc.offset)
		if err := f.ApplyInventory(inv("r1", identity(id, "g1", protocol.TrustPaired)), at); err != nil {
			t.Fatalf("refresh at %s: %v", at, err)
		}
		v, _ := f.Host(id, at)
		if v.Connectivity != tc.want {
			t.Fatalf("at %s expected %s, got %s", tc.offset, tc.want, v.Connectivity)
		}
	}
}

func sampleFor(id, generation, bootID, sampleID string, seq uint64, observed time.Time) protocol.Observation {
	return protocol.Observation{
		HostID:     id,
		Generation: generation,
		Sample: protocol.Sample{
			ID:         sampleID,
			BootID:     bootID,
			Sequence:   seq,
			ObservedAt: observed,
			IntervalMS: 10000,
			Scope:      "host",
			CPU: &protocol.CPUGroup{
				Group:              protocol.Group{Status: protocol.CollectorOK, ObservedAt: &observed},
				UtilizationPercent: ptr(12.5),
			},
		},
	}
}

func ptr(v float64) *float64 { return &v }

func TestObservationAdmissionRejectsDuplicateAndOutOfOrder(t *testing.T) {
	id := hostID('e')
	f, _ := newFleet(t, id)
	if err := f.ApplyInventory(inv("r1", identity(id, "g1", protocol.TrustPaired)), t0); err != nil {
		t.Fatal(err)
	}
	ref := protocol.HostRef{HostID: id, Generation: "g1"}
	if err := f.AcceptObservation(ref, sampleFor(id, "g1", "b1", "s1", 1, t0), t0); err != nil {
		t.Fatalf("first sample rejected: %v", err)
	}
	if err := f.AcceptObservation(ref, sampleFor(id, "g1", "b1", "s1", 1, t0), t0); !errors.Is(err, ErrDuplicate) {
		t.Fatalf("expected duplicate rejection, got %v", err)
	}
	if err := f.AcceptObservation(ref, sampleFor(id, "g1", "b1", "s0", 0, t0), t0); !errors.Is(err, ErrOutOfOrder) {
		t.Fatalf("expected out-of-order rejection, got %v", err)
	}
	if err := f.AcceptObservation(ref, sampleFor(id, "g2", "b1", "s2", 2, t0), t0); !errors.Is(err, ErrGenerationMismatch) {
		t.Fatalf("expected generation rejection, got %v", err)
	}
}

func TestPartialMetricsAreStaleNotZeroAndHealthIsIndependent(t *testing.T) {
	id := hostID('f')
	f, _ := newFleet(t, id)
	if err := f.ApplyInventory(inv("r1", identity(id, "g1", protocol.TrustPaired)), t0); err != nil {
		t.Fatal(err)
	}
	ref := protocol.HostRef{HostID: id, Generation: "g1"}
	obs := sampleFor(id, "g1", "b1", "s1", 1, t0)
	obs.CPU.Group.Status = protocol.CollectorPermissionDenied
	obs.CPU.UtilizationPercent = nil
	if err := f.AcceptObservation(ref, obs, t0); err != nil {
		t.Fatal(err)
	}
	if err := f.AcceptHeartbeat(ref, protocol.Heartbeat{HostID: id, Generation: "g1", BootID: "b1"}, t0.Add(5*time.Second)); !errors.Is(err, ErrTrustUnknown) {
		// The lease expires without a successful refresh; a heartbeat cannot fix it.
		t.Fatalf("expected trust unknown, got %v", err)
	}
	at := t0.Add(5 * time.Second)
	if err := f.ApplyInventory(inv("r1", identity(id, "g1", protocol.TrustPaired)), at); err != nil {
		t.Fatal(err)
	}
	if err := f.AcceptHeartbeat(ref, protocol.Heartbeat{HostID: id, Generation: "g1", BootID: "b1"}, at); err != nil {
		t.Fatal(err)
	}
	v, _ := f.Host(id, at)
	if v.Connectivity != protocol.ConnectivityOnline {
		t.Fatalf("heartbeat must establish online even when metrics fail, got %s", v.Connectivity)
	}
	if v.Sample.CPU != nil && v.Sample.CPU.UtilizationPercent != nil {
		t.Fatal("missing metric must stay null, never a fabricated zero")
	}
	later := t0.Add(25 * time.Second)
	if err := f.ApplyInventory(inv("r1", identity(id, "g1", protocol.TrustPaired)), later); err != nil {
		t.Fatal(err)
	}
	v, _ = f.Host(id, later)
	if len(v.StaleGroups) == 0 {
		t.Fatal("metrics older than two own intervals must be reported stale")
	}
}

func TestRuntimeAttributionStaysLocal(t *testing.T) {
	local := hostID('0')
	remote := hostID('1')
	f, _ := newFleet(t, local, remote)
	if err := f.ApplyInventory(inv("r1",
		identity(local, "g1", protocol.TrustLocal),
		identity(remote, "g2", protocol.TrustPaired),
	), t0); err != nil {
		t.Fatal(err)
	}
	env := protocol.RuntimeEnvelope{
		SchemaVersion: protocol.SchemaVersion,
		HostID:        remote,
		Generation:    "g2",
		SourceID:      "py-1",
		Sequence:      1,
		ObservedAt:    t0,
	}
	if err := f.AcceptRuntime(env, t0); !errors.Is(err, ErrRuntimeAttribution) {
		t.Fatalf("remote runtime values must stay unknown, got %v", err)
	}
	env.HostID = local
	env.Generation = "g1"
	if err := f.AcceptRuntime(env, t0); err != nil {
		t.Fatalf("local runtime observation rejected: %v", err)
	}
}

func TestPollableHonoursMonitoredHostsAndAccess(t *testing.T) {
	id := hostID('9')
	f, _ := newFleet(t) // no monitored host ids, include_local false
	if err := f.ApplyInventory(inv("r1", identity(id, "g1", protocol.TrustPaired)), t0); err != nil {
		t.Fatal(err)
	}
	if refs := f.Pollable(t0); len(refs) != 0 {
		t.Fatalf("enrollment alone must not enable monitoring, got %d pollable", len(refs))
	}

	g, _ := newFleet(t, id)
	denied := identity(id, "g1", protocol.TrustPaired)
	denied.MonitoringAccess = protocol.AccessDenied
	if err := g.ApplyInventory(inv("r1", denied), t0); err != nil {
		t.Fatal(err)
	}
	ref := protocol.HostRef{HostID: id, Generation: "g1"}
	if err := g.AcceptHeartbeat(ref, protocol.Heartbeat{HostID: id, Generation: "g1", BootID: "b1"}, t0); !errors.Is(err, ErrAccessDenied) {
		t.Fatalf("expected access denial, got %v", err)
	}
}
