// Package protocol holds the versioned monitoring-v1 wire types, limits and
// strict validation shared by every other package in this module.
package protocol

import (
	"bytes"
	"encoding/json"
	"fmt"
	"sort"
	"strconv"
	"time"
)

// SchemaVersion is the only accepted wire revision.
const SchemaVersion = 1

// Bounded limits from the monitoring-v1 contract.
const (
	MaxWorkerFrameBytes    = 256 << 10
	MaxRuntimePayloadBytes = 64 << 10
	MaxBootstrapFrameBytes = 64 << 10
	MaxConfiguredHosts     = 32
	MaxInterfacesPerHost   = 64
	MaxDisksPerHost        = 32
	MaxGPUsPerHost         = 16

	MaxHistoryPointsPerSeries = 1000
	MaxHistorySeries          = 16
	MaxHistoryResponseBytes   = 2 << 20

	MaxEventRingEvents  = 4096
	MaxEventRingBytes   = 8 << 20
	MaxSubscribers      = 8
	MaxSubscriberEvents = 128
	MaxSubscriberBytes  = 1 << 20
	MaxSnapshotPins     = 8

	MemoryHorizon   = time.Hour
	MemoryRingBytes = 16 << 20

	StoreRowCap      = 250_000
	StoreByteBudget  = 128 << 20
	StoreQueueDepth  = 512
	MinuteRetention  = 24 * time.Hour
	FiveMinRetention = 7 * 24 * time.Hour
)

// Timing limits from the contract.
const (
	TrustLease        = 3 * time.Second
	InventoryInterval = time.Second
	RequestDeadline   = 3 * time.Second
	GlobalSlots       = 4
	SnapshotTTL       = 30 * time.Second
	KeepAliveInterval = 15 * time.Second
)

// TrustState is the trust projection for a host.
type TrustState string

const (
	TrustLocal   TrustState = "local"
	TrustPaired  TrustState = "paired"
	TrustRevoked TrustState = "revoked"
	TrustUnknown TrustState = "unknown"
)

// Connectivity is the liveness projection for a host.
type Connectivity string

const (
	ConnectivityUnknown Connectivity = "unknown"
	ConnectivityOnline  Connectivity = "online"
	ConnectivityStale   Connectivity = "stale"
	ConnectivityOffline Connectivity = "offline"
)

// Access reports whether monitoring data may be collected.
type Access string

const (
	AccessAllowed     Access = "allowed"
	AccessDenied      Access = "denied"
	AccessUnsupported Access = "unsupported"
	AccessDisabled    Access = "disabled"
)

// CollectorStatus reports per-group collection quality.
type CollectorStatus string

const (
	CollectorOK               CollectorStatus = "ok"
	CollectorWarmingUp        CollectorStatus = "warming_up"
	CollectorUnsupported      CollectorStatus = "unsupported"
	CollectorPermissionDenied CollectorStatus = "permission_denied"
	CollectorError            CollectorStatus = "error"
	CollectorStale            CollectorStatus = "stale"
)

// ServiceHealth reports service readiness, never inferred from mere existence.
type ServiceHealth string

const (
	ServiceHealthy     ServiceHealth = "healthy"
	ServiceUnhealthy   ServiceHealth = "unhealthy"
	ServiceUnknown     ServiceHealth = "unknown"
	ServiceUnsupported ServiceHealth = "unsupported"
	ServiceDisabled    ServiceHealth = "disabled"
)

// Sample groups that the scheduler may request.
const (
	GroupCPU      = "cpu"
	GroupMemory   = "memory"
	GroupDiskIO   = "disk_io"
	GroupNetwork  = "network"
	GroupDisks    = "disks"
	GroupGPU      = "gpu"
	GroupThermal  = "thermal"
	GroupPower    = "power"
	GroupServices = "services"
)

// SampleGroups is the ordered allowlist of requestable groups.
var SampleGroups = []string{GroupCPU, GroupMemory, GroupDiskIO, GroupNetwork, GroupDisks, GroupGPU, GroupThermal, GroupPower, GroupServices}

