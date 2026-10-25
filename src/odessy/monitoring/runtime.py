"""Nonblocking allowlisted runtime telemetry, never conversation contents."""
import asyncio
import math
import re
import uuid
from datetime import datetime, timezone

NUMERIC = {'request_duration_ms', 'ttft_ms', 'generation_tokens_per_second', 'estimated_cost'}
COUNTERS = {'prompt_tokens', 'completion_tokens', 'errors', 'timeouts', 'tool_failures', 'active_jobs'}
LABELS = {'model', 'provider', 'participant', 'selected_model', 'used_model', 'currency', 'price_status'}


def sanitize(values):
    result = {}
    for key in NUMERIC | COUNTERS:
        value = values.get(key)
        if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 2**53 - 1:
            if key in COUNTERS and type(value) is not int:
                continue
            result[key] = value
    for key in LABELS:
        value = values.get(key)
        # No arbitrary free text, paths, endpoint URLs or credentials in labels.
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:/@+-]{1,128}', value) and '://' not in value:
            from odessy.authority.redaction import redact_text
            result[key] = redact_text(value)
    return result


class RuntimeObservationSink:
    def __init__(self, send, *, host_id, generation, capacity=64):
        self.send, self.host_id, self.generation = send, host_id, generation
        self.source = uuid.uuid4().hex
        self.sequence = 0
        self.dropped = 0
        self.queue = asyncio.Queue(maxsize=capacity)
        self.task = None
        self.closed = False

    def publish(self, observation):
        if self.closed:
            return
        self.sequence += 1
        value = {'schema_version': 1, 'host_id': self.host_id, 'generation': self.generation,
                 'source_id': self.source, 'sequence': self.sequence,
                 'observed_at': datetime.now(timezone.utc).isoformat(), 'ai': sanitize(observation)}
        try:
            self.queue.put_nowait(value)
        except asyncio.QueueFull:
            self.dropped += 1
            return
        if self.task is None:
            self.task = asyncio.create_task(self._run())

    async def _run(self):
        while True:
            value = await self.queue.get()
            try:
                await asyncio.wait_for(self.send(value), timeout=1)
            except Exception:
                self.dropped += 1
            finally:
                self.queue.task_done()

    async def close(self):
        self.closed = True
        try:
            await asyncio.wait_for(self.queue.join(), timeout=2)
        except asyncio.TimeoutError:
            self.dropped += self.queue.qsize()
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
