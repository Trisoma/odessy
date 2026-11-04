# Odessy review rules

Report actionable defects introduced by the change, with a concrete trigger and
impact. Avoid speculative findings, style-only comments, and claims that a check
or test ran when it did not. Review against the current code and the documented
limitations; do not treat a documented limitation alone as a newly introduced bug.

## Authority, credentials, and execution

- Flag changes that allow tool execution, approvals, or question responses to
  bypass policy or become detached from their authoritative session, request,
  target, workspace, or permission scope.
- Check authentication and input validation at service and tool boundaries.
  Do not expose credentials, private payloads, or sensitive paths in logs or
  public responses. Preserve loopback restrictions where required.
- Check that changes to sandboxing, pairing, trust, or transport do not expand
  privileges or claim protections that the active platform cannot enforce.

## Desktop and client behavior

- Desktop presentation must use existing controllers and must not introduce
  direct network clients, provider SDKs, command execution, or trust-file edits.
- Preserve unsaved drafts, pending approvals, session identity, and existing
  navigation and shutdown guards. Selecting a device must not change the
  conversation's execution target or grant access.
- Make unavailable and demo behavior explicit; never silently substitute demo
  data for a failed live operation. Check keyboard access, accessibility, narrow
  layouts, large text, and loading, offline, error, and empty states when changed.
- Preserve authenticated client behavior, response validation, streaming,
  cancellation, and compatibility with the existing service contracts.

## Installation, tests, and CI

- Check installer and update changes for verified inputs, safe path handling,
  recovery, and preservation of existing user data.
- Flag tests that remove meaningful coverage, rely on personal credentials or
  local machine state, or omit relevant security and failure cases.
- Check workflow changes for least-privilege permissions, untrusted input,
  dependency provenance, and whether required checks still run.

Use the linked project documents for the relevant area. Escalate changes to
protected desktop foundation code for project-owner review as described in the
desktop ownership contract.
