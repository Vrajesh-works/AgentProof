# AgentProof - Product Doc

## Problem

Teams shipping tool-using AI agents (support bots that look up customers and
issue refunds, booking agents, internal workflow agents) change prompts,
models, and tool schemas every week. Each change can silently break behavior
that used to work: the agent refunds the wrong amount, skips the lookup step,
or succeeds 3 runs out of 4 instead of 4 out of 4. Unit tests do not catch
this because the failure is in the agent's choices, not in the tool code, and
a single manual run hides flakiness.

There is no cheap, repeatable way to answer "is this version of the agent at
least as reliable as the last one?" before merging.

## Target users

- **Agent engineers / forward deployed engineers** who own an agent in
  production and need a regression gate on every PR.
- **Eval / platform engineers** who maintain task suites and want reliability
  metrics (pass@k, pass^k) instead of a single pass rate.
- **Reviewers** who want to see exactly which step of which trial went wrong
  (tool call, arguments, tokens, latency, cost) without rerunning anything.

Initial domain: enterprise customer operations (billing lookups, refunds,
order cancellations, flight changes), where a wrong tool call moves money.

## Core features (MVP, built today)

1. **YAML task suites** - each task declares the user instruction, the
   initial environment state, the allowed tools, and the expected outcome
   (end-state diff, required tool-call order, output schema, open-answer
   rubric). Two suites ship: a tau-bench style retail/airline suite on a
   built-in sandboxed environment, and a suite that drives the QuilrAI
   customer billing MCP server over stdio.
2. **Deterministic checkers first** - state diff (expected changes and no
   unexpected side effects), JSON schema match on the final answer, tool-call
   order (subsequence or exact), forbidden tools, and substring/regex checks.
   An **LLM judge** exists only for open-ended answers, is pluggable, and has
   an agreement report (accuracy and Cohen's kappa) against hand labels.
3. **Repeated trials and reliability metrics** - every task runs k times;
   AgentProof reports pass@k (at least one success) and pass^k (all k
   succeed, the tau-bench reliability metric) so flaky agents show up.
4. **Per-step traces** - every trial emits OpenTelemetry spans for the agent
   step, each model call (tokens, latency, cost) and each tool call
   (arguments, result, latency, error), stored with the run and optionally
   exported over OTLP.
5. **Run viewer (React + TypeScript)** - runs list, run detail with per-task
   pass@k / pass^k, trial timeline with the span waterfall, and a **version
   diff view** comparing two runs task by task.
6. **CI gate** - a CLI `agentproof gate` and a reusable GitHub Action that
   runs a suite on a PR, compares it to a committed baseline, posts a
   markdown summary, and fails the job when any score drops by more than the
   allowed tolerance.
7. **Sandboxed tools with timeouts and budget caps** - per tool call
   timeout, per trial wall clock, max steps, max tool calls, max tokens and
   max cost in USD. MCP servers run as child processes with resource limits
   and are killed on timeout.
8. **Model adapters** - Claude and OpenAI behind one interface, configured
   by environment variables. A deterministic scripted agent (with seeded,
   configurable fault injection) lets CI and tests run with no API key.

## Non-goals (for this version)

- Hosting agents or acting as an agent framework. AgentProof only drives an
  agent through an adapter and observes it.
- Re-implementing the full tau-bench user simulator. Tasks are single-turn
  instructions; multi-turn simulated users are future work.
- Cloud deployment (AWS, Azure, managed databases, Terraform/Bicep). The
  storage, trace export, and runner layers are adapters so cloud backends can
  be added later; see `docs/PLAN.md` "Future work".
- Multi-tenant auth, RBAC, or billing.
- Modifying the systems under test. The QuilrAI MCP server is launched from
  a user-configured checkout, never vendored or changed.

## Success metrics

- **Gate correctness:** the GitHub Action fails a PR whose agent drops any
  task's pass^k by more than the tolerance and passes an unchanged PR, shown
  by integration tests and by the repo's own CI.
- **Flakiness visibility:** a seeded flaky agent produces pass@k = 1.0 and
  pass^k < 1.0 on the same task, and the viewer shows the failing trial.
- **Determinism:** two runs of the scripted agent with the same seed give
  byte-identical scores.
- **Zero-key CI:** the full test suite and the gate run without any model
  API key.
- **Time to first result:** `make up` (or `docker compose up`) starts the API,
  database, viewer and a seeded demo run in under 2 minutes on a laptop.
- **Judge trust:** the LLM judge agreement report prints accuracy and kappa
  against the shipped hand labels; the judge is only used where deterministic
  checks cannot decide.
