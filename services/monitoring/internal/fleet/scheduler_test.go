package fleet

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/odessy/odessy/services/monitoring/internal/protocol"
	"github.com/odessy/odessy/services/monitoring/internal/testsupport"
)

func TestSchedulerKeepsInventoryResponsiveUnderStalledPolls(t *testing.T) {
	ids := []string{hostID('1'), hostID('2'), hostID('3'), hostID('4'), hostID('5')}
	f, _ := newFleet(t, ids...)
	hosts := make([]protocol.HostIdentity, 0, len(ids))
	for _, id := range ids {
		hosts = append(hosts, identity(id, "g1", protocol.TrustPaired))
	}
	if err := f.ApplyInventory(inv("r1", hosts...), t0); err != nil {
		t.Fatal(err)
	}

	fake := testsupport.NewFakeLink()
	fake.SampleFn = func(ref protocol.HostRef, groups []string) (protocol.Observation, error) {
		time.Sleep(50 * time.Millisecond)
		return protocol.Observation{}, &fakeTimeout{}
	}
	fake.HeartbeatFn = func(ref protocol.HostRef) (protocol.Heartbeat, error) {
		time.Sleep(50 * time.Millisecond)
		return protocol.Heartbeat{}, &fakeTimeout{}
	}

	clock := testsupport.NewClock(t0)
	s := NewScheduler(fake, f, clock, f.intervals)
	s.SetJitter(func() time.Duration { return time.Second })

	// First cycle dispatches heartbeats for every pollable host; only three
	// sample slots exist plus the reserved inventory slot.
	s.Cycle(context.Background(), t0)
	time.Sleep(20 * time.Millisecond)
	s.Cycle(context.Background(), t0.Add(protocol.InventoryInterval))
	time.Sleep(20 * time.Millisecond)
	s.Cycle(context.Background(), t0.Add(2*protocol.InventoryInterval))
	s.Wait()

	calls := fake.Record()
	inventories, heartbeats := 0, 0
	for _, c := range calls {
		switch c.Op {
		case protocol.OpInventory:
			inventories++
		case protocol.OpHeartbeat:
			heartbeats++
		case protocol.OpSample:
			t.Fatalf("stalled heartbeat must not overlap a sample for the same host")
		}
	}
	if inventories < 2 {
		t.Fatalf("inventory must keep refreshing while polls stall, got %d", inventories)
	}
	if heartbeats > len(ids) {
		t.Fatalf("scheduler re-dispatched a host with work in flight: %d heartbeats for %d hosts", heartbeats, len(ids))
	}
}

type fakeTimeout struct{}

func (f *fakeTimeout) Error() string { return "deadline_exceeded" }

func TestSchedulerCoalescesOverdueGroupsAndHonoursCadence(t *testing.T) {
	id := hostID('7')
	f, _ := newFleet(t, id)
	if err := f.ApplyInventory(inv("r1", identity(id, "g1", protocol.TrustLocal)), t0); err != nil {
		t.Fatal(err)
	}
	fake := testsupport.NewFakeLink()
	fake.HeartbeatFn = func(ref protocol.HostRef) (protocol.Heartbeat, error) {
		return protocol.Heartbeat{HostID: ref.HostID, Generation: ref.Generation, BootID: "b1", ObservedAt: t0}, nil
	}
	fake.SampleFn = func(ref protocol.HostRef, groups []string) (protocol.Observation, error) {
		return protocol.Observation{
			HostID:     ref.HostID,
			Generation: ref.Generation,
			Sample: protocol.Sample{
				ID:         "s-1",
				BootID:     "b1",
				Sequence:   1,
				ObservedAt: t0,
				IntervalMS: 10000,
				Scope:      "host",
			},
		}, nil
	}
	clock := testsupport.NewClock(t0)
	s := NewScheduler(fake, f, clock, f.intervals)
	s.Cycle(context.Background(), t0)
	s.Wait()
	s.Cycle(context.Background(), t0.Add(100*time.Millisecond))
	s.Wait()

	var sampleGroups []string
	for _, c := range fake.Record() {
		if c.Op == protocol.OpSample {
			sampleGroups = c.Groups
		}
	}
	if len(sampleGroups) == 0 {
		t.Fatal("expected a coalesced sample request")
	}
	joined := strings.Join(sampleGroups, ",")
	for _, want := range []string{protocol.GroupCPU, protocol.GroupMemory, protocol.GroupNetwork, protocol.GroupDiskIO, protocol.GroupGPU, protocol.GroupThermal, protocol.GroupPower, protocol.GroupDisks} {
		if !strings.Contains(joined, want) {
			t.Fatalf("expected overdue group %q in coalesced request %v", want, sampleGroups)
		}
	}
}
