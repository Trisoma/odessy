# Monitoring API / UI teammate handoff

Contract revision: `monitoring-v1`. Sources: `contracts/monitoring/`, outer
`multihost-monitoring-codex-plan.md` section2, protected Dart
`MonitoringClient` exported by `packages/odessy_client`.

| GET route | Result |
| --- | --- |
| `/v1/monitoring` | health, history degradation and event cursor |
| `/v1/monitoring/hosts` | hosts, next_cursor, event_cursor |
| `/v1/monitoring/hosts/{id}` | schema_version and `host` object |
| `/v1/monitoring/hosts/{id}/metrics` | sample, stale_groups, services and ai |
| `/v1/monitoring/hosts/{id}/history` | bounded series; SQLite aggregates for steps of 60 seconds or more |
| `/v1/monitoring/events` | named SSE events with epoch:sequence IDs |

Use existing application Authorization bearer headers, never query-string
credentials. Complete pinned pagination then subscribe with Last-Event-ID.
`resync_required` means discard the replay position and fetch a fresh snapshot.
Reconnection never repeats tool/model work. Disabled/unavailable service returns
503 while conversation APIs remain usable. Unknown/denied host returns404;
invalid queries/ranges return400. No execution, pairing or mutation API is added.

Render trust, connectivity, telemetry permission, collector quality and service
readiness independently. Old samples remain visibly historical/stale. Missing
GPU/TTFT/power is unknown/unsupported, not zero. Display timestamps/units. Show
history degradation explicitly rather than an apparently successful empty chart.

UI example: instantiate `MonitoringClient(existingApiOrigin, tokenFromCredentialStore)`,
call `listHosts()`, follow `nextCursor`, and `watchEvents(after: snapshot.cursor)`.
Close the client on service/origin changes. `getHost`, `getMetrics` and `getHistory`
are read-only. No Flutter application presentation files were changed here.

## Persisted history

SQLite stores 60-second buckets for 24 hours and 300-second buckets for seven days.
Choose steps divisible by the selected tier; ranges older than 24 hours require
300-second resolution. Start times round down to the tier boundary: the first
point represents the overlapping whole bucket, not a clipped partial interval.
Queries are bounded to 1,000 points, 16 series and 2 MiB. Mean values use bounded
sample-duration weights; min/max and counts are retained. Missing observations
are not fabricated. Storage failure degrades history without stopping live metrics.
