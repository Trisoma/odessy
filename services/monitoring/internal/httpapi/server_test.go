package httpapi

import (
	"bufio"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/odessy/odessy/services/monitoring/internal/events"
	"github.com/odessy/odessy/services/monitoring/internal/fleet"
	"github.com/odessy/odessy/services/monitoring/internal/history"
	"github.com/odessy/odessy/services/monitoring/internal/protocol"
	"github.com/odessy/odessy/services/monitoring/internal/runtime"
	"github.com/odessy/odessy/services/monitoring/internal/testsupport"
)

const testToken = "0123456789abcdef0123456789abcdef"

var baseTime = time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)

func hostID(c byte) string { return "host_" + strings.Repeat(string(c), 64) }

func identity(id, generation string, trust protocol.TrustState) protocol.HostIdentity {
	return protocol.HostIdentity{
		HostID:            id,
		InstallationID:    "install-" + id,
		FingerprintSHA256: strings.Repeat("f", 64),
		TrustState:        trust,
		MonitoringAccess:  protocol.AccessAllowed,
		Generation:        generation,
	}
}

type harness struct {
	server   *Server
	fleet    *fleet.Fleet
	bus      *events.Bus
	clock    *testsupport.Clock
	memory   *history.Memory
	revision int
}

func newHarness(t *testing.T, ids ...string) *harness {
	t.Helper()
	clock := testsupport.NewClock(baseTime)
	settings := protocol.DefaultSettings()
	settings.MonitoredHostIDs = ids
	norm, err := settings.Normalized()
	if err != nil {
		t.Fatal(err)
	}
	bus := events.New("epoch1")
	f := fleet.New(clock, norm, norm.Intervals(), func(ev fleet.Event) {
		bus.Publish(ev.Kind, ev.HostID, ev.Payload)
	})
	mem := history.NewMemory(protocol.MemoryHorizon, protocol.MemoryRingBytes)
	s := New(Deps{
		Token:     testToken,
		Fleet:     f,
		Bus:       bus,
		Memory:    mem,
		Ingestor:  runtime.New(f, clock.Now),
		Clock:     clock,
		Intervals: norm.Intervals(),
	})
	return &harness{server: s, fleet: f, bus: bus, clock: clock, memory: mem, revision: 1}
}

func (h *harness) applyInventory(t *testing.T, now time.Time, hosts ...protocol.HostIdentity) {
	t.Helper()
	h.revision++
	rev := "r" + string(rune('0'+h.revision))
	if err := h.fleet.ApplyInventory(protocol.Inventory{Revision: rev, Hosts: hosts}, now); err != nil {
		t.Fatalf("apply inventory: %v", err)
	}
}

func (h *harness) do(t *testing.T, method, target string, body string, token string) *httptest.ResponseRecorder {
	t.Helper()
	var reader *strings.Reader
	if body == "" {
		reader = strings.NewReader("")
	} else {
		reader = strings.NewReader(body)
	}
	req := httptest.NewRequest(method, target, reader)
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	rec := httptest.NewRecorder()
	h.server.Handler().ServeHTTP(rec, req)
	return rec
}

func TestAuthenticationIsRequiredOnEveryRoute(t *testing.T) {
	h := newHarness(t)
	for _, target := range []string{
		"/internal/v1/monitoring",
		"/internal/v1/hosts",
		"/internal/v1/events",
		"/internal/v1/hosts/" + hostID('a'),
		"/internal/v1/hosts/" + hostID('a') + "/history?from=2026-01-01T00:00:00Z&to=2026-01-01T01:00:00Z&series=cpu.utilization_percent",
	} {
		rec := h.do(t, http.MethodGet, target, "", "")
		if rec.Code != http.StatusUnauthorized {
			t.Fatalf("%s without auth: expected 401, got %d", target, rec.Code)
		}
		var body struct {
			SchemaVersion int `json:"schema_version"`
			Error         struct {
				Code string `json:"code"`
			} `json:"error"`
		}
		if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
			t.Fatalf("error envelope: %v", err)
		}
		if body.SchemaVersion != protocol.SchemaVersion || body.Error.Code == "" {
			t.Fatalf("unstable error envelope: %s", rec.Body.String())
		}
	}
	if rec := h.do(t, http.MethodGet, "/internal/v1/monitoring", "", "wrong-token"); rec.Code != http.StatusUnauthorized {
		t.Fatalf("wrong token: expected 401, got %d", rec.Code)
	}
	if rec := h.do(t, http.MethodGet, "/arbitrary/path", "", testToken); rec.Code != http.StatusNotFound {
		t.Fatalf("arbitrary path: expected 404, got %d", rec.Code)
	}
}

