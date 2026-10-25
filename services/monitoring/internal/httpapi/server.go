// Package httpapi exposes the private loopback monitoring API and SSE stream.
package httpapi

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/odessy/odessy/services/monitoring/internal/events"
	"github.com/odessy/odessy/services/monitoring/internal/fleet"
	"github.com/odessy/odessy/services/monitoring/internal/history"
	"github.com/odessy/odessy/services/monitoring/internal/protocol"
	"github.com/odessy/odessy/services/monitoring/internal/runtime"
)

// Deps are the wired collaborators.
type Deps struct {
	Token     string
	Fleet     *fleet.Fleet
	Bus       *events.Bus
	Memory    *history.Memory
	Store     *history.Store
	Ingestor  *runtime.Ingestor
	Clock     fleet.Clock
	Intervals protocol.Intervals
}

type snapshot struct {
	hosts     []fleet.HostView
	cursor    string
	createdAt time.Time
	nextIndex int
}

// Server serves every internal route.
type Server struct {
	deps Deps
	mux  *http.ServeMux

	mu    sync.Mutex
	snaps map[string]*snapshot
}

// New builds the handler set.
func New(d Deps) *Server {
	s := &Server{deps: d, mux: http.NewServeMux(), snaps: make(map[string]*snapshot)}
	s.routes()
	return s
}

func (s *Server) routes() {
	s.mux.HandleFunc("GET /internal/v1/monitoring", s.auth(s.handleOverview))
	s.mux.HandleFunc("GET /internal/v1/hosts", s.auth(s.handleHosts))
	s.mux.HandleFunc("GET /internal/v1/hosts/{id}", s.auth(s.handleHost))
	s.mux.HandleFunc("GET /internal/v1/hosts/{id}/metrics", s.auth(s.handleMetrics))
	s.mux.HandleFunc("GET /internal/v1/hosts/{id}/history", s.auth(s.handleHistory))
	s.mux.HandleFunc("GET /internal/v1/events", s.auth(s.handleEvents))
	s.mux.HandleFunc("POST /internal/v1/runtime-observations", s.auth(s.handleRuntime))
	s.mux.HandleFunc("/", s.handleNotFound)
}

// Handler returns the root handler.
func (s *Server) Handler() http.Handler { return s.mux }

func (s *Server) auth(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		header := r.Header.Get("Authorization")
		token := strings.TrimPrefix(header, "Bearer ")
		if token == "" || subtle.ConstantTimeCompare([]byte(token), []byte(s.deps.Token)) != 1 {
			writeError(w, http.StatusUnauthorized, "INVALID_REQUEST", "authentication required", false)
			return
		}
		next(w, r)
	}
}

func (s *Server) handleNotFound(w http.ResponseWriter, r *http.Request) {
	writeError(w, http.StatusNotFound, "HOST_NOT_FOUND", "not found", false)
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

type errorBody struct {
	Code      string `json:"code"`
	Message   string `json:"message"`
	Retryable bool   `json:"retryable"`
}

func writeError(w http.ResponseWriter, status int, code, message string, retryable bool) {
	writeJSON(w, status, map[string]any{
		"schema_version": protocol.SchemaVersion,
		"error":          errorBody{Code: code, Message: message, Retryable: retryable},
	})
}

func (s *Server) handleOverview(w http.ResponseWriter, r *http.Request) {
	snap := s.deps.Fleet.Snapshot(s.deps.Clock.Now())
	rows, bytes, degraded := 0, 0, ""
	if s.deps.Store != nil {
		rows, bytes, degraded = s.deps.Store.Status()
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"schema_version":  protocol.SchemaVersion,
		"generated_at":    snap.GeneratedAt,
		"health":          snap.Health,
		"host_count":      len(snap.Hosts),
		"event_cursor":    s.deps.Bus.Cursor(),
		"history_rows":    rows,
		"history_bytes":   bytes,
		"history_degrade": degraded,
	})
}

func (s *Server) handleHosts(w http.ResponseWriter, r *http.Request) {
	limit := 20
	if raw := r.URL.Query().Get("limit"); raw != "" {
		n, err := strconv.Atoi(raw)
		if err != nil || n <= 0 || n > 50 {
			writeError(w, http.StatusBadRequest, "INVALID_REQUEST", "limit must be 1..50", false)
			return
		}
		limit = n
	}
	cursor := r.URL.Query().Get("cursor")
	snap, offset, err := s.resolveCursor(cursor)
	if err != nil {
		writeError(w, http.StatusBadRequest, "INVALID_REQUEST", "pagination cursor expired; restart pagination", false)
		return
	}
	end := offset + limit
	if end > len(snap.hosts) {
		end = len(snap.hosts)
	}
	var next *string
	if end < len(snap.hosts) {
		value := fmt.Sprintf("%s:%d", s.snapshotID(snap), end)
		next = &value
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"schema_version": protocol.SchemaVersion,
		"hosts":          snap.hosts[offset:end],
		"next_cursor":    next,
		"event_cursor":   snap.cursor,
	})
}

