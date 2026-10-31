# Provider placeholders — what is declared but not backed

The authoritative list of capability Odessy **declares** and does not **implement**.
Referenced by `odessy/execution/__init__.py` and `odessy/devices/__init__.py`.

Baseline: the released **v0.3.0 private prerelease**. Unfinished capabilities and
design decisions below move to the [v0.4.0 cycle](../releases/v0.4.0.md), subject
to their prerequisites; they are not promised implementations.

## Why this document exists

A capability-driven system that silently *lacks* something is indistinguishable from
one that silently *has* it. Every entry below is a place where a reader could otherwise
reasonably assume support that does not exist.

The rule the list enforces:

> **A placeholder is declared and structurally unregistered.** It appears in a
> declaration, never in a registry. `select()` must be unable to return one as a matter
> of construction, not because a flag says so.

An abstraction with no demonstrated backend is not built here. Declarations carry
*intent*; they carry no operation surface, because designing that surface with nothing
to drive it would be guessing at what the operations are.

---

## 1. Execution providers

| Provider | Platform | Status |
|---|---|---|
| `bwrap` | Linux | Implemented. Functional probe; four distinguishable availability states. |
| `landlock` (Python) | Linux | Implemented. Requires the native `landlock-run` launcher, which Odessy does not ship, so it reports unavailable unless one is on `PATH`. |
| `landlock` (TypeScript) | Linux | **Placeholder.** See §1.1. |
| `seatbelt` | macOS | Implemented. |
| `windows-acl` | Windows | Implemented. **`partial`** enforcement — see §1.2. |
| `unconfined` | any | Implemented. Not a backend: the named, reported absence of confinement. |

### 1.1 The TypeScript Landlock ABI is unavailable

The provider is **complete and fully tested against an injected ABI**; only the input is
missing. Node cannot issue `landlock_create_ruleset`, so `probeLandlock()` reports the
ABI as unknown and the provider is unavailable on every Linux host in production.

Making it live means binding libc's `syscall` at **fixed arity** — `koffi` cannot issue
a bare syscall:

```text
long syscall(long, void*, size_t, unsigned int)
call: syscall(444, NULL, 0, LANDLOCK_CREATE_RULESET_VERSION)
```

That declaration must be verified on **x86_64 and arm64** before being trusted. A wrong
FFI signature yields a wrong ABI, which yields a wrong limitation set that still reads
as a considered statement about the host — the single worst outcome available here, and
why reporting the ABI as unknown is preferable to deriving it from the kernel release.

The injection used by tests is a **test seam, not an escape hatch.**

### 1.2 The Windows backend is `partial`, by construction

Not a placeholder — an implemented backend that cannot be complete. Two documented gaps:

1. `WRITE_RESTRICTED` must retain `Everyone` in both restricting lists. Without it, early
   DLL initialization dies (`0xC0000142`) and CNG crashes PowerShell (`0xE0434352`). So an
   external object whose DACL grants Everyone a requested write right stays writable.
2. NTFS hard links alias a file *object*, not a path, so a granted workspace file is
   writable through an external alias. Rejecting multiply-linked files is not viable for
   ordinary pnpm workspaces.

Derived-but-undocumented device and ioctl boundaries are **deliberately absent** from the
limitation set. The reasoning is sound, but a limitation set is read as a considered
statement about the host, so an inference must not be promoted to evidence. Phase 10
decides whether the vocabulary needs them.

---

## 2. Managed devices

An MCU is a **managed device, not an execution environment**. There is no `confine` and
no `execute`; device access is a *permission* surface under the authority layer, while a
confined process reaching the device directly is the *isolation* layer's concern.

| Family | Capabilities (intended) | Status |
|---|---|---|
| ESP32 | gpio, pwm, adc, serial, spi, i2c, usb | **Declared** |
| STM32 | gpio, pwm, adc, serial, spi, i2c, can, usb | **Declared** |
| RP2040 | gpio, pwm, adc, serial, spi, i2c, usb | **Declared** |

All three are declarations only:

- **No transport implementation** exists for any of them.
- **No device-conformance harness** exists, so nothing has ever been driven.
- **No firmware contract** exists.
- The **intended capability lists are unverified.** They are what the family is *expected*
  to offer, not what has been confirmed against a datasheet or a device. The field name
  says so.

`default_device_providers()` returns **empty**, and nothing registers from anywhere else.
That is the mechanism that makes a declaration unable to become a selection: there is no
code path by which a placeholder reaches a caller, because there is nothing to return.

There is deliberately **no device operation API** (read, write, transaction). That surface
gets defined when the first device is actually driven, against something real.

---

## 3. Cross-cutting deferrals

| Item | Status |
|---|---|
| **Host pairing** | The Rust `odessy-link` mTLS path exists (`ODESSY_PAIRED_HOST`), but Jetson, real-network and Linux/ARM64 qualification remain pending. Single mode starts a local subprocess; split mode also supports a configured remote MCP URL, which is **not** authenticated. The `ExecutionTarget` marker provides the explicit authorization gate a paired host must retain. |
| **Voice escalation approval** | The seam is wired end to end, async, and returns a closed outcome. The prompt is still a console prompt; spoken approval waits on a composition decision. |
| **Python Windows backend** | Python ships no Windows provider, so on Windows the Python bridge reports `unconfined` while the TypeScript host can genuinely confine. The Jetson runs Linux, so this is not on the production path — but it is an asymmetry, not a non-issue. |

---

## 4. Asymmetries worth knowing

- **`HostFacts` differs between languages.** TypeScript has a `windowsAcl` section Python
  lacks. This is not a defect: Python is the policy authority while TypeScript is also an
  execution implementation. Contract *semantics* must stay aligned; internal probe structs
  need not be identical.
- **The authorization gate covers all tool calls**, not only process ones — stricter than
  the invariant states. If a legitimate read-only cross-host use appears, narrow it to
  gate on `requirement_for(...) is not None`.

---

## 5. Adding a placeholder

1. Add it to a **declaration**, not to a registry function.
2. Give it a **reason** it is unbacked. A declaration without a reason is a claim without
   evidence.
3. Confirm it is **structurally unreachable**: no registry returns it, and a test asserts
   that.
4. Add it to the table above.

## 6. How this is verified

| Property | Test |
|---|---|
| No device provider is shipped | `test_no_device_provider_is_shipped` |
| A declaration cannot be a provider | `test_no_declaration_can_be_a_provider` |
| A declaration cannot satisfy a requirement | `test_a_declaration_cannot_satisfy_an_execution_requirement` |
| Every declared family has a reason | `test_every_declaration_gives_a_reason_it_is_unbacked` |
| No family is silently undeclared | `test_declarations_cover_every_family_in_the_vocabulary` |
| No invented device capabilities | `test_intended_capabilities_come_from_the_shared_vocabulary` |
| The TS Landlock ABI is not guessed | `probe.ts` reports the ABI as unknown, by construction |
