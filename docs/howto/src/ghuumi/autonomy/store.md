# `src\ghuumi\autonomy\store.py`

> Persistence for autonomy: corpus, examples, cycle history and the promoted index and adapter.

**Read this when:** documents or examples are duplicated or lost, or a promoted index or adapter is not applied.

---

## What it does
Keeps state as atomic JSON plus `corpus.jsonl` and `examples.jsonl`, deduplicates by URL and hash, and mirrors documents under `docs/<topic>/` for indexing. `apply_promoted(config)` attaches the promoted index to `agent.knowledge_indexes` and sets `backend.adapter_path` only on the transformers backend when none is set. The CLI calls it from `_load`.

See [docs/AUTONOMY.md](../../../../AUTONOMY.md) for the whole flow.

