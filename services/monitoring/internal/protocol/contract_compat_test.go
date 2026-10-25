package protocol

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

// sharedFixture loads a Codex-published contract fixture read-only. The test
// skips when the shared contract is not present in this checkout.
func sharedFixture(t *testing.T, name string) []byte {
	t.Helper()
	path := filepath.Join("..", "..", "..", "..", "contracts", "monitoring", "fixtures", name)
	data, err := os.ReadFile(path)
	if err != nil {
		t.Skipf("shared contract fixture %s unavailable: %v", name, err)
	}
	return data
}

func TestSharedSampleFixtureIsCompatible(t *testing.T) {
	data := sharedFixture(t, "sample.json")
	var sample Sample
	if err := DecodeBounded(data, MaxWorkerFrameBytes, &sample); err != nil {
		t.Fatalf("shared sample fixture rejected: %v", err)
	}
	obs := Observation{HostID: "host_" + "a", Generation: "g1", Sample: sample}
	// The shared fixture uses a placeholder host id, so validate through a
	// well-formed host reference while keeping every sample field intact.
	obs.HostID = "host_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	if err := ValidateObservation(obs); err != nil {
		t.Fatalf("shared sample fixture invalid: %v", err)
	}
	if sample.SchemaVersion != SchemaVersion {
		t.Fatalf("fixture schema version %d", sample.SchemaVersion)
	}
	if sample.CPU == nil || sample.CPU.Status != CollectorWarmingUp {
		t.Fatal("warming_up collector status must survive decoding")
	}
	if sample.GPU == nil || len(sample.GPU.GPUs) != 0 {
		t.Fatal("published `devices` alias for GPU entries must decode")
	}
	if sample.Network == nil || len(sample.Network.Interfaces) != 0 {
		t.Fatal("published `devices` alias for network interfaces must decode")
	}
	if sample.Memory == nil || sample.Memory.AvailableBytes == nil {
		t.Fatal("null-unsupported values must decode as present-or-absent, never invented")
	}
}

func TestSharedRuntimeFixtureIsCompatible(t *testing.T) {
	data := sharedFixture(t, "runtime.json")
	var env RuntimeEnvelope
	if err := DecodeBounded(data, MaxRuntimePayloadBytes, &env); err != nil {
		t.Fatalf("shared runtime fixture rejected: %v", err)
	}
	if len(env.Services) != 1 || env.Services[0].Name != "coordinator" || env.Services[0].Status != ServiceHealthy {
		t.Fatalf("keyed service object must decode into entries: %+v", env.Services)
	}
	if env.AI == nil || env.AI.Latest == nil {
		t.Fatal("flat ai fields must be promoted into the latest activity")
	}
	if env.AI.Latest.TimeToFirstTokenMS != nil {
		t.Fatal("missing native TTFT must stay null")
	}
	if env.AI.Latest.Model != "" {
		t.Fatal("selected/loaded model must not be invented from a missing field")
	}
	if env.ObservedAt.Location() != time.UTC {
		t.Fatalf("fixture timestamps must stay UTC, got %v", env.ObservedAt.Location())
	}
}
