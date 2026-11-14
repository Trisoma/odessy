# Shared execution rules

Read README.md, doc/requirements.md, doc/contracts.md, doc/ownership.md and your predecessor reports. Inspect live source and all applicable AGENTS.md. Read repository docs/setup/installer-release-and-updates-plan.md; for Dart also docs/ui/{README,api-contract,design}. At authoring HEAD was d8678e3 on feat/ui-wiring-integration with a clean tree: verify, do not assume or switch automatically. Historical parent COMMON.md is context; this pack's explicit scope supersedes old exclusions of PTY/history and stale branch/SSH descriptions.

## Scope and preservation

All user-facing copy is English. Retain Odessy's Flutter/Python/Node/Rust ownership, selectable Custom/Harness engines, supported providers/tools, boat identity, all eight settings groups, Activity & usage, Jobs, Handoffs, workspace/grants, voice lifecycle, pairing/monitoring consent and installation/recovery. Preserve settings, credentials, drafts, dirty guards and all pre-existing dirty/untracked files/deletions. Leave D:/Odessy and unrelated/PyTorch jobs untouched. Current source, not historical SSH evidence, defines available capabilities.

No commits, pushes, merges, publication, remote/trust/key edits, dependency downloads or changes to the user's existing installed app by default. Inspect and reuse cached tooling/artifacts. If a dependency truly needs downloading, document the specific necessity and obtain authorization before that download. Isolated local test/build artifacts are allowed. Installer verification must use isolated temporary profiles/directories without touching the existing installation. Never call a skipped install check passing.

Prefer reuse, then thin adaptation, then justified new code. Inspect license and version before copying any reference code; declare shipped dependencies/resources. Do not edit reference trees or rely on their absolute paths at runtime. No paid provider or hardware is needed for offline automated validation. Do not build an updater, marketplace, full IDE, remote terminal or cross-engine conversation migration.

## Concurrent writers

Ownership clarification: 03 is the exclusive Wave 1 owner of app.dart, theme and shell/conversation composition assigned in ownership.md. The shared-composition reservation below applies to unassigned foundation/client composition, not those explicitly assigned files. 06 takes those files only after 03 stops.

Every source/test/manifest/contract file has one writer. Frozen contracts and ownership below are prerequisites, not suggestions. Workers may read shared files but submit precise integration instructions for files they do not own. Only the active integrator updates shared registries/exports/manifests/locks, session.py, bootstrap.py, api.py and common Dart composition. 00 installs minimal mount points so Wave 1 can run independently. No worker runs broad formatters that write others' files. Build/output directories also need ownership: do not concurrently run Flutter builds/tests using the same generated state; record a sequential test queue in ownership.md. Worker Python tests use distinct temp profiles/ports.

Resolve a necessary contract change with the integrator; record the new revision and notify consumers. Do not invent a competing DTO. Worktrees only if the user separately chooses them; do not create branch churn to solve missing ownership. If a worker is blocked on a shared edit, finish isolated work and provide a concrete patch recipe rather than waiting silently.

## Backend invariants

Plugin scope: the existing Harness engine supports Cordis plugin composition/bridges, and Odessy supports external MCP tools. This does not make the Python application a generic plugin host. Inspect supported pinned plugin APIs before building equivalent behavior; reuse through a thin declared adapter where feasible. No plugin marketplace or wholesale backend rewrite is required. A plugin's existence in the sibling Harness snapshot is not proof that the shipped Odessy profile enables/exposes it.

The service owns persistence, authorization and execution. Dart uses typed clients/controllers; presentation never opens databases, files, sockets or subprocesses. Secure credentials stay in existing secure storage, never transcript DB/exports/logs. Use transactional migrations, versioned schemas, bounded queries/output, consistent timestamps, crash-safe writes and clear disk-full/corrupt/schema-too-new errors. Preserve redaction and sensitive-path/confinement/symlink/junction defenses.

Stored transcript, remembered facts, retrieved documents and tool output are untrusted context, never fresh authorization. Restore/reconnect/search/opening a page must not rerun a tool, resend a prompt, revive an expired grant or restart audio/processes. Distinguish reading history, preparing context and actually resuming a runtime. Engine support must be explicit; unsupported continuation is truthful read-only history, not fallback/replay. Stop does not imply rollback. Monitoring authentication, telemetry consent, workspace access, execution and microphone permissions remain independent.

