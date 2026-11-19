# `src\ghuumi\core\logging.py`

> One logging setup for the `ghuumi` logger tree: coloured console lines for people, one-JSON-object-per-line for log aggregators, and UTF-8-safe streams on Windows.

**Read this when:** you need DEBUG or JSON logs, a log setting "does nothing", lines print twice or with garbled glyphs, or you add structured fields to log calls.

---

## What it does
- `get_logger(name)` returns a logger under the `ghuumi.` namespace. On the first call, it configures logging from the environment.
- `setup_logging(level, fmt=...)` (re)configures the `ghuumi` tree:
  - one stderr handler, with either `ConsoleFormatter` or `JsonFormatter`;
  - propagation disabled;
  - chatty third-party loggers quietened.
- `ensure_utf8_streams()` reconfigures stdout and stderr to UTF-8 with `errors="replace"`.

## Why it exists
**JSON from day one.** Ghuumi runs both as an interactive CLI and as a service. Once you run more than one replica, grepping human-readable logs stops working, and a log pipeline (Loki, CloudWatch, Datadog) needs parseable lines. Having the JSON switch from day one means that move costs one setting, not a refactor.

**UTF-8 streams.** Windows consoles default to a legacy code page (cp1252 or cp437). Ghuumi's output is full of em-dashes, arrows and box-drawing characters, which would otherwise render as noise. Worse, they could crash with `UnicodeEncodeError` on a redirected stream. `errors="replace"` makes a wrong glyph the worst case.

## Mental model
- **Ghuumi owns the `ghuumi` tree.**
  - `get_logger("server")` and `get_logger("ghuumi.server")` both return `ghuumi.server`.
  - Nothing is attached to the root logger, and `propagate = False`. Without that, uvicorn's root configuration would print every line twice.
- **Configuration is lazy and environment-driven by default.**
  - Most modules (`core\registry.py`, `agent\chat.py`, the backends, the server and train modules, …) run `logger = get_logger(__name__)` at import time.
  - The first such import calls `setup_logging($GHUUMI_LOG_LEVEL or "INFO")`. Importing `ghuumi.cli` or `ghuumi.core.config` alone configures nothing.
  - The format comes from `$GHUUMI_LOG_FORMAT`, or `console` when it is unset.
- **`ghuumi serve` reconfigures from the config file.**
  - `create_app()` (`src\ghuumi\server\app.py:180`) calls `setup_logging(config.telemetry.log_level, fmt=config.telemetry.log_format)`. That is the only explicit call in the code base.
  - Two consequences, both verified:

    | Command | Level and format come from | Ignored |
    |---|---|---|
    | `ghuumi serve`, or anything that calls `create_app()` | `telemetry.log_level` and `telemetry.log_format`: YAML, `GHUUMI_TELEMETRY__LOG_LEVEL`, `GHUUMI_TELEMETRY__LOG_FORMAT`, `serve --log-level` | `GHUUMI_LOG_LEVEL`, `GHUUMI_LOG_FORMAT`. `telemetry.log_format` is never empty, so it always beats the env var. |
    | `chat`, `eval`, `bench`, `info`, `config`, `train` | `GHUUMI_LOG_LEVEL`, `GHUUMI_LOG_FORMAT` | `telemetry.*`. For example, `configs\test.yaml` sets `log_level: DEBUG`, but `ghuumi chat -c test` logs at INFO. |

    The bare `GHUUMI_LOG_*` variables are not config paths: `load_config` skips them, and `GHUUMI_LOG_LEVEL` does not change `telemetry.log_level`.
- **Every `setup_logging()` call fully replaces the handler.** It clears the handlers, adds one, and sets the level. The `_CONFIGURED` flag only stops `get_logger()` from configuring a second time.
- **Level names are case-insensitive here, and an unknown name such as `"verbose"` silently becomes INFO.** That leniency reaches only `$env:GHUUMI_LOG_LEVEL` and direct calls. `telemetry.log_level` is validated when the config loads: `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`, in any case and with surrounding spaces, plus the aliases `WARN` and `FATAL`. So `ghuumi serve` never hands uvicorn a level name it doesn't know.
- **Output details:**
  - Everything goes to **stderr**, so stdout stays clean for answers and tables.
  - Console lines are coloured only when stderr is a TTY. They look like `HH:MM:SS LEVEL    name   message`, using local time and the logger name without `ghuumi.`.
  - JSON `ts` is **UTC** (`%Y-%m-%dT%H:%M:%S`), with no zone suffix and second resolution.
