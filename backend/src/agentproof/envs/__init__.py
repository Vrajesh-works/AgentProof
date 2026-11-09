"""Environment factory."""

from __future__ import annotations

from typing import Any

from agentproof.envs.airline import make_airline
from agentproof.envs.base import Environment, ToolResult, ToolSchema
from agentproof.envs.mcp import McpEnvironment, McpServerConfig
from agentproof.envs.retail import make_retail
from agentproof.suite import EnvironmentSpec

__all__ = ["Environment", "ToolResult", "ToolSchema", "build_environment"]


def build_environment(spec: EnvironmentSpec, initial_state: dict[str, Any] | None = None) -> Environment:
    if spec.type == "retail":
        return make_retail(initial_state)
    if spec.type == "airline":
        return make_airline(initial_state)
    if spec.type == "mcp":
        if initial_state:
            raise ValueError(
                "mcp environments take their state from the server; initial_state is not supported"
            )
        return McpEnvironment(McpServerConfig.from_options(spec.options))
    raise ValueError(f"unknown environment type {spec.type!r}")