// Platform identifies the peer operating system.
type Platform struct {
	OS   string `json:"os"`
	Arch string `json:"arch"`
}

// HostIdentity is the Rust-reported public identity of a host.
type HostIdentity struct {
	HostID               string     `json:"host_id"`
	InstallationID       string     `json:"installation_id"`
	FingerprintSHA256    string     `json:"fingerprint_sha256"`
	Alias                string     `json:"alias,omitempty"`
	Platform             *Platform  `json:"platform"`
	TrustState           TrustState `json:"trust_state"`
	MonitoringAccess     Access     `json:"monitoring_access"`
	Generation           string     `json:"generation"`
	CertificateExpiresAt *time.Time `json:"certificate_expires_at"`
}

// Inventory is the authoritative result of an inventory refresh.
type Inventory struct {
	Revision string         `json:"revision"`
	Hosts    []HostIdentity `json:"hosts"`
}

// Heartbeat proves authenticated contact; no metric collection is required.
type Heartbeat struct {
	HostID     string    `json:"host_id"`
	Generation string    `json:"generation"`
	BootID     string    `json:"boot_id"`
	Sequence   uint64    `json:"sequence"`
	ObservedAt time.Time `json:"observed_at"`
	ReceivedAt time.Time `json:"received_at"`
	LatencyMS  *float64  `json:"latency_ms"`
}

// HostRef is an opaque host reference plus the generation it was issued for.
type HostRef struct {
	HostID     string `json:"host_id"`
	Generation string `json:"generation"`
}

// Group carries the collector metadata shared by every metric group.
type Group struct {
	Status               CollectorStatus `json:"status"`
	Reason               string          `json:"reason,omitempty"`
	ObservedAt           *time.Time      `json:"observed_at"`
	CollectionDurationMS *float64        `json:"collection_duration_ms"`
}

// CPUGroup is host CPU utilisation and load.
type CPUGroup struct {
	Group
	UtilizationPercent *float64 `json:"utilization_percent"`
	Load1              *float64 `json:"load_1"`
	Load5              *float64 `json:"load_5"`
	Load15             *float64 `json:"load_15"`
	CoreCount          *float64 `json:"core_count"`
}

// MemoryGroup is host memory usage; shared memory is never reported as dedicated.
type MemoryGroup struct {
	SwapTotalBytes *float64 `json:"swap_total_bytes"`
	SwapUsedBytes  *float64 `json:"swap_used_bytes"`
	Group
	TotalBytes         *float64 `json:"total_bytes"`
	UsedBytes          *float64 `json:"used_bytes"`
	AvailableBytes     *float64 `json:"available_bytes"`
	SharedBytes        *float64 `json:"shared_bytes"`
	UtilizationPercent *float64 `json:"utilization_percent"`
}

// InterfaceGroup is a network interface entry.
type InterfaceGroup struct {
	ID             string   `json:"id"`
	ReceiveBPS     *float64 `json:"receive_bytes_per_second"`
	TransmitBPS    *float64 `json:"transmit_bytes_per_second"`
	ReceiveErrors  *float64 `json:"receive_errors"`
	TransmitErrors *float64 `json:"transmit_errors"`
}

// NetworkGroup is the network group payload.
type NetworkGroup struct {
	Group
	Interfaces []InterfaceGroup `json:"interfaces"`
	Truncated  bool             `json:"truncated,omitempty"`
}

// UnmarshalJSON accepts the published `devices` alias for interfaces.
func (n *NetworkGroup) UnmarshalJSON(data []byte) error {
	type alias NetworkGroup
	aux := struct {
		*alias
		Devices []InterfaceGroup `json:"devices"`
	}{alias: (*alias)(n)}
	if err := json.Unmarshal(data, &aux); err != nil {
		return err
	}
	if len(n.Interfaces) == 0 && len(aux.Devices) > 0 {
		n.Interfaces = aux.Devices
	}
	return nil
}

