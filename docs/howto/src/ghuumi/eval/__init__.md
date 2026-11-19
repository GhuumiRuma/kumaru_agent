# `src\ghuumi\eval\__init__.py`

> Public re-export surface for Ghuumi's v0.1 evaluation harness (`ghuumi eval`). The v0.2 benchmarks (`ghuumi bench`) live in the subpackage `ghuumi.eval.benchmarks`, which this file does not re-export.

**Read this when:** you want to import evaluation primitives from `ghuumi.eval` instead of the deeper `ghuumi.eval.harness` module, or `from ghuumi.eval import …` can't find a benchmark name.

---

## What it does

This module imports `Case`, `EvalResult`, and `run_eval` from `ghuumi.eval.harness` and exposes them in `__all__`. It gives callers a stable package-level import path.

It contains no evaluation logic itself. All suite loading, scoring, backend calls, tables, and JSON output live in `harness.py`.

Since v0.2 the package has two layers:

| Import from | Command | What it measures | Page |
|---|---|---|---|
| `ghuumi.eval` (implemented in `ghuumi.eval.harness`) | `ghuumi eval` | Smoke and speed checks of the agent path: substring checks at temperature 0, plus time to first token and tokens/second. | [`harness.md`](harness.md) |
| `ghuumi.eval.benchmarks`, and `ghuumi.eval.benchmarks.runner` | `ghuumi bench list\|run\|report\|compare` | Accuracy on GSM8K, MMLU-Pro, IFEval, HumanEval, MultiPL-E (HumanEval in eight languages) and golden-format question sets, with 95% confidence intervals and paired significance tests. | [`benchmarks\__init__.md`](benchmarks/__init__.md) |

This file re-exports only the first layer. The subpackage has its own `__all__`: `BenchItem`, `Benchmark`, `Grade`, `select_items`. The run machinery is imported from `ghuumi.eval.benchmarks.runner`: `BenchRunner`, `BenchSettings`, `run_benchmarks`, `compare_runs`, `load_summaries`, `list_benchmarks` and the `render_*` functions.

## Why it exists

Package-level exports keep downstream code from depending on the internal file layout. If the harness implementation moves later, `from ghuumi.eval import run_eval` can remain stable.

Without this file, callers would either import from `ghuumi.eval.harness` directly or accidentally rely on non-public internals.

## Mental model

1. **This is an API shim.** It re-exports stable names.
2. **The implementation is in `harness.py`.** Debug behavior there.
3. **`__all__` is the contract.** Only listed names are intended package-level API.
4. **Two layers, no imports between them.** The harness and the benchmarks both build their backend from `GhuumiConfig` and decode through `ChatAgent`, and neither imports the other.
   - Importing `ghuumi.eval.benchmarks` runs this file first, because Python imports the parent package before a subpackage. That also loads `harness.py`, which is cheap.
   - It does not load `runner.py` or the `datasets` library.
   - The CLI imports both layers lazily, inside the command that needs them.

## Public API

| Name | Signature | What it's for |
|---|---|---|
| `Case` | `class Case(BaseModel)` | Re-export of one evaluation case: prompt plus required/forbidden substrings. |
| `EvalResult` | `class EvalResult(BaseModel)` | Re-export of aggregate evaluation results. |
| `run_eval` | `async run_eval(config: GhuumiConfig, *, suite: str = "smoke", output: str \| None = None) -> EvalResult` | Re-export of the async harness entry point. |
| `__all__` | `list[str]` | Declares package-level public names: `Case`, `EvalResult`, `run_eval`. |

