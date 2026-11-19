# `src\ghuumi\agent\__init__.py`

> Package entry point for Ghuumi's agent layer.

**Read this when:** you need to import the agent package, understand what belongs in `ghuumi.agent`, or keep CLI/server code decoupled from agent internals.

---

## What it does
This file exposes `ChatAgent` as the public symbol of `ghuumi.agent`. It also documents the boundary between the agent layer and the server layer. The package groups prompt assembly, memory trimming, the tools and the loop that runs them with your approval, and turn orchestration.

## Why it exists
Without this package boundary, callers would import implementation modules directly and couple themselves to file layout. The server should know that a `ChatAgent` exists, not how prompt, memory, and tools are internally split. That keeps the same agent usable from FastAPI, tests, a CLI command, or a future batch evaluator.

## Mental model
- `ghuumi.agent` is the conversation layer, not the HTTP layer.
- `ChatAgent` is the only exported package-level API.
- Submodules stay replaceable because callers import from the package boundary.

## Public API

| Name | Signature | What it's for |
|---|---|---|
| `ChatAgent` | `class ChatAgent(backend: Backend, config: GhuumiConfig, *, approver: Approver \| None = None)` | Orchestrates one chat turn: `prepare` builds the system prompt and fits history to the context budget, `resolve_params` merges generation settings (since v0.2 including the `thinking_mode` preset), and `complete`/`stream` call the backend and run the tool calls it asks for. A tool that would change something runs only after `approver` says yes; without one, those tools are not offered. `describe` reports configuration. Re-exported from `ghuumi.agent.chat`; see [chat.md](chat.md). |
| `__all__` | `list[str] = ["ChatAgent"]` | Defines the intentional public import surface for `from ghuumi.agent import *`. |

## How to use it

### Import the agent from the package boundary

```python
import asyncio

from ghuumi.agent import ChatAgent
from ghuumi.core.config import load_config
from ghuumi.core.registry import BACKENDS
from ghuumi.core.types import Message


async def main() -> None:
    config = load_config("test")
    backend = BACKENDS.create(config.backend.name, config.backend)
    await backend.load()

    agent = ChatAgent(backend, config)
    result = await agent.complete([Message.user("Say hello in five words.")])
    print(result.message.content)


asyncio.run(main())
```

### Keep server code independent of submodules

```python
from ghuumi.agent import ChatAgent

# Prefer this over importing ghuumi.agent.chat.ChatAgent in application code.
def make_agent(backend, config):
    return ChatAgent(backend, config)
```

### Check what the package exports

```python
import ghuumi.agent as agent_pkg

assert agent_pkg.__all__ == ["ChatAgent"]
```

## How to extend it
Add new agent-layer capabilities in submodules, then export only stable entry points here.

Worked example: if you add `EvaluatorAgent` in `src\ghuumi\agent\eval.py`, re-export it only after it is stable.

```python
# src\ghuumi\agent\__init__.py
from ghuumi.agent.chat import ChatAgent
from ghuumi.agent.eval import EvaluatorAgent

__all__ = ["ChatAgent", "EvaluatorAgent"]
```

Do not export helper functions that are only used inside `ChatAgent`; import those from their modules in tests instead.

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `ImportError: cannot import name 'ChatAgent' from 'ghuumi.agent'` | Package not installed from this checkout or stale environment. | Reinstall the project in editable mode from the repo root. |
| Circular import while starting the server | New package export imports server code back into `ghuumi.agent`. | Keep agent code independent of `ghuumi.server`; move shared contracts to `ghuumi.core`. |
| CLI and server behave differently | One caller imported and modified lower-level modules directly. | Import `ChatAgent` from `ghuumi.agent` and pass explicit `GhuumiConfig`. |
| Tools are never called | Caller bypassed `ChatAgent` and called a backend directly, or built it without an `approver`, so tools that change things were withheld (a warning is logged). | Use `ChatAgent.complete` or `stream` for chat turns, and pass an `approver` where a person can answer (`chat.md`). |

## Related files

- `src\ghuumi\agent\chat.py` — defines the re-exported `ChatAgent`.
- `src\ghuumi\agent\prompt.py` — builds the system message used by the agent.
- `src\ghuumi\agent\memory.py` — enforces the context-window budget.
- `src\ghuumi\agent\tools.py` — resolves configured tool names, and asks the approver before a tool changes anything.
- `src\ghuumi\agent\workspace.py` and `commands.py` — the project-folder tools: files, and allowed programs.
- `src\ghuumi\server\app.py` — imports `ChatAgent` to serve HTTP requests.

