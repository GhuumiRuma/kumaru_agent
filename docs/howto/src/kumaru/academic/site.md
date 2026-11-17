# `src/kumaru/academic/site.py`

`collect_site(catalog, source_id, web, output, max_pages=100)` saves the pages of one
reviewed documentation site into a **new** folder that `kumaru learn` can index. It
is how versioned language and framework documentation becomes reference material
for the coding agent. CLI: `kumaru academic collect-site SOURCE_ID -o DIR`.

How it works:

- **Same review as one page.** `web.require_collectable` applies the checks
  `collect_source` uses (human origin, text permission, reviewed topics, no pending
  notices, not restricted) before the first request. One review covers the site.
- **Scope is the reviewed URL's folder.** Breadth-first from the source URL, a link is
  followed only on the same scheme and host, below the folder holding that URL (`.../library/`
  for `.../library/` or `.../library/intro.html`), and without a query string. Binary
  suffixes (`.png`, `.pdf`, `.zip`, ...) are not requested. A redirect out of scope is skipped.
- **Every request goes through `PublicWeb`**: robots per page, at least one second
  per host, DNS pinning, 2 MB responses. `analyze_page` classifies each page.
- **Pages are skipped, not failed**, when blocked, gated, empty, marked
  `noindex` (meta `robots` or `KumaruAcademic`), duplicates of a saved page (same
  body hash) or disclosing AI text. `nofollow` stops link extraction from that page.
  The root is the exception: if it fails, nothing is written, and an AI disclosure
  on it revokes trust as in `collect_source`.
- **Output.** Original HTML or plain text, mirrored by URL path with safe names
  (no `..`, Windows device names, trailing dots or overlong segments), written with
  `open("x")` and numbered on collision. `_kumaru_site.json` records the untrusted
  notice, scope, limits, every page's URL, final URL, hash and time, and each skip
  reason. `.json` is not a documentation suffix, so `kumaru learn` ignores it.
- **Bounds.** `max_pages` (1-1000) counts requests, including skipped ones. At most
  50,000 links are remembered. A `TransientFetchError` stops the crawl, writes the
  manifest with `stopped` and `complete: false`, and re-raises (HTTP 503).

The catalogue stores no site text and the source record is not changed (except an
AI disclosure on the root). Re-run into a new folder to refresh.
Tests: `tests/test_academic_site.py`. [User guide](../../../../ACADEMIC.md#collect-a-documentation-site).
