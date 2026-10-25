// Package history keeps bounded recent samples and durable aggregates.
package history

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"sort"
	"sync"
	"time"

	"github.com/odessy/odessy/services/monitoring/internal/protocol"
)

// ErrLimit maps to RESOURCE_LIMIT at the HTTP layer.
var ErrLimit = errors.New("history limit reached")

// ErrInvalidSeries maps to INVALID_REQUEST.
var ErrInvalidSeries = errors.New("invalid series")

// ErrInvalidRange maps to INVALID_RANGE.
var ErrInvalidRange = errors.New("invalid range")

// Point is one chart point.
type Point struct {
	Min      float64   `json:"min"`
	Max      float64   `json:"max"`
	Count    int       `json:"count"`
	At       time.Time `json:"at"`
	Value    float64   `json:"value"`
	Coverage float64   `json:"coverage"`
}

// Series is one named time series.
type Series struct {
	Name   string  `json:"name"`
	Points []Point `json:"points"`
}

type entry struct {
	sample protocol.Sample
	size   int
}

// Memory retains recent samples within a horizon and byte cap.
type Memory struct {
	mu      sync.Mutex
	horizon time.Duration
	cap     int
	bytes   int
	perHost map[string][]entry
	seen    map[string]map[string]struct{}
}

// NewMemory builds the recent-sample ring.
func NewMemory(horizon time.Duration, byteCap int) *Memory {
	return &Memory{horizon: horizon, cap: byteCap, perHost: make(map[string][]entry), seen: make(map[string]map[string]struct{})}
}

