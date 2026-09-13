import asyncio
import json
from typing import Any

import httpx
import pytest

from juried.config import TargetConfig
from juried.scenarios import Turn
from juried.targets.http import (
    HttpTarget,
    TargetConfigError,
    expand_env,
    extract_path,
    render_body,
)
from juried.transport import TransportFailure


def test_render_body_substitutes_placeholders() -> None:
    template = {
        "input": "Q: {{message}}",
        "history": "{{history}}",
        "options": {"stream": False, "tags": ["{{message}}", 1]},
    }
    history = [Turn(role="user", content="hi"), Turn(role="assistant", content="hello")]
    rendered = render_body(template, "what?", history)
    assert rendered == {
        "input": "Q: what?",
        "history": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ],
        "options": {"stream": False, "tags": ["what?", 1]},
    }


def test_extract_path() -> None:
    data = {"choices": [{"message": {"content": "hi"}}], "reply": "x"}
    assert extract_path(data, "choices.0.message.content") == "hi"
    assert extract_path(data, "reply") == "x"
    with pytest.raises(TargetConfigError, match="not found"):
        extract_path(data, "choices.0.text")
    with pytest.raises(TargetConfigError, match="out of range"):
        extract_path(data, "choices.3")


def test_expand_env() -> None:
    headers = {"Authorization": "Bearer ${TOKEN}", "X-Plain": "value"}
    assert expand_env(headers, {"TOKEN": "abc"}) == {
        "Authorization": "Bearer abc",
        "X-Plain": "value",
    }
    with pytest.raises(TargetConfigError, match="TOKEN"):
        expand_env(headers, {})
    assert expand_env("https://${HOST}/v1/${PATH}", {"HOST": "h", "PATH": "p"}) == "https://h/v1/p"
    nested = {"tenant": "${TENANT}", "options": [{"key": "${KEY}"}, 3, None], "flag": True}
    assert expand_env(nested, {"TENANT": "t", "KEY": "k"}) == {
        "tenant": "t",
        "options": [{"key": "k"}, 3, None],
        "flag": True,
    }


def test_placeholders_follow_one_rule() -> None:
    history = [Turn(role="user", content="hi"), Turn(role="assistant", content='say "yes"')]
    template = {
        "exact_message": "{{message}}",
        "exact_history": "{{history}}",
        "inline_message": "User said: {{message}}!",
        "inline_history": "Context: {{history}}",
        "both": "{{history}} then {{message}}",
    }
    rendered = render_body(template, "what?", history)
    turns = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": 'say "yes"'}]
    assert rendered["exact_message"] == "what?"
    assert rendered["exact_history"] == turns
    assert rendered["inline_message"] == "User said: what?!"
    assert rendered["inline_history"] == "Context: " + json.dumps(turns)
    assert json.loads(rendered["both"].split(" then ")[0]) == turns


def make_target(
    handler: Any,
    retries: int = 2,
    response_path: str = "reply",
    environ: dict[str, str] | None = None,
    usage_paths: tuple[str, str] | None = None,
    stream: tuple[str, str] | None = None,
) -> tuple[HttpTarget, httpx.AsyncClient]:
    config = TargetConfig(
        url="http://bot.test/chat",
        headers={"X-Key": "${KEY}"},
        response_path=response_path,
        retries=retries,
        usage_input_path=None if usage_paths is None else usage_paths[0],
        usage_output_path=None if usage_paths is None else usage_paths[1],
        stream=stream is not None,
        stream_format="sse" if stream is None else stream[0],  # type: ignore[arg-type]
        stream_path=None if stream is None else stream[1],
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpTarget(config, client, environ=environ or {"KEY": "secret"}), client


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_send_success_and_header_expansion() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"reply": "hello there"})

    target, _ = make_target(handler)
    response = run(target.send("hi", [Turn(role="user", content="earlier")]))
    assert response.text == "hello there"
    assert response.status_code == 200
    assert response.elapsed_ms >= 0
    assert response.bytes == len(json.dumps({"reply": "hello there"}, separators=(",", ":")))
    assert response.input_tokens is None and response.output_tokens is None
    assert seen["headers"]["x-key"] == "secret"
    assert seen["body"] == {"message": "hi", "history": [{"role": "user", "content": "earlier"}]}


def test_token_counts_are_read_from_usage_paths() -> None:
    payload = {"choices": [{"message": {"content": "hi"}}], "usage": {"in": 41, "out": 7.0}}
    target, _ = make_target(
        lambda request: httpx.Response(200, json=payload),
        response_path="choices.0.message.content",
        usage_paths=("usage.in", "usage.out"),
    )
    response = run(target.send("hi", []))
    assert (response.input_tokens, response.output_tokens) == (41, 7)
    missing, _ = make_target(
        lambda request: httpx.Response(200, json={"reply": "x", "usage": {"in": 1}}),
        usage_paths=("usage.in", "usage.out"),
    )
    with pytest.raises(TargetConfigError, match=r"usage_output_path 'usage\.out': key 'out'"):
        run(missing.send("hi", []))
    wrong, _ = make_target(
        lambda request: httpx.Response(200, json={"reply": "x", "usage": {"in": "41", "out": 1}}),
        usage_paths=("usage.in", "usage.out"),
    )
    with pytest.raises(TargetConfigError, match=r"usage_input_path 'usage\.in' did not select"):
        run(wrong.send("hi", []))


