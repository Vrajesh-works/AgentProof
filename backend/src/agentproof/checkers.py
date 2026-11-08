"""Deterministic checkers.

Each checker looks at one aspect of a finished trial and returns a
``CheckResult``. A trial passes only if every applicable check passes. The
checks run in a fixed order so results are stable and easy to diff.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

import jsonschema

from agentproof.sandbox import ToolCallRecord
from agentproof.suite import Expected

DELETED = "__deleted__"


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class Observation:
    initial_state: dict[str, Any]
    final_state: dict[str, Any]
    tool_log: list[ToolCallRecord]
    answer: str
    probe_results: list[tuple[Any, Any]] = field(default_factory=list)  # (Probe, ToolResult)


# -- state diff ----------------------------------------------------------------------


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested dicts to ``{"a/b/c": leaf}``. Lists are leaves."""
    if isinstance(value, dict) and value:
        out: dict[str, Any] = {}
        for key, item in value.items():
            out.update(flatten(item, f"{prefix}/{key}" if prefix else str(key)))
        return out
    return {prefix: value}


def state_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Leaf-level changes from ``before`` to ``after``; removed leaves map to ``DELETED``."""
    flat_before, flat_after = flatten(before), flatten(after)
    diff: dict[str, Any] = {}
    for path, value in flat_after.items():
        if path not in flat_before or not values_equal(flat_before[path], value):
            diff[path] = value
    for path in flat_before:
        if path not in flat_after:
            diff[path] = DELETED
    return diff


def values_equal(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(values_equal(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(values_equal(a[k], b[k]) for k in a)
    return a == b


def check_state_diff(obs: Observation, expected: Expected) -> CheckResult:
    actual = state_diff(obs.initial_state, obs.final_state)
    wanted: dict[str, Any] = {}
    for path, value in (expected.state_diff or {}).items():
        path = path.strip("/")
        wanted.update(flatten(value, path) if isinstance(value, dict) else {path: value})
    problems = []
    for path, value in sorted(wanted.items()):
        if path not in actual:
            problems.append(f"{path}: expected {value!r}, unchanged")
        elif not values_equal(actual[path], value):
            problems.append(f"{path}: expected {value!r}, got {actual[path]!r}")
    if not expected.allow_extra_state_changes:
        extra = sorted(set(actual) - set(wanted))
        problems.extend(f"{path}: unexpected change to {actual[path]!r}" for path in extra)
    if problems:
        return CheckResult("state_diff", False, "; ".join(problems[:10]))
    return CheckResult("state_diff", True, f"{len(wanted)} expected change(s) matched")


# -- tool calls ---------------------------------------------------------------------


def _called(obs: Observation) -> list[str]:
    return [record.tool for record in obs.tool_log]


def check_tool_order(obs: Observation, expected: Expected) -> CheckResult:
    order = expected.tool_order or []
    calls = _called(obs)
    if expected.tool_order_mode == "exact":
        ok = calls == order
        return CheckResult("tool_order", ok, "" if ok else f"expected exactly {order}, got {calls}")
    position = 0
    for tool in calls:
        if position < len(order) and tool == order[position]:
            position += 1
    ok = position == len(order)
    detail = "" if ok else f"expected {order} as a subsequence of {calls}; stuck at {order[position]!r}"
    return CheckResult("tool_order", ok, detail)


def args_match(actual: Any, wanted: Any) -> bool:
    """``wanted`` matches if every key it names is present in ``actual`` with an equal value."""
    if isinstance(wanted, dict):
        return isinstance(actual, dict) and all(
            k in actual and args_match(actual[k], v) for k, v in wanted.items()
        )
    if isinstance(wanted, list) and isinstance(actual, list):
        return sorted(map(json.dumps, actual)) == sorted(map(json.dumps, wanted))
    return values_equal(actual, wanted)


def check_required_calls(obs: Observation, expected: Expected) -> CheckResult:
    missing = []
    for spec in expected.required_calls:
        if not any(r.tool == spec.tool and r.ok and args_match(r.args, spec.args) for r in obs.tool_log):
            missing.append(f"{spec.tool}({json.dumps(spec.args, sort_keys=True)})")
    if missing:
        return CheckResult("required_calls", False, "no successful call " + ", ".join(missing))
    return CheckResult("required_calls", True, f"{len(expected.required_calls)} required call(s) found")


def check_forbidden_tools(obs: Observation, expected: Expected) -> CheckResult:
    used = sorted({tool for tool in _called(obs) if tool in expected.forbidden_tools})
    return CheckResult("forbidden_tools", not used, f"called forbidden {used}" if used else "")


# -- final answer ------------------------------------------------------------------


def check_answer(obs: Observation, expected: Expected) -> list[CheckResult]:
    results = []
    answer = obs.answer or ""
    lowered = answer.lower()
    if expected.answer_contains:
        missing = [s for s in expected.answer_contains if s.lower() not in lowered]
        results.append(CheckResult("answer_contains", not missing, f"missing {missing}" if missing else ""))
    if expected.answer_not_contains:
        present = [s for s in expected.answer_not_contains if s.lower() in lowered]
        results.append(
            CheckResult("answer_not_contains", not present, f"contains {present}" if present else "")
        )
    if expected.answer_regex:
        ok = re.search(expected.answer_regex, answer) is not None
        results.append(
            CheckResult("answer_regex", ok, "" if ok else f"no match for /{expected.answer_regex}/")
        )
    if expected.answer_schema is not None:
        results.append(_check_schema(answer, expected.answer_schema))
    return results


def _extract_json(text: str) -> Any:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    return json.loads(text)


def _check_schema(answer: str, schema: dict[str, Any]) -> CheckResult:
    try:
        data = _extract_json(answer)
    except (json.JSONDecodeError, ValueError):
        return CheckResult("answer_schema", False, "final answer is not valid JSON")
    try:
        jsonschema.validate(data, schema)
    except jsonschema.ValidationError as exc:
        where = "/".join(str(p) for p in exc.absolute_path) or "(root)"
        return CheckResult("answer_schema", False, f"{where}: {exc.message}")
    return CheckResult("answer_schema", True, "")


# -- probes ------------------------------------------------------------------------


def check_probes(obs: Observation) -> CheckResult:
    problems = []
    for probe, result in obs.probe_results:
        label = f"{probe.tool}({json.dumps(probe.args, sort_keys=True)})"
        if probe.expect_error:
            if result.ok:
                problems.append(f"{label}: expected an error")
            continue
        if not result.ok:
            problems.append(f"{label}: {result.error}")
        elif not args_match(result.output, probe.expect):
            problems.append(f"{label}: got {json.dumps(result.output, sort_keys=True)[:300]}")
    if problems:
        return CheckResult("probes", False, "; ".join(problems))
    return CheckResult("probes", True, f"{len(obs.probe_results)} probe(s) matched")


def run_deterministic(obs: Observation, expected: Expected) -> list[CheckResult]:
    results: list[CheckResult] = []
    if expected.state_diff is not None:
        results.append(check_state_diff(obs, expected))
    if expected.tool_order is not None:
        results.append(check_tool_order(obs, expected))
    if expected.required_calls:
        results.append(check_required_calls(obs, expected))
    if expected.forbidden_tools:
        results.append(check_forbidden_tools(obs, expected))
    results.extend(check_answer(obs, expected))
    if expected.probes:
        results.append(check_probes(obs))
    return results
