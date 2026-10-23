# Desktop foundation boundaries

The local OpenClaw checkout separates apps, packages, extensions, security and
tests. DeepSeek Harness separates desktop, desktop-host and web apps from reusable
client/API packages. Odessy adopts the useful separation of responsibilities,
without copying their runtime or adding directories merely to resemble them.

```text
apps/desktop/                    Flutter app and native platform runners
  lib/app/                      composition, connection, session lifecycle
  lib/features/<feature>/
    controllers/                protected state and validated commands
    presentation/               teammate-owned views
  lib/widgets/                  reusable presentation primitives
  test/                         controller, widget, visual and architecture checks
packages/odessy_client/          pure Dart HTTP/SSE contracts and credentials seam
docs/ui/                        contracts, settings inventory, design and handoff
tool/                           contract checks and opt-in real API fixtures
src/odessy/application/          existing Python authority and session API
native/odessy-link/              existing Rust host security subsystem
tools/                          existing Node MCP execution host
```

Widgets → controllers → typed client → authenticated Python API. Host management
will have its own narrow typed interface; it is not arbitrary remote execution.
No provider credentials, policy engine, model loop or trust registry moves to Dart.

## Review enforcement

Architecture tests and CI are local/repository checks, not an authorization system.
The owner must configure GitHub branch protection to require the desktop check and
review before merging teammate PRs. Protected paths are listed in AGENTS.md;
no fictional CODEOWNERS username or enforced branch rule is claimed.

Teammate branches start from `origin/feat/flutter-desktop-preview`; PR base is
`feat/flutter-desktop-preview` until the owner integrates v0.5.0. Submit small
feature PRs with tests and screenshots. A missing API is a contract request, not
permission to edit host files, call providers, or invent successful live responses.
