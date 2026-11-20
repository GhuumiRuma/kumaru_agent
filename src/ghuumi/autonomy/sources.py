"""Autonomous source collection: licensed open-access works only, through the safe fetcher.

This is separate from the reviewed academic catalogue on purpose. The catalogue
needs a human to confirm provenance and permission before text is collected;
this loop replaces that confirmation with machine-checkable evidence instead
(an explicit open licence in OpenAlex metadata, a page that is reachable
without a gate, no AI-generation disclosure) and keeps everything it collects
apart, labelled, and removable.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from urllib.parse import urlencode, urlsplit

from pydantic import ValidationError

from ghuumi.academic.models import normalized_doi, now
from ghuumi.academic.openaccess import (
    OPENALEX,
    WORK_FIELDS,
    OpenAlexResults,
    OpenAlexWork,
    openalex_json,
    openalex_locations,
)
from ghuumi.academic.web import (
    AccessBlocked,
    FetchError,
    PublicWeb,
    RateLimited,
    TransientFetchError,
    analyze_page,
)
from ghuumi.autonomy.store import Document, Store
from ghuumi.core.config import AutonomyConfig
from ghuumi.core.errors import RequestError
from ghuumi.core.logging import get_logger

logger = get_logger(__name__)

MIN_CHARS = 1500


@dataclass
class PullReport:
    added: list[Document] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def search_works(
    web: PublicWeb, topic: str, limit: int, api_key: str | None = None
) -> list[OpenAlexWork]:
    params = {
        "search": topic[:500],
        "per_page": limit,
        "select": WORK_FIELDS,
        "filter": "is_oa:true",
    }
    url = f"{OPENALEX}/works?{urlencode(params)}"
    try:
        return OpenAlexResults.model_validate(openalex_json(web, url, api_key)).results
    except ValidationError as exc:
        raise FetchError("OpenAlex returned invalid scholarly metadata") from exc


def _collect_work(
    work: OpenAlexWork,
    topic: str,
    config: AutonomyConfig,
    store: Store,
    web: PublicWeb,
    report: PullReport,
) -> None:
    allowed = {name.casefold() for name in config.licenses}
    free = [loc for loc in openalex_locations(work) if loc.license in allowed]
    if not free:
        report.skip("no open licence")
        return
    title = " ".join((work.display_name or "").split())[:500]
    if not title:
        report.skip("untitled")
        return
    for location in free:
        if location.url in store.state().seen_urls:
            report.skip("already collected")
            return
        try:
            page = web.fetch(location.url)
            updates, text = analyze_page(page)
        except RateLimited:
            raise
        except (AccessBlocked, TransientFetchError, FetchError, RequestError) as exc:
            logger.info("autonomy: %s not collected: %s", location.url, exc)
            if not isinstance(exc, TransientFetchError):
                store.remember_url(location.url)
            report.skip("not reachable")
            continue
        if updates.get("access") != "open":
            store.remember_url(location.url)
            report.skip("access gate")
            continue
        if updates.get("origin") == "ai":
            store.remember_url(location.url)
            report.skip("AI-generated")
            continue
        if urlsplit(page.url).hostname != urlsplit(location.url).hostname:
            report.skip("redirected to another host")
            continue
        text = text.strip()
        if len(text) < MIN_CHARS:
            store.remember_url(location.url)
            report.skip("too little text")
            continue
        text = text[: config.max_chars_per_document]
        digest = hashlib.sha256(text.encode()).hexdigest()
        doi = None
        if work.doi:
            try:
                doi = normalized_doi(work.doi)
            except RequestError:
                doi = None
        doc = Document(
            id=digest[:16],
            topic=topic,
            title=title,
            url=location.url,
            doi=doi,
            license=location.license or "",
            sha256=digest,
            fetched_at=now(),
            text=text,
        )
        if store.add_document(doc):
            report.added.append(doc)
        else:
            report.skip("duplicate text")
        return
    # Every free location failed; nothing to add.


def pull_sources(
    config: AutonomyConfig,
    store: Store,
    *,
    web: PublicWeb | None = None,
    openalex_api_key: str | None = None,
) -> PullReport:
    """One collection pass over `config.topics`, bounded by `max_documents_per_cycle`."""
    web = web or PublicWeb()
    report = PullReport()
    for topic in config.topics:
        if len(report.added) >= config.max_documents_per_cycle:
            break
        try:
            works = search_works(web, topic, config.works_per_topic, openalex_api_key)
        except RateLimited as exc:
            report.error = str(exc)
            break
        except (FetchError, RequestError) as exc:
            report.error = f"{topic}: {exc}"
            continue
        for work in works:
            if len(report.added) >= config.max_documents_per_cycle:
                break
            try:
                _collect_work(work, topic, config, store, web, report)
            except RateLimited as exc:
                report.error = str(exc)
                return report
    return report