// DiskGroup is the disk capacity group payload.
type DiskGroup struct {
	Group
	Disks     []DiskEntry `json:"disks"`
	Truncated bool        `json:"truncated,omitempty"`
}

// DiskEntry is one mounted device.
type DiskEntry struct {
	AvailableBytes     *float64 `json:"available_bytes"`
	ID                 string   `json:"id"`
	TotalBytes         *float64 `json:"total_bytes"`
	UsedBytes          *float64 `json:"used_bytes"`
	FreeBytes          *float64 `json:"free_bytes"`
	UtilizationPercent *float64 `json:"utilization_percent"`
}

// DiskIOGroup is per-device throughput.
type DiskIOGroup struct {
	Group
	Devices   []DiskIOEntry `json:"devices"`
	Truncated bool          `json:"truncated,omitempty"`
}

// DiskIOEntry is one device's throughput counters.
type DiskIOEntry struct {
	ID       string   `json:"id"`
	ReadBPS  *float64 `json:"read_bytes_per_second"`
	WriteBPS *float64 `json:"write_bytes_per_second"`
}

// GPUGroup is per-GPU telemetry.
type GPUGroup struct {
	Group
	GPUs      []GPUEntry `json:"gpus"`
	Truncated bool       `json:"truncated,omitempty"`
}

// UnmarshalJSON accepts the published `devices` alias for GPUs.
func (g *GPUGroup) UnmarshalJSON(data []byte) error {
	type alias GPUGroup
	aux := struct {
		*alias
		Devices []GPUEntry `json:"devices"`
	}{alias: (*alias)(g)}
	if err := json.Unmarshal(data, &aux); err != nil {
		return err
	}
	if len(g.GPUs) == 0 && len(aux.Devices) > 0 {
		g.GPUs = aux.Devices
	}
	return nil
}

// GPUEntry is one GPU reading. Shared RAM is never labelled dedicated VRAM.
type GPUEntry struct {
	MemoryKind         string   `json:"memory_kind,omitempty"`
	PowerWatts         *float64 `json:"power_watts"`
	ID                 string   `json:"id"`
	Name               string   `json:"name,omitempty"`
	UtilizationPercent *float64 `json:"utilization_percent"`
	MemoryUsedBytes    *float64 `json:"memory_used_bytes"`
	MemoryTotalBytes   *float64 `json:"memory_total_bytes"`
	TemperatureCelsius *float64 `json:"temperature_celsius"`
}

// ThermalGroup is temperature telemetry.
type ThermalGroup struct {
	Group
	Sensors []SensorEntry `json:"sensors"`
}

// SensorEntry is one temperature sensor.
type SensorEntry struct {
	ID                 string   `json:"id"`
	TemperatureCelsius *float64 `json:"temperature_celsius"`
}

// PowerGroup is power telemetry; each entry names its measured domain.
type PowerGroup struct {
	Group
	Rails []PowerEntry `json:"rails"`
}

// PowerEntry is one measured power domain.
type PowerEntry struct {
	ID     string   `json:"id"`
	Domain string   `json:"domain"`
	Watts  *float64 `json:"watts"`
}

// ServiceEntry is one local service readiness row.
type ServiceEntry struct {
	Name       string        `json:"name"`
	Status     ServiceHealth `json:"status"`
	ObservedAt *time.Time    `json:"observed_at"`
	Source     string        `json:"source,omitempty"`
	Reason     string        `json:"reason,omitempty"`
}

// Cost is an estimated cost with its currency and price status.
type Cost struct {
	Estimated   *float64 `json:"estimated"`
	Currency    string   `json:"currency,omitempty"`
	PriceStatus string   `json:"price_status,omitempty"`
}

