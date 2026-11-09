"""Demo data: run the shipped suites with the baseline and the flaky agent.

Gives the viewer something real to show on first start (``docker compose up``)
without any API key: every number comes from actually running the suites.
"""

from __future__ import annotations

import os
from pathlib import Path

from agentproof.agents import load_agent
from agentproof.db import Database
from agentproof.runner import RunOptions, run_suite
from agentproof.suite import load_suite


def repo_root() -> Path:
    env = os.environ.get("AGENTPROOF_HOME")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3]


async def seed(db: Database, root: Path | None = None) -> list[str]:
    root = root or repo_root()
    ids = []
    for suite_file in ("retail.yaml", "airline.yaml"):
        suite = load_suite(root / "suites" / suite_file)
        for agent_file, label in (("baseline.yaml", "main"), ("flaky.yaml", "demo: flaky candidate")):
            agent = load_agent(str(root / "agents" / agent_file))
            run = await run_suite(suite, agent, RunOptions(label=label))
            db.save_run(run)
            ids.append(run.id)
    return ids
