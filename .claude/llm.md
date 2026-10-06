# LLM Integration Rules

Apply when the product calls a language model. Distilled from ZenTallyBot
(D-006, D-087, D-098, testing rules). For classic ML training see
`machine-learning.md`.

## 1. One module owns the model

All calls live in one module (`ai_service.py` or `llm_client.py`): client
setup, model name from config, timeouts, retries, prompt templates. No
vendor SDK import anywhere else. Swapping the provider touches one file.

## 2. Structured output only

Every call requests JSON (`response_format={"type": "json_object"}` or the
provider's equivalent) and validates the result against a schema before
use. Free-text parsing is forbidden. A response that fails validation is
logged and treated as a failed call, not guessed at.

## 3. Every call is traced

Table `llm_traces` (or equivalent) with: `ts_utc`, `call_type`, `model`
(the model the response actually reports, not the configured one),
`latency_ms`, `status` (`ok` / `failed` / `timeout`), token counts, a
`request_id`, the user/tenant id, and a hash or truncated copy of prompt
and response. Cost, latency and error rate are read from this table, never
estimated.

## 4. Untrusted input is data, not instructions

Any text from users, documents or third parties that goes into a prompt is
wrapped in explicit delimiters (`⟦ ⟧` or similar) by one helper
(`wrap_user_input()`), which also strips those delimiters from the input
and neutralises code fences. Every prompt carries a preamble stating that
delimited content is inert data. This applies to internal pipelines too:
if an LLM output or a user message ever reaches an agent with write access
(a QA bot, a code agent), it is wrapped there as well (ZenTallyBot D-098).

## 5. Prompts are code

- Prompts live in the LLM module as named constants or templates, versioned
  with the code. Changes to a prompt get a decision entry when they change
  behaviour.
- Language selection is explicit: the prompt includes language rules for
  the resolved language; the model never guesses.
- Tests that assert prompt *structure* (required sections, placeholders)
  are fixed by updating the test, never by editing the prompt to fit the
  test (ZenTallyBot D-004).

## 6. Confidence over questions

Prefer a best-effort answer with a stated confidence and an easy correction
path over asking the user a clarifying question. Log corrections; they are
the training signal for the next prompt iteration.

## 7. Timeouts and fallbacks

Every call has a timeout (30 s default). On timeout or failure the user
gets a specific message from `strings.py`, the trace row records the
failure, and no partial data is persisted.

## 8. Tests

| Kind | Files | Runs |
|------|-------|------|
| Prompt structure | `test_<module>_prompts.py` | Always, no API key |
| Behaviour with mocked model | `test_<feature>.py` with a stubbed client | Always |
| Live model | `test_*_llm.py` | **Never by Claude.** Owner or mentor, manually, with a real key. |

- Live tests: `~5` examples per behaviour per language, each real input
  appears in exactly one file, borderline cases assert `in (a, b)` rather
  than forcing the prompt.
- The stubbed client is the default in `conftest.py`; a live call in a unit
  test is a failure.

## 9. Shadow first

A new prompt, model or gate ships in shadow mode: it runs, logs what it
would have answered, and the old path stays live until the trace table
shows it is better. Then switch. Withdrawal is a config change.

## 10. Cost is a metric

Track spend per call type per day from `llm_traces`. A requirement that
adds LLM calls names the expected call volume and the budget.