// ModelActivity is the latest attributable model activity.
type ModelActivity struct {
	Model                     string   `json:"model"`
	Provider                  string   `json:"provider,omitempty"`
	Participant               string   `json:"participant,omitempty"`
	RequestDurationMS         *float64 `json:"request_duration_ms"`
	TimeToFirstTokenMS        *float64 `json:"ttft_ms"`
	GenerationTokensPerSecond *float64 `json:"generation_tokens_per_second"`
	PromptTokens              *float64 `json:"prompt_tokens"`
	CompletionTokens          *float64 `json:"completion_tokens"`
	Errors                    *float64 `json:"errors"`
	Timeouts                  *float64 `json:"timeouts"`
	ToolFailures              *float64 `json:"tool_failures"`
	ActiveJobs                *float64 `json:"active_jobs"`
	LoadedObservedModel       string   `json:"loaded_observed_model,omitempty"`
	Cost                      Cost     `json:"cost"`
}

// AI is the runtime AI summary supplied by Python. The published contract also
// allows the latest-activity counters at the top level; both shapes are read
// into Latest.
type AI struct {
	Latest    *ModelActivity   `json:"latest"`
	Providers []ProviderBucket `json:"providers"`
}

// UnmarshalJSON accepts both `{"latest":{...}}` and flat latest-activity fields.
func (a *AI) UnmarshalJSON(data []byte) error {
	type alias AI
	aux := struct {
		*alias
		Model                     string   `json:"model"`
		Provider                  string   `json:"provider"`
		Participant               string   `json:"participant"`
		RequestDurationMS         *float64 `json:"request_duration_ms"`
		TimeToFirstTokenMS        *float64 `json:"ttft_ms"`
		GenerationTokensPerSecond *float64 `json:"generation_tokens_per_second"`
		PromptTokens              *float64 `json:"prompt_tokens"`
		CompletionTokens          *float64 `json:"completion_tokens"`
		Errors                    *float64 `json:"errors"`
		Timeouts                  *float64 `json:"timeouts"`
		ToolFailures              *float64 `json:"tool_failures"`
		ActiveJobs                *float64 `json:"active_jobs"`
		LoadedObservedModel       string   `json:"loaded_observed_model"`
		Cost                      *Cost    `json:"cost"`
	}{alias: (*alias)(a)}
	if err := json.Unmarshal(data, &aux); err != nil {
		return err
	}
	var present map[string]json.RawMessage
	if err := json.Unmarshal(data, &present); err != nil {
		return err
	}
	flat := false
	for _, key := range []string{
		"model", "provider", "participant", "request_duration_ms", "ttft_ms",
		"generation_tokens_per_second", "prompt_tokens", "completion_tokens", "errors",
		"timeouts", "tool_failures", "active_jobs", "loaded_observed_model", "cost",
	} {
		if _, ok := present[key]; ok {
			flat = true
			break
		}
	}
	if !flat {
		return nil
	}
	if a.Latest == nil {
		a.Latest = &ModelActivity{}
	}
	if aux.Model != "" {
		a.Latest.Model = aux.Model
	}
	if aux.Provider != "" {
		a.Latest.Provider = aux.Provider
	}
	if aux.Participant != "" {
		a.Latest.Participant = aux.Participant
	}
	if aux.RequestDurationMS != nil {
		a.Latest.RequestDurationMS = aux.RequestDurationMS
	}
	if aux.TimeToFirstTokenMS != nil {
		a.Latest.TimeToFirstTokenMS = aux.TimeToFirstTokenMS
	}
	if aux.GenerationTokensPerSecond != nil {
		a.Latest.GenerationTokensPerSecond = aux.GenerationTokensPerSecond
	}
	if aux.PromptTokens != nil {
		a.Latest.PromptTokens = aux.PromptTokens
	}
	if aux.CompletionTokens != nil {
		a.Latest.CompletionTokens = aux.CompletionTokens
	}
	if aux.Errors != nil {
		a.Latest.Errors = aux.Errors
	}
	if aux.Timeouts != nil {
		a.Latest.Timeouts = aux.Timeouts
	}
	if aux.ToolFailures != nil {
		a.Latest.ToolFailures = aux.ToolFailures
	}
	if aux.ActiveJobs != nil {
		a.Latest.ActiveJobs = aux.ActiveJobs
	}
	if aux.LoadedObservedModel != "" {
		a.Latest.LoadedObservedModel = aux.LoadedObservedModel
	}
	if aux.Cost != nil {
		a.Latest.Cost = *aux.Cost
	}
	return nil
}