Full control suppresses routine confirmations only inside explicitly granted authority. Approve for me requires a real service-side review decision with recorded rationale and fail-closed behavior; if no automatic reviewer is available, prompt the user and state why. No blanket approve-on-client. Permissions are scoped/versioned, revocable, isolated per session; tests must cover in-flight changes and both engine adapters. Hard denials remain hard denials.

## Receipts and gates

Each worker writes doc/reports/NN.md: requirement IDs, exact files, contract revision, production behavior, commands/counts, pending integration edits, failures, remaining limitations and next owner. No TODO-only feature counts as implemented. Tests should exercise persistence/restart, authorization boundaries, crash/error/race cases and real HTTP-to-Dart flow where relevant; fixture provider output must be labeled. Run affected checks before handoff; 07 owns the complete sequential integration suite.

Evidence levels stay separate: source tests, rendered UI, native runtime, package contents, isolated installation/upgrade, real hardware/provider and hosted CI. Reuse earlier evidence only when source/artifact hashes match. Human visual acceptance belongs to the user; do not require manual user work to finish source implementation, and do not claim their approval on their behalf.

## Branch/worktree workflow — supersedes conflicting earlier defaults

User-requested isolation and time limits override earlier same-checkout, no-worktree and no-commit defaults in this pack. Scoped local task/integration commits and sequential cherry-picks are authorized. No push, merge to develop, publication, reset/stash/clean or cleanup. Preserve all existing work. Recheck branches, paths and ancestry before acting.

00 creates/reuses feat/agent-basics-integration in a separate .worktrees/agent-basics-integration worktree under the parent workspace, based on the verified feat/ui-wiring-integration tip or explicitly selected successor. Do not switch the user's checkout. Record absolute paths and exact base SHA in ownership.md. Commit only owned foundation changes locally. Freeze this commit as Wave 1 base.

01/02/03 each use separate branches feat/agent-basics-01, -02, -03 and corresponding .worktrees/agent-basics-01 etc, all from that exact frozen base. Workers edit/test/commit only their own scoped files; never blindly git add -A. Each receipt records base and commit SHAs. Keep exclusive file ownership despite isolation. Pack reports outside the repository retain one writer each.

06 preflight runs in the integration worktree only after all Wave 1 workers stop. Review diffs, cherry-pick scoped commits sequentially 01,02,03, resolve conflicts without dropping features, apply integration edits and test. Freeze the resulting commit as Wave 2 base and publish READY. Then create worker branches/worktrees feat/agent-basics-04, -05, -06 and .worktrees/agent-basics-04 etc from that exact commit. 06 moves its UI work to its own worker worktree; it does not keep editing the integration checkout concurrently. 04/05 start only after the base receipt.

07 runs in the integration worktree after all Wave 2 writers stop. Review/cherry-pick 04,05,06 sequentially, resolve conflicts and run final qualification. Commit only scoped integration fixes locally. Report final SHA, dirty state and missing gates. Leave branches/worktrees intact for the user; no automatic cleanup or merge to develop.

Use unique temp profiles, databases, ports and logs. Serialize builds/tests when shared Flutter SDK caches or external resources conflict. Reuse supported caches; no automatic huge dependency copies/downloads. Ownership.md must list branch/worktree/base SHA for every worker. A dirty starting checkout requires a preservation strategy, never forced switching or lost work.

## Base branch clarification — user plans UI merge today

The user may merge feat/ui-wiring-integration into develop independently today. Do not block, perform or undo that merge. Start feat/agent-basics-integration from the verified current UI integration tip; record its exact SHA before workers branch. If the UI branch has already been merged/deleted, use the verified merge result on develop containing the UI changes. Ancestry may differ after squash merge: verify equivalent UI content rather than assuming the original SHA is an ancestor.

An in-progress worker wave stays on its frozen base even if develop changes. Do not rebase or retarget active worker branches. After workers finish and their commits are integrated, the integrator may merge updated develop into the agent-basics integration branch locally, resolving squash/overlap conflicts carefully and rerunning affected checks. No force push/history rewrite. The user later decides when to merge agent-basics into develop; agents do not perform that target-branch merge automatically. This clarification supersedes any implication that the user must keep the UI branch unmerged.
