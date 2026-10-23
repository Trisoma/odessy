import importlib.util
from pathlib import Path
import pytest


def module():
    path=Path(__file__).parents[1]/'tool/sign_setup_artifact.py'
    spec=importlib.util.spec_from_file_location('setup_signing',path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result


def test_signing_requires_explicit_identity_and_verifies(tmp_path):
    artifact=tmp_path/'setup.exe';artifact.write_bytes(b'exe');calls=[]
    with pytest.raises(ValueError):module().sign(artifact,'windows','',runner=lambda a:calls.append(a))
    module().sign(artifact,'windows','A'*40,timestamp='https://timestamp.digicert.com',runner=lambda a:calls.append(a))
    assert calls[0][1]=='sign' and calls[-1][1]=='verify'
    assert '/sha1' in calls[0] and '/tr' in calls[0]
