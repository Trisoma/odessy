"""Fixed coordinator service entrypoint; no model is inferred during startup."""
from __future__ import annotations
import argparse
import asyncio
import os
from pathlib import Path
from .cli import _load
from .configuration import environment_text
from .planning import build_plan


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--selection',required=True)
    parser.add_argument('--workspace',required=True)
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args(argv)
    selection=_load(args.selection)
    if selection.role=='execution' or selection.model is None:
        raise ValueError('coordinator service requires an explicitly configured model')
    # Reuse the same validated export rules, without persisting credential contents.
    environment_text(build_plan(selection))
    for key,value in build_plan(selection).environment.items(): os.environ[key]=value
    from odessy.application.serve import run_server
    asyncio.run(run_server(port=args.port,workspace=Path(args.workspace),backend=selection.model.provider))

if __name__=='__main__': main()
