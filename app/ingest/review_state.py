"""Inline review state on value rows (T2 design): `status`, `review_reasons`,
`review_detail` kept consistent with the CHECK invariants:

    status = 'ok'       <=> no reasons
    status = 'conflict' <=> 'conflict' in reasons
    otherwise             'needs_review'

`review_detail` is `{<reason>: {...}}` in the shapes of
`app/schemas/review.py::REVIEW_DETAIL_MODELS`. Reasons are kept in the canonical
order of `app.models.REVIEW_REASONS`, so equal states compare equal.
"""

from __future__ import annotations

from typing import Any

from app.models import REVIEW_REASONS

_ORDER = {r: i for i, r in enumerate(REVIEW_REASONS)}


def status_for(reasons: list[str] | tuple[str, ...]) -> str:
    if not reasons:
        return "ok"
    return "conflict" if "conflict" in reasons else "needs_review"


def set_flags(row: Any, flags: dict[str, dict]) -> None:
    """Replace the row's whole review state with `flags` ({reason: detail})."""
    reasons = sorted(flags, key=_ORDER.__getitem__)
    row.review_reasons = reasons
    row.review_detail = {r: flags[r] for r in reasons} or None
    row.status = status_for(reasons)


def get_flags(row: Any) -> dict[str, dict]:
    detail = row.review_detail or {}
    return {r: detail.get(r, {}) for r in (row.review_reasons or [])}


def add_flag(row: Any, reason: str, detail: dict) -> bool:
    """Add or replace one reason. Returns True if the row's state changed."""
    flags = get_flags(row)
    if flags.get(reason) == detail and reason in (row.review_reasons or []):
        return False
    flags[reason] = detail
    set_flags(row, flags)
    return True


def drop_flag(row: Any, reason: str) -> bool:
    flags = get_flags(row)
    if reason not in flags:
        return False
    del flags[reason]
    set_flags(row, flags)
    return True


def clear_flags(row: Any) -> None:
    set_flags(row, {})
