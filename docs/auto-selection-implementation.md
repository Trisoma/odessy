# Automatic selection implementation record

Implements the approved `autoselection-plan.md` supplied on October 20, 2026,
on `feat/auto-model-selection`, based on `develop` revision `1d02a30`.

- Routing: conservative rules, lazy local roles, text-only fast generation,
  initial-proposal cloud fallback only.
- Runtime: background generation only for auto, shared stop/deadline/call guard,
  stop checks after preparation, cancellation-resistant worker draining.
- Artifacts: additive route settings and per-attempt stages; existing keys retained.
- Verification: scripted route/authority/transport tests, independent code review,
  full Python suite and Node checks; hardware qualification remains separate.

Ruling: the wake branch is not merged, so provide a role-message context input
without implementing a second voice session store. Continuous-session integration
must use the final wake controller interface when those branches are integrated.

Ruling: opaque Anthropic SDK retries are disabled only during routed generation;
OpenAI-compatible retries are individually guarded. This preserves explicit-backend
defaults while keeping auto's cancellation and call budget enforceable.

Independent review found unguarded transport retries and repeated cancellation
orphaning a worker. Both were reproduced by failing regression tests and fixed.
The router rejects turn initialization during active generation. Hardware latency
and memory measurements remain pending; no paid evaluation was run.

## Recorded verification

- Baseline model/batch tests: 111 passed, 1 skipped.
- Final full Python suite with normal subprocess permissions: 969 passed,
  3 skipped in 196.98 seconds.
- Latest routing/doctor regression selection, including two tests added after
  full-suite collection: 52 passed.
- Node typecheck and build passed; Node tests: 174 passed, 1 skipped.
- Restricted subprocess tests initially hit Windows process restrictions;
  the full Python and Node suites passed when rerun with normal permissions.
- No Jetson benchmark, model download, or paid evaluation was performed.