- **On every setup, `httpx`, `httpcore`, `urllib3` and `filelock` are set to WARNING.** At INFO they log each HTTP request, which drowns the useful lines.
- **uvicorn's own loggers are not Ghuumi's.**
  - `app.run()` passes `log_level=telemetry.log_level.lower()` and `access_log=False`. Ghuumi logs its own request lines instead.
  - uvicorn keeps its default `LOGGING_CONFIG`, so its startup and shutdown lines are never JSON.

## Public API

| Name | Signature | What it's for |
|---|---|---|
| `ensure_utf8_streams` | `ensure_utf8_streams() -> None` | Reconfigures stdout and stderr to UTF-8 with `errors="replace"`. It skips streams without `reconfigure` and suppresses `ValueError`/`OSError`. Called by `cli.main()` and by every `setup_logging()`. |
| `ConsoleFormatter` | `ConsoleFormatter(*, color: bool = True)` | Compact aligned single-line records: local time, the level padded to 8, the logger name without `ghuumi.` padded to 26, the message, and the traceback when there is one. |
| `ConsoleFormatter.format` | `format(self, record: logging.LogRecord) -> str` | Renders one record. Ignores `extra` fields. |
| `JsonFormatter` | `JsonFormatter()` | One JSON object per line. |
| `JsonFormatter.format` | `format(self, record: logging.LogRecord) -> str` | Writes keys `ts`, `level`, `logger`, `msg`, plus every non-standard record attribute (your `extra=` fields), plus `exc` when there is a traceback. Non-JSON values go through `str`. |
| `setup_logging` | `setup_logging(level: str = "INFO", *, fmt: str \| None = None) -> None` | (Re)configures the `ghuumi` tree. `fmt` is `"json"`, or anything else for console. It defaults to `$GHUUMI_LOG_FORMAT`, else `"console"`. |
| `get_logger` | `get_logger(name: str) -> logging.Logger` | Returns `ghuumi.<name>`, or `name` unchanged if it already starts with `ghuumi`. Configures from `GHUUMI_LOG_LEVEL` and `GHUUMI_LOG_FORMAT` on first use. |

`_CONFIGURED`, `_COLORS` and `_RESET` are private.

## How to use it

Get a module logger, and attach fields that become top-level JSON keys:

```python
from ghuumi.core.logging import get_logger

logger = get_logger(__name__)
logger.info("request completed", extra={"tokens": 128, "latency_ms": 42.5})
```

With JSON on, that line is (captured):

```
{"ts": "2026-10-10T19:29:08", "level": "INFO", "logger": "ghuumi.server", "msg": "request completed", "tokens": 128, "latency_ms": 42.5}
```

The console format prints only `12:29:08 INFO     server                     request completed`.

Turn on DEBUG or JSON for the CLI commands:

```powershell
$env:GHUUMI_LOG_LEVEL = 'DEBUG'
$env:GHUUMI_LOG_FORMAT = 'json'
ghuumi chat -c test -q "hi"
Remove-Item Env:GHUUMI_LOG_LEVEL, Env:GHUUMI_LOG_FORMAT
```

…and for the server, where the `telemetry` section rules:

```powershell
ghuumi serve -c default --log-level DEBUG
$env:GHUUMI_TELEMETRY__LOG_FORMAT = 'json'; ghuumi serve -c default
```

Or set it in YAML, as `configs\vllm.yaml` does:

```yaml
telemetry:
  log_level: INFO
  log_format: json
```

Configure explicitly in your own entry point or test:

```python
from ghuumi.core.logging import setup_logging

setup_logging("DEBUG", fmt="json")
```

## How to extend it
- **New fields:** pass them per call with `extra={...}`. Do not add a custom formatter for this.
  - `extra` keys must not collide with `LogRecord` attributes: `message`, `msg`, `args`, `name`, `levelname` and so on. Python raises `KeyError: "Attempt to overwrite 'message' in LogRecord"`.
  - Keys equal to `ts`, `level` or `logger` are accepted by `logging` but then silently dropped by the formatter's `setdefault`.
