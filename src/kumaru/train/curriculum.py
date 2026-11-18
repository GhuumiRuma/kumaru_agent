"""Curriculum phases: what Kumaru studies next, as one file you can re-run.

A phase file (``configs/curriculum/*.yaml``) lists the topics to collect
references for and the benchmarks that measure them. ``kumaru curriculum run
PHASE -c PROFILE`` does both, and repeating it only does what is missing:

* topics are added (or updated) in the academic catalogue;
* each topic is searched once per query, remembered in
  ``<catalogue>.<phase>.searches.json`` beside the catalogue's database;
* the benchmarks run in one fixed folder per setup, so a repeat resumes the
  run and never pays for the same answer twice.

A phase changes no weights and approves nothing: every work it finds stays
unreviewed until a person reviews it on the Academic page.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from kumaru.academic.catalog import Catalog
from kumaru.academic.models import Topic
from kumaru.core.errors import ConfigError, RequestError

# `discover(catalog, topic_id, query=...)` with its provider settings bound.
Search = Callable[..., dict[str, Any]]


class Measure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    benchmarks: list[str] = Field(min_length=1)
    limit: int | None = Field(default=100, ge=1)  # questions per benchmark; null = all
    think: bool | None = None  # null = the profile's default


class Collect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topics: list[Topic] = Field(min_length=1)
    queries: list[str] = Field(default_factory=lambda: [""], min_length=1)


class Phase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$", max_length=60)
    title: str = Field(min_length=1)
    measure: Measure | None = None
    collect: Collect | None = None


def load_phase(path: str | Path) -> Phase:
    path = Path(path)
    try:
        return Phase.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError) as exc:
        raise ConfigError(f"Cannot read curriculum phase {path}: {exc}") from exc


def searches_file(catalog: Catalog, phase: Phase) -> Path:
    return catalog.path.with_name(f"{catalog.path.stem}.{phase.id}.searches.json")


def collect(phase: Phase, catalog: Catalog, search: Search, *, refresh: bool = False) -> dict[str, Any]:
    """Add the phase's topics, then run each (topic, query) search not done yet.

    Area roots are not searched: a whole field's name is too broad. A topic
    with no sources at all (a new catalogue, say) is searched again. The first
    failed search (an outage, a spent daily budget) stops collection; the
    searches left over run next time.
    """
    searched: list[dict[str, Any]] = []
    skipped, error = 0, None
    if phase.collect is None:
        return {"searched": searched, "skipped": skipped, "error": error}
    for topic in phase.collect.topics:
        catalog.put_topic(topic)
    path = searches_file(catalog, phase)
    done: set[str] = set()
    if not refresh and path.exists():
        try:
            done = set(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            done = set()  # unreadable: searching again only costs requests
    try:
        for topic in (t for t in phase.collect.topics if t.parent_id is not None):
            stocked = bool(catalog.list_sources(topic_id=topic.id, containers=False, limit=1))
            for query in phase.collect.queries:
                key = f"{topic.id}|{query}"
                if key in done and stocked:
                    skipped += 1
                    continue
                try:
                    found = search(catalog, topic.id, query=query)
                except RequestError as exc:
                    error = f"{topic.id} {query!r}: {exc}"
                    break
                done.add(key)
                searched.append({"topic": topic.id, "query": query, "found": len(found.get("source_ids", []))})
            if error:
                break
    finally:
        path.write_text(json.dumps(sorted(done), indent=1), encoding="utf-8")
    return {"searched": searched, "skipped": skipped, "error": error}
