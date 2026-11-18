"""The Education page's data: `kumaru bench` run folders read per area, live while running."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kumaru.core.config import REPO_ROOT, EducationConfig, KumaruConfig
from kumaru.eval.benchmarks.stats import wilson_interval as bench_wilson
from kumaru.server import education
from kumaru.server.app import create_app


def write_bench(
    root: Path, run: str, benchmark: str, *, items: list | None = None, run_json: dict | None = None, **summary: object
) -> Path:
    """One benchmark of one run, as `kumaru bench run` lays it out."""
    folder = root / run / benchmark
    folder.mkdir(parents=True)
    fields = {"benchmark": benchmark, "label": "qwen36-nothink", "model": "qwen36", **summary}
    (folder / "summary.json").write_text(json.dumps(fields), encoding="utf-8")
    if items is not None:
        lines = [row if isinstance(row, str) else json.dumps(row) for row in items]
        (folder / "items.jsonl").write_text("\n".join(lines), encoding="utf-8")
    if run_json is not None:
        (root / run / "run.json").write_text(json.dumps(run_json), encoding="utf-8")
    return folder


@pytest.mark.parametrize(("successes", "n"), [(0, 0), (0, 5), (3, 7), (64, 99), (763, 763)])
def test_wilson_interval_matches_the_bench_statistics(successes: int, n: int) -> None:
    assert education.wilson_interval(successes, n) == bench_wilson(successes, n)


def test_a_finished_run_is_read_from_its_summary(tmp_path: Path) -> None:
    write_bench(
        tmp_path, "run1", "supergpqa_cs", complete=True, n=12, n_correct=9, n_planned=12,
        by_group={"Databases": {"n": 8, "accuracy": 0.75}, "Operating Systems": {"n": 4, "accuracy": 0.75}},
        items=["not read: the summary is final"],
    )  # fmt: skip
    [m] = education.read_education([str(tmp_path)]).measurements

    assert (m.complete, m.answered, m.correct, m.planned) == (True, 12, 9, 12)
    assert [(a.name, a.answered, a.correct) for a in m.areas] == [("Databases", 8, 6), ("Operating Systems", 4, 3)]
    assert m.areas[0].ci95 == bench_wilson(6, 8)


def test_an_unfinished_run_counts_the_planned_answers_so_far(tmp_path: Path) -> None:
    write_bench(
        tmp_path, "run1", "supergpqa_cs", complete=False, n=0, item_ids=["a", "b", "c", "d"],
        items=[
            {"id": "a", "group": "Databases", "correct": False},
            {"id": "a", "group": "Databases", "correct": True},  # a retry: the last record wins
            {"id": "b", "group": "Operating Systems", "correct": False},
            {"id": "x", "group": "Databases", "correct": True},  # not planned (an earlier, larger -n)
            '{"id": "c", "group": "Data',  # the line being written
        ],
    )  # fmt: skip
    [m] = education.read_education([str(tmp_path)]).measurements

    assert (m.complete, m.planned, m.answered, m.correct) == (False, 4, 2, 1)
    assert [(a.name, a.answered, a.correct) for a in m.areas] == [("Databases", 1, 1), ("Operating Systems", 1, 0)]


def test_the_newest_run_per_setup_wins_and_suite_members_share_a_section(tmp_path: Path) -> None:
    for run, started, correct in (("old", "2026-10-22T08:20:09+00:00", 70), ("new", "2026-10-22T09:55:57+00:00", 94)):
        write_bench(tmp_path, run, "multipl_e_cs", complete=True, n=100, n_correct=correct, started_at=started)
    write_bench(tmp_path, "think", "multipl_e_cs", complete=True, n=100, n_correct=96, label="qwen36-think")
    found = education.read_education([str(tmp_path)]).measurements

    assert [(m.label, m.run, m.correct, m.family) for m in found] == [
        ("qwen36-nothink", "new", 94, "multipl_e"),
        ("qwen36-think", "think", 96, "multipl_e"),
    ]


def test_references_count_only_when_the_run_sent_them(tmp_path: Path) -> None:
    agent = {"agent": {"knowledge_files": ["a.json"], "knowledge_indexes": ["b.sqlite", "c.sqlite"]}}
    write_bench(tmp_path, "with", "gsm8k", complete=True, label="refs",
                run_json={"settings": {"use_agent_prompt": True}, "config": agent})
    write_bench(tmp_path, "without", "gsm8k", complete=True, label="plain",
                run_json={"settings": {"use_agent_prompt": False}, "config": agent})  # fmt: skip
    found = {m.label: m.references for m in education.read_education([str(tmp_path)]).measurements}

    assert found == {"plain": 0, "refs": 3}


def test_unreadable_runs_and_missing_folders_are_reported_not_fatal(tmp_path: Path) -> None:
    write_bench(tmp_path, "good", "gsm8k", complete=True, n=1, n_correct=1)
    broken = tmp_path / "broken" / "gsm8k"
    broken.mkdir(parents=True)
    (broken / "summary.json").write_text("{not json", encoding="utf-8")
    result = education.read_education([str(tmp_path), str(tmp_path / "absent")])

    assert [m.run for m in result.measurements] == ["good"]
    assert result.missing == [str(tmp_path / "absent")]
    assert len(result.skipped) == 1 and "broken" in result.skipped[0]


def test_a_relative_folder_starts_at_the_kumaru_folder() -> None:
    assert education.resolve_dirs(["runs/bench"]) == [REPO_ROOT / "runs" / "bench"]


def test_the_endpoint_and_page_are_served(echo_config: KumaruConfig, tmp_path: Path) -> None:
    write_bench(tmp_path, "run1", "supergpqa_cs", complete=True, n=2, n_correct=1,
                by_group={"Databases": {"n": 2, "accuracy": 0.5}})  # fmt: skip
    config = echo_config.model_copy(update={"education": EducationConfig(runs_dirs=[str(tmp_path)])})
    with TestClient(create_app(config)) as client:
        body = client.get("/v1/education").json()
        page = client.get("/education")
        index = client.get("/").text

    assert body["runs_dirs"] == [str(tmp_path)]
    assert body["measurements"][0]["areas"][0] == {
        "name": "Databases", "answered": 2, "correct": 1, "accuracy": 0.5, "ci95": list(bench_wilson(1, 2)),
    }  # fmt: skip
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert 'href="/education"' in index
