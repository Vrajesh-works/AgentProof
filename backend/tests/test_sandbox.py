from __future__ import annotations

import asyncio
import time

import pytest

from agentproof.envs.base import InProcessEnvironment, PyTool, obj_schema
from agentproof.sandbox import BudgetExceeded, Sandbox
from agentproof.suite import Budget
from agentproof.tracing import TraceRecorder


def slow(state, seconds):
    time.sleep(seconds)
    return {"slept": seconds}


def echo(state, text):
    state.setdefault("seen", []).append(text)
    return {"echo": text}


def make_env():
    return InProcessEnvironment(
        state={},
        tools={
            "slow": PyTool("slow", "sleeps", obj_schema({"seconds": {"type": "number"}}), slow),
            "echo": PyTool("echo", "echoes", obj_schema({"text": {"type": "string"}}), echo),
        },
    )


@pytest.fixture
def recorder():
    rec = TraceRecorder()
    yield rec
    rec.shutdown()


def test_tool_timeout_returns_an_error_result_and_a_span(recorder):
    sandbox = Sandbox(make_env(), Budget(tool_timeout_s=0.2), recorder.tracer)
    started = time.perf_counter()
    result = asyncio.run(sandbox.call_tool("slow", {"seconds": 2}))
    assert time.perf_counter() - started < 1.5
    assert not result.ok and result.error_kind == "timeout"
    spans = recorder.drain()
    assert spans[0]["name"] == "tool.call slow"
    assert spans[0]["status"] == "ERROR"
    assert spans[0]["attributes"]["agentproof.tool.error_kind"] == "timeout"


def test_allowlist_blocks_tools_and_hides_their_schemas(recorder):
    sandbox = Sandbox(make_env(), Budget(), recorder.tracer, allowed=["echo"])
    assert [t.name for t in sandbox.visible_tools()] == ["echo"]
    result = asyncio.run(sandbox.call_tool("slow", {"seconds": 0}))
    assert result.error_kind == "not_allowed"
    assert sandbox.tool_log[0].tool == "slow"


def test_max_tool_calls_cap(recorder):
    sandbox = Sandbox(make_env(), Budget(max_tool_calls=2), recorder.tracer)

    async def go():
        await sandbox.call_tool("echo", {"text": "a"})
        await sandbox.call_tool("echo", {"text": "b"})
        await sandbox.call_tool("echo", {"text": "c"})

    with pytest.raises(BudgetExceeded) as info:
        asyncio.run(go())
    assert info.value.cap == "max_tool_calls"
    assert sandbox.env.state["seen"] == ["a", "b"]  # the over-budget call never ran


@pytest.mark.parametrize(
    "budget,cap",
    [
        (Budget(max_steps=1), "max_steps"),
        (Budget(max_tokens=150), "max_tokens"),
        (Budget(max_cost_usd=0.01), "max_cost_usd"),
    ],
)
def test_llm_budget_caps(recorder, budget, cap):
    sandbox = Sandbox(make_env(), budget, recorder.tracer)
    with pytest.raises(BudgetExceeded) as info:
        for _ in range(3):
            with sandbox.llm_call("m") as call:
                call.input_tokens, call.output_tokens, call.cost_usd = 60, 40, 0.006
    assert info.value.cap == cap


def test_llm_span_records_usage(recorder):
    sandbox = Sandbox(make_env(), Budget(), recorder.tracer)
    with sandbox.llm_call("claude-x") as call:
        call.input_tokens, call.output_tokens, call.cost_usd, call.stop_reason = 10, 5, 0.001, "end_turn"
    span = recorder.drain()[0]
    assert span["kind"] == "llm"
    assert span["attributes"]["gen_ai.request.model"] == "claude-x"
    assert span["attributes"]["gen_ai.usage.input_tokens"] == 10
    assert sandbox.usage.to_dict()["cost_usd"] == 0.001
