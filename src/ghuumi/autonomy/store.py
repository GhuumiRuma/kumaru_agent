"""On-disk state of the self-improvement loop: corpus, examples, history, promotion."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ghuumi.core.errors import ConfigError

PROMOTED = "promoted.json"


class Document(BaseModel):
    """One collected open-access text, with the provenance needed to cite or remove it."""

    id: str
    topic: str
    title: str
    url: str
    doi: str | None = None
    license: str
    sha256: str
    fetched_at: str
    text: str


class Example(BaseModel):
    """One verified training example, tied to the document it came from."""

    document_id: str
    split: str
    messages: list[dict[str, str]]


class State(BaseModel):
    seen_urls: list[str] = Field(default_factory=list)
    seen_hashes: list[str] = Field(default_factory=list)
    processed_documents: list[str] = Field(default_factory=list)
    indexed_documents: int = 0
    trained_examples: int = 0
    cycles: list[dict[str, Any]] = Field(default_factory=list)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:60] or "topic"


class Store:
    """Files under `autonomy.home`. One process at a time: there is no locking."""

    def __init__(self, home: str | Path) -> None:
        self.home = Path(home).expanduser()
        self.corpus = self.home / "corpus.jsonl"
        self.examples = self.home / "examples.jsonl"
        self.docs = self.home / "docs"
        self.state_file = self.home / "state.json"

    # -- state ----------------------------------------------------------------

    def state(self) -> State:
        try:
            return State.model_validate_json(self.state_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return State()
        except (OSError, ValueError) as exc:
            raise ConfigError(f"Cannot read autonomy state {self.state_file}: {exc}") from exc

    def save_state(self, state: State) -> None:
        _atomic_write(self.state_file, state.model_dump_json(indent=2) + "\n")

    def append_cycle(self, record: dict[str, Any], keep: int = 50) -> None:
        state = self.state()
        state.cycles = [*state.cycles, record][-keep:]
        self.save_state(state)

    # -- corpus ---------------------------------------------------------------

    def documents(self) -> Iterator[Document]:
        yield from self._read(self.corpus, Document)

    def add_document(self, doc: Document) -> bool:
        """Store `doc` unless its URL or text was collected before."""
        state = self.state()
        if doc.url in state.seen_urls or doc.sha256 in state.seen_hashes:
            return False
        self._append(self.corpus, [doc.model_dump_json()])
        folder = self.docs / slug(doc.topic)
        folder.mkdir(parents=True, exist_ok=True)
        header = f"{doc.title}\n{doc.url}\nLicense: {doc.license}\n\n"
        (folder / f"{doc.id}.txt").write_text(header + doc.text, encoding="utf-8")
        state.seen_urls.append(doc.url)
        state.seen_hashes.append(doc.sha256)
        self.save_state(state)
        return True

    def remember_url(self, url: str) -> None:
        """Skip a URL that failed permanently, so each cycle does not retry it."""
        state = self.state()
        if url not in state.seen_urls:
            state.seen_urls.append(url)
            self.save_state(state)

    # -- examples -------------------------------------------------------------

    def read_examples(self) -> list[Example]:
        return list(self._read(self.examples, Example))

    def add_examples(self, examples: Iterable[Example]) -> int:
        lines = [example.model_dump_json() for example in examples]
        self._append(self.examples, lines)
        return len(lines)

    # -- promotion ------------------------------------------------------------

    def promoted(self) -> dict[str, Any]:
        try:
            data = json.loads((self.home / PROMOTED).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def promote(self, **fields: Any) -> None:
        _atomic_write(
            self.home / PROMOTED, json.dumps({**self.promoted(), **fields}, indent=2) + "\n"
        )

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _append(path: Path, lines: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as out:
            for line in lines:
                out.write(line + "\n")

    @staticmethod
    def _read(path: Path, model: type[Any]) -> Iterator[Any]:
        if not path.is_file():
            return
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield model.model_validate_json(line)


def apply_promoted(config: Any) -> Any:
    """`config` with the promoted adapter and documentation index attached.

    Does nothing unless `autonomy.enabled` and `autonomy.use_promoted` are set.
    The adapter is attached only when the backend can load one, and never over
    an `adapter_path` the user chose.
    """
    if not (config.autonomy.enabled and config.autonomy.use_promoted):
        return config
    promoted = Store(config.autonomy.home).promoted()
    index = promoted.get("index")
    if index and Path(index).is_file() and index not in config.agent.knowledge_indexes:
        config.agent.knowledge_indexes = [*config.agent.knowledge_indexes, index]
    adapter = promoted.get("adapter")
    if (
        adapter
        and Path(adapter).is_dir()
        and not config.backend.adapter_path
        and config.backend.name == "transformers"
    ):
        config.backend.adapter_path = adapter
    return config
