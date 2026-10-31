# Contributing to Odessy

Send small, focused pull requests to `develop`. Explain the problem, the change,
and what you verified or could not verify. Add or update tests for behavior changes;
never remove tests or weaken security assertions to make checks pass. See
[limitations](LIMITATION.md) when deciding whether code or an expectation is wrong.

Never commit secrets, API keys, private recordings or machine-specific credentials.
Redact diagnostics. Report security issues through [SECURITY.md](SECURITY.md).
Review and understand AI-assisted contributions and disclose their use in the PR.
By submitting a contribution, you confirm you have the right to submit it and
license it under the repository's [Apache-2.0 license](LICENSE).

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

## Checks

Run checks for affected components and report exact commands and results.
The main Python job runs:

```sh
python -m pytest -q -n 2 --dist loadfile --tb=short --durations=10
python -m odessy --help
python -m odessy --fake-model "what time is it?"
python -m odessy --fake-model "list ."
```

CI checks that the fake-model replies start with `The current time is ` and
`Directory entries: ` respectively, and that the directory reply includes
`pyproject.toml`. These are plumbing checks, not real-model acceptance.

From `tools/`:

```sh
npm ci --no-audit --no-fund
npm run typecheck
npm test
```

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

Live Linux confinement checks require bubblewrap, util-linux and usable
unprivileged user namespaces. See CI for runner-specific AppArmor setup. Set
`ODESSY_SANDBOX_LIVE=1`, then run at root:

```sh
python -m pytest -q tests/test_sandbox_live.py
```

From `tools/`, run `node --test dist/tests/sandbox-live.test.js`. The CI doctor
check is `python -m odessy doctor | tee doctor.txt; grep -q "read-only: bwrap (full; network cut, credentials hidden)" doctor.txt`
under Bash at root. These checks need a real Linux kernel; Windows is insufficient.

Desktop checks, at root:

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
