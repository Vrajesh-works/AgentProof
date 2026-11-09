from __future__ import annotations

import pytest

from agentproof.suite import Budget, BudgetOverride, SuiteError, interpolate_env, load_suite, parse_suite
from tests.conftest import SUITES


def minimal(**overrides):
    data = {
        "suite": "s1",
        "environment": {"type": "retail"},
        "tasks": [{"id": "t1", "instruction": "do it", "expected": {"state_diff": {}}}],
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize("name", ["retail.yaml", "airline.yaml", "quilrai_mcp.yaml"])
def test_shipped_suites_load(name):
    suite = load_suite(SUITES / name)
    assert suite.tasks
    assert len({t.id for t in suite.tasks}) == len(suite.tasks)


def test_defaults_and_budget_merge():
    suite = parse_suite(minimal())
    assert suite.k == 4
    merged = suite.budget.merged(BudgetOverride(max_steps=3))
    assert merged.max_steps == 3
    assert merged.max_tool_calls == Budget().max_tool_calls


def test_rejects_duplicate_task_ids():
    data = minimal()
    data["tasks"] = data["tasks"] * 2
    with pytest.raises(SuiteError, match="duplicate task id"):
        parse_suite(data)


def test_rejects_empty_expected_block():
    data = minimal()
    data["tasks"][0]["expected"] = {}
    with pytest.raises(SuiteError, match="empty 'expected'"):
        parse_suite(data)


def test_rejects_unknown_fields_and_bad_regex():
    data = minimal()
    data["tasks"][0]["expectd"] = {}
    with pytest.raises(SuiteError):
        parse_suite(data)
    data = minimal()
    data["tasks"][0]["expected"] = {"answer_regex": "("}
    with pytest.raises(SuiteError, match="invalid regex"):
        parse_suite(data)


def test_env_interpolation_only_touches_environment():
    data = minimal(environment={"type": "mcp", "options": {"command": ["${SERVER_BIN}", "${MODE:-stdio}"]}})
    data["tasks"][0]["instruction"] = "literal ${SERVER_BIN}"
    suite = parse_suite(data, environ={"SERVER_BIN": "/opt/server"})
    assert suite.environment.options["command"] == ["/opt/server", "stdio"]
    assert suite.tasks[0].instruction == "literal ${SERVER_BIN}"


def test_missing_env_var_without_default_is_an_error():
    with pytest.raises(SuiteError, match="NOPE"):
        interpolate_env("${NOPE}", {})


def test_unreadable_file(tmp_path):
    with pytest.raises(SuiteError):
        load_suite(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("- just\n- a list\n")
    with pytest.raises(SuiteError, match="mapping"):
        load_suite(bad)
