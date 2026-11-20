"""One turn of the loop: pull sources, refresh the index, build examples, train, gate, promote.

Safety rules, in the order they apply:

- Nothing runs unless `autonomy.enabled` is true.
- Examples are written by the *base* model, never by an adapter this loop
  produced, so errors cannot compound from one cycle to the next.
- A trained adapter is promoted (served by default) only if it scores at least
  the base model's pass rate on `autonomy.gate_suite`, minus `max_regression`.
  A failed gate leaves the adapter on disk and the old promotion untouched.
- Retrieval (the documentation index) is promoted at once: it changes what the
  model is shown, never its weights, and replacing it is one file.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ghuumi.agent.doc_index import build_index
from ghuumi.autonomy.examples import Generate, build_examples, write_datasets
from ghuumi.autonomy.sources import pull_sources
from ghuumi.autonomy.store import Store
from ghuumi.core.config import GhuumiConfig
from ghuumi.core.errors import ConfigError
from ghuumi.core.logging import get_logger
from ghuumi.core.types import GenerationParams, Message

logger = get_logger(__name__)

Trainer = Callable[[Path], Path]
Evaluator = Callable[[GhuumiConfig, str], Awaitable[float]]


@dataclass
class CycleReport:
    started: str
    documents_added: int = 0
    pull_skipped: dict[str, int] = field(default_factory=dict)
    pull_error: str | None = None
    index: str | None = None
    new_train_examples: int = 0
    new_eval_examples: int = 0
    trained: bool = False
    adapter: str | None = None
    base_score: float | None = None
    adapter_score: float | None = None
    promoted: bool = False
    note: str = ""


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def base_config(cfg: GhuumiConfig) -> GhuumiConfig:
    """`cfg` without any adapter, so generation and scoring use the untouched base."""
    copy = cfg.model_copy(deep=True)
    copy.backend.adapter_path = None
    return copy


async def _backend_generator(cfg: GhuumiConfig) -> tuple[Generate, Callable[[], Awaitable[None]]]:
    from ghuumi.core.registry import BACKENDS

    backend = BACKENDS.create(cfg.backend.name, cfg.backend)
    await backend.load()
    params = GenerationParams(temperature=0.3, max_tokens=700)

    async def generate(messages: list[Message]) -> str:
        return (await backend.generate(messages, params)).message.content

    return generate, backend.aclose


async def _eval_score(cfg: GhuumiConfig, suite: str) -> float:
    from ghuumi.eval.harness import run_eval

    return (await run_eval(cfg, suite=suite)).pass_rate


def train_config_for(
    cfg: GhuumiConfig, train_file: Path, eval_file: Path | None, out: Path
) -> Path:
    """Write the YAML `ghuumi train` will run, from the template or sane defaults."""
    auto = cfg.autonomy
    if auto.train_template:
        raw: dict[str, Any] = yaml.safe_load(Path(auto.train_template).read_text("utf-8")) or {}
    elif cfg.backend.name == "transformers":
        raw = {"task": "lora", "base_model": cfg.backend.model, "load_in_4bit": True}
    else:
        raise ConfigError(
            f"Cannot train backend '{cfg.backend.name}': only the transformers backend loads "
            "LoRA adapters.",
            hint="Set autonomy.train_template to a train YAML with a Hugging Face base_model, "
            "and serve with the transformers backend to use the result.",
        )
    raw["task"] = "lora"
    raw["output_dir"] = str(out / "run")
    raw.setdefault("data", {})
    raw["data"].update(train_file=str(train_file), eval_file=str(eval_file) if eval_file else None)
    path = out / "train.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def _default_trainer(config_path: Path) -> Path:
    from ghuumi.train.runner import run_training

    return run_training(config_path)


def refresh_index(store: Store) -> Path | None:
    """Rebuild the documentation index when documents were added since the last one."""
    state = store.state()
    count = sum(1 for _ in store.documents())
    if not count or count == state.indexed_documents:
        return None
    folder = store.home / "indexes"
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f"index-{_stamp()}.sqlite"
    build_index(store.docs, output, topic="autonomous sources", version=_stamp()[:8])
    previous = store.promoted().get("index")
    store.promote(index=str(output))
    state.indexed_documents = count
    store.save_state(state)
    if previous and Path(previous) != output:
        Path(previous).unlink(missing_ok=True)  # best effort: a running server may hold it
    return output


async def run_cycle(
    cfg: GhuumiConfig,
    *,
    pull: bool = True,
    build: bool = True,
    train: bool = True,
    web: Any = None,
    generator: Generate | None = None,
    trainer: Trainer | None = None,
    evaluator: Evaluator | None = None,
) -> CycleReport:
    auto = cfg.autonomy
    if not auto.enabled:
        raise ConfigError(
            "Autonomy is switched off.", hint="Set `autonomy.enabled: true` in your config."
        )
    store = Store(auto.home)
    report = CycleReport(started=_stamp())

    if pull and auto.topics:
        pulled = await asyncio.to_thread(
            pull_sources, auto, store, web=web, openalex_api_key=cfg.academic.openalex_api_key
        )
        report.documents_added = len(pulled.added)
        report.pull_skipped = pulled.skipped
        report.pull_error = pulled.error
    elif pull:
        report.note = "no autonomy.topics set; nothing pulled. "

    index = await asyncio.to_thread(refresh_index, store)
    report.index = str(index) if index else None

    close: Callable[[], Awaitable[None]] | None = None
    generate = generator
    if generate is None and build:
        pending = any(d.id not in store.state().processed_documents for d in store.documents())
        if pending:
            generate, close = await _backend_generator(base_config(cfg))
    try:
        if generate is not None and build:
            built = await build_examples(auto, store, generate)
            report.new_train_examples, report.new_eval_examples = built.train, built.eval
    finally:
        if close:
            await close()

    if not train:
        store.append_cycle(asdict(report))
        return report

    state = store.state()
    total_train = sum(1 for e in store.read_examples() if e.split == "train")
    fresh = total_train - state.trained_examples
    if fresh < auto.min_new_examples:
        report.note += f"{fresh} new training examples; waiting for {auto.min_new_examples}."
        store.append_cycle(asdict(report))
        return report

    stamp = _stamp()
    out = store.home / "runs" / stamp
    train_file, eval_file, _ = write_datasets(store, out / "data")
    config_path = train_config_for(cfg, train_file, eval_file, out)
    started = time.perf_counter()
    adapter = await asyncio.to_thread(trainer or _default_trainer, config_path)
    report.trained, report.adapter = True, str(adapter)
    logger.info("autonomy: trained in %.1f min", (time.perf_counter() - started) / 60)

    score = evaluator or _eval_score
    candidate = cfg.model_copy(deep=True)
    candidate.backend.adapter_path = str(adapter)
    report.base_score = await score(base_config(cfg), auto.gate_suite)
    report.adapter_score = await score(candidate, auto.gate_suite)

    state = store.state()
    state.trained_examples = total_train
    store.save_state(state)
    if report.adapter_score + 1e-9 >= report.base_score - auto.max_regression:
        store.promote(
            adapter=str(adapter),
            adapter_score=report.adapter_score,
            base_score=report.base_score,
            promoted_at=stamp,
        )
        report.promoted = True
    else:
        report.note += "adapter failed the gate and was not promoted."
    store.append_cycle(asdict(report))
    return report


async def run_loop(cfg: GhuumiConfig, *, train: bool = True, **kwargs: Any) -> None:
    """Repeat `run_cycle` every `autonomy.interval_s` until cancelled."""
    while True:
        try:
            report = await run_cycle(cfg, train=train, **kwargs)
            logger.info("autonomy cycle done: %s", report)
        except ConfigError:
            raise
        except Exception:  # noqa: BLE001 - one failed cycle must not end the loop
            logger.exception("autonomy cycle failed; trying again next interval")
        await asyncio.sleep(cfg.autonomy.interval_s)
