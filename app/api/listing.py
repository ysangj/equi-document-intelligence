"""Server-side lists (C3 pagination + sort, C4 filters, C5 CSV).

Each list endpoint builds a `Listing`: a selectable whose columns carry the API names,
the sortable columns, the primary key (the final tie-breaker) and a row -> item
function. `run_page` returns the `Page` envelope; `csv_response` streams every
matching row as RFC 4180 CSV with the same filters and sort.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import ColumnElement, Engine, Select, func, select
from sqlalchemy.orm import Session

from app.schemas.common import ListQuery

CSV_BATCH = 1000


# ---- value conversion ---------------------------------------------------------------


def utc(ts: datetime | None) -> datetime | None:
    """Timestamps go out as ISO 8601 UTC with a Z suffix and whole seconds (C1)."""
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).replace(microsecond=0)


def num(x: Any) -> float | None:
    """Decimal/int -> float (JSON number). Trailing zeros of numeric columns vanish."""
    if x is None:
        return None
    return float(x)


def clean_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            v = float(v)
        elif isinstance(v, datetime):
            v = utc(v)
        out[k] = v
    return out


def now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


# ---- listing ----------------------------------------------------------------------------


@dataclass
class Listing:
    base: Select  # SELECT with the API column names (labels), filters applied
    sortable: dict[str, ColumnElement]  # API sort name -> SQL expression
    pk: ColumnElement
    to_item: Callable[[dict[str, Any]], dict[str, Any]]
    item_model: type[BaseModel]
    name: str  # CSV file name prefix, e.g. "returns"
    conditions: list[ColumnElement] = field(default_factory=list)

    def where(self, *conds: ColumnElement) -> Listing:
        self.conditions.extend(conds)
        return self

    def _filtered(self) -> Select:
        stmt = self.base
        if self.conditions:
            stmt = stmt.where(*self.conditions)
        return stmt

    def order_by(self, keys: Sequence[tuple[str, bool]]) -> list[ColumnElement]:
        """Requested keys, NULLs last in both directions, then the primary key ascending."""
        out = []
        for name, desc in keys:
            col = self.sortable[name]
            out.append((col.desc() if desc else col.asc()).nulls_last())
        out.append(self.pk.asc())
        return out

    def run_page(self, session: Session, q: ListQuery) -> dict[str, Any]:
        stmt = self._filtered()
        total = session.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()
        rows = session.execute(
            stmt.order_by(*self.order_by(q.sort_keys())).limit(q.page_size).offset((q.page - 1) * q.page_size)
        ).mappings().all()
        return {
            "items": [self.to_item(clean_row(dict(r))) for r in rows],
            "total": total,
            "page": q.page,
            "page_size": q.page_size,
            "sort": q.sort,
        }

    def iter_all(self, engine: Engine, q: ListQuery) -> Iterator[dict[str, Any]]:
        """Every matching row in sort order, streamed from a server-side cursor on its
        own connection (the request session may be closed while the body streams)."""
        stmt = self._filtered().order_by(*self.order_by(q.sort_keys()))
        with engine.connect() as conn:
            result = conn.execution_options(stream_results=True, yield_per=CSV_BATCH).execute(stmt)
            for row in result.mappings():
                yield self.to_item(clean_row(dict(row)))


def any_of(col: ColumnElement, values: Sequence[Any]) -> ColumnElement | None:
    """Repeated query key: OR within the key (C4). None when the key is absent."""
    if not values:
        return None
    return col.in_(list(dict.fromkeys(values)))


def conditions(*conds: ColumnElement | None) -> list[ColumnElement]:
    return [c for c in conds if c is not None]


# ---- CSV (C5) ----------------------------------------------------------------------------


def csv_cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple)):
        return ";".join(csv_cell(x) for x in v)
    if isinstance(v, datetime):
        return utc(v).isoformat().replace("+00:00", "Z")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, Decimal):
        return format(v, "f")
    return str(v)


def csv_lines(columns: Sequence[str], items: Iterable[dict[str, Any]]) -> Iterator[str]:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(columns)
    n = 0
    for item in items:
        writer.writerow([csv_cell(item.get(c)) for c in columns])
        n += 1
        if n % 200 == 0:
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()
    yield buf.getvalue()


def csv_response(listing: Listing, engine: Engine, q: ListQuery) -> StreamingResponse:
    columns = list(listing.item_model.model_fields)
    filename = f"{listing.name}_{now_utc().date().isoformat()}.csv"
    return StreamingResponse(
        csv_lines(columns, listing.iter_all(engine, q)),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
