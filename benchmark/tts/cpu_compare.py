"""Offline CPU synthesis benchmark; never downloads, opens audio, or edits settings.

Run this file directly: the existing benchmark package has broader dependencies.
Each engine/thread combination gets a fresh sequential subprocess/session.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone

TEXTS = {
    "short": "Odessy is ready to help you.",
    "answer": "Odessy is ready. This is a short local speech performance test. "
    "The model generates speech on this computer. We are measuring how long it "
    "takes to produce the complete answer before any audio is played.",
}


def digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def measure(engine, text):
    start, cpu = time.perf_counter(), time.process_time()
    if engine[0] == "kokoro":
        samples, rate = engine[1].create(text, voice="af_sarah", speed=1.0, lang="en-us")
        first = time.perf_counter() - start
        duration, chunks = len(samples) / rate, 1
    else:
        first, duration, chunks = None, 0, 0
        for chunk in engine[1].synthesize(text):
            if first is None:
                first = time.perf_counter() - start
            duration += len(chunk.audio_int16_bytes) / (
                chunk.sample_rate * chunk.sample_width * chunk.sample_channels)
            chunks += 1
    wall = time.perf_counter() - start
    if not duration or first is None:
        raise RuntimeError("Synthesizer produced no audio")
    return {"wall_seconds": wall, "first_audio_seconds": first,
            "audio_seconds": duration, "rtf": wall / duration, "chunks": chunks,
            "process_cpu_seconds": time.process_time() - cpu}


def worker(args):
    import faulthandler
    faulthandler.dump_traceback_later(35)
    import onnxruntime as ort
    # Import phonemizer/native dependencies before constructing ORT worker pools.
    if args.engine == "kokoro":
        from kokoro_onnx import Kokoro
    start = time.perf_counter()
    options = ort.SessionOptions()
    options.intra_op_num_threads = args.worker_threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    session = ort.InferenceSession(str(args.model), sess_options=options,
                                   providers=["CPUExecutionProvider"])
    if args.engine == "kokoro":
        engine = ("kokoro", Kokoro.from_session(session, str(args.voices)))
    else:
        from piper import PiperVoice
        from piper.config import PiperConfig
        config = json.loads(Path(str(args.model) + ".json").read_text(encoding="utf-8"))
        engine = ("piper", PiperVoice(session=session, config=PiperConfig.from_dict(config)))
    load = time.perf_counter() - start
    partial = {"engine": args.engine, "threads": args.worker_threads,
               "load_seconds": load, "runs": []}
    def checkpoint():
        Path(args.worker_output).write_text(json.dumps(partial, indent=2), encoding="utf-8")
    checkpoint()
    # A true first request is recorded separately; no hidden warmup.
    cold = measure(engine, TEXTS["short"])
    partial["cold_short"] = cold
    checkpoint()
    rows = []
    for repeat in range(args.repeats):
        # Reverse text order on alternating repetitions to expose order effects.
        for name in list(TEXTS)[::1 if repeat % 2 == 0 else -1]:
            rows.append({"text": name, "repeat": repeat, **measure(engine, TEXTS[name])})
            partial["runs"] = rows
            checkpoint()
    versions = {name: importlib.metadata.version(name) for name in
                ("onnxruntime", "numpy", "kokoro-onnx" if args.engine == "kokoro" else "piper-tts")}
    result = {"engine": args.engine, "threads": args.worker_threads,
              "load_seconds": load, "cold_short": cold, "runs": rows,
              "versions": versions, "providers": session.get_providers()}
    Path(args.worker_output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    faulthandler.cancel_dump_traceback_later()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kokoro-python", type=Path)
    parser.add_argument("--piper-python", type=Path)
    parser.add_argument("--kokoro-model", type=Path)
    parser.add_argument("--kokoro-voices", type=Path)
    parser.add_argument("--piper-model", type=Path)
    parser.add_argument("--threads", default="0,1,2,4,8")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=120, help="Per-profile seconds")
    parser.add_argument("--output", type=Path, default=Path("benchmark/results/tts-cpu.json"))
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--engine", choices=["kokoro", "piper"], help=argparse.SUPPRESS)
    parser.add_argument("--model", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--voices", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--worker-threads", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--worker-output", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args)
        return
    threads = list(dict.fromkeys(int(x) for x in args.threads.split(",")))
    if not threads or min(threads) < 0 or args.repeats < 1 or args.timeout < 1:
        parser.error("threads must be nonnegative; repeats/timeout must be positive")
    engines = []
    for name in ("kokoro", "piper"):
        executable, model = getattr(args, name + "_python"), getattr(args, name + "_model")
        if executable is None and model is None:
            continue
        required = [executable, model] + ([args.kokoro_voices] if name == "kokoro"
                                         else [Path(str(model) + ".json")])
        if any(p is None or not p.is_file() for p in required):
            parser.error(f"Missing local {name} interpreter/model/voice/config; no downloads are performed")
        engines.append((name, executable.resolve(), model.resolve(), required[2]))
    if not engines:
        parser.error("Provide at least one engine's local interpreter/model arguments")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {"schema": 1, "utc": datetime.now(timezone.utc).isoformat(),
              "platform": platform.platform(), "processor": platform.processor(),
              "logical_cpus": os.cpu_count(), "texts": TEXTS, "repeats": args.repeats,
              "thread_order": threads, "benchmark_sha256": digest(__file__),
              "scope": "CPU synthesis only; no speaker playback, agent, STT, or perceived quality measurement. "
              "First audio is an API chunk, not sub-phrase streaming. 0 means ONNX default.",
              "assets": {}, "profiles": []}
    for name, _, model, auxiliary in engines:
        report["assets"][name] = {"model_sha256": digest(model),
                                   "auxiliary_sha256": digest(auxiliary)}
    for threads_count in threads:
        for name, executable, model, _ in engines:
            temporary = args.output.with_suffix(".worker.json")
            cmd = [str(executable), "-B", str(Path(__file__).resolve()), "--worker",
                   "--engine", name, "--model", str(model), "--worker-threads", str(threads_count),
                   "--repeats", str(args.repeats), "--worker-output", str(temporary)]
            if name == "kokoro":
                cmd += ["--voices", str(args.kokoro_voices.resolve())]
            try:
                process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           text=True, encoding="utf-8", errors="replace")
                try:
                    _, stderr = process.communicate(timeout=args.timeout)
                except subprocess.TimeoutExpired:
                    # Windows venv launchers spawn a real Python child. Killing
                    # only the launcher leaves the inference worker running.
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                       capture_output=True, timeout=10)
                    else:
                        process.kill()
                    _, stderr = process.communicate(timeout=10)
                    raise RuntimeError(f"Profile timed out after {args.timeout}s: {stderr[-3000:]}")
                if process.returncode:
                    raise RuntimeError(stderr[-3000:])
                profile = json.loads(temporary.read_text(encoding="utf-8"))
                for text in TEXTS:
                    subset = [r for r in profile["runs"] if r["text"] == text]
                    profile.setdefault("median", {})[text] = {key: statistics.median(
                        r[key] for r in subset) for key in
                        ("wall_seconds", "first_audio_seconds", "audio_seconds", "rtf", "process_cpu_seconds")}
                print(json.dumps({"engine": name, "threads": threads_count,
                                  "median": profile["median"]}), flush=True)
            except (subprocess.TimeoutExpired, RuntimeError, ValueError) as exc:
                profile = {"engine": name, "threads": threads_count, "error": str(exc)}
                if temporary.is_file():
                    profile["partial"] = json.loads(temporary.read_text(encoding="utf-8"))
                print(json.dumps(profile), flush=True)
            finally:
                temporary.unlink(missing_ok=True)
            report["profiles"].append(profile)
            args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if any("error" in p for p in report["profiles"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
