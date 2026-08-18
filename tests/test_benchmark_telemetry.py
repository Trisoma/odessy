from __future__ import annotations

from pathlib import Path

from benchmark.scenarios import build_real_components
from benchmark.telemetry import (
    sample_benchmark_process_memory,
    sample_process_tree_memory,
    sample_system_memory,
)
from odessy.voice.config import VoiceConfig


def test_sample_benchmark_process_memory_reads_fake_status(tmp_path: Path, monkeypatch) -> None:
    proc_root = tmp_path / "proc"
    _write_status(proc_root / "123" / "status", pid=123, ppid=1, rss_kb=111, swap_kb=22)
    monkeypatch.setattr("benchmark.telemetry.os.getpid", lambda: 123)

    sample = sample_benchmark_process_memory(proc_root=proc_root)

    assert sample.rss_bytes == 111 * 1024
    assert sample.swap_bytes == 22 * 1024


def test_sample_process_tree_memory_sums_descendants_and_notes_snapshot(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    _write_status(proc_root / "100" / "status", pid=100, ppid=1, rss_kb=100, swap_kb=10)
    _write_status(proc_root / "101" / "status", pid=101, ppid=100, rss_kb=25, swap_kb=3)
    _write_status(proc_root / "102" / "status", pid=102, ppid=101, rss_kb=5, swap_kb=1)

    sample = sample_process_tree_memory(root_pid=100, proc_root=proc_root)

    assert sample.rss_bytes == (100 + 25 + 5) * 1024
    assert sample.swap_bytes == (10 + 3 + 1) * 1024
    assert sample.telemetry_notes is not None
    assert "point-in-time snapshot" in sample.telemetry_notes


def test_sample_system_memory_reads_fake_meminfo(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(
        "\n".join(
            [
                "MemTotal:       1000 kB",
                "MemAvailable:    700 kB",
                "SwapTotal:       200 kB",
                "SwapFree:         50 kB",
            ]
        ),
        encoding="utf-8",
    )

    sample = sample_system_memory(meminfo_path=meminfo)

    assert sample.ram_used_bytes == 300 * 1024
    assert sample.ram_available_bytes == 700 * 1024
    assert sample.swap_used_bytes == 150 * 1024
    assert sample.swap_available_bytes == 50 * 1024


def test_real_voice_turn_components_are_wired(tmp_path: Path) -> None:
    config = VoiceConfig(
        whisper_bin="whisper-cli",
        whisper_model="tiny.bin",
        kokoro_model="kokoro.onnx",
        kokoro_voices="voices.bin",
    )

    components = build_real_components(config, tmp_path)

    assert components["model"].__class__.__name__ == "OllamaModel"
    assert components["stt"].__class__.__name__ == "WhisperCppSTT"
    assert components["tts"].__class__.__name__ == "KokoroTTS"
    assert components["audio_io"].__class__.__name__ == "CommandAudioIO"


def _write_status(path: Path, *, pid: int, ppid: int, rss_kb: int, swap_kb: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                f"Pid:\t{pid}",
                f"PPid:\t{ppid}",
                f"VmRSS:\t{rss_kb} kB",
                f"VmSwap:\t{swap_kb} kB",
            ]
        ),
        encoding="utf-8",
    )