func (s *Server) snapshotID(snap *snapshot) string {
	s.mu.Lock()
	defer s.mu.Unlock()
	for id, candidate := range s.snaps {
		if candidate == snap {
			return id
		}
	}
	return ""
}

func (s *Server) resolveCursor(cursor string) (*snapshot, int, error) {
	if cursor == "" {
		snap := s.newSnapshot()
		return snap, 0, nil
	}
	id, rawOffset, ok := strings.Cut(cursor, ":")
	if !ok {
		return nil, 0, errors.New("bad cursor")
	}
	offset, err := strconv.Atoi(rawOffset)
	if err != nil || offset < 0 {
		return nil, 0, errors.New("bad cursor")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	snap, ok := s.snaps[id]
	if !ok {
		return nil, 0, errors.New("unknown snapshot")
	}
	if s.deps.Clock.Now().Sub(snap.createdAt) > protocol.SnapshotTTL {
		delete(s.snaps, id)
		return nil, 0, errors.New("snapshot expired")
	}
	if offset > len(snap.hosts) {
		return nil, 0, errors.New("bad offset")
	}
	return snap, offset, nil
}

func (s *Server) newSnapshot() *snapshot {
	now := s.deps.Clock.Now()
	view, cursor := s.deps.Fleet.SnapshotWithCursor(now, s.deps.Bus.Cursor)
	snap := &snapshot{hosts: view.Hosts, cursor: cursor, createdAt: now}
	s.mu.Lock()
	defer s.mu.Unlock()
	for id, candidate := range s.snaps {
		if now.Sub(candidate.createdAt) > protocol.SnapshotTTL {
			delete(s.snaps, id)
		}
	}
	if len(s.snaps) >= protocol.MaxSnapshotPins {
		var oldestID string
		var oldest time.Time
		for id, candidate := range s.snaps {
			if oldestID == "" || candidate.createdAt.Before(oldest) {
				oldestID, oldest = id, candidate.createdAt
			}
		}
		delete(s.snaps, oldestID)
	}
	id := fmt.Sprintf("snap-%d", now.UnixNano())
	s.snaps[id] = snap
	return snap
}

func (s *Server) handleHost(w http.ResponseWriter, r *http.Request) {
	view, ok := s.deps.Fleet.Host(r.PathValue("id"), s.deps.Clock.Now())
	if !ok {
		writeError(w, http.StatusNotFound, "HOST_NOT_FOUND", "host not found", false)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"schema_version": protocol.SchemaVersion, "host": view})
}

func (s *Server) handleMetrics(w http.ResponseWriter, r *http.Request) {
	view, ok := s.deps.Fleet.Host(r.PathValue("id"), s.deps.Clock.Now())
	if !ok {
		writeError(w, http.StatusNotFound, "HOST_NOT_FOUND", "host not found", false)
		return
	}
	if view.MonitoringAccess != protocol.AccessAllowed {
		writeError(w, http.StatusForbidden, "HOST_ACCESS_DENIED", "monitoring access denied", false)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"schema_version": protocol.SchemaVersion,
		"host_id":        view.HostID,
		"sample":         view.Sample,
		"stale_groups":   view.StaleGroups,
		"services":       view.Services,
		"ai":             view.AI,
	})
}

