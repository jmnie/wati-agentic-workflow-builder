# Architecture

## Goals

1. Take a plain-English instruction and turn it into one or more WATI API
   calls in the right order.
2. Keep the LLM out of the hot path: planning is one structured call,
   execution is plain Python.
3. Make every "would I really run this?" decision an explicit step the user
   can see before any side effects happen.
4. Be testable end-to-end without a network or an API key.

## Component map

```
┌──────────────────────────────────────────────────────────────────────┐
│                              Agent                                    │
│  ┌───────────┐    ┌───────────┐    ┌───────────┐    ┌─────────────┐  │
│  │  Memory   │───▶│  Planner  │───▶│  Plan     │───▶│  Executor   │  │
│  │ (per      │    │  (LLM +   │    │ (Pydantic │    │ (rollback,  │  │
│  │  session) │    │   fake)   │    │  schema)  │    │  dry-run)   │  │
│  └───────────┘    └───────────┘    └───────────┘    └──────┬──────┘  │
└─────────────────────────────────────────────────────────────┼─────────┘
                                                              ▼
                                                       ┌─────────────┐
                                                       │ ToolRegistry│
                                                       └──────┬──────┘
                                                              ▼
                                                       ┌─────────────┐
                                                       │ WatiClient  │
                                                       │ mock | real │
                                                       └─────────────┘
```

* **Agent** — public façade (`chat`, `confirm`, `reset`). Owns one Memory
  and tracks at most one pending plan per session.
* **Memory** — bounded deque of `(role, content)` turns. No summarisation in
  V1 — long-running sessions just lose oldest turns.
* **Planner** — Protocol with two implementations:
  * `LLMPlanner` delegates to an `LLMBackend` (see below). Both backends use
    forced tool-/function-calling with an `input_schema` derived from
    `Plan.model_json_schema()`, so the model is required to emit a structurally
    valid plan rather than free prose.
  * `FakePlanner` is rule-based; it covers the assignment's three example
    requests so the agent works offline and tests stay deterministic.
* **LLMBackend** — single Protocol with two implementations
  (`agent/llm.py`):
  * `AnthropicBackend` — native Claude Messages API with prompt caching on
    the system prompt.
  * `OpenAICompatibleBackend` — any chat-completions endpoint speaking the
    OpenAI tool-calling shape: OpenAI itself, DeepSeek, Moonshot (Kimi),
    Together, Groq, OpenRouter, Ollama, vLLM, etc. `base_url` is the only
    config knob that changes between them.

  The factory (`build_backend`) returns `None` on any init failure (missing
  key, missing SDK, unknown provider, env-level proxy issues), and the
  planner falls back to `FakePlanner` so the agent stays usable.
* **Plan / Step / ExecutionReport** — Pydantic models in `agent/schemas.py`.
  The Plan is the contract between the planner and executor.
* **Executor** — turns a Plan into API calls. Handles placeholder resolution,
  iteration over a previous step's list output, dry-run mode, rollback, and
  malformed-plan failures.
* **ToolRegistry** — list of `Tool` dataclasses (`agent/tools.py`). Each tool
  is the single source of truth for: its name + description + JSON Schema (used
  in the LLM prompt), its handler, its `destructive` flag, and its inverse
  (used by the rollback engine).
* **WatiClient** — ABC with `MockWatiClient` and `RealWatiClient`. The agent
  never talks to anything else.

## Data flow for one turn

```
user_message ─▶ Agent.chat
                  │
                  ├── if pending plan: classify yes/dry/no/new
                  │     └─ run / dry-run / cancel / fall through
                  │
                  ├── memory.add_user
                  ├── plan = planner.plan(message, memory)
                  │
                  ├── if plan.needs_clarification:
                  │     └─ return CLARIFICATION (no execution)
                  │
                  ├── if plan.steps == []:
                  │     └─ return CHITCHAT
                  │
                  ├── if plan.requires_confirmation or has_destructive_step:
                  │     └─ stash plan, return PLAN_PREVIEW
                  │
                  └── else: Executor.execute(plan) ─▶ EXECUTION
```

## Plan schema

```python
class Step(BaseModel):
    id: int                          # 1-indexed, contiguous
    tool: str                        # registered tool name
    args: dict[str, Any]             # may contain placeholders
    description: str                 # human-readable, shown in preview
    destructive: bool = False
    for_each: int | None = None      # iterate over step <for_each>.output

class Plan(BaseModel):
    summary: str
    steps: list[Step]
    needs_clarification: list[str]   # questions to ask user
    requires_confirmation: bool      # forces preview even for non-destructive
    notes: list[str]                 # caveats shown in preview
```

