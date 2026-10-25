package protocol

import (
	"os"
	"path/filepath"
	"testing"
)

func readFixture(t *testing.T, name string) []byte {
	t.Helper()
	path := filepath.Join("..", "..", "testdata", name)
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read fixture %s: %v", name, err)
	}
	return data
}

func TestOwnedFixturesMatchTheContract(t *testing.T) {
	var inv Inventory
	if err := DecodeBounded(readFixture(t, "inventory.json"), MaxWorkerFrameBytes, &inv); err != nil {
		t.Fatalf("inventory fixture: %v", err)
	}
	if err := ValidateInventory(inv); err != nil {
		t.Fatalf("inventory fixture invalid: %v", err)
	}
	if len(inv.Hosts) != 3 {
		t.Fatalf("expected 3 fixture hosts, got %d", len(inv.Hosts))
	}

	var obs Observation
	if err := DecodeBounded(readFixture(t, "sample.json"), MaxWorkerFrameBytes, &obs); err != nil {
		t.Fatalf("sample fixture: %v", err)
	}
	if err := ValidateObservation(obs); err != nil {
		t.Fatalf("sample fixture invalid: %v", err)
	}
	if obs.Memory.SharedBytes != nil {
		t.Fatal("unsupported memory values must stay null")
	}
	if obs.CPU.UtilizationPercent == nil || *obs.CPU.UtilizationPercent != 37.5 {
		t.Fatal("fixture CPU utilisation not decoded")
	}
	if obs.GPU.Status != CollectorUnsupported || obs.DiskIO.Status != CollectorPermissionDenied || obs.Power.Status != CollectorWarmingUp {
		t.Fatal("fixture must cover unsupported / permission_denied / warming_up collector states")
	}
}

func TestSecretScanRejectsConversationFields(t *testing.T) {
	cases := []string{
		`{"ai":{"latest":{"prompt":"hi"}}}`,
		`{"services":[{"messages":[]}]}`,
		`{"ai":{"latest":{"credentials":{"api_key":"x"}}}}`,
	}
	for _, c := range cases {
		if err := ScanForbiddenKeys([]byte(c)); err == nil {
			t.Fatalf("expected rejection for %s", c)
		}
	}
	if err := ScanForbiddenKeys([]byte(`{"ai":{"latest":{"model":"m","ttft_ms":null}}}`)); err != nil {
		t.Fatalf("valid runtime document rejected: %v", err)
	}
}
