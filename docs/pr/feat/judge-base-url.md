# B1: OpenAI-compatible endpoints for the judge

`base_url` under `[judge]` already existed and both providers honoured it, so most of this
is `api_key_env`, making the openai client work against the servers people actually run
behind a custom URL, and giving the live workflow a way to prove it.

## What changed

- `[judge] api_key_env` and `[generate] api_key_env`: the environment variable holding the
  key, defaulting to the provider's own (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`). Generation
  inherits the judge's value while the provider is the same, exactly as `base_url` does.
  Blank `base_url` or `api_key_env` values are rejected with a message; surrounding
  whitespace is stripped. Both keys are in the README example, `.env.example`, and the
  `init` template; `JURIED_JUDGE_API_KEY_ENV` and `JURIED_GENERATE_API_KEY_ENV` override
  them like any other key.
- `LLMProvider` takes `api_key_env` and reads the key through one `api_key()` method;
  `build_provider` passes it through from the CLI and the pytest plugin.
- The openai client sends `max_tokens` when `base_url` is anything but
  `https://api.openai.com/v1`, and `max_completion_tokens` otherwise. The JSON schema
  `response_format` is sent in both cases.
- Live contract test: one judge request to a local Ollama through the openai client,
  keyed by `JURIED_LIVE_OLLAMA_BASE_URL` (default `http://127.0.0.1:11434/v1`) and
  `JURIED_LIVE_OLLAMA_MODEL` (default `llama3.2`). It checks the model list first and skips
  with a reason when the server or model is absent, and asserts the contract (request
  accepted, verdict parsed, token counts present) rather than the verdict, since a small
  model may misjudge. The workflow passes the two variables, fails only on a missing
  provider key as before, and prints a `::notice::` when the Ollama test was skipped.
- README: Install line, the `[judge]` example, a paragraph on compatible endpoints under
  Configuration, `[generate]` keys, and the Development note on the workflow.
  CONTRIBUTING documents the new variables.

## Decisions the brief did not cover

- The brief says `api_key_env` defaults to `OPENAI_API_KEY`; for the anthropic provider
  the default is `ANTHROPIC_API_KEY`, which is what "the provider's own" means.
- `max_tokens` versus `max_completion_tokens` is decided by whether `base_url` is OpenAI's
  own host. OpenAI's reasoning models reject `max_tokens`; Ollama, vLLM and LM Studio
  mostly reject or ignore `max_completion_tokens`. A host based switch is a heuristic, and
  the README says so in effect by naming the servers it was written for.
- Ollama ignores the Authorization header but juried still insists the named variable is
  set, so no code path sends a request with no key by accident. The docs say to set it to
  anything.
- The Ollama live test asserts the contract, not that a small local model judges
  correctly.

## Checks

`make check` (ruff, mypy, pytest): 178 passed. The machine this was written on has no
Ollama, so the live test was run in live mode without keys to check the skip path: it
skipped with "no Ollama at http://127.0.0.1:11434: the OpenAI compatible endpoint test did
not run", the text the workflow turns into a notice. The request shape against a
compatible endpoint is covered by a mocked test; a real Ollama has not yet answered it.
