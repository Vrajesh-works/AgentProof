"""Run a suite: every task, n trials each, checked and traced."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from opentelemetry.trace import Status, StatusCode

from agentproof.agents.base import Agent
from agentproof.agents.scripted import stable_rng
from agentproof.checkers import CheckResult, Observation, run_deterministic
from agentproof.envs import build_environment
from agentproof.judge import Judge, KeywordJudge
from agentproof.metrics import mean, pass_at_k, pass_hat_k
from agentproof.results import RunResult, RunSummary, TaskResult, TrialResult
from agentproof.sandbox import AgentContext, BudgetExceeded, Sandbox
from agentproof.suite import Suite, Task
from agentproof.tracing import TraceRecorder


@dataclass
class RunOptions:
    k: int | None = None
    n: int | None = None
    seed: int = 0
    tasks: list[str] | None = None
    judge: Judge | None = None
    label: str | None = None
    git_sha: str | None = None
    on_trial: Callable[[str, TrialResult], None] | None = None


async def run_trial(
    suite: Suite, task: Task, agent: Agent, index: int, seed: int, judge: Judge, recorder: TraceRecorder
) -> TrialResult:
    tracer = recorder.tracer
    budget = suite.budget.merged(task.budget)
    stop_reason, error, answer = "done", None, ""
    started = time.perf_counter()
    sandbox: Sandbox | None = None
    initial: dict = {}
    final: dict = {}
    probe_results = []
    with tracer.start_as_current_span(f"trial {task.id} #{index}") as span:
        span.set_attribute("agentproof.kind", "trial")
        span.set_attribute("agentproof.task_id", task.id)
        span.set_attribute("agentproof.trial", index)
        span.set_attribute("agentproof.agent", f"{agent.name}@{agent.version}")
        env = build_environment(suite.environment, task.initial_state)
        try:
            await env.start()
            initial = await env.snapshot()
            sandbox = Sandbox(env, budget, tracer, task.tools)
            ctx = AgentContext(task, suite.system_prompt, sandbox, stable_rng(seed, task.id, index), index)
            try:
                outcome = await asyncio.wait_for(agent.run(ctx), timeout=budget.trial_timeout_s)
                stop_reason, answer = outcome.stop_reason, outcome.answer
            except BudgetExceeded as exc:
                stop_reason, error = "budget", str(exc)
            except TimeoutError:
                stop_reason, error = "timeout", f"trial exceeded {budget.trial_timeout_s}s"
            except Exception as exc:  # noqa: BLE001 - an agent crash is a recorded failure, not a harness crash
                stop_reason, error = "error", f"{type(exc).__name__}: {exc}"
            final = await env.snapshot()
            for probe in task.expected.probes:
                probe_results.append((probe, await env.call(probe.tool, probe.args)))
        except Exception as exc:  # noqa: BLE001 - environment failure
            stop_reason, error = "error", f"environment error: {type(exc).__name__}: {exc}"
        finally:
            await env.close()

        tool_log = sandbox.tool_log if sandbox else []
        checks = [
            CheckResult(
                "completed", stop_reason == "done", error or ("" if stop_reason == "done" else stop_reason)
            )
        ]
        if stop_reason != "error" or sandbox is not None:
            obs = Observation(initial, final, tool_log, answer, probe_results)
            checks.extend(run_deterministic(obs, task.expected))
            if task.expected.judge is not None:
                verdict = await judge.grade(task.instruction, answer, task.expected.judge)
                checks.append(CheckResult(f"judge:{judge.name}", verdict.passed, verdict.rationale))
        passed = all(c.passed for c in checks)
        usage = sandbox.usage.to_dict() if sandbox else {}
        span.set_attribute("agentproof.passed", passed)
        span.set_attribute("agentproof.stop_reason", stop_reason)
        for key, value in usage.items():
            span.set_attribute(f"agentproof.usage.{key}", value)
        if not passed:
            failed = ", ".join(c.name for c in checks if not c.passed)
            span.set_status(Status(StatusCode.ERROR, f"failed: {failed}"))
    latency = (time.perf_counter() - started) * 1000
    return TrialResult(
        index=index,
        passed=passed,
        stop_reason=stop_reason,
        error=error,
        checks=[c.to_dict() for c in checks],
        final_answer=answer,
        tool_calls=[r.to_dict() for r in tool_log],
        usage=usage,
        latency_ms=round(latency, 3),
        spans=recorder.drain(),
    )


def summarize_task(task: Task, trials: list[TrialResult], k: int) -> TaskResult:
    n = len(trials)
    c = sum(t.passed for t in trials)
    return TaskResult(
        task_id=task.id,
        description=task.description,
        tags=task.tags,
        n=n,
        successes=c,
        pass_at_1=round(c / n, 6),
        pass_at_k=round(pass_at_k(n, c, k), 6),
        pass_hat_k=round(pass_hat_k(n, c, k), 6),
        trials=trials,
    )


async def run_suite(suite: Suite, agent: Agent, options: RunOptions | None = None) -> RunResult:
    options = options or RunOptions()
    k = options.k or suite.k
    n = max(options.n or k, k)
    judge = options.judge or KeywordJudge()
    selected = [t for t in suite.tasks if options.tasks is None or t.id in options.tasks]
    if not selected:
        raise ValueError("no tasks selected")
    recorder = TraceRecorder()
    task_results: list[TaskResult] = []
    try:
        for task in selected:
            trials = []
            for index in range(n):
                trial = await run_trial(suite, task, agent, index, options.seed, judge, recorder)
                trials.append(trial)
                if options.on_trial is not None:
                    options.on_trial(task.id, trial)
            task_results.append(summarize_task(task, trials, k))
    finally:
        recorder.shutdown()
    all_trials = [t for tr in task_results for t in tr.trials]
    summary = RunSummary(
        tasks=len(task_results),
        trials=len(all_trials),
        pass_at_1=round(mean([t.pass_at_1 for t in task_results]), 6),
        pass_at_k=round(mean([t.pass_at_k for t in task_results]), 6),
        pass_hat_k=round(mean([t.pass_hat_k for t in task_results]), 6),
        input_tokens=sum(t.usage.get("input_tokens", 0) for t in all_trials),
        output_tokens=sum(t.usage.get("output_tokens", 0) for t in all_trials),
        cost_usd=round(sum(t.usage.get("cost_usd", 0.0) for t in all_trials), 6),
        mean_latency_ms=round(mean([t.latency_ms for t in all_trials]), 3),
    )
    return RunResult(
        id=uuid.uuid4().hex[:12],
        suite=suite.suite,
        environment=suite.environment.type,
        agent=agent.describe(),
        k=k,
        n=n,
        seed=options.seed,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        git_sha=options.git_sha,
        label=options.label,
        summary=summary,
        tasks=task_results,
    )
