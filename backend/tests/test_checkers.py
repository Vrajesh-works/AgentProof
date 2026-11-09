from __future__ import annotations

from agentproof.checkers import (
    DELETED,
    Observation,
    args_match,
    check_answer,
    check_probes,
    check_required_calls,
    check_state_diff,
    check_tool_order,
    run_deterministic,
    state_diff,
)
from agentproof.envs.base import ToolResult
from agentproof.sandbox import ToolCallRecord
from agentproof.suite import Expected, Probe, ToolCallSpec

BEFORE = {"orders": {"W1": {"status": "pending", "total": 10.0}, "W2": {"status": "done"}}, "log": []}


def obs(after=None, calls=(), answer=""):
    log = [ToolCallRecord(tool, args, ok) for tool, args, ok in calls]
    return Observation(BEFORE, after if after is not None else BEFORE, log, answer)


def test_state_diff_reports_changes_additions_and_deletions():
    after = {"orders": {"W1": {"status": "cancelled", "total": 10.0, "reason": "x"}}, "log": ["a"]}
    diff = state_diff(BEFORE, after)
    assert diff == {
        "orders/W1/status": "cancelled",
        "orders/W1/reason": "x",
        "orders/W2/status": DELETED,
        "log": ["a"],
    }


def test_state_diff_check_passes_and_rejects_side_effects():
    after = {"orders": {"W1": {"status": "cancelled", "total": 10.0}, "W2": {"status": "done"}}, "log": []}
    expected = Expected(state_diff={"orders/W1/status": "cancelled"})
    assert check_state_diff(obs(after), expected).passed
    side_effect = {
        "orders": {"W1": {"status": "cancelled", "total": 10.0}, "W2": {"status": "reopened"}},
        "log": [],
    }
    result = check_state_diff(obs(side_effect), expected)
    assert not result.passed and "unexpected change" in result.detail
    relaxed = Expected(state_diff={"orders/W1/status": "cancelled"}, allow_extra_state_changes=True)
    assert check_state_diff(obs(side_effect), relaxed).passed


def test_empty_state_diff_means_nothing_may_change():
    assert check_state_diff(obs(), Expected(state_diff={})).passed
    changed = {"orders": {"W1": {"status": "x", "total": 10.0}, "W2": {"status": "done"}}, "log": []}
    assert not check_state_diff(obs(changed), Expected(state_diff={})).passed


def test_state_diff_nested_expected_values_and_float_tolerance():
    after = {
        "orders": {
            "W1": {"status": "pending", "total": 10.0000001, "addr": {"zip": "1"}},
            "W2": {"status": "done"},
        },
        "log": [],
    }
    expected = Expected(state_diff={"orders/W1/addr": {"zip": "1"}})
    assert check_state_diff(obs(after), expected).passed


def test_tool_order_subsequence_and_exact():
    o = obs(calls=[("find", {}, True), ("get", {}, True), ("cancel", {}, True)])
    assert check_tool_order(o, Expected(tool_order=["get", "cancel"])).passed
    assert not check_tool_order(o, Expected(tool_order=["cancel", "get"])).passed
    assert not check_tool_order(o, Expected(tool_order=["get", "cancel"], tool_order_mode="exact")).passed
    assert check_tool_order(o, Expected(tool_order=["find", "get", "cancel"], tool_order_mode="exact")).passed


def test_required_calls_match_arg_subsets_and_need_success():
    o = obs(
        calls=[
            ("refund", {"id": "C1", "amount": 45.5, "reason": "dup"}, True),
            ("refund", {"id": "C2"}, False),
        ]
    )
    assert check_required_calls(
        o, Expected(required_calls=[ToolCallSpec(tool="refund", args={"amount": 45.5})])
    ).passed
    assert not check_required_calls(
        o, Expected(required_calls=[ToolCallSpec(tool="refund", args={"id": "C2"})])
    ).passed


def test_args_match_lists_are_order_insensitive():
    assert args_match({"ids": ["b", "a"]}, {"ids": ["a", "b"]})
    assert not args_match({"ids": ["a"]}, {"ids": ["a", "b"]})


def test_answer_checks():
    expected = Expected(answer_contains=["Cancelled"], answer_not_contains=["sorry"], answer_regex=r"W\d+")
    results = check_answer(obs(answer="Order W1 is cancelled."), expected)
    assert all(r.passed for r in results)
    results = check_answer(obs(answer="sorry, no"), expected)
    assert [r.passed for r in results] == [False, False, False]


def test_answer_schema_accepts_fenced_json_and_reports_path():
    schema = {"type": "object", "required": ["n"], "properties": {"n": {"const": 1}}}
    expected = Expected(answer_schema=schema)
    assert check_answer(obs(answer='```json\n{"n": 1}\n```'), expected)[0].passed
    bad = check_answer(obs(answer='{"n": 2}'), expected)[0]
    assert not bad.passed and bad.detail.startswith("n:")
    assert "not valid JSON" in check_answer(obs(answer="n is 1"), expected)[0].detail


def test_probes():
    probe = Probe(tool="get", args={"id": "C1"}, expect={"balance": 274.5})
    good = Observation({}, {}, [], "", [(probe, ToolResult(ok=True, output={"balance": 274.5, "name": "x"}))])
    assert check_probes(good).passed
    bad = Observation({}, {}, [], "", [(probe, ToolResult(ok=True, output={"balance": 320.0}))])
    assert not check_probes(bad).passed
    erroring = Probe(tool="get", args={}, expect_error=True)
    assert check_probes(Observation({}, {}, [], "", [(erroring, ToolResult(ok=False, error="nope"))])).passed


def test_run_deterministic_only_runs_requested_checks():
    names = [r.name for r in run_deterministic(obs(), Expected(state_diff={}, forbidden_tools=["x"]))]
    assert names == ["state_diff", "forbidden_tools"]
