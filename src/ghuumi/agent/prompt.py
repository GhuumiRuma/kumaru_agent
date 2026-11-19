"""System-prompt assembly.

Why a module rather than a string constant
------------------------------------------
The system prompt is the highest-leverage, lowest-cost lever you have over
answer quality — it is the one part of model behaviour you can change in
milliseconds instead of hours of fine-tuning. Isolating it here means you can
A/B test prompts, version them, and inject runtime facts (today's date, enabled
tools) without touching the orchestration code.

The order of the assembled sections is deliberate. Models attend most reliably
to the beginning and the end of their context (the "lost in the middle" effect),
so stable identity goes first and volatile, task-specific context goes last —
closest to the user's question.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Sequence

from ghuumi.agent.doc_index import EXCERPTS_RULE
from ghuumi.agent.knowledge import EXPERTISE
from ghuumi.core.config import AgentConfig
from ghuumi.core.types import Message, Role

# Appended to every system prompt. These are behavioural guardrails that apply
# regardless of which persona the user configures.
QUALITY_DIRECTIVES = """\
Operating rules:
- Lead with the direct answer. Put reasoning after it, not before.
- Match answer length to question complexity. Do not pad.
- If you do not know, say "I don't know" and name what would settle it.
- Never fabricate citations, URLs, APIs, file paths, version numbers, or statistics.
- When a question is ambiguous in a way that changes the answer, ask one clarifying question.
- Use fenced code blocks with a language tag for all code."""

# Added when `ghuumi chat --dev` points the project folder at Ghuumi's own source.
DEVELOPER_MODE_RULES = """\
Developer mode: the project folder is your own source code (the ghuumi package).
- Read the code before changing it, and make small, focused changes.
- After each change run the tests with run_command: python -m pytest -q. Fix what you break.
- Every new source file needs a companion guide in docs/howto at the matching path; update
  the guide of any file whose behaviour you change.
- Do not weaken safety checks (approval, path limits, command allowlist) or the tests that guard them.
- Your edits take effect only after the user restarts Ghuumi; say so when you finish.
- Explain what you changed and why, and leave the work uncommitted for the user to review."""


def build_system_prompt(
    config: AgentConfig,
    *,
    tool_descriptions: Sequence[str] = (),
    extra_context: str | None = None,
    include_date: bool = True,
    excerpts: bool = False,
) -> str:
    """Compose the full system prompt from its parts.

    `excerpts` adds the rule for the documentation excerpts `ChatAgent` puts
    in the latest user message when `agent.knowledge_indexes` is set.
    """
    sections: list[str] = [config.system_prompt.strip(), QUALITY_DIRECTIVES]
    if config.developer_mode:
        sections.append(DEVELOPER_MODE_RULES)
    if config.expertise:
        sections.append(
            "Engineering guidance:\n"
            + "\n".join(EXPERTISE[name] for name in dict.fromkeys(config.expertise))
        )
    if excerpts:
        # Fixed text, so the prompt prefix is the same whatever is retrieved.
        sections.append(EXCERPTS_RULE)

    if include_date:
        today = _dt.datetime.now().strftime("%Y-%m-%d")
        # Models have a training cutoff and will otherwise confidently assume a
        # stale date. One line removes a whole class of wrong answers.
        sections.append(f"Today's date is {today}.")

    if tool_descriptions:
        listed = "\n".join(f"- {d}" for d in tool_descriptions)
        sections.append(f"You can call these tools:\n{listed}")

    if extra_context:
        # Reference packs and caller context land here — last, so they sit
        # nearest the question. Documentation excerpts go in the question itself.
        sections.append(f"Relevant context:\n{extra_context.strip()}")

    return "\n\n".join(s for s in sections if s)


def ensure_system_message(
    messages: Sequence[Message], system_prompt: str
) -> list[Message]:
    """Return `messages` with exactly one system message, at index 0.

    Normalizing here means every downstream component can assume the invariant,
    and a client that forgets to send a system message still gets correct
    behaviour instead of a bare, unguided model.
    """
    body = [m for m in messages if m.role != Role.SYSTEM]
    # Client system messages augment the configured prompt rather than replacing
    # it, so server-side guardrails always apply. Every nonblank one is kept, in
    # order: clients often send several, and dropping any loses instructions.
    client = [m.content.strip() for m in messages if m.role == Role.SYSTEM and m.content.strip()]
    merged = "\n\n".join([system_prompt, *client])

    return [Message.system(merged), *body]
