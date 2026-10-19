"""Query model for endpoints that take no parameters, so an unknown param is a 422
like everywhere else (C1)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Query

from app.schemas.common import ApiModel


class NoParams(ApiModel):
    pass


NoQuery = Annotated[NoParams, Query()]
