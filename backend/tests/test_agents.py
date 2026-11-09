from __future__ import annotations

import asyncio
import random
from types import SimpleNamespace

import pytest

from agentproof.agents import ScriptedAgent, ToolLoopAgent, load_agent
from agentproof.agents.llm import price
from agentproof.envs.retail import make_retail
from agentproof.sandbox import AgentContext, Sandbox
from agentproof.suite import Budget, load_suite
from agentproof.tracing import TraceRecorder
from tests.conftest import AGENTS, SUITES

TASK = load_suite(SUITES / "retail.yaml").task("cancel-pending-order")


def context(task=TASK, index=0, budget=None):
    rec = TraceRecorder()
    sandbox = Sandbox(make_retail(task.initial_state), budget or Budget(), rec.tracer, task.tools)
    return AgentContext(task, "system", sandbox, random.Random(0), index), rec


def test_load_agent_from_spec_and_yaml():
    agent = load_agent("scripted:fault_rate=0.25,seed=4,version=v9")
    assert isinstance(agent, ScriptedAgent)
    assert (agent.fault_rate, agent.seed, agent.version) == (0.25, 4, "v9")
    flaky = load_agent(str(AGENTS / "flaky.yaml"))
    assert flaky.task_fault_rates["cancel-reason-mapping"] == 0.5
    with pytest.raises(ValueError):
        load_agent("mystery")
    with pytest.raises(ValueError):
        load_agent("scripted:fault_rate=2")


def test_scripted_faults_are_deterministic_and_cover_every_mode():
    agent = ScriptedAgent(fault_rate=1.0, seed=5)
    seen = set()
    for index in range(40):
        ctx, _ = context(index=index)
        plan_a = agent.plan(ctx)
        plan_b = agent.plan(context(index=index)[0])
        assert plan_a == plan_b
        seen.add(plan_a[2])
    assert seen == {"skip_step", "wrong_args", "extra_call", "bad_answer"}


def test_scripted_agent_without_faults_replays_reference():
    ctx, _ = context()
    outcome = asyncio.run(ScriptedAgent().run(ctx))
    assert outcome.answer == TASK.reference_answer
    assert [r.tool for r in ctx.sandbox.tool_log] == [s.tool for s in TASK.reference]


# -- model adapters with fake SDK clients -------------------------------------------


class FakeAnthropic:
    """Returns a tool_use turn, then a final text turn, shaped like the SDK's Message."""

    def __init__(self):
        self.requests = []
        self.messages = SimpleNamespace(create=self.create)

    async def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        usage = SimpleNamespace(input_tokens=1000, output_tokens=100)
        if len(self.requests) == 1:
            return SimpleNamespace(
                stop_reason="tool_use",
                usage=usage,
                content=[
                    SimpleNamespace(type="text", text="Let me cancel it."),
                    SimpleNamespace(
                        type="tool_use",
                        id="tu_1",
                        name="cancel_pending_order",
                        input={"order_id": "W1001", "reason": "ordered by mistake"},
                    ),
                ],
            )
        return SimpleNamespace(
            stop_reason="end_turn",
            usage=usage,
            content=[SimpleNamespace(type="text", text="Cancelled W1001.")],
        )


def test_claude_tool_loop_runs_tools_and_prices_usage():
    client = FakeAnthropic()
    agent = ToolLoopAgent(provider="claude", model="claude-opus-5-5", client=client)
    ctx, _ = context()
    outcome = asyncio.run(agent.run(ctx))
    assert outcome.answer == "Cancelled W1001." and outcome.stop_reason == "done"
    assert ctx.sandbox.tool_log[0].ok
    second = client.requests[1]["messages"]
    assert second[-1]["content"][0]["type"] == "tool_result"
    assert second[-1]["content"][0]["tool_use_id"] == "tu_1"
    assert ctx.sandbox.usage.steps == 2
    # 2 calls x (1000 in at $4/M + 100 out at $20/M)
    assert ctx.sandbox.usage.cost_usd == pytest.approx(2 * (0.004 + 0.002))


class FakeOpenAI:
    def __init__(self):
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def create(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        usage = SimpleNamespace(prompt_tokens=500, completion_tokens=50)
        if len(self.requests) == 1:
            call = SimpleNamespace(
                id="call_1", function=SimpleNamespace(name="get_order", arguments='{"order_id": "W1001"}')
            )
            message = SimpleNamespace(content=None, tool_calls=[call])
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message, finish_reason="tool_calls")], usage=usage
            )
        message = SimpleNamespace(content="Order W1001 is pending.", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=usage)


def test_openai_tool_loop(monkeypatch):
    monkeypatch.delenv("AGENTPROOF_PRICE_INPUT_PER_MTOK", raising=False)
    client = FakeOpenAI()
    agent = ToolLoopAgent(provider="openai", model="some-model", client=client)
    ctx, _ = context()
    outcome = asyncio.run(agent.run(ctx))
    assert outcome.answer == "Order W1001 is pending."
    tool_msg = client.requests[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "call_1"
    assert client.requests[0]["tools"][0]["type"] == "function"
    assert ctx.sandbox.usage.cost_usd == 0.0  # unknown model price


def test_refusal_is_not_treated_as_success():
    class Refuser(FakeAnthropic):
        async def create(self, **kwargs):
            return SimpleNamespace(
                stop_reason="refusal", usage=SimpleNamespace(input_tokens=1, output_tokens=0), content=[]
            )

    ctx, _ = context()
    outcome = asyncio.run(ToolLoopAgent(provider="claude", client=Refuser()).run(ctx))
    assert outcome.stop_reason == "refusal"


def test_price_table_and_env_override(monkeypatch):
    assert price("claude-haiku-5-5", 1_000_000, 1_000_000) == (pytest.approx(0.6), True)
    assert price("mystery", 10, 10) == (0.0, False)
    monkeypatch.setenv("AGENTPROOF_PRICE_INPUT_PER_MTOK", "1")
    monkeypatch.setenv("AGENTPROOF_PRICE_OUTPUT_PER_MTOK", "2")
    assert price("mystery", 1_000_000, 1_000_000) == (pytest.approx(3.0), True)
