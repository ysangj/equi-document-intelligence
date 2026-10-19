"""Canonical names for funds and accounts (FR-6, BUG-Q3-01).

The rule (deterministic; the same documents give the same name in any processing order):

    The canonical name of a fund (account) is, among the entity's current canonical
    name and its aliases, the name that occurs in the most distinct VISIBLE documents
    (latest version, not removed). Ties go to the name that occurs in more `factsheet`
    documents (accounts: `account_statement`), then to the longest name, then
    alphabetically (case-insensitive, then exact code points).

- "Occurs in a document" = the document has a value row (returns, fund_facts,
  statements) resolved through that alias (`fund_alias_id` / `account_alias_id`).
  A row without an alias counts for the current canonical name.
- Names are compared by `names.alias_key` (case/whitespace/dash-insensitive). For an
  account, an alias of the form "<name> (<masked number>)" (written by `resolve`
  when the plain name belongs to another account) stands for "<name>".
- Only visible documents count. When no name occurs in any visible document, the
  name is left unchanged (nothing to decide on).
- A candidate that would collide with another entity's canonical name (the unique
  indexes) is skipped; the next one in rule order is used.
- A user decision wins: the newest `review_actions` row with action `make_canonical`
  whose alias (natural key `{entity, alias: lower(alias)}`) belongs to the entity
  pins that alias as the canonical name, until a newer `make_canonical` on the same
  entity. The rule is not applied to a pinned entity. A pinned name stays even when
  every document carrying it is removed (the alias row is never deleted). If that
  alias row ever moves to another entity, the pin no longer applies to this one.

Renaming never creates or merges entities: `fund_id`/`account_id` are stable, only
`canonical_name` changes, so the views (`v_funds`, `v_returns`, ...) show the new name.
The alias rows trade roles:
- automatic rename: the new name's alias row and the old name's alias row swap
  `(match_method, match_score, confirmed)`. The canonical name's own row keeps the
  canonical role (e.g. `new`, confirmed), and an open fuzzy question moves to the
  name that is no longer canonical. Northwind: "Northwind Global Macro Fund" becomes
  canonical and the review item becomes "Northwind Macro Fund Ltd" -> Northwind
  Global Macro Fund (fuzzy, unconfirmed). If the old name had no alias row, one is
  created with the new row's state, and the new row becomes `exact`, confirmed.
- user rename (`make_canonical`): the same swap, then both rows are confirmed.

When it runs (always under the postprocess advisory lock):
- `postprocess.process_extraction`, for the entities the document's rows used
  before and after (every version of the same Drive file);
- review actions that move or delete rows (`reject_alias`, `choose`, `edit` on a
  conflict), for the entities involved;
- the worker, after a committed sync that marked documents `removed`, for the
  entities those documents used;
- the worker at boot: `recompute_all` (idempotent; also corrects existing data).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models import Account, AccountAlias, Fund, FundAlias
from app.normalize.names import alias_key

log = logging.getLogger(__name__)

PREFERRED_DOCUMENT_TYPE = {"fund": "factsheet", "account": "account_statement"}


@dataclass(frozen=True)
class NameUsage:
    """One candidate name and the visible documents it occurs in."""

    name: str
    documents: frozenset[int] = frozenset()
    preferred: frozenset[int] = frozenset()  # the subset of factsheets / account statements


def rank_key(c: NameUsage) -> tuple:
    """Sort key: the first candidate in this order is the canonical name."""
    return (-len(c.documents), -len(c.preferred), -len(c.name), c.name.casefold(), c.name)


def choose_canonical(candidates: Iterable[NameUsage]) -> NameUsage | None:
    """The rule. None when no candidate occurs in any visible document."""
    ranked = sorted(candidates, key=rank_key)
    if not ranked or not ranked[0].documents:
        return None
    return ranked[0]


def preferred_spelling(a: str, b: str) -> str:
    """Of two spellings with the same alias key, the one to store. Order-independent:
    mixed case beats ALL CAPS, then more capitals (Title Case), then code points."""
    def rank(s: str) -> tuple:
        return (not s.isupper(), sum(ch.isupper() for ch in s), s)
    return max(a, b, key=rank)


# ---- entity specs --------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Spec:
    entity: str
    entity_model: type
    alias_model: type
    owner: str  # fund_id | account_id
    alias_table: str
    entity_table: str
    usage_sql: str
    linked_docs_sql: str


_FUND_USAGE = """
SELECT x.entity_id, x.alias_id, x.document_id, d.document_type
FROM (SELECT fund_id AS entity_id, fund_alias_id AS alias_id, document_id FROM returns WHERE fund_id = ANY(:ids)
      UNION SELECT fund_id, fund_alias_id, document_id FROM fund_facts WHERE fund_id = ANY(:ids)
      UNION SELECT fund_id, fund_alias_id, document_id FROM statements WHERE fund_id = ANY(:ids)) x
