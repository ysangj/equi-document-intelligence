"""Shared helpers for the T6 API tests (not a test module).

- `api_client(settings)`: a TestClient on the real app, with `get_settings` overridden
  (demo mode by default) and every request hitting the test database (conftest).
- `ingest_into(engine, tmp)`: empties the ingestion tables and runs the synthetic
  sample documents through the real T4/T5 pipeline (the planted conflict, aliases,
  YTD and roll-forward cases included).
- `validate(model, body)`: the response must validate against its `app/schemas` model.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from sqlalchemy import Engine, text

from app.config import Settings, get_settings
from app.ingest import postprocess
from app.main import app
from tests.pipeline_helpers import FIXED_TODAY, ingest_samples

TRUNCATE_ALL = ("TRUNCATE documents, extractions, sync_state, funds, accounts, oauth_credentials, review_actions "
                "RESTART IDENTITY CASCADE")


def demo_settings(**kw) -> Settings:
    return Settings(_env_file=None, DEMO_MODE="true", **kw)


def drive_settings(**kw) -> Settings:
    return Settings(_env_file=None, google_client_id="test-client-id", **kw)


@contextlib.contextmanager
def api_client(settings: Settings | None = None) -> Iterator[TestClient]:
    s = settings or demo_settings()
    app.dependency_overrides[get_settings] = lambda: s
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_settings, None)


def truncate(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(TRUNCATE_ALL))


def ingest_into(engine: Engine, tmp: Path) -> dict[str, str]:
    truncate(engine)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(postprocess, "_today", lambda: FIXED_TODAY)
        return ingest_samples(engine, tmp)


def validate(model: Any, body: Any) -> Any:
    adapter = model if isinstance(model, TypeAdapter) else TypeAdapter(model)
    return adapter.validate_python(body)


def q1(engine: Engine, sql: str, **params) -> Any:
    """First column of the first row; commits (so INSERT ... RETURNING works)."""
    with engine.begin() as c:
        return c.execute(text(sql), params).scalar()


def rows(engine: Engine, sql: str, **params) -> list[dict]:
    with engine.connect() as c:
        return [dict(r) for r in c.execute(text(sql), params).mappings()]


def exec_sql(engine: Engine, sql: str, **params) -> None:
    with engine.begin() as c:
        c.execute(text(sql), params)


def return_id(engine: Engine, fund_like: str, period_end: str, doc_like: str, return_type: str = "MTD") -> int:
    rid = q1(engine, """
        SELECT r.id FROM returns r JOIN funds f ON f.id = r.fund_id JOIN documents d ON d.id = r.document_id
        WHERE f.canonical_name ILIKE :f AND r.period_end = CAST(:p AS date) AND d.name ILIKE :d
          AND r.return_type = :rt AND r.net_gross = 'net'""",
             f=fund_like, p=period_end, d=doc_like, rt=return_type)
    assert rid is not None, (fund_like, period_end, doc_like)
    return rid


def assert_error(resp, status: int, code: str, loc: str | None = None) -> dict:
    assert resp.status_code == status, resp.text
    body = resp.json()
    from app.schemas.common import ErrorResponse

    ErrorResponse.model_validate(body)
    assert body["error"]["code"] == code, body
    if loc is not None:
        assert loc in [d["loc"] for d in body["error"]["details"] or []], body
    return body
