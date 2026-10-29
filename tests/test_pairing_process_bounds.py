import sys
import time

import pytest
from odessy.setup.native_bridge import _native_json

def test_pairing_cleanup_does_not_wait_for_descendant_output():
    source='import subprocess,sys,time; subprocess.Popen([sys.executable,"-c","import time;time.sleep(3)"]);time.sleep(10)'
    started=time.monotonic()
    with pytest.raises(ValueError):
        _native_json([sys.executable,'-c',source],.2)
    assert time.monotonic()-started<2

def test_pairing_oversized_output_is_refused():
    with pytest.raises(ValueError,match='too much data'):
        _native_json([sys.executable,'-c','import sys;sys.stdout.write("x"*500000)'],3)
