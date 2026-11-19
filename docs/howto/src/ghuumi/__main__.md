# `src\ghuumi\__main__.py`

> `python -m ghuumi` runs the `ghuumi` command: `python -m ghuumi serve -c qwen36` is `ghuumi serve -c qwen36`.

**Read this when:** you start Ghuumi without the `ghuumi` launcher, for example from `pythonw.exe`.

---

## What it does

It calls `ghuumi.cli.main()`, the same entry point as the `ghuumi` command, with its error handling and exit codes ([`cli.md`](cli.md)).

## Why it exists

The desktop shortcuts run `pythonw.exe -m ghuumi app -c qwen36`. `pythonw.exe` is the Python that opens no console window, and `ghuumi.exe` is a console program, so it would open one. With `-m ghuumi`, the window-less Python runs the same command ([`desktop.md`](desktop.md)).

## Related files

- [`cli.md`](cli.md): every command.
- [`desktop.md`](desktop.md): what `ghuumi app` does without a console.
