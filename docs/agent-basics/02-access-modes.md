# 02 — Real service-side access modes (Wave 1, slot B)

Start only after 00 READY. Read COMMON.md/contracts. Implement B05 behind frozen policy/API interfaces; do not redesign the policy system or change UI files.

Expose canonical Ask every time, Approve for me, Full control. Define what each controls, its scope, revision, default and restart behavior. Ask prompts for authorizable side effects, without asking for every harmless read. Approve for me performs the frozen real automatic policy review, records reason/action/target/scope, and escalates uncertain/high-risk/unsupported actions. Full control avoids routine prompts only within explicit grants; it does not turn off confinement, sensitive-path denials or independent permissions. Do not implement a fake automatic reviewer that always returns approve.

Use existing broker/execution_access and the actual Custom/Harness adapters; no frontend-only flag, no granting by opening a panel. Bind decisions to exact arguments/content/session and current permission revision. Revoke/downgrade safely during a running turn, invalidate affected pending decisions, and prevent historical data or reconnect from authorizing work. Clarify already-dispatched actions versus future dispatch. Unsupported modes must be capability-reported honestly, not silently mapped to a weaker/stronger mode.

Test end-to-end policy dispatch for allowed/refused actions, reviewer escalation, exact binding, stale requests, cross-session isolation, in-flight revocation, reconnect/restart and both engines' adapters. Do not equate mocked Harness protocol tests with actual installed runtime qualification. Supply shared integration edits/API registration recipes to 06; document any required runtime dependency/config change for 07.

Write doc/reports/02.md with source evidence, semantics, tests and limits. Stop without changing 01/03 files.

## Hard time limit and isolation

Hard wall-clock limit: **120 minutes**, including inspection, code, tests and handoff. Reserve the last 10 minutes for checks/reporting. At the limit stop, report unfinished requirement IDs and recovery steps; never claim incomplete work finished. Read COMMON.md branch/worktree workflow. Use your assigned isolated worktree. Scoped local task commits are authorized; no push or develop merge.
