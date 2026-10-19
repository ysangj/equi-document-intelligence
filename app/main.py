"""FastAPI application (`uvicorn app.main:app --port 8000`)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine

from app.api import include_routers
from app.api.ask import router as ask_router
from app.api.params import NoQuery
from app.config import ConfigError, Settings, get_settings, require_settings
from app.db import current_revision, get_engine, ping
from app.logging_setup import configure_logging

log = logging.getLogger("app.api")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    configure_logging(settings.secret_values())
    try:
        require_settings(settings)
    except ConfigError as e:
        log.error(str(e), extra={"event": "config_error", "missing": e.missing})
        raise
    log.info(
        "api starting",
        extra={"event": "startup", "demo_mode": settings.demo_mode, "ask_enabled": settings.ask_enabled},
    )
    yield


app = FastAPI(title="Fund Document Intelligence", version="0.1.0", lifespan=lifespan)
include_routers(app)  # T6 routers + the C2 error envelope
app.include_router(ask_router)  # T8: POST /api/ask


# ---- schemas -------------------------------------------------------------


class HealthResponse(BaseModel):
    status: Literal["ok", "error"]
    db: Literal["ok", "error"]
    alembic_revision: str | None


class Feature(BaseModel):
    enabled: bool
    reason: str | None = None


class Features(BaseModel):
    browse: Feature
    search: Feature
    review: Feature
    ask: Feature
    drive: Feature


class ConfigResponse(BaseModel):
    demo_mode: bool
    demo_reason: Literal["DEMO_MODE", "GOOGLE_CLIENT_ID_UNSET"] | None
    extraction_source: Literal["claude", "fixture"]  # same spelling as extractions.source
    claude_model: str
    sync_interval_seconds: int
    features: Features


# ---- routes --------------------------------------------------------------


@app.get("/api/health", response_model=HealthResponse, responses={503: {"model": HealthResponse}})
def health(engine: Engine = Depends(get_engine)):
    try:
        ping(engine)
        rev = current_revision(engine)
    except Exception as e:  # noqa: BLE001 - any DB failure means unhealthy
        log.warning("health db check failed", extra={"event": "health_db_error", "error_type": type(e).__name__})
        body = HealthResponse(status="error", db="error", alembic_revision=None)
        return JSONResponse(status_code=503, content=body.model_dump())
    return HealthResponse(status="ok", db="ok", alembic_revision=rev)


def build_config(s: Settings) -> ConfigResponse:
    ask = (
        Feature(enabled=True)
        if s.ask_enabled
        else Feature(enabled=False, reason="Ask is disabled because ANTHROPIC_API_KEY is not set.")
    )
    drive = (
        Feature(enabled=False, reason="Demo mode: documents are read from ./sample_docs instead of Google Drive.")
        if s.demo_mode
        else Feature(enabled=True)
    )
    on = Feature(enabled=True)
    return ConfigResponse(
        demo_mode=s.demo_mode,
        demo_reason=s.demo_reason,  # type: ignore[arg-type]
        extraction_source="fixture" if s.use_fixtures else "claude",
        claude_model=s.claude_model,
        sync_interval_seconds=s.sync_interval_seconds,
        features=Features(browse=on, search=on, review=on, ask=ask, drive=drive),
    )


@app.get("/api/config", response_model=ConfigResponse)
def config(_: NoQuery, settings: Settings = Depends(get_settings)) -> ConfigResponse:
    return build_config(settings)
