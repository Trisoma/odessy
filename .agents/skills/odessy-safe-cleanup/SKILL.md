---
name: odessy-safe-cleanup
description: Audit and verify a bounded Odessy repository cleanup or behavior-preserving refactor, including dynamic consumers and installed packaging. Use for maintenance preparation or an explicitly authorized cleanup slice.
---

# Odessy safe cleanup

This skill supplies evidence and verification, not authorization to start another
prompt. Preparation-only requests stop after workflow validation. Never execute
Prompt 21 automatically. NO CHANGE / DEFERRED is a successful outcome when safe
removal cannot be demonstrated.

Read root and affected nested AGENTS.md, root SKILL.md, CONTRIBUTING.md and the
installer plan. The prompt pack's COMMON.md and CONTINUATION.md live in sibling
`../restructure/`; read them and the authorized prompt when applicable. Resolve
paths from the actual repository root. Historical reports are evidence to recheck,
not current source truth. Keep the current authorized branch and existing edits.

## Audit consumers before proposing deletion

Name one concrete maintenance burden and its replacement. Record each candidate's
static imports/callers, string-based lookups, lazy imports, registry/decorator
registrations, CLI entry points, subprocess launches, configuration names, external
MCP consumers, tests, docs, and installed/package resources. Use `rg` plus source
inspection; no text hits or static unused-code warning proves absence of consumers.
Check `pyproject.toml` scripts/package-data, Harness artifact/profile/plugin inputs,
Node exports/registration, platform helpers and setup bundle inventories. Generated
output, vendored code, compatibility adapters and shared installer modules require
their actual owner/consumer analysis. Do not execute unknown plugins to discover them.

## Bound the behavior-preserving slice

Capture branch/HEAD/dirty state and relevant failing/passing baselines. Define the
observable behavior and expected unchanged interfaces before editing. Prefer reuse
or relocation over adaptation, then a reference port, then new implementation only
for a demonstrated gap. Change one justified slice; compare the same meaningful
fixture/contract before and afterward. Do not weaken tests to accept lost behavior.
If consumers, replacement, or baseline cannot be established, retain the code and
report NO CHANGE / DEFERRED with the exact missing evidence and reconsideration gate.

## Preserve architecture and authority

Inspect `src/odessy/runtime_backends/selection.py`: legacy/custom and Harness remain
selectable, one engine owns a session, no silent fallback or turn replay. Harness
owns migrated generic runtime mechanics; Odessy owns policy, experience and narrow
documented extensions. Preserve pinned packaged plugins and lifecycle disposal.
Do not introduce another plugin manager or vendor the upstream engine.
Keep Python authority, Node MCP execution, Rust identity/paired transport, Go
monitoring and Flutter typed presentation boundaries. SSH login does not imply
monitoring freshness or execution grants. Preserve pairing, identity/trust, consent,
request-bound approvals, permissions/revocation, confinement and credential isolation.
Preserve voice/component opt-outs, settings, history and user-owned assets. Defer
changes outside the authorized slice, including protected surfaces without approval.

## Verify contributors and affected runtime selections

Use [the verification checklist](references/verification.md) to choose checks for
the actual slice. Verify supported setup commands and troubleshooting against source
and CI; ordinary fixtures require no paid provider, mic, GPU, VM or remote host.
Exercise both runtime selections when selection/composition is affected; fixture
success does not establish installed/native/hardware support. Trace package closure
before deleting resources; installed-behavior changes require coherent artifact
and installed evidence under existing repository instructions and authorization.

Report changed/retained consumers, commands and exit results, baseline failures,
skips, missing platform coverage and remaining gates. Keep secret values out of
logs. End with PASS for the demonstrated scope, or NO CHANGE / DEFERRED. Do not
commit, push, publish, install globally, modify trust or run the next task implicitly.
