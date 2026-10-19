# Execution Provider Port Manifest

## Purpose

Odessy's ExecutionProvider layer is built by moving and adapting process-confinement code
from the DeepSeek Harness (`dsh`) rather than depending on it at runtime. This document
records, for every component considered, whether it was reused, adapted, reimplemented
from scratch, or deliberately excluded, and why.

The target outcome is **dsh-inspired, Odessy-owned infrastructure**: the design ideas and
the proven backend implementations are carried over, but Odessy has no runtime dependency
on `dsh` or on Cordis.

## Upstream

Source checkout: `deepseek-harness-master` (repository `deepseek-ai/deepseek-harness`).

Packages examined:

```text
@deepseek-ai/dsh-sandbox
@deepseek-ai/dsh-sandbox-local
@deepseek-ai/dsh-sandbox-policy
@deepseek-ai/dsh-sandbox-windows-acl
@deepseek-ai/dsh-win32-process
@deepseek-ai/dsh-lazy-require
@deepseek-ai/dsh-util-values
```

License: **MIT**. Verified on 2026-10-07 by reading each package's `package.json` in the
checkout (not the npm registry metadata, which reports `BSD-3-Clause` at the packument
top level for some packages and is stale). No per-package `LICENSE` files exist; the
repository-root `LICENSE` applies. Copyright notice: `Copyright (c) 2026 DeepSeek`.

Full license text: `THIRD-PARTY-LICENSES/DeepSeek-Harness-MIT.txt`.

## Verdict Vocabulary

```text
REUSE         Moved with no semantic change. Only mechanical edits: import paths,
              module system, or removal of an unused parameter.

ADAPT         Moved and changed to fit Odessy's architecture. Behaviour is preserved;
              the surrounding integration is rewritten.

REIMPLEMENT   The behaviour is kept, the code is written fresh. Used where the source is
              in another language, or where Odessy's design deliberately diverges.

EXCLUDE       Deliberately not moved. Recorded here so the omission is a decision rather
              than an oversight.
```

Preference order, per the project decision: `REUSE > ADAPT > REIMPLEMENT > EXCLUDE`.

## Summary

Line counts are from the checkout on 2026-10-07 and cover source files only
(`src/**`, tests excluded).

| Package | Lines | Verdict for the package | Notes |
|---|---:|---|---|
| `sandbox/src` | 489 | REUSE (types) + REIMPLEMENT (pure helpers in Python) + REIMPLEMENT (service class) | The vocabulary moves verbatim as shared schema; the pure helpers keep their behaviour but are written fresh in Python, since the source is TypeScript. The Cordis `Service` base does not exist in Odessy. |
| `sandbox-local/src` | 596 | REUSE (bwrap, seatbelt profile builders) + REIMPLEMENT (landlock grants) + ADAPT (provider) | The provider's runner-selection table is replaced by Odessy's capability-driven probe. |
| `sandbox-policy/src` | 254 | ADAPT (precedence logic) + EXCLUDE (session, invariant) | Odessy has no session model. |
| `sandbox-windows-acl/src` | 1732 | REUSE (6 files) + ADAPT (4 files) | The only backend requiring native code. |
| `win32-process/src` | 1101 | REUSE | Its sole dsh import is `lazy-require`. |
| `lazy-require/src` | 22 | REUSE | One file, imports only `node:module`. |
| `util/values/src` | 221 | REUSE (one function) | Only `assertNever` is used. |
| **Total examined** | **4415** | | |
| **Moved as-is** | **~2855** | | `win32-process` + `sandbox-windows-acl` + `lazy-require`. |
| **Ported to Python** | **~324** | | `roots` + `diagnostics` + `escalation`. |
| **Excluded** | **88** | | `invariant` + `session-mode`. |

## `sandbox/src` — 489 lines

