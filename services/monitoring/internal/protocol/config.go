package protocol

import (
	"fmt"
	"time"
)

// Cadence holds the per-group collection intervals.
type Cadence struct {
	HeartbeatMS int `json:"heartbeat_ms"`
	BasicMS     int `json:"basic_ms"`
	DiskMS      int `json:"disk_ms"`
	GPUMS       int `json:"gpu_ms"`
	RuntimeMS   int `json:"runtime_ms"`
	AggregateMS int `json:"aggregate_ms"`
}

// Retention holds history retention policy in coarse units.
type Retention struct {
	MemoryMinutes  int `json:"memory_minutes"`
	MinuteHours    int `json:"minute_hours"`
	FiveMinuteDays int `json:"five_minute_days"`
}

// Settings is the validated Go-side configuration supplied at bootstrap.
type Settings struct {
	MonitoredHostIDs []string   `json:"monitored_host_ids"`
	IncludeLocal     bool       `json:"include_local"`
	Preset           string     `json:"preset"`
	Cadence          *Cadence   `json:"cadence"`
	Retention        *Retention `json:"retention"`
}

// Hard minimums from the contract. Configuration below these is rejected.
const (
	MinHeartbeatMS = 5000
	MinBasicMS     = 5000
	MinGPUMS       = 10000
	MinDiskMS      = 30000
	MinRuntimeMS   = 5000
	MinAggregateMS = 60000
)

// Preset names.
const (
	PresetDefault  = "default"
	PresetLowPower = "low_power"
)

// DefaultSettings returns the contract default cadence and retention.
func DefaultSettings() Settings {
	return Settings{Preset: PresetDefault}
}

// Intervals is the scheduler-facing form of a validated cadence.
type Intervals struct {
	Heartbeat time.Duration
	Basic     time.Duration
	Disk      time.Duration
	GPU       time.Duration
	Runtime   time.Duration
	Aggregate time.Duration
}

// BasicGroups is the coalesced basic sample group set.
func (Intervals) BasicGroups() []string {
	return []string{GroupCPU, GroupMemory, GroupNetwork, GroupDiskIO}
}

// GPUGroups is the coalesced GPU/thermal/power group set.
func (Intervals) GPUGroups() []string {
	return []string{GroupGPU, GroupThermal, GroupPower}
}