def test_env_expands_in_url_and_body_but_not_fingerprint() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"reply": "ok"})

    config = TargetConfig(
        url="http://${HOST}/chat",
        body={"message": "{{message}}", "tenant": "${TENANT}", "nested": {"key": "${KEY}"}},
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    environ = {"HOST": "bot.test", "TENANT": "acme", "KEY": "s3cret"}
    target = HttpTarget(config, client, environ=environ)
    run(target.send("hi", []))
    assert seen["url"] == "http://bot.test/chat"
    assert seen["body"] == {"message": "hi", "tenant": "acme", "nested": {"key": "s3cret"}}
    assert "s3cret" not in target.fingerprint()
    assert "acme" not in target.fingerprint()
    assert "${HOST}" in target.fingerprint()
    with pytest.raises(TargetConfigError, match="KEY"):
        HttpTarget(config, client, environ={"HOST": "h", "TENANT": "t"})


def test_retries_on_transport_error_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("juried.transport.asyncio.sleep", no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("refused")
        if calls == 2:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"reply": "ok"})

    target, _ = make_target(handler, retries=2)
    assert run(target.send("hi", [])).text == "ok"
    assert calls == 3


def test_server_error_is_transport_failure_without_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, text="internal error")

    target, _ = make_target(handler, retries=3)
    with pytest.raises(TransportFailure, match="HTTP 500") as info:
        run(target.send("hi", []))
    assert info.value.status_code == 500
    assert calls == 1


def test_exhausted_retries_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("juried.transport.asyncio.sleep", no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    target, _ = make_target(handler, retries=1)
    with pytest.raises(TransportFailure, match="ReadTimeout"):
        run(target.send("hi", []))


def test_non_json_and_non_string_responses() -> None:
    target, _ = make_target(lambda request: httpx.Response(200, text="<html>"))
    with pytest.raises(TransportFailure, match="not JSON"):
        run(target.send("hi", []))
    target, _ = make_target(lambda request: httpx.Response(200, json={"reply": 3}))
    with pytest.raises(TargetConfigError, match="did not select a string"):
        run(target.send("hi", []))


def test_against_threaded_fake_bot(fake_bot_url: str) -> None:
    async def go() -> str:
        async with httpx.AsyncClient() as client:
            target = HttpTarget(TargetConfig(url=f"{fake_bot_url}/chat"), client, environ={})
            return (await target.send("what are your hours?", [])).text

    assert "9am to 5pm" in run(go())


def sse(*events: Any, done: bool = True) -> bytes:
    body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    return (body + ("data: [DONE]\n\n" if done else "")).encode()


def test_sse_stream_is_joined_and_timed() -> None:
    chunks = [
        {"choices": [{"delta": {"role": "assistant"}}]},
        {"choices": [{"delta": {"content": "Open "}}]},
        {"choices": [{"delta": {"content": "9am."}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"in": 12, "out": 3}},
    ]
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, content=sse(*chunks), headers={"content-type": "text/event-stream"}
        )

    target, _ = make_target(
        handler, usage_paths=("usage.in", "usage.out"), stream=("sse", "choices.0.delta.content")
    )
    response = run(target.send("hours?", []))
    assert response.text == "Open 9am."
    assert response.status_code == 200
    assert (response.input_tokens, response.output_tokens) == (12, 3)
    assert response.first_token_ms is not None
    assert 0 <= response.first_token_ms <= response.elapsed_ms
    payload = len(sse(*chunks)) - len(b"data: [DONE]\n\n")
    assert payload - 8 <= response.bytes <= payload
    assert seen["body"]["message"] == "hours?"


def test_sse_parsing_follows_the_spec() -> None:
    body = (
        b": keep-alive comment\n"
        b"event: message\n"
        b"id: 1\n"
        b'data: {"text":\n'
        b'data: "two lines"}\n'
        b"\n"
        b'data:{"text": " no space"}\n\n'
        b'data: {"text": " last"}'
    )
    target, _ = make_target(
        lambda request: httpx.Response(200, content=body), stream=("sse", "text")
    )
    assert run(target.send("x", [])).text == "two lines no space last"


def test_ndjson_stream() -> None:
    body = (
        b'{"message": {"content": "Hello"}, "done": false}\n\n'
        b'{"message": {"content": " there"}, "done": true, "eval_count": 5, '
        b'"prompt_eval_count": 9}\n'
    )
    target, _ = make_target(
        lambda request: httpx.Response(200, content=body),
        usage_paths=("prompt_eval_count", "eval_count"),
        stream=("ndjson", "message.content"),
    )
    response = run(target.send("x", []))
    assert response.text == "Hello there"
    assert (response.input_tokens, response.output_tokens) == (9, 5)


def test_stream_errors_are_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("juried.transport.asyncio.sleep", no_sleep)
    empty, _ = make_target(lambda request: httpx.Response(200, content=b""), stream=("sse", "t"))
    with pytest.raises(TransportFailure, match="carried no events"):
        run(empty.send("x", []))
    no_text, _ = make_target(
        lambda request: httpx.Response(200, content=sse({"other": 1})), stream=("sse", "t")
    )
    with pytest.raises(TargetConfigError, match=r"stream_path 't' selected no text in 1 event"):
        run(no_text.send("x", []))
    bad_json, _ = make_target(
        lambda request: httpx.Response(200, content=b"data: {nope\n\n"), stream=("sse", "t")
    )
    with pytest.raises(TransportFailure, match="is not JSON"):
        run(bad_json.send("x", []))
    calls = 0

    def flaky(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, content=sse({"t": "ok"}))

    retried, _ = make_target(flaky, retries=1, stream=("sse", "t"))
    assert run(retried.send("x", [])).text == "ok"
    assert calls == 2
    failing, _ = make_target(lambda request: httpx.Response(500, text="boom"), stream=("sse", "t"))
    with pytest.raises(TransportFailure, match="HTTP 500") as info:
        run(failing.send("x", []))
    assert info.value.status_code == 500
