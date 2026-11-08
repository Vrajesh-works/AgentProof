"""Environment interface shared by the built-in and MCP-backed environments."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import jsonschema


@dataclass(frozen=True)
class ToolSchema:
    name: str
    description: str
    input_schema: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


@dataclass
class ToolResult:
    """Outcome of one tool call as the agent sees it.

    ``ok=False`` covers both business refusals ("order is not pending") and
    harness errors (timeout, unknown tool, invalid arguments); ``error_kind``
    tells them apart for the trace.
    """

    ok: bool
    output: Any = None
    error: str | None = None
    error_kind: str | None = None  # "domain", "validation", "timeout", "not_allowed", "protocol", "crash"

    def as_text(self) -> str:
        if self.ok:
            return self.output if isinstance(self.output, str) else json.dumps(self.output, sort_keys=True)
        return json.dumps({"error": self.error, "kind": self.error_kind})


class ToolError(Exception):
    """Raised by a tool implementation for a business refusal."""


class Environment(ABC):
    """A stateful world the agent acts on through tools."""

    name: str = "env"

    @abstractmethod
    def tool_schemas(self) -> list[ToolSchema]: ...

    @abstractmethod
    async def call(self, tool: str, args: dict[str, Any]) -> ToolResult: ...

    @abstractmethod
    async def snapshot(self) -> dict[str, Any]:
        """Return the current state, used for end-state diffs."""

    async def start(self) -> None:  # noqa: B027 - optional hook
        """Prepare the environment for one trial."""

    async def close(self) -> None:  # noqa: B027 - optional hook
        """Release resources held for the trial."""


@dataclass
class PyTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    fn: Callable[..., Any]


@dataclass
class InProcessEnvironment(Environment):
    """Environment whose state is a JSON-like dict and whose tools are Python functions.

    Tool functions run in a worker thread so a slow or hung tool can be cut off by
    the sandbox timeout without blocking the event loop.
    """

    state: dict[str, Any] = field(default_factory=dict)
    tools: dict[str, PyTool] = field(default_factory=dict)

    def tool_schemas(self) -> list[ToolSchema]:
        return [ToolSchema(t.name, t.description, t.input_schema) for t in self.tools.values()]

    async def call(self, tool: str, args: dict[str, Any]) -> ToolResult:
        spec = self.tools.get(tool)
        if spec is None:
            return ToolResult(ok=False, error=f"unknown tool {tool!r}", error_kind="protocol")
        try:
            jsonschema.validate(args, spec.input_schema)
        except jsonschema.ValidationError as exc:
            return ToolResult(ok=False, error=f"invalid arguments: {exc.message}", error_kind="validation")
        try:
            output = await run_in_daemon_thread(spec.fn, self.state, **args)
        except ToolError as exc:
            return ToolResult(ok=False, error=str(exc), error_kind="domain")
        return ToolResult(ok=True, output=output)

    async def snapshot(self) -> dict[str, Any]:
        return copy.deepcopy(self.state)


async def run_in_daemon_thread(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run ``fn`` in its own daemon thread and await the result.

    ``asyncio.to_thread`` uses the loop's default executor, and ``asyncio.run``
    joins that executor on exit, so a tool that hangs past its timeout would
    still hold the whole run open. A daemon thread is abandoned instead.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[Any] = loop.create_future()

    def deliver(setter: Callable[[Any], None], value: Any) -> None:
        if not future.done():
            setter(value)

    def target() -> None:
        try:
            value = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - forwarded to the awaiting coroutine
            setter, value = future.set_exception, exc
        else:
            setter = future.set_result
        # If the loop already finished, the caller gave up on this call.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(deliver, setter, value)

    threading.Thread(target=target, daemon=True, name=f"tool-{getattr(fn, '__name__', 'fn')}").start()
    return await future


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Return ``base`` with ``patch`` merged in recursively (patch wins)."""
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def obj_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required if required is not None else list(properties),
        "additionalProperties": False,
    }