func TestSnapshotPaginationSurvivesConcurrentRevoke(t *testing.T) {
	ids := []string{hostID('a'), hostID('b'), hostID('c')}
	h := newHarness(t, ids...)
	hosts := []protocol.HostIdentity{
		identity(ids[0], "g1", protocol.TrustPaired),
		identity(ids[1], "g1", protocol.TrustPaired),
		identity(ids[2], "g1", protocol.TrustPaired),
	}
	h.applyInventory(t, baseTime, hosts...)

	rec := h.do(t, http.MethodGet, "/internal/v1/hosts?limit=2", "", testToken)
	if rec.Code != http.StatusOK {
		t.Fatalf("list failed: %d %s", rec.Code, rec.Body.String())
	}
	var page1 struct {
		Hosts       []map[string]any `json:"hosts"`
		NextCursor  *string          `json:"next_cursor"`
		EventCursor string           `json:"event_cursor"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &page1); err != nil {
		t.Fatal(err)
	}
	if len(page1.Hosts) != 2 || page1.NextCursor == nil {
		t.Fatalf("expected 2 hosts and a cursor, got %+v", page1)
	}

	// Revoke a host between pages: the pinned snapshot must not shift.
	h.applyInventory(t, baseTime, hosts[0], hosts[2])

	rec = h.do(t, http.MethodGet, "/internal/v1/hosts?limit=2&cursor="+*page1.NextCursor, "", testToken)
	if rec.Code != http.StatusOK {
		t.Fatalf("page 2 failed: %d %s", rec.Code, rec.Body.String())
	}
	var page2 struct {
		Hosts       []map[string]any `json:"hosts"`
		EventCursor string           `json:"event_cursor"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &page2); err != nil {
		t.Fatal(err)
	}
	if len(page2.Hosts) != 1 {
		t.Fatalf("pinned snapshot must still hold 3 hosts, page 2 had %d", len(page2.Hosts))
	}
	if page2.EventCursor != page1.EventCursor {
		t.Fatal("event cursor must come from the pinned snapshot")
	}

	if rec := h.do(t, http.MethodGet, "/internal/v1/hosts?cursor=snap-999:0", "", testToken); rec.Code != http.StatusBadRequest {
		t.Fatalf("expired cursor must be rejected, got %d", rec.Code)
	}
}

func TestHistoryEnforcesQueryLimits(t *testing.T) {
	id := hostID('a')
	h := newHarness(t, id)
	h.applyInventory(t, baseTime, identity(id, "g1", protocol.TrustPaired))

	rec := h.do(t, http.MethodGet,
		"/internal/v1/hosts/"+id+"/history?from=2026-01-01T00:00:00Z&to=2026-01-01T06:00:00Z&step=1&series=cpu.utilization_percent",
		"", testToken)
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "RESOURCE_LIMIT") {
		t.Fatalf("range beyond 1000 points must be an explicit limit error, got %d %s", rec.Code, rec.Body.String())
	}

	rec = h.do(t, http.MethodGet,
		"/internal/v1/hosts/"+id+"/history?from=2026-01-01T00:00:00Z&to=2026-01-01T00:10:00Z&step=60&series=secret.value",
		"", testToken)
	if rec.Code != http.StatusBadRequest || !strings.Contains(rec.Body.String(), "INVALID_REQUEST") {
		t.Fatalf("unknown series must be rejected, got %d %s", rec.Code, rec.Body.String())
	}
}

func TestEventsResyncOnUnknownEpoch(t *testing.T) {
	h := newHarness(t)
	srv := httptest.NewServer(h.server.Handler())
	defer srv.Close()

	req, err := http.NewRequest(http.MethodGet, srv.URL+"/internal/v1/events", nil)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+testToken)
	req.Header.Set("Last-Event-ID", "other-epoch:12")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("expected SSE 200, got %d", resp.StatusCode)
	}
	scanner := bufio.NewScanner(resp.Body)
	deadline := time.Now().Add(2 * time.Second)
	for scanner.Scan() {
		if strings.Contains(scanner.Text(), "event: resync_required") {
			return
		}
		if time.Now().After(deadline) {
			break
		}
	}
	t.Fatal("unknown epoch must produce resync_required before closing")
}

func TestRuntimeObservationsRejectSecretsAndRemoteAttribution(t *testing.T) {
	local := hostID('0')
	remote := hostID('1')
	h := newHarness(t, local, remote)
	h.applyInventory(t, baseTime,
		identity(local, "g1", protocol.TrustLocal),
		identity(remote, "g2", protocol.TrustPaired),
	)

	secret := `{"schema_version":1,"host_id":"` + local + `","generation":"g1","source_id":"py","sequence":1,"observed_at":"2026-01-01T00:00:00Z","services":[],"ai":null,"prompt":"hello"}`
	rec := h.do(t, http.MethodPost, "/internal/v1/runtime-observations", secret, testToken)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("secret-bearing payload must be rejected, got %d %s", rec.Code, rec.Body.String())
	}

	remoteBody := `{"schema_version":1,"host_id":"` + remote + `","generation":"g2","source_id":"py","sequence":1,"observed_at":"2026-01-01T00:00:00Z"}`
	rec = h.do(t, http.MethodPost, "/internal/v1/runtime-observations", remoteBody, testToken)
	if rec.Code != http.StatusForbidden {
		t.Fatalf("remote runtime attribution must be refused, got %d %s", rec.Code, rec.Body.String())
	}

	okBody := `{"schema_version":1,"host_id":"` + local + `","generation":"g1","source_id":"py","sequence":1,"observed_at":"2026-01-01T00:00:00Z","services":[{"name":"mcp_server","status":"healthy"}]}`
	rec = h.do(t, http.MethodPost, "/internal/v1/runtime-observations", okBody, testToken)
	if rec.Code != http.StatusAccepted {
		t.Fatalf("sanitized local payload must be accepted, got %d %s", rec.Code, rec.Body.String())
	}
	rec = h.do(t, http.MethodPost, "/internal/v1/runtime-observations", okBody, testToken)
	if rec.Code != http.StatusBadRequest {
		t.Fatalf("duplicate source sequence must be rejected, got %d", rec.Code)
	}
}
