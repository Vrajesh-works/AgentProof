"""Model-driven agents: one tool-use loop, provider sessions for Claude and OpenAI.

The loop is owned here (not by an SDK helper) because every model call and tool
call has to pass through the sandbox for budget caps and tracing.

Configuration comes from environment variables:

- ``ANTHROPIC_API_KEY`` / ``AGENTPROOF_CLAUDE_MODEL`` (default ``claude-opus-5-5``)
- ``OPENAI_API_KEY`` / ``AGENTPROOF_OPENAI_MODEL`` (default ``gpt-4.1-mini``)
- ``AGENTPROOF_PRICE_INPUT_PER_MTOK`` / ``AGENTPROOF_PRICE_OUTPUT_PER_MTOK`` to price
  a model missing from the table below.

Server-side model fallbacks are deliberately not enabled for Claude: an eval run
must measure the model it names, so a refusal is recorded as a refusal.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from agentproof.agents.base import Agent, AgentOutcome
from agentproof.envs.base import ToolResult, ToolSchema
from agentproof.sandbox import AgentContext

# USD per million tokens (input, output). Anthropic first-party list prices.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.0, 50.0),
}


def price(model: str, input_tokens: int, output_tokens: int) -> tuple[float, bool]:
    """Return (cost in USD, whether the price is known)."""
    rates = PRICING.get(model)
    if rates is None:
        env_in = os.environ.get("AGENTPROOF_PRICE_INPUT_PER_MTOK")
        env_out = os.environ.get("AGENTPROOF_PRICE_OUTPUT_PER_MTOK")
        if env_in is None or env_out is None:
            return 0.0, False
        rates = (float(env_in), float(env_out))
    return (input_tokens * rates[0] + output_tokens * rates[1]) / 1_000_000, True


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class ModelTurn:
    text: str
    tool_calls: list[ToolCall]
    input_tokens: int
    output_tokens: int
    stop_reason: str


class ProviderSession(Protocol):
    model: str

    def start(self, system: str, instruction: str, tools: list[ToolSchema]) -> None: ...

    async def step(self) -> ModelTurn: ...

    def add_tool_results(self, results: list[tuple[ToolCall, ToolResult]]) -> None: ...


class AnthropicSession:
    """Manual tool-use loop state for the Anthropic Messages API (official SDK)."""

    def __init__(self, model: str, client: Any = None, max_tokens: int = 16000):
        if client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - optional extra
                raise RuntimeError("install the 'claude' extra: pip install 'agentproof[claude]'") from exc
            client = anthropic.AsyncAnthropic()
        self.client = client
        self.model = model
        self.max_tokens = max_tokens
        self.system = ""
        self.tools: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []

    def start(self, system: str, instruction: str, tools: list[ToolSchema]) -> None:
        self.system = system
        self.tools = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools
        ]
        self.messages = [{"role": "user", "content": instruction}]

    async def step(self) -> ModelTurn:
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self.system,
            tools=self.tools,
            messages=self.messages,
        )
        # Append the full content (thinking and tool_use blocks included) so the
        # next request replays the conversation unchanged.
        self.messages.append({"role": "assistant", "content": response.content})
        text = "".join(getattr(block, "text", "") for block in response.content if block.type == "text")
        calls = [
            ToolCall(block.id, block.name, dict(block.input or {}))
            for block in response.content
            if block.type == "tool_use"
        ]
        if response.stop_reason != "tool_use":
            calls = []
        return ModelTurn(
            text=text,
            tool_calls=calls,
            input_tokens=int(response.usage.input_tokens),
            output_tokens=int(response.usage.output_tokens),
            stop_reason=str(response.stop_reason),
        )

    def add_tool_results(self, results: list[tuple[ToolCall, ToolResult]]) -> None:
        # All results go back in one user message, which keeps parallel tool use working.
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": result.as_text(),
                        "is_error": not result.ok,
                    }
                    for call, result in results
                ],
            }
        )


class OpenAISession:
    """Tool-calling loop state for the OpenAI Chat Completions API (official SDK)."""

    def __init__(self, model: str, client: Any = None):
        if client is None:
            try:
                import openai
            except ImportError as exc:  # pragma: no cover - optional extra
                raise RuntimeError("install the 'openai' extra: pip install 'agentproof[openai]'") from exc
            client = openai.AsyncOpenAI()
        self.client = client
        self.model = model
        self.tools: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []

    def start(self, system: str, instruction: str, tools: list[ToolSchema]) -> None:
        self.tools = [
            {
                "type": "function",
                "function": {"name": t.name, "description": t.description, "parameters": t.input_schema},
            }
            for t in tools
        ]
        self.messages = [{"role": "system", "content": system}, {"role": "user", "content": instruction}]

    async def step(self) -> ModelTurn:
        response = await self.client.chat.completions.create(
            model=self.model, messages=self.messages, tools=self.tools
        )
        choice = response.choices[0]
        message = choice.message
        calls: list[ToolCall] = []
        for tc in message.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"__unparseable_arguments__": tc.function.arguments}
            calls.append(
                ToolCall(tc.id, tc.function.name, args if isinstance(args, dict) else {"value": args})
            )
        entry: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
        if message.tool_calls:
            entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in message.tool_calls
            ]
        self.messages.append(entry)
        return ModelTurn(
            text=message.content or "",
            tool_calls=calls,
            input_tokens=int(response.usage.prompt_tokens),
            output_tokens=int(response.usage.completion_tokens),
            stop_reason=str(choice.finish_reason),
        )

    def add_tool_results(self, results: list[tuple[ToolCall, ToolResult]]) -> None:
        for call, result in results:
            self.messages.append({"role": "tool", "tool_call_id": call.id, "content": result.as_text()})


@dataclass
class ToolLoopAgent(Agent):
    """Runs a provider session until the model answers without calling a tool."""

    provider: str = "claude"
    model: str = ""
    version: str = "v1"
    client: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.provider not in {"claude", "openai"}:
            raise ValueError(f"unknown provider {self.provider!r}")
        if not self.model:
            if self.provider == "claude":
                self.model = os.environ.get("AGENTPROOF_CLAUDE_MODEL", "claude-opus-5-5")
            else:
                self.model = os.environ.get("AGENTPROOF_OPENAI_MODEL", "gpt-4.1-mini")
        self.name = self.provider

    def describe(self) -> dict[str, object]:
        return {"name": self.name, "version": self.version, "model": self.model}

    def _session(self) -> ProviderSession:
        if self.provider == "claude":
            return AnthropicSession(self.model, client=self.client)
        return OpenAISession(self.model, client=self.client)

    async def run(self, ctx: AgentContext) -> AgentOutcome:
        session = self._session()
        session.start(ctx.system_prompt, ctx.instruction, ctx.tools)
        while True:
            with ctx.llm_call(self.model) as call:
                turn = await session.step()
                call.input_tokens = turn.input_tokens
                call.output_tokens = turn.output_tokens
                call.stop_reason = turn.stop_reason
                cost, known = price(self.model, turn.input_tokens, turn.output_tokens)
                call.cost_usd = cost
                call.attributes["agentproof.cost_known"] = known
            if not turn.tool_calls:
                reason = (
                    "done" if turn.stop_reason in {"end_turn", "stop", "stop_sequence"} else turn.stop_reason
                )
                return AgentOutcome(answer=turn.text, stop_reason=reason)
            results = [(tc, await ctx.call_tool(tc.name, tc.args)) for tc in turn.tool_calls]
            session.add_tool_results(results)
