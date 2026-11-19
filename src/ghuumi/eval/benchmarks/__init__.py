"""Public benchmarks with honest statistics: `ghuumi bench`.

`ghuumi eval` answers "is this setup working, and how fast?". This package
answers "how *good* is the model, and did a change really help?" — on public
datasets, with every answer kept for audit and every comparison tested for
significance.

Modules, in reading order:

* `base`      — the `Benchmark` contract and the deterministic question sampler.
* `extract`   — pulling the final answer out of free-form model text.
* `stats`     — confidence intervals and paired significance tests.
* `gsm8k`, `mmlu_pro`, `ifeval` (+ `ifeval_checks`), `humaneval`,
  `multipl_e` (+ `codeparse`), `golden`
              — one module per benchmark.
* `codeexec`  — runs the code benchmarks' model-written programs.
* `runner`    — runs benchmarks, resumes interrupted runs, compares results.

Benchmarks are registered lazily in `ghuumi.core.registry.BENCHMARKS`, so
importing this package stays cheap; the dataset library loads only when a
benchmark actually runs.
"""

from ghuumi.eval.benchmarks.base import BenchItem, Benchmark, Grade, select_items

__all__ = ["BenchItem", "Benchmark", "Grade", "select_items"]
