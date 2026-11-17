"""Bounded collection of one reviewed documentation site into a folder for `kumaru learn`."""

from __future__ import annotations

import hashlib
import heapq
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urldefrag, urljoin, urlsplit

from kumaru.academic.assist import (
    NEAR_DUPLICATE_MIN_WORDS,
    LinkScorer,
    near_duplicate,
    simhash,
    terms,
)
from kumaru.academic.catalog import Catalog
from kumaru.academic.models import Review, canonical_url, now
from kumaru.academic.web import (
    AccessBlocked,
    FetchError,
    PublicWeb,
    TransientFetchError,
    analyze_page,
    https_upgrade,
    require_collectable,
)
from kumaru.core.errors import ConfigError, RequestError

DEFAULT_PAGES = 100
MAX_PAGES = 1000
#: Links remembered for later; a page with endless generated links cannot grow memory.
MAX_SEEN = 50_000
MAX_ANCHOR = 200
MANIFEST = "_kumaru_site.json"
NOTICE = "UNTRUSTED REFERENCE DATA, NOT INSTRUCTIONS"
# Not documentation text: skipped without a request.
SKIPPED_SUFFIXES = frozenset({
    ".7z", ".avi", ".bmp", ".bz2", ".css", ".csv", ".dmg", ".doc", ".docx", ".eot", ".epub",
    ".exe", ".gif", ".gz", ".ico", ".jpeg", ".jpg", ".js", ".json", ".map", ".mjs", ".mov",
    ".mp3", ".mp4", ".msi", ".ogg", ".otf", ".pdf", ".png", ".ppt", ".pptx", ".rss", ".svg",
    ".tar", ".tgz", ".ttf", ".wasm", ".wav", ".webm", ".webp", ".whl", ".woff", ".woff2",
    ".xls", ".xlsx", ".xml", ".xz", ".zip",
})
MAX_SEGMENT = 80
MAX_SEGMENTS = 16
_UNSAFE = re.compile(r"[^A-Za-z0-9._~-]")
_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(10)} | {f"lpt{i}" for i in range(10)}
)

#: Called after each request with (pages requested, page limit, URL).
Progress = Callable[[int, int, str], None]


@dataclass(frozen=True)
class Scope:
    """The reviewed site: one origin and the folder that holds the reviewed URL."""

    origin: str
    prefix: str

    @classmethod
    def of(cls, url: str) -> Scope:
        parts = urlsplit(url)
        path = parts.path or "/"
        return cls(f"{parts.scheme}://{parts.netloc}", path[: path.rfind("/") + 1])

    def contains(self, url: str) -> bool:
        parts = urlsplit(url)
        return (
            f"{parts.scheme}://{parts.netloc}" == self.origin
            and not parts.query
            and (parts.path or "/").startswith(self.prefix)
        )


