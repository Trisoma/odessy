from __future__ import annotations

import odessy.voice.loop as voice_loop


def test_voice_loop_module_has_cli_entrypoint() -> None:
    assert callable(voice_loop.main)
