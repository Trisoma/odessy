# Third-party notices — odessy-monitor

This module has **zero third-party Go dependencies**. `go.mod` lists only the
standard library (`go 1.25`), so no dependency licences need to be redistributed
for the Go service itself.

Planned but not yet vendored:

- `modernc.org/sqlite` (BSD-3-Clause) — pinned pure-Go SQLite driver for
  history persistence. It could not be fetched in the offline build
  environment; see `README.md` "Known limitations". When added, pin the exact
  version and record its full transitive module list here before release.

Runtime interactions are process boundaries, not linked code:

- The Rust `odessy-link` telemetry worker is distributed and licensed with the
  desktop application.
- Python and Flutter components are owned by Codex and the UI teammate.