func (s *Server) handleHistory(w http.ResponseWriter, r *http.Request) {
	hostID := r.PathValue("id")
	if _, ok := s.deps.Fleet.Host(hostID, s.deps.Clock.Now()); !ok {
		writeError(w, http.StatusNotFound, "HOST_NOT_FOUND", "host not found", false)
		return
	}
	query := r.URL.Query()
	from, err := time.Parse(time.RFC3339, query.Get("from"))
	if err != nil {
		writeError(w, http.StatusBadRequest, "INVALID_RANGE", "from must be RFC3339", false)
		return
	}
	to, err := time.Parse(time.RFC3339, query.Get("to"))
	if err != nil {
		writeError(w, http.StatusBadRequest, "INVALID_RANGE", "to must be RFC3339", false)
		return
	}
	step := 60
	if raw := query.Get("step"); raw != "" {
		n, convErr := strconv.Atoi(raw)
		if convErr != nil || n < 1 {
			writeError(w, http.StatusBadRequest, "INVALID_RANGE", "step must be a positive number of seconds", false)
			return
		}
		step = n
	}
	var series []string
	for _, value := range query["series"] {
		if value == "" {
			continue
		}
		series = append(series, value)
	}
	if len(series) == 0 {
		writeError(w, http.StatusBadRequest, "INVALID_REQUEST", "at least one series is required", false)
		return
	}
	var result []history.Series
	if s.deps.Store != nil && step >= 60 {
		result, err = s.deps.Store.Query(hostID, series, from, to, time.Duration(step)*time.Second)
	} else {
		result, err = s.deps.Memory.Query(hostID, series, from, to, time.Duration(step)*time.Second)
	}
	if err != nil {
		code := "INVALID_REQUEST"
		if errors.Is(err, history.ErrInvalidRange) {
			code = "INVALID_RANGE"
		}
		if errors.Is(err, history.ErrLimit) {
			code = "RESOURCE_LIMIT"
		}
		writeError(w, http.StatusBadRequest, code, err.Error(), false)
		return
	}
	payload := map[string]any{
		"schema_version": protocol.SchemaVersion,
		"host_id":        hostID,
		"from":           from.UTC(),
		"to":             to.UTC(),
		"step_seconds":   step,
		"series":         result,
	}
	raw, _ := json.Marshal(payload)
	if len(raw) > protocol.MaxHistoryResponseBytes {
		writeError(w, http.StatusRequestEntityTooLarge, "RESOURCE_LIMIT", "history response exceeds 2 MiB", false)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(raw)
}

func (s *Server) handleRuntime(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(io.LimitReader(r.Body, protocol.MaxRuntimePayloadBytes+1))
	if err != nil {
		writeError(w, http.StatusBadRequest, "INVALID_REQUEST", "unreadable body", false)
		return
	}
	if len(body) > protocol.MaxRuntimePayloadBytes {
		writeError(w, http.StatusRequestEntityTooLarge, "RESOURCE_LIMIT", "runtime payload exceeds 64 KiB", false)
		return
	}
	if err := s.deps.Ingestor.Handle(body); err != nil {
		status := http.StatusBadRequest
		code := "INVALID_REQUEST"
		switch {
		case errors.Is(err, fleet.ErrUnknownHost):
			status, code = http.StatusNotFound, "HOST_NOT_FOUND"
		case errors.Is(err, fleet.ErrRuntimeAttribution), errors.Is(err, fleet.ErrAccessDenied):
			status, code = http.StatusForbidden, "HOST_ACCESS_DENIED"
		case errors.Is(err, fleet.ErrGenerationMismatch):
			status, code = http.StatusConflict, "INVALID_REQUEST"
		case errors.Is(err, protocol.ErrResourceLimit):
			status, code = http.StatusRequestEntityTooLarge, "RESOURCE_LIMIT"
		}
		writeError(w, status, code, err.Error(), false)
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"schema_version": protocol.SchemaVersion, "accepted": true})
}

func (s *Server) handleEvents(w http.ResponseWriter, r *http.Request) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, http.StatusInternalServerError, "MONITOR_UNAVAILABLE", "streaming unsupported", true)
		return
	}
	sub, err := s.deps.Bus.Subscribe(r.Header.Get("Last-Event-ID"))
	if err != nil {
		if errors.Is(err, events.ErrResyncRequired) {
			writeResync(w, flusher, s.deps.Bus.Epoch())
			return
		}
		writeError(w, http.StatusServiceUnavailable, "MONITOR_UNAVAILABLE", "subscriber limit reached", true)
		return
	}
	defer sub.Close()

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)
	fmt.Fprint(w, ": connected\n\n")
	flusher.Flush()

	keepalive := time.NewTicker(protocol.KeepAliveInterval)
	defer keepalive.Stop()
	ctx := r.Context()
	for {
		select {
		case <-ctx.Done():
			return
		case <-keepalive.C:
			fmt.Fprint(w, ": keepalive\n\n")
			flusher.Flush()
			if sub.Overflowed() {
				writeResync(w, flusher, s.deps.Bus.Epoch())
				return
			}
		case ev, open := <-sub.C:
			if open {
				sub.Ack(ev)
			}
			if !open {
				writeResync(w, flusher, s.deps.Bus.Epoch())
				return
			}
			writeEvent(w, flusher, s.deps.Bus.Epoch(), ev)
		}
	}
}

func writeEvent(w io.Writer, flusher http.Flusher, epoch string, ev events.Event) {
	data, err := json.Marshal(map[string]any{
		"schema_version": protocol.SchemaVersion,
		"host_id":        ev.HostID,
		"payload":        ev.Data,
	})
	if err != nil {
		return
	}
	fmt.Fprintf(w, "id: %s\nevent: %s\ndata: %s\n\n", ev.ID(epoch), ev.Name, data)
	flusher.Flush()
}

func writeResync(w io.Writer, flusher http.Flusher, epoch string) {
	data, _ := json.Marshal(map[string]any{"schema_version": protocol.SchemaVersion, "epoch": epoch})
	fmt.Fprintf(w, "event: %s\ndata: %s\n\n", fleet.EventResyncRequired, data)
	flusher.Flush()
}

// HandlerWithContext keeps the handler signature explicit for tests.
var _ = context.Background
