# Third-Party Notices And Licenses

Odessy includes original code plus selected tool-handler code and implementation patterns adapted from permissively licensed open-source references. Adapted code is limited to laptop-side read-only tool-handler behavior. Odessy's runtime architecture, authority model, Python policy layer, confirmation boundary, and MCP integration remain project-specific.

Full upstream license texts are included in:

- `THIRD-PARTY-LICENSES/OpenClaw-MIT.txt`
- `THIRD-PARTY-LICENSES/NemoClaw-Apache-2.0.txt`

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