JOIN documents d ON d.id = x.document_id AND d.is_latest AND d.status <> 'removed'
"""
_ACCOUNT_USAGE = """
SELECT DISTINCT s.account_id AS entity_id, s.account_alias_id AS alias_id, s.document_id, d.document_type
FROM statements s JOIN documents d ON d.id = s.document_id AND d.is_latest AND d.status <> 'removed'
WHERE s.account_id = ANY(:ids)
"""
_FUND_LINKED_DOCS = """
SELECT document_id FROM returns WHERE fund_alias_id = ANY(:ids)
UNION SELECT document_id FROM fund_facts WHERE fund_alias_id = ANY(:ids)
UNION SELECT document_id FROM statements WHERE fund_alias_id = ANY(:ids)
"""
_ACCOUNT_LINKED_DOCS = "SELECT DISTINCT document_id FROM statements WHERE account_alias_id = ANY(:ids)"

SPECS = {
    "fund": _Spec("fund", Fund, FundAlias, "fund_id", "fund_aliases", "funds", _FUND_USAGE, _FUND_LINKED_DOCS),
    "account": _Spec("account", Account, AccountAlias, "account_id", "account_aliases", "accounts", _ACCOUNT_USAGE,
                     _ACCOUNT_LINKED_DOCS),
}


def name_of(entity: str, alias_text: str, owner) -> str:
    """The name an alias row stands for ("Acme (XXXX-1)" -> "Acme" on account XXXX-1)."""
    if entity == "account" and owner.account_number_masked:
        suffix = f" ({owner.account_number_masked})"
        if alias_text.endswith(suffix) and len(alias_text) > len(suffix):
            return alias_text[: -len(suffix)]
    return alias_text


@dataclass
class RecomputeResult:
    renamed: list[tuple[str, int, str, str]] = field(default_factory=list)  # (entity, id, old, new)
    document_ids: set[int] = field(default_factory=set)  # documents whose review status may have changed

    def merge(self, other: RecomputeResult) -> RecomputeResult:
        self.renamed += other.renamed
        self.document_ids |= other.document_ids
        return self


def _pins(session: Session, spec: _Spec, ids: list[int]) -> dict[int, int]:
    """entity id -> alias id pinned by the newest `make_canonical` decision."""
    rows = session.execute(text(f"""
SELECT DISTINCT ON (a.{spec.owner}) a.{spec.owner} AS entity_id, a.id AS alias_id
FROM review_actions ra JOIN {spec.alias_table} a ON lower(a.alias) = ra.natural_key->>'alias'
WHERE ra.action = 'make_canonical' AND ra.target_table = :t AND a.{spec.owner} = ANY(:ids)
ORDER BY a.{spec.owner}, ra.id DESC"""), {"t": spec.alias_table, "ids": ids}).all()
    return {r.entity_id: r.alias_id for r in rows}


def name_taken(session: Session, spec: _Spec, owner, name: str) -> bool:
    """Would `name` collide with another entity's canonical name (unique indexes)?"""
    m = spec.entity_model
    stmt = select(m.id).where(func.lower(m.canonical_name) == name.lower(), m.id != owner.id)
    if spec.entity == "account":
        stmt = stmt.where(func.coalesce(Account.account_number_masked, "") == (owner.account_number_masked or ""))
    return session.execute(stmt.limit(1)).first() is not None