// ServiceList decodes both the array shape and the published keyed-object shape.
type ServiceList []ServiceEntry

// UnmarshalJSON accepts `[{name,...}]` and `{"coordinator":{"status":...}}`.
func (s *ServiceList) UnmarshalJSON(data []byte) error {
	trimmed := bytes.TrimSpace(data)
	if len(trimmed) == 0 || string(trimmed) == "null" {
		*s = nil
		return nil
	}
	if trimmed[0] == '[' {
		var list []ServiceEntry
		if err := json.Unmarshal(trimmed, &list); err != nil {
			return err
		}
		*s = list
		return nil
	}
	var keyed map[string]struct {
		Status     ServiceHealth `json:"status"`
		ObservedAt *time.Time    `json:"observed_at"`
		Source     string        `json:"source"`
		Reason     string        `json:"reason"`
	}
	if err := json.Unmarshal(trimmed, &keyed); err != nil {
		return err
	}
	names := make([]string, 0, len(keyed))
	for name := range keyed {
		names = append(names, name)
	}
	sort.Strings(names)
	list := make([]ServiceEntry, 0, len(names))
	for _, name := range names {
		entry := keyed[name]
		list = append(list, ServiceEntry{Name: name, Status: entry.Status, ObservedAt: entry.ObservedAt, Source: entry.Source, Reason: entry.Reason})
	}
	*s = list
	return nil
}

// ProviderBucket is a bounded per-provider/model summary.
type ProviderBucket struct {
	Provider string   `json:"provider"`
	Model    string   `json:"model"`
	Requests *float64 `json:"requests"`
	Errors   *float64 `json:"errors"`
	Cost     Cost     `json:"cost"`
}

// Sample is one collected observation of a host.
type Sample struct {
	SchemaVersion int           `json:"schema_version,omitempty"`
	ID            string        `json:"id"`
	BootID        string        `json:"boot_id"`
	Sequence      uint64        `json:"sequence"`
	ObservedAt    time.Time     `json:"observed_at"`
	ReceivedAt    time.Time     `json:"received_at"`
	IntervalMS    int           `json:"interval_ms"`
	Scope         string        `json:"scope"`
	CPU           *CPUGroup     `json:"cpu"`
	Memory        *MemoryGroup  `json:"memory"`
	DiskIO        *DiskIOGroup  `json:"disk_io"`
	Network       *NetworkGroup `json:"network"`
	Disks         *DiskGroup    `json:"disks"`
	GPU           *GPUGroup     `json:"gpu"`
	Thermal       *ThermalGroup `json:"thermal"`
	Power         *PowerGroup   `json:"power"`
}

// Observation is a sample attributed to a host and generation.
type Observation struct {
	HostID     string `json:"host_id"`
	Generation string `json:"generation"`
	Sample
}

// RuntimeEnvelope is the sanitized runtime observation posted by Python.
type RuntimeEnvelope struct {
	SchemaVersion int         `json:"schema_version"`
	HostID        string      `json:"host_id"`
	Generation    string      `json:"generation"`
	SourceID      string      `json:"source_id"`
	Sequence      uint64      `json:"sequence"`
	ObservedAt    time.Time   `json:"observed_at"`
	Services      ServiceList `json:"services"`
	AI            *AI         `json:"ai"`
}

// WorkerRequest is a JSONL request frame to the Rust telemetry worker.
type WorkerRequest struct {
	SchemaVersion int      `json:"schema_version"`
	ID            string   `json:"id"`
	Op            string   `json:"op"`
	HostID        string   `json:"host_id,omitempty"`
	Generation    string   `json:"generation,omitempty"`
	Groups        []string `json:"groups,omitempty"`
	TargetID      string   `json:"target_id,omitempty"`
}

