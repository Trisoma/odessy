"""Measure what each usable execution backend really enforces here (prompt 08).

The report's enforcement matrix is not written from documentation: this script runs a real
command through every backend the host offers, under a policy that asks for a network cut, a
hidden path and a write boundary, and prints what happened with the evidence.

    python verify_enforcement.py --work <dir> [--json]

``--work`` is created by the script and must be disposable: the probe writes a workspace, a
directory outside it, a hidden directory, a junction, and starts a loopback listener.

Reading the output:

* ``usable: false`` means the backend refused the *control* (a legitimate write inside the
  workspace it granted). Nothing else it reports can be trusted, and it is never counted as
  enforcement.
* ``enforced: true`` means the real command was refused. ``false`` means it was allowed.
* ``contradicts`` is the one direction that matters: dimensions the plan calls enforced
  while the probe measured them open.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from odessy.execution.contract import ExecutionPolicy  # noqa: E402
from odessy.execution.enforcement_probe import probe_enforcement  # noqa: E402
from odessy.execution.providers import default_execution_providers  # noqa: E402
from odessy.execution.providers.unconfined import UnconfinedProvider  # noqa: E402

#: What the plan would ask for under the shipped defaults: a workspace-write command, the
#: credential list hidden, and the network cut for a command that was not confirmed.
LIMITATIONS_WHEN_ASKED = ("cannot_restrict_network", "cannot_hide_paths", "cannot_restrict_reads")


def _listener() -> tuple[socket.socket, int]:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)

    def accept() -> None:
        while True:
            try:
                connection, _ = listener.accept()
            except OSError:
                return
            connection.close()

    threading.Thread(target=accept, daemon=True).start()
    return listener, listener.getsockname()[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", required=True)
    parser.add_argument("--json", action="store_true", help="machine-readable output only")
    args = parser.parse_args()

    work = Path(args.work).resolve()
    workspace, outside, hidden = work / "ws", work / "outside", work / "hidden"
    for path in (workspace, outside, hidden):
        path.mkdir(parents=True, exist_ok=True)
    (hidden / "secret.txt").write_text("credential", encoding="utf-8")

    listener, port = _listener()
    policy = ExecutionPolicy(mode="workspace-write", workspace_root=str(workspace),
                             network="deny", hidden_paths=(str(hidden),))
    report: dict = {"policy": {"mode": policy.mode, "network": policy.network,
                               "hidden_paths": list(policy.hidden_paths)}, "backends": []}
    try:
        for provider in [*default_execution_providers(), UnconfinedProvider()]:
            probe = provider.probe()
            entry: dict = {"provider": provider.provider_id, "available": probe.available,
                           "probe_reason": probe.reason}
            if probe.available:
                measurement = probe_enforcement(
                    provider, policy, workspace=workspace, outside=outside, hidden=hidden,
                    hidden_file=hidden / "secret.txt", port=port,
                )
                entry.update({
                    "usable": measurement.usable,
                    "declared_enforcement": measurement.enforcement,
                    "dimensions": measurement.as_dict()["measurements"],
                    "contradicts": list(measurement.contradicts(LIMITATIONS_WHEN_ASKED)),
                })
            report["backends"].append(entry)
    finally:
        listener.close()

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    print(f"policy: mode={policy.mode} network={policy.network} "
          f"hidden={len(policy.hidden_paths)} path(s)")
    for entry in report["backends"]:
        if not entry["available"]:
            print(f"\n{entry['provider']}: unavailable — {entry['probe_reason']}")
            continue
        status = "usable" if entry["usable"] else "UNUSABLE (control refused)"
        print(f"\n{entry['provider']}: {status}, declared {entry['declared_enforcement']}")
        for dimension in entry["dimensions"]:
            verdict = {True: "enforced", False: "OPEN", None: "unmeasured"}[dimension["enforced"]]
            print(f"  {dimension['dimension']:16} {verdict:10} {dimension['detail']}")
        if entry["contradicts"]:
            print(f"  OVERSTATED: {', '.join(entry['contradicts'])}")
    print()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
