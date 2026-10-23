# Third-Party Notices And Licenses

## Runtime libraries and model assets (October 23, 2026)

See the [third-party license inventory](docs/reference/third-party-licenses.md)
for voice, wake detection, local models, Python/Node/Rust dependencies and external
tools. Code licenses and weight licenses are separate. In particular: openWakeWord
code is Apache-2.0, pretrained Hey Jarvis weights are CC-BY-NC-SA-4.0, Kokoro weights
are Apache-2.0, kokoro-onnx is MIT, and Whisper code/weights and whisper.cpp are MIT.
The default phonemizer/eSpeak NG path includes GPL-3.0-or-later components.
LFM and Gemma use their own model terms, not Odessy's Apache-2.0 license.
This inventory does not replace a release bundle's exact transitive notices.

The following historical adaptation notices remain unchanged.

Odessy includes original code plus selected tool-handler and process-confinement code and implementation patterns adapted from permissively licensed open-source references. Odessy's runtime architecture, authority model, Python policy layer, confirmation boundary, and MCP integration remain project-specific.

Full upstream license texts are included in:

- `THIRD-PARTY-LICENSES/OpenClaw-MIT.txt`
- `THIRD-PARTY-LICENSES/NemoClaw-Apache-2.0.txt`
- `THIRD-PARTY-LICENSES/DeepSeek-Harness-MIT.txt`

## OpenClaw

Source: `C:\Users\13647\OneDrive\Desktop\odessy\openclaw\openclaw-main`

License: MIT

Copyright notice retained from upstream:

```text
Copyright (c) 2026 OpenClaw Foundation
```

License text included at:

```text
THIRD-PARTY-LICENSES/OpenClaw-MIT.txt
```

Adapted source files:

- `src/agents/sessions/tools/read.ts`
- `src/agents/sessions/tools/ls.ts`
- `src/agents/sessions/tools/find.ts`
- `src/agents/sessions/tools/grep.ts`
- `src/agents/sessions/tools/path-utils.ts`
- `src/agents/sessions/tools/truncate.ts`
- `src/agents/sessions/tools/tool-contracts.ts`

Changes made for Odessy:

- Adapted selected tool-handler code and implementation patterns for Odessy's local MCP tool server.
- Renamed capabilities to Odessy's MCP tool names.
- Removed UI rendering, media handling, session manager dependencies, and write/edit behavior.
- Routed execution through Odessy's Node MCP tool server and workspace guard.
- Preserved the read-only project-inspection behavior and structured-output style.

## NemoClaw

Source: `C:\Users\13647\OneDrive\Desktop\odessy\nemoclaw\NemoClaw-main`

License: Apache-2.0

License text included at:

```text
THIRD-PARTY-LICENSES/NemoClaw-Apache-2.0.txt
```

Adapted source files:

- `tools/advisors/repo-read-only-tools.mts`
- `tools/advisors/git.mts`
- `nemoclaw/src/security/safe-resolve-path.ts`

Changes made for Odessy:

- Adapted selected code and implementation patterns for Odessy's local single-workspace read-only tool server.
- Adapted repo-confined path checks to a local single-workspace MCP server.
- Adapted fixed-argv Git command style for local read-only Git tools.
- Removed NemoClaw sandbox lifecycle, gateway, Docker/OpenShell, and multi-agent assumptions.
- Modified files are documented in `docs/step-6-import-audit.md`.

## Hermes

Source: `C:\Users\13647\OneDrive\Desktop\odessy\hermes`

License: MIT

Reference files:

- `model_tools.py`
- `agent/tool_executor.py`
- `agent/tool_guardrails.py`
- `agent/file_safety.py`

Use in Odessy:

- Architecture and guardrail patterns only.
- No direct code copied in Step 6.

Because Hermes was used as an architectural reference only, no Hermes license text is required for copied code in Step 6. If future Odessy work copies or closely adapts Hermes code, this notice file must be updated and the relevant license text must be included.

## DeepSeek Harness (dsh)

Source: `C:\Users\13647\OneDrive\Desktop\MiMundo\GitHub\odessy\deepseek-harness-master`

Upstream repository: `deepseek-ai/deepseek-harness`

License: MIT

Copyright notice retained from upstream:

```text
Copyright (c) 2026 DeepSeek
```

License text included at:

```text
THIRD-PARTY-LICENSES/DeepSeek-Harness-MIT.txt
```

Adapted source files:

- `packages/sandbox/sandbox/src/roots.ts`
- `packages/sandbox/sandbox/src/diagnostics.ts`
- `packages/sandbox/sandbox/src/escalation.ts`
- `packages/sandbox/sandbox/src/index.ts`
- `packages/sandbox/sandbox-local/src/profiles.ts`
- `packages/sandbox/sandbox-local/src/index.ts`
- `packages/sandbox/sandbox-policy/src/index.ts`
- `packages/sandbox/sandbox-windows-acl/src/acl.ts`
- `packages/sandbox/sandbox-windows-acl/src/ffi.ts`
- `packages/sandbox/sandbox-windows-acl/src/grant.ts`
- `packages/sandbox/sandbox-windows-acl/src/index.ts`
- `packages/sandbox/sandbox-windows-acl/src/path-boundary.ts`
- `packages/sandbox/sandbox-windows-acl/src/runner.ts`
- `packages/sandbox/sandbox-windows-acl/src/spawn.ts`
- `packages/sandbox/sandbox-windows-acl/src/token.ts`
- `packages/sandbox/sandbox-windows-acl/src/win32-abi.ts`
- `packages/sandbox/sandbox-windows-acl/src/workspace-sid.ts`
- `packages/subprocess/win32-process/src/abi.ts`
- `packages/subprocess/win32-process/src/control-stdio.ts`
- `packages/subprocess/win32-process/src/errors.ts`
- `packages/subprocess/win32-process/src/ffi.ts`
- `packages/subprocess/win32-process/src/index.ts`
- `packages/subprocess/win32-process/src/koffi.ts`
- `packages/subprocess/win32-process/src/process.ts`
- `packages/util/lazy-require/src/index.ts`

Changes made for Odessy:

- Moved the same-world process-confinement contract and its backends into Odessy as
  project-owned infrastructure. There is no runtime dependency on dsh or on Cordis.
- Reimplemented the Cordis `Service` base as a Python protocol and a TypeScript interface.
- Replaced dsh's static per-platform runner table with capability-driven runtime discovery.
- Removed dsh session, invariant, and config-schema integration; Odessy has no session model.
- Removed the `dsh-subprocess/control` file-descriptor feature from the Windows runner;
  Odessy owns its own process timeout and termination.
- Removed dsh's `tsx` source-launch fallback for the Windows runner; Odessy ships built output.
- Kept the backend denial dialects, runner-failure rules, and the Windows ACL restricted-token
  implementation behaviourally unchanged.

Per-file verdicts, line counts, and the reasoning for each decision are recorded in
`docs/execution/PORT-MANIFEST.md`.
