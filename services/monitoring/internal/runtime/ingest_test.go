package runtime

import (
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/odessy/odessy/services/monitoring/internal/fleet"
	"github.com/odessy/odessy/services/monitoring/internal/protocol"
)

var t0 = time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)

func hostID(c byte) string { return "host_" + strings.Repeat(string(c), 64) }

type fixedClock struct{}

func (fixedClock) Now() time.Time { return t0 }

func newFleet(t *testing.T, ids ...protocol.HostIdentity) *fleet.Fleet {
	t.Helper()
	settings := protocol.DefaultSettings()
	norm, err := settings.Normalized()
	if err != nil {
		t.Fatal(err)
	}
	f := fleet.New(fixedClock{}, norm, norm.Intervals(), nil)
	if err := f.ApplyInventory(protocol.Inventory{Revision: "r1", Hosts: ids}, t0); err != nil {
		t.Fatal(err)
	}
	return f
}

func identity(id string, trust protocol.TrustState) protocol.HostIdentity {
	return protocol.HostIdentity{
		HostID:            id,
		InstallationID:    "install-" + id,
		FingerprintSHA256: strings.Repeat("f", 64),
		TrustState:        trust,
		MonitoringAccess:  protocol.AccessAllowed,
		Generation:        "g1",
	}
}

func body(id string, seq uint64, extra string) []byte {
	return []byte(fmt.Sprintf(
		`{"schema_version":1,"host_id":%q,"generation":"g1","source_id":"py","sequence":%d,"observed_at":"2026-01-01T00:00:00Z"%s}`,
		id, seq, extra))
}

func TestHandleAcceptsSanitizedLocalObservation(t *testing.T) {
	local := hostID('a')
	f := newFleet(t, identity(local, protocol.TrustLocal))
	in := New(f, fixedClock{}.Now)
	if err := in.Handle(body(local, 1, `,"services":[{"name":"tts","status":"unhealthy"}]`)); err != nil {
		t.Fatalf("valid observation rejected: %v", err)
	}
}

func TestHandleDeduplicatesPerSourceSequence(t *testing.T) {
	local := hostID('a')
	f := newFleet(t, identity(local, protocol.TrustLocal))
	in := New(f, fixedClock{}.Now)
	if err := in.Handle(body(local, 5, "")); err != nil {
		t.Fatal(err)
	}
	if err := in.Handle(body(local, 5, "")); !errors.Is(err, fleet.ErrOutOfOrder) {
		t.Fatalf("replayed sequence must be rejected, got %v", err)
	}
	if err := in.Handle(body(local, 4, "")); !errors.Is(err, fleet.ErrOutOfOrder) {
		t.Fatalf("decreasing sequence must be rejected, got %v", err)
	}
	if err := in.Handle(body(local, 6, "")); err != nil {
		t.Fatalf("increasing sequence rejected: %v", err)
	}
}

func TestHandleRejectsSecretsUnknownFieldsAndOversize(t *testing.T) {
	local := hostID('a')
	f := newFleet(t, identity(local, protocol.TrustLocal))
	in := New(f, fixedClock{}.Now)

	if err := in.Handle(body(local, 1, `,"prompt":"secret"`)); err == nil {
		t.Fatal("secret-bearing field must be rejected")
	}
	if err := in.Handle(body(local, 1, `,"unknown_field":1`)); err == nil {
		t.Fatal("unknown field must be rejected")
	}
	big := body(local, 1, `,"services":[]`)
	padded := append(big[:len(big)-1], []byte(strings.Repeat(" ", protocol.MaxRuntimePayloadBytes)+"}")...)
	if err := in.Handle(padded); !errors.Is(err, protocol.ErrResourceLimit) {
		t.Fatalf("oversize body must be rejected, got %v", err)
	}
	if err := in.Handle(body(local, 1, `,"schema_version":2`)); err == nil {
		t.Fatal("duplicate/conflicting schema version must be rejected")
	}
}

func TestHandleRejectsRemoteAttribution(t *testing.T) {
	local := hostID('a')
	remote := hostID('b')
	f := newFleet(t, identity(local, protocol.TrustLocal), identity(remote, protocol.TrustPaired))
	in := New(f, fixedClock{}.Now)
	if err := in.Handle(body(remote, 1, "")); !errors.Is(err, fleet.ErrRuntimeAttribution) {
		t.Fatalf("remote attribution must be refused, got %v", err)
	}
}
