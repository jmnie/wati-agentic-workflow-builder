# Build Notes

## Problem framing

The assignment is essentially: turn natural-language WATI workflows into
runnable plans. I scoped V1 around the assignment's three example
instructions, then made sure the architecture didn't paint me into a corner
for the dimensions the rubric weighs heaviest:

* **Agent design (30%)** — clean separation of plan / execute / tool
  registry; the LLM is one hop, not the entire control loop.
* **API understanding (25%)** — tool registry mirrors the assignment's
  endpoint reference; iteration is a first-class step kind because most
  realistic workflows are "find ... then act on each".
* **Product thinking (20%)** — destructive plans always preview; dry-run
  doesn't fire side effects; rollback is honest about what's reversible.
* **Code quality (15%)** — typed Pydantic schemas at the boundaries;
  small files; no premature abstraction.
* **Write-up (10%)** — README + ARCHITECTURE + this file.

## How I spent my time (~3 hours)

| Phase | Time | What I did |
| --- | --- | --- |
| Spec read & sketch | ~15 min | Identified the assignment's three example flows and the minimum tool set needed to cover them; sketched the plan/execute split. |
| Mock client + tool registry | ~35 min | Realistic seed data; ABC + factory; tool registry as single source of truth. |
| Planner (LLM + fake) | ~35 min | Forced-tool-use prompt design; rule-based fallback so tests don't need an API key. |
| Executor | ~30 min | Placeholder resolution, iteration, dry-run, rollback. This is the deterministic core; I invested heavily in tests here. |
| Server + chat UI | ~25 min | FastAPI endpoints + a single-file vanilla-JS chat with confirm/dry-run/cancel buttons. |
| CLI | ~10 min | Typer + Rich panels for plan previews. |
| Tests | ~30 min | 83 tests, 79% coverage. Spent the bulk on executor edge cases (rollback, dry-run, iteration over non-list, simulated API failures). |
| Docs | ~15 min | README, ARCHITECTURE, BUILD_NOTES. |

## What I prioritised

1. **A clean plan/execute split.** The LLM gets one job (produce a Plan); the
   executor is plain Python. I can debug plan execution without re-rolling
   the dice on the model.
2. **Tool registry as single source of truth.** Adding a new WATI endpoint
   is one entry — the LLM prompt, the executor dispatch, and the JSON Schema
   all update from it.
3. **Honest UX around side effects.** Every destructive step is flagged in
   the preview; dry-run actually doesn't send anything; rollback only claims
   to undo what it can.
4. **Tests first for the deterministic core.** I have an LLM-free path (the
   `FakePlanner`) that exercises the same agent code paths in CI.

## What I intentionally did NOT build

* **Persistent state.** Sessions live in process memory. A real deployment
  needs a session store (Redis / Postgres) and stateless API instances.
* **Streaming.** One JSON blob per turn. Streaming would be a nice UX
  upgrade for long plans but adds complexity I didn't need for a demo.
* **Memory summarisation.** When the deque fills up, oldest turns drop. A
  recursive summariser would buy us longer sessions; out of scope.
* **Snapshots for `update_contact_attributes`.** Without a snapshot we can't
  invert it; rather than ship a half-rollback I marked the limitation in
  the tool's notes (which surface in the plan preview).
* **Operator-side ergonomics.** No "save this workflow as a recipe" or
  scheduled execution.
* **Auth / multi-tenant.** Single-user, local-only. CORS is wide open.
* **Real-API integration tests.** The `RealWatiClient` is a thin httpx
  layer — without sandbox creds I couldn't run it, but the mock client
  implements the same interface so swapping it in is a config change.

## V2 roadmap

In rough priority order:

1. **Persistent sessions** + JWT auth on the API (multi-user demos).
2. **Snapshot-based rollback** for attribute writes (and, eventually, for
   any tool with a "read current state" pre-step the planner can insert
   automatically when the user requests rollback support).
3. **Streaming chat responses** with token-level updates and a per-step
   progress indicator during execution.
4. **Idempotency keys** on every WATI write, so retries are safe.
5. **Batching + pagination awareness.** "Send to all VIP contacts" should
   chunk + report progress + respect WATI rate limits, not naively iterate.
6. **Memory summarisation.** Compact older turns into a running summary so
   long sessions stay coherent without exploding the prompt.
7. **Recipe store.** Let users save tested plans as named workflows that
   can be triggered via webhook or schedule.
8. **Eval harness.** Curated set of "user said X → expected plan Y" pairs
   to regression-test prompt + tool-registry changes.

## Trade-offs worth noting

* **Forced tool use vs. JSON-mode.** I chose tool-use over JSON-mode because
  the model's tool input is validated against `input_schema` and we get clean
  failure modes when the model deviates. Both Anthropic (native) and OpenAI
  (and OpenAI-compatible providers) implement this, so the planner's
  `LLMBackend` abstraction can pick a backend at runtime without losing the
  guarantee.

* **Iteration as a first-class step kind vs. inline `$step.N` references.**
  I considered letting any arg reference any prior step's output (`$step.1[*].wAid`
  inside a list), but that pushes complex JSONPath into the planner. A single
  `for_each` field on a step is dumber and easier to model — and it covers
  every pattern in the assignment.

* **Mock parity vs. real-API integration.** The mock client mirrors the
  real API's surface and shape, but I haven't seeded every edge case
  (rate-limit responses, partial-success batch responses, malformed
  template parameters). For a 3-hour demo, mock parity is enough; the
  ABC means the real client can override behaviour without touching the
  agent.

* **`FakePlanner` rules vs. removing it.** The rule-based planner doubled
  as both an offline demo path and a deterministic test substrate. The
  alternative was mocking the Anthropic SDK in tests, which is more
  fragile. The cost is a few hundred lines of regex that won't generalise
  beyond the canonical instructions — but that's a feature: it forces real
  user instructions through the LLM, where they belong.

* **Confirmation-by-text vs. confirmation-by-button.** The CLI uses text
  ("yes" / "dry run" / "no"); the web UI exposes both text and buttons.
  Text is what would matter on a WhatsApp-bot interface — buttons are a
  shortcut on the web.
