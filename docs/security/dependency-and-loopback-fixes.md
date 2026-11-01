# Dependency and loopback fixes

Branch: `fix/security-dependencies-and-loopback`, based on `origin/develop` at
`cc3ee94`. Created in the existing checkout, without a worktree.

## Dependency advisories

The initial production npm audit reported nine vulnerable packages: one critical,
four high and four moderate. The patched lockfile and clean Windows installation
both report **zero vulnerabilities**.

| Package | Patched locked version |
|---|---|
| `@modelcontextprotocol/sdk` | 1.32.2 |
| `zod` | 4.6.7 |
| `proxy-addr` | 2.0.8 |
| `body-parser` | 2.4.0 |
| `fast-uri` | 3.1.9 |
| `ip-address` | 10.7.5 |
| `hono` | 4.13.16 |
| `qs` | 6.17.1 |
| `@hono/node-server` | 2.1.5 |

The direct dependency minimums are now MCP SDK 1.32.2 and Zod 4.6.7. Zod is a
major upgrade: the existing tool-schema tests, TypeScript checks and real MCP
integration tests passed. A regression verifies that oversized `hidden_paths`
and `extra_writable` arrays produce one size error instead of accumulating an
error for every invalid element. No `--force` upgrade or advisory suppression
was used.

References: [Zod advisory](https://github.com/advisories/GHSA-mrhx-c7xc-m3w5)
and [Zod migration guide](https://zod.dev/v4/changelog).

## CodeQL alert #11

The downloaded SARIF trace starts at an implicit empty literal in the endpoint
URL f-string in `installer/components/ollama.py`, then propagates through the
parsed URL into the socket-address tuple, including its port. The endpoint was
already restricted to loopback; the previous hostname fallback did not remove
that source of the scanner finding.

The URL now uses equivalent explicit nonempty string components. The probe also
independently validates a literal loopback IP and constructs its numeric bind
host from packed address bytes. Tests retain `127.0.0.2` and IPv6 `::1` support
and reject missing, wildcard, external and unresolved hostname inputs. No scan
suppression or network exposure was added.

## Verification

```text
cd tools
npm audit --omit=dev
found 0 vulnerabilities
npm run typecheck
tsc --noEmit (exit 0)
npm test
207 tests; 202 passed; 5 skipped; 0 failed

cd ..
python -m pytest -q tests/test_installer_ollama.py tests/test_installer_ollama_runtime.py --tb=short
38 passed, 39 subtests passed in 4.80s

# Python 3.13.15; ODESSY_LINK_E2E=1
python -m pytest -q tests/test_paired_transport.py tests/test_paired_integration.py tests/test_paired_agent.py tests/test_paired_host.py tests/test_node_tool_server_loop.py tests/test_terminal_executor_integration.py tests/test_security_logging.py --tb=short
41 passed in 91.60s

python tool/check_desktop_contracts.py
Validated 107 runtime settings; unavailable controls do not count as implemented.

git diff --check
exit 0 (only Git line-ending conversion warnings)
```

Raw before/after audits and the original Python SARIF are saved locally under
ignored `build/security-audit/`. The original Windows dependency installation hit
an EPERM error; it was preserved under ignored build output and replaced with a
successful clean install from the patched lockfile.

Not verified: Linux, the full repository test suite, and a fresh CodeQL scan of
this branch. GitHub alert closure requires the updated files to be pushed and
scanned; local audit success is not proof that GitHub has closed its alerts.
