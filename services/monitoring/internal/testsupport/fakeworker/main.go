// Command fakeworker is a local stand-in for the Rust telemetry worker. It is
// used only by tests in this module and never shipped.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"time"

	"github.com/odessy/odessy/services/monitoring/internal/protocol"
)

func main() {
	if len(os.Args) != 3 || os.Args[1] != "telemetry" || os.Args[2] != "worker" {
		fmt.Fprintln(os.Stderr, "fakeworker: expected telemetry worker")
		os.Exit(2)
	}
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 4096), protocol.MaxWorkerFrameBytes)
	out := jsonEncoder()
	for scanner.Scan() {
		line := scanner.Bytes()
		if os.Getenv("FAKEWORKER_GARBAGE_FIRST") == "1" {
			fmt.Fprintln(os.Stdout, "this is not json")
			os.Unsetenv("FAKEWORKER_GARBAGE_FIRST")
		}
		var req protocol.WorkerRequest
		if err := json.Unmarshal(line, &req); err != nil {
			continue
		}
		if delay := os.Getenv("FAKEWORKER_DELAY_MS"); delay != "" {
			if ms, err := strconv.Atoi(delay); err == nil {
				time.Sleep(time.Duration(ms) * time.Millisecond)
			}
		}
		resp := protocol.WorkerResponse{SchemaVersion: protocol.SchemaVersion, ID: req.ID, OK: true}
		result, err := handle(req)
		if err != nil {
			resp.OK = false
			resp.Error = &protocol.WorkerError{Code: "worker_error", Message: err.Error()}
		} else {
			raw, marshalErr := json.Marshal(result)
			if marshalErr != nil {
				resp.OK = false
				resp.Error = &protocol.WorkerError{Code: "encode_error", Message: "encode failed"}
			} else {
				resp.Result = raw
			}
		}
		out(resp)
	}
}

func jsonEncoder() func(protocol.WorkerResponse) {
	enc := json.NewEncoder(os.Stdout)
	return func(resp protocol.WorkerResponse) { _ = enc.Encode(resp) }
}

func handle(req protocol.WorkerRequest) (any, error) {
	now := time.Now().UTC()
	switch req.Op {
	case protocol.OpInventory:
		return protocol.Inventory{Revision: "rev-1", Hosts: []protocol.HostIdentity{{
			HostID:            os.Getenv("FAKEWORKER_HOST_ID"),
			InstallationID:    "install-1",
			FingerprintSHA256: "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
			Alias:             "fake",
			TrustState:        protocol.TrustLocal,
			MonitoringAccess:  protocol.AccessAllowed,
			Generation:        "g1",
		}}}, nil
	case protocol.OpHeartbeat:
		return protocol.Heartbeat{HostID: req.HostID, Generation: req.Generation, BootID: "boot-1", Sequence: 1, ObservedAt: now, ReceivedAt: now}, nil
	case protocol.OpSample:
		return protocol.Observation{
			HostID:     req.HostID,
			Generation: req.Generation,
			Sample: protocol.Sample{
				ID:         fmt.Sprintf("obs-%d", time.Now().UnixNano()),
				BootID:     "boot-1",
				Sequence:   1,
				ObservedAt: now,
				IntervalMS: 10000,
				Scope:      "host",
				CPU:        &protocol.CPUGroup{Group: protocol.Group{Status: protocol.CollectorOK, ObservedAt: &now}},
			},
		}, nil
	case protocol.OpCancel:
		return map[string]any{"acknowledged": true, "target_id": req.TargetID}, nil
	default:
		return nil, fmt.Errorf("unknown op")
	}
}
