from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kumaru.academic import web as web_module
from kumaru.academic.catalog import Catalog
from kumaru.academic.models import Evidence, Review, Source, Topic
from kumaru.academic.site import MANIFEST, Scope, collect_site, relative_path
from kumaru.academic.web import Page, PublicWeb, TransientFetchError
from kumaru.cli import app as cli
from kumaru.core.errors import ConfigError, RequestError

ROOT = "https://docs.example.org/lib/"


def html(body: str, head: str = "") -> bytes:
    return f"<html><head><title>T</title>{head}</head><body>{body}</body></html>".encode()


SITE = {
    ROOT: html(
        "<h1>Library</h1><p>Overview of the library.</p>"
        '<a href="a">A</a> <a href="b.html#part">B</a> <a href="/other/x">out</a>'
        '<a href="https://evil.example/lib/">host</a> <a href="c?page=2">query</a>'
        '<a href="logo.png">png</a> <a href="javascript:alert(1)">js</a> <a href="a">again</a>'
        '<a href="hidden">hidden</a> <a href="moved">moved</a> <a href="copy">copy</a>'
    ),
    ROOT + "a": html('<h1>A</h1><p>Functions in module a.</p><a href="sub/">sub</a>'),
    ROOT + "b.html": html("<h1>B</h1><p>Classes in module b.</p>"),
    ROOT + "sub/": html("<h1>Sub</h1><p>A nested page.</p>"),
    ROOT + "hidden": html("<p>Draft.</p>", '<meta name="robots" content="noindex">'),
    ROOT + "copy": html("<h1>A</h1><p>Functions in module a.</p><a href=\"sub/\">sub</a>"),
}


class SiteWeb(PublicWeb):
    def __init__(self, pages: dict[str, bytes] | None = None, fail: str = "") -> None:
        super().__init__()
        self.pages = SITE if pages is None else pages
        self.fail = fail
        self.calls: list[str] = []

    def fetch(self, url, *, robots=True, headers=None):
        self.calls.append(url)
        if url == self.fail:
            raise TransientFetchError("network down")
        if url == ROOT + "moved":
            return Page("https://docs.example.org/elsewhere", 200, "text/html", html("<p>x</p>"))
        if url not in self.pages:
            return Page(url, 404, "text/html", b"")
        return Page(url, 200, "text/html; charset=utf-8", self.pages[url])


@pytest.fixture
def catalog(tmp_path: Path) -> Catalog:
    db = Catalog(tmp_path / "academic.sqlite")
    db.put_topic(Topic(id="python", name="Python", level="area"))
    return db


def reviewed(catalog: Catalog, url: str = ROOT) -> str:
    source = catalog.discover(Source(url=url, title="Library docs", topic_ids=["python"]))
    proof = dict(url="https://docs.example.org/license", reviewed=True, value=1)
    catalog.review(source.id, Review(
        origin="human_verified", permission="text_permitted", topic_ids=["python"],
        evidence=[
            Evidence(kind="origin", note="Maintainers write it.", **proof),
            Evidence(kind="permission", note="Documentation license permits copying.", **proof),
        ],
    ))
    return source.id


def test_unreviewed_site_is_never_fetched(catalog, tmp_path):
    source = catalog.discover(Source(url=ROOT, title="Library docs", topic_ids=["python"]))
    web = SiteWeb()
    with pytest.raises(RequestError):
        collect_site(catalog, source.id, web, tmp_path / "out")
    assert web.calls == [] and not (tmp_path / "out").exists()


def test_crawl_stays_in_scope_and_writes_a_learnable_folder(catalog, tmp_path):
    out = tmp_path / "out"
    web = SiteWeb()
    report = collect_site(catalog, reviewed(catalog), web, out)
    assert all(url.startswith(ROOT) for url in web.calls)
    assert sorted(web.calls) == sorted(
        ROOT + p for p in ["", "a", "b.html", "sub/", "hidden", "moved", "copy"]
    )
    assert report["saved"] == 4 and report["complete"] and report["scope"] == ROOT
    assert sorted(p.relative_to(out).as_posix() for p in out.rglob("*.html")) == [
        "a.html", "b.html", "index.html", "sub/index.html",
    ]
    manifest = json.loads((out / MANIFEST).read_text())
    assert manifest["notice"].startswith("UNTRUSTED")
    reasons = {item["url"]: item["reason"] for item in manifest["skipped_pages"]}
    assert "indexed" in reasons[ROOT + "hidden"]
    assert "outside" in reasons[ROOT + "moved"]
    assert "Same content" in reasons[ROOT + "copy"]
    assert catalog.get(reviewed(catalog)).text is None
    result = CliRunner().invoke(cli, [
        "learn", "Library", "--version", "1", "--source", str(out), "-o", str(tmp_path / "i.sqlite"),
    ])
    assert result.exit_code == 0, result.output


def test_page_limit_and_existing_folders_are_respected(catalog, tmp_path):
    source_id = reviewed(catalog)
    report = collect_site(catalog, source_id, SiteWeb(), tmp_path / "out", max_pages=2)
    assert report["requested"] == 2 and not report["complete"]
    with pytest.raises(ConfigError):
        collect_site(catalog, source_id, SiteWeb(), tmp_path / "out")
    with pytest.raises(RequestError):
        collect_site(catalog, source_id, SiteWeb(), tmp_path / "new", max_pages=1001)


def test_outage_stops_the_crawl_and_keeps_a_partial_manifest(catalog, tmp_path):
    out = tmp_path / "out"
    with pytest.raises(TransientFetchError):
        collect_site(catalog, reviewed(catalog), SiteWeb(fail=ROOT + "b.html"), out)
    manifest = json.loads((out / MANIFEST).read_text())
    assert manifest["stopped"] == "network down" and not manifest["complete"]
    assert manifest["saved"] == 2


def test_blocked_root_writes_nothing(catalog, tmp_path):
    with pytest.raises(RequestError):
        collect_site(catalog, reviewed(catalog), SiteWeb(pages={}), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_file_names_are_safe_on_every_platform():
    scope = Scope.of(ROOT + "index.html")
    assert scope.prefix == "/lib/"
    assert relative_path(ROOT, scope, "text/html") == ["index.html"]
    assert relative_path(ROOT + "a%2F..%2F..%2Fx", scope, "text/html") == ["a_.._.._x.html"]
    assert relative_path(ROOT + "CON", scope, "text/html") == ["_CON.html"]
    assert relative_path(ROOT + "notes.txt", scope, "text/plain") == ["notes.txt"]
    long = relative_path(ROOT + "x" * 300, scope, "text/html")[0]
    assert len(long) <= 90 and long.endswith(".html")


def test_cli_collects_a_site(catalog, tmp_path, monkeypatch):
    monkeypatch.setattr(web_module, "PublicWeb", SiteWeb)
    result = CliRunner().invoke(cli, [
        "academic", "--database", str(catalog.path), "collect-site", reviewed(catalog),
        "-o", str(tmp_path / "out"), "--max-pages", "3",
    ])
    assert result.exit_code == 0, result.output
    assert '"saved": 3' in result.stdout
