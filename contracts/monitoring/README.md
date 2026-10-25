# Monitoring contract revision monitoring-v1

JSON Schema2020-12. Bootstrap/private worker and public routes follow the outer
multihost-monitoring-codex-plan.md section2. Units: bytes, bytes/second, ms, Celsius, watts.
Unsupported values are null with quality, not zero. Max worker line256KiB, runtime64KiB,
public JSON2MiB. Heartbeat5s, basic10s, GPU15s, capacity60s. Inventory1s/trust lease3s.

Private worker ops: inventory, heartbeat, sample, cancel. No arbitrary commands.
Public events use epoch:sequence IDs and Last-Event-ID; expired cursor requires resync.
Trust, connectivity, service health and collector quality are independent.
