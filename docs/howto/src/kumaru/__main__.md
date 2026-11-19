# `src\kumaru\__main__.py`

> `python -m kumaru` runs the `kumaru` command: `python -m kumaru serve -c qwen36` is `kumaru serve -c qwen36`.

**Read this when:** you start Kumaru without the `kumaru` launcher, for example from `pythonw.exe`.

---

## What it does

It calls `kumaru.cli.main()`, the same entry point as the `kumaru` command, with its error handling and exit codes ([`cli.md`](cli.md)).

## Why it exists

The desktop shortcuts run `pythonw.exe -m kumaru app -c qwen36`. `pythonw.exe` is the Python that opens no console window, and `kumaru.exe` is a console program, so it would open one. With `-m kumaru`, the window-less Python runs the same command ([`desktop.md`](desktop.md)).

## Related files

- [`cli.md`](cli.md): every command.
- [`desktop.md`](desktop.md): what `kumaru app` does without a console.
