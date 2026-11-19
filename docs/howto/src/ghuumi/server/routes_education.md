# `src\ghuumi\server\routes_education.py`

> `GET /v1/education`: the newest benchmark results per area, as JSON, for the Education page.

**Read this when:** you call the endpoint yourself or change what the page receives.

---

## What it does

- **`GET /v1/education`** returns `read_education(config.education.runs_dirs)` ([`education.md`](education.md)): `runs_dirs`, `missing`, `skipped` and `measurements`, each with its `areas`.
- It is a plain (synchronous) handler, so FastAPI runs it in a worker thread: reading result files never blocks chat streaming.
- Like `/health`, it needs no API key: it returns only aggregate scores and folder names, never questions or answers.

## Example

```powershell
curl http://127.0.0.1:8080/v1/education
```

```json
{"runs_dirs": ["D:/ghuumi/runs/bench"], "missing": [], "skipped": [],
 "measurements": [{"benchmark": "supergpqa_cs", "family": "supergpqa_cs", "label": "qwen36-nothink",
   "answered": 171, "correct": 110, "accuracy": 0.643, "ci95": [0.569, 0.711], "complete": false,
   "areas": [{"name": "Databases", "answered": 35, "correct": 24, "accuracy": 0.686, "ci95": [0.52, 0.81]}]}]}
```

## Related files

- [`app.md`](app.md): includes this router, and serves `/education` when the UI is on.
- [`../../../ui/education.js.md`](../../../ui/education.js.md): the page that polls it.
