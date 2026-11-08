"""REST API for the run viewer.

Endpoints are read-mostly: runs come in through ``POST /api/runs`` (a
``RunResult`` document from the CLI or CI) or from the demo seeder, and the
viewer reads runs, trials with their spans, and run-to-run diffs.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from agentproof.db import Database
from agentproof.gate import METRICS, compare
from agentproof.results import RunResult

log = logging.getLogger(__name__)


def create_app(db: Database | None = None, *, seed_demo: bool | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.db = db or Database.from_env()
        wants_seed = seed_demo if seed_demo is not None else os.environ.get("AGENTPROOF_SEED_DEMO") == "1"
        if wants_seed and app.state.db.count_runs() == 0:
            from agentproof.demo import seed

            ids = await seed(app.state.db)
            log.info("seeded demo runs %s", ids)
        yield

    app = FastAPI(title="AgentProof", version="0.1.0", lifespan=lifespan)
    origins = [o for o in os.environ.get("AGENTPROOF_CORS_ORIGINS", "").split(",") if o]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])

    def database() -> Database:
        return app.state.db

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "runs": database().count_runs()}

    @app.get("/api/suites")
    def suites() -> list[str]:
        return database().suites()

    @app.get("/api/runs")
    def list_runs(suite: str | None = None, limit: int = Query(200, ge=1, le=1000)) -> list[dict[str, Any]]:
        return database().list_runs(suite=suite, limit=limit)

    @app.post("/api/runs", status_code=201)
    def import_run(run: RunResult) -> dict[str, str]:
        database().save_run(run)
        return {"id": run.id}

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        run = database().get_run(run_id)
        if run is None:
            raise HTTPException(404, f"run {run_id} not found")
        return run.model_dump()

    @app.get("/api/runs/{run_id}/tasks/{task_id}/trials/{index}")
    def get_trial(run_id: str, task_id: str, index: int) -> dict[str, Any]:
        trial = database().get_trial(run_id, task_id, index)
        if trial is None:
            raise HTTPException(404, "trial not found")
        return trial.model_dump()

    @app.get("/api/diff")
    def diff(
        base: str,
        head: str,
        tolerance: float = Query(0.0, ge=0, le=1),
        metrics: str = "pass_hat_k,pass_at_k",
    ) -> dict[str, Any]:
        base_run, head_run = database().get_run(base), database().get_run(head)
        if base_run is None or head_run is None:
            raise HTTPException(404, "run not found")
        selected = tuple(m for m in metrics.split(",") if m)
        if not selected or set(selected) - set(METRICS):
            raise HTTPException(422, f"metrics must be a subset of {list(METRICS)}")
        try:
            report = compare(base_run, head_run, metrics=selected, tolerance=tolerance)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {**report.to_dict(), "markdown": report.markdown()}

    static_dir = os.environ.get("AGENTPROOF_STATIC_DIR")
    if static_dir and Path(static_dir).is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="viewer")

    return app


app = create_app()
