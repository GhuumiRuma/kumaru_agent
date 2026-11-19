"""Shared offline fixtures for exercising Ghuumi without model downloads."""

import shutil
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ghuumi.agent.chat import ChatAgent
from ghuumi.backends.echo import EchoBackend
from ghuumi.core.config import BackendConfig, GhuumiConfig
from ghuumi.core.types import GenerationParams
from ghuumi.server.app import create_app


@pytest.fixture
def echo_config() -> GhuumiConfig:
    """Return a small deterministic config that never touches GPU-backed code."""
    return GhuumiConfig(
        backend=BackendConfig(
            name="echo",
            model="echo",
            max_context_length=2048,
            extra={"delay_s": 0},
        ),
        generation=GenerationParams(max_tokens=32, temperature=0.0),
    )


@pytest.fixture
async def loaded_echo_backend(echo_config: GhuumiConfig) -> EchoBackend:
    backend = EchoBackend(echo_config.backend)
    await backend.load()
    try:
        yield backend
    finally:
        await backend.aclose()


@pytest.fixture
def chat_agent(loaded_echo_backend: EchoBackend, echo_config: GhuumiConfig) -> ChatAgent:
    return ChatAgent(loaded_echo_backend, echo_config)


@pytest.fixture
def test_client(echo_config: GhuumiConfig) -> Generator[TestClient, None, None]:
    app = create_app(echo_config)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def scratch_dir() -> Generator[Path, None, None]:
    """An empty directory under tests/.scratch, removed (recursively) afterwards."""
    path = Path(__file__).resolve().parent / ".scratch"
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir()
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
