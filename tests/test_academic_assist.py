from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ghuumi.academic.assist import (
    LinkScorer,
    near_duplicate,
    rank_candidates,
    simhash,
    suggest_queries,
    terms,
)
from ghuumi.academic.catalog import Catalog
from ghuumi.academic.models import Review, Source
from ghuumi.cli import app as cli

WORDS = [f"word{i}" for i in range(60)]
# Prose with realistic repetition: word i appears (i % 7) + 1 times.
TEXT = " ".join(w for i, w in enumerate(WORDS) for _ in range(i % 7 + 1))


@pytest.fixture
def catalog(tmp_path: Path) -> Catalog:
    return Catalog(tmp_path / "academic.sqlite")


def add(catalog: Catalog, slug: str, title: str, topic: str = "calculus", **kwargs) -> Source:
    return catalog.discover(Source(
        url=f"https://example.org/{slug}", title=title, topic_ids=[topic], **kwargs,
    ))


def test_rank_puts_the_query_match_first_and_explains_itself(catalog):
    chain = add(catalog, "a", "The chain rule for derivatives", published_year=2001)
    add(catalog, "b", "Calculus for engineers", citation_count=900, published_year=2020)
    add(catalog, "c", "Lecture notes", citation_count=5)
    ranked = rank_candidates(catalog, "calculus", "chain rule")
    assert [row["id"] for row in ranked][0] == chain.id
    top = ranked[0]
    assert top["components"]["relevance"] == 1 and top["matched"] == ["chain", "rule"]
    assert ranked[1]["components"]["citations"] == 1 and ranked[1]["components"]["recency"] == 1
    assert all(0 <= row["score"] <= 1 for row in ranked)


def test_rank_skips_reviewed_sources_unless_asked(catalog):
    source = add(catalog, "a", "Chain rule")
    catalog.review(source.id, Review(topic_ids=["calculus"]))
    assert rank_candidates(catalog, "calculus", "chain") == []
    assert len(rank_candidates(catalog, "calculus", "chain", include_reviewed=True)) == 1
    assert rank_candidates(catalog, "groups") == []


def test_suggestions_are_distinctive_and_skip_the_topic_name(catalog):
    for i, title in enumerate([
        "Fundamental theorem of calculus", "A proof of the fundamental theorem",
        "Fundamental theorem and Riemann sums", "Riemann sums in calculus",
    ]):
        add(catalog, f"c{i}", title)
    for i in range(6):
        add(catalog, f"g{i}", f"A theorem about groups {i}", topic="groups")
    result = suggest_queries(catalog, "calculus")
    queries = [s["query"] for s in result["suggestions"]]
    assert result["basis"] == "unreviewed" and result["sources"] == 4
    assert queries[:2] == ["fundamental", "fundamental theorem"]
    assert "riemann sums" in queries and "calculus" not in queries
    assert queries.index("theorem") > queries.index("fundamental theorem")


def test_cli_rank_and_suggest(catalog):
    add(catalog, "a", "The chain rule")
    prefix = ["academic", "--database", str(catalog.path)]
    ranked = CliRunner().invoke(cli, [*prefix, "rank", "calculus", "-q", "chain rule"])
    assert ranked.exit_code == 0 and json.loads(ranked.stdout)[0]["title"] == "The chain rule"
    suggested = CliRunner().invoke(cli, [*prefix, "suggest-queries", "calculus", "--limit", "5"])
    assert suggested.exit_code == 0 and json.loads(suggested.stdout)["topic_id"] == "calculus"


def test_link_scorer_prefers_focus_and_penalises_low_value_pages():
    scorer = LinkScorer("asyncio tasks")
    relevant = scorer.score("asyncio-task.html", "Coroutines and Tasks", 1)
    other = scorer.score("json.html", "JSON encoder", 1)
    assert relevant > other
    assert scorer.score("changelog.html", "Changelog", 1) < other
    assert scorer.score("json.html", "JSON", 1, parent_relevance=1) > other
    assert LinkScorer().score("a.html", "A", 1) > LinkScorer().score("a/b.html", "B", 2)


def test_simhash_finds_near_duplicates_but_not_different_text():
    base = simhash(terms(TEXT))
    edited = simhash(terms(TEXT.replace("word5", "other", 1) + " extra"))
    different = simhash(terms(" ".join(reversed(WORDS)) + " " + " ".join(f"x{i}" for i in range(200))))
    assert base == simhash(terms(TEXT))
    assert near_duplicate(edited, {base: "https://a"}) == "https://a"
    assert near_duplicate(different, {base: "https://a"}) is None