def _alias_row_for(rows: list, key: str, spec: _Spec, owner):
    """The entity's alias row standing for the name with this key (lowest id first)."""
    for r in sorted(rows, key=lambda r: r.id):
        if alias_key(name_of(spec.entity, r.alias, owner)) == key:
            return r
    return None


def _linked_documents(session: Session, spec: _Spec, alias_rows) -> set[int]:
    ids = [r.id for r in alias_rows if r is not None]
    out = {r.source_document_id for r in alias_rows if r is not None and r.source_document_id}
    if ids:
        out |= {d for (d,) in session.execute(text(spec.linked_docs_sql), {"ids": ids}) if d is not None}
    return out


def rename(session: Session, entity: str, owner, new_row, *, user: bool) -> RecomputeResult:
    """Make `new_row`'s name the canonical name of `owner` (see the module docstring for
    how the alias rows trade roles). Returns the documents to refresh."""
    spec = SPECS[entity]
    rows = list(session.execute(select(spec.alias_model).where(
        getattr(spec.alias_model, spec.owner) == owner.id)).scalars())
    old_name = owner.canonical_name
    new_name = name_of(entity, new_row.alias, owner)
    old_row = _alias_row_for([r for r in rows if r.id != new_row.id], alias_key(old_name), spec, owner)
    if alias_key(new_name) == alias_key(old_name):
        old_row = None  # a spelling change of the same name: the rows keep their roles
    elif old_row is None:
        clash = session.execute(select(spec.alias_model.id).where(
            func.lower(spec.alias_model.alias) == old_name.lower())).first()
        if clash is None:
            old_row = spec.alias_model(alias=old_name, match_method=new_row.match_method,
                                       match_score=new_row.match_score, confirmed=new_row.confirmed,
                                       source_document_id=new_row.source_document_id)
            setattr(old_row, spec.owner, owner.id)
            session.add(old_row)
            new_row.match_method, new_row.match_score, new_row.confirmed = "exact", None, True
    else:
        (old_row.match_method, old_row.match_score, old_row.confirmed,
         new_row.match_method, new_row.match_score, new_row.confirmed) = (
            new_row.match_method, new_row.match_score, new_row.confirmed,
            old_row.match_method, old_row.match_score, old_row.confirmed)
    if user:
        new_row.confirmed = True
        if old_row is not None:
            old_row.confirmed = True
    owner.canonical_name = new_name
    session.flush()
    result = RecomputeResult(document_ids=_linked_documents(session, spec, [new_row, old_row]))
    if new_name != old_name:
        result.renamed.append((entity, owner.id, old_name, new_name))
        log.info("canonical name changed", extra={"event": "canonical_renamed", "entity": entity,
                                                  "entity_id": owner.id, "by": "user" if user else "rule"})
    return result


