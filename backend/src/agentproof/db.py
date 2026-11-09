"""Storage for runs, task results, trials and spans.

``DATABASE_URL`` selects the backend: PostgreSQL in Docker Compose and CI
(``postgresql+psycopg://...``), SQLite for local development (the default).
The storage class is the seam where a managed database would plug in later.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, selectinload

from agentproof.results import RunResult, RunSummary, TaskResult, TrialResult

DEFAULT_URL = "sqlite:///./agentproof.db"


class Base(DeclarativeBase):
    pass


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    suite: Mapped[str] = mapped_column(String(200), index=True)
    environment: Mapped[str] = mapped_column(String(50))
    agent: Mapped[dict[str, Any]] = mapped_column(JSON)
    agent_name: Mapped[str] = mapped_column(String(200))
    agent_version: Mapped[str] = mapped_column(String(200))
    k: Mapped[int] = mapped_column(Integer)
    n: Mapped[int] = mapped_column(Integer)
    seed: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    git_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON)
    tasks: Mapped[list[TaskRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="TaskRow.position"
    )


class TaskRow(Base):
    __tablename__ = "task_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    task_id: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    n: Mapped[int] = mapped_column(Integer)
    successes: Mapped[int] = mapped_column(Integer)
    pass_at_1: Mapped[float] = mapped_column(Float)
    pass_at_k: Mapped[float] = mapped_column(Float)
    pass_hat_k: Mapped[float] = mapped_column(Float)
    run: Mapped[RunRow] = relationship(back_populates="tasks")
    trials: Mapped[list[TrialRow]] = relationship(
        back_populates="task", cascade="all, delete-orphan", order_by="TrialRow.index"
    )


class TrialRow(Base):
    __tablename__ = "trials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_result_id: Mapped[int] = mapped_column(ForeignKey("task_results.id", ondelete="CASCADE"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    passed: Mapped[bool] = mapped_column(Boolean)
    stop_reason: Mapped[str] = mapped_column(String(50))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    final_answer: Mapped[str] = mapped_column(Text, default="")
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    usage: Mapped[dict[str, Any]] = mapped_column(JSON)
    latency_ms: Mapped[float] = mapped_column(Float)
    task: Mapped[TaskRow] = relationship(back_populates="trials")
    spans: Mapped[list[SpanRow]] = relationship(
        back_populates="trial", cascade="all, delete-orphan", order_by="SpanRow.start_ns"
    )


class SpanRow(Base):
    __tablename__ = "spans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trial_id: Mapped[int] = mapped_column(ForeignKey("trials.id", ondelete="CASCADE"), index=True)
    span_id: Mapped[str] = mapped_column(String(16))
    trace_id: Mapped[str] = mapped_column(String(32))
    parent_span_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    name: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(20))
    start_ns: Mapped[int] = mapped_column(BigInteger)
    end_ns: Mapped[int] = mapped_column(BigInteger)
    duration_ms: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON)
    trial: Mapped[TrialRow] = relationship(back_populates="spans")


def _span_dict(row: SpanRow) -> dict[str, Any]:
    return {
        "span_id": row.span_id,
        "trace_id": row.trace_id,
        "parent_span_id": row.parent_span_id,
        "name": row.name,
        "kind": row.kind,
        "start_ns": row.start_ns,
        "end_ns": row.end_ns,
        "duration_ms": row.duration_ms,
        "status": row.status,
        "attributes": row.attributes,
    }


def _trial_model(row: TrialRow, with_spans: bool) -> TrialResult:
    return TrialResult(
        index=row.index,
        passed=row.passed,
        stop_reason=row.stop_reason,
        error=row.error,
        checks=row.checks,
        final_answer=row.final_answer,
        tool_calls=row.tool_calls,
        usage=row.usage,
        latency_ms=row.latency_ms,
        spans=[_span_dict(s) for s in row.spans] if with_spans else [],
    )


class Database:
    def __init__(self, url: str | None = None):
        self.url = url or DEFAULT_URL
        connect_args = {"check_same_thread": False} if self.url.startswith("sqlite") else {}
        self.engine: Engine = create_engine(self.url, connect_args=connect_args, pool_pre_ping=True)
        Base.metadata.create_all(self.engine)

    @classmethod
    def from_env(cls) -> Database:
        return cls(os.environ.get("DATABASE_URL"))

    def save_run(self, run: RunResult) -> None:
        with Session(self.engine) as session, session.begin():
            existing = session.get(RunRow, run.id)
            if existing is not None:
                session.delete(existing)
                session.flush()
            row = RunRow(
                id=run.id,
                suite=run.suite,
                environment=run.environment,
                agent=run.agent,
                agent_name=str(run.agent.get("name", "")),
                agent_version=str(run.agent.get("version", "")),
                k=run.k,
                n=run.n,
                seed=run.seed,
                created_at=datetime.fromisoformat(run.created_at),
                git_sha=run.git_sha,
                label=run.label,
                summary=run.summary.model_dump(),
            )
            for position, task in enumerate(run.tasks):
                task_row = TaskRow(
                    position=position,
                    task_id=task.task_id,
                    description=task.description,
                    tags=task.tags,
                    n=task.n,
                    successes=task.successes,
                    pass_at_1=task.pass_at_1,
                    pass_at_k=task.pass_at_k,
                    pass_hat_k=task.pass_hat_k,
                )
                for trial in task.trials:
                    trial_row = TrialRow(
                        index=trial.index,
                        passed=trial.passed,
                        stop_reason=trial.stop_reason,
                        error=trial.error,
                        checks=trial.checks,
                        final_answer=trial.final_answer,
                        tool_calls=trial.tool_calls,
                        usage=trial.usage,
                        latency_ms=trial.latency_ms,
                    )
                    trial_row.spans = [
                        SpanRow(**{k: v for k, v in span.items() if k in _SPAN_FIELDS})
                        for span in trial.spans
                    ]
                    task_row.trials.append(trial_row)
                row.tasks.append(task_row)
            session.add(row)

    def list_runs(self, suite: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            query = select(RunRow).order_by(RunRow.created_at.desc(), RunRow.id).limit(limit)
            if suite:
                query = query.where(RunRow.suite == suite)
            return [
                {
                    "id": r.id,
                    "suite": r.suite,
                    "environment": r.environment,
                    "agent": r.agent,
                    "k": r.k,
                    "n": r.n,
                    "seed": r.seed,
                    "created_at": r.created_at.isoformat(),
                    "git_sha": r.git_sha,
                    "label": r.label,
                    "summary": r.summary,
                }
                for r in session.scalars(query)
            ]

    def get_run(self, run_id: str, with_spans: bool = False) -> RunResult | None:
        with Session(self.engine) as session:
            options = selectinload(RunRow.tasks).selectinload(TaskRow.trials)
            if with_spans:
                options = options.selectinload(TrialRow.spans)
            row = session.scalars(select(RunRow).where(RunRow.id == run_id).options(options)).first()
            if row is None:
                return None
            created = row.created_at
            return RunResult(
                id=row.id,
                suite=row.suite,
                environment=row.environment,
                agent=row.agent,
                k=row.k,
                n=row.n,
                seed=row.seed,
                created_at=created.isoformat(timespec="seconds")
                if created.tzinfo
                else created.isoformat() + "+00:00",
                git_sha=row.git_sha,
                label=row.label,
                summary=RunSummary(**row.summary),
                tasks=[
                    TaskResult(
                        task_id=t.task_id,
                        description=t.description,
                        tags=t.tags,
                        n=t.n,
                        successes=t.successes,
                        pass_at_1=t.pass_at_1,
                        pass_at_k=t.pass_at_k,
                        pass_hat_k=t.pass_hat_k,
                        trials=[_trial_model(tr, with_spans) for tr in t.trials],
                    )
                    for t in row.tasks
                ],
            )

    def get_trial(self, run_id: str, task_id: str, index: int) -> TrialResult | None:
        with Session(self.engine) as session:
            query = (
                select(TrialRow)
                .join(TaskRow)
                .where(TaskRow.run_id == run_id, TaskRow.task_id == task_id, TrialRow.index == index)
                .options(selectinload(TrialRow.spans))
            )
            row = session.scalars(query).first()
            return _trial_model(row, with_spans=True) if row else None

    def suites(self) -> list[str]:
        with Session(self.engine) as session:
            return sorted(set(session.scalars(select(RunRow.suite))))

    def count_runs(self) -> int:
        with Session(self.engine) as session:
            return len(session.scalars(select(RunRow.id)).all())


_SPAN_FIELDS = {
    "span_id",
    "trace_id",
    "parent_span_id",
    "name",
    "kind",
    "start_ns",
    "end_ns",
    "duration_ms",
    "status",
    "attributes",
}
