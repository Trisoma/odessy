package fleet

import (
	"context"
	"fmt"
	"math/rand"
	"sync"
	"time"

	"github.com/odessy/odessy/services/monitoring/internal/link"
	"github.com/odessy/odessy/services/monitoring/internal/protocol"
)

// Scheduler is the only component that decides when to poll a host.
type Scheduler struct {
	link      link.Link
	fleet     *Fleet
	clock     Clock
	intervals protocol.Intervals
	slots     chan struct{}
	jitter    func() time.Duration

	mu            sync.Mutex
	inflight      map[string]bool
	inventoryBusy bool
	nextInventory time.Time
	nextGroup     map[string]map[string]time.Time
	failures      map[string]int
	rotation      int
	closed        bool
	wg            sync.WaitGroup
}

// NewScheduler builds a scheduler with the contract slot count.
func NewScheduler(l link.Link, f *Fleet, clock Clock, intervals protocol.Intervals) *Scheduler {
	return &Scheduler{
		link:      l,
		fleet:     f,
		clock:     clock,
		intervals: intervals,
		slots:     make(chan struct{}, protocol.GlobalSlots-1),
		jitter:    defaultJitter,
		inflight:  make(map[string]bool),
		nextGroup: make(map[string]map[string]time.Time),
		failures:  make(map[string]int),
	}
}

func defaultJitter() time.Duration {
	// 1–60 s with a bounded multiplicative jitter.
	base := time.Duration(1+rand.Intn(2000)) * time.Millisecond
	if base > 60*time.Second {
		return 60 * time.Second
	}
	return base
}

// SetJitter overrides the retry backoff source for deterministic tests.
func (s *Scheduler) SetJitter(fn func() time.Duration) { s.jitter = fn }

func (s *Scheduler) tryAcquire() bool {
	select {
	case s.slots <- struct{}{}:
		return true
	default:
		return false
	}
}

func (s *Scheduler) release() { <-s.slots }

func (s *Scheduler) due(host string, group string, now time.Time) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	groups, ok := s.nextGroup[host]
	if !ok {
		return true
	}
	at, ok := groups[group]
	if !ok {
		return true
	}
	return !now.Before(at)
}

func (s *Scheduler) schedule(host string, group string, at time.Time) {
	s.mu.Lock()
	if s.nextGroup[host] == nil {
		s.nextGroup[host] = make(map[string]time.Time)
	}
	s.nextGroup[host][group] = at
	s.mu.Unlock()
}

// cycleGroups returns the group cadences the scheduler owns.
func (s *Scheduler) cycleGroups() []struct {
	name     string
	interval time.Duration
} {
	return []struct {
		name     string
		interval time.Duration
	}{
		{protocol.GroupCPU, s.intervals.Basic},
		{protocol.GroupMemory, s.intervals.Basic},
		{protocol.GroupNetwork, s.intervals.Basic},
		{protocol.GroupDiskIO, s.intervals.Basic},
		{protocol.GroupServices, s.intervals.Basic},
		{protocol.GroupGPU, s.intervals.GPU},
		{protocol.GroupThermal, s.intervals.GPU},
		{protocol.GroupPower, s.intervals.GPU},
		{protocol.GroupDisks, s.intervals.Disk},
	}
}

// Cycle dispatches every request that is due at now. It never blocks: hosts
// that cannot obtain a slot wait for the next cycle, so no backlog replays.
func (s *Scheduler) Cycle(ctx context.Context, now time.Time) {
	s.mu.Lock()
	if s.closed {
		s.mu.Unlock()
		return
	}
	s.mu.Unlock()

	if !now.Before(s.nextInventory) && !s.inventoryBusy {
		s.mu.Lock()
		s.inventoryBusy = true
		s.mu.Unlock()
		s.wg.Add(1)
		go func() {
			defer s.wg.Done()
			defer func() {
				s.mu.Lock()
				s.inventoryBusy = false
				s.mu.Unlock()
			}()
			rctx, cancel := context.WithTimeout(ctx, protocol.RequestDeadline)
			defer cancel()
			inv, err := s.link.Inventory(rctx)
			if err != nil {
				s.fleet.MarkInventoryFailure(now, err)
				s.schedule("", "inventory", now.Add(protocol.InventoryInterval))
				return
			}
			if err := s.fleet.ApplyInventory(inv, now); err != nil {
				s.fleet.MarkInventoryFailure(now, err)
				s.schedule("", "inventory", now.Add(protocol.InventoryInterval))
				return
			}
			s.schedule("", "inventory", now.Add(protocol.InventoryInterval))
		}()
	}

	refs := s.fleet.Pollable(now)
	if len(refs) == 0 {
		return
	}
	start := s.rotation % len(refs)
	s.rotation++

	for i := 0; i < len(refs); i++ {
		ref := refs[(start+i)%len(refs)]
		if ref.Generation == "" {
			continue
		}
		if !s.hostIdle(ref.HostID) {
			continue
		}
		if s.fleet.Blocked(ref.HostID) {
			continue
		}

		dueGroups := make([]string, 0, 4)
		for _, g := range s.cycleGroups() {
			if s.due(ref.HostID, g.name, now) {
				dueGroups = append(dueGroups, g.name)
			}
		}
		heartbeatDue := s.due(ref.HostID, "heartbeat", now)
		if !heartbeatDue && len(dueGroups) == 0 {
			continue
		}
		if !s.tryAcquire() {
			return
		}
		s.setInflight(ref.HostID, true)
		s.wg.Add(1)
		if heartbeatDue {
			s.dispatchHeartbeat(ctx, ref, now)
			continue
		}
		s.dispatchSample(ctx, ref, dueGroups, now)
	}
}