Nothing from `ghuumi.eval.benchmarks` is available here. Its API is in [`benchmarks\__init__.md`](benchmarks/__init__.md#public-api) and [`benchmarks\runner.md`](benchmarks/runner.md#public-api).

## How to use it

Import from the package:

```python
import asyncio
from ghuumi.core.config import load_config
from ghuumi.eval import run_eval

cfg = load_config(None, overrides={"backend": {"name": "echo", "model": "echo"}})
result = asyncio.run(run_eval(cfg, suite="smoke", output="runs/eval-smoke.json"))
print(result.pass_rate)
```

Use the CLI instead when operating from a shell:

```powershell
ghuumi eval --suite smoke --output runs\eval-smoke.json
ghuumi eval --suite speed --output runs\eval-speed.json
```

Evaluate a trained adapter:

```powershell
ghuumi eval --backend transformers --model Qwen/Qwen2.5-7B-Instruct --adapter runs\lora-qwen\adapter --suite smoke --output runs\lora-qwen\eval-smoke.json
```

The v0.2 benchmarks are imported from their own modules:

```python
from ghuumi.eval.benchmarks import BenchItem, Benchmark, Grade, select_items     # the benchmark contract
from ghuumi.eval.benchmarks.runner import BenchSettings, compare_runs, run_benchmarks
```

```powershell
ghuumi bench run golden -c qwen36                        # the 27 hand-written golden questions
ghuumi bench compare runs\bench\<run-A> runs\bench\<run-B>
```

[`benchmarks\__init__.md`](benchmarks/__init__.md#from-python) has a runnable offline Python example.

## Tuning / operational notes

This module has no runtime tuning knobs. Operational choices come from `run_eval()`:

- `suite="smoke"` checks deterministic quality tripwires.
- `suite="speed"` measures latency and throughput cases.
- `suite="path\to\cases.jsonl"` loads custom `Case` rows.
- `output="...json"` writes the full `EvalResult` payload.

For training operations, run eval before and after adapter changes. A quick smoke suite is a regression tripwire for instruction following, arithmetic, format compliance, code generation, factual recall, and calibrated refusal.

Choose the layer by the question you are asking. The full comparison is in [`harness.md`](harness.md#ghuumi-eval-or-ghuumi-bench).

- **`ghuumi eval`**: a seconds-long tripwire, and the only one of the two layers that reports time to first token.
  - The built-in suites' per-case budgets are 8–512 tokens, and there is no thinking flag.
  - Run it with thinking off: use a thinking-off profile, or set `$env:GHUUMI_GENERATION__THINKING='false'`.
- **`ghuumi bench`**: accuracy with 95% confidence intervals, and A-vs-B comparisons.
  - Every answer gets 8,192 tokens, plus the thinking budget when thinking is on.
  - The report's **cut off** column (`n_truncated`) shows when that cap still binds.

## How to extend it

If `harness.py` adds a stable new public class, re-export it here and update `__all__`:

```python
from ghuumi.eval.harness import Case, EvalResult, JudgeResult, run_eval

__all__ = ["Case", "EvalResult", "JudgeResult", "run_eval"]
```

Do not re-export helper functions such as `load_suite()` unless you want to support them as public API.

Do not re-export the benchmark API here either. Every `import ghuumi.eval` would then also load `runner.py`, which adds over a hundred modules (Rich, asyncio, ssl, subprocess and more; 111 when measured on Windows). That cost would land on the `ghuumi eval` path and on every `import ghuumi.eval.benchmarks`. To add a benchmark, follow [`benchmarks\__init__.md`](benchmarks/__init__.md#add-a-benchmark-end-to-end).

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `ImportError: cannot import name ... from ghuumi.eval` | Name is not re-exported in `__all__` | Import from `ghuumi.eval.harness` or add a deliberate re-export. |
| `ImportError: cannot import name 'BenchRunner' from 'ghuumi.eval'` (or `run_benchmarks`, `BenchSettings`, …) | The benchmark API lives in the subpackage and is not re-exported. | `from ghuumi.eval.benchmarks.runner import BenchRunner`. The contract types come from `ghuumi.eval.benchmarks`. |
| Eval behavior differs from expectation | Looking at this shim instead of implementation | Read `src\ghuumi\eval\harness.py`. |
| Circular import after adding exports | New exported object imports `ghuumi.eval` during module import | Keep implementation imports one-way from `__init__` to `harness`. |
| CLI eval fails | Backend/config issue, not this module | Check `ghuumi eval` args and `harness.py` failure modes. |

## Related files

- `src\ghuumi\eval\harness.py` — implementation of all re-exported names ([`harness.md`](harness.md)).
- `src\ghuumi\eval\benchmarks\` — the v0.2 benchmarks package and `ghuumi bench` ([`benchmarks\__init__.md`](benchmarks/__init__.md)).
- `src\ghuumi\cli.py` — `ghuumi eval` imports `run_eval` from `ghuumi.eval.harness`, and `ghuumi bench` imports from `ghuumi.eval.benchmarks.runner`.
- `src\ghuumi\core\config.py` — provides the `GhuumiConfig` passed to `run_eval`.
- `src\ghuumi\agent\chat.py` — harness uses `ChatAgent` to complete prompts.
- [`docs\BENCHMARKS.md`](../../../../BENCHMARKS.md) — benchmark methodology and results.
