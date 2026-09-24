Run the normal voice command, then look for the new phase lines:

```bash
cd ~/odessy
source .venv/bin/activate

python -m odessy voice
```

You should now see something like:

```text
Recording started
Recording stopped
Transcribing speech
Model is thinking
Generating action proposal
Validating action
Running tool
Generating response
Synthesizing speech
Playing response
```

For benchmark output with the new timing columns:

```bash
python -m benchmark \
  --scenario voice_turn \
  --adapter real \
  --audio-path path/to/input.wav \
  --runs 3 \
  --warmup 1
```

Then inspect:

```bash
benchmark/results/<timestamp>/samples.csv
```

Look for columns like:

```text
agent_turn_total_ms
proposal_generation_ms
proposal_parsing_ms
repair_generation_ms
policy_validation_ms
mcp_execution_ms
final_generation_ms
proposal_valid
repair_triggered
tool_called
prompt_tokens
completion_tokens
```

For a live microphone run, use `python -m odessy voice`. For repeatable timing, use `python -m benchmark --scenario voice_turn ...` with a fixed audio file.