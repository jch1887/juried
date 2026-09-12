import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest


class FakeBotHandler(BaseHTTPRequestHandler):
    counter = 0
    lock = threading.Lock()

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        message = str(payload.get("message", "")).lower()
        with FakeBotHandler.lock:
            FakeBotHandler.counter += 1
            call = FakeBotHandler.counter
        if self.path == "/broken":
            self.respond(500, {"error": "boom"})
            return
        if "hours" in message or "open" in message:
            reply = "We are open Monday to Friday, 9am to 5pm."
        elif "refund" in message:
            reply = "Refunds are accepted within 14 days." if call % 3 else "Refunds are possible."
        elif "history" in message:
            reply = f"History had {len(payload.get('history', []))} turns."
        else:
            reply = "I'm not sure about that, please contact support."
        self.respond(200, {"reply": reply, "echo": payload})

    def respond(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        return None


@pytest.fixture(scope="session")
def fake_bot_url() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeBotHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
