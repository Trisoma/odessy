package link

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/odessy/odessy/services/monitoring/internal/protocol"
)

type fakeProvider struct {
	id      string
	inv     protocol.Inventory
	fail    bool
	closed  int
	beats   int
	samples int
}

func (f *fakeProvider) Inventory(context.Context) (protocol.Inventory, error) {
	if f.fail {
		return protocol.Inventory{}, &Failure{Code: "provider_down", Message: "down", Retryable: true}
	}
	return f.inv, nil
}
func (f *fakeProvider) Heartbeat(context.Context, protocol.HostRef) (protocol.Heartbeat, error) {
	f.beats++
	now := time.Now().UTC()
	return protocol.Heartbeat{HostID: hostIDFor(f.id), Generation: "g", BootID: "b", ObservedAt: now}, nil
}
func (f *fakeProvider) Sample(context.Context, protocol.HostRef, []string) (protocol.Observation, error) {
	f.samples++
	return protocol.Observation{HostID: hostIDFor(f.id), Generation: "g",
		Sample: protocol.Sample{ID: "s", BootID: "b", Sequence: 1,
			ObservedAt: time.Now().UTC(), IntervalMS: 1, Scope: "host"}}, nil
}
func (f *fakeProvider) Close() error { f.closed++; return nil }

func hostIDFor(id string) string {
	value := strings.Repeat("0", 64)
	return "host_" + value[:64-len(id)] + id
}

func providerInventory(id string) protocol.Inventory {
	return protocol.Inventory{Revision: id, Hosts: []protocol.HostIdentity{{
		HostID: hostIDFor(id), InstallationID: id, FingerprintSHA256: "SHA256:" + id,
		TrustState: protocol.TrustPaired, MonitoringAccess: protocol.AccessAllowed,
		Generation: "g" + id,
	}}}
}

func TestCompositeSharesOneInventoryAndRoutesByHost(t *testing.T) {
	a := &fakeProvider{id: "a", inv: providerInventory("a")}
	b := &fakeProvider{id: "b", inv: providerInventory("b")}
	source := Compose(a, b)
	defer source.Close()
	inv, err := source.Inventory(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if len(inv.Hosts) != 2 || inv.Revision == "" {
		t.Fatalf("inventory = %+v", inv)
	}
	ref := protocol.HostRef{HostID: hostIDFor("a"), Generation: "ga"}
	if _, err := source.Heartbeat(context.Background(), ref); err != nil {
		t.Fatal(err)
	}
	if a.beats != 1 || b.beats != 0 {
		t.Fatalf("heartbeat routed a=%d b=%d", a.beats, b.beats)
	}
	if _, err := source.Sample(context.Background(), protocol.HostRef{HostID: hostIDFor("b"), Generation: "gb"},
		[]string{protocol.GroupCPU}); err != nil {
		t.Fatal(err)
	}
	if a.samples != 0 || b.samples != 1 {
		t.Fatalf("sample routed a=%d b=%d", a.samples, b.samples)
	}
	if _, err := source.Heartbeat(context.Background(), protocol.HostRef{HostID: hostIDFor("c")}); err == nil {
		t.Fatal("unknown host must not be routed")
	}
}

func TestCompositeKeepsKnownHostsWhenProviderBrieflyFails(t *testing.T) {
	a := &fakeProvider{id: "a", inv: providerInventory("a")}
	b := &fakeProvider{id: "b", inv: providerInventory("b")}
	source := Compose(a, b)
	defer source.Close()
	if _, err := source.Inventory(context.Background()); err != nil {
		t.Fatal(err)
	}
	a.fail = true
	inv, err := source.Inventory(context.Background())
	if err != nil {
		t.Fatalf("one provider failure must keep the other: %v", err)
	}
	if len(inv.Hosts) != 2 {
		t.Fatalf("cached hosts lost: %+v", inv.Hosts)
	}
}

func TestCompositeRejectsDuplicateHostIdentity(t *testing.T) {
	a := &fakeProvider{id: "a", inv: providerInventory("a")}
	b := &fakeProvider{id: "a", inv: providerInventory("a")}
	source := Compose(a, b)
	defer source.Close()
	if _, err := source.Inventory(context.Background()); err == nil {
		t.Fatal("duplicate host identity must be rejected")
	}
}

func TestCompositeClosesEveryOwnedProvider(t *testing.T) {
	a := &fakeProvider{id: "a", inv: providerInventory("a")}
	b := &fakeProvider{id: "b", inv: providerInventory("b")}
	source := Compose(a, b)
	if err := source.Close(); err != nil {
		t.Fatal(err)
	}
	if a.closed != 1 || b.closed != 1 {
		t.Fatalf("closed a=%d b=%d", a.closed, b.closed)
	}
}

func TestCompositeAllProvidersFailed(t *testing.T) {
	a := &fakeProvider{id: "a", inv: providerInventory("a"), fail: true}
	source := Compose(a)
	defer source.Close()
	_, err := source.Inventory(context.Background())
	if err == nil {
		t.Fatal("all-provider failure must be reported")
	}
	if !errors.Is(err, context.Canceled) && !strings.Contains(err.Error(), "provider_down") {
		t.Fatalf("unexpected error: %v", err)
	}
}
