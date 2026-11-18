"""How educated the model is, per area: `kumaru bench` results for the web UI.

Every `kumaru bench run` leaves a folder: ``run.json``, and per benchmark a
``summary.json`` and an ``items.jsonl`` holding one graded answer per line
(`kumaru.eval.benchmarks.runner`). This module reads those files and reports,
for each benchmark and setup (run label and model), the newest measurement:
the share of questions answered correctly, overall and per area (a subject,
subfield or language), each with a Wilson 95% interval.

A finished benchmark is read from its ``summary.json``. One still running, or
stopped part-way, is read from ``items.jsonl`` instead, because the runner
rewrites ``summary.json`` only when a benchmark starts and stops; so a
measurement shows while it is being made.

The server never imports benchmark code (docs/ARCHITECTURE.md), so this module
parses the files itself and repeats the Wilson interval of
``eval/benchmarks/stats.py``; a test keeps the two identical.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from pydantic import BaseModel, Field

from kumaru.core.config import REPO_ROOT
from kumaru.core.logging import get_logger
from kumaru.core.registry import BENCHMARK_SUITES

logger = get_logger(__name__)

Z95 = 1.959963984540054
# Parsed items.jsonl files, keyed by path and checked against size and mtime.
_CACHE_LIMIT = 256
_cache: dict[Path, tuple[tuple[int, int], dict[str, dict[str, Any]]]] = {}
_cache_lock = Lock()


def wilson_interval(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (default 95%)."""
    if n <= 0:
        return 0.0, 1.0
    p = successes / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


class Area(BaseModel):
    """One area of a benchmark: a subject, a subfield or a language."""

    name: str
    answered: int
    correct: int
    accuracy: float
    ci95: tuple[float, float]


class Measurement(BaseModel):
    """The newest result of one benchmark for one setup."""

    benchmark: str
    # The page section: a suite's name for its members (multipl_e_py -> multipl_e).
    family: str
    label: str
    model: str
    thinking: bool | None = None
    # Knowledge packs and indexes added to every question (bench --agent-prompt).
    references: int = 0
    run: str
    started_at: str = ""
    updated_at: str = ""
    complete: bool = False
    planned: int = 0
    answered: int = 0
    correct: int = 0
    accuracy: float = 0.0
    ci95: tuple[float, float] = (0.0, 1.0)
    areas: list[Area] = Field(default_factory=list)


class Education(BaseModel):
    """What the Education page shows, and where it looked."""

    runs_dirs: list[str]
    # Configured folders that do not exist.
    missing: list[str] = Field(default_factory=list)
    # Benchmark folders whose files could not be read, with the reason.
    skipped: list[str] = Field(default_factory=list)
    measurements: list[Measurement] = Field(default_factory=list)


def family_of(benchmark: str) -> str:
    """The suite a benchmark belongs to, or the benchmark itself."""
    for suite, members in BENCHMARK_SUITES.items():
        if benchmark in members:
            return suite
    return benchmark


def resolve_dirs(runs_dirs: list[str]) -> list[Path]:
    """Configured folders as absolute paths; relative ones start at the Kumaru folder."""
    paths = []
    for entry in runs_dirs:
        path = Path(entry).expanduser()
        paths.append(path if path.is_absolute() else REPO_ROOT / path)
    return paths


def benchmark_dirs(root: Path) -> list[Path]:
    """Every folder holding a summary.json at most two levels below `root`.

    `root` may be a folder of runs, one run, or one benchmark of a run.
    """
    found: list[Path] = []
    if (root / "summary.json").is_file():
        return [root]
    try:
        children = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return found
    for child in children:
        if (child / "summary.json").is_file():
            found.append(child)
            continue
        try:
            found.extend(sorted(g for g in child.iterdir() if (g / "summary.json").is_file()))
        except OSError:
            continue
    return found


def read_records(path: Path) -> dict[str, dict[str, Any]]:
    """The records of an items.jsonl, the last per id winning; torn lines skipped.

    Only `group` and `correct` are kept, and files are re-read only when their
    size or modification time changes.
    """
    try:
        stat = path.stat()
    except OSError:
        return {}
    key = (stat.st_size, stat.st_mtime_ns)
    with _cache_lock:
        cached = _cache.get(path)
    if cached and cached[0] == key:
        return cached[1]
    records: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8", errors="replace") as lines:
        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                continue  # the line being written, or a torn one after a hard kill
            if isinstance(record, dict) and isinstance(record.get("id"), str):
                records[record["id"]] = {"group": record.get("group"), "correct": bool(record.get("correct"))}
    with _cache_lock:
        if len(_cache) >= _CACHE_LIMIT:
            _cache.clear()
        _cache[path] = (key, records)
    return records


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="seconds")


