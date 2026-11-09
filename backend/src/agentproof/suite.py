"""YAML task suite models and loader.

A suite is a list of tasks run against one environment. Each task declares the
instruction the agent receives, the tools it may use, an optional reference
solution, and an ``expected`` block that the deterministic checkers (and, only
for open answers, the judge) evaluate.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


class SuiteError(ValueError):
    """Raised when a suite file cannot be parsed or fails validation."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Budget(_Strict):
    """Caps that bound one trial. Any cap that trips ends the trial as a failure."""

    max_steps: int = Field(default=20, ge=1, description="Model calls (agent turns) per trial")
    max_tool_calls: int = Field(default=15, ge=0)
    max_tokens: int = Field(default=200_000, ge=1, description="Input plus output tokens per trial")
    max_cost_usd: float = Field(default=1.0, ge=0)
    tool_timeout_s: float = Field(default=10.0, gt=0)
    trial_timeout_s: float = Field(default=120.0, gt=0)

    def merged(self, override: BudgetOverride | None) -> Budget:
        if override is None:
            return self
        data = self.model_dump()
        data.update(override.model_dump(exclude_none=True))
        return Budget(**data)


class BudgetOverride(_Strict):
    max_steps: int | None = Field(default=None, ge=1)
    max_tool_calls: int | None = Field(default=None, ge=0)
    max_tokens: int | None = Field(default=None, ge=1)
    max_cost_usd: float | None = Field(default=None, ge=0)
    tool_timeout_s: float | None = Field(default=None, gt=0)
    trial_timeout_s: float | None = Field(default=None, gt=0)


class ToolCallSpec(_Strict):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class Probe(_Strict):
    """A read-only tool call made after the trial; ``expect`` is matched as a subset."""

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    expect: dict[str, Any] = Field(default_factory=dict)
    expect_error: bool = False


class JudgeSpec(_Strict):
    rubric: str
    must_mention: list[str] = Field(default_factory=list)
    must_not_mention: list[str] = Field(default_factory=list)


class Expected(_Strict):
    state_diff: dict[str, Any] | None = Field(
        default=None,
        description="Slash-separated state paths mapped to their expected new value. "
        "Use '__deleted__' for removed keys. An empty dict means 'no state change'.",
    )
    allow_extra_state_changes: bool = False
    tool_order: list[str] | None = None
    tool_order_mode: Literal["subsequence", "exact"] = "subsequence"
    required_calls: list[ToolCallSpec] = Field(
        default_factory=list, description="Calls that must appear; args are matched as a subset"
    )
    forbidden_tools: list[str] = Field(default_factory=list)
    answer_contains: list[str] = Field(default_factory=list)
    answer_not_contains: list[str] = Field(default_factory=list)
    answer_regex: str | None = None
    answer_schema: dict[str, Any] | None = Field(
        default=None, description="JSON Schema the final answer must satisfy once parsed as JSON"
    )
    probes: list[Probe] = Field(default_factory=list)
    judge: JudgeSpec | None = None

    @field_validator("answer_regex")
    @classmethod
    def _valid_regex(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"invalid regex: {exc}") from None
        return value

    def is_empty(self) -> bool:
        return self.model_dump(exclude_defaults=True) == {}


class Task(_Strict):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    instruction: str = Field(min_length=1)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    tools: list[str] | None = Field(default=None, description="Allowlist; None means every env tool")
    initial_state: dict[str, Any] | None = Field(
        default=None, description="Deep-merged over the environment's default state"
    )
    reference: list[ToolCallSpec] = Field(default_factory=list)
    reference_answer: str = ""
    expected: Expected
    budget: BudgetOverride | None = None

    @model_validator(mode="after")
    def _has_some_expectation(self) -> Task:
        if self.expected.is_empty():
            raise ValueError(f"task {self.id!r} has an empty 'expected' block; nothing would be checked")
        return self


class EnvironmentSpec(_Strict):
    type: Literal["retail", "airline", "mcp"]
    options: dict[str, Any] = Field(default_factory=dict)


class Suite(_Strict):
    suite: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    description: str = ""
    environment: EnvironmentSpec
    k: int = Field(default=4, ge=1, le=64)
    budget: Budget = Field(default_factory=Budget)
    system_prompt: str = (
        "You are a careful customer operations agent. Use the tools to complete the user's "
        "request exactly. Do not take actions the user did not ask for. When you are done, "
        "reply with a short confirmation of what you did."
    )
    tasks: list[Task] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> Suite:
        seen: set[str] = set()
        for task in self.tasks:
            if task.id in seen:
                raise ValueError(f"duplicate task id {task.id!r}")
            seen.add(task.id)
        return self

    def task(self, task_id: str) -> Task:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(task_id)


def interpolate_env(value: Any, environ: dict[str, str] | None = None) -> Any:
    """Replace ``${VAR}`` and ``${VAR:-default}`` in every string of a YAML tree."""
    env = os.environ if environ is None else environ

    def _sub(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        if name in env:
            return env[name]
        if default is not None:
            return default
        raise SuiteError(f"environment variable {name} is not set and has no default")

    if isinstance(value, str):
        return _ENV_PATTERN.sub(_sub, value)
    if isinstance(value, list):
        return [interpolate_env(item, env) for item in value]
    if isinstance(value, dict):
        return {key: interpolate_env(item, env) for key, item in value.items()}
    return value


def parse_suite(data: Any, *, environ: dict[str, str] | None = None) -> Suite:
    if not isinstance(data, dict):
        raise SuiteError("suite file must contain a mapping at the top level")
    data = dict(data)
    # Only the environment block is interpolated: instructions and expected
    # values are test data and must stay literal.
    if "environment" in data:
        data["environment"] = interpolate_env(data["environment"], environ)
    try:
        return Suite.model_validate(data)
    except ValueError as exc:
        raise SuiteError(str(exc)) from None


def load_suite(path: str | Path, *, environ: dict[str, str] | None = None) -> Suite:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise SuiteError(f"cannot read suite {path}: {exc}") from None
    return parse_suite(data, environ=environ)
