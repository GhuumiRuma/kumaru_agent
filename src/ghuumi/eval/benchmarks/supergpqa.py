"""SuperGPQA — graduate-level multiple choice across 285 disciplines, per area.

26,529 questions at graduate level, organized from 13 disciplines through 72
fields down to 285 subfields; most have ten options, so random guessing scores
about 10%. Ghuumi runs two slices of it, so a score says *which area* a model
knows:

* ``supergpqa_cs``: the 763 questions of the field "Computer Science and
  Technology", grouped by its 10 subfields (Data Structures, Operating
  Systems, Computer Networks, Databases, Formal Languages, ...).
* ``supergpqa_stem``: the rest of the Science and Engineering disciplines
  (16,967 questions) in six areas: Mathematics, Physics, Chemistry, Biology,
  Engineering (the other 29 engineering fields) and Other sciences
  (astronomy, geology, ...). Sample it with ``-n``. It leaves computer science
  to ``supergpqa_cs``, so running both never asks a question twice.

Protocol: the reference zero-shot prompt and option layout (``config/prompt/
zero-shot.yaml`` and ``infer/data_loader.py`` in the SuperGPQA repository),
graded on the letter of the final "Answer: X" line with `extract_choice`. Two
differences from the reference grader (``eval/eval.py``): Ghuumi never matches
an answer by its option text, and falls back to the last standalone capital
letter. Both are visible in ``answers found by:``.

Sampling is stratified by area (see `select_items`), so ``-n`` keeps the mix.

Source: M-A-P Team, 2025, "SuperGPQA: Scaling LLM Evaluation across 285
Graduate Disciplines"; dataset ``m-a-p/SuperGPQA`` (ODC-BY), pinned to one
revision.
"""

from __future__ import annotations

from typing import Any, ClassVar

from ghuumi.eval.benchmarks.base import BenchItem, Benchmark, Grade, load_hf_rows
from ghuumi.eval.benchmarks.extract import extract_choice

DATASET = "m-a-p/SuperGPQA"
# The dataset's only commit with its current 26,529 questions; a new upload
# cannot change the questions under an existing run.
REVISION = "4430d4458112c7d4497fdcf94d7cc223313d6acf"
LETTERS = "ABCDEFGHIJ"
CS_FIELD = "Computer Science and Technology"
# The STEM areas reported on their own; the rest of Science and Engineering
# is pooled, so no area is a handful of questions.
CORE_FIELDS = ("Mathematics", "Physics", "Chemistry", "Biology")

PROMPT = (
    "Answer the following multiple choice question. There is only one correct answer. "
    "The last line of your response should be in the format 'Answer: $LETTER' (without quotes), "
    "where LETTER is one of A, B, C, D, E, F, G, H, I, or J.\n\n{question}\n{options}"
)


def stem_area(row: dict[str, Any]) -> str | None:
    """The area a question counts toward in ``supergpqa_stem``; None outside STEM."""
    if row["field"] == CS_FIELD:  # all of it is in supergpqa_cs
        return None
    if row["field"] in CORE_FIELDS:
        return str(row["field"])
    if row["discipline"] == "Engineering":
        return "Engineering"
    if row["discipline"] == "Science":
        return "Other sciences"
    return None


def cs_area(row: dict[str, Any]) -> str | None:
    """The subfield of a computer science question; None for every other field."""
    return str(row["subfield"]) if row["field"] == CS_FIELD else None


class SuperGPQA(Benchmark):
    """Shared loading and grading; a slice chooses its questions and their areas."""

    name = "supergpqa"
    description: ClassVar[str] = "Graduate-level multiple choice."
    source: ClassVar[str] = "m-a-p/SuperGPQA - M-A-P Team 2025"

    @staticmethod
    def area(row: dict[str, Any]) -> str | None:
        raise NotImplementedError

    def load(self) -> list[BenchItem]:
        items = []
        for row in load_hf_rows(DATASET, None, "train", revision=REVISION):
            area = self.area(row)
            if area is None:
                continue
            options = [str(option) for option in row["options"]]
            letters = LETTERS[: len(options)]
            listing = "\n".join(f"{letter}) {text}" for letter, text in zip(letters, options, strict=True))
            items.append(
                BenchItem(
                    id=f"{self.name}/{row['uuid']}",
                    prompt=PROMPT.format(question=row["question"], options=listing),
                    answer=str(row["answer_letter"]),
                    group=area,
                    meta={"letters": letters},
                )
            )
        return items

    def grade(self, item: BenchItem, output: str) -> Grade:
        letter, method = extract_choice(output, item.meta.get("letters", LETTERS))
        return Grade(correct=letter == item.answer, extracted=letter, detail={"method": method})


class SuperGPQACS(SuperGPQA):
    name = "supergpqa_cs"
    description: ClassVar[str] = "Graduate computer science, 10 areas: data structures, OS, networks, theory..."
    source: ClassVar[str] = "m-a-p/SuperGPQA, field Computer Science and Technology (763) - M-A-P Team 2025"
    area = staticmethod(cs_area)


class SuperGPQASTEM(SuperGPQA):
    name = "supergpqa_stem"
    description: ClassVar[str] = "Graduate STEM besides CS, 6 areas: maths, physics, chemistry, biology..."
    source: ClassVar[str] = "m-a-p/SuperGPQA, Science and Engineering except CS (16,967) - M-A-P Team 2025"
    area = staticmethod(stem_area)