### Placeholder substitution

A step can reference the iteration item via `$item` or `$item.<field>`. The
executor materialises arguments at run time, once per iteration:

```yaml
step 1: find_contacts(tag="VIP")        # output: list[contact]
step 2: send_template_message
        for_each: 1
        args:
          whatsapp_number: "$item.wAid"
          template_name: "renewal_reminder"
          parameters: [{name: "body_1", value: "$item.fullName"}]
```

Whole-string substitutions preserve the original Python type; inline ones
produce a string. This keeps the LLM's job simple ("just write
`$item.wAid`") and the runtime predictable.

### Execution guardrails

The LLM is required to emit a structured `Plan`, but the executor still treats
the plan as untrusted input. If the model produces an unknown tool, omits a
required argument, references `$item` outside a `for_each` step, or points
`for_each` at a step that did not produce a list, the executor returns a failed
`ExecutionReport` instead of crashing the CLI or API server.

This is intentionally handled in the execution layer rather than only in the
prompt. Prompt instructions reduce bad plans; runtime validation keeps the demo
usable when a bad plan still slips through.

## Confirmation & dry-run

* **Preview** — any plan with a destructive step (or `requires_confirmation`)
  pauses for explicit confirmation.
* **Dry-run** — read-only tools still execute (so iteration steps see real
  data); destructive tools record the resolved args without calling. This
  preserves the *shape* of the run while guaranteeing zero side effects.
* **Cancel** — discards the pending plan; memory is preserved so the user
  can reword.

## Rollback

* Each tool optionally defines an `inverse` function: `args, result → InverseCall | None`.
* After a successful step, the executor records the inverse.
* On failure, it walks the recorded list backwards, executing each inverse
  best-effort (logging but not aborting on individual rollback failures).
* Tools without an inverse (sending messages, broadcasts, attribute writes
  without a snapshot) flag the limitation in their `notes`, which surfaces
  in the plan preview before any commit.

This keeps rollback honest: we only claim to undo what's actually
undoable. The user sees the limits up front, not after the fact.

## Why this split?

* **LLM ⊥ Execution.** The LLM is non-deterministic; the executor is plain
  Python with full unit tests. Bugs in plan execution are reproducible without
  rerunning the model.
* **Tool registry as single source of truth.** Adding a new endpoint means
  adding one `Tool` entry — the planner's prompt, the executor's dispatch,
  and the schema all update automatically.
* **Mock parity.** The mock client implements the same ABC as the real
  client, so swapping backends is a config change. Tests pin behaviour
  against the mock; integration with the live sandbox needs only a token.

## What's intentionally not here (V2 candidates)

* Persistent sessions / memory store. V1 is process-local.
* Conversation summarisation when memory hits its bound.
* Streaming responses to the UI (we currently send one JSON blob per turn).
* Authn/authz on the API (no users, no rate limits).
* Idempotency keys and retries on the client.
* Pagination / batch progress reporting for very large fan-outs.
* Inverse snapshots for `update_contact_attributes` (would let us roll those
  back too).

## LLM-prompt design notes

* **Tool catalog** is rendered into the system prompt by `tool_catalog_for_prompt()`,
  which iterates the registry. Each tool lists its parameters, types, and
  `[destructive]` / `[invertible]` flags.
* **Forced structured output.** We use `tool_choice={"type":"tool"}` so the
  model *must* call `submit_plan`. The tool's `input_schema` is the Pydantic
  schema for `Plan`. This eliminates an entire class of "model returned prose
  instead of JSON" failures.
* **Cache control.** The system prompt is sent with
  `cache_control: ephemeral` on every turn, so multi-turn sessions don't
  re-pay the prompt-token cost.
* **Examples.** The prompt includes two worked examples (the assignment's
  illustrative requests). They establish: how to compose `find_contacts +
  for_each + send_template_message`, and how to compose `assign_team +
  add_tag` for an "escalate" intent.

## Why Claude Sonnet 4.6 by default?

Planning is a structured-output task with light reasoning; Sonnet 4.6 is the
cost/quality sweet spot — Opus is overkill, Haiku occasionally fumbles
multi-step plans with iteration. The model is configurable via `LLM_MODEL`.

## Why two LLM backends rather than just one?

Routing Claude through an OpenAI-compatibility translation layer would mean
losing prompt caching (Anthropic-only) and giving up clean error messages
when the native API rejects something. So Anthropic gets a native backend.

Everything else gets pooled behind `OpenAICompatibleBackend`, because the
OpenAI tool-calling API is now the lingua franca for hosted LLMs and most
providers expose it directly. Two backends, dozens of supported endpoints.
