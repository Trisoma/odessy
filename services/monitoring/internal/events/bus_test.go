package events

import (
	"errors"
	"testing"

	"github.com/odessy/odessy/services/monitoring/internal/protocol"
)

func TestReplayBeyondSubscriberCapacityRequiresResync(t *testing.T) {
	bus := New("test")
	for i := 0; i < 129; i++ {
		bus.Publish("host.updated", "", nil)
	}
	sub, err := bus.Subscribe("test:0")
	if err == nil {
		sub.Close()
		t.Fatal("oversized replay must require resync before subscription")
	}
}

func TestCursorAndReplayAreEpochScoped(t *testing.T) {
	b := New("epoch-a")
	b.Publish("host.added", "host_1", nil)
	b.Publish("host.trust_changed", "host_1", nil)

	sub, err := b.Subscribe("epoch-a:1")
	if err != nil {
		t.Fatalf("replay from a known cursor failed: %v", err)
	}
	defer sub.Close()
	ev := <-sub.C
	if ev.Seq != 2 || ev.Name != "host.trust_changed" {
		t.Fatalf("expected replayed seq 2, got %+v", ev)
	}

	if _, err := b.Subscribe("epoch-b:1"); !errors.Is(err, ErrResyncRequired) {
		t.Fatalf("unknown epoch must require resync, got %v", err)
	}
	if _, err := b.Subscribe("epoch-a:99"); !errors.Is(err, ErrResyncRequired) {
		t.Fatalf("future cursor must require resync, got %v", err)
	}
}

func TestSlowSubscriberIsClosedForResyncWithoutBlockingOthers(t *testing.T) {
	b := New("epoch-a")
	stalled, err := b.Subscribe("")
	if err != nil {
		t.Fatal(err)
	}
	defer stalled.Close()
	healthy, err := b.Subscribe("")
	if err != nil {
		t.Fatal(err)
	}
	defer healthy.Close()

	go func() {
		for range healthy.C {
		}
	}()

	for i := 0; i < protocol.MaxSubscriberEvents+10; i++ {
		b.Publish("host.updated", "host_1", map[string]any{"i": i})
		select {
		case <-healthy.C:
		default:
		}
	}
	if !stalled.Overflowed() {
		t.Fatal("stalled subscriber must be marked overflowed so the stream resyncs")
	}
	if b.SubscriberCount() != 1 {
		t.Fatalf("overflowed subscriber should be removed, %d remain", b.SubscriberCount())
	}
}

func TestSubscriberCapIsEnforced(t *testing.T) {
	b := New("epoch-a")
	subs := make([]*Subscription, 0, protocol.MaxSubscribers)
	for i := 0; i < protocol.MaxSubscribers; i++ {
		sub, err := b.Subscribe("")
		if err != nil {
			t.Fatalf("subscriber %d rejected: %v", i, err)
		}
		subs = append(subs, sub)
	}
	if _, err := b.Subscribe(""); !errors.Is(err, ErrTooManySubscribers) {
		t.Fatalf("expected subscriber cap error, got %v", err)
	}
	for _, sub := range subs {
		sub.Close()
	}
}

func TestRingEvictionRequiresResync(t *testing.T) {
	b := New("epoch-a")
	for i := 0; i < protocol.MaxEventRingEvents+5; i++ {
		b.Publish("host.metrics", "host_1", nil)
	}
	if _, err := b.Subscribe("epoch-a:1"); !errors.Is(err, ErrResyncRequired) {
		t.Fatalf("expired replay cursor must require resync, got %v", err)
	}
}
