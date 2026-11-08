"""The sandbox every agent action goes through.

It enforces the tool allowlist, a timeout on every tool call, and the trial
budget (model steps, tool calls, tokens, cost). It also records the tool-call
log the checkers read and emits a span per tool call and per model call.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from opentelemetry.trace import Status, StatusCode, Tracer

from agentproof.envs.base import Environment, ToolResult, ToolSchema
from agentproof.suite import Budget, Task
from agentproof.tracing import attr_json


class BudgetExceeded(Exception):
    def __init__(self, cap: str, limit: float, value: float):
        super().__init__(f"budget cap {cap} exceeded ({value} > {limit})")
        self.cap = cap
        self.limit = limit
        self.value = value


@dataclass
class ToolCallRecord:
    tool: str
    args: dict[str, Any]
    ok: bool
    output: Any = None
    error: str | None = None
    error_kind: str | None = None
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "args": self.args,
            "ok": self.ok,
            "output": self.output,
            "error": self.error,
            "error_kind": self.error_kind,
            "latency_ms": round(self.latency_ms, 3),
        }


@dataclass
class Usage:
    steps: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "steps": self.steps,
            "tool_calls": self.tool_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
        }


@dataclass
class LLMCall:
    """Handle the agent fills in while a model call span is open."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    stop_reason: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)


class Sandbox:
    def __init__(self, env: Environment, budget: Budget, tracer: Tracer, allowed: list[str] | None = None):
        self.env = env
        self.budget = budget
        self.tracer = tracer
        self.allowed = set(allowed) if allowed is not None else None
        self.usage = Usage()
        self.tool_log: list[ToolCallRecord] = []

    def visible_tools(self) -> list[ToolSchema]:
        schemas = self.env.tool_schemas()
        if self.allowed is None:
            return schemas
        return [s for s in schemas if s.name in self.allowed]

    def _check(self, cap: str, limit: float, value: float) -> None:
        if value > limit:
            raise BudgetExceeded(cap, limit, value)

    @contextmanager
    def llm_call(self, model: str) -> Iterator[LLMCall]:
        """Wrap one model call: counts a step, opens a span, then charges tokens and cost."""
        self.usage.steps += 1
        self._check("max_steps", self.budget.max_steps, self.usage.steps)
        call = LLMCall(model=model)
        with self.tracer.start_as_current_span(f"llm.call {model}") as span:
            span.set_attribute("agentproof.kind", "llm")
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.request.model", model)
            span.set_attribute("agentproof.step", self.usage.steps)
            yield call
            span.set_attribute("gen_ai.usage.input_tokens", call.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", call.output_tokens)
            span.set_attribute("agentproof.cost_usd", round(call.cost_usd, 6))
            if call.stop_reason:
                span.set_attribute("gen_ai.response.finish_reasons", [call.stop_reason])
            for key, value in call.attributes.items():
                span.set_attribute(key, value)
        self.usage.input_tokens += call.input_tokens
        self.usage.output_tokens += call.output_tokens
        self.usage.cost_usd += call.cost_usd
        self._check("max_tokens", self.budget.max_tokens, self.usage.tokens)
        self._check("max_cost_usd", self.budget.max_cost_usd, round(self.usage.cost_usd, 9))

    async def call_tool(self, tool: str, args: dict[str, Any]) -> ToolResult:
        self.usage.tool_calls += 1
        self._check("max_tool_calls", self.budget.max_tool_calls, self.usage.tool_calls)
        args = dict(args or {})
        with self.tracer.start_as_current_span(f"tool.call {tool}") as span:
            span.set_attribute("agentproof.kind", "tool")
            span.set_attribute("gen_ai.operation.name", "execute_tool")
            span.set_attribute("gen_ai.tool.name", tool)
            span.set_attribute("agentproof.tool.args", attr_json(args))
            started = time.perf_counter()
            if self.allowed is not None and tool not in self.allowed:
                result = ToolResult(
                    ok=False, error=f"tool {tool!r} is not allowed for this task", error_kind="not_allowed"
                )
            else:
                try:
                    result = await asyncio.wait_for(
                        self.env.call(tool, args), timeout=self.budget.tool_timeout_s
                    )
                except TimeoutError:
                    result = ToolResult(
                        ok=False,
                        error=f"tool {tool!r} timed out after {self.budget.tool_timeout_s}s",
                        error_kind="timeout",
                    )
                    on_timeout = getattr(self.env, "on_timeout", None)
                    if on_timeout is not None:
                        await on_timeout(tool)
            latency = (time.perf_counter() - started) * 1000
            span.set_attribute("agentproof.tool.ok", result.ok)
            span.set_attribute("agentproof.tool.latency_ms", round(latency, 3))
            if result.ok:
                span.set_attribute("agentproof.tool.result", attr_json(result.output))
            else:
                span.set_attribute("agentproof.tool.error", result.error or "")
                span.set_attribute("agentproof.tool.error_kind", result.error_kind or "")
                span.set_status(Status(StatusCode.ERROR, result.error or "tool error"))
        self.tool_log.append(
            ToolCallRecord(tool, args, result.ok, result.output, result.error, result.error_kind, latency)
        )
        return result


@dataclass
class AgentContext:
    """Everything an agent may use during one trial."""

    task: Task
    system_prompt: str
    sandbox: Sandbox
    rng: random.Random
    trial_index: int

    @property
    def instruction(self) -> str:
        return self.task.instruction

    @property
    def tools(self) -> list[ToolSchema]:
        return self.sandbox.visible_tools()

    async def call_tool(self, tool: str, args: dict[str, Any]) -> ToolResult:
        return await self.sandbox.call_tool(tool, args)

    def llm_call(self, model: str):
        return self.sandbox.llm_call(model)
