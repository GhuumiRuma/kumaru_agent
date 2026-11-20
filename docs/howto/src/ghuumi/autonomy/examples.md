# `src\ghuumi\autonomy\examples.py`

> Builds quote-verified training examples from pulled documents.

**Read this when:** training data looks wrong, is empty, or leaks between train and eval.

---

## What it does
Splits documents into passages and asks the base model for question, answer and quote as JSON. A candidate is kept only if the quote (6+ words) appears verbatim in the passage. Questions are deduplicated, documents are built once, and the train/eval split is by document hash so a document never lands in both.

See [docs/AUTONOMY.md](../../../../AUTONOMY.md) for the whole flow.

