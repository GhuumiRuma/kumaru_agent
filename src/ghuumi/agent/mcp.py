"""MCP client: lets the agent call the tools of any Model Context Protocol server.

`agent.mcp_servers` (or ``ghuumi chat --mcp-file``, which reads the common
``{"mcpServers": {...}}`` JSON) names servers. At start-up each one is
connected, its tools are listed, and every tool becomes a `Tool` called
``mcp_<server>_<tool>`` that the model can request like any other.

Two transports, no extra dependency: a local program speaking JSON-RPC over
stdio, and a remote streamable-HTTP endpoint (JSON or event-stream replies).

Safety
------
A server is code someone else wrote, and what it returns is untrusted text.
So an MCP tool is never read-only by default: each call shows the server, tool
and arguments and runs only if you say yes. ``auto_approve`` lists the tools you
trust to run unasked. Like the project-folder tools, MCP tools are withheld
where nobody can approve (HTTP server, web UI, eval).
"""

from __future__ import annotations

import atexit
import contextlib
import itertools
import json
import os
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from typing import Any

from ghuumi.agent.tools import Action, Tool
from ghuumi.core.config import McpServerConfig
from ghuumi.core.errors import ConfigError, ToolError
from ghuumi.core.logging import get_logger

logger = get_logger(__name__)

PROTOCOL_VERSION = "2025-06-18"
_MAX_TOOL_NAME = 64


class McpError(ToolError):
    """A server failed to start, answered with an error, or stopped answering."""


