package protocol

import (
	"errors"
	"strings"
	"testing"
	"time"
)

func TestDecodeBoundedRejectsUnknownFieldsAndOversize(t *testing.T) {
	type small struct {
		A int `json:"a"`
	}
	var dst small
	if err := DecodeBounded([]byte(`{"a":1,"b":2}`), 1024, &dst); !errors.Is(err, ErrInvalid) {
		t.Fatalf("unknown field must be rejected, got %v", err)
	}
	if err := DecodeBounded([]byte(`{"a":1} {"a":2}`), 1024, &dst); !errors.Is(err, ErrInvalid) {
		t.Fatalf("trailing data must be rejected, got %v", err)
	}
	if err := DecodeBounded([]byte(`{"a":1}`), 4, &dst); !errors.Is(err, ErrResourceLimit) {
		t.Fatalf("oversize payload must be rejected, got %v", err)
	}
}

func TestSettingsRejectInvalidCadenceInsteadOfClamping(t *testing.T) {
	s := DefaultSettings()
	s.Cadence = &Cadence{HeartbeatMS: 1000}
	if _, err := s.Normalized(); err == nil {
		t.Fatal("heartbeat below the hard minimum must be rejected")
	}

	s = DefaultSettings()
	s.Preset = PresetLowPower
	norm, err := s.Normalized()
	if err != nil {
		t.Fatalf("low power preset: %v", err)
	}
	if norm.Cadence.HeartbeatMS != 15000 || norm.Cadence.BasicMS != 30000 || norm.Cadence.DiskMS != 120000 || norm.Cadence.GPUMS != 30000 {
		t.Fatalf("unexpected low power cadence %+v", norm.Cadence)
	}

	s = DefaultSettings()
	s.MonitoredHostIDs = []string{"not-a-host"}
	if _, err := s.Normalized(); err == nil {
		t.Fatal("invalid host id must be rejected")
	}
	many := make([]string, MaxConfiguredHosts+1)
	for i := range many {
		many[i] = "host_" + strings.Repeat("a", 64)
	}
	s = DefaultSettings()
	s.MonitoredHostIDs = many
	if _, err := s.Normalized(); err == nil {
		t.Fatal("more than 32 hosts must be rejected")
	}
}

func TestObservationValidationBounds(t *testing.T) {
	now := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	base := Observation{
		HostID:     "host_" + strings.Repeat("a", 64),
		Generation: "g1",
		Sample: Sample{
			ID:         "s1",
			BootID:     "b1",
			Sequence:   1,
			ObservedAt: now,
			IntervalMS: 10000,
			Scope:      "host",
		},
	}
	if err := ValidateObservation(base); err != nil {
		t.Fatalf("baseline should validate: %v", err)
	}

	negative := base
	neg := -1.0
	negative.Memory = &MemoryGroup{Group: Group{Status: CollectorOK}, UsedBytes: &neg}
	if err := ValidateObservation(negative); err == nil {
		t.Fatal("negative byte counters must be rejected")
	}

	fractional := base
	frac := 1.5
	fractional.Memory = &MemoryGroup{Group: Group{Status: CollectorOK}, UsedBytes: &frac}
	if err := ValidateObservation(fractional); err == nil {
		t.Fatal("fractional byte counters must be rejected")
	}

	overPercent := base
	hundred := 101.0
	overPercent.CPU = &CPUGroup{Group: Group{Status: CollectorOK}, UtilizationPercent: &hundred}
	if err := ValidateObservation(overPercent); err == nil {
		t.Fatal("utilization above 100 must be rejected")
	}

	localTime := base
	localTime.ObservedAt = now.In(time.FixedZone("CST", 8*3600))
	if err := ValidateObservation(localTime); err == nil {
		t.Fatal("non-UTC timestamps must be rejected")
	}

	tooMany := base
	tooMany.GPU = &GPUGroup{Group: Group{Status: CollectorOK}}
	for i := 0; i < MaxGPUsPerHost+1; i++ {
		tooMany.GPU.GPUs = append(tooMany.GPU.GPUs, GPUEntry{ID: "gpu"})
	}
	if err := ValidateObservation(tooMany); !errors.Is(err, ErrResourceLimit) {
		t.Fatalf("excess GPUs must be rejected, got %v", err)
	}
}

func TestHostIDShape(t *testing.T) {
	if err := ValidateHostID("host_" + strings.Repeat("A", 64)); err == nil {
		t.Fatal("uppercase digest must be rejected")
	}
	if err := ValidateHostID("192.168.1.4"); err == nil {
		t.Fatal("addresses are never identities")
	}
	if err := ValidateLinkBinary("odessy-link"); err == nil {
		t.Fatal("bare command names must be rejected")
	}
	if err := ValidateLinkBinary(`C:\Program Files\odessy\odessy-link.exe`); err != nil {
		t.Fatalf("plain absolute path should be accepted: %v", err)
	}
	if err := ValidateLinkBinary(`C:\Users\RUNNER~1\odessy-link.exe`); err != nil {
		t.Fatalf("Windows short path should be accepted: %v", err)
	}
}
