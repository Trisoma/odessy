# Prompt00 source-backed capability/reuse map

Observed November14 at UI base d8678e3. References are filesystem snapshots without .git; no upstream freshness claim. No reference source copied.

| Area | Source evidence | Current behavior / foundation decision |
| --- | --- | --- |
| Live sessions | application/api.py:117,805–875; session_directory.py:1–24 | Process dict, live directory only; add separate conversations API |
| Text memory | application/session.py:79,144,528,573,632,741; voice/memory.py | Text uses voice-named bounded RAM memory too; six exchanges/8000chars default, clears on close |
| UI selection | app/session_controller.dart:407; client/src/session_directory.dart:3 | resumeSession selects idle LIVE controller, not disk restoration |
| Project grouping | project_catalog_controller.dart:159,188,319,341 | Service-instance bound metadata; migrate durable association explicitly |
| Usage history | models/usage_history.py:12–60 | Existing private SQLite; preserve DB/schema |
| Monitoring history | services/monitoring/internal/history/sqlite.go | Separate telemetry store; preserve |
| Harness | runtime_backends/contracts.py:194–207; harness/runtime.py:324 | Current adapter no durable-history/resume; new ACP session only |
| Pin | harness/pin.py:28–30 | 0.2.1-rc.2, source 687f282e8625c6b8d11ff58211a90524be2e4317, not sibling master |
| Approval | session.py:363; runtime/agent_loop.py:1237; authority/action_approval.py:69 | Broker + exact content/policy binding; mode review must preserve it |
| Scope | authority/policy.py:204,697–735; execution_access.py:1–15 | Policy/hook/confinement owner; LAN credentials are separate, not a generic session mode |
| Inspector | workspace_inspector_controller.dart:5,25; application/workspace_inspector.py | terminal enum currently means recorded output; add distinct interactive service |
| Production launch | apps/desktop/lib/main.dart:15,68–70 | Ordinary DisconnectedApi/config restore, --demo injects DemoApi; standalone tastecode_demo not accepted |
| Local launcher | application/local_desktop.py; tool/build_local_desktop.ps1 | Isolated checkout-local profile and build defines; don't confuse installed/debug launch |
| Packaging | installer/components/application.py; tool/build_setup_bundle.py; tool/package_native_setup.py | Existing source/helper inventories, admitted source formats .py/.json/.toml/.txt/.md; use Python migrations |
| TasteCode | apps/server/src/store.ts,terminal.ts,checkpoint.ts; adapter-claude-code/src/permission-mode.ts | SQLite/history, PTY and temporary Git-index/safety snapshots are behavior references; no whole server copy |
| Harness master | packages/session/session-persistence-jsonl/src/{index,storage}.ts; session-checkpoint-policy/src/index.ts; session-query/tool-session-query; compaction | Rich runtime plugins exist there, not automatically connected to pinned Odessy adapter |

Licenses checked: TasteCode LICENSE Apache-2.0; Harness LICENSE MIT; Odessy Apache-2.0. Existing THIRD-PARTY-LICENSES/DeepSeek-Harness-MIT.txt present. A future port/dependency must preserve required notices and exact source identity.

Plugin distinctions: Cordis runtime composition and Odessy stream bridge, external MCP tools, and ordinary Python application modules are different extension boundaries. No generic Python plugin loader/marketplace is introduced. Reuse supported pinned APIs first; no undeclared sibling-directory dependency.

Known PTY gate: TasteCode package.json pins node-pty1.2.0-beta.15; no corresponding cached module/ConPTY artifact found in inspected reference or Odessy tool node_modules. tools/package.json has MCP SDK,koffi,zod. Koffi is not a verified PTY. No dependency install/download was made;05 must explicitly resolve or report B13 BLOCKED. This does not prevent Wave1 foundation work.
