"""HTTP routers (T6, T7 auth/drive). `app.main` includes them; `errors.install_error_handlers` gives
every response the C2 error envelope."""

from __future__ import annotations

from fastapi import FastAPI

from app.api import auth, data, drive, ingestion, review, search, sources
from app.api.errors import install_error_handlers

ROUTERS = (auth.router, drive.router, ingestion.router, data.router, sources.router, search.router, review.router)


def include_routers(app: FastAPI) -> None:
    install_error_handlers(app)
    for r in ROUTERS:
        app.include_router(r)


__all__ = ["include_routers", "ROUTERS"]
