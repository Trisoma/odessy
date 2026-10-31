# Contributing to Odessy

Welcome aboard! ⛵ Thank you for helping improve Odessy. Bug reports, focused fixes,
and clearer documentation all help make this developer preview easier to use.
This guide explains where to start, how to set up your environment, and what to
include when submitting a change.

For an overview of the project, start with the [README](README.md). Architecture
and component documentation live in [docs/](docs/).

## Choose where to start

| Contribution | How to get started |
|---|---|
| Bug fixes and small documentation fixes | Open a focused pull request directly. Link an existing issue when relevant. |
| New features, enhancements, or larger changes | Open an issue first to explain the problem and proposed approach before starting implementation. |
| Refactor-only or CI changes | Please do not open standalone pull requests for these changes. |
| Security vulnerabilities | Follow [SECURITY.md](SECURITY.md). Do not disclose vulnerabilities in public issues. |

You are welcome to report a problem even if you cannot implement a fix. Let us
know whether you plan to work on it so others can coordinate with you.

## Before opening an issue

Search existing issues and pull requests, including closed and merged ones, to
avoid duplicate work. Check the relevant source and [known limitations](LIMITATION.md)
for existing behavior and constraints. If a PR already addresses the problem,
consider helping review or improve it.

Keep each issue focused on one problem. For a bug report, include:

- The version or commit, operating system, architecture, and model backend.
- Steps to reproduce the problem, including the commands you ran.
- Expected and actual behavior, with redacted diagnostics where useful.

For a feature proposal, describe the use case, the desired behavior, and any
alternatives you considered. For larger work, comment on the issue before starting
so contributors can avoid overlapping efforts.

## Prepare a pull request

Target `develop` and keep the change small enough to review. Separate unrelated
fixes, formatting changes, and dependency updates.

Before requesting review:

- Explain the problem and how the change addresses it. Link related issues.
- Add or update tests for behavior changes. Never remove tests or weaken security
  assertions just to make checks pass. Use [LIMITATION.md](LIMITATION.md) to help
  determine whether the implementation or a test expectation needs correcting.
- Update documentation when setup, configuration, or user-visible behavior changes.
- List the exact checks you ran and their results, including failures, skipped
  checks, and any validation you could not perform.
- Remove secrets and private data from code, fixtures, logs, and screenshots.

## Changelog

For user-visible changes, add a short entry under **Unreleased** in
[CHANGELOG.md](CHANGELOG.md). Describe the effect on users; omit internal cleanup
and typo-only edits. Maintainers group entries under a version and release date
when preparing a release. Keep detailed qualification evidence and known
limitations in the release notes under `docs/releases/`.

## AI-assisted contributions

AI-assisted contributions are welcome; our team uses these tools too. Please
state in your PR when you used AI assistance and describe what you reviewed and
verified yourself. You remain responsible for understanding the submitted code,
checking its behavior, and confirming that you have the right to contribute it.

## Secrets and security

Never commit API keys, tokens, private recordings, or machine-specific credentials.
Redact sensitive values and personal information before sharing diagnostics.
If you discover a vulnerability, use the private reporting instructions in
[SECURITY.md](SECURITY.md).

## Development setup

Commands below come from [CI](.github/workflows/ci.yml) and
[desktop preview CI](.github/workflows/desktop-preview.yml). CI covers Python
3.10/3.13 on Ubuntu and 3.12/3.13 on Windows, Node 22/24 on both,
stable Rust, and Flutter 3.47.7 (stable) on Ubuntu. This coverage does not
establish hardware, macOS or live-provider support.

Create and activate a virtual environment with Python 3.10-3.13:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

On Linux, activate with `source .venv/bin/activate`. Use Node 22 for the Python
integration checks and stable Rust for pairing work. From the repository root:

```powershell
cd tools
npm ci --no-audit --no-fund
npm run build
cd ..
python -m pip install -e ".[test,ui]" "pytest-xdist>=3.6,<4"
$env:ODESSY_CONFIG_FILE="$PWD/.ci-no-config.env"
$env:PYTHONUTF8="1"
```

On Linux, use `export ODESSY_CONFIG_FILE="$PWD/.ci-no-config.env"` and
`export PYTHONUTF8=1` instead of the PowerShell environment assignments.

## Run the relevant checks

Run the checks for the components you changed and include the exact commands and
results in your PR. State which checks you could not run and why.

### Python runtime

From the repository root:

```sh
python -m pytest -q -n 2 --dist loadfile --tb=short --durations=10
python -m odessy --help
python -m odessy --fake-model "what time is it?"
python -m odessy --fake-model "list ."
```

CI checks that the fake-model replies start with `The current time is ` and
`Directory entries: ` respectively, and that the directory reply includes
`pyproject.toml`. These are plumbing checks, not real-model acceptance.

### Node tool server

From `tools/`:

```sh
npm ci --no-audit --no-fund
npm run typecheck
npm test
```

### Rust transport and pairing

From `native/odessy-link/`:

```sh
cargo test --locked
cargo clippy --all-targets -- -D warnings
cargo build --locked
```

For paired integration, build Node and Rust first, set `ODESSY_LINK_E2E=1`
(PowerShell: `$env:ODESSY_LINK_E2E="1"`), then run at root:

```sh
python -m pytest -q tests/test_paired_transport.py tests/test_paired_integration.py tests/test_paired_agent.py tests/test_paired_host.py
```

### Live Linux confinement

These checks require bubblewrap, util-linux and usable
unprivileged user namespaces. See CI for runner-specific AppArmor setup. Set
`ODESSY_SANDBOX_LIVE=1`, then run at root:

```sh
python -m pytest -q tests/test_sandbox_live.py
```

From `tools/`, run `node --test dist/tests/sandbox-live.test.js`. The CI doctor
check is `python -m odessy doctor | tee doctor.txt; grep -q "read-only: bwrap (full; network cut, credentials hidden)" doctor.txt`
under Bash at root. These checks need a real Linux kernel; Windows is insufficient.

### Desktop and Dart client

From the repository root:

```sh
python tool/check_desktop_contracts.py
python -m unittest discover -s tests -p test_desktop_settings_inventory.py -v
```

From `packages/odessy_client/`, with Flutter 3.47.7's Dart SDK:

```sh
dart pub get
dart format --output=none --set-exit-if-changed lib test
dart analyze
dart test
```

From `apps/desktop/`:

```sh
flutter pub get
dart format --output=none --set-exit-if-changed lib test
flutter analyze
flutter test
```

Hardware, physical audio, real-network pairing and live-provider checks are
separate acceptance work. State explicitly when they were not run.

## Contribution license

By submitting a contribution, you confirm that you have the right to submit it
and agree to license it under the project's [Apache-2.0 license](LICENSE).
