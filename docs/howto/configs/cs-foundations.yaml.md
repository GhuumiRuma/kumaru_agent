# `configs\curriculum\cs-foundations.yaml`

> Phase 1 of Kumaru's curriculum: computer science fundamentals to graduate level, measured and supplied with references before any programming language.

**Read this when:** you run or repeat phase 1, or copy it to start the next phase.

---

## What it does

```powershell
kumaru curriculum run configs\curriculum\cs-foundations.yaml -c qwen36
```

- **Measures** with `supergpqa_cs` (all 763 graduate CS questions, 10 areas) and `supergpqa_stem` (1,000 of the other science and engineering questions, 6 areas), thinking off. Results go to `<first education.runs_dirs>\cs-foundations-qwen36-nothink` and show on the Education page.
- **Collects** open-access textbooks, surveys and lecture notes from OpenAlex for the 10 CS areas, named like the benchmark's areas so a weak bar points at its references. First run: 30 searches of 20 works each.

## Why these settings

- **Thinking off:** about 2,300 tokens per answer; thinking on costs several times that for a few points. Measure the cheap setting first.
- **All of CS, a sample of the rest:** CS is the focus, so every CS area gets all its questions; the other sciences need only a broad reading.
- **One fixed run folder:** a repeat resumes; finished work costs nothing.

## After it runs

1. Open the Education page (`/education`) to see each area's bar.
2. Review the collected works on the Academic page (`/academic`, with `academic.enabled: true`). Only reviewed works should become references.
3. Start the next phase by copying this file: a new `id` (equal to the file name), its benchmarks (`multipl_e` for languages) and its topics.

## Related files

- [`../src/kumaru/train/curriculum.md`](../src/kumaru/train/curriculum.md): the command and the file format.
- [`../src/kumaru/eval/benchmarks/supergpqa.md`](../src/kumaru/eval/benchmarks/supergpqa.md): the benchmark.