| File | Lines | Verdict | Reason |
|---|---:|---|---|
| `roots.ts` | 52 | **REUSE** (TypeScript) / **REIMPLEMENT** (Python) | Zero external imports; `canonicalPath` and `writableRoots` are pure. The TypeScript side moves it as-is. The Python side cannot delegate to `os.path.realpath`: on 2026-10-07 that was **found empirically** to collapse `..` lexically *before* resolving a preceding symlink, so `link/..` resolved to the link's own parent and silently widened a writable root by one level. `execution/roots.py` therefore resolves component by component, matching Node's *native* realpath — which is exactly why upstream used the native call. `tests/test_execution_roots.py` pins the behaviour. |
| `diagnostics.ts` | 94 | **REUSE** (TypeScript) / **REIMPLEMENT** (Python) | Zero external imports. The runner-failure and denial classification *behaviour* is shared, but the error shapes are language-specific: Node classifies `EACCES`/`ENOENT` spawn rejections, Python classifies `FileNotFoundError`/`PermissionError`. |
| `escalation.ts` | 187 | **REUSE** (TypeScript) / **REIMPLEMENT** (Python) | Its only import is `assertNever`, a one-line helper, inlined. The ladder, the argument-pairing validation, and every user-facing error text are preserved verbatim in `execution/escalation.py`. |
| `index.ts` — type definitions | ~90 | **REUSE** | `SandboxMode`, `ConfinedSandboxMode`, `SandboxExecutionPolicy`, `SandboxPolicy`, `SandboxEnforcement`, `RunnerFailureRule`, `ConfinedArgv` carry no dsh dependency. They become the Odessy contract vocabulary. |
| `index.ts` — `SandboxProvider` class | ~75 | **REIMPLEMENT** | `abstract class SandboxProvider extends Service` with `HarnessError` and `SessionId` is Cordis-specific. Odessy expresses the same contract as a Python `Protocol` and a TypeScript interface. The *contract*, including the fail-closed rule and the prohibition on silent unconfined passthrough, is preserved verbatim. |

## `sandbox-local/src` — 596 lines

| File | Lines | Verdict | Reason |
|---|---:|---|---|
| `profiles.ts` — `bwrapProfileArgs` | ~20 | **REUSE** | Pure argument construction. Moved verbatim. |
| `profiles.ts` — `seatbeltProfileArgs` | ~25 | **REUSE** | Pure argument construction, including the SBPL string escaping. Moved verbatim. |
| `profiles.ts` — `landlockProfileArgs` | ~8 | **REIMPLEMENT** | Depends on `grantArgs` from `dsh-node-addon-system`. The emitted flag vocabulary (`--ro`, `--rw`) is documented by the launcher, so Odessy emits it directly instead of taking the dependency. |
| `index.ts` | 543 | **ADAPT** | The valuable content is the runner chain, the functional probes, the verdict caching, the denial dialects, the runner-failure rules, and the Windows ACL grant lifecycle. The Cordis `Service` base, the `schemastery` config schema, the `SessionId` keying, and the static `PLATFORM_CHAINS` table are replaced. See "Deliberate Divergence" below. |

## `sandbox-policy/src` — 254 lines

| File | Lines | Verdict | Reason |
|---|---:|---|---|
| `index.ts` | 166 | **ADAPT** | Only the mode-precedence idea is carried over: explicit request mode, else a configured override, else a deployment default; plus the requirement that the workspace root be an absolute path. The `Session`/`SessionProjection` integration and the `schemastery` config schema are replaced by Odessy's own `OdessyPolicy` resolution. |
| `session-mode.ts` | 51 | **EXCLUDE** | Persists the mode as a `sandbox/mode` event on a dsh `Session`. Odessy has no session event model; mode resolution is per-invocation plus configuration. |
| `invariant.ts` | 37 | **EXCLUDE** | Installs a dsh-invariants companion for the session projection. Odessy has no invariant framework. |

## `sandbox-windows-acl/src` — 1732 lines

This is the only backend that cannot be reimplemented practically, because it is a
substantial piece of Win32 restricted-token and ACL work bound through `koffi`.

| File | Lines | Verdict | Reason |
|---|---:|---|---|
| `token.ts` | 213 | **REUSE** | `CreateRestrictedToken` assembly, the restricting-SID lists per mode, logon-SID discovery, and the token default-DACL grant. No dsh imports. |
| `acl.ts` | 259 | **REUSE** | DACL read/merge/apply, the exact-ACE skip, the per-path `LockFileEx` lock, and the Modify ACE mask. No dsh imports. |
| `grant.ts` | 97 | **REUSE** | `AclWriteGrant` lifecycle. No dsh imports. |
| `win32-abi.ts` | 97 | **REUSE** | Struct sizes, offsets, and constants. No dsh imports. |
| `workspace-sid.ts` | 51 | **REUSE** | Deterministic workspace write SID and domain-separated temp SID derivation. No dsh imports. |
| `path-boundary.ts` | 36 | **REUSE** | `assertTempRootOutsideWorkspace` and `assertPrivateTempDisjoint`. No dsh imports. |
| `runner.ts` | 214 | **ADAPT** | The runner argv contract, the exit-127 failure dialect, the stdio inheritance, and the exit-code mirroring are preserved exactly. Removed: the `dsh-subprocess/control` file-descriptor feature (2 constants, 3 lines), which Odessy does not use because it owns its own process termination. After this removal `runner.ts` has **zero** dsh imports. |
| `index.ts` | 422 | **ADAPT** | `AclSandbox` and the grant-lifecycle logic are kept. Changed: `Win32Error` from `dsh-win32-process` becomes the vendored equivalent. |
| `ffi.ts` | 289 | **ADAPT** | The koffi binding table is kept whole. Changed: `createLazyRequire` and the shared `Win32ProcessBindings` come from the vendored `win32-process` package. |
| `spawn.ts` | 54 | **ADAPT** | Changed: `drainPipe` and the process-creation bindings come from the vendored package. |