func (s *Scheduler) dispatchHeartbeat(ctx context.Context, ref protocol.HostRef, now time.Time) {
	go func() {
		defer s.wg.Done()
		defer s.release()
		defer s.setInflight(ref.HostID, false)
		rctx, cancel := context.WithTimeout(ctx, protocol.RequestDeadline)
		defer cancel()
		hb, err := s.link.Heartbeat(rctx, ref)
		if err != nil {
			s.failed(ref.HostID, "heartbeat", err, now)
			return
		}
		if err := s.fleet.AcceptHeartbeat(ref, hb, s.clock.Now()); err != nil {
			s.failed(ref.HostID, "heartbeat", err, now)
			return
		}
		s.succeeded(ref.HostID, "heartbeat", s.intervals.Heartbeat, now)
	}()
}

func (s *Scheduler) dispatchSample(ctx context.Context, ref protocol.HostRef, groups []string, now time.Time) {
	go func() {
		defer s.wg.Done()
		defer s.release()
		defer s.setInflight(ref.HostID, false)
		rctx, cancel := context.WithTimeout(ctx, protocol.RequestDeadline)
		defer cancel()
		obs, err := s.link.Sample(rctx, ref, groups)
		if err != nil {
			s.failed(ref.HostID, "sample", err, now)
			return
		}
		if err := s.fleet.AcceptObservation(ref, obs, s.clock.Now()); err != nil {
			s.failed(ref.HostID, "sample", err, now)
			return
		}
		for _, g := range groups {
			s.succeeded(ref.HostID, g, s.groupInterval(g), now)
		}
		s.succeeded(ref.HostID, "sample", s.intervals.Basic, now)
	}()
}

func (s *Scheduler) groupInterval(group string) time.Duration {
	switch group {
	case protocol.GroupDisks:
		return s.intervals.Disk
	case protocol.GroupGPU, protocol.GroupThermal, protocol.GroupPower:
		return s.intervals.GPU
	default:
		return s.intervals.Basic
	}
}

func (s *Scheduler) hostIdle(host string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	return !s.inflight[host]
}

func (s *Scheduler) setInflight(host string, v bool) {
	s.mu.Lock()
	s.inflight[host] = v
	s.mu.Unlock()
}

func (s *Scheduler) succeeded(host, group string, interval time.Duration, now time.Time) {
	s.mu.Lock()
	s.failures[host] = 0
	s.mu.Unlock()
	s.schedule(host, group, now.Add(interval))
}

func (s *Scheduler) failed(host, group string, err error, now time.Time) {
	if link.IsIdentityFailure(err) {
		// Permission/identity failures wait for an inventory or config change.
		s.fleet.Block(host, err.Error())
		return
	}
	s.mu.Lock()
	attempt := s.failures[host] + 1
	s.failures[host] = attempt
	s.mu.Unlock()
	backoff := s.jitter()
	if backoff < time.Second {
		backoff = time.Second
	}
	if backoff > 60*time.Second {
		backoff = 60 * time.Second
	}
	s.schedule(host, group, now.Add(backoff))
}

func (s *Scheduler) setClosed() {
	s.mu.Lock()
	s.closed = true
	s.mu.Unlock()
}

// Wait blocks until in-flight work drains.
func (s *Scheduler) Wait() { s.wg.Wait() }

// Close stops scheduling new work and waits for in-flight polls.
func (s *Scheduler) Close() {
	s.setClosed()
	s.Wait()
}

// Run drives Cycle on the heartbeat interval until ctx is done.
func (s *Scheduler) Run(ctx context.Context) {
	ticker := time.NewTicker(time.Second / 4)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			s.Close()
			return
		case <-ticker.C:
			s.Cycle(ctx, s.clock.Now())
		}
	}
}

var _ = fmt.Sprintf
