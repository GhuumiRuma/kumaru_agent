# `src/kumaru/academic/assist.py`

Cheap, model-free helpers that make searching faster. None of them reviews, approves
or scores legitimacy ([scoring.py](scoring.md) does that). They only decide **what to
look at first**, either for a person or for a crawler's page budget.

| Function | CLI | Algorithm | Cost |
|---|---|---|---|
| `rank_candidates(catalog, topic, query)` | `rank TOPIC -q ...` | BM25 over titles; query words count double, topic path and aliases once; normalised to the best match, then `0.7·relevance + 0.2·log-citations + 0.1·recency` (`RANK_WEIGHTS`) | O(title words) time, O(sources + vocabulary) space |
| `suggest_queries(catalog, topic)` | `suggest-queries TOPIC` | Pseudo-relevance feedback: words and word pairs in ≥2 of the topic's titles, scored `share × log((N+1)/(df+1))` against the whole catalogue; the topic's own name/aliases are left out | O(title words), O(vocabulary) |
| `LinkScorer(focus)` | `collect-site --focus` | Best-first crawl priority: share of focus words in anchor text and URL path, `+0.3 ×` the parent page's share, `−0.05` per depth, `−0.5` for search/changelog/index pages (`LOW_VALUE`) | O(anchor + path words) per link |
| `simhash` / `near_duplicate` | used by `collect-site` | 64-bit SimHash of word counts (BLAKE2b per word); pages within `NEAR_DUPLICATE_BITS` (3) of a saved page are skipped | O(words) per page; 8 bytes kept per page; O(pages) comparison |

Details:

- `rank` lists only sources whose topics are **not yet reviewed** (a review queue)
  unless `--all`. Unknown citation counts or years add 0, they are not guessed.
  Each row gives `components` and `matched` terms, so you can see why it ranks there.
- `suggest-queries` uses the topic's reviewed sources when there are at least 3,
  otherwise all of its sources, and says which (`basis`). Feed a suggestion back into
  `search TOPIC --query "..."`.
- Both read at most `MAX_DOCUMENTS` (20,000) sources, 500 at a time.
- Near-duplicate checks apply only to pages with at least
  `NEAR_DUPLICATE_MIN_WORDS` (100) content words. Short templated pages
  (one per function) differ in only a few words and would be wrongly merged.
  Identical pages are still caught by their SHA-256.
- Words come from `terms()`: lower-cased, single characters and `doc_index.STOPWORDS`
  removed, so programming words like `set` and `new` stay.

Changing a weight changes behaviour that is pinned by tests:
`tests/test_academic_assist.py` and the crawl tests in `tests/test_academic_site.py`.
[User guide](../../../../ACADEMIC.md#search-assistance).