## `win32-process/src` — 1101 lines

| File | Lines | Verdict | Reason |
|---|---:|---|---|
| `ffi.ts` | 352 | **REUSE** | The shared koffi binding surface. |
| `process.ts` | 602 | **REUSE** | Process creation, pipe draining, exit handling. |
| `abi.ts` | 47 | **REUSE** | Win32 constants. |
| `control-stdio.ts` | 43 | **REUSE** | stdio handling. |
| `errors.ts` | 13 | **REUSE** | `Win32Error`. |
| `index.ts` | 37 | **REUSE** | Package barrel. |
| `koffi.ts` | 7 | **REUSE** | Its only import is `dsh-lazy-require`. |

## `lazy-require/src` — 22 lines

**REUSE.** A single file whose only import is `node:module`. Moved with attribution. It
exists to defer the `koffi` native load until the Windows runner actually runs.

## `util/values/src` — 221 lines

**REUSE (one function).** Only `assertNever` is used, by `escalation.ts`. It is a
one-line exhaustiveness helper and is inlined rather than moving the package.

## Explicitly Not Moved

| Item | Reason |
|---|---|
| `sandbox-policy/src/session-mode.ts`, `invariant.ts` | Session and invariant frameworks that Odessy does not have. |
| `native/system` (the `landlock-run` launcher, C) | A prebuilt static binary distributed as platform npm packages. Odessy **probes for the binary** and reports its absence honestly rather than vendoring or compiling C. |
| `native/system` `flock` Node-API addon | Only needed by dsh's file-locking paths, which Odessy does not use. |
| Cordis (`@deepseek-ai/cordis`), `schemastery` | Dependency-injection and config-schema frameworks. Odessy's provider registry is a plain Python/TypeScript registry. |
| `dsh-llm`, `dsh-session`, `dsh-invariants`, `dsh-session-projection` | dsh runtime frameworks. The only thing Odessy needs from them is an error base class, which it defines itself. |
| `dsh-subprocess` | dsh's execution seam, which sits *above* the sandbox in dsh's layering. Odessy's executors already exist. Note this package confirms dsh separates execution from confinement; Odessy makes the same separation. |
| `dsh-output-retention`, `dsh-util-workspace-path`, `dsh-timeout` | Out of scope for this port. Recorded as a follow-up comparison opportunity, since Odessy implemented its own output retention and path confinement in v0.2.0. |

## Deliberate Divergence: Provider Selection

dsh selects a sandbox runner from a static table keyed by platform:

```text
linux  -> bwrap, then landlock
darwin -> seatbelt
win32  -> windows-acl
```

A sole candidate is selected without a probe; competing candidates are functionally
probed once in chain order; the first usable verdict is cached for the provider's lifetime.

**Odessy replaces this with capability-driven discovery.** A probe determines what the host
actually offers — operating system, kernel, Landlock ABI, bwrap usability (four distinct
states), available binaries, and permission state — and selection satisfies the *policy
request* rather than the *platform name*. Selection filters candidates by whether they
satisfy the request; when the request requires isolation and nothing satisfies it, Odessy
denies rather than selecting a weaker provider.

The trade-off is recorded honestly: dsh's table is simple and predictable, while discovery
can surprise. Odessy mitigates this by making the probe report always user-visible and by
caching the verdict per process.

## Verified Constants

The plan that preceded this port carried a Landlock ABI table explicitly marked as
unverified. It was verified **before** being encoded, against the kernel header as
quoted in the launcher's C source (`native/system/packages/entry/src/main.c`),
which labels each access bit with the ABI that introduced it:

