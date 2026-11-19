# `src\ghuumi\train\curriculum.py`

> Curriculum phases: one YAML file per area of study that says which references to collect and which benchmarks measure it. `ghuumi curriculum run` does both, and repeating it only does what is missing.

**Read this when:** you start a new area (the next is programming languages), re-run a phase after a stop, or wonder why a repeat finished in seconds.

---

## What it does

```powershell
ghuumi curriculum run configs\curriculum\cs-foundations.yaml -c qwen36
ghuumi curriculum run configs\curriculum\cs-foundations.yaml -c qwen36 --no-measure   # references only
.\scripts\overnight.ps1 "ghuumi curriculum run configs\curriculum\cs-foundations.yaml -c qwen36"
```

1. **Collect** (`collect`): adds the phase's topics to the academic catalogue, then searches OpenAlex for open-access works, once per (topic, query). Area roots are not searched (too broad). Done searches are remembered in `<catalogue>.<phase>.searches.json` beside the catalogue's database (by default `~/.local/share/ghuumi/academic.cs-foundations.searches.json`). A topic with no sources at all is searched again. The first failed search (an outage, a spent daily budget) stops collection; the rest run next time. `--refresh` repeats every search.
2. **Measure**: runs the phase's benchmarks in **one fixed folder per setup**, `<first education.runs_dirs>/<phase id>-<profile>-<think mode>`, for example `D:\ghuumi\runs\bench\cs-foundations-qwen36-nothink`. A repeat resumes it: answered questions are reused, never asked again. The Education page reads the same folder.

Nothing is approved and no weights change: every work found stays unreviewed until a person reviews it on the Academic page.

## Why it exists

- **Repeatable without an assistant.** A phase is data, not a session: re-run it after a crash, a reboot or a week, and it picks up where it stopped. The next area is a new YAML file, not new code.
- **No token is spent twice.** Model tokens go only to unanswered questions; searches already done cost nothing.

## Phase file

| Field | Meaning |
|---|---|
| `id` | Names the run folder and the searches file; must equal the file name (a test checks). |
| `title` | Shown when it runs. |
| `measure.benchmarks` | Names from `ghuumi bench list`, or golden-set `.jsonl` paths. |
| `measure.limit` | Questions per benchmark, a stratified sample; `null` = all. Default 100. |
| `measure.think` | `false`, `true`, or omitted for the profile's default. |
| `collect.topics` | Academic `Topic` records (`id`, `name`, `level`, `parent_id`, `aliases`), parents first. |
| `collect.queries` | Words added to each topic's search, e.g. `textbook`. Default: one search with none. |

## Public API

| Name | What it's for |
|---|---|
| `load_phase(path) -> Phase` | Read and validate a phase file; `ConfigError` names the problem. |
| `collect(phase, catalog, search, *, refresh=False) -> dict` | Step 1. `search` is `discover` with its provider settings bound; tests pass a stub. Returns `searched`, `skipped` and `error`. |
| `searches_file(catalog, phase) -> Path` | Where done searches are remembered. |
| `Phase`, `Measure`, `Collect` | The file's models; unknown fields are refused. |

## Failure modes

- **`collection stopped: ... 429`**: OpenAlex's free daily budget is spent. Run again tomorrow, or set `academic.openalex_api_key`.
- **A resume is refused:** the profile's model or settings changed since the run started, so old answers would not count. Use another profile name, or move the old folder away.

## Related files

- [`../../../configs/cs-foundations.yaml.md`](../../../configs/cs-foundations.yaml.md): phase 1.
- [`../eval/benchmarks/runner.md`](../eval/benchmarks/runner.md): resuming and run folders.
- [`../server/education.md`](../server/education.md): the page that shows the results.
- [`../../../../ACADEMIC.md`](../../../../ACADEMIC.md): reviewing what was collected.
