from __future__ import annotations

import asyncio
import json

import pytest

from agentproof.agents import ScriptedAgent, load_agent
from agentproof.cli import main
from agentproof.gate import compare
from agentproof.runner import RunOptions, run_suite
from agentproof.suite import load_suite
from tests.conftest import AGENTS, SUITES

SUITE = load_suite(SUITES / "retail.yaml")


@pytest.fixture(scope="module")
def baseline():
    return asyncio.run(run_suite(SUITE, ScriptedAgent(version="v1"), RunOptions()))


@pytest.fixture(scope="module")
def regressed():
    agent = ScriptedAgent(
        version="v2", seed=2, fault_modes=("bad_answer",), task_fault_rates={"cancel-pending-order": 0.5}
    )
    return asyncio.run(run_suite(SUITE, agent, RunOptions()))


def test_identical_runs_pass(baseline):
    again = asyncio.run(run_suite(SUITE, ScriptedAgent(version="v1"), RunOptions()))
    report = compare(baseline, again)
    assert not report.failed
    assert {t.status for t in report.tasks} == {"unchanged"}


def test_regression_fails_and_names_the_task(baseline, regressed):
    report = compare(baseline, regressed)
    assert report.failed
    assert [t.task_id for t in report.regressed_tasks] == ["cancel-pending-order"]
    md = report.markdown()
    assert "FAILED" in md and "`cancel-pending-order` | regressed" in md


def test_tolerance_absorbs_small_drops(baseline, regressed):
    assert not compare(baseline, regressed, tolerance=1.0).failed


def test_improvement_and_missing_tasks(baseline, regressed):
    report = compare(regressed, baseline)
    assert not report.failed
    assert report.tasks[[t.task_id for t in report.tasks].index("cancel-pending-order")].status == "improved"
    partial = baseline.model_copy(update={"tasks": baseline.tasks[:-1]})
    assert compare(baseline, partial).failed
    assert not compare(baseline, partial, fail_on_missing=False).failed
    assert compare(partial, baseline).tasks[-1].status in {"new", "unchanged"}


def test_rejects_different_suites_and_unknown_metrics(baseline):
    other = baseline.model_copy(update={"suite": "other"})
    with pytest.raises(ValueError):
        compare(baseline, other)
    with pytest.raises(ValueError):
        compare(baseline, baseline, metrics=("accuracy",))


def test_cli_run_and_gate_exit_codes(tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    base, head, flaky = tmp_path / "base.json", tmp_path / "head.json", tmp_path / "flaky.json"
    suite = str(SUITES / "retail.yaml")
    assert main(["run", suite, "--agent", str(AGENTS / "baseline.yaml"), "--out", str(base), "--quiet"]) == 0
    assert main(["run", suite, "--agent", str(AGENTS / "candidate.yaml"), "--out", str(head), "--quiet"]) == 0
    assert main(["gate", "--base", str(base), "--head", str(head)]) == 0
    assert main(["run", suite, "--agent", str(AGENTS / "flaky.yaml"), "--out", str(flaky), "--quiet"]) == 0
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    report_json = tmp_path / "report.json"
    assert main(["gate", "--base", str(base), "--head", str(flaky), "--json", str(report_json)]) == 1
    assert "FAILED" in summary.read_text()
    assert json.loads(report_json.read_text())["failed"] is True


def test_cli_min_pass_hat_k(tmp_path):
    flaky = str(AGENTS / "flaky.yaml")
    assert (
        main(["run", str(SUITES / "retail.yaml"), "--agent", flaky, "--min-pass-hat-k", "1.0", "--quiet"])
        == 1
    )


def test_cli_validate(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("suite: x\n")
    assert main(["validate", str(SUITES / "retail.yaml")]) == 0
    assert main(["validate", str(bad)]) == 1


def test_flaky_demo_agent_actually_regresses_retail():
    flaky = asyncio.run(run_suite(SUITE, load_agent(str(AGENTS / "flaky.yaml")), RunOptions()))
    assert flaky.summary.pass_hat_k < 1.0