- **Changing the JSON shape**, e.g. adding milliseconds: edit `JsonFormatter.format()`. Aggregators key on the existing four fields, so add keys rather than renaming them.

  ```python
  payload: dict[str, Any] = {
      "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
      "ts_ms": int(record.created * 1000),
      "level": record.levelname,
      "logger": record.name,
      "msg": record.getMessage(),
  }
  ```

- **New entry points** (a worker, a script): call `setup_logging(cfg.telemetry.log_level, fmt=cfg.telemetry.log_format)` once after `load_config()`, as `create_app()` does, if the config file should rule.
- **Do not** add per-module handlers or call `logging.basicConfig()`. Both bypass the propagation guard and duplicate lines.

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `$env:GHUUMI_LOG_LEVEL='DEBUG'` has no effect on `ghuumi serve` | `create_app()` reconfigures from `telemetry.*`. | `ghuumi serve --log-level DEBUG`, or `$env:GHUUMI_TELEMETRY__LOG_LEVEL='DEBUG'`. |
| `telemetry.log_level` or `log_format` in YAML is ignored by `chat`, `eval` or `bench` | Only `serve` reads `telemetry.*`. The other commands configure from `GHUUMI_LOG_*` on first import. | Use `$env:GHUUMI_LOG_LEVEL` and `$env:GHUUMI_LOG_FORMAT` for those commands. |
| `error: Invalid configuration: ... telemetry.log_level ...` from `ghuumi serve --log-level verbose` | `telemetry.log_level` takes only the five standard names (plus `WARN` and `FATAL`), checked when the config loads. Earlier builds passed the name through, and uvicorn died with `KeyError: 'verbose'`. | Use `critical`, `error`, `warning`, `info` or `debug` (any case). |
| `$env:GHUUMI_LOG_LEVEL = 'verbose'` logs at INFO without a warning | The bare variable is not a config field, so nothing validates it; `setup_logging` maps unknown names to INFO. | Use one of the five names. |
| A few non-JSON lines in JSON mode | They come from uvicorn's own loggers (startup, shutdown, errors), which keep uvicorn's formatter. | Filter them in the pipeline, or pass a `log_config` to `uvicorn.run` in `app.run()`. |
| Each line appears twice | Someone called `logging.basicConfig()` or added a handler to the root logger or a `ghuumi.*` logger. | Remove it; only `setup_logging()` should attach handlers. |
| `KeyError: "Attempt to overwrite 'message' in LogRecord"` | An `extra=` key collides with a `LogRecord` attribute. | Rename the key. |
| `extra` fields missing from output | `ConsoleFormatter` prints only the message. | Use JSON format when fields must be queryable. |
| `httpx`/`urllib3` request spam | Something lowered those loggers after `setup_logging()`. | Call `setup_logging()` again, or set them back to WARNING. |
| `UnicodeEncodeError: 'charmap' codec can't encode character …`, or `?` in place of box-drawing characters, on Windows | Output went to a legacy-code-page stream before `ensure_utf8_streams()` ran, e.g. in your own script that prints before configuring logging. | Call `ensure_utf8_streams()` first. The `ghuumi` CLI does this in `main()`. |
| JSON timestamps look hours off | `ts` is UTC with no zone suffix; console lines use local time. | Treat `ts` as UTC in the aggregator. |

## Related files
- `src\ghuumi\server\app.py` — `create_app()` makes the only explicit `setup_logging()` call. `run()` passes the level to uvicorn and disables its access log.
- `src\ghuumi\core\config.py` — `TelemetryConfig.log_level` (`"INFO"`; one of five names, normalized and validated at load) and `log_format` (`"console"`). Bare `GHUUMI_LOG_*` variables are skipped by the env loader.
- `src\ghuumi\cli.py` — `main()` calls `ensure_utf8_streams()`. `serve --log-level` maps to `telemetry.log_level`.
- `src\ghuumi\agent\chat.py`, `src\ghuumi\backends\base.py` — the main producers of timing lines (`streamed: …`, `backend '…' ready`).
- `configs\default.yaml`, `configs\vllm.yaml` — the `telemetry` section. `vllm.yaml` switches to JSON.
