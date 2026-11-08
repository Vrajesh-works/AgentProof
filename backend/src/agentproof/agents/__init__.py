"""Agent registry: build an agent from a spec string or a YAML config file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agentproof.agents.base import Agent, AgentOutcome
from agentproof.agents.llm import ToolLoopAgent
from agentproof.agents.scripted import ScriptedAgent

__all__ = ["Agent", "AgentOutcome", "ScriptedAgent", "ToolLoopAgent", "build_agent", "load_agent"]


def build_agent(config: dict[str, Any]) -> Agent:
    config = dict(config)
    kind = config.pop("type", "scripted")
    if kind == "scripted":
        if "fault_modes" in config:
            config["fault_modes"] = tuple(config["fault_modes"])
        return ScriptedAgent(**config)
    if kind in {"claude", "openai"}:
        return ToolLoopAgent(provider=kind, **config)
    raise ValueError(f"unknown agent type {kind!r}")


def _coerce(value: str) -> Any:
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    return value


def load_agent(spec: str) -> Agent:
    """``spec`` is a YAML path, or ``type[:key=value,...]`` such as ``scripted:fault_rate=0.3``."""
    path = Path(spec)
    if spec.endswith((".yaml", ".yml")) or path.is_file():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError(f"agent config {spec} must be a mapping")
        return build_agent(data)
    kind, _, rest = spec.partition(":")
    config: dict[str, Any] = {"type": kind}
    for pair in filter(None, rest.split(",")):
        key, _, value = pair.partition("=")
        config[key.strip()] = _coerce(value.strip())
    return build_agent(config)
