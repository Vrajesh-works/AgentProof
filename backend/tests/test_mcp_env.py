"""MCP environment against a real child process (the fake customer server)."""

from __future__ import annotations

import asyncio
import time

import pytest

from agentproof.agents import ScriptedAgent
from agentproof.envs.mcp import McpEnvironment, McpError, McpServerConfig
from agentproof.runner import RunOptions, run_suite
from agentproof.sandbox import Sandbox
from agentproof.suite import Budget, load_suite, parse_suite
from agentproof.tracing import TraceRecorder
from tests.conftest import SUITES

pytestmark = pytest.mark.integration

PROBES = {"customers/CUST-00042": {"tool": "get_customer_record", "args": {"customer_id": "CUST-00042"}}}


async def with_env(command, fn, **config):
    env = McpEnvironment(McpServerConfig(command=command, state_probes=PROBES, **config))
    await env.start()
    try:
        return await fn(env)
    finally:
        await env.close()


def test_lists_tools_and_snapshots_state(fake_mcp_command):
    async def go(env):
        assert {t.name for t in env.tool_schemas()} >= {"get_customer_record", "trigger_refund"}
        assert env.server_info["name"] == "fake-customer-mcp"
        return await env.snapshot()

    snap = asyncio.run(with_env(fake_mcp_command, go))
    assert snap["customers"]["CUST-00042"]["refundable_balance"] == 320.0


def test_result_channels(fake_mcp_command):
    async def go(env):
        ok = await env.call(
            "trigger_refund", {"customer_id": "CUST-00042", "amount": 20, "reason": "duplicate"}
        )
        refused = await env.call(
            "trigger_refund", {"customer_id": "CUST-01337", "amount": 5, "reason": "goodwill"}
        )
        invalid = await env.call("trigger_refund", {"customer_id": "CUST-00042", "amount": -1, "reason": "x"})
        unknown = await env.call("nope", {})
        return ok, refused, invalid, unknown, await env.snapshot()

    ok, refused, invalid, unknown, snap = asyncio.run(with_env(fake_mcp_command, go))
    assert ok.ok and ok.output["remaining_refundable_balance"] == 300.0
    assert refused.error_kind == "domain"  # isError result: a business refusal
    assert invalid.error_kind == "validation"  # JSON-RPC -32602
    assert unknown.error_kind == "protocol"
    assert snap["customers"]["CUST-00042"]["refundable_balance"] == 300.0


def test_stdout_noise_is_counted_not_fatal(fake_mcp_command):
    async def go(env):
        result = await env.call("get_customer_record", {"customer_id": "CUST-00007"})
        return result, env.stdout_noise_lines

    result, noise = asyncio.run(with_env(fake_mcp_command, go, env={"FAKE_MCP_NOISE": "1"}))
    assert result.ok
    assert noise >= 1


def test_hung_tool_times_out_and_server_is_killed(fake_mcp_command):
    recorder = TraceRecorder()

    async def go(env):
        sandbox = Sandbox(env, Budget(tool_timeout_s=0.5), recorder.tracer)
        started = time.perf_counter()
        hung = await sandbox.call_tool("slow_tool", {})
        elapsed = time.perf_counter() - started
        after = await sandbox.call_tool("get_customer_record", {"customer_id": "CUST-00042"})
        return hung, elapsed, after

    hung, elapsed, after = asyncio.run(with_env(fake_mcp_command, go))
    assert hung.error_kind == "timeout" and elapsed < 5
    assert after.error_kind == "crash"  # killed server stays dead for the rest of the trial


def test_crashing_server_fails_calls_cleanly(fake_mcp_command):
    async def go(env):
        crashed = await env.call("crash_tool", {})
        return crashed, await env.call("get_customer_record", {"customer_id": "CUST-00042"})

    crashed, after = asyncio.run(with_env(fake_mcp_command, go))
    assert crashed.error_kind == "crash"
    assert after.error_kind == "crash" and "exited" in after.error


def test_bad_command_raises(tmp_path):
    env = McpEnvironment(McpServerConfig(command=[str(tmp_path / "missing")]))
    with pytest.raises(McpError):
        asyncio.run(env.start())


def test_server_that_never_answers_initialize(tmp_path):
    import sys

    env = McpEnvironment(
        McpServerConfig(command=[sys.executable, "-c", "import time; time.sleep(30)"], startup_timeout_s=0.5)
    )
    with pytest.raises(McpError, match="initialize"):
        asyncio.run(env.start())


def test_quilrai_suite_runs_against_fake_server(fake_mcp_command):
    """The shipped QuilrAI suite, pointed at the fake server, scores 1.0 with fresh state per trial."""
    real = load_suite(SUITES / "quilrai_mcp.yaml")
    data = real.model_dump()
    data["environment"]["options"]["command"] = fake_mcp_command
    data["tasks"] = [
        t for t in data["tasks"] if t["id"] in {"refund-active-customer", "refuse-frozen-account"}
    ]
    suite = parse_suite(data)
    result = asyncio.run(run_suite(suite, ScriptedAgent(), RunOptions(k=2)))
    assert result.summary.pass_hat_k == 1.0, [c for t in result.tasks for tr in t.trials for c in tr.checks]


def test_cli_prints_why_a_trial_never_completed(tmp_path, capsys):
    """When the MCP server cannot start, the run log shows the server's stderr, not just 'completed'."""
    import sys

    import yaml

    from agentproof.cli import main

    data = load_suite(SUITES / "quilrai_mcp.yaml").model_dump(mode="json")
    data["environment"]["options"]["command"] = [
        sys.executable,
        "-c",
        "import sys; sys.stderr.write('interpreter too old'); sys.exit(2)",
    ]
    data["tasks"] = data["tasks"][:1]
    suite_file = tmp_path / "broken.yaml"
    suite_file.write_text(yaml.safe_dump(data), encoding="utf-8")

    assert main(["run", str(suite_file), "--n", "1", "--k", "1", "--min-pass-hat-k", "1.0"]) == 1
    err = capsys.readouterr().err
    assert "FAIL" in err
    assert "interpreter too old" in err
