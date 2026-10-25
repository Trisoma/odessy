// Package testsupport provides deterministic clocks and scripted fake links.
package testsupport

import (
	"context"
	"sync"
	"time"

	"github.com/odessy/odessy/services/monitoring/internal/protocol"
)

// Clock is a manual clock; tests advance it instead of sleeping.
type Clock struct {
	mu  sync.Mutex
	now time.Time
}

// NewClock starts a clock at t.
func NewClock(t time.Time) *Clock { return &Clock{now: t} }

// Now returns the current fake time.
func (c *Clock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.now
}

// Advance moves the clock forward.
func (c *Clock) Advance(d time.Duration) {
	c.mu.Lock()
	c.now = c.now.Add(d)
	c.mu.Unlock()
}

// Call records one fake link invocation.
type Call struct {
	Op     string
	HostID string
	Groups []string
}

// FakeLink is a scriptable Link with delay, failure and duplicate support.
type FakeLink struct {
	mu sync.Mutex

	InventoryResult *protocol.Inventory
	InventoryError  error
	HeartbeatFn     func(protocol.HostRef) (protocol.Heartbeat, error)
	SampleFn        func(protocol.HostRef, []string) (protocol.Observation, error)
	Delay           time.Duration
	Block           <-chan struct{}

	Calls  []Call
	Closed bool
}

// NewFakeLink returns an empty fake link.
func NewFakeLink() *FakeLink { return &FakeLink{} }

func (f *FakeLink) note(c Call) {
	f.mu.Lock()
	f.Calls = append(f.Calls, c)
	f.mu.Unlock()
}

// Record returns a copy of the recorded calls.
func (f *FakeLink) Record() []Call {
	f.mu.Lock()
	defer f.mu.Unlock()
	out := make([]Call, len(f.Calls))
	copy(out, f.Calls)
	return out
}

func (f *FakeLink) wait(ctx context.Context) error {
	if f.Block != nil {
		select {
		case <-f.Block:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	if f.Delay > 0 {
		select {
		case <-time.After(f.Delay):
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	return nil
}

// Inventory implements Link.
func (f *FakeLink) Inventory(ctx context.Context) (protocol.Inventory, error) {
	f.note(Call{Op: protocol.OpInventory})
	if err := f.wait(ctx); err != nil {
		return protocol.Inventory{}, err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.InventoryError != nil {
		return protocol.Inventory{}, f.InventoryError
	}
	if f.InventoryResult == nil {
		return protocol.Inventory{}, nil
	}
	return *f.InventoryResult, nil
}

// Heartbeat implements Link.
func (f *FakeLink) Heartbeat(ctx context.Context, ref protocol.HostRef) (protocol.Heartbeat, error) {
	f.note(Call{Op: protocol.OpHeartbeat, HostID: ref.HostID})
	if err := f.wait(ctx); err != nil {
		return protocol.Heartbeat{}, err
	}
	f.mu.Lock()
	fn := f.HeartbeatFn
	f.mu.Unlock()
	if fn == nil {
		return protocol.Heartbeat{HostID: ref.HostID, Generation: ref.Generation, BootID: "boot-1", ObservedAt: time.Now().UTC()}, nil
	}
	return fn(ref)
}

// Sample implements Link.
func (f *FakeLink) Sample(ctx context.Context, ref protocol.HostRef, groups []string) (protocol.Observation, error) {
	f.note(Call{Op: protocol.OpSample, HostID: ref.HostID, Groups: groups})
	if err := f.wait(ctx); err != nil {
		return protocol.Observation{}, err
	}
	f.mu.Lock()
	fn := f.SampleFn
	f.mu.Unlock()
	if fn == nil {
		return protocol.Observation{}, nil
	}
	return fn(ref, groups)
}

// Close implements Link.
func (f *FakeLink) Close() error {
	f.mu.Lock()
	f.Closed = true
	f.mu.Unlock()
	return nil
}