```text
LL_FS_REFER       (1 << 13)   ABI 2
LL_FS_TRUNCATE    (1 << 14)   ABI 3   (ABI 4 added TCP bits only)
LL_FS_IOCTL_DEV   (1 << 15)   ABI 5
LL_ABI1_MASK      = LL_FS_REFER - 1   (bits 0..12)
MAX_ABI           = 5
```

The plan's table was correct. Two details it did not carry, both of which would
have produced wrong code:

- **ABI 4 added no filesystem right.** It added the TCP network bits only, so
  filesystem governance does not change between ABI 3 and ABI 4. A limitation set
  derived from "ABI 4 is newer than ABI 3" would have been wrong.
- **`__NR_landlock_create_ruleset` is 444 on every architecture**, per the
  post-2011 unified syscall table. No per-architecture table is needed, which is
  why `execution/probe.py` can query the ABI with one syscall number.

The mapping lives in `execution/providers/landlock.py` as three named ABI
constants and is pinned across ABIs 1 to 5 by `tests/test_execution_providers.py`.

Two further facts were taken from the same source and shape the implementation:

- **`no_new_privs` is mandatory** for an unprivileged Landlock restrict, and it
  also neutralises setuid/setgid escalation *inside* the sandbox. The launcher
  sets it; Odessy relies on that rather than repeating it.
- **The launcher's own probe is the model for the bwrap probe.** Upstream's
  comment is explicit: a version check "would miss a kernel that has the syscalls
  but refuses enforcement; actually restricting is the only honest signal".
  Odessy's bwrap probe was strengthened to match, attempting a prohibited write
  and requiring it to be denied.

## Port Sequence

The Windows ACL stack is the highest-risk part of this port. It is the only
backend that cannot be reimplemented from its documentation, and it is a security
boundary Odessy will own outright.

The sequence is therefore fixed, and mixing its steps is the failure mode it
exists to prevent:

```text
1. dsh source
2. Odessy moved source            byte-identical where possible
3. minimal dependency rewiring    imports only
4. tests pass                     run, not merely present
5. Odessy-specific cleanup
```

Step 2 moves files with no behavioural change. Step 3 changes only what must
change to resolve a dependency:

- the two `dsh-subprocess/control` constants, dropped along with the control-fd
  feature, because Odessy owns its own process timeout and termination;
- the `dsh-win32-process` imports, repointed at the vendored copy;
- the `tsx/esm/api` source-launch fallback, dropped because Odessy ships built
  output.

**Step 5 does not begin in the same commit as steps 2 to 4.** Architectural
refactoring mixed into a provenance-sensitive Windows security port destroys the
attribution that makes a later failure diagnosable: it would be impossible to say
whether something broke because of the port or because of the redesign.

## Deferred To Later Steps

Two things are intentionally incomplete, recorded here so neither is mistaken for
an oversight:

| Item | Status | Why |
|---|---|---|
| The TypeScript Landlock ABI | Deferred to the Windows ACL step | Node cannot read the ABI: it is obtainable only through the `landlock_create_ruleset` syscall, and `landlock-run --probe` reports full or partial but never *which* right is missing. Both facts are needed, so the ABI is reported as unknown rather than guessed. `koffi` arrives with the Windows port, and only `probe.ts` needs to change; the provider logic is already verified across ABIs 1 to 5 with an injected value. **The injection is a test seam, not a permanent escape hatch.** |
| `escalation.ts` | Not mirrored, permanently | The escalation choreography is a policy-layer concern, and Odessy's policy layer is Python. Mirroring it would create two authorities for one approval decision. The TypeScript side gets the vocabulary and the mechanism, not the approval boundary. |

## Facts Preserved Verbatim

The following are carried across unchanged, because each exists for a specific failure
that has already been paid for once:

- The `confine(argv, policy)` contract, taking exact argv rather than a shell string.
- The prohibition on silent unconfined passthrough: a provider that cannot confine must
  fail closed.
- Per-backend **denial dialects**, never a cross-backend union. A union claims denials a
  given backend never produces.
- **Runner failure is not command denial.** Runner failure means the command never ran;
  denial means confinement worked and blocked it.
- Per-call policy carriage: policy rides the call and is never fixed on the provider.
- The escalation ladder as a closed table, strictly wider, one call only, with
  non-widening requests rejected without prompting a human.
- The explicit vocabulary bound: network and process visibility are outside the
  file-effect vocabulary.
