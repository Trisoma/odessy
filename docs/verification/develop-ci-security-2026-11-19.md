# Develop CI and security fixes — November 19, 2026

Scope: the Linux failures after PR #74 merged at `7351c69`, Dependabot alerts
#34–36 and CodeQL alert #13. No existing user installation was changed.

## Linux CI

Both failed Python jobs in run `44435868676` refused fake Harness startup when
the runner could provide full host isolation. Automatic isolation resolved to
`required`, while the pinned Harness boundary cannot hide credential paths.
The protocol tests had inadvertently depended on the runner's capabilities.

The protocol fixture now injects an unconfined host registry only for its explicit
set of fake-runtime test modules. Tests inspecting a launchable sandbox overlay
request `preferred` isolation explicitly. Production policy is unchanged. A new
capable-host regression verifies that `auto` still resolves to `required` and
refuses the Harness hidden-path gap. Native sandbox tests retain real host probes.

## Security changes

- OpenTelemetry API: pin 1.45.2 for GHSA-mf5v-w3w3-f6cg. The Windows dependency
  lock, wheel URL/hash/size, unpacked resource size and license inventory agree.
  The downloaded Apache-2.0 wheel's metadata and SHA-256 were verified.
- Node HTTP adapter: resolve `@hono/node-server` 2.1.6 for
  GHSA-qv5m-9pm6-hqp3 and GHSA-jpvv-r467-5p4w. It remains a transitive dependency;
  the registry integrity matches the lockfile and `npm ci` succeeds.
- Audio errors: fixed actionable microphone/speaker messages replace exception
  text in HTTP errors, voice state and emitted events. The new regression failed
  before the fix for both device-error paths, then passed after the change.

## Local checks

| Command/check | Result |
| --- | --- |
| All `tests/test_harness_*.py` | 301 passed |
| `test_desktop_voice`, `test_native_audio`, `test_live_voice_configuration`, `test_harness_sandbox` | 86 passed |
| `test_installer_space`, `test_setup_bundle`, `test_desktop_voice_api`, `test_voice_runtime_events`, `test_voice_native_startup` | 71 passed, 12 subtests passed |
| Revised HTTP/state/event disclosure regression | 4 passed |
| `npm ci --no-audit --no-fund` followed by `npm test` | 202 passed, 5 skipped |
| `npm audit --omit=dev --json` | 0 vulnerabilities |
| Windows installer resource/lock/license consistency | Passed |
| Isolated OpenTelemetry 1.45.2 wheel | Valid trace accepted; one-million-character header rejected in 0.0011 seconds in this smoke check |
| Newly built application wheel installed into an isolated target | All 4 HTTP/state/event disclosure cases passed against the installed module |

These are focused local Windows checks, not a claim that Linux CI or CodeQL has
already passed at a new revision. GitHub reruns those checks after the push.
No alerts are dismissed manually; GitHub must analyze the fixed default branch.

## Package boundary

The source fix changes the application wheel and dependency inputs. The previous
`6f96e71` EXE cannot acquire them from source edits. The unpublished alpha draft
and its receipt explicitly require a security rebuild before distribution.
No native installer rebuild, fresh GUI installation or hardware acceptance is
claimed by this receipt. The existing artifact's historical hashes remain intact.

The local dirty-source wheel used for that installed-module smoke check was
`odessy-0.5.0.dev0-py3-none-any.whl`, SHA-256
`15befd08b645bcaf07c0ff64d10fde743ce053f27705b6ec31100e21a7857319`.
It is a test artifact, not a native release candidate or published asset.
