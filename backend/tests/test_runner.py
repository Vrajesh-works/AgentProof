from __future__ import annotations

import asyncio

import pytest

from agentproof.agents import ScriptedAgent, load_agent
from agentproof.agents.base import Agent, AgentOutcome
from agentproof.runner import RunOptions, run_suite
from agentproof.suite import load_suite, parse_suite
from tests.conftest import AGENTS, SUITES


def run(suite, agent, **opts):
    return asyncio.run(run_suite(suite, agent, RunOptions(**opts)))


@pytest.mark.parametrize("name", ["retail.yaml", "airline.yaml"])
def test_reference_solutions_pass_every_task(name):
    result = run(load_suite(SUITES / name), ScriptedAgent())
    failing = {
        t.task_id: [c for tr in t.trials for c in tr.checks if not c["passed"]]
        for t in result.tasks
        if t.successes < t.n
    }
    assert not failing
    assert result.summary.pass_hat_k == 1.0


def test_same_seed_gives_identical_scores():
    suite = load_suite(SUITES / "retail.yaml")
    agent = load_agent(str(AGENTS / "flaky.yaml"))
    a = run(suite, agent, seed=3)
    b = run(suite, agent, seed=3)
    assert a.scores() == b.scores()


def test_flaky_agent_shows_gap_between_pass_at_k_and_pass_hat_k():
    suite = load_suite(SUITES / "retail.yaml")
    agent = ScriptedAgent(task_fault_rates={"cancel-reason-mapping": 0.5}, seed=1)
    result = run(suite, agent, n=8, k=4, tasks=["cancel-reason-mapping"])
    task = result.tasks[0]
    assert 0 < task.successes < task.n
    assert task.pass_at_k > task.pass_hat_k


def test_trial_records_tool_calls_usage_and_spans():
    result = run(load_suite(SUITES / "retail.yaml"), ScriptedAgent(), k=1, tasks=["cancel-pending-order"])
    trial = result.tasks[0].trials[0]
    assert [c["tool"] for c in trial.tool_calls] == [
        "find_user_by_email",
        "get_order",
        "cancel_pending_order",
    ]
    kinds = [s["kind"] for s in trial.spans]
    assert kinds.count("trial") == 1 and kinds.count("tool") == 3 and kinds.count("llm") == 4
    root = next(s for s in trial.spans if s["kind"] == "trial")
    children = [s for s in trial.spans if s["parent_span_id"] == root["span_id"]]
    assert len(children) == 7
    assert trial.usage["tool_calls"] == 3
    assert result.summary.input_tokens > 0


class Crashy(Agent):
    name = "crashy"

    async def run(self, ctx):
        await ctx.call_tool("get_order", {"order_id": "W1001"})
        raise RuntimeError("boom")


class Sleepy(Agent):
    name = "sleepy"

    async def run(self, ctx):
        await asyncio.sleep(5)
        return AgentOutcome("late")


class Spender(Agent):
    name = "spender"

    async def run(self, ctx):
        while True:
            with ctx.llm_call("m") as call:
                call.input_tokens = 1000
            await ctx.call_tool("get_order", {"order_id": "W1001"})


def tiny_suite(**budget):
    return parse_suite(
        {
            "suite": "tiny",
            "environment": {"type": "retail"},
            "k": 1,
            "budget": budget,
            "tasks": [{"id": "t", "instruction": "x", "expected": {"state_diff": {}}}],
        }
    )


@pytest.mark.parametrize(
    "agent,budget,reason",
    [
        (Crashy(), {}, "error"),
        (Sleepy(), {"trial_timeout_s": 0.2}, "timeout"),
        (Spender(), {"max_tool_calls": 3}, "budget"),
        (Spender(), {"max_tokens": 2500}, "budget"),
    ],
)
def test_agent_failures_become_failed_trials(agent, budget, reason):
    result = run(tiny_suite(**budget), agent)
    trial = result.tasks[0].trials[0]
    assert not trial.passed
    assert trial.stop_reason == reason
    assert trial.checks[0]["name"] == "completed" and not trial.checks[0]["passed"]


def test_environment_start_failure_is_recorded():
    suite = parse_suite(
        {
            "suite": "broken",
            "environment": {"type": "mcp", "options": {"command": ["/definitely/not/a/binary"]}},
            "k": 1,
            "tasks": [{"id": "t", "instruction": "x", "expected": {"state_diff": {}}}],
        }
    )
    trial = run(suite, ScriptedAgent()).tasks[0].trials[0]
    assert trial.stop_reason == "error"
    assert "environment error" in trial.error
