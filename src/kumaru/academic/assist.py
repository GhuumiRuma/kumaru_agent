"""Cheap, model-free search assistance: triage ranking, query suggestions,
focused-crawl link scores and near-duplicate fingerprints.

Everything here is linear in the text it reads and keeps at most a vocabulary
or a few integers per document. None of it approves, reviews or scores the
legitimacy of a source: these are orderings for a human or a crawler budget.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from collections.abc import Iterable, Iterator

from kumaru.academic.catalog import Catalog
from kumaru.academic.models import Source
from kumaru.agent.doc_index import STOPWORDS
from kumaru.core.errors import RequestError

_WORD = re.compile(r"[^\W_]+")
#: Sources read for one ranking or suggestion; bounds time and memory on huge catalogues.
MAX_DOCUMENTS = 20_000
_PAGE = 500
# BM25's usual constants: term-frequency saturation and length normalisation.
K1, B = 1.2, 0.75
#: How `rank_candidates` mixes its components; relevance dominates by design.
RANK_WEIGHTS = {"relevance": 0.7, "citations": 0.2, "recency": 0.1}
# Path or anchor words that rarely lead to reference material.
LOW_VALUE = frozenset(
    """genindex search changelog changes whatsnew news blog archive archives print login
    signin signup register 404 download downloads release releases""".split()  # noqa: SIM905
)
SIMHASH_BITS = 64
#: Pages within this many differing bits are near-duplicates (about 95% similar).
NEAR_DUPLICATE_BITS = 3
#: Shorter pages are compared exactly only: templated stubs differ in few words.
NEAR_DUPLICATE_MIN_WORDS = 100


def terms(text: str) -> list[str]:
    """Lower-case content words: no stopwords, no single characters."""
    return [w for w in (m.group(0).lower() for m in _WORD.finditer(text)) if len(w) > 1 and w not in STOPWORDS]


def _sources(catalog: Catalog, topic_id: str | None) -> Iterator[Source]:
    """Individual works (not containers), in pages, up to `MAX_DOCUMENTS`."""
    for offset in range(0, MAX_DOCUMENTS, _PAGE):
        page = catalog.list_sources(topic_id=topic_id, containers=False, limit=_PAGE, offset=offset)
        yield from page
        if len(page) < _PAGE:
            return


def _topic_terms(catalog: Catalog, topic_id: str) -> list[str]:
    words: list[str] = []
    for topic in catalog.topic_path(topic_id):
        words += terms(" ".join([topic.name, *topic.aliases]))
    return list(dict.fromkeys(words))


# -- 1. Review triage: BM25 over titles plus citation and recency priors ---------


def rank_candidates(
    catalog: Catalog, topic_id: str, query: str = "", *, limit: int = 20, include_reviewed: bool = False,
) -> list[dict]:
    """Order a topic's sources for review: most relevant to the topic and query first.

    Relevance is BM25 between the source title and the query terms (the query's
    own words count double; the topic path and aliases count once), divided by the
    best score so it is 0..1. Citations and recency are log/linear priors scaled
    to the corpus; unknown values contribute 0. O(total title words) time and
    O(sources + vocabulary) space.
    """
    if not 1 <= limit <= 500:
        raise RequestError("limit must be between 1 and 500")
    weights = Counter(_topic_terms(catalog, topic_id))
    for word in terms(query):
        weights[word] += 2
    docs = [(s, Counter(terms(s.title))) for s in _sources(catalog, topic_id)]
    if not docs:
        return []
    df: Counter[str] = Counter()
    for _, counts in docs:
        df.update(counts.keys())
    n = len(docs)
    avg = sum(sum(c.values()) for _, c in docs) / n or 1
    idf = {w: math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5)) for w in weights}
    cites = max((s.citation_count or 0 for s, _ in docs), default=0)
    years = [s.published_year for s, _ in docs if s.published_year]
    oldest, newest = (min(years), max(years)) if years else (0, 0)
    rows = []
    for source, counts in docs:
        if source.topic_reviewed and not include_reviewed:
            continue
        length = sum(counts.values())
        bm25 = sum(
            weight * idf[w] * counts[w] * (K1 + 1) / (counts[w] + K1 * (1 - B + B * length / avg))
            for w, weight in weights.items() if counts[w]
        )
        rows.append((source, bm25, [w for w in weights if counts[w]]))
    best = max((bm25 for _, bm25, _ in rows), default=0) or 1
    ranked: list[tuple[float, str, dict]] = []
    for source, bm25, matched in rows:
        parts = {
            "relevance": bm25 / best,
            "citations": math.log1p(source.citation_count or 0) / math.log1p(cites) if cites else 0.0,
            "recency": (
                (source.published_year - oldest) / (newest - oldest)
                if source.published_year and newest > oldest else 0.0
            ),
        }
        score = round(sum(RANK_WEIGHTS[k] * v for k, v in parts.items()), 4)
        ranked.append((-score, source.id, dict(
            id=source.id, title=source.title, url=source.url, score=score,
            components={k: round(v, 4) for k, v in parts.items()}, matched=matched,
        )))
    ranked.sort(key=lambda row: row[:2])
    return [row for _, _, row in ranked[:limit]]


# -- 2. Query suggestions: pseudo-relevance feedback ----------------------------


def _phrases(title: str) -> set[str]:
    """Distinct words and adjacent word pairs of one title."""
    words = terms(title)
    return set(words) | {f"{a} {b}" for a, b in zip(words, words[1:], strict=False)}


def suggest_queries(catalog: Catalog, topic_id: str, *, limit: int = 10) -> dict:
    """Terms that distinguish a topic's sources from the whole catalogue.

    Foreground: the topic's human-reviewed sources (all its sources when fewer
    than 3 are reviewed). Background: the catalogue. Each word or word pair in
    at least two foreground titles scores `fg_share * log((N + 1) / (bg_df + 1))`,
    a TF-IDF over document frequencies. Terms already in the topic's name or
    aliases are left out. O(total title words) time, O(vocabulary) space.
    """
    if not 1 <= limit <= 50:
        raise RequestError("limit must be between 1 and 50")
    known = set(_topic_terms(catalog, topic_id))
    topic_sources = list(_sources(catalog, topic_id))
    reviewed = [s for s in topic_sources if s.topic_reviewed]
    basis = reviewed if len(reviewed) >= 3 else topic_sources
    fg: Counter[str] = Counter()
    for source in basis:
        fg.update(_phrases(source.title))
    bg: Counter[str] = Counter()
    total = 0
    for source in _sources(catalog, None):
        bg.update(p for p in _phrases(source.title) if p in fg)
        total += 1
    scored = []
    for phrase, count in fg.items():
        if count < 2 or set(phrase.split()) <= known:
            continue
        score = count / len(basis) * math.log((total + 1) / (bg[phrase] + 1))
        if score > 0:
            scored.append((score, phrase, count))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return dict(
        topic_id=topic_id,
        basis="reviewed" if basis is reviewed else "unreviewed",
        sources=len(basis),
        suggestions=[
            dict(query=phrase, score=round(score, 4), sources=count)
            for score, phrase, count in scored[:limit]
        ],
    )


# -- 3. Focused crawling: which link to fetch next -------------------------------


class LinkScorer:
    """Scores links for a best-first crawl toward `focus` (empty: breadth-first).

    A link's priority is the share of focus words in its anchor text and URL path,
    plus 0.3 times the share found in the page that links to it, minus 0.05 per
    level of depth, minus 0.5 for low-value pages (search, changelogs, indexes).
    O(anchor + URL words) per link.
    """

    def __init__(self, focus: str = "") -> None:
        self.focus = frozenset(terms(focus))

    def page_relevance(self, text: str) -> float:
        if not self.focus:
            return 0.0
        return len(self.focus.intersection(terms(text))) / len(self.focus)

    def score(self, url_path: str, anchor: str, depth: int, parent_relevance: float = 0.0) -> float:
        words = set(terms(url_path.replace("/", " "))) | set(terms(anchor))
        match = len(self.focus & words) / len(self.focus) if self.focus else 0.0
        penalty = 0.5 if words & LOW_VALUE else 0.0
        return match + 0.3 * parent_relevance - 0.05 * depth - penalty


# -- 4. Near-duplicates: SimHash -------------------------------------------------


def simhash(words: Iterable[str]) -> int:
    """64-bit SimHash of word counts: similar texts get fingerprints a few bits apart."""
    totals = [0] * SIMHASH_BITS
    for word, count in Counter(words).items():
        value = int.from_bytes(hashlib.blake2b(word.encode(), digest_size=8).digest(), "big")
        for bit in range(SIMHASH_BITS):
            totals[bit] += count if value >> bit & 1 else -count
    return sum(1 << bit for bit, total in enumerate(totals) if total > 0)


def near_duplicate(fingerprint: int, seen: dict[int, str]) -> str | None:
    """The URL whose fingerprint is within `NEAR_DUPLICATE_BITS`, if any. O(len(seen))."""
    for other, url in seen.items():
        if (fingerprint ^ other).bit_count() <= NEAR_DUPLICATE_BITS:
            return url
    return None
