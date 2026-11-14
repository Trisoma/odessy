# 03 — Codex-inspired production shell (Wave 1, slot C)

Start only after 00 READY. Read COMMON.md, desktop instructions, visual requirements B06–B09/B17 and contracts. Use design-taste-frontend adapted to desktop Flutter; this is not a web/framework migration.

Inspect actual production OdessyApp and user-confirmed docs/view screenshots. Distinguish standalone tastecode_demo, production --demo and richer screenshot fixtures. Record which entry point and fixture each render uses. Do not judge success by launching an old debug executable. Keep original screenshots; new screenshots get a new subdirectory and manifest.

Implement a 52–56px icon rail (Chat, Activity & usage, Devices, Jobs, Handoffs, Settings bottom) with tooltips, selected states, keyboard focus and accessible names. Adjacent independently collapsible sidebar holds New chat/search, projects/nested sessions and Recent. Retain workspace management and connection controls in logical reachable locations; preserve every existing supported destination. Make chat central with fewer duplicated headers and status rows.

Place a genuine top-right inspector open/close button, preserving Files/Output/Changes and leaving a typed seam for Terminal. Support resizable inspector with sensible bounds and persisted local preference. Narrow windows collapse/overlay rather than squeeze the chat; retain drafts/focus/dirty settings protection. Model and voice controls sit adjacent in the composer; advanced voice options move into an anchored popover with real lifecycle actions. Provide a typed mode-picker seam using 00 DTOs; don't invent backend success while 02 is still running.

Use existing controllers/fixtures against frozen contracts, not direct service calls in views. Keep running model versus next-turn model distinct, connection errors and exact approval information visible. Match structure of the Codex reference with Odessy typography/boat/navy-mint identity; all labels English.

Run assigned widget/keyboard/resizing/scaling tests and production renders at 1600x1050, 1280x800 and 720x900, light/dark where relevant, under the agreed Flutter test lock. Human acceptance remains pending. Write doc/reports/03.md, render paths and precise wiring instructions for 06. Stop; do not edit shared client/session files.

## Hard time limit and isolation

Hard wall-clock limit: **90 minutes**, including inspection, code, tests and handoff. Reserve the last 10 minutes for checks/reporting. At the limit stop, report unfinished requirement IDs and recovery steps; never claim incomplete work finished. Read COMMON.md branch/worktree workflow. Use your assigned isolated worktree. Scoped local task commits are authorized; no push or develop merge.