// Normalized validates the settings and fills defaults. Invalid configuration is
// rejected rather than silently clamped.
func (s Settings) Normalized() (Settings, error) {
	out := s
	if out.Preset == "low-power" {
		out.Preset = PresetLowPower
	}
	if out.Preset == "" {
		out.Preset = PresetDefault
	}
	if out.Preset != PresetDefault && out.Preset != PresetLowPower {
		return Settings{}, fmt.Errorf("unknown cadence preset %q", out.Preset)
	}
	if len(out.MonitoredHostIDs) > MaxConfiguredHosts {
		return Settings{}, fmt.Errorf("monitored_host_ids exceeds %d hosts", MaxConfiguredHosts)
	}
	seen := make(map[string]struct{}, len(out.MonitoredHostIDs))
	for _, id := range out.MonitoredHostIDs {
		if err := ValidateHostID(id); err != nil {
			return Settings{}, err
		}
		if _, dup := seen[id]; dup {
			return Settings{}, fmt.Errorf("duplicate monitored host id %q", id)
		}
		seen[id] = struct{}{}
	}

	base := Cadence{HeartbeatMS: 5000, BasicMS: 10000, DiskMS: 60000, GPUMS: 15000, RuntimeMS: 10000, AggregateMS: 60000}
	if out.Preset == PresetLowPower {
		base = Cadence{HeartbeatMS: 15000, BasicMS: 30000, DiskMS: 120000, GPUMS: 30000, RuntimeMS: 30000, AggregateMS: 60000}
	}
	if out.Cadence != nil {
		c := *out.Cadence
		if c.HeartbeatMS != 0 {
			base.HeartbeatMS = c.HeartbeatMS
		}
		if c.BasicMS != 0 {
			base.BasicMS = c.BasicMS
		}
		if c.DiskMS != 0 {
			base.DiskMS = c.DiskMS
		}
		if c.GPUMS != 0 {
			base.GPUMS = c.GPUMS
		}
		if c.RuntimeMS != 0 {
			base.RuntimeMS = c.RuntimeMS
		}
		if c.AggregateMS != 0 {
			base.AggregateMS = c.AggregateMS
		}
	}
	switch {
	case base.HeartbeatMS < MinHeartbeatMS:
		return Settings{}, fmt.Errorf("heartbeat_ms %d below minimum %d", base.HeartbeatMS, MinHeartbeatMS)
	case base.BasicMS < MinBasicMS:
		return Settings{}, fmt.Errorf("basic_ms %d below minimum %d", base.BasicMS, MinBasicMS)
	case base.GPUMS < MinGPUMS:
		return Settings{}, fmt.Errorf("gpu_ms %d below minimum %d", base.GPUMS, MinGPUMS)
	case base.DiskMS < MinDiskMS:
		return Settings{}, fmt.Errorf("disk_ms %d below minimum %d", base.DiskMS, MinDiskMS)
	case base.RuntimeMS < MinRuntimeMS:
		return Settings{}, fmt.Errorf("runtime_ms %d below minimum %d", base.RuntimeMS, MinRuntimeMS)
	case base.AggregateMS < MinAggregateMS:
		return Settings{}, fmt.Errorf("aggregate_ms %d below minimum %d", base.AggregateMS, MinAggregateMS)
	}
	out.Cadence = &base

	ret := Retention{MemoryMinutes: 60, MinuteHours: 24, FiveMinuteDays: 7}
	if out.Retention != nil {
		if out.Retention.MemoryMinutes != 0 {
			ret.MemoryMinutes = out.Retention.MemoryMinutes
		}
		if out.Retention.MinuteHours != 0 {
			ret.MinuteHours = out.Retention.MinuteHours
		}
		if out.Retention.FiveMinuteDays != 0 {
			ret.FiveMinuteDays = out.Retention.FiveMinuteDays
		}
	}
	if ret.MemoryMinutes < 1 || ret.MemoryMinutes > 60 {
		return Settings{}, fmt.Errorf("memory_minutes %d outside 1..60", ret.MemoryMinutes)
	}
	if ret.MinuteHours < 1 || ret.MinuteHours > 24 {
		return Settings{}, fmt.Errorf("minute_hours %d outside 1..24", ret.MinuteHours)
	}
	if ret.FiveMinuteDays < 1 || ret.FiveMinuteDays > 7 {
		return Settings{}, fmt.Errorf("five_minute_days %d outside 1..7", ret.FiveMinuteDays)
	}
	out.Retention = &ret
	return out, nil
}

// Intervals converts a normalized cadence to durations.
func (s Settings) Intervals() Intervals {
	c := Cadence{HeartbeatMS: 5000, BasicMS: 10000, DiskMS: 60000, GPUMS: 15000, RuntimeMS: 10000, AggregateMS: 60000}
	if s.Cadence != nil {
		c = *s.Cadence
	}
	return Intervals{
		Heartbeat: time.Duration(c.HeartbeatMS) * time.Millisecond,
		Basic:     time.Duration(c.BasicMS) * time.Millisecond,
		Disk:      time.Duration(c.DiskMS) * time.Millisecond,
		GPU:       time.Duration(c.GPUMS) * time.Millisecond,
		Runtime:   time.Duration(c.RuntimeMS) * time.Millisecond,
		Aggregate: time.Duration(c.AggregateMS) * time.Millisecond,
	}
}

// Monitors reports whether a host id is configured for monitoring.
func (s Settings) Monitors(hostID string, local bool) bool {
	if local && s.IncludeLocal {
		return true
	}
	for _, id := range s.MonitoredHostIDs {
		if id == hostID {
			return true
		}
	}
	return false
}
