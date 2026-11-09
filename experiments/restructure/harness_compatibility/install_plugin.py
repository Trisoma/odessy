"""Install the spike plugin bundle into an isolated Harness profile.

`dsh plugin add` shells out to `pnpm`, which the packaged runtime does not bundle.
This script points that call at a **workspace-local** toolchain (portable Node +
pnpm unpacked side by side), so no system-wide install and no user-machine
requirement is involved. It is a development prerequisite, not a product design
constraint: the packaged profile is what ships.

Usage:

    python install_plugin.py --exe <runtime exe> --home <DSH_HOME> --toolchain <dir>

`<toolchain>` must contain `bin/pnpm.cmd`, `package/bin/pnpm.cjs` and a portable
`node-v22.19.0-win-x64/` directory (see the compatibility README).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent / "plugin" / "odessy-stream-bridge"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exe", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--toolchain", required=True)
    parser.add_argument("--profile", default="acp")
    args = parser.parse_args()

    toolchain = Path(args.toolchain).resolve()
    node_dirs = sorted(toolchain.glob("node-v*-win-x64"))
    if not node_dirs:
        print("no portable node-v*-win-x64 found in the toolchain", file=sys.stderr)
        return 2
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PATH"] = os.pathsep.join([str(toolchain / "bin"), str(node_dirs[-1]), env.get("PATH", "")])
    env["PNPM_HOME"] = str(toolchain / "home")
    env["npm_config_cache"] = str(toolchain / "npm-cache")
    env["npm_config_store_dir"] = str(toolchain / "store")
    env["DSH_HOME"] = str(Path(args.home).resolve())

    command = [args.exe, "plugin", "--profile", args.profile, "add", f"file:{PLUGIN}"]
    print("+ " + " ".join(command))
    # pnpm writes UTF-8 regardless of the Windows console code page; decode explicitly so
    # a non-ASCII status line cannot fail the install.
    result = subprocess.run(command, env=env, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    sys.stdout.write(result.stdout or "")
    sys.stderr.write(result.stderr or "")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