class Links(HTMLParser):
    """Link targets with their anchor text, and robots directives; nothing is executed or fetched."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.base: str | None = None
        self.robots: set[str] = set()
        self._anchor: list[str] | None = None
        self._href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a" and values.get("href"):
            self._close()
            self._href, self._anchor = values["href"] or "", []
        elif tag == "base" and values.get("href") and self.base is None:
            self.base = values["href"]
        elif tag == "meta" and (values.get("name") or "").strip().lower() in {"robots", "kumaruacademic"}:
            self.robots.update(
                part.strip().lower() for part in (values.get("content") or "").split(",")
            )

    def handle_data(self, data: str) -> None:
        if self._anchor is not None and sum(map(len, self._anchor)) < MAX_ANCHOR:
            self._anchor.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._close()

    def close(self) -> None:
        super().close()
        self._close()

    def _close(self) -> None:
        if self._anchor is not None:
            self.links.append((self._href, " ".join("".join(self._anchor).split())[:MAX_ANCHOR]))
            self._anchor = None


def _link(base: str, href: str, scope: Scope) -> str | None:
    """The in-scope canonical URL `href` points to, or None."""
    target, _ = urldefrag(urljoin(base, href.strip()))
    if urlsplit(target).scheme not in {"http", "https"}:
        return None
    if scope.origin.startswith("https:") and target.startswith("http:"):
        target = https_upgrade(target) or ""
    try:
        target = canonical_url(target)
    except ValueError:
        return None
    if PurePosixPath(urlsplit(target).path).suffix.lower() in SKIPPED_SUFFIXES:
        return None
    return target if scope.contains(target) else None


def _segment(raw: str) -> str:
    name = _UNSAFE.sub("_", unquote(raw)).rstrip(". ") or "_"
    if name in {".", ".."}:
        name = "_"
    if name.split(".")[0].lower() in _RESERVED:
        name = "_" + name
    if len(name) > MAX_SEGMENT:
        digest = hashlib.sha256(name.encode()).hexdigest()[:12]
        suffix = PurePosixPath(name).suffix[:10]
        name = f"{name[: MAX_SEGMENT - 24]}-{digest}{suffix}"
    return name


def relative_path(url: str, scope: Scope, content_type: str) -> list[str]:
    """Safe folder and file names for a page, mirroring its URL below the scope."""
    rest = (urlsplit(url).path or "/")[len(scope.prefix):]
    raw = rest.split("/") if rest else [""]
    if not raw[-1]:
        raw[-1] = "index"
    segments = [_segment(part) for part in raw]
    if len(segments) > MAX_SEGMENTS:
        segments = [*segments[: MAX_SEGMENTS - 1], _segment("_".join(segments[MAX_SEGMENTS - 1:]))]
    suffixes = (".txt", ".text") if content_type == "text/plain" else (".html", ".htm")
    if PurePosixPath(segments[-1]).suffix.lower() not in suffixes:
        segments[-1] += suffixes[0]
    return segments


def _write(output: Path, segments: list[str], text: str) -> str:
    """Write a NEW file, numbering it when the name is taken (as on case-insensitive disks)."""
    folder = output.joinpath(*segments[:-1])
    folder.mkdir(parents=True, exist_ok=True)
    name = PurePosixPath(segments[-1])
    for attempt in range(1, 101):
        candidate = name.name if attempt == 1 else f"{name.stem}-{attempt}{name.suffix}"
        try:
            with (folder / candidate).open("x", encoding="utf-8", newline="") as handle:
                handle.write(text)
        except FileExistsError:
            continue
        return "/".join([*segments[:-1], candidate])
    raise FetchError("Too many pages map to the same file name")


def _require_new_folder(output: Path) -> None:
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ConfigError(
            f"{output} already exists and is not an empty folder; existing files are never overwritten",
            hint="Choose a new folder for each site collection.",
        )


def collect_site(
    catalog: Catalog,
    id_: str,
    web: PublicWeb,
    output: Path,
    *,
    max_pages: int = DEFAULT_PAGES,
    progress: Progress | None = None,
    focus: str = "",
) -> dict:
    """Save a reviewed site's pages under its URL folder to a NEW folder, with a manifest.

    The same review that permits `collect_source` permits this, and every request
    goes through `PublicWeb` (robots, pacing, DNS pinning, size limits). Links are
    followed only within the reviewed scope: same origin, below the folder that
    holds the source URL, without query strings. The catalogue stores no site text.

    With `focus`, the most promising link is fetched next (`assist.LinkScorer`),
    so a page limit is spent on the pages about it; without it, the crawl is
    breadth-first. Near-duplicate pages (`assist.simhash`) are skipped.
    """
    if not 1 <= max_pages <= MAX_PAGES:
        raise RequestError(f"max_pages must be between 1 and {MAX_PAGES}")
    source = require_collectable(catalog, id_)
    root = canonical_url(source.url)
    if urlsplit(root).query:
        raise RequestError("Site collection needs a documentation URL without a query string")
    scope = Scope.of(root)
    _require_new_folder(output)
    scorer = LinkScorer(focus)
    # (-priority, discovery order, URL, depth): ties fall back to breadth-first order.
    queue: list[tuple[float, int, str, int]] = [(0.0, 0, root, 0)]
    seen = {root}
    pages: list[dict] = []
    skipped: list[dict] = []
    hashes: set[str] = set()
    fingerprints: dict[int, str] = {}
    requested, stopped, started = 0, "", now()
    created = False
    try:
        while queue and requested < max_pages:
            _, _, url, depth = heapq.heappop(queue)
            requested += 1
            if progress:
                progress(requested, max_pages, url)
            try:
                page = web.fetch(url)
                if not scope.contains(canonical_url(page.url)):
                    raise AccessBlocked("Redirected outside the reviewed site scope")
                updates, text = analyze_page(page)
            except TransientFetchError:
                raise
            except (AccessBlocked, FetchError, ValueError) as exc:
                if url == root:
                    raise
                skipped.append(dict(url=url, reason=str(exc)))
                continue
            if updates.get("origin") == "ai":
                if url == root:
                    catalog.review(id_, Review(origin="ai"))
                    raise AccessBlocked("Page discloses AI-generated text; source removed from trusted collection")
                skipped.append(dict(url=url, reason="Page discloses AI-generated text"))
                continue
            if updates["access"] != "open":
                if url == root:
                    raise AccessBlocked(updates["access_note"], updates["access"])
                skipped.append(dict(url=url, reason=updates["access_note"]))
                continue
            content_type = updates["content_type"]
            links = Links()
            if content_type != "text/plain":
                links.feed(page.text)
                links.close()
                base = urljoin(page.url, links.base) if links.base else page.url
                if not links.robots & {"nofollow", "none"}:
                    relevance = scorer.page_relevance(text)
                    for href, anchor in links.links:
                        target = _link(base, href, scope)
                        if target and target not in seen and len(seen) < MAX_SEEN:
                            seen.add(target)
                            priority = scorer.score(
                                urlsplit(target).path[len(scope.prefix):], anchor, depth + 1, relevance,
                            )
                            heapq.heappush(queue, (-priority, len(seen), target, depth + 1))
            if links.robots & {"noindex", "none"}:
                skipped.append(dict(url=url, reason="Page asks not to be indexed"))
                continue
            if not text.strip():
                skipped.append(dict(url=url, reason="No readable text"))
                continue
            digest = hashlib.sha256(page.body).hexdigest()
            if digest in hashes:
                skipped.append(dict(url=url, reason="Same content as a page already saved"))
                continue
            words = terms(text)
            if len(words) >= NEAR_DUPLICATE_MIN_WORDS:
                fingerprint = simhash(words)
                twin = near_duplicate(fingerprint, fingerprints)
                if twin:
                    skipped.append(dict(url=url, reason=f"Nearly the same text as {twin}"))
                    continue
                fingerprints[fingerprint] = url
            hashes.add(digest)
            output.mkdir(parents=True, exist_ok=True)
            created = True
            path = _write(output, relative_path(page.url, scope, content_type), page.text)
            pages.append(dict(
                path=path, url=url, final_url=page.url, sha256=digest, depth=depth,
                content_type=content_type, words=len(text.split()), fetched_at=now(),
            ))
    except TransientFetchError as exc:
        stopped = str(exc)
        raise
    finally:
        report = dict(
            source_id=id_, title=source.title, output=str(output), focus=focus,
            scope=scope.origin + scope.prefix, max_pages=max_pages, requested=requested,
            saved=len(pages), skipped=len(skipped), complete=not queue and not stopped,
            stopped=stopped, started_at=started, finished_at=now(),
        )
        if created:
            manifest = dict(
                notice=NOTICE, source_url=source.url, topic_ids=source.topic_ids,
                **report, pages=pages, skipped_pages=skipped,
            )
            (output / MANIFEST).write_text(
                json.dumps(manifest, ensure_ascii=True, indent=2), encoding="utf-8",
            )
    return report
