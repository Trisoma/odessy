# The Real-Time Boundary

What Odessy does and does not promise about timing, and where the boundary between
tiers actually falls.

## The claim being refused

Odessy is **not a hard-real-time system**, and must not be described as one. The
Linux host — the Jetson, where the model, the agent loop, the authority layer, and
orchestration run — is **upper-tier compute and control planning**. It decides
*what* should happen and *whether* it may. It does not and cannot guarantee *when*.

## Why the Linux tier cannot be real-time

This is a property of the platform, not a current implementation shortfall, so it
will not be fixed by optimising the Python.

```text
scheduling      The Linux scheduler offers no bounded worst-case dispatch latency.
                Even PREEMPT_RT, which this board does not run, narrows the tail
                rather than eliminating it. There is no contractual deadline.

runtime         CPython adds pauses Linux has no control over: garbage collection,
                and GIL contention whenever more than one thread is live. A
                "fast path" can be preempted by a collection it did not schedule.

allocation      Model inference, tokenisation, and audio buffers allocate. An
                allocation is not bounded, and no amount of tuning makes it so.

I/O stack       USB and network transports retry, reorder, and time out on their
                own schedule. A command issued on time can still complete late.

tool path       A tool call crosses an MCP boundary, and may execute on another
                host entirely. That hop has no latency contract at all.
```

The last two are structural. Even a hypothetically perfect Linux would still be
waiting on a transport and a remote host it does not control.

## Where the boundary actually falls

```text
┌─────────────────────────────────────────────────┐
│ Linux host (Jetson) — upper tier                │
│ model · agent loop · authority · planning       │
│ best-effort latency, NO timing guarantee        │
└────────────────┬────────────────────────────────┘
                 │  link (best effort)
┌────────────────▼────────────────────────────────┐
│ Managed device (MCU) — lower tier               │
│ gpio · pwm · adc · serial · spi · i2c · can     │
│ owns its own real-time loop, if it needs one    │
└─────────────────────────────────────────────────┘
```

The split is a **responsibility** boundary, not merely a performance one:

- The **host** decides and orchestrates. It is where judgement lives, and judgement
  tolerates jitter.
- The **device** performs low-level work on a timescale the host cannot meet. If a
  deadline matters, the device owns it — the firmware closes the loop locally and
  the host supervises.

That is why an MCU is modelled as a **managed device** and not as an execution
environment: Odessy does not run code on it, and does not schedule it. The device is
reached through a permission surface, and no ordering guarantee is offered across
that surface.

## What is not claimed

```text
- No bounded worst-case latency anywhere in the stack.
- No guarantee about when a tool call reaches a device, or when it completes.
- No guarantee that a planned action executes before a device-side deadline.
- No real-time scheduling, priority inheritance, or deadline propagation.
- No claim that "fast enough on the bench" implies a bound.
```

## What is claimed

```text
- Timing failures are reported rather than hidden. A tool timeout is a reported
  outcome with its own status, distinct from a failure.
- Nothing is detached. A turn cannot leave work running outside its own lifecycle,
  which is what makes best-effort latency survivable.
- A device that needs a deadline owns that deadline in firmware, where the timing
  problem actually is.
```

## Consequence for the device layer

Because the host cannot guarantee timing, a future device provider must not offer an
API that *implies* one. An operation that reads a sensor is a request; whether the
value is fresh is the device's business, and the interface has to say so rather than
letting a caller assume synchrony.

This is one reason the device layer currently declares families without defining an
operation surface: the timing semantics are part of what that surface must express,
and they cannot be designed before there is a device to measure.

## Related

- `docs/execution/PROVIDER-PLACEHOLDERS.md` — what is declared and not backed.
- `docs/reference/current-limitations.mdx` — the user-facing limits reference.
- `LIMITATION.md` — the isolation layer's limits and quarantined failures.
