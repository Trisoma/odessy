# Changelog

User-visible changes are recorded here, starting with the next release.

## Unreleased — v0.5.0-alpha (Developer Preview)

- Update the pinned OpenTelemetry API to 1.45.2 and Node HTTP adapter to 2.1.6 for denial-of-service and request-path security fixes.
- Keep backend exception text out of desktop audio errors while preserving device-selection guidance.
- Make fake Harness protocol tests independent of runner sandbox capabilities; required production isolation still refuses unsupported boundaries.
- Consolidate the production desktop with project/recent navigation, preserved Activity & usage, Jobs/Handoffs, inspector panels and local appearance preferences.
- Add durable conversation/project history, search/export and bounded Custom restart continuation; Harness restart history remains read-only.
- Add service-owned Custom access modes, explicit project memory and opt-in lexical document retrieval.
- Add conditional local Windows PTY integration and Git text checkpoints.
- Disable checkpoint restoration on Windows at the service/write boundary pending directory-handle safety; retain creation/list/preview and show the restriction in the desktop.
- Refresh user/developer documentation and distinguish current capabilities from historical release/qualification evidence.

- Add temporary SSH connections, read-only monitoring and separately selected, policy-gated Custom SSH commands; remote workspaces and remote PTY remain unavailable.
- Offer Piper or Kokoro during Windows setup; verify the installed runtime/model before automatic Piper activation and rebind managed Piper safely during repair/upgrade.
- Fix deeply nested Windows installer extraction and retain system/profile launch variables required by OpenSSH.
- Update the illustrated manual and essential English, Chinese and Spanish guides for the draft installer.
- Keep Harness available after repair and upgrade, and reject incomplete Windows installers.
- Prevent repeated streamed voice replies and reset timing correctly for follow-up recordings.
- Preserve unsaved API keys on status refresh and wait for credential saves before leaving setup.
