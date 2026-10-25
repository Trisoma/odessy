// Package runtime admits sanitized runtime observations from Python.
package runtime

import (
	"fmt"
	"sync"
	"time"

	"github.com/odessy/odessy/services/monitoring/internal/fleet"
	"github.com/odessy/odessy/services/monitoring/internal/protocol"
)

// Ingestor validates and deduplicates runtime telemetry.
type Ingestor struct {
	fleet  *fleet.Fleet
	mu     sync.Mutex
	lastBy map[string]uint64
	now    func() time.Time
}

// New builds an ingestor bound to the fleet projection.
func New(f *fleet.Fleet, now func() time.Time) *Ingestor {
	return &Ingestor{fleet: f, lastBy: make(map[string]uint64), now: now}
}

// Handle validates a raw runtime observation body and applies it.
func (i *Ingestor) Handle(body []byte) error {
	if len(body) > protocol.MaxRuntimePayloadBytes {
		return fmt.Errorf("%w: runtime body exceeds %d bytes", protocol.ErrResourceLimit, protocol.MaxRuntimePayloadBytes)
	}
	if err := protocol.ScanForbiddenKeys(body); err != nil {
		return err
	}
	var env protocol.RuntimeEnvelope
	if err := protocol.DecodeBounded(body, protocol.MaxRuntimePayloadBytes, &env); err != nil {
		return err
	}
	if err := protocol.ValidateRuntimeEnvelope(env); err != nil {
		return err
	}

	key := fmt.Sprintf("%s/%s/%s", env.SourceID, env.HostID, env.Generation)
	i.mu.Lock()
	if last, ok := i.lastBy[key]; ok && env.Sequence <= last {
		i.mu.Unlock()
		return fleet.ErrOutOfOrder
	}
	i.lastBy[key] = env.Sequence
	i.mu.Unlock()

	return i.fleet.AcceptRuntime(env, i.now())
}
