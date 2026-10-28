"""Observational startup configuration; never infer readiness from saved files."""
import hashlib
import json
import os
from pathlib import Path
from odessy.setup.contracts import SetupSelection, public

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def configuration_owner(root='', installation_id=None):
    if root:
        if not installation_id:
            from odessy.setup.installed import spec
            installation_id=spec(root)['installation_id']
        return digest({'installation_root': os.path.normcase(str(Path(root).resolve())), 'installation_id':installation_id})
    from odessy.setup.configured import desktop_state_file
    return digest({'source_state': os.path.normcase(str(desktop_state_file().resolve()))})

def configuration_descriptor(selection, workspace, *, owner):
    value=public(selection if isinstance(selection, SetupSelection) else SetupSelection.from_dict(selection))
    voice=value['voice']
    sections={
        'llm': value['model'],
        'stt': {key: voice[key] for key in ('stt_provider','whisper_bin','whisper_model','stt_executable','stt_model')},
        'tts': {key: voice[key] for key in ('tts_provider','kokoro_model','kokoro_voices','tts_executable','tts_model')},
        'voice': {key:voice[key] for key in ('enabled','audio_backend','audio_input_device','audio_output_device','wake_model')},
        'host': {'role':value['role'],'workspace':os.path.normcase(str(Path(workspace).expanduser().resolve()))},
    }
    from odessy.execution.outbound import read as outbound_consent
    try:
        consent=outbound_consent()
        if consent['alias']:sections['host']['outbound_consent_revision']=consent['revision']
    except ValueError:
        sections['host']['outbound_consent_revision']='unreadable'
    fingerprints={name:digest(data) for name,data in sections.items()}
    return {'owner':owner,'fingerprints':fingerprints,'fingerprint':digest({'owner':owner,'sections':fingerprints}),
            'model':None if value['model'] is None else {key:value['model'][key] for key in ('provider','name')},
            'workspace':sections['host']['workspace'], 'role':value['role']}
