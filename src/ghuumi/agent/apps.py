"""`scaffold_app`: start an application of a chosen kind in the project folder.

The model can already write any file, so any kind of app is possible; this tool
gives it a known-good starting point so it begins from a project that runs, then
extends it with `write_file`, `edit_file` and `run_command`. Kinds:

==================  =========================================================
``static-web``      index.html, style.css, app.js (open it in a browser)
``python-cli``      a package with ``python -m <name>`` and a pytest test
``python-api``      a FastAPI service with a health route and a test
``node-cli``        a Node.js command with ``npm start`` and ``npm test``
``node-web``        a dependency-free Node.js HTTP server
``blank``           README.md only, for kinds not listed here
==================  =========================================================

Every file goes through the workspace's diff-and-approve path as one change, in
a new folder ``<name>/`` that must not exist yet, so nothing is overwritten.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from ghuumi.agent.tools import Action, Tool
from ghuumi.agent.workspace import Workspace
from ghuumi.core.errors import ToolError

_NAME = re.compile(r"[a-z][a-z0-9-]{0,39}")


def _py_pkg(name: str) -> str:
    return name.replace("-", "_")


def _static_web(n: str) -> dict[str, str]:
    return {
        "index.html": f'<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        f'<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>{n}</title>\n'
        f'<link rel="stylesheet" href="style.css">\n</head>\n<body>\n<main id="app"><h1>{n}</h1></main>\n'
        '<script src="app.js"></script>\n</body>\n</html>\n',
        "style.css": "body { font-family: system-ui, sans-serif; margin: 2rem; }\n",
        "app.js": "document.getElementById('app').append(' Ready.');\n",
        "README.md": f"# {n}\n\nOpen `index.html` in a browser.\n",
    }


def _python_cli(n: str) -> dict[str, str]:
    p = _py_pkg(n)
    return {
        "pyproject.toml": f'[project]\nname = "{n}"\nversion = "0.1.0"\nrequires-python = ">=3.10"\n\n'
        '[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.build_meta"\n\n'
        f'[tool.setuptools]\npackages = ["{p}"]\n',
        f"{p}/__init__.py": "\n",
        f"{p}/__main__.py": f'from {p}.core import greet\n\nprint(greet("world"))\n',
        f"{p}/core.py": 'def greet(who: str) -> str:\n    return f"Hello, {who}!"\n',
        "tests/test_core.py": f'from {p}.core import greet\n\n\ndef test_greet():\n    assert greet("a") == "Hello, a!"\n',
        "README.md": f"# {n}\n\nRun: `python -m {p}`. Test: `python -m pytest`.\n",
    }


def _python_api(n: str) -> dict[str, str]:
    p = _py_pkg(n)
    return {
        "pyproject.toml": f'[project]\nname = "{n}"\nversion = "0.1.0"\nrequires-python = ">=3.10"\n'
        'dependencies = ["fastapi", "uvicorn"]\n\n[build-system]\nrequires = ["setuptools>=68"]\n'
        f'build-backend = "setuptools.build_meta"\n\n[tool.setuptools]\npackages = ["{p}"]\n',
        f"{p}/__init__.py": "\n",
        f"{p}/app.py": 'from fastapi import FastAPI\n\napp = FastAPI()\n\n\n@app.get("/health")\n'
        'def health() -> dict:\n    return {"status": "ok"}\n',
        "tests/test_app.py": f'from fastapi.testclient import TestClient\n\nfrom {p}.app import app\n\n\n'
        'def test_health():\n    assert TestClient(app).get("/health").json() == {"status": "ok"}\n',
        "README.md": f"# {n}\n\nInstall: `python -m pip install -e .`. Run: `python -m uvicorn {p}.app:app`. "
        "Test: `python -m pytest` (needs httpx).\n",
    }


def _node_cli(n: str) -> dict[str, str]:
    return {
        "package.json": f'{{\n  "name": "{n}",\n  "version": "0.1.0",\n  "type": "module",\n'
        '  "scripts": { "start": "node index.js", "test": "node --test" }\n}\n',
        "index.js": "import { greet } from './greet.js';\n\nconsole.log(greet(process.argv[2] ?? 'world'));\n",
        "greet.js": "export const greet = (who) => `Hello, ${who}!`;\n",
        "greet.test.js": "import test from 'node:test';\nimport assert from 'node:assert/strict';\n"
        "import { greet } from './greet.js';\n\ntest('greet', () => assert.equal(greet('a'), 'Hello, a!'));\n",
        "README.md": f"# {n}\n\n`npm start`, `npm test`.\n",
    }


def _node_web(n: str) -> dict[str, str]:
    return {
        "package.json": f'{{\n  "name": "{n}",\n  "version": "0.1.0",\n  "type": "module",\n'
        '  "scripts": { "start": "node index.js", "test": "node --test" }\n}\n',
        "server.js": "import http from 'node:http';\n\nexport const server = http.createServer((req, res) => {\n"
        "  res.setHeader('content-type', 'application/json');\n"
        "  res.end(JSON.stringify({ status: 'ok', path: req.url }));\n});\n",
        "index.js": "import { server } from './server.js';\n\n"
        "server.listen(process.env.PORT ?? 3000, '127.0.0.1');\n",
        "server.test.js": "import test from 'node:test';\nimport assert from 'node:assert/strict';\n"
        "import { server } from './server.js';\n\ntest('responds', async () => {\n"
        "  await new Promise((r) => server.listen(0, '127.0.0.1', r));\n"
        "  const res = await fetch(`http://127.0.0.1:${server.address().port}/x`);\n"
        "  assert.equal((await res.json()).status, 'ok');\n  server.close();\n});\n",
        "README.md": f"# {n}\n\n`npm start` serves on http://127.0.0.1:3000.\n",
    }


def _blank(n: str) -> dict[str, str]:
    return {"README.md": f"# {n}\n"}


TEMPLATES = {
    "static-web": _static_web,
    "python-cli": _python_cli,
    "python-api": _python_api,
    "node-cli": _node_cli,
    "node-web": _node_web,
    "blank": _blank,
}


class ScaffoldAppTool(Tool):
    name = "scaffold_app"
    description = (
        "Start a new application in the project folder, in a new folder named after it. "
        f"kind is one of: {', '.join(TEMPLATES)}. Creates a small working project with a "
        "test where the kind supports one; build the real features afterwards with "
        "write_file and edit_file, and check them with run_command. Use kind 'blank' for "
        "anything else (desktop, mobile, games, ...) and write the files yourself. The user "
        "approves the whole set of files at once."
    )
    parameters = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(TEMPLATES)},
            "name": {"type": "string", "description": "Folder and project name: lowercase letters, digits, hyphens."},
        },
        "required": ["kind", "name"],
    }
    read_only = False
    needs_workspace = True

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    async def run(self, **kwargs: Any) -> str:
        raise ToolError("scaffold_app runs only through prepare() and the user's approval.")

    async def prepare(self, **kwargs: Any) -> Action:
        unknown = sorted(set(kwargs) - {"kind", "name"})
        if unknown:
            raise ToolError(f"scaffold_app takes kind and name, not {', '.join(unknown)}.")
        kind, name = kwargs.get("kind"), kwargs.get("name")
        if kind not in TEMPLATES:
            raise ToolError(f"kind must be one of: {', '.join(TEMPLATES)}.")
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise ToolError("name must be lowercase letters, digits and hyphens, starting with a letter.")
        return await asyncio.to_thread(self._plan, kind, name)

    def _plan(self, kind: str, name: str) -> Action:
        target, _ = self.workspace.resolve(name)
        if target.exists():
            raise ToolError(f"'{name}' already exists; choose another name or edit it with edit_file.")
        actions = [
            self.workspace.plan_write(f"{name}/{rel}", content)
            for rel, content in TEMPLATES[kind](name).items()
        ]
        summary = f"Scaffold a {kind} app in {name}/ ({len(actions)} files)\n\n" + "\n\n".join(
            a.summary for a in actions
        )

        async def apply() -> str:
            results = [await a.apply() for a in actions]
            return f"Scaffolded {kind} app '{name}':\n" + "\n".join(results)

        return Action(summary=summary, apply=apply)
