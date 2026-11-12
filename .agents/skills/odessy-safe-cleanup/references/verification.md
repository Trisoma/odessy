# Cleanup verification checklist

Run from the repository root unless a working directory is named. Commands are
templates: verify installed SDKs and caches first; do not download merely to prepare.

- [ ] Capture `git status --short`, `git branch --show-current`, `git rev-parse HEAD`.
- [ ] Name one burden, supported consumers, replacement and observable invariants.
- [ ] Trace static AND dynamic/runtime/external consumers, package inventories,
      subprocess entry points, CLI names, plugin profiles and platform helpers.
- [ ] Record a focused baseline; distinguish existing failures from new regressions.
- [ ] Preserve both engine selections and policy/consent/transport boundaries.
- [ ] Choose affected checks below, rerun after the slice and inspect the full diff.
- [ ] Verify contributor fixture/setup and actionable troubleshooting offline.
- [ ] Record source vs installed vs native/hardware evidence separately.
- [ ] `git diff --check`; preserve unrelated edits. Report NO CHANGE / DEFERRED if
      safety evidence is insufficient; never auto-delete from analyzer output.

| Scope | Existing checks and consumer evidence |
| --- | --- |
| Python selection/composition | `python -m pytest -q tests/test_runtime_backends.py tests/test_harness_resources.py`; inspect selection.py, Harness config/resources/profile/pin and package-data. Add affected lifecycle/bridge fixture tests, not live providers. |
| Authority/security | `python -m pytest -q tests/test_authority_boundary.py tests/test_permission_hardening.py`; inspect tool contract, credentials, policy and permission consumers. OS sandbox live jobs are separate. |
| TypeScript MCP | In tools: `npm run typecheck`, `npm test`; package-lock.json + local TypeScript, server.registerTool and execution contract consumers. `npm ci` only if dependency preparation is authorized/needed. |
| Rust pairing | In native/odessy-link: `cargo test --locked --offline`; existing CI also runs `cargo clippy --all-targets -- -D warnings`. Preserve identity, grants, transport and telemetry consumers. |
| Go monitoring | In services/monitoring: `go test ./...`, `go vet ./...`; set `GOPROXY=off` and `GOTOOLCHAIN=local` for cache-only checks. go.mod/go.sum verify dependency closure; live target/freshness checks are separate. |
| Dart client | In packages/odessy_client: `dart analyze`, `dart test`; use existing pubspec.lock and cached SDK. |
| Flutter desktop | In apps/desktop: `flutter analyze --no-pub`, `flutter test --no-pub`; root `python tool/check_desktop_contracts.py`. CI also checks `dart format --output=none --set-exit-if-changed lib test`. |
| Packaging | `python -m pytest -q tests/test_setup_bundle.py`; inspect pyproject.toml package-data/scripts, installer release manifests, tool/build_setup_bundle.py and tool/audit_setup_artifact.py. Build/audit coherent artifacts only when the actual slice warrants it; do not rebuild for a skill-only edit. |
| Contributor smoke | `python -m odessy --help`; documented fake-model time/list fixtures only with isolated config. Do not launch live providers or read actual credentials as a fixture. |

## Tool selection and limitations

Use existing pytest, strict TypeScript, Node test runner, Cargo tests/Clippy,
Go tests/vet, Dart/Flutter analysis/tests, contract validators and git checks first.
Ruff is optional for a demonstrated Python lint gap: use a pinned development-only
tool and scoped rules/targets, no autofix or broad new lint gate. Vulture and Knip
are optional candidate generators, not deletion proof: lazy imports, registry names,
MCP callers and packaged plugin files escape their graph. Do not add them merely
for tool count; require a concrete gap and maintainable config before installation.

Current security mechanisms include policy/permission regressions, sandbox live CI,
CodeQL, immutable Harness artifact hashes/licences, installer manifest/ABI/hash
validation and language lockfiles. These do not imply a current vulnerability audit.
Do not refresh locks or runtime dependencies as incidental cleanup. Recheck known
baseline failures before relying on older reports; redact credential assertions.

Evidence row per candidate: path/symbol | consumers (static/dynamic/external/package)
| replacement | baseline command/result | after command/result | coverage limits
| decision (retain/remove/NO CHANGE/DEFERRED). Unknown consumers => retain/defer.