// Append stores a sample, deduplicating by observation id.
func (m *Memory) Append(hostID string, sample protocol.Sample) {
	raw, err := json.Marshal(sample)
	size := len(raw) + 64
	if err != nil {
		size = 1024
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.seen[hostID] == nil {
		m.seen[hostID] = make(map[string]struct{})
	}
	if _, dup := m.seen[hostID][sample.ID]; dup {
		return
	}
	m.seen[hostID][sample.ID] = struct{}{}
	m.perHost[hostID] = append(m.perHost[hostID], entry{sample: sample, size: size})
	m.bytes += size
	cutoff := sample.ObservedAt.Add(-m.horizon)
	for id, list := range m.perHost {
		drop := 0
		for drop < len(list) && (list[drop].sample.ObservedAt.Before(cutoff) || m.bytes > m.cap) {
			m.bytes -= list[drop].size
			delete(m.seen[id], list[drop].sample.ID)
			drop++
		}
		if drop > 0 {
			m.perHost[id] = list[drop:]
		}
	}
}

// Bytes reports current retained bytes.
func (m *Memory) Bytes() int {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.bytes
}

type extractor struct {
	name string
	get  func(protocol.Sample) (float64, bool)
	sum  bool
}

func known(v *float64) (float64, bool) {
	if v == nil {
		return 0, false
	}
	return *v, true
}

func firstOf(list []*float64) (float64, bool) {
	for _, v := range list {
		if v != nil {
			return *v, true
		}
	}
	return 0, false
}

var extractors = []extractor{
	{"cpu.utilization_percent", func(s protocol.Sample) (float64, bool) {
		if s.CPU == nil {
			return 0, false
		}
		return known(s.CPU.UtilizationPercent)
	}, false},
	{"cpu.load_1", func(s protocol.Sample) (float64, bool) {
		if s.CPU == nil {
			return 0, false
		}
		return known(s.CPU.Load1)
	}, false},
	{"memory.used_bytes", func(s protocol.Sample) (float64, bool) {
		if s.Memory == nil {
			return 0, false
		}
		return known(s.Memory.UsedBytes)
	}, false},
	{"memory.total_bytes", func(s protocol.Sample) (float64, bool) {
		if s.Memory == nil {
			return 0, false
		}
		return known(s.Memory.TotalBytes)
	}, false},
	{"memory.utilization_percent", func(s protocol.Sample) (float64, bool) {
		if s.Memory == nil {
			return 0, false
		}
		return known(s.Memory.UtilizationPercent)
	}, false},
	{"gpu.utilization_percent", func(s protocol.Sample) (float64, bool) {
		if s.GPU == nil {
			return 0, false
		}
		var vals []*float64
		for _, g := range s.GPU.GPUs {
			vals = append(vals, g.UtilizationPercent)
		}
		return firstOf(vals)
	}, false},
	{"gpu.memory_used_bytes", func(s protocol.Sample) (float64, bool) {
		if s.GPU == nil {
			return 0, false
		}
		var vals []*float64
		for _, g := range s.GPU.GPUs {
			vals = append(vals, g.MemoryUsedBytes)
		}
		return firstOf(vals)
	}, false},
	{"thermal.max_celsius", func(s protocol.Sample) (float64, bool) {
		if s.Thermal == nil {
			return 0, false
		}
		best := math.Inf(-1)
		found := false
		for _, sensor := range s.Thermal.Sensors {
			if sensor.TemperatureCelsius == nil {
				continue
			}
			found = true
			best = math.Max(best, *sensor.TemperatureCelsius)
		}
		return best, found
	}, false},
	{"power.total_watts", func(s protocol.Sample) (float64, bool) {
		if s.Power == nil {
			return 0, false
		}
		total := 0.0
		found := false
		for _, rail := range s.Power.Rails {
			if rail.Watts == nil {
				continue
			}
			found = true
			total += *rail.Watts
		}
		return total, found
	}, true},
	{"disks.used_bytes", func(s protocol.Sample) (float64, bool) {
		if s.Disks == nil {
			return 0, false
		}
		total := 0.0
		found := false
		for _, d := range s.Disks.Disks {
			if d.UsedBytes == nil {
				continue
			}
			found = true
			total += *d.UsedBytes
		}
		return total, found
	}, true},
	{"network.receive_bytes_per_second", func(s protocol.Sample) (float64, bool) {
		if s.Network == nil {
			return 0, false
		}
		total := 0.0
		found := false
		for _, nic := range s.Network.Interfaces {
			if nic.ReceiveBPS == nil {
				continue
			}
			found = true
			total += *nic.ReceiveBPS
		}
		return total, found
	}, true},
	{"disk_io.read_bytes_per_second", func(s protocol.Sample) (float64, bool) {
		if s.DiskIO == nil {
			return 0, false
		}
		total := 0.0
		found := false
		for _, d := range s.DiskIO.Devices {
			if d.ReadBPS == nil {
				continue
			}
			found = true
			total += *d.ReadBPS
		}
		return total, found
	}, true},
	{"disk_io.write_bytes_per_second", func(s protocol.Sample) (float64, bool) {
		if s.DiskIO == nil {
			return 0, false
		}
		total := 0.0
		found := false
		for _, d := range s.DiskIO.Devices {
			if d.WriteBPS == nil {
				continue
			}
			found = true
			total += *d.WriteBPS
		}
		return total, found
	}, true},
}

// KnownSeries lists the queryable series allowlist.
func KnownSeries() []string {
	out := make([]string, 0, len(extractors))
	for _, e := range extractors {
		out = append(out, e.name)
	}
	return out
}

func findExtractor(name string) (extractor, bool) {
	for _, e := range extractors {
		if e.name == name {
			return e, true
		}
	}
	return extractor{}, false
}

// Query builds weighted step points. Missing buckets stay gaps, never zeros.
func (m *Memory) Query(hostID string, series []string, from, to time.Time, step time.Duration) ([]Series, error) {
	if len(series) == 0 {
		return nil, fmt.Errorf("%w: series is required", ErrInvalidSeries)
	}
	if len(series) > protocol.MaxHistorySeries {
		return nil, fmt.Errorf("%w: series count %d exceeds %d", ErrLimit, len(series), protocol.MaxHistorySeries)
	}
	if step < time.Second {
		return nil, fmt.Errorf("%w: step must be at least 1s", ErrInvalidRange)
	}
	if !to.After(from) {
		return nil, fmt.Errorf("%w: to must be after from", ErrInvalidRange)
	}
	if points := int(to.Sub(from)/step) + 1; points > protocol.MaxHistoryPointsPerSeries {
		return nil, fmt.Errorf("%w: %d points exceeds %d per series", ErrLimit, points, protocol.MaxHistoryPointsPerSeries)
	}

	m.mu.Lock()
	list := append([]entry(nil), m.perHost[hostID]...)
	m.mu.Unlock()

	out := make([]Series, 0, len(series))
	for _, name := range series {
		ext, ok := findExtractor(name)
		if !ok {
			return nil, fmt.Errorf("%w: unknown series %q", ErrInvalidSeries, name)
		}
		buckets := map[int64]*bucket{}
		for _, e := range list {
			at := e.sample.ObservedAt
			if at.Before(from) || at.After(to) {
				continue
			}
			v, ok := ext.get(e.sample)
			if !ok {
				continue
			}
			key := at.Truncate(step).Unix()
			b, ok := buckets[key]
			if !ok {
				b = &bucket{}
				buckets[key] = b
			}
			b.add(v)
		}
		keys := make([]int64, 0, len(buckets))
		for k := range buckets {
			keys = append(keys, k)
		}
		sort.Slice(keys, func(i, j int) bool { return keys[i] < keys[j] })
		s := Series{Name: name}
		for _, k := range keys {
			b := buckets[k]
			expected := math.Max(1, step.Seconds()/math.Max(1, float64(intervalOf(list, k, step))))
			cov := math.Min(1, float64(b.count)/expected)
			s.Points = append(s.Points, Point{At: time.Unix(k, 0).UTC(), Value: b.value(), Coverage: cov, Min: b.min, Max: b.max, Count: b.count})
		}
		out = append(out, s)
	}
	return out, nil
}

func intervalOf(list []entry, key int64, step time.Duration) float64 {
	for _, e := range list {
		if e.sample.IntervalMS > 0 {
			return float64(e.sample.IntervalMS) / 1000
		}
	}
	return step.Seconds()
}

type bucket struct {
	sum   float64
	count int
	min   float64
	max   float64
}

func (b *bucket) add(v float64) {
	if b.count == 0 {
		b.min, b.max = v, v
	}
	b.sum += v
	b.count++
	b.min = math.Min(b.min, v)
	b.max = math.Max(b.max, v)
}

func (b *bucket) value() float64 { return b.sum / float64(b.count) }
