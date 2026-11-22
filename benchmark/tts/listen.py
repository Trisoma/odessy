"""Standalone Piper listening preview. No downloads or Odessy configuration edits.

Pass --text for one WAV, or --interactive to keep the model warm between inputs.
Playback only happens when --play is explicitly supplied (Windows file handler).
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import wave


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, default=root / '.voice-cache/piper/en_US-ljspeech-medium/en_US-ljspeech-medium.onnx')
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--output', type=Path, default=root / 'benchmark/results/piper-preview.wav')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--text')
    mode.add_argument('--interactive', action='store_true')
    parser.add_argument('--play', action='store_true')
    args = parser.parse_args()
    if args.threads < 0:
        parser.error('threads must be nonnegative; 0 selects ONNX default')
    if not args.model.is_file() or not Path(str(args.model) + '.json').is_file():
        parser.error('Local model/config missing; this preview never downloads')
    if args.output.suffix.lower() != '.wav':
        parser.error('output must end in .wav')
    if args.play and os.name != 'nt':
        parser.error('--play uses the Windows file handler; open the WAV manually on other systems')
    started = time.perf_counter()
    import onnxruntime as ort
    from piper import PiperVoice
    from piper.config import PiperConfig
    options = ort.SessionOptions()
    options.intra_op_num_threads = args.threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    session = ort.InferenceSession(str(args.model), sess_options=options, providers=['CPUExecutionProvider'])
    config = PiperConfig.from_dict(json.loads(Path(str(args.model) + '.json').read_text(encoding='utf-8')))
    voice = PiperVoice(session=session, config=config)
    load = time.perf_counter() - started
    print(f'Model/import load: {load:.3f}s; CPU threads: {args.threads}. No Odessy settings changed.', flush=True)
    counter = 0
    while True:
        try:
            text = input('English text (empty line exits): ') if args.interactive else args.text
        except (EOFError, KeyboardInterrupt):
            break
        if not text.strip():
            break
        path = args.output if not args.interactive else args.output.with_name(
            args.output.stem + f'-{counter + 1:03d}.wav')
        path.parent.mkdir(parents=True, exist_ok=True)
        first, duration, count = None, 0.0, 0
        started = time.perf_counter()
        with wave.open(str(path), 'wb') as wav:
            for chunk in voice.synthesize(text):
                if first is None:
                    first = time.perf_counter() - started
                    wav.setparams((chunk.sample_channels, chunk.sample_width, chunk.sample_rate, 0, 'NONE', ''))
                data = chunk.audio_int16_bytes
                wav.writeframes(data)
                duration += len(data) / (chunk.sample_rate * chunk.sample_width * chunk.sample_channels)
                count += 1
        elapsed = time.perf_counter() - started
        if not duration:
            raise RuntimeError('No audio produced')
        result = {'utc': datetime.now(timezone.utc).isoformat(), 'request': counter + 1,
                  'cold_request': counter == 0, 'model_load_seconds': load,
                  'synthesis_and_wav_seconds': elapsed, 'first_chunk_seconds': first,
                  'audio_seconds': duration, 'rtf': elapsed / duration, 'chunks': count,
                  'threads': args.threads, 'providers': session.get_providers(),
                  'wav': str(path.resolve()),
                  'scope': 'Standalone synthesis including WAV writes; no audible-onset measurement.'}
        path.with_suffix('.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        print(f'{elapsed:.3f}s to synthesize {duration:.2f}s of speech; first chunk {first:.3f}s; RTF {elapsed/duration:.3f}', flush=True)
        print(path.resolve(), flush=True)
        if args.play:
            os.startfile(str(path.resolve()))
        counter += 1
        if not args.interactive:
            break


if __name__ == '__main__':
    main()
