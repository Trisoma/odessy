"""Fixed coordinator service entrypoint; no model is inferred during startup."""
from __future__ import annotations
import argparse
import asyncio
import os
from pathlib import Path
from .cli import _load
from .configuration import environment_text
from .planning import build_plan


def run(selection,*,workspace,host='127.0.0.1',port=8765,token=None,watch_stdin=False,configuration_owner=None):
    if selection.role=='execution' or selection.model is None:
        raise ValueError('coordinator service requires an explicitly configured model')
    # Reuse the same validated export rules, without persisting credential contents.
    plan=build_plan(selection)
    environment_text(plan)
    if selection.model.credential_env:
        from .verification import credential_value
        key=credential_value(selection.model)
        if not key:
            raise ValueError('Supply the selected credential environment variable before starting Odessy.')
        os.environ['ODESSY_CLOUD_API_KEY']=key
    for key,value in plan.environment.items(): os.environ[key]=value
    from odessy.application.serve import run_server
    from odessy.application.capabilities import configuration_descriptor
    descriptor = configuration_descriptor(selection, workspace, owner=configuration_owner) if configuration_owner else None
    desktop_voice=None
    if selection.voice.enabled:
        import socket
        from odessy.application.desktop_voice import DesktopVoice
        from odessy.voice.desktop_audio import DesktopAudio
        from odessy.voice.config import VoiceConfig
        voice_config=VoiceConfig.from_env()
        desktop_voice=DesktopVoice(lambda:DesktopAudio(voice_config),host=socket.gethostname())
    asyncio.run(run_server(host=host,port=port,workspace=Path(workspace),backend=selection.model.provider,
                           token=token,watch_stdin=watch_stdin,print_token=token is None,
                           runtime_configuration=descriptor,desktop_voice=desktop_voice))


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--selection',required=True)
    parser.add_argument('--workspace',required=True)
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args(argv)
    run(_load(args.selection),workspace=args.workspace,port=args.port)

if __name__=='__main__': main()
