"""A deterministic stand in for an LLM backed FAQ bot, so the example runs without API keys.

Run it with `python server.py` and point juried at http://127.0.0.1:8765/chat. Every fourth
refund question deliberately drops the 14 day detail, so that one scenario shows a flaky
pass rate in the report. `python server.py --sse` streams each reply word by word as
server-sent events in the shape OpenAI compatible endpoints use, for trying juried's
streaming target; `--port` moves it off 8765.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

PORT = 8765

HOURS = "We are open Monday to Friday, 9am to 5pm, and closed at weekends."
REFUND_FULL = "You can return any item within 14 days of delivery for a full refund."
REFUND_VAGUE = "Returns are accepted for a full refund, please contact us to arrange one."
DELIVERY = "Standard delivery takes 3 to 5 working days and costs 3.99."
UNKNOWN = "I'm not sure about that. Please email help@example.com and the team will help."


class Handler(BaseHTTPRequestHandler):
    refund_calls = 0
    lock = threading.Lock()
    sse = False

    def do_POST(self) -> None:
        if self.path != "/chat":
            self.reply(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.reply(400, {"error": "body must be JSON"})
            return
        message = str(payload.get("message", "")).lower()
        history = payload.get("history") or []
        text = answer(message, history)
        # Token counts in the shape an OpenAI compatible endpoint uses, so the example can
        # show juried pricing the target side; a word count stands in for a tokeniser.
        prompt_tokens = len(message.split()) + sum(
            len(str(turn.get("content", "")).split()) for turn in history
        )
        usage = {"prompt_tokens": prompt_tokens, "completion_tokens": len(text.split())}
        if Handler.sse:
            self.stream(text, usage)
            return
        self.reply(200, {"reply": text, "usage": usage})

    # A role only first chunk, one chunk per word, a finish chunk carrying the usage, then
    # [DONE]: the sequence an OpenAI compatible endpoint sends, with a short pause so the
    # time to first token is visibly shorter than the whole reply.
    def stream(self, text: str, usage: dict[str, int]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        chunks: list[dict[str, Any]] = [{"choices": [{"delta": {"role": "assistant"}}]}]
        words = text.split(" ")
        chunks.extend(
            {"choices": [{"delta": {"content": word + (" " if i < len(words) - 1 else "")}}]}
            for i, word in enumerate(words)
        )
        chunks.append({"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": usage})
        for chunk in chunks:
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()
            time.sleep(0.005)
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def reply(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        return None


def answer(message: str, history: list[dict[str, str]]) -> str:
    if any(word in message for word in ("hour", "open", "close", "weekend", "saturday", "sunday")):
        return HOURS
    if any(word in message for word in ("refund", "return", "money back")):
        with Handler.lock:
            Handler.refund_calls += 1
            vague = Handler.refund_calls % 4 == 0
        return REFUND_VAGUE if vague else REFUND_FULL
    if any(word in message for word in ("deliver", "shipping", "post")):
        return DELIVERY
    if history and message.strip() in {"yes", "yes please", "ok", "go on"}:
        return "Of course. " + answer(history[-1].get("content", "").lower(), [])
    return UNKNOWN


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sse", action="store_true", help="stream replies as server-sent events")
    parser.add_argument("--port", type=int, default=PORT, help=f"port to listen on ({PORT})")
    args = parser.parse_args()
    Handler.sse = args.sse
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    mode = " (streaming as SSE)" if args.sse else ""
    print(f"faq-bot listening on http://127.0.0.1:{args.port}/chat{mode}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
