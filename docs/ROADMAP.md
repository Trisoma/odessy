# Contributor roadmap

> DRAFT — status map for v0.5.0-unstable, not release commitments.
> “Tested” means the scoped observations cited below, not all combinations.

| Works today (tested) | Experimental | Planned |
|---|---|---|
| Windows 11 x64 source-preview CLI and Flutter chat with configured local/cloud models [1] | Linux x86_64 source use; CI coverage is not native packaging acceptance [1] | Completed onboarding and role-aware installer/wizard [2] |
| Policy-controlled tools with approve/reject, Stop and audit log on the recorded Windows path [1] | Jetson Orin Nano 4GB, headless/cloud recommended; local-model memory reliability unresolved [1,3] | Qualified cross-platform packages, signing and broader hardware measurements [2] |
| `python -m odessy doctor` diagnostics [1] | Voice: fake-model audio smoke and recorded-WAV checks exist; physical echo and extended wake metrics remain incomplete [3,4] | Custom Odessy wake word and GitHub Pages documentation deployment [2] |
| Historical Windows–Jetson mTLS file-read, wrong-grant/fingerprint refusal and session revocation smoke checks [4] | Split/LAN workflows as a whole; plaintext LAN executor only on trusted links [1] | Finished Go monitoring milestone, including durable-history and broader qualification work [2,5] |
| Shared interaction regressions and scoped recorded-WAV approval/refusal checks [4] | Rust telemetry → Go → Python → Dart integration checkpoint; monitoring is not supported/shipped [2,5] | Measurements on additional GPUs before any new support claim [1] |

Sources:

1. [Release notes](releases/v0.5.0-unstable-notes.md) and [README support matrix](../README.md).
2. [v0.5.0 planning](releases/v0.5.0.md); historical plans are not completion claims.
3. [Jetson qualification, October 23](qualification/2026-10-23-jetson.md).
4. [Interactions/pairing qualification, October 23](qualification/2026-10-23-interactions-and-pairing.md).
5. [Monitoring qualification ledger](monitoring/qualification.md).

No minimum hardware specification is established. macOS is untested/unsupported;
MCU control is not shipped. Voice assets need separate downloads and license review.
Historical tests do not sign off the current release commit. See
[starter drafts](contributor-issues.md) for proposed work, not opened GitHub issues.
