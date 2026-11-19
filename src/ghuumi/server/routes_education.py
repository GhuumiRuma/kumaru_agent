"""``GET /v1/education``: how educated the model is, per area, for the Education page.

Read-only and unauthenticated like ``/v1/info``: it returns accuracies and
counts, never the questions or answers. See `ghuumi.server.education`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ghuumi.server.education import read_education

router = APIRouter()


@router.get("/v1/education")
def education(request: Request) -> dict[str, Any]:
    """The newest `ghuumi bench` measurement of each benchmark and setup, per area.

    A plain function, not a coroutine: FastAPI runs it on a worker thread, so
    reading run folders never stalls a reply that is streaming.
    """
    config = request.app.state.ghuumi.config
    return read_education(config.education.runs_dirs).model_dump(mode="json")
