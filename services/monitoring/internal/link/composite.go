package link

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"

	"github.com/odessy/odessy/services/monitoring/internal/protocol"
)

// Composite gives the existing scheduler one Link while preserving independent
// providers (for example the paired Rust worker and one SSH computer). It owns
// no polling, freshness or event state; those stay in the one Go fleet.
type Composite struct {
	mu        sync.RWMutex
	providers []*compositeProvider
	routes    map[string]Link
	closed    bool
}

type compositeProvider struct {
	link Link
	last protocol.Inventory
	has  bool
}

// Compose combines providers. Inventory is merged and cached per provider so a
// temporary provider failure does not make that provider's known hosts vanish
// (which would create false revocation tombstones).
func Compose(links ...Link) *Composite {
	c := &Composite{routes: make(map[string]Link)}
	for _, source := range links {
		if source != nil {
			c.providers = append(c.providers, &compositeProvider{link: source})
		}
	}
	return c
}

func (c *Composite) Inventory(ctx context.Context) (protocol.Inventory, error) {
	c.mu.RLock()
	if c.closed {
		c.mu.RUnlock()
		return protocol.Inventory{}, &Failure{Code: "composite_closed", Message: "monitor link is closed"}
	}
	providers := append([]*compositeProvider(nil), c.providers...)
	c.mu.RUnlock()
	if len(providers) == 0 {
		return protocol.Inventory{}, &Failure{Code: "no_provider", Message: "no monitoring provider is configured"}
	}

	hosts := make([]protocol.HostIdentity, 0)
	revisions := make([]string, 0, len(providers))
	routes := make(map[string]Link)
	var failures []error
	usable := 0
	for _, provider := range providers {
		inventory, err := provider.link.Inventory(ctx)
		if err == nil {
			if err = protocol.ValidateInventory(inventory); err == nil {
				provider.last = inventory
				provider.has = true
			}
		}
		if err != nil {
			failures = append(failures, fmt.Errorf("%T: %w", provider.link, err))
			if provider.has {
				inventory = provider.last
			} else {
				continue
			}
		}
		usable++
		if inventory.Revision != "" {
			revisions = append(revisions, inventory.Revision)
		}
		for _, host := range inventory.Hosts {
			if existing, ok := routes[host.HostID]; ok && existing != provider.link {
				return protocol.Inventory{}, &Failure{
					Code: "host_conflict", Message: "two providers claim the same host identity"}
			}
			routes[host.HostID] = provider.link
			hosts = append(hosts, host)
		}
	}
	if usable == 0 {
		if len(failures) == 0 {
			return protocol.Inventory{}, &Failure{Code: "no_inventory", Message: "inventory is empty"}
		}
		return protocol.Inventory{}, errors.Join(failures...)
	}
	sort.Slice(hosts, func(i, j int) bool { return hosts[i].HostID < hosts[j].HostID })
	merged := protocol.Inventory{Revision: strings.Join(revisions, "|"), Hosts: hosts}
	if err := protocol.ValidateInventory(merged); err != nil {
		return protocol.Inventory{}, err
	}
	c.mu.Lock()
	if c.closed {
		c.mu.Unlock()
		return protocol.Inventory{}, &Failure{Code: "composite_closed", Message: "monitor link is closed"}
	}
	c.routes = routes
	c.mu.Unlock()
	return merged, nil
}

func (c *Composite) routed(hostID string) (Link, error) {
	c.mu.RLock()
	source := c.routes[hostID]
	closed := c.closed
	c.mu.RUnlock()
	if closed {
		return nil, &Failure{Code: "composite_closed", Message: "monitor link is closed"}
	}
	if source == nil {
		return nil, &Failure{Code: "unknown_host", Message: "host has no provider in this monitor"}
	}
	return source, nil
}

func (c *Composite) Heartbeat(ctx context.Context, ref protocol.HostRef) (protocol.Heartbeat, error) {
	source, err := c.routed(ref.HostID)
	if err != nil {
		return protocol.Heartbeat{}, err
	}
	return source.Heartbeat(ctx, ref)
}

func (c *Composite) Sample(ctx context.Context, ref protocol.HostRef, groups []string) (protocol.Observation, error) {
	source, err := c.routed(ref.HostID)
	if err != nil {
		return protocol.Observation{}, err
	}
	return source.Sample(ctx, ref, groups)
}

func (c *Composite) Close() error {
	c.mu.Lock()
	if c.closed {
		c.mu.Unlock()
		return nil
	}
	c.closed = true
	providers := c.providers
	c.providers = nil
	c.routes = make(map[string]Link)
	c.mu.Unlock()
	var failures []error
	for _, provider := range providers {
		if err := provider.link.Close(); err != nil {
			failures = append(failures, err)
		}
	}
	return errors.Join(failures...)
}