def recompute(session: Session, entity: str, ids: Iterable[int]) -> RecomputeResult:
    """Apply the rule to these funds/accounts (callers hold the postprocess lock)."""
    spec = SPECS[entity]
    ids = sorted({i for i in ids if i is not None})
    result = RecomputeResult()
    if not ids:
        return result
    session.flush()
    owners = {o.id: o for o in session.execute(select(spec.entity_model).where(
        spec.entity_model.id.in_(ids)).order_by(spec.entity_model.id)).scalars()}
    alias_rows: dict[int, list] = {}
    for a in session.execute(select(spec.alias_model).where(getattr(spec.alias_model, spec.owner).in_(ids))
                             .order_by(spec.alias_model.id)).scalars():
        alias_rows.setdefault(getattr(a, spec.owner), []).append(a)
    usage: dict[int, list] = {}
    for u in session.execute(text(spec.usage_sql), {"ids": ids}):
        usage.setdefault(u.entity_id, []).append(u)
    pins = _pins(session, spec, ids)
    preferred_type = PREFERRED_DOCUMENT_TYPE[entity]

    for eid, owner in owners.items():
        rows = alias_rows.get(eid, [])
        by_id = {r.id: r for r in rows}
        pinned = by_id.get(pins.get(eid)) if eid in pins else None
        if pinned is not None:
            if name_of(entity, pinned.alias, owner) != owner.canonical_name:
                result.merge(rename(session, entity, owner, pinned, user=True))
            continue
        groups: dict[str, dict] = {}
        canonical_key = alias_key(owner.canonical_name)
        groups[canonical_key] = {"rows": [], "docs": set(), "pref": set()}
        for r in rows:
            k = alias_key(name_of(entity, r.alias, owner))
            groups.setdefault(k, {"rows": [], "docs": set(), "pref": set()})["rows"].append(r)
        for u in usage.get(eid, []):
            r = by_id.get(u.alias_id)
            k = alias_key(name_of(entity, r.alias, owner)) if r is not None else canonical_key
            groups[k]["docs"].add(u.document_id)
            if u.document_type == preferred_type:
                groups[k]["pref"].add(u.document_id)
        candidates = []
        for k, g in groups.items():
            if g["rows"]:
                name = g["rows"][0].alias
                for r in g["rows"][1:]:
                    name = preferred_spelling(name, r.alias)
                name = name_of(entity, name, owner)
            else:
                name = owner.canonical_name
            candidates.append((NameUsage(name, frozenset(g["docs"]), frozenset(g["pref"])), k, g["rows"]))
        candidates.sort(key=lambda c: rank_key(c[0]))
        for usage_, key, grp_rows in candidates:
            if not usage_.documents:
                break  # nothing occurs in a visible document: leave the name alone
            if usage_.name == owner.canonical_name:
                break
            if name_taken(session, spec, owner, usage_.name):
                continue
            row = next((r for r in grp_rows if name_of(entity, r.alias, owner) == usage_.name), None)
            if row is None:  # the canonical group without a row of its own: spelling only
                if key == canonical_key:
                    break
                row = grp_rows[0]
            result.merge(rename(session, entity, owner, row, user=False))
            break
    return result


def entities_for_documents(session: Session, document_ids: Iterable[int]) -> tuple[set[int], set[int]]:
    """(fund ids, account ids) that rows of these documents use."""
    ids = sorted({d for d in document_ids if d is not None})
    if not ids:
        return set(), set()
    funds = {f for (f,) in session.execute(text("""
SELECT fund_id FROM returns WHERE document_id = ANY(:ids)
UNION SELECT fund_id FROM fund_facts WHERE document_id = ANY(:ids)
UNION SELECT fund_id FROM statements WHERE document_id = ANY(:ids) AND fund_id IS NOT NULL"""), {"ids": ids})}
    accounts = {a for (a,) in session.execute(text(
        "SELECT DISTINCT account_id FROM statements WHERE document_id = ANY(:ids)"), {"ids": ids})}
    return funds, accounts


def recompute_entities(session: Session, fund_ids: Iterable[int], account_ids: Iterable[int]) -> RecomputeResult:
    return recompute(session, "fund", fund_ids).merge(recompute(session, "account", account_ids))


def recompute_for_documents(session: Session, document_ids: Iterable[int]) -> RecomputeResult:
    funds, accounts = entities_for_documents(session, document_ids)
    return recompute_entities(session, funds, accounts)


def recompute_all(session: Session) -> RecomputeResult:
    """Every fund and account. Idempotent: a second run changes nothing."""
    funds = [i for (i,) in session.execute(select(Fund.id))]
    accounts = [i for (i,) in session.execute(select(Account.id))]
    return recompute_entities(session, funds, accounts)


def run_recompute(engine, document_ids: Iterable[int] | None = None) -> RecomputeResult:
    """One transaction under the postprocess lock: every entity (`document_ids` None,
    the worker's boot run) or the entities these documents use (after a sync removed
    them). Then done <-> needs_review is re-derived for the documents involved."""
    from app.ingest.postprocess import lock_postprocess, refresh_document_statuses  # postprocess imports us

    with Session(engine) as s, s.begin():
        lock_postprocess(s)
        res = recompute_all(s) if document_ids is None else recompute_for_documents(s, document_ids)
        refresh_document_statuses(s, res.document_ids)
    return res


__all__ = ["run_recompute", "NameUsage", "RecomputeResult", "choose_canonical", "preferred_spelling", "rank_key", "rename",
           "recompute", "recompute_all", "recompute_entities", "recompute_for_documents", "entities_for_documents",
           "name_of", "name_taken", "PREFERRED_DOCUMENT_TYPE", "SPECS"]
