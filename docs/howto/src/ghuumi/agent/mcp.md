# `src\ghuumi\agent\mcp.py`

> MCP client: the tools of any Model Context Protocol server, offered to the agent behind your approval.

**Read this when:** you connect a server, add auto-approved tools, or debug a server that did not appear.

---

## What it does
Connects each server in `agent.mcp_servers` (or `ghuumi chat --mcp-file FILE`), lists its tools and wraps each as a `Tool` named `mcp_<server>_<tool>`. Two transports, no extra dependency: a local program over stdio (`command`, `args`, `env`) and a remote streamable-HTTP endpoint (`url`, `headers`; JSON or event-stream replies).

## Why it exists
MCP is the common way to plug in files, databases, browsers, SaaS APIs and more. One client gives the agent all of them.

## Mental model
- `ToolBelt` calls `connect_servers()` when there is an approver. A server that fails to start or list is reported in `ToolBelt.mcp_errors` (printed by `ghuumi chat`) and skipped; the chat still starts.
- **Every call asks you first.** The summary shows the server, the exact tool and its arguments. Only tools named in `auto_approve` run unasked; a server's own "read-only" hints are ignored because the server is untrusted.
- Results are untrusted text, escaped and size-capped like any tool result.
- Without an approver (HTTP server, web UI, eval) MCP tools are withheld, as `mcp:<server>` in `ToolBelt.withheld`.
- `include` limits which of a server's tools are offered; many tools cost context.

## Config
```yaml
agent:
  mcp_servers:
    - name: files
      command: npx
      args: ["-y", "@modelcontextprotocol/server-filesystem", "C:/projects"]
      auto_approve: [read_file]
    - name: docs
      url: https://example.com/mcp
      headers: {Authorization: "Bearer ..."}
```
`--mcp-file` takes the common JSON form: `{"mcpServers": {"files": {"command": "npx", "args": [...], "autoApprove": [...]}}}`.

## How it fails
| Symptom | Cause |
|---|---|
| `MCP server 'x' unavailable: cannot start ...` | The command is not installed or not on `PATH`. |
| `did not answer in Ns` | Server stuck; raise `timeout_s`. |
| Tools missing | `include` excludes them, or the backend cannot call tools (use `qwen36`). |
| Secrets | `env` and `headers` are sent to the server you chose; the child inherits your environment. Only connect servers you trust. |

## Extending
Add a transport by implementing `request`/`notify` for it in `McpConnection`.
