"""Open review items (FR-13), built on the internal `review_queue` view (T2).

- A flagged value row is a `value` item.
- Rows carrying `conflict` that point at each other (`review_detail.conflict.
  conflicting_ids`) form ONE `conflict` item (T3 decision 4). Its id is the
  lowest-id member's; POST accepts any member's id. A conflict row whose partners
  are no longer visible is shown as a plain `value` item.
- An unconfirmed fuzzy alias is an `alias` item.

`open_items` is cheap (flagged rows only) and is the single source for
`GET /api/review`, its `counts`, `/api/progress.review_open` and
`/api/documents.review_count`, so those numbers always agree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import REVIEW_REASONS

_ORDER = {r: i for i, r in enumerate(REVIEW_REASONS)}
ALIAS_TABLES = ("fund_aliases", "account_aliases")

_QUEUE_SQL = text("""
SELECT item_key, target_table, target_id, status, review_reasons, review_detail, document_id, document_name,
       fund_id, account_id, created_at
FROM review_queue
""")


@dataclass
class OpenItem:
    id: str
    kind: str  # value | conflict | alias
    target_table: str
    target_id: int
    status: str
    reasons: list[str]
    detail: dict[str, Any]
    document_id: int | None
    document_name: str | None
    created_at: datetime
    members: list[int] = field(default_factory=list)  # conflict: every row id of the group (sorted)
    document_ids: set[int] = field(default_factory=set)  # every member's document

    def covers(self, table: str, target_id: int) -> bool:
        return table == self.target_table and (target_id == self.target_id or target_id in self.members)


def _sorted_reasons(reasons) -> list[str]:
    return sorted(set(reasons or []), key=lambda r: _ORDER.get(r, 99))


def open_items(session: Session) -> list[OpenItem]:
    rows = [dict(r) for r in session.execute(_QUEUE_SQL).mappings()]
    items: list[OpenItem] = []
    conflict_rows: dict[str, dict[int, dict]] = {}
    for r in rows:
        if r["target_table"] in ALIAS_TABLES:
            items.append(_item("alias", r))
        elif "conflict" in (r["review_reasons"] or []):
            conflict_rows.setdefault(r["target_table"], {})[r["target_id"]] = r
        else:
            items.append(_item("value", r))

    for table, by_id in conflict_rows.items():
        parent = {i: i for i in by_id}

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i, r in by_id.items():
            partners = ((r["review_detail"] or {}).get("conflict") or {}).get("conflicting_ids") or []
            for p in partners:
                if p in by_id:
                    a, b = find(i), find(p)
                    if a != b:
                        parent[max(a, b)] = min(a, b)
        groups: dict[int, list[int]] = {}
        for i in by_id:
            groups.setdefault(find(i), []).append(i)
        for members in groups.values():
            members.sort()
            rep = by_id[members[0]]
            if len(members) < 2:
                items.append(_item("value", rep))
                continue
            item = _item("conflict", rep)
            item.members = members
            item.reasons = _sorted_reasons(x for m in members for x in by_id[m]["review_reasons"])
            item.status = "conflict"
            item.document_ids = {by_id[m]["document_id"] for m in members if by_id[m]["document_id"] is not None}
            items.append(item)
    return items


def _item(kind: str, r: dict) -> OpenItem:
    detail = r["review_detail"] or {}
    if kind == "alias":
        # The view builds the alias row's detail flat ({alias, match_score, fund_id});
        # the API keys every detail by reason (REVIEW_DETAIL_MODELS).
        detail = {"alias_match": {k: v for k, v in detail.items() if v is not None or k == "match_score"}}
    return OpenItem(
        id=r["item_key"], kind=kind, target_table=r["target_table"], target_id=int(r["target_id"]),
        status=r["status"], reasons=_sorted_reasons(r["review_reasons"]), detail=detail,
        document_id=r["document_id"], document_name=r["document_name"], created_at=r["created_at"],
        members=[int(r["target_id"])], document_ids={r["document_id"]} if r["document_id"] is not None else set(),
    )


def find_item(items: list[OpenItem], table: str, target_id: int) -> OpenItem | None:
    for it in items:
        if it.covers(table, target_id):
            return it
    return None


def counts(items: list[OpenItem]) -> dict[str, dict[str, int]]:
    by_kind = {"value": 0, "conflict": 0, "alias": 0}
    by_reason: dict[str, int] = {}
    for it in items:
        by_kind[it.kind] += 1
        for r in it.reasons:
            by_reason[r] = by_reason.get(r, 0) + 1
    return {"by_kind": by_kind,
            "by_reason": {r: by_reason[r] for r in sorted(by_reason, key=lambda r: _ORDER.get(r, 99))}}


def review_count_by_document(items: list[OpenItem]) -> dict[int, int]:
    """Open items per document. A conflict group counts once for each member document."""
    out: dict[int, int] = {}
    for it in items:
        for d in it.document_ids:
            out[d] = out.get(d, 0) + 1
    return out
