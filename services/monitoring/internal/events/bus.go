// Package events owns the atomic event cursor and bounded replay ring.
package events

import (
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"sync"

	"github.com/odessy/odessy/services/monitoring/internal/protocol"
)

// ErrResyncRequired signals an unknown or expired cursor.
var ErrResyncRequired = errors.New("resync required")

// ErrTooManySubscribers is returned when the subscriber cap is reached.
var ErrTooManySubscribers = errors.New("subscriber limit reached")

// Event is one sequenced monitor event.
type Event struct {
	size   int
	Seq    uint64
	Name   string
	HostID string
	Data   any
}

// ID renders the SSE id for this event.
func (e Event) ID(epoch string) string { return fmt.Sprintf("%s:%d", epoch, e.Seq) }

type subscriber struct {
	ch       chan Event
	done     chan struct{}
	bytes    int
	overflow bool
}

// Bus is a bounded event log with epoch-scoped sequence numbers.
type Bus struct {
	mu        sync.Mutex
	epoch     string
	seq       uint64
	ring      []Event
	ringBytes int
	ringSizes []int
	subs      map[*subscriber]struct{}
	resynced  uint64
}

// New builds a bus for one process epoch.
func New(epoch string) *Bus {
	return &Bus{epoch: epoch, subs: make(map[*subscriber]struct{})}
}

// Epoch returns the process epoch used in SSE ids.
func (b *Bus) Epoch() string { return b.epoch }

// Cursor returns the current `epoch:sequence` cursor.
func (b *Bus) Cursor() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return fmt.Sprintf("%s:%d", b.epoch, b.seq)
}

// Publish appends an event, evicting expired ring entries.
func (b *Bus) Publish(name, hostID string, data any) Event {
	size := 128 + len(hostID) + len(name)
	if raw, err := json.Marshal(data); err == nil {
		size += len(raw)
	}
	b.mu.Lock()
	b.seq++
	ev := Event{Seq: b.seq, Name: name, HostID: hostID, Data: data, size: size}
	b.ring = append(b.ring, ev)
	b.ringSizes = append(b.ringSizes, size)
	b.ringBytes += size
	for len(b.ring) > protocol.MaxEventRingEvents || b.ringBytes > protocol.MaxEventRingBytes {
		b.ringBytes -= b.ringSizes[0]
		b.ring = b.ring[1:]
		b.ringSizes = b.ringSizes[1:]
	}
	for sub := range b.subs {
		b.deliverLocked(sub, ev, size)
	}
	b.mu.Unlock()
	return ev
}

func (b *Bus) deliverLocked(sub *subscriber, ev Event, size int) {
	if sub.overflow {
		return
	}
	if sub.bytes+size > protocol.MaxSubscriberBytes {
		sub.overflow = true
		close(sub.ch)
		delete(b.subs, sub)
		b.resynced++
		return
	}
	select {
	case sub.ch <- ev:
		sub.bytes += size
	default:
		// The consumer is not keeping up. Close for resync; never drop a trust
		// transition silently.
		sub.overflow = true
		close(sub.ch)
		delete(b.subs, sub)
		b.resynced++
	}
}

// Subscription is one bounded event stream.
type Subscription struct {
	C    <-chan Event
	bus  *Bus
	sub  *subscriber
	once sync.Once
}

// Close releases the subscription.
func (s *Subscription) Close() {
	s.once.Do(func() {
		s.bus.mu.Lock()
		close(s.sub.done)
		if _, ok := s.bus.subs[s.sub]; ok {
			delete(s.bus.subs, s.sub)
			close(s.sub.ch)
		}
		s.bus.mu.Unlock()
	})
}

// Overflowed reports whether the consumer was closed for falling behind.
func (s *Subscription) Overflowed() bool {
	s.bus.mu.Lock()
	defer s.bus.mu.Unlock()
	return s.sub.overflow
}

// Subscribe attaches a subscriber, replaying at most the bounded ring.
func (b *Bus) Subscribe(lastEventID string) (*Subscription, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if len(b.subs) >= protocol.MaxSubscribers {
		return nil, ErrTooManySubscribers
	}
	sub := &subscriber{ch: make(chan Event, protocol.MaxSubscriberEvents), done: make(chan struct{})}
	var replay []Event
	if lastEventID != "" {
		epoch, rawSeq, ok := strings.Cut(lastEventID, ":")
		if !ok {
			return nil, ErrResyncRequired
		}
		seq, err := strconv.ParseUint(rawSeq, 10, 64)
		if err != nil {
			return nil, ErrResyncRequired
		}
		if epoch != b.epoch || seq > b.seq {
			return nil, ErrResyncRequired
		}
		oldest := uint64(0)
		if len(b.ring) > 0 {
			oldest = b.ring[0].Seq
		}
		if seq+1 < oldest && oldest > 0 {
			return nil, ErrResyncRequired
		}
		for _, ev := range b.ring {
			if ev.Seq > seq {
				replay = append(replay, ev)
			}
		}
	}
	queuedBytes := 0
	for _, ev := range replay {
		queuedBytes += ev.size
	}
	if len(replay) > protocol.MaxSubscriberEvents || queuedBytes > protocol.MaxSubscriberBytes {
		return nil, ErrResyncRequired
	}
	for _, ev := range replay {
		sub.ch <- ev
	}
	sub.bytes = queuedBytes
	b.subs[sub] = struct{}{}
	return &Subscription{C: sub.ch, bus: b, sub: sub}, nil
}

// SubscriberCount reports the current subscriber total.
func (b *Bus) SubscriberCount() int {
	b.mu.Lock()
	defer b.mu.Unlock()
	return len(b.subs)
}

// Ack frees queued-byte capacity only after the consumer receives this event.
func (s *Subscription) Ack(ev Event) {
	s.bus.mu.Lock()
	defer s.bus.mu.Unlock()
	s.sub.bytes -= ev.size
	if s.sub.bytes < 0 {
		s.sub.bytes = 0
	}
}
