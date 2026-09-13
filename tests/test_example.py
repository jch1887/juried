"""The example project's fake bot in its streaming mode, driven through the real target."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from juried.config import TargetConfig
from juried.targets.http import HttpTarget

SERVER = Path(__file__).resolve().parents[1] / "examples" / "faq-bot" / "server.py"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def sse_bot_url() -> Iterator[str]:
    port = free_port()
    process = subprocess.Popen(
        [sys.executable, str(SERVER), "--sse", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("the example server did not start")
        yield f"http://127.0.0.1:{port}/chat"
    finally:
        process.kill()
        process.wait()


def test_example_server_streams_sse(sse_bot_url: str) -> None:
    config = TargetConfig(
        url=sse_bot_url,
        stream=True,
        stream_format="sse",
        stream_path="choices.0.delta.content",
        usage_input_path="usage.prompt_tokens",
        usage_output_path="usage.completion_tokens",
    )

    async def go() -> tuple[str, float | None, float, int | None]:
        async with httpx.AsyncClient() as client:
            target = HttpTarget(config, client, environ={})
            response = await target.send("what are your opening hours?", [])
            return (
                response.text,
                response.first_token_ms,
                response.elapsed_ms,
                response.output_tokens,
            )

    import asyncio

    text, first, total, output_tokens = asyncio.run(go())
    assert text == "We are open Monday to Friday, 9am to 5pm, and closed at weekends."
    assert first is not None and first < total
    assert output_tokens == len(text.split())


def test_example_streams_end_to_end_under_pytest(
    pytester: pytest.Pytester, sse_bot_url: str
) -> None:
    pytester.makefile(
        ".toml",
        juried=f"""
[target]
url = "{sse_bot_url}"
stream = true
stream_path = "choices.0.delta.content"

[run]
runs = 3

[judge]
provider = "stub"
""",
    )
    pytester.makefile(".md", acceptance="# Criteria\n\n## Opening hours\nStates the hours.\n")
    scenarios = pytester.mkdir("scenarios")
    (scenarios / "hours.yaml").write_text(
        "criterion: opening-hours\nscenarios:\n  - name: Asks hours\n"
        '    message: what are your opening hours?\n    expected: Mentions "9am" and "5pm".\n'
    )
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)
    report = json.loads((pytester.path / "reports" / "juried-report.json").read_text())
    scenario = report["criteria"][0]["scenarios"][0]
    assert scenario["latency"]["first_token"]["measured"] == 3
    assert scenario["latency"]["first_token"]["mean_ms"] < scenario["latency"]["mean_ms"]
    assert all(a["first_token_ms"] is not None for a in scenario["attempts"])
    assert "first token" in (pytester.path / "reports" / "juried-report.html").read_text()