// WorkerError is a sanitized worker failure.
type WorkerError struct {
	Code      string `json:"code"`
	Message   string `json:"message"`
	Retryable bool   `json:"retryable"`
}

// WorkerResponse is a JSONL response frame from the Rust telemetry worker.
type WorkerResponse struct {
	SchemaVersion int             `json:"schema_version"`
	ID            string          `json:"id"`
	OK            bool            `json:"ok"`
	Result        json.RawMessage `json:"result,omitempty"`
	Error         *WorkerError    `json:"error,omitempty"`
}

// Worker operations.
const (
	OpInventory = "inventory"
	OpHeartbeat = "heartbeat"
	OpSample    = "sample"
	OpCancel    = "cancel"
)

// BootstrapFrame is the first private stdin frame from Python.
type BootstrapFrame struct {
	SchemaVersion int      `json:"schema_version"`
	Op            string   `json:"op"`
	Token         string   `json:"token"`
	StateDir      string   `json:"state_dir"`
	LinkBinary    string   `json:"link_binary"`
	Settings      Settings `json:"settings"`
}

// ReadyFrame is the single stdout readiness frame.
type ReadyFrame struct {
	SchemaVersion int    `json:"schema_version"`
	Op            string `json:"op"`
	BaseURL       string `json:"base_url"`
	Epoch         string `json:"epoch"`
}

// Rust registry revisions are integers; the internal revision remains opaque.
func (i *Inventory) UnmarshalJSON(data []byte) error {
	var value struct {
		Revision json.RawMessage `json:"revision"`
		Hosts    []HostIdentity  `json:"hosts"`
	}
	if err := json.Unmarshal(data, &value); err != nil {
		return err
	}
	var revision string
	if err := json.Unmarshal(value.Revision, &revision); err != nil {
		var number int64
		if err := json.Unmarshal(value.Revision, &number); err != nil || number < 0 {
			return fmt.Errorf("invalid registry revision")
		}
		revision = strconv.FormatInt(number, 10)
	}
	i.Revision = revision
	i.Hosts = value.Hosts
	return nil
}

func (d *DiskGroup) UnmarshalJSON(data []byte) error {
	type alias DiskGroup
	var value struct {
		*alias
		Devices []DiskEntry `json:"devices"`
	}
	value.alias = (*alias)(d)
	if err := json.Unmarshal(data, &value); err != nil {
		return err
	}
	if len(d.Disks) == 0 {
		d.Disks = value.Devices
	}
	for i := range d.Disks {
		if d.Disks[i].FreeBytes == nil {
			d.Disks[i].FreeBytes = d.Disks[i].AvailableBytes
		}
	}
	return nil
}
func (t *ThermalGroup) UnmarshalJSON(data []byte) error {
	type alias ThermalGroup
	var value struct {
		*alias
		Devices []SensorEntry `json:"devices"`
	}
	value.alias = (*alias)(t)
	if err := json.Unmarshal(data, &value); err != nil {
		return err
	}
	if len(t.Sensors) == 0 {
		t.Sensors = value.Devices
	}
	return nil
}
func (p *PowerGroup) UnmarshalJSON(data []byte) error {
	type alias PowerGroup
	var value struct {
		*alias
		Devices []PowerEntry `json:"devices"`
	}
	value.alias = (*alias)(p)
	if err := json.Unmarshal(data, &value); err != nil {
		return err
	}
	if len(p.Rails) == 0 {
		p.Rails = value.Devices
	}
	return nil
}
func (n *InterfaceGroup) UnmarshalJSON(data []byte) error {
	type alias InterfaceGroup
	var value struct {
		*alias
		RX *float64 `json:"rx_bytes_per_second"`
		TX *float64 `json:"tx_bytes_per_second"`
	}
	value.alias = (*alias)(n)
	if err := json.Unmarshal(data, &value); err != nil {
		return err
	}
	if n.ReceiveBPS == nil {
		n.ReceiveBPS = value.RX
	}
	if n.TransmitBPS == nil {
		n.TransmitBPS = value.TX
	}
	return nil
}
