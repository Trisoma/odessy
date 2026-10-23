# Desktop ownership and contribution contract

Release target: v0.5.0. Base UI work on `feat/flutter-desktop-preview` until the
owner explicitly chooses integration into develop. Never merge/push automatically.

## Boundaries

- `lib/features/*/presentation/`: teammate-owned views and presentation state.
- `lib/app/`, feature `controllers/`, `../../packages/odessy_client/`: protected
  foundation. Changes require project-owner review; documentation alone is not a
  GitHub branch-protection rule.
- `lib/widgets/`, theme tokens, dependencies, platform runners and public DTOs:
  shared contract changes, reviewed before screens depend on them.
- Python owns agent execution and authorization; Rust owns host identity and secure
  transport; Node owns existing MCP execution. Never recreate these in Dart.

Views invoke typed controller commands. No HTTP clients, sockets, provider SDKs,
process execution, trust-file edits, token persistence or agent-framework imports
in presentation. Missing capabilities must stay visibly unavailable in live mode.
Mocks must be labelled and must not silently replace failed live operations.

Approval responses bind to the displayed session/request, never an editable action
summary. Questions supply information, not authorization. Model selection affects
the next turn; Stop is separate and never promises rollback.

## Definition of done for a screen PR

1. Use existing repositories/controllers or propose a typed contract addition.
2. Include empty, loading, offline, error, busy and success states where applicable.
3. Include widget tests, keyboard/focus behavior, accessible labels and screenshots.
4. Run `flutter analyze`, `flutter test`, and the contracts validator from the root.
5. Do not change source ownership, disable architecture tests or invent backend
   success. Document remaining native/platform qualification honestly.

Read `../../docs/ui/README.md`, `../../docs/ui/api-contract.md` and
`../../docs/ui/design.md` before adding screens.
