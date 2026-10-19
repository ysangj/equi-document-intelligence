"""Fund and account resolution (FR-6): extracted name -> canonical row via aliases.

Order, for funds:
1. Exact alias match (`names.alias_key`: case/whitespace/dash-normalized) against
   every alias, confirmed or not, and every canonical name -> link to that fund.
   Review decisions on aliases persist this way: a confirmed alias, or one moved to
   another fund by `reject_alias` (match_method 'manual'), is matched exactly the next
   time the name is seen, so it never re-enters the queue (FR-13).
2. Fuzzy (`names.best_fuzzy_match`: score >= 80 and the distinct-token guard) -> link,
   and add the name as an UNCONFIRMED alias with match_method 'fuzzy' and its score.
   That alias is the `alias_match` review item.
3. Otherwise a new fund, with the name as its canonical name and a confirmed 'new'
   alias. `exact`/`new` aliases are written confirmed (T3).

Accounts work the same way on the account name, with the masked account number as a
guard: an account whose masked number differs from a non-null extracted one is never
a match (same name, different account). A matching account with no number on file
gets the number filled in.

An exact match seen in another letter case keeps the order-independent preferred
spelling (`canonical.preferred_spelling`). The canonical NAME itself is chosen by
`app.ingest.canonical` (most documents, then factsheets, longest, alphabetical) after
each document, not by which name was seen first.

Callers hold the postprocess advisory lock (`postprocess.lock_postprocess`), so two
jobs never race to create the same fund or alias.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingest.canonical import preferred_spelling
from app.models import Account, AccountAlias, Fund, FundAlias
from app.normalize.names import FuzzyMatch, alias_key, best_fuzzy_match, display_name


@dataclass(frozen=True)
class Resolved:
    entity_id: int
    alias_id: int | None
    method: str  # exact | fuzzy | new
    score: float | None = None

    @property
    def needs_confirmation(self) -> bool:
        return self.method == "fuzzy"


def resolve_fund(session: Session, name: str, *, document_id: int | None) -> Resolved:
    disp = display_name(name)
    key = alias_key(disp)
    aliases = session.execute(select(FundAlias.id, FundAlias.fund_id, FundAlias.alias,
                                     FundAlias.match_method, FundAlias.confirmed)).all()
    for a in sorted(aliases, key=lambda a: a.id):
        if alias_key(a.alias) == key:
            _respell(session, FundAlias, a.id, a.alias, disp)
            method = "fuzzy" if (a.match_method == "fuzzy" and not a.confirmed) else "exact"
            return Resolved(a.fund_id, a.id, method)
    funds = session.execute(select(Fund.id, Fund.canonical_name)).all()
    for f in sorted(funds, key=lambda f: f.id):
        if alias_key(f.canonical_name) == key:  # canonical name without an alias row
            alias = _add_fund_alias(session, f.id, disp, "exact", None, document_id, confirmed=True)
            return Resolved(f.id, alias.id, "exact")
    candidates = [(a.fund_id, a.alias) for a in aliases] + [(f.id, f.canonical_name) for f in funds]
    match: FuzzyMatch | None = best_fuzzy_match(disp, candidates)
    if match is not None:
        alias = _add_fund_alias(session, match.entity_id, disp, "fuzzy", match.score, document_id, confirmed=False)
        return Resolved(match.entity_id, alias.id, "fuzzy", match.score)
    fund = Fund(canonical_name=disp)
    session.add(fund)
    session.flush()
    alias = _add_fund_alias(session, fund.id, disp, "new", None, document_id, confirmed=True)
    return Resolved(fund.id, alias.id, "new")


def _respell(session: Session, model, alias_id: int, stored: str, seen: str) -> None:
    """Same name in another letter case ("MERIDIAN RATES ..." vs "Meridian Rates ..."):
    keep the `canonical.preferred_spelling` of the two, so the stored text does not
    depend on which document came first. Case-only differences, so `lower(alias)`
    (the unique index and the review decision key) never changes."""
    if seen == stored or seen.lower() != stored.lower():
        return
    best = preferred_spelling(stored, seen)
    if best != stored:
        session.get(model, alias_id).alias = best
        session.flush()


def _add_fund_alias(session, fund_id, alias, method, score, document_id, *, confirmed) -> FundAlias:
    row = FundAlias(fund_id=fund_id, alias=alias, confirmed=confirmed, match_method=method,
                    match_score=score, source_document_id=document_id)
    session.add(row)
    session.flush()
    return row


def fill_fund_details(fund: Fund, *, manager: str | None, strategy: str | None, currency: str | None) -> None:
    """Fill manager/strategy/currency from the extraction where the fund has none."""
    if manager and not fund.manager:
        fund.manager = display_name(manager)
    if strategy and not fund.strategy:
        fund.strategy = display_name(strategy)
    if currency and not fund.currency:
        fund.currency = currency


def _masked_key(masked: str | None) -> str | None:
    return alias_key(masked) if masked and masked.strip() else None


def resolve_account(session: Session, name: str, *, masked: str | None, custodian: str | None,
                    currency: str | None, document_id: int | None) -> Resolved:
    disp = display_name(name)
    key = alias_key(disp)
    mkey = _masked_key(masked)
    accounts = {a.id: a for a in session.execute(select(Account)).scalars()}

    def compatible(acc: Account) -> bool:
        other = _masked_key(acc.account_number_masked)
        return mkey is None or other is None or other == mkey

    aliases = session.execute(select(AccountAlias.id, AccountAlias.account_id, AccountAlias.alias,
                                     AccountAlias.match_method, AccountAlias.confirmed)).all()
    for a in sorted(aliases, key=lambda a: a.id):
        acc = accounts.get(a.account_id)
        if acc is not None and alias_key(a.alias) == key and compatible(acc):
            _respell(session, AccountAlias, a.id, a.alias, disp)
            _fill_account(acc, masked, custodian, currency)
            method = "fuzzy" if (a.match_method == "fuzzy" and not a.confirmed) else "exact"
            return Resolved(acc.id, a.id, method)
    for acc in sorted(accounts.values(), key=lambda a: a.id):
        if alias_key(acc.canonical_name) == key and compatible(acc):
            _fill_account(acc, masked, custodian, currency)
            alias = _add_account_alias(session, acc.id, disp, "exact", None, document_id, confirmed=True)
            return Resolved(acc.id, alias.id if alias else None, "exact")
    candidates = [(a.account_id, a.alias) for a in aliases if a.account_id in accounts and compatible(accounts[a.account_id])]
    candidates += [(acc.id, acc.canonical_name) for acc in accounts.values() if compatible(acc)]
    match = best_fuzzy_match(disp, candidates)
    if match is not None:
        _fill_account(accounts[match.entity_id], masked, custodian, currency)
        alias = _add_account_alias(session, match.entity_id, disp, "fuzzy", match.score, document_id, confirmed=False)
        if alias is not None:
            return Resolved(match.entity_id, alias.id, "fuzzy", match.score)
    acc = Account(canonical_name=disp, account_number_masked=display_name(masked) if mkey else None,
                  custodian=display_name(custodian) if custodian else None, currency=currency)
    session.add(acc)
    session.flush()
    alias = _add_account_alias(session, acc.id, disp, "new", None, document_id, confirmed=True)
    if alias is None and mkey:  # the plain name is another account's alias (same name, other number)
        alias = _add_account_alias(session, acc.id, f"{disp} ({display_name(masked)})", "new", None, document_id,
                                   confirmed=True)
    return Resolved(acc.id, alias.id if alias else None, "new")


def _fill_account(acc: Account, masked, custodian, currency) -> None:
    if masked and masked.strip() and not acc.account_number_masked:
        acc.account_number_masked = display_name(masked)
    if custodian and not acc.custodian:
        acc.custodian = display_name(custodian)
    if currency and not acc.currency:
        acc.currency = currency


def _add_account_alias(session, account_id, alias, method, score, document_id, *, confirmed) -> AccountAlias | None:
    """None when the alias text already belongs to an account (aliases are unique)."""
    key = alias_key(alias)
    for (existing,) in session.execute(select(AccountAlias.alias)):
        if alias_key(existing) == key:
            return None
    row = AccountAlias(account_id=account_id, alias=alias, confirmed=confirmed, match_method=method,
                       match_score=score, source_document_id=document_id)
    session.add(row)
    session.flush()
    return row
