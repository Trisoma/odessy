"""Deterministic, local, OpenAI-compatible provider fixture for the Harness spike.

This is an *experiment*: it is not imported by `src/odessy`, not packaged, and not
used by any production code path. It exists so the pinned Harness runtime can run a
controlled prompt without a real provider, a key, or network access.

The Harness DeepSeek adapter reads `DEEPSEEK_BASE_URL` and `DEEPSEEK_API_KEY` from
the child environment, so pointing those at this server is what makes the run
deterministic.

Behaviour is selected by markers in the last user message so a scenario can ask for
a long stream (cancellation) or a tool call (permission) without editing this file:

    "slow"          -> 40 chunks, 250 ms apart (about 10 s of stream)
    "tool <name> <json-args>" -> one tool call for <name>, then a short final text
    anything else   -> 5 chunks, 120 ms apart

Every request is appended to `requests.jsonl` next to the log file given by
`--log-dir`, so the runner can assert what the runtime actually sent (for example
that a resumed session replayed its history).
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_LOCK = threading.Lock()
_COUNTER = {"n": 0}
#: Set by the runner: absolute path outside the session workspace used by the
#: escalation scenario. The first call writes there (the sandbox denies it), the
#: retry asks for a wider mode, and the client rejects that request.
ESCALATION_TARGET: str | None = None


def _tool_results(messages: list) -> int:
    count = 0
    for message in messages:
        if message.get("role") == "tool":
            count += 1
            continue
        content = message.get("content")
        if isinstance(content, list):
            count += sum(1 for part in content if isinstance(part, dict) and part.get("type") == "tool_result")
    return count


def _chunks_for(prompt: str) -> tuple[list[str], int, dict | None]:
    """Return (text chunks, delay ms, tool_call or None)."""
    if prompt.startswith("escalate"):
        target = ESCALATION_TARGET or "spike-outside.txt"
        command = "Set-Content -LiteralPath '" + target + "' -Value spike"
        return (["Attempting the write."], 10, {"name": "pwsh", "arguments": {
            "description": "Write a marker outside the workspace",
            "command": command,
        }})
    if "slow survive" in prompt.lower():
        # 5 s: still running after the other turn is cancelled and observed.
        return ([f"token{i} " for i in range(20)], 250, None)
    if "slow cancel" in prompt.lower():
        return ([f"token{i} " for i in range(60)], 250, None)
    if prompt.startswith("tool "):
        _, name, raw = prompt.split(" ", 2)
        try:
            arguments = json.loads(raw)
        except json.JSONDecodeError:
            arguments = {"command": raw}
        return (["Working on it."], 10, {"name": name, "arguments": arguments})
    if "slow" in prompt.lower():
        return ([f"token{i} " for i in range(40)], 250, None)
    return (["Hello", " from", " the", " deterministic", " fixture."], 120, None)


def _text_of(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


#: The runtime appends its own user-role context snapshot *after* the caller's prompt, so
#: "the last user message" is that snapshot. Skipping it by its documented prefix is what
#: makes the fixture react to the request rather than to the runtime's bookkeeping.
CONTEXT_PREFIX = "Current runtime context."


def prompt_of(messages: list) -> str:
    """The caller's request: the newest user message that is not a context snapshot.

    Reading the conversation's older user messages was wrong too: a second turn would be
    replayed with the first turn's scenario. Only the newest request counts.
    """
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        text = _text_of(message)
        if text.startswith(CONTEXT_PREFIX):
            continue
        return text
    return ""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    log_dir: Path

    def log_message(self, fmt, *args):  # keep stdout clean for the runner
        return

    def _record(self, entry: dict) -> None:
        with _LOCK:
            _COUNTER["n"] += 1
            entry["seq"] = _COUNTER["n"]
            with (self.log_dir / "requests.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def do_GET(self):  # some clients probe /models
        body = json.dumps({"object": "list", "data": [{"id": "mock-model", "object": "model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {}
        messages = payload.get("messages") or []
        prompt = prompt_of(messages)
        chunks, delay_ms, tool_call = _chunks_for(prompt)
        if prompt.startswith("question"):
            # Prompt 07: the model asks a structured clarification. The first request has
            # no tool result and asks; after the tool returns, the same turn continues.
            if _tool_results(messages) == 0:
                chunks, delay_ms, tool_call = ([], 0, {"name": "ask_user_question", "arguments": {
                    "questions": [{
                        "id": "cleanup",
                        "question": "Proceed with the cleanup?",
                        "header": "Confirm",
                        "options": [{"label": "Yes"}, {"label": "No"}],
                    }],
                }})
            else:
                chunks, delay_ms, tool_call = (["Answer received; continuing."], 10, None)
        # Escalate exactly once: after the first tool result. A rejected approval
        # returns another result, and asking again would loop forever. Guarded on the
        # scenario, so an ordinary tool scenario is not hijacked into an escalation.
        elif prompt.startswith("escalate") and _tool_results(messages) == 1:
            target = ESCALATION_TARGET or "spike-outside.txt"
            command = "Set-Content -LiteralPath '" + target + "' -Value spike"
            chunks, delay_ms = ["Retrying with the wider mode."], 10
            tool_call = {"name": "pwsh", "arguments": {
                "description": "Retry the denied write with escalation",
                "command": command,
                "sandbox_permissions": "danger-full-access",
                "justification": "Spike: prove the approval prompt reaches the client.",
            }}
        tools = payload.get("tools") or []

        def tool_name(tool: dict) -> str:
            return (tool.get("function") or {}).get("name") or tool.get("name") or ""

        schemas = {}
        for tool in tools:
            name = tool_name(tool)
            if any(marker in name for marker in ("pwsh", "bash", "shell", "exec", "fs")):
                schemas[name] = (tool.get("function") or {}).get("parameters") or tool.get("input_schema")
        self._record({
            "path": self.path,
            "stream": bool(payload.get("stream")),
            "model": payload.get("model"),
            "message_roles": [m.get("role") for m in messages],
            "n_messages": len(messages),
            "last_user": prompt[:200],
            "tools": [tool_name(t) for t in tools][:60],
            "tool_schemas": schemas,
            # What the model was actually shown after a tool ran. Without it a fixture can
            # prove a call was *made* but not that the caller saw its result.
            "tool_results": [_text_of(m)[:2000] for m in messages
                             if m.get("role") == "tool"],
            "t": time.time(),
        })

        if payload.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                if "messages" in self.path:
                    self._anthropic_stream(payload, chunks, delay_ms, tool_call)
                else:
                    self._openai_stream(payload, chunks, delay_ms, tool_call)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
            finally:
                self.close_connection = True
            return

        message: dict = {"role": "assistant", "content": "".join(chunks)}
        if tool_call is not None:
            message = {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call_spike_1", "type": "function",
                "function": {"name": tool_call["name"], "arguments": json.dumps(tool_call["arguments"])},
            }]}
        if "messages" in self.path:
            body = json.dumps({
                "id": "msg_mock", "type": "message", "role": "assistant",
                "model": payload.get("model", "mock"),
                "content": ([{"type": "text", "text": message.get("content", "")}]
                            + ([{"type": "tool_use", "id": "toolu_spike_1",
                                 "name": tool_call["name"], "input": tool_call["arguments"]}]
                               if tool_call else [])),
                "stop_reason": "tool_use" if tool_call else "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }).encode()
        else:
            body = json.dumps({
                "id": "mock", "object": "chat.completion", "created": 0,
                "model": payload.get("model", "mock"),
                "choices": [{"index": 0, "message": message,
                             "finish_reason": "tool_calls" if tool_call else "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, payload: dict) -> None:
        self.wfile.write(b"data: " + json.dumps(payload).encode() + b"\n\n")
        self.wfile.flush()

    def _event(self, name: str, payload: dict) -> None:
        self.wfile.write(b"event: " + name.encode() + b"\ndata: " + json.dumps(payload).encode() + b"\n\n")
        self.wfile.flush()

    def _openai_stream(self, payload, chunks, delay_ms, tool_call) -> None:
        base = {"id": "mock", "object": "chat.completion.chunk", "created": 0,
                "model": payload.get("model", "mock")}
        if tool_call is not None:
            delta = {"role": "assistant", "content": "", "tool_calls": [{
                "index": 0, "id": "call_spike_1", "type": "function",
                "function": {"name": tool_call["name"],
                             "arguments": json.dumps(tool_call["arguments"])},
            }]}
            self._sse({**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
            self._sse({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
        else:
            for text in chunks:
                self._sse({**base, "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]})
                time.sleep(delay_ms / 1000.0)
            self._sse({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        self.wfile.write(b"data: [DONE]\n\n")

    def _anthropic_stream(self, payload, chunks, delay_ms, tool_call) -> None:
        """Anthropic Messages SSE, which is what the DeepSeek adapter asks for."""
        model = payload.get("model", "mock")
        self._event("message_start", {"type": "message_start", "message": {
            "id": "msg_mock", "type": "message", "role": "assistant", "model": model,
            "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 1, "output_tokens": 0},
        }})
        if tool_call is not None:
            self._event("content_block_start", {"type": "content_block_start", "index": 0,
                                                "content_block": {"type": "text", "text": ""}})
            self._event("content_block_delta", {"type": "content_block_delta", "index": 0,
                                                "delta": {"type": "text_delta", "text": "Working on it."}})
            self._event("content_block_stop", {"type": "content_block_stop", "index": 0})
            self._event("content_block_start", {"type": "content_block_start", "index": 1,
                                                "content_block": {"type": "tool_use", "id": "toolu_spike_1",
                                                                  "name": tool_call["name"], "input": {}}})
            self._event("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {
                "type": "input_json_delta", "partial_json": json.dumps(tool_call["arguments"])}})
            self._event("content_block_stop", {"type": "content_block_stop", "index": 1})
            stop_reason = "tool_use"
        else:
            self._event("content_block_start", {"type": "content_block_start", "index": 0,
                                                "content_block": {"type": "text", "text": ""}})
            for text in chunks:
                self._event("content_block_delta", {"type": "content_block_delta", "index": 0,
                                                    "delta": {"type": "text_delta", "text": text}})
                time.sleep(delay_ms / 1000.0)
            self._event("content_block_stop", {"type": "content_block_stop", "index": 0})
            stop_reason = "end_turn"
        self._event("message_delta", {"type": "message_delta",
                                      "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                                      "usage": {"output_tokens": max(1, len(chunks))}})
        self._event("message_stop", {"type": "message_stop"})


def serve(log_dir: Path, port: int = 0) -> tuple[ThreadingHTTPServer, int]:
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = type("BoundHandler", (Handler,), {"log_dir": log_dir})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-dir", required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    server, port = serve(Path(args.log_dir), args.port)
    print(json.dumps({"port": port}), flush=True)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
