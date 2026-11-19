# `src\ghuumi\server\education.py`

> Reads the folders `ghuumi bench run` leaves and reports, per benchmark and setup, the newest share of questions answered right, overall and per area, each with a 95% interval: the data behind the Education page.

**Read this when:** a bar on the Education page looks wrong or is missing, or you point the page at another runs folder.

---

## What it does

- **`read_education(runs_dirs)`** looks in each folder of `education.runs_dirs` (relative ones start at the Ghuumi folder) for benchmark folders up to two levels deep, reads each, and keeps the **newest measurement per (benchmark, label, model)**. Folders that do not exist are listed in `missing`; unreadable benchmark folders in `skipped`, with the reason.
- **`read_measurement(bench_dir)`**:
  - a finished benchmark (`summary.json` says `complete`) is read from `summary.json`;
  - one still running, or stopped part-way, from `items.jsonl`, because the runner rewrites `summary.json` only when a benchmark starts and stops. Only the run's own `item_ids` count, the last record per id wins, and a torn last line is skipped. So a measurement shows **while it is being made**.
  - `references` counts the knowledge packs and indexes in `run.json` when the run used `--use-agent-prompt`: a run with references is told apart from one without.
- **`read_records(path)`** caches each parsed `items.jsonl` by size and modification time (256 files at most), so polling a long run costs milliseconds.
- **`wilson_interval`** repeats `eval/benchmarks/stats.py`'s, because the server never imports benchmark code; a test keeps the two identical.
- **`family_of`** puts a suite's members in one section (`multipl_e_py` -> `multipl_e`).

## Why it exists

"How educated is the model?" needs a number per area that is honest about its uncertainty. The bench runner already saves every graded answer; this module only reads them, so the page costs no model tokens.

## Public API

| Name | What it's for |
|---|---|
| `read_education(runs_dirs) -> Education` | Everything the page shows. |
| `read_measurement(bench_dir) -> Measurement` | One benchmark folder. |
| `resolve_dirs(runs_dirs) -> list[Path]` | Configured folders as absolute paths. Also used by `ghuumi curriculum run` for where a phase's run goes. |
| `benchmark_dirs(root)`, `read_records(path)`, `family_of(name)`, `wilson_interval(k, n)` | The pieces above. |
| `Education`, `Measurement`, `Area` | The response models; `Area` is one subject, subfield or language with `answered`, `correct`, `accuracy` and `ci95`. |

## Failure modes

- **A bar is missing:** its run folder is not under any `education.runs_dirs` entry (see `missing` in the response), or it is more than two levels deep.
- **An older result shows:** two runs share benchmark, label and model; the newest wins. Give an experiment its own `--label`.

## Related files

- [`routes_education.md`](routes_education.md): the endpoint.
- [`../../../ui/education.js.md`](../../../ui/education.js.md): the page.
- [`../eval/benchmarks/runner.md`](../eval/benchmarks/runner.md): what a run folder holds.
- [`../core/config.md`](../core/config.md): `education.runs_dirs`.
