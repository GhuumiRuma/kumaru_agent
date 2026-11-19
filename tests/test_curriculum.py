"""Protect curriculum phases: shipped files load, searches run once, runs resume.

Offline: searches are stubbed and benchmarks run on the echo backend.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ghuumi.academic.catalog import Catalog
from ghuumi.academic.models import Source
from ghuumi.academic.web import FetchError
from ghuumi.cli import app
from ghuumi.core.config import REPO_ROOT
from ghuumi.core.registry import BENCHMARK_SUITES, BENCHMARKS
from ghuumi.eval.benchmarks import runner
from ghuumi.train.curriculum import Phase, collect, load_phase, searches_file

PHASES = sorted((REPO_ROOT / "configs" / "curriculum").glob("*.yaml"))
PHASE = {
    "id": "p",
    "title": "P",
    "collect": {
        "queries": ["textbook", "survey"],
        "topics": [
            {"id": "cs", "name": "Computer science", "level": "area"},
            {"id": "os", "name": "Operating Systems", "level": "subfield", "parent_id": "cs"},
            {"id": "db", "name": "Databases", "level": "subfield", "parent_id": "cs"},
        ],
    },
}


@pytest.mark.parametrize("path", PHASES, ids=lambda p: p.stem)
def test_shipped_phases_load_name_real_benchmarks_and_fit_the_catalogue(path: Path, tmp_path: Path) -> None:
    phase = load_phase(path)
    assert phase.id == path.stem  # one id per file: its searches and runs are its own
    for name in phase.measure.benchmarks if phase.measure else []:
        assert name in BENCHMARK_SUITES or BENCHMARKS.get(name)
    catalog = Catalog(tmp_path / "academic.sqlite")
    for topic in phase.collect.topics if phase.collect else []:
        catalog.put_topic(topic)  # refuses an unknown parent or a misplaced level


def stub_search(calls: list[tuple[str, str]], fail_on: tuple[str, str] | None = None):
    def search(catalog: Catalog, topic_id: str, *, query: str) -> dict:
        calls.append((topic_id, query))
        if (topic_id, query) == fail_on:
            raise FetchError("OpenAlex daily budget spent")
        source = catalog.discover(Source(
            url=f"https://example.org/{topic_id}/{len(calls)}", title=f"{topic_id} {query}", topic_ids=[topic_id],
        ))  # fmt: skip
        return {"source_ids": [source.id]}

    return search


def test_collect_searches_once_and_a_repeat_finishes_what_failed(tmp_path: Path) -> None:
    phase = Phase.model_validate(PHASE)
    catalog = Catalog(tmp_path / "academic.sqlite")
    calls: list[tuple[str, str]] = []
    first = collect(phase, catalog, stub_search(calls, fail_on=("db", "survey")))
    # The area root is too broad to search; the failure stops collection.
    assert calls == [("os", "textbook"), ("os", "survey"), ("db", "textbook"), ("db", "survey")]
    assert len(first["searched"]) == 3 and "budget" in first["error"]
    assert catalog.topic("db").parent_id == "cs"

    calls.clear()
    second = collect(phase, catalog, stub_search(calls))
    assert calls == [("db", "survey")] and second["skipped"] == 3 and second["error"] is None
    calls.clear()
    assert collect(phase, catalog, stub_search(calls))["skipped"] == 4 and calls == []
    assert collect(phase, catalog, stub_search(calls), refresh=True)["skipped"] == 0 and len(calls) == 4
    remembered = searches_file(catalog, phase).read_text(encoding="utf-8")
    assert json.loads(remembered) == ["db|survey", "db|textbook", "os|survey", "os|textbook"]

    # A remembered search that left no sources (a new catalogue) runs again.
    (tmp_path / "new").mkdir()
    fresh = Catalog(tmp_path / "new" / "academic.sqlite")
    searches_file(fresh, phase).write_text(remembered, encoding="utf-8")
    calls.clear()
    collect(phase, fresh, stub_search(calls))
    assert len(calls) == 4


def test_run_measures_in_one_folder_that_a_repeat_resumes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "git_commit", lambda: None)  # no subprocesses in tests
    monkeypatch.setattr(runner, "environment", lambda: {"python": "test"})
    golden = tmp_path / "mini.jsonl"
    row = {"id": "say", "prompt": "Say ghuumi.", "grader": "contains", "answer": "ghuumi"}
    golden.write_text(json.dumps(row) + "\n", encoding="utf-8")
    profile = tmp_path / "echo.yaml"
    profile.write_text("backend:\n  name: echo\n  model: echo\n  extra:\n    delay_s: 0\n", encoding="utf-8")
    phase = tmp_path / "p.yaml"
    phase.write_text(
        f"id: p\ntitle: P\nmeasure:\n  benchmarks: ['{golden.as_posix()}']\n  think: false\n", encoding="utf-8"
    )
    args = ["curriculum", "run", str(phase), "-c", str(profile), "--out-root", str(tmp_path / "runs")]
    for _ in range(2):
        result = CliRunner().invoke(app, args)
        assert result.exit_code == 0, result.output
    items = tmp_path / "runs" / "p-echo-nothink" / "golden-mini" / "items.jsonl"
    assert len(items.read_text(encoding="utf-8").splitlines()) == 1  # the repeat asked nothing again
