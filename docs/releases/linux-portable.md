# Odessy Linux portable preview — v0.5.0-unstable

Status: packaging/launcher implementation; no accepted Linux release artifact yet.
The current development machine has Ubuntu 24.04 x86_64 under WSL2, but no native
Linux Flutter SDK, GTK development files, Rust/Go or Linux Node runtime available.
SDK download attempts failed. This does not establish Linux or ARM64 acceptance.

## Testers: run an accepted archive

Download the archive matching your CPU (`x86_64` or `arm64`) and its `.sha256` file.
Linux uses a native executable, not a Windows `.exe`. Do not run this preview as
root. A graphical Linux desktop with GTK 3 and a supported graphics stack is
required; this archive does not bundle a Linux distribution or glibc.

```bash
sha256sum -c odessy-0.5.0.dev0-linux-x86_64.tar.gz.sha256
tar -xzf odessy-0.5.0.dev0-linux-x86_64.tar.gz
cd odessy-0.5.0.dev0-linux-x86_64
./Odessy --verify
./Odessy --diagnose
./Odessy
```

The launcher uses the bundled Python/Node/Rust/Go components. Testers do not need
Flutter, compilers, npm or a system Python. Start with an existing local Ollama
model: **Get started → On this computer → Find installed models**, select the
exact existing model name, select a workspace, then save and continue. No model
is pulled and no inference is performed during configuration/discovery. Sending
an actual prompt deliberately uses the chosen model.

The bundled Node host still applies existing tool policy, workspace authority,
confirmation and available host sandbox checks. Linux confinement availability
must be checked on the target kernel; a portable archive does not itself create
an isolation backend. Do not interpret pairing or telemetry grants as permission
to execute commands.

Configuration stays outside the archive at
`${XDG_STATE_HOME:-$HOME/.local/state}/odessy/portable/configuration.json`.
An explicit absolute `ODESSY_PORTABLE_STATE` can select another dedicated external
state directory. Workspaces, models, credentials and Rust trust remain separate.
Moving the extracted folder does not change model selection. Only credential
environment-variable references are saved; actual keys must be supplied separately.
Voice models/dependencies are not included by default.

The **Monitor** page uses the existing API. Monitoring is opt-in: before launching,
set `ODESSY_MONITORING=on` and `ODESSY_MONITOR_LOCAL=on` for local collection.
Initialize identity and enroll/grant remote telemetry through existing reviewed
pairing procedures; the launcher never creates trust or executes an inference
probe. See [monitoring](../monitoring/README.md) in the source repository.

Close the application to stop its owned coordinator; there is no system service,
installer or automatic updater. To remove binaries, close the application and
remove the extracted folder. External settings, models, workspaces and credentials
are preserved. To update, verify and extract a new archive rather than merging
files into the old bundle.

If execution fails: check CPU architecture, archive checksum/executable bits and
GTK runtime availability. Run `./Odessy --diagnose` from a terminal; no model is
started by diagnosis. A `noexec` mount requires extracting to an executable mount.
Missing display, libraries, model endpoint or monitoring is an error, not demo mode.

## Maintainers: native build

Build each architecture natively. Initial intended baseline is Ubuntu 24.04 x64;
Ubuntu 22.04/Jetson/macOS/ARM64 are not qualified by a 24.04 x64 build. Record the
actual glibc/native dependency baseline and test the clean target distribution.

Inputs must be independently fetched/verified before invoking the builder:

- Relocatable CPython distribution with pip/setuptools and applicable license texts.
- Native Node distribution including npm and its license texts.
- Native Flutter SDK; Rust/Cargo, Go, CMake, Ninja, Clang and GTK 3 development files.
- Offline Python wheelhouse satisfying `odessy[ui,cloud]` and build dependencies.
- Reviewed notices directory containing license texts plus `inventory.json`.

```bash
bash tool/linux/build_portable.sh \
  --python-runtime /absolute/verified-python \
  --node-runtime /absolute/verified-node \
  --wheelhouse /absolute/linux-wheelhouse \
  --notices /absolute/reviewed-notices \
  --flutter /absolute/flutter/bin/flutter \
  --output /absolute/artifacts/odessy-0.5.0.dev0-linux-x86_64.tar.gz
```

The builder stages the supplied runtime, installs Python wheels offline, builds
Flutter/Node/Rust/Go from current source, imports the Python API outside the
checkout, and creates a `.tar.gz`, per-file manifest and external SHA-256 file.
Node npm and native build tools may fetch their build dependencies; runtime and
model downloads are not performed. Temporary build output is invocation-owned.
Existing output is never overwritten. Source checkout changes are not reset.

The notices inventory has schema:

```json
{"schema_version":1,"components":[
  {"name":"python","version":"actual-version","license":"actual-license",
   "source":"upstream-source-url","notice":"Python-LICENSE.txt"}
]}
```

Required top-level names: `python`, `desktop`, `node`, `tools`, `link`, `monitor`.
Add every redistributed dependency and applicable notice, including Python wheels,
Node modules, Rust crates, Go modules/SQLite, Flutter engine/plugins and transitive
native components. The assembler checks inventory structure/file existence; it
cannot decide legal completeness. Do not use placeholder entries in a release.

## Acceptance before publication

1. Native build succeeds and `bash tool/linux/check_portable.sh <archive>` passes.
2. Extract outside the checkout with no development tools on PATH; verify, launch
   and configure an existing LLM through the actual Flutter UI.
3. Deliberately send text; complete one policy-controlled tool call; verify both
   approval and rejection/cancellation. Startup must not silently use a fake model.
4. Test monitoring, identity/permission failures, disconnect/reconnect and history.
5. Close/reopen/move the archive and check saved configuration and child cleanup.
6. Review native dependencies, notices and checksums; record screenshots and hashes.

Native WSL packaging/configuration regressions passed on October 26; no native
Flutter artifact was built. Synthetic packaging tests and WSL startup/configuration tests are not substitutes
for the native Flutter/model/tool acceptance above. No ARM64 qualification follows
from x86_64 checks. Signing and physical audio remain separate release gates.

## October 26 implementation evidence

- Portable review/state/relocation, archive hashes/modes, architecture rejection,
  internal symlink verification, private-key rejection and public CA retention
  have regression coverage. Independent review corrections were incorporated.
- 70 affected Python configuration/portable tests passed; 18 portable/installed
  bridge tests passed (overlapping coverage, not additive). The existing real
  Windows Node/MCP integration passed separately after process access was allowed.
- 21 Flutter installed/controller/architecture checks passed; scoped Dart analysis
  was clean before final documentation. No Linux Flutter compile is claimed.
- Native Ubuntu 24.04 x86_64 WSL2 shell syntax/Python checks and portable unit tests
  ran. Native build preflight stopped at missing Cargo; Flutter Linux, Go, Node
  and GTK development prerequisites also remain unavailable. Flutter SDK endpoint
  attempts returned 404 or timed out. No system package installation was attempted.
- **Artifact path: none.** No tester-ready Linux archive was fabricated from stub
  test executables. Full GUI/existing-model/approved-tool/cleanup acceptance is
  blocked until native build inputs are supplied; ARM64 is unqualified.
