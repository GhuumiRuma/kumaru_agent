"""Protect MCP tools, app scaffolding and developer mode: an MCP call needs a yes, a broken
server cannot take the chat down, scaffolding never overwrites, and the dev prompt is present.
"""

import json
import sys
import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from ghuumi.agent.mcp import load_mcp_file, tool_name
from ghuumi.agent.prompt import DEVELOPER_MODE_RULES, build_system_prompt
from ghuumi.agent.tools import ApprovalRequest, ToolBelt
from ghuumi.core.config import AgentConfig, McpServerConfig, WorkspaceConfig
from ghuumi.core.errors import ConfigError

SERVER = textwrap.dedent(
    """
    import json, sys
    for line in sys.stdin:
        msg = json.loads(line)
        if "id" not in msg:
            continue
        method = msg["method"]
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {}, "serverInfo": {"name": "t"}}
        elif method == "tools/list":
            result = {"tools": [
                {"name": "add", "description": "Add", "inputSchema": {"type": "object",
                 "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}}},
                {"name": "other", "inputSchema": {"type": "object"}},
            ]}
        elif method == "tools/call":
            a = msg["params"]["arguments"]
            result = {"content": [{"type": "text", "text": str(a["a"] + a["b"])}]}
        else:
            result = {}
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
    """
)


@pytest.fixture
def server(tmp_path: Path) -> McpServerConfig:
    script = tmp_path / "server.py"
    script.write_text(SERVER, encoding="utf-8")
    return McpServerConfig(name="calc", command=sys.executable, args=[str(script)], timeout_s=20)


async def _yes(_: ApprovalRequest) -> bool:
    return True


def test_mcp_tools_are_listed_and_prefixed(server: McpServerConfig) -> None:
    belt = ToolBelt([], approver=_yes, mcp_servers=[server])
    assert set(belt.tools) == {"mcp_calc_add", "mcp_calc_other"}
    assert belt.mcp_errors == []


async def test_mcp_call_runs_only_after_approval(server: McpServerConfig) -> None:
    asked: list[ApprovalRequest] = []

    async def approver(request: ApprovalRequest) -> bool:
        asked.append(request)
        return len(asked) > 1

    belt = ToolBelt([], approver=approver, mcp_servers=[server])
    assert "DENIED" in await belt.invoke("mcp_calc_add", {"a": 1, "b": 2})
    assert await belt.invoke("mcp_calc_add", {"a": 1, "b": 2}) == "3"
    assert "tool: add" in asked[0].summary


async def test_auto_approve_and_include(server: McpServerConfig) -> None:
    cfg = server.model_copy(update={"include": ["add"], "auto_approve": ["add"]})

    async def never(_: ApprovalRequest) -> bool:
        raise AssertionError("auto-approved tool must not ask")

    belt = ToolBelt([], approver=never, mcp_servers=[cfg])
    assert list(belt.tools) == ["mcp_calc_add"]
    assert await belt.invoke("mcp_calc_add", {"a": 2, "b": 2}) == "4"


def test_mcp_tools_withheld_without_approver(server: McpServerConfig) -> None:
    belt = ToolBelt([], mcp_servers=[server])
    assert not belt
    assert belt.withheld == ["mcp:calc"]


def test_broken_server_is_reported_not_fatal(server: McpServerConfig) -> None:
    bad = McpServerConfig(name="bad", command="definitely-not-a-program-xyz")
    belt = ToolBelt([], approver=_yes, mcp_servers=[bad, server])
    assert len(belt.mcp_errors) == 1 and "bad" in belt.mcp_errors[0]
    assert "mcp_calc_add" in belt.tools


def test_server_needs_exactly_one_transport() -> None:
    with pytest.raises(ValidationError):
        McpServerConfig(name="x")
    with pytest.raises(ValidationError):
        McpServerConfig(name="x", command="a", url="http://localhost:1")


def test_tool_names_are_safe_and_unique() -> None:
    first = tool_name("s", "do.it/now", set())
    assert first == "mcp_s_do_it_now"
    assert tool_name("s", "do.it/now", {first}) == first + "_2"
    assert len(tool_name("s", "x" * 200, set())) <= 64


def test_load_mcp_file(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps({"mcpServers": {"my.srv": {"command": "npx", "args": ["-y", "pkg"], "autoApprove": ["ls"]}}}),
        encoding="utf-8",
    )
    (server,) = load_mcp_file(str(path))
    assert server.name == "my_srv" and server.auto_approve == ["ls"]
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_mcp_file(str(path))


def _belt(tmp_path: Path) -> ToolBelt:
    return ToolBelt(
        ["scaffold_app"], workspace=WorkspaceConfig(root=str(tmp_path)), approver=_yes
    )


async def test_scaffold_creates_project_and_never_overwrites(tmp_path: Path) -> None:
    belt = _belt(tmp_path)
    result = await belt.invoke("scaffold_app", {"kind": "python-cli", "name": "demo-app"})
    assert result.startswith("Scaffolded")
    assert (tmp_path / "demo-app" / "demo_app" / "core.py").is_file()
    assert (tmp_path / "demo-app" / "tests" / "test_core.py").is_file()
    again = await belt.invoke("scaffold_app", {"kind": "static-web", "name": "demo-app"})
    assert again.startswith("ERROR") and "already exists" in again


@pytest.mark.parametrize("kind", ["static-web", "python-api", "node-cli", "node-web", "blank"])
async def test_every_scaffold_kind_writes_files(tmp_path: Path, kind: str) -> None:
    result = await _belt(tmp_path).invoke("scaffold_app", {"kind": kind, "name": "x1"})
    assert result.startswith("Scaffolded"), result
    assert (tmp_path / "x1" / "README.md").is_file()


async def test_scaffold_rejects_bad_names(tmp_path: Path) -> None:
    belt = _belt(tmp_path)
    for name in ("../evil", "Bad Name", ""):
        out = await belt.invoke("scaffold_app", {"kind": "blank", "name": name})
        assert out.startswith("ERROR")


def test_developer_mode_prompt() -> None:
    assert DEVELOPER_MODE_RULES in build_system_prompt(AgentConfig(developer_mode=True))
    assert DEVELOPER_MODE_RULES not in build_system_prompt(AgentConfig())