def _area(name: str, answered: int, correct: int) -> Area:
    accuracy = correct / answered if answered else 0.0
    return Area(name=name, answered=answered, correct=correct, accuracy=accuracy, ci95=wilson_interval(correct, answered))


def _references(run_json: Path) -> int:
    """Knowledge packs and indexes the run added to every question, or 0."""
    try:
        run = json.loads(run_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not (run.get("settings") or {}).get("use_agent_prompt"):
        return 0
    agent = (run.get("config") or {}).get("agent") or {}
    return len(agent.get("knowledge_files") or []) + len(agent.get("knowledge_indexes") or [])


def read_measurement(bench_dir: Path) -> Measurement:
    """One benchmark of one run. Raises ValueError or OSError for unreadable files."""
    summary = json.loads((bench_dir / "summary.json").read_text(encoding="utf-8"))
    if not isinstance(summary, dict) or not isinstance(summary.get("benchmark"), str):
        raise ValueError("summary.json names no benchmark")
    items = bench_dir / "items.jsonl"
    mtimes = [p.stat().st_mtime for p in (bench_dir / "summary.json", items) if p.exists()]
    complete = bool(summary.get("complete"))
    planned_ids = [i for i in summary.get("item_ids") or [] if isinstance(i, str)]
    planned = len(planned_ids) or int(summary.get("n_planned") or 0)
    if complete:
        answered = int(summary.get("n") or 0)
        correct = int(summary.get("n_correct") or 0)
        areas = []
        for name, group in sorted((summary.get("by_group") or {}).items()):
            n = int(group.get("n") or 0)
            areas.append(_area(name, n, round(n * float(group.get("accuracy") or 0.0))))
    else:
        records = read_records(items)
        if planned_ids:
            wanted = set(planned_ids)
            records = {i: r for i, r in records.items() if i in wanted}
        answered = len(records)
        correct = sum(r["correct"] for r in records.values())
        tally: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for record in records.values():
            if record["group"]:
                tally[record["group"]][0] += 1
                tally[record["group"]][1] += record["correct"]
        areas = [_area(name, n, c) for name, (n, c) in sorted(tally.items())]
    benchmark = summary["benchmark"]
    return Measurement(
        benchmark=benchmark,
        family=family_of(benchmark),
        label=str(summary.get("label") or bench_dir.parent.name),
        model=str(summary.get("model") or ""),
        thinking=summary.get("thinking"),
        references=_references(bench_dir.parent / "run.json"),
        run=bench_dir.parent.name,
        started_at=str(summary.get("started_at") or ""),
        updated_at=_iso(max(mtimes)) if mtimes else "",
        complete=complete,
        planned=planned,
        answered=answered,
        correct=correct,
        accuracy=correct / answered if answered else 0.0,
        ci95=wilson_interval(correct, answered),
        areas=areas,
    )


def read_education(runs_dirs: list[str]) -> Education:
    """The newest measurement of each benchmark per setup (label and model)."""
    roots = resolve_dirs(runs_dirs)
    result = Education(runs_dirs=[str(p) for p in roots])
    newest: dict[tuple[str, str, str], Measurement] = {}
    for root in roots:
        if not root.is_dir():
            result.missing.append(str(root))
            continue
        for bench_dir in benchmark_dirs(root):
            try:
                found = read_measurement(bench_dir)
            except (OSError, ValueError, TypeError) as exc:
                logger.warning("education: skipping %s: %s", bench_dir, exc)
                result.skipped.append(f"{bench_dir}: {exc}")
                continue
            key = (found.benchmark, found.label, found.model)
            held = newest.get(key)
            if held is None or (found.started_at, found.updated_at) > (held.started_at, held.updated_at):
                newest[key] = found
    result.measurements = sorted(newest.values(), key=lambda m: (m.family, m.benchmark, m.label, m.model))
    return result