class McpConnection:
    """One live connection to one server. Blocking; callers use `asyncio.to_thread`."""

    def __init__(self, config: McpServerConfig) -> None:
        self.config = config
        self._ids = itertools.count(1)
        self._proc: subprocess.Popen[bytes] | None = None
        self._cond = threading.Condition()
        self._replies: dict[int, dict[str, Any]] = {}
        self._closed = False
        self._write_lock = threading.Lock()
        self._session: str | None = None

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self.config.command:
            self._spawn()
        self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "ghuumi", "version": "1"},
            },
        )
        self.notify("notifications/initialized")

    def _spawn(self) -> None:
        command = self.config.command or ""
        program = shutil.which(command) or command  # finds npx.cmd and friends on Windows
        env = {**os.environ, **self.config.env}
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - the user configured this program
                [program, *self.config.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
            )
        except OSError as exc:
            raise McpError(f"cannot start '{command}': {exc}") from exc
        threading.Thread(target=self._read_stdout, daemon=True).start()

    def close(self) -> None:
        self._closed = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                if proc.stdin:
                    proc.stdin.close()
                proc.terminate()
                proc.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
        with self._cond:
            self._cond.notify_all()

    # -- JSON-RPC -----------------------------------------------------------

    def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        ident = next(self._ids)
        message = {"jsonrpc": "2.0", "id": ident, "method": method, "params": params or {}}
        if self.config.url:
            reply = self._http(message, ident)
        else:
            self._send(message)
            reply = self._wait(ident)
        if "error" in reply:
            err = reply["error"]
            raise McpError(f"{self.config.name}: {err.get('message', err)}")
        return reply.get("result")

    def notify(self, method: str) -> None:
        message = {"jsonrpc": "2.0", "method": method}
        if self.config.url:
            self._http(message, None)
        else:
            self._send(message)

    def _send(self, message: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None or proc.stdin is None:
            raise McpError(f"MCP server '{self.config.name}' is not running")
        line = (json.dumps(message) + "\n").encode("utf-8")
        with self._write_lock:
            try:
                proc.stdin.write(line)
                proc.stdin.flush()
            except OSError as exc:
                raise McpError(f"MCP server '{self.config.name}' closed its input: {exc}") from exc

    def _read_stdout(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        for raw in proc.stdout:
            try:
                message = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue  # servers may log to stdout; not a JSON-RPC line
            if not isinstance(message, dict):
                continue
            if "method" in message:
                if "id" in message:  # a request from the server: we offer no capabilities
                    reply = {"jsonrpc": "2.0", "id": message["id"]}
                    if message["method"] == "ping":
                        reply["result"] = {}
                    else:
                        reply["error"] = {"code": -32601, "message": "not supported"}
                    with contextlib.suppress(McpError):
                        self._send(reply)
                continue
            with self._cond:
                self._replies[message.get("id")] = message
                self._cond.notify_all()
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def _wait(self, ident: int) -> dict[str, Any]:
        with self._cond:
            ok = self._cond.wait_for(
                lambda: ident in self._replies or self._closed, timeout=self.config.timeout_s
            )
            if ident in self._replies:
                return self._replies.pop(ident)
        if not ok:
            raise McpError(f"MCP server '{self.config.name}' did not answer in {self.config.timeout_s}s")
        raise McpError(f"MCP server '{self.config.name}' stopped")

    def _http(self, message: dict[str, Any], ident: int | None) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
            **self.config.headers,
        }
        if self._session:
            headers["Mcp-Session-Id"] = self._session
        req = urllib.request.Request(  # noqa: S310 - scheme checked by the config pattern
            self.config.url or "", json.dumps(message).encode("utf-8"), headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout_s) as resp:  # noqa: S310
                self._session = resp.headers.get("Mcp-Session-Id") or self._session
                if ident is None:
                    return {}
                body = resp.read().decode("utf-8", "replace")
                kind = resp.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            raise McpError(f"{self.config.name}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise McpError(f"{self.config.name}: {exc}") from exc
        payloads = _sse_payloads(body) if "text/event-stream" in kind else [body]
        for payload in payloads:
            try:
                data = json.loads(payload)
            except ValueError:
                continue
            for item in data if isinstance(data, list) else [data]:
                if isinstance(item, dict) and item.get("id") == ident:
                    return item
        raise McpError(f"{self.config.name}: no reply to '{message.get('method')}'")

    # -- MCP methods --------------------------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(20):  # bounded: a server cannot page forever
            result = self.request("tools/list", {"cursor": cursor} if cursor else {}) or {}
            tools.extend(t for t in result.get("tools", []) if isinstance(t, dict) and t.get("name"))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        result = self.request("tools/call", {"name": name, "arguments": arguments}) or {}
        parts: list[str] = []
        for item in result.get("content", []):
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, dict) and item.get("type") == "resource":
                res = item.get("resource") or {}
                parts.append(str(res.get("text") or f"[resource {res.get('uri', '')}]"))
            elif isinstance(item, dict):
                parts.append(f"[{item.get('type', 'content')} omitted]")
        if not parts and result.get("structuredContent") is not None:
            parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
        text = "\n".join(parts) or "(no output)"
        return f"ERROR: {text}" if result.get("isError") else text


def _sse_payloads(body: str) -> list[str]:
    """The ``data:`` payloads of an event-stream body, one string per event."""
    events: list[str] = []
    current: list[str] = []
    for line in body.splitlines():
        if line.startswith("data:"):
            current.append(line[5:].lstrip())
        elif not line.strip() and current:
            events.append("\n".join(current))
            current = []
    if current:
        events.append("\n".join(current))
    return events


class McpTool(Tool):
    """One server tool, shown to the model under a prefixed, schema-faithful name."""

    read_only = False

    def __init__(self, conn: McpConnection, spec: dict[str, Any], name: str) -> None:
        self.conn = conn
        self.remote_name = str(spec["name"])
        self.name = name
        server = conn.config.name
        self.description = f"[MCP server {server}] {spec.get('description') or self.remote_name}"
        schema = spec.get("inputSchema")
        self.parameters = schema if isinstance(schema, dict) and schema else {"type": "object", "properties": {}}
        self.timeout_s = float(conn.config.timeout_s) + 5
        # Only what the user listed runs unasked; a server's own hints are not trusted.
        self.read_only = self.remote_name in conn.config.auto_approve

    async def run(self, **kwargs: Any) -> str:
        import asyncio

        return await asyncio.to_thread(self.conn.call_tool, self.remote_name, kwargs)

    async def prepare(self, **kwargs: Any) -> Action:
        shown = json.dumps(kwargs, ensure_ascii=False, sort_keys=True, default=str)
        where = self.conn.config.url or f"{self.conn.config.command} {' '.join(self.conn.config.args)}"
        return Action(
            summary=f"MCP server '{self.conn.config.name}' ({where})\ntool: {self.remote_name}\narguments: {shown}",
            apply=lambda: self.run(**kwargs),
        )


def tool_name(server: str, tool: str, taken: set[str]) -> str:
    """``mcp_<server>_<tool>``, restricted to what model APIs accept and kept unique."""
    base = re.sub(r"[^A-Za-z0-9_-]", "_", f"mcp_{server}_{tool}")[:_MAX_TOOL_NAME]
    name, n = base, 2
    while name in taken:
        suffix = f"_{n}"
        name, n = base[: _MAX_TOOL_NAME - len(suffix)] + suffix, n + 1
    return name


def connect_servers(
    servers: list[McpServerConfig],
) -> tuple[dict[str, McpTool], list[McpConnection], list[str]]:
    """Connect every server. A server that fails is reported, not fatal.

    Returns the tools by name, the live connections, and one error line per failure.
    """
    tools: dict[str, McpTool] = {}
    conns: list[McpConnection] = []
    errors: list[str] = []
    seen: set[str] = set()
    for cfg in servers:
        if cfg.name in seen:
            raise ConfigError(f"Two MCP servers are named '{cfg.name}'.", hint="Names must be unique.")
        seen.add(cfg.name)
        conn = McpConnection(cfg)
        try:
            conn.start()
            listed = conn.list_tools()
        except ToolError as exc:
            conn.close()
            errors.append(f"MCP server '{cfg.name}' unavailable: {exc}")
            continue
        conns.append(conn)
        for spec in listed:
            if cfg.include and spec["name"] not in cfg.include:
                continue
            tool = McpTool(conn, spec, tool_name(cfg.name, str(spec["name"]), set(tools)))
            tools[tool.name] = tool
    for conn in conns:
        atexit.register(conn.close)
    return tools, conns, errors


def load_mcp_file(path: str) -> list[McpServerConfig]:
    """Read servers from a ``{"mcpServers": {name: {...}}}`` file (the format other MCP clients use)."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ConfigError(f"Cannot read MCP file '{path}': {exc}") from exc
    entries = data.get("mcpServers", data.get("servers")) if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        raise ConfigError(f"'{path}' has no \"mcpServers\" object.")
    out: list[McpServerConfig] = []
    for name, entry in entries.items():
        if not isinstance(entry, dict):
            raise ConfigError(f"MCP server '{name}' in '{path}' must be an object.")
        fields = {k: entry[k] for k in ("command", "args", "env", "url", "headers", "timeout_s") if k in entry}
        if "include" in entry:
            fields["include"] = entry["include"]
        if "autoApprove" in entry:
            fields["auto_approve"] = entry["autoApprove"]
        elif "auto_approve" in entry:
            fields["auto_approve"] = entry["auto_approve"]
        try:
            out.append(McpServerConfig(name=re.sub(r"[^A-Za-z0-9_-]", "_", name)[:24], **fields))
        except ValueError as exc:
            raise ConfigError(f"MCP server '{name}' in '{path}' is invalid: {exc}") from exc
    return out
