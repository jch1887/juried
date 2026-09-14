# Configuration

The full `juried.toml` reference. The README shows the keys a first run needs.

`juried.toml` lives at the repo root. Any key can be overridden with an environment
variable named `JURIED_<SECTION>_<KEY>`, such as `JURIED_RUN_RUNS=20`. `.env.example`
lists every variable juried reads, including the provider keys and the overrides;
copy it to `.env` and export it from your shell, since juried does not load it itself.

```toml
[target]
url = "https://staging.example.com/api/chat"
headers = { Authorization = "Bearer ${STAGING_TOKEN}" }
body = { message = "{{message}}", history = "{{history}}" }
response_path = "choices.0.message.content"
usage_input_path = "usage.prompt_tokens"       # optional: token counts in the reply
usage_output_path = "usage.completion_tokens"
input_price = 2.0                              # optional: US dollars per million tokens
output_price = 10.0
# cost_per_request = 0.002                     # or a flat price per call
# stream = true                                # the endpoint streams its reply
# stream_format = "sse"                        # or "ndjson"
# stream_path = "choices.0.delta.content"      # text delta in each event

[run]
runs = 20          # attempts per scenario
misses = 1         # failed attempts a scenario may have and still pass
concurrency = 4    # requests in flight to the target, across all scenarios
# concurrency_scope = "global"   # under pytest-xdist, share the caps across workers

[judge]
provider = "anthropic"    # anthropic, openai or stub
model = "claude-sonnet-5" # pinned and recorded with every verdict
concurrency = 4           # requests in flight to the judge, across all scenarios
# base_url = "http://127.0.0.1:11434/v1"   # any OpenAI compatible endpoint, with provider = "openai"
# api_key_env = "OLLAMA_API_KEY"           # variable holding the key, when not the provider's own
```

Set `temperature` under `[judge]` only for a model that accepts it. `claude-sonnet-5` rejects
the parameter, so the example leaves it out; when it is unset nothing is sent and the report
says so.

`base_url` points a provider at another host. With `provider = "openai"` any OpenAI
compatible endpoint works as judge and generator without a new provider: Ollama at
`http://127.0.0.1:11434/v1`, vLLM, LM Studio, OpenRouter, or Azure OpenAI with its
deployment path. `api_key_env` names the environment variable holding the key when it is
not `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`, so a second account or a proxy can have its
own; juried still reads nothing but the process environment, and Ollama ignores the key
but the variable must hold something. Against a custom `base_url` the openai provider sends
`max_tokens` rather than `max_completion_tokens`, which the compatible servers know, and
asks for a JSON schema response as it does of OpenAI; Ollama, vLLM and LM Studio honour
that, and a server that does not fails with the HTTP error it returns. The pinned judge
prompt and the verdict cache are unchanged by the host: a local model is a judge like any
other and needs calibrating like any other.

A body value that is exactly `"{{message}}"` or `"{{history}}"` becomes the scenario
message or the earlier turns as a list of `{role, content}` objects, keeping its type.
Inside longer text `{{message}}` is replaced with the message and `{{history}}` with the
turns as JSON. `${NAME}` anywhere in `url`, `headers` or `body` is replaced with that
environment variable before the request is sent, and the run stops before any request if
the variable is unset. `response_path` is a dotted path into the JSON reply, and so are
`usage_input_path` and `usage_output_path`, which name the token counts in it if the target
reports them; see "What a run costs".

For an endpoint that streams its reply, set `stream = true`, `stream_format` (`sse` for
server-sent events, the shape OpenAI compatible endpoints use, or `ndjson` for one JSON
object per line, as Ollama's own API sends) and `stream_path`, the dotted path to the text
delta in each event, such as `choices.0.delta.content` or `message.content`. juried joins
the deltas into the response, skips events without one (a role preamble, a finish marker,
`[DONE]`), takes token counts from whichever event carries them, and records the time to
the first delta as well as the whole reply; the report shows both. A stream that carries
events but never a delta at `stream_path` stops the scenario with a message showing the
last event, as a bad `response_path` does. Retries and `${NAME}` work as for a plain
endpoint. The example bot streams when started with `python server.py --sse`.

`[generate]` takes `provider`, `model`, `temperature`, `base_url`, `api_key_env`,
`scenarios_per_criterion`, `max_tokens` and `adversarial_pack`. `provider` and `model` default to the judge's,
and so do `base_url` and `api_key_env` while the provider is the same. `temperature` does
not: the judge's is chosen for consistent verdicts and generation wants variety, so it is
unset unless you set it under `[generate]`.
