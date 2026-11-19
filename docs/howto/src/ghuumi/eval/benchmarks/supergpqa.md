# `src\ghuumi\eval\benchmarks\supergpqa.py`

> SuperGPQA: graduate-level multiple choice (mostly ten options) in two slices, `supergpqa_cs` (all 763 computer science questions, by subfield) and `supergpqa_stem` (the other sciences and engineering, 16,967 questions, by area), so a score says *which area* a model knows.

**Read this when:** you run either slice, you read the Education page's graduate-level bars, or you wonder why a STEM run has no computer science area.

---

## What it does

- **`SuperGPQA.load()`** reads `m-a-p/SuperGPQA`, split `train` (26,529 questions), pinned to `REVISION`, through `load_hf_rows`. A slice's `area(row)` picks the questions and names their group; rows it returns `None` for are left out.
  - `id` = `<slice>/<uuid>`, `answer` = the dataset's `answer_letter`, `meta = {"letters": ...}` (the letters that exist).
  - `prompt` = `PROMPT`, the reference zero-shot prompt, with options as `A) text` lines. Options keep their own newlines, as the reference does.
- **`SuperGPQA.grade()`**: `extract_choice` on the final `Answer: X` line, falling back to the last standalone capital letter. `detail.method` records which.
- **`cs_area(row)`**: the subfield for the field "Computer Science and Technology": Databases 157, Data Structures 99, Operating Systems 85, Principles of Computer Organization 82, Computer Software and Theory 79, Computer Networks 59, Formal Languages 52, Computer Architecture 50, Advanced Programming Languages 50, Pattern Recognition 50.
- **`stem_area(row)`**: Mathematics 2,622, Physics 2,845, Chemistry 1,769, Biology 1,120, Engineering 7,129 (the other engineering fields, pooled) and Other sciences 1,482 (astronomy, geology, ...). Computer science returns `None`: it is all in `supergpqa_cs`.

## Why it exists

- **Graduate level, per area.** MMLU-Pro has one "computer science" subject; SuperGPQA has ten CS subfields, enough to see *where* the model is weak.
- **No question is paid for twice.** Running both slices asks each question once: `supergpqa_stem` leaves computer science to `supergpqa_cs`. A sampled STEM run would otherwise spend about 43 answers (100,000 tokens) on questions the CS slice already answered.
- **Cheap, fixed questions.** One letter per answer, no judge model, and a pinned revision: a new dataset upload cannot change the questions under a run being resumed.

## Mental model

- **Chance is 10%.** Most questions have ten options; a bar near 10% means guessing.
- **`-n` samples stratified by area** (`select_items`), so `-n 1000` of the STEM slice keeps its mix. The CS slice has 763 questions; any `-n` of 763 or more asks them all.
- **Cost:** with thinking off, Qwen3.6 writes about 2,300 tokens per answer (240 to 7,200). 763 CS questions take about 1.5 to 3 hours on one RTX 3090.
- **Two differences from the reference grader** (`eval/eval.py`): Ghuumi never matches an answer by its option text, and it falls back to the last capital letter. `answers found by:` shows how often each happened.
- `scripts/check_graders.py supergpqa` proves the grader against every answer key, in seven phrasings ([page](../../../../scripts/check_graders.py.md)).

## Public API

| Name | What it's for |
|---|---|
| `SuperGPQACS` | Registered as `supergpqa_cs` (alias `supergpqa-cs`). |
| `SuperGPQASTEM` | Registered as `supergpqa_stem` (alias `supergpqa-stem`). |
| `SuperGPQA` | The shared base: `load`, `grade`, and `area(row)` for a new slice. |
| `cs_area`, `stem_area` | The two slices' area functions. |
| `DATASET`, `REVISION`, `PROMPT`, `LETTERS`, `CS_FIELD`, `CORE_FIELDS` | The dataset, its pinned commit, the prompt, the option letters, the CS field's name and the sciences reported on their own. |

## Failure modes

- **First run downloads the dataset** (about 40 MB) from Hugging Face; later runs use the cache.
- **A changed grader or prompt is not a resumable run.** The fingerprint covers settings, not code: start a new run folder after editing either.

## Related files

- [`base.md`](base.md): `BenchItem`, `load_hf_rows`, `select_items`.
- [`extract.md`](extract.md): the letter extraction order.
- [`../../server/education.md`](../../server/education.md): how the results become the Education page's bars.
- [`../../../../configs/cs-foundations.yaml.md`](../../../../configs/cs-foundations.yaml.md): the curriculum phase that runs both slices.
