"""Environment backed by an MCP server spoken to over stdio.

AgentProof ships its own small JSON-RPC client instead of depending on an MCP
SDK: it only needs ``initialize``, ``tools/list`` and ``tools/call``, and owning
the transport makes per-call timeouts, process-group kills and stdout-purity
accounting straightforward.

Each trial gets a fresh server process, so in-memory server state cannot leak
between trials. End state is observed through ``state_probes``: read-only tool
calls made before and after the trial whose results form the state snapshot.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
from dataclasses import dataclass, field
from typing import Any

from agentproof.envs.base import Environment, ToolResult, ToolSchema

PROTOCOL_VERSION = "2025-06-18"
_STDERR_KEEP = 64 * 1024


class McpError(RuntimeError):
    pass


@dataclass
class McpServerConfig:
    command: list[str]
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    startup_timeout_s: float = 60.0
    state_probes: dict[str, dict[str, Any]] = field(default_factory=dict)
    cpu_seconds: int | None = 120
    memory_mb: int | None = None

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> McpServerConfig:
        command = options.get("command")
        if isinstance(command, str):
            command = command.split()
        if not command:
            raise McpError("mcp environment needs options.command")
        return cls(
            command=[str(part) for part in command],
            cwd=options.get("cwd"),
            env={str(k): str(v) for k, v in (options.get("env") or {}).items()},
            startup_timeout_s=float(options.get("startup_timeout_s", 60.0)),
            state_probes=dict(options.get("state_probes") or {}),
            cpu_seconds=options.get("cpu_seconds", 120),
            memory_mb=options.get("memory_mb"),
        )


def _limits(cpu_seconds: int | None, memory_mb: int | None):  # pragma: no cover - runs in the child
    def apply() -> None:
        import resource

        if cpu_seconds:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        if memory_mb:
            limit = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

    return apply


class McpEnvironment(Environment):
    name = "mcp"

    def __init__(self, config: McpServerConfig):
        self.config = config
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._reader: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr = bytearray()
        self._tools: list[ToolSchema] = []
        self.server_info: dict[str, Any] = {}
        self.stdout_noise_lines = 0
        self.dead_reason: str | None = None

    # -- lifecycle -----------------------------------------------------------------

    async def start(self) -> None:
        env = {**os.environ, **self.config.env}
        kwargs: dict[str, Any] = {}
        if sys.platform != "win32":
            kwargs["start_new_session"] = True
            kwargs["preexec_fn"] = _limits(self.config.cpu_seconds, self.config.memory_mb)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self.config.command,
                cwd=self.config.cwd,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=16 * 1024 * 1024,
                **kwargs,
            )
        except OSError as exc:
            raise McpError(f"cannot start MCP server {self.config.command!r}: {exc}") from None
        self._reader = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())
        try:
            init = await asyncio.wait_for(
                self._request(
                    "initialize",
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "agentproof", "version": "0.1.0"},
                    },
                ),
                timeout=self.config.startup_timeout_s,
            )
            self.server_info = init.get("serverInfo", {})
            await self._notify("notifications/initialized", {})
            listed = await asyncio.wait_for(
                self._request("tools/list", {}), timeout=self.config.startup_timeout_s
            )
        except (TimeoutError, McpError) as exc:
            await self.close()
            raise McpError(
                f"MCP server failed to initialize: {exc or 'timeout'}; stderr: {self.stderr_tail()}"
            ) from None
        self._tools = [
            ToolSchema(t["name"], t.get("description", ""), t.get("inputSchema") or {"type": "object"})
            for t in listed.get("tools", [])
        ]

    async def close(self) -> None:
        proc = self._proc
        if proc is not None and proc.returncode is None:
            if proc.stdin is not None:
                with contextlib.suppress(Exception):
                    proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except TimeoutError:
                self._kill()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(proc.wait(), timeout=5)
        for task in (self._reader, self._stderr_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._fail_pending("server closed")

    def _kill(self) -> None:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            if sys.platform != "win32":
                os.killpg(proc.pid, signal.SIGKILL)
            else:  # pragma: no cover
                proc.kill()

    async def on_timeout(self, tool: str) -> None:
        """Called by the sandbox when a tool call times out: the server is presumed hung."""
        self.dead_reason = f"server killed after {tool!r} timed out"
        self._kill()
        self._fail_pending(self.dead_reason)

    # -- transport -----------------------------------------------------------------

    async def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                break
            text = line.strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                self.stdout_noise_lines += 1
                continue
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                self.stdout_noise_lines += 1
                continue
            msg_id = message.get("id")
            if msg_id is not None and ("result" in message or "error" in message):
                future = self._pending.pop(msg_id, None)
                if future is not None and not future.done():
                    future.set_result(message)
            elif msg_id is not None and "method" in message:
                # Server-to-client request (sampling, roots...): not supported, answer politely.
                await self._send(
                    {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": "not supported"}}
                )
        code = await self._proc.wait()
        if self.dead_reason is None:
            self.dead_reason = f"server exited with code {code}"
        self._fail_pending(self.dead_reason)

    async def _read_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        while True:
            chunk = await self._proc.stderr.read(4096)
            if not chunk:
                return
            self._stderr.extend(chunk)
            if len(self._stderr) > _STDERR_KEEP:
                del self._stderr[: len(self._stderr) - _STDERR_KEEP]

    def stderr_tail(self, limit: int = 2000) -> str:
        return self._stderr[-limit:].decode("utf-8", errors="replace")

    def _fail_pending(self, reason: str) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(McpError(reason))
        self._pending.clear()

    async def _send(self, message: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None or self._proc.returncode is not None:
            raise McpError(self.dead_reason or "server is not running")
        self._proc.stdin.write((json.dumps(message) + "\n").encode())
        try:
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise McpError(f"server pipe closed: {exc}") from None

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.dead_reason is not None:
            raise McpError(self.dead_reason)
        msg_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        try:
            await self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
            message = await future
        finally:
            self._pending.pop(msg_id, None)
        if "error" in message:
            error = message["error"] or {}
            raise _RpcError(
                int(error.get("code", -32603)), str(error.get("message", "error")), error.get("data")
            )
        return message.get("result") or {}

    # -- Environment API -------------------------------------------------------------

    def tool_schemas(self) -> list[ToolSchema]:
        return list(self._tools)

    async def call(self, tool: str, args: dict[str, Any]) -> ToolResult:
        try:
            result = await self._request("tools/call", {"name": tool, "arguments": args})
        except _RpcError as exc:
            kind = "validation" if exc.code == -32602 else "protocol"
            return ToolResult(ok=False, error=f"JSON-RPC {exc.code}: {exc.message}", error_kind=kind)
        except McpError as exc:
            return ToolResult(ok=False, error=str(exc), error_kind="crash")
        output = _result_payload(result)
        if result.get("isError"):
            message = output if isinstance(output, str) else json.dumps(output, sort_keys=True)
            return ToolResult(ok=False, error=message, error_kind="domain", output=output)
        return ToolResult(ok=True, output=output)

    async def snapshot(self) -> dict[str, Any]:
        state: dict[str, Any] = {}
        for path, probe in sorted(self.config.state_probes.items()):
            result = await self.call(str(probe["tool"]), dict(probe.get("args") or {}))
            state[path] = result.output if result.ok else {"error": result.error, "kind": result.error_kind}
        return _nest(state)


class _RpcError(McpError):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.data = data


def _result_payload(result: dict[str, Any]) -> Any:
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    texts = [block.get("text", "") for block in result.get("content", []) if block.get("type") == "text"]
    text = "\n".join(texts)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text


def _nest(flat: dict[str, Any]) -> dict[str, Any]:
    """Turn ``{"customers/CUST-1": {...}}`` into ``{"customers": {"CUST-1": {...}}}``."""
    out: dict[str, Any] = {}
    for path, value in flat.items():
        parts = [p for p in path.split("/") if p]
        node = out
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return out
