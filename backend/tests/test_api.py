"""API and storage, on SQLite by default and on PostgreSQL when DATABASE_URL points there."""

from __future__ import annotations

import asyncio
import os

import pytest
from fastapi.testclient import TestClient

from agentproof.agents import ScriptedAgent, load_agent
from agentproof.api import create_app
from agentproof.db import Database
from agentproof.runner import RunOptions, run_suite
from agentproof.suite import load_suite
from tests.conftest import AGENTS, SUITES

pytestmark = pytest.mark.integration


@pytest.fixture
def db(tmp_path):
    url = os.environ.get("AGENTPROOF_TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'test.db'}"
    database = Database(url)
    if url.startswith("postgresql"):
        from agentproof.db import Base

        Base.metadata.drop_all(database.engine)
        Base.metadata.create_all(database.engine)
    return database


@pytest.fixture(scope="module")
def runs():
    suite = load_suite(SUITES / "retail.yaml")
    base = asyncio.run(run_suite(suite, ScriptedAgent(version="v1"), RunOptions(label="main")))
    head = asyncio.run(run_suite(suite, load_agent(str(AGENTS / "flaky.yaml")), RunOptions(label="pr")))
    return base, head


def test_round_trip_preserves_results(db, runs):
    base, _ = runs
    db.save_run(base)
    loaded = db.get_run(base.id, with_spans=True)
    assert loaded is not None
    assert loaded.scores() == base.scores()
    first = loaded.tasks[0].trials[0]
    assert first.spans == base.tasks[0].trials[0].spans
    assert db.get_run(base.id).tasks[0].trials[0].spans == []  # spans only on request
    db.save_run(base)  # re-import replaces instead of duplicating
    assert db.count_runs() == 1


def test_api_endpoints(db, runs):
    base, head = runs
    with TestClient(create_app(db, seed_demo=False)) as client:
        assert client.get("/api/health").json()["runs"] == 0
        assert client.post("/api/runs", json=base.model_dump()).status_code == 201
        assert client.post("/api/runs", json=head.model_dump()).status_code == 201
        listed = client.get("/api/runs").json()
        assert {r["id"] for r in listed} == {base.id, head.id}
        assert client.get("/api/suites").json() == ["retail-core"]

        detail = client.get(f"/api/runs/{base.id}").json()
        assert detail["summary"]["pass_hat_k"] == 1.0
        task_id = detail["tasks"][0]["task_id"]
        trial = client.get(f"/api/runs/{base.id}/tasks/{task_id}/trials/0").json()
        assert any(s["kind"] == "tool" for s in trial["spans"])

        diff = client.get("/api/diff", params={"base": base.id, "head": head.id}).json()
        assert diff["failed"] is True
        assert any(t["status"] == "regressed" for t in diff["tasks"])
        assert "AgentProof gate" in diff["markdown"]

        assert client.get("/api/runs/nope").status_code == 404
        assert client.get(f"/api/runs/{base.id}/tasks/x/trials/0").status_code == 404
        assert (
            client.get("/api/diff", params={"base": base.id, "head": head.id, "metrics": "bogus"}).status_code
            == 422
        )
        assert client.post("/api/runs", json={"id": "broken"}).status_code == 422


def test_demo_seed_on_startup(db):
    with TestClient(create_app(db, seed_demo=True)) as client:
        runs = client.get("/api/runs").json()
    assert len(runs) == 4
    labels = {r["label"] for r in runs}
    assert labels == {"main", "demo: flaky candidate"}
