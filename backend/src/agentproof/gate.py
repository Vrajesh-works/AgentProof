"""Compare two runs and decide whether the head regressed.

Used by the ``agentproof gate`` CLI and the GitHub Action: a PR fails when any
watched metric drops by more than the tolerance, either for the suite as a whole
or for any single task.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentproof.results import RunResult

METRICS = ("pass_hat_k", "pass_at_k", "pass_at_1")
LABELS = {"pass_hat_k": "pass^k", "pass_at_k": "pass@k", "pass_at_1": "pass@1"}


@dataclass
class TaskDelta:
    task_id: str
    base: dict[str, float] | None
    head: dict[str, float] | None
    regressions: list[str] = field(default_factory=list)
    improvements: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.base is None:
            return "new"
        if self.head is None:
            return "missing"
        if self.regressions:
            return "regressed"
        if self.improvements:
            return "improved"
        return "unchanged"


@dataclass
class GateReport:
    base: RunResult
    head: RunResult
    metrics: tuple[str, ...]
    tolerance: float
    summary_regressions: list[str]
    tasks: list[TaskDelta]
    fail_on_missing: bool = True

    @property
    def regressed_tasks(self) -> list[TaskDelta]:
        return [t for t in self.tasks if t.status == "regressed"]

    @property
    def missing_tasks(self) -> list[TaskDelta]:
        return [t for t in self.tasks if t.status == "missing"]

    @property
    def failed(self) -> bool:
        return bool(
            self.summary_regressions or self.regressed_tasks or (self.fail_on_missing and self.missing_tasks)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "failed": self.failed,
            "tolerance": self.tolerance,
            "metrics": list(self.metrics),
            "base": {"id": self.base.id, "agent": self.base.agent, "summary": self.base.summary.model_dump()},
            "head": {"id": self.head.id, "agent": self.head.agent, "summary": self.head.summary.model_dump()},
            "summary_regressions": self.summary_regressions,
            "tasks": [
                {
                    "task_id": t.task_id,
                    "status": t.status,
                    "base": t.base,
                    "head": t.head,
                    "regressions": t.regressions,
                    "improvements": t.improvements,
                }
                for t in self.tasks
            ],
        }

    def markdown(self) -> str:
        verdict = "FAILED - reliability regressed" if self.failed else "PASSED - no regression"
        lines = [
            f"## AgentProof gate: {verdict}",
            "",
            f"Suite `{self.head.suite}`, k={self.head.k}, n={self.head.n}, tolerance {self.tolerance:.3f}. "
            f"Base `{_agent(self.base)}` vs head `{_agent(self.head)}`.",
            "",
            "| Metric | Base | Head | Delta |",
            "| --- | ---: | ---: | ---: |",
        ]
        for metric in METRICS:
            b = getattr(self.base.summary, metric)
            h = getattr(self.head.summary, metric)
            lines.append(f"| {LABELS[metric]} | {b:.3f} | {h:.3f} | {_delta(h - b)} |")
        lines.append(
            f"| cost (USD) | {self.base.summary.cost_usd:.4f} | {self.head.summary.cost_usd:.4f} | |"
        )
        lines += [
            "",
            "| Task | Status | pass^k base | pass^k head | pass@k base | pass@k head |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
        order = {"regressed": 0, "missing": 1, "new": 2, "improved": 3, "unchanged": 4}
        for t in sorted(self.tasks, key=lambda d: (order[d.status], d.task_id)):
            lines.append(
                f"| `{t.task_id}` | {t.status} | {_fmt(t.base, 'pass_hat_k')} | {_fmt(t.head, 'pass_hat_k')} "
                f"| {_fmt(t.base, 'pass_at_k')} | {_fmt(t.head, 'pass_at_k')} |"
            )
        if self.summary_regressions:
            lines += ["", "Suite-level regressions: " + "; ".join(self.summary_regressions)]
        return "\n".join(lines) + "\n"


def _agent(run: RunResult) -> str:
    return f"{run.agent.get('name')}@{run.agent.get('version')}"


def _delta(value: float) -> str:
    if abs(value) < 5e-7:
        return "0"
    return f"{value:+.3f}"


def _fmt(values: dict[str, float] | None, metric: str) -> str:
    return "-" if values is None else f"{values[metric]:.3f}"


def compare(
    base: RunResult,
    head: RunResult,
    *,
    metrics: tuple[str, ...] = ("pass_hat_k", "pass_at_k"),
    tolerance: float = 0.0,
    fail_on_missing: bool = True,
) -> GateReport:
    unknown = set(metrics) - set(METRICS)
    if unknown:
        raise ValueError(f"unknown metrics {sorted(unknown)}")
    if base.suite != head.suite:
        raise ValueError(f"cannot compare different suites: {base.suite!r} vs {head.suite!r}")
    summary_regressions = []
    for metric in metrics:
        b, h = getattr(base.summary, metric), getattr(head.summary, metric)
        if h < b - tolerance - 1e-9:
            summary_regressions.append(f"{LABELS[metric]} {b:.3f} -> {h:.3f}")
    base_tasks = {t.task_id: t for t in base.tasks}
    head_tasks = {t.task_id: t for t in head.tasks}
    deltas = []
    for task_id in sorted(set(base_tasks) | set(head_tasks)):
        b_task, h_task = base_tasks.get(task_id), head_tasks.get(task_id)
        b_vals = {m: getattr(b_task, m) for m in METRICS} if b_task else None
        h_vals = {m: getattr(h_task, m) for m in METRICS} if h_task else None
        delta = TaskDelta(task_id, b_vals, h_vals)
        if b_vals and h_vals:
            for metric in metrics:
                if h_vals[metric] < b_vals[metric] - tolerance - 1e-9:
                    delta.regressions.append(metric)
                elif h_vals[metric] > b_vals[metric] + 1e-9:
                    delta.improvements.append(metric)
        deltas.append(delta)
    return GateReport(base, head, metrics, tolerance, summary_regressions, deltas, fail_on_missing)
