"""Serializable run results.

``RunResult`` is the one document that the CLI writes, the gate compares, the
API imports, and the viewer renders, so CI does not need a database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


class TrialResult(BaseModel):
    index: int
    passed: bool
    stop_reason: str  # done, budget, timeout, error, refusal, ...
    error: str | None = None
    checks: list[dict[str, Any]] = Field(default_factory=list)
    final_answer: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    latency_ms: float = 0.0
    spans: list[dict[str, Any]] = Field(default_factory=list)


class TaskResult(BaseModel):
    task_id: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    n: int
    successes: int
    pass_at_1: float
    pass_at_k: float
    pass_hat_k: float
    trials: list[TrialResult]


class RunSummary(BaseModel):
    tasks: int
    trials: int
    pass_at_1: float
    pass_at_k: float
    pass_hat_k: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    mean_latency_ms: float


class RunResult(BaseModel):
    schema_version: int = SCHEMA_VERSION
    id: str
    suite: str
    environment: str
    agent: dict[str, Any]
    k: int
    n: int
    seed: int
    created_at: str
    git_sha: str | None = None
    label: str | None = None
    summary: RunSummary
    tasks: list[TaskResult]

    def task(self, task_id: str) -> TaskResult | None:
        return next((t for t in self.tasks if t.task_id == task_id), None)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> RunResult:
        return cls.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))

    def scores(self) -> dict[str, Any]:
        """Only the deterministic numbers: used to prove two runs scored the same."""
        return {
            "summary": {k: v for k, v in self.summary.model_dump().items() if k != "mean_latency_ms"},
            "tasks": {
                t.task_id: {"successes": t.successes, "pass_at_k": t.pass_at_k, "pass_hat_k": t.pass_hat_k}
                for t in self.tasks
            },
        }
