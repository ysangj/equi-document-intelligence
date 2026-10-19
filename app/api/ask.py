"""`POST /api/ask` (FR-11): Claude tool-use answer with citations.

- 503 `ask_disabled` when `ANTHROPIC_API_KEY` is not set (same message as
  `/api/config` `features.ask.reason`).
- 502 `upstream_error` when the Anthropic API fails after the SDK's bounded retries
  (`ASK_MAX_RETRIES` retries of 429/5xx/timeouts/connection errors, each request
  capped at `ASK_REQUEST_TIMEOUT_SECONDS`).
- Synchronous (`def`): FastAPI runs it in its thread pool.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import Engine

from app.api.errors import ApiError
from app.ask.loop import AskUpstreamError, DbToolExecutor, run_ask
from app.config import Settings, get_settings
from app.db import engine_for, get_engine
from app.schemas import AskRequest, AskResponse
from app.schemas.common import ErrorResponse

log = logging.getLogger("app.api.ask")

router = APIRouter(prefix="/api", tags=["ask"])

ASK_DISABLED_MESSAGE = "Ask is disabled because ANTHROPIC_API_KEY is not set."
ASK_REQUEST_TIMEOUT_SECONDS = 30.0
ASK_MAX_RETRIES = 2

ClientFactory = Callable[[str], Any]


def default_client_factory(api_key: str) -> Any:
    import anthropic

    return anthropic.Anthropic(api_key=api_key, timeout=ASK_REQUEST_TIMEOUT_SECONDS, max_retries=ASK_MAX_RETRIES)


def get_client_factory() -> ClientFactory:
    """Dependency; tests override it with a scripted fake client."""
    return default_client_factory


@router.post(
    "/ask",
    response_model=AskResponse,
    responses={422: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def ask(body: AskRequest, settings: Settings = Depends(get_settings), engine: Engine = Depends(get_engine),
        client_factory: ClientFactory = Depends(get_client_factory)) -> dict[str, Any]:
    if not settings.ask_enabled:
        raise ApiError(503, "ask_disabled", ASK_DISABLED_MESSAGE)
    client = client_factory(settings.anthropic_api_key.get_secret_value())  # type: ignore[union-attr]
    executor = DbToolExecutor(engine, engine_for(settings.database_readonly_url))
    try:
        return run_ask(body.question, body.history, client=client, executor=executor, model=settings.claude_model,
                       max_steps=settings.ask_max_steps)
    except AskUpstreamError as e:
        log.warning("ask upstream error", extra={"event": "ask_upstream_error", "error": str(e)})
        raise ApiError(502, "upstream_error", str(e)) from None
