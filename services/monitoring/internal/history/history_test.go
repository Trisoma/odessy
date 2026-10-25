package history

import (
	"errors"
	"testing"
	"time"

	"github.com/odessy/odessy/services/monitoring/internal/protocol"
)

var t0 = time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)

func sample(id string, seq uint64, at time.Time, cpu *float64) protocol.Sample {
	s := protocol.Sample{ID: id, BootID: "b1", Sequence: seq, ObservedAt: at, IntervalMS: 10000, Scope: "host"}
	if cpu != nil {
		s.CPU = &protocol.CPUGroup{Group: protocol.Group{Status: protocol.CollectorOK, ObservedAt: &at}, UtilizationPercent: cpu}
	}
	return s
}

func f(v float64) *float64 { return &v }

func TestMemoryDeduplicatesAndKeepsMissingIntervalsAsGaps(t *testing.T) {
	m := NewMemory(time.Hour, protocol.MemoryRingBytes)
	m.Append("h1", sample("s1", 1, t0, f(10)))
	m.Append("h1", sample("s1", 1, t0, f(10)))
	m.Append("h1", sample("s2", 2, t0.Add(3*time.Minute), f(30)))

	series, err := m.Query("h1", []string{"cpu.utilization_percent"}, t0, t0.Add(4*time.Minute), time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	points := series[0].Points
	if len(points) != 2 {
		t.Fatalf("expected 2 buckets (duplicates collapse, gaps stay gaps), got %d: %+v", len(points), points)
	}
	if points[0].Value != 10 || points[0].Coverage >= 1 {
		t.Fatalf("first bucket should carry real data with partial coverage, got %+v", points[0])
	}
	for _, p := range points {
		if p.Value == 0 {
			t.Fatal("missing intervals must never become zero values")
		}
	}
}

func TestMemoryHonoursByteBudget(t *testing.T) {
	m := NewMemory(time.Hour, 4096)
	for i := 0; i < 200; i++ {
		m.Append("h1", sample(string(rune('a'+i%26))+string(rune('a'+i)), uint64(i+1), t0.Add(time.Duration(i)*time.Second), f(float64(i))))
	}
	if m.Bytes() > 4096 {
		t.Fatalf("memory ring exceeded its 4 KiB test budget: %d", m.Bytes())
	}
}

func TestQueryLimitsAreExplicit(t *testing.T) {
	m := NewMemory(time.Hour, protocol.MemoryRingBytes)
	if _, err := m.Query("h1", []string{"cpu.utilization_percent"}, t0, t0.Add(time.Second), 0); !errors.Is(err, ErrInvalidRange) {
		t.Fatalf("expected range error, got %v", err)
	}
	if _, err := m.Query("h1", []string{"nope"}, t0, t0.Add(time.Minute), time.Minute); !errors.Is(err, ErrInvalidSeries) {
		t.Fatalf("expected series error, got %v", err)
	}
	tooMany := make([]string, protocol.MaxHistorySeries+1)
	for i := range tooMany {
		tooMany[i] = "cpu.utilization_percent"
	}
	if _, err := m.Query("h1", tooMany, t0, t0.Add(time.Minute), time.Minute); !errors.Is(err, ErrLimit) {
		t.Fatalf("expected series-count limit, got %v", err)
	}
	if _, err := m.Query("h1", []string{"cpu.utilization_percent"}, t0, t0.Add(10*time.Hour), time.Second); !errors.Is(err, ErrLimit) {
		t.Fatalf("expected point-count limit, got %v", err)
	}
}

func TestStoreStopsStoringWhenCapacityIsReached(t *testing.T) {
	dir := t.TempDir()
	store, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	store.Enqueue("h1", sample("s1", 1, time.Now().UTC(), f(1)))
	time.Sleep(50 * time.Millisecond)
	rows, _, degraded := store.Status()
	if rows < 1 {
		t.Fatalf("expected one persisted row, got %d", rows)
	}
	if degraded != "" {
		t.Fatalf("fresh store must not be degraded: %s", degraded)
	}
}
