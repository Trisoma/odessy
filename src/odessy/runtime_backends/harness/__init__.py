"""The Harness engine: one supervised child process per session, narrow by construction.

Import cost is deliberate: ``odessy.runtime_backends.harness`` pulls in subprocess and
threading machinery, so :mod:`odessy.runtime_backends.selection` imports this package
lazily, inside the ``harness`` builder, and only when that engine is actually chosen.

The bundle of what the child is allowed to be is small and reviewed:
- ``pin`` — the one allowed version and its artifact digest;
- ``config`` — explicit launch description and a minimal child environment;
- ``process`` — stdio framing, failure classification, owned process-tree cleanup;
- ``runtime`` — the ``RuntimeSession`` implementation for one Odessy session;
- ``profile`` — the reviewed overlay text that disables every model-facing tool, written
  into the owned Harness home at launch;
- ``bridge`` — the bounded client for the live-stream plugin (loopback, token, one session);
- ``stream`` — the per-turn translator that turns those frames into ``delta`` events and one
  frozen settlement. Deltas are transient; the committed answer still comes from ACP only.
"""

from __future__ import annotations

from odessy.runtime_backends.harness.approvals import (
    ApprovalBindError,
    HarnessApprovalBridge,
    PERMISSION_METHOD,
)
from odessy.runtime_backends.harness.bridge import BridgeError, LiveBridge
from odessy.runtime_backends.harness.config import (
    HarnessConfig,
    default_home,
    from_environment,
)
from odessy.runtime_backends.harness.process import (
    HarnessProcess,
    HarnessProcessError,
    HarnessProtocolError,
    HarnessTransportError,
)
from odessy.runtime_backends.harness.profile import (
    NARROW_PATCH_NAME,
    NARROW_PATCH_TEXT,
    materialize_narrow_patch,
)
from odessy.runtime_backends.harness.questions import HarnessQuestionBridge
from odessy.runtime_backends.harness.runtime import HarnessRuntime
from odessy.runtime_backends.harness.stream import (
    Delta,
    Settlement,
    ToolProgress,
    TurnStream,
)

__all__ = [
    "ApprovalBindError",
    "BridgeError",
    "Delta",
    "HarnessApprovalBridge",
    "HarnessConfig",
    "HarnessProcess",
    "HarnessProcessError",
    "HarnessProtocolError",
    "HarnessQuestionBridge",
    "HarnessRuntime",
    "HarnessTransportError",
    "LiveBridge",
    "NARROW_PATCH_NAME",
    "NARROW_PATCH_TEXT",
    "PERMISSION_METHOD",
    "Settlement",
    "ToolProgress",
    "TurnStream",
    "default_home",
    "from_environment",
    "materialize_narrow_patch",
]
