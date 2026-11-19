# `src\ghuumi\agent\apps.py`

> `scaffold_app`: start an application of a chosen kind in the project folder.

**Read this when:** you want the agent to begin a new app, or add a template.

---

## What it does
Creates `<name>/` with a small working project from one of: `static-web`, `python-cli`, `python-api`, `node-cli`, `node-web`, `blank`. Most include a test. The agent then builds the real app with `write_file`, `edit_file` and `run_command` (`python -m pytest`, `npm test`). Use `blank` for anything else (desktop, games, mobile) and let the agent write the files.

## Why it exists
A model that starts from a project that already runs and tests makes fewer structural mistakes than one inventing layout from nothing.

## Mental model
- It is a changing tool: all files are planned through `Workspace.plan_write`, shown as diffs in one summary, and written only after you say yes.
- The folder must not exist, so nothing is overwritten. The name is lowercase letters, digits and hyphens, so it cannot escape the project folder.
- Registered lazily as `scaffold_app`; part of `WORKSPACE_TOOLS`, so `--workspace` and `--dev` enable it.

## Extending
Write a function returning `{relative_path: text}` and add it to `TEMPLATES`. Update `tests\test_mcp_apps.py` (its kinds list).

## How it fails
`ERROR: 'x' already exists` (pick another name), or a bad `kind`/`name` (the message lists the valid ones). Commands such as `npm install` need the program on the allowed list (`agent.workspace.allowed_commands`).
