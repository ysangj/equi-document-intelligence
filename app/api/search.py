"""`GET /api/search?q=` (FR-10): keyword search, no LLM.

The four groups run concurrently, each on its own pooled connection:
- funds:     canonical names and every alias, one hit per fund (best match);
- accounts:  the same over canonical names, aliases and masked numbers;
- documents: `documents.name`;
- chunks:    `tsv @@ websearch_to_tsquery('english', q)` ranked by `ts_rank_cd`,
             highlighted with `ts_headline` (sentinels -> safe `<mark>` HTML).
Only visible documents (latest version, not removed) and their funds/accounts are
searched. Queries shorter than 2 characters (after trimming) return empty groups.

Name matching (funds, accounts, documents). A name matches when either
- a query word is a prefix of some word of the name (words = runs of letters and
  digits, case-insensitive; in multi-word queries, 1-2 letter words are ignored):
  "kestrel quant", "north", "4471", "acme", the "meridian" of "meridian duration"; or
- the query has at least FUZZY_MIN_LENGTH characters and pg_trgm
  `word_similarity(q, name) >= FUZZY_THRESHOLD`, which catches typos ("nortwind").
The fuzzy cutoff is high enough that a common word sharing a suffix with a name does
not match ("duration" vs "Birchwood Foundation" is 0.44), and short queries never
match fuzzily. Names matching more query words by prefix rank first, then `score`
(`word_similarity`) desc.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Engine, text

from app.db import get_engine
from app.schemas import SearchQuery, SearchResponse
from app.schemas.common import ErrorResponse
from app.schemas.search import HEADLINE_START, HEADLINE_STOP, MIN_QUERY_LENGTH, TS_HEADLINE_OPTIONS, headline_to_html

router = APIRouter(prefix="/api", tags=["search"])

FUZZY_THRESHOLD = 0.5
FUZZY_MIN_LENGTH = 4
_WORD_RE = re.compile(r"[^\W_]+")


def _prefix_match(col: str) -> str:
    """SQL: how many query words (:qwords) start some word of `col`."""
    return (f"(SELECT count(*) FROM unnest(CAST(:qwords AS text[])) qw WHERE EXISTS ("
            f"SELECT 1 FROM regexp_split_to_table(lower({col}), '[^[:alnum:]]+') nw WHERE starts_with(nw, qw)))")


def query_words(q: str) -> list[str]:
    """Words used for prefix matching. In a multi-word query, alphabetic words shorter
    than 3 letters ("in", "of") are dropped so they don't match everything."""
    words = _WORD_RE.findall(q.lower())
    if len(words) > 1:
        words = [w for w in words if len(w) >= 3 or any(ch.isdigit() for ch in w)]
    return list(dict.fromkeys(words))


_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="search")

_FUNDS_SQL = text("""
WITH visible AS (
  SELECT fund_id FROM v_returns UNION SELECT fund_id FROM v_fund_facts
  UNION SELECT fund_id FROM v_statements WHERE fund_id IS NOT NULL
), names AS (
  SELECT f.id AS fund_id, f.canonical_name AS name, NULL::text AS alias FROM funds f
  UNION ALL
  SELECT a.fund_id, a.alias, a.alias FROM fund_aliases a
), scored AS (
  SELECT n.fund_id, n.alias, word_similarity(:q, n.name) AS score, PREFIX AS matched FROM names n
  WHERE n.fund_id IN (SELECT fund_id FROM visible)
), best AS (
  SELECT DISTINCT ON (fund_id) fund_id, alias, score, matched FROM scored
  WHERE matched > 0 OR (:fuzzy AND score >= :t)
  ORDER BY fund_id, matched DESC, score DESC, (alias IS NULL) DESC
)
SELECT f.id AS fund_id, f.canonical_name AS fund_name, f.manager,
       CASE WHEN b.alias IS NOT NULL AND lower(b.alias) <> lower(f.canonical_name)
                 AND b.score > word_similarity(:q, f.canonical_name) THEN b.alias END AS matched_alias,
       b.score
FROM best b JOIN funds f ON f.id = b.fund_id
ORDER BY b.matched DESC, b.score DESC, f.canonical_name, f.id
LIMIT :limit
""".replace("PREFIX", _prefix_match("n.name")))

_ACCOUNTS_SQL = text("""
WITH visible AS (SELECT DISTINCT account_id FROM v_statements),
names AS (
  SELECT ac.id AS account_id, ac.canonical_name AS name, NULL::text AS alias FROM accounts ac
  UNION ALL
  SELECT ac.id, ac.account_number_masked, NULL FROM accounts ac WHERE ac.account_number_masked IS NOT NULL
  UNION ALL
  SELECT a.account_id, a.alias, a.alias FROM account_aliases a
), scored AS (
  SELECT n.account_id, n.alias, word_similarity(:q, n.name) AS score, PREFIX AS matched FROM names n
  WHERE n.account_id IN (SELECT account_id FROM visible)
), best AS (
  SELECT DISTINCT ON (account_id) account_id, alias, score, matched FROM scored
  WHERE matched > 0 OR (:fuzzy AND score >= :t)
  ORDER BY account_id, matched DESC, score DESC, (alias IS NULL) DESC
)
SELECT ac.id AS account_id, ac.canonical_name AS account_name, ac.account_number_masked, ac.custodian,
       CASE WHEN b.alias IS NOT NULL AND lower(b.alias) <> lower(ac.canonical_name)
                 AND b.score > word_similarity(:q, ac.canonical_name) THEN b.alias END AS matched_alias,
       b.score
FROM best b JOIN accounts ac ON ac.id = b.account_id
ORDER BY b.matched DESC, b.score DESC, ac.canonical_name, ac.id
LIMIT :limit
""".replace("PREFIX", _prefix_match("n.name")))

_DOCUMENTS_SQL = text("""
SELECT document_id, document_name, document_type, status, web_view_link, score FROM (
  SELECT d.id AS document_id, d.name AS document_name, d.document_type, d.status, d.web_view_link,
         word_similarity(:q, d.name) AS score, PREFIX AS matched
  FROM documents d
  WHERE d.is_latest AND d.status <> 'removed'
) x
WHERE matched > 0 OR (:fuzzy AND score >= :t)
ORDER BY matched DESC, score DESC, document_name, document_id
LIMIT :limit
""".replace("PREFIX", _prefix_match("d.name")))

# Rank first, then headline only the hits that are returned (ts_headline is the slow part).
_CHUNKS_SQL = text(f"""
WITH query AS (SELECT websearch_to_tsquery('english', :q) AS tsq),
hits AS (
  SELECT c.id, c.document_id, c.page, c.chunk_index, c.text, ts_rank_cd(c.tsv, query.tsq) AS rank
  FROM document_chunks c
  JOIN documents d ON d.id = c.document_id
  CROSS JOIN query
  WHERE c.tsv @@ query.tsq AND d.is_latest AND d.status <> 'removed'
  ORDER BY rank DESC, c.id
  LIMIT :limit
)
SELECT h.id AS chunk_id, h.document_id, d.name AS document_name, d.document_type, h.page, h.chunk_index,
       ts_headline('english', translate(h.text, :sentinels, ''), query.tsq, :opts) AS headline,
       h.rank, d.web_view_link
FROM hits h JOIN documents d ON d.id = h.document_id CROSS JOIN query
ORDER BY h.rank DESC, h.id
""")


def _run(engine: Engine, stmt, params: dict) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(stmt, params).mappings()]
        conn.rollback()
    return rows


_ALIASES_SQL = {
    "funds": text("""SELECT a.fund_id AS owner, a.alias, a.confirmed FROM fund_aliases a JOIN funds f ON f.id = a.fund_id
                     WHERE a.fund_id = ANY(:ids) AND lower(a.alias) <> lower(f.canonical_name)
                     ORDER BY lower(a.alias), a.id"""),
    "accounts": text("""SELECT a.account_id AS owner, a.alias, a.confirmed FROM account_aliases a
                        JOIN accounts ac ON ac.id = a.account_id
                        WHERE a.account_id = ANY(:ids) AND lower(a.alias) <> lower(ac.canonical_name)
                        ORDER BY lower(a.alias), a.id"""),
}


def _with_aliases(engine: Engine, group: str, hits: list[dict], id_key: str) -> list[dict]:
    if not hits:
        return hits
    by_owner: dict[int, list[dict]] = {}
    for r in _run(engine, _ALIASES_SQL[group], {"ids": [h[id_key] for h in hits]}):
        by_owner.setdefault(r["owner"], []).append({"alias": r["alias"], "confirmed": r["confirmed"]})
    return [{**h, "aliases": by_owner.get(h[id_key], [])} for h in hits]


def _funds(engine: Engine, params: dict) -> list[dict]:
    return _with_aliases(engine, "funds", _run(engine, _FUNDS_SQL, params), "fund_id")


def _accounts(engine: Engine, params: dict) -> list[dict]:
    return _with_aliases(engine, "accounts", _run(engine, _ACCOUNTS_SQL, params), "account_id")


def _round(x: Any, n: int = 4) -> float:
    return round(float(x), n)


def run_search(engine: Engine, q: str, limit: int) -> dict[str, Any]:
    started = time.perf_counter()
    query = q.strip()
    groups: dict[str, list] = {"funds": [], "accounts": [], "documents": [], "chunks": []}
    if len(query) < MIN_QUERY_LENGTH:
        return {"query": query, "min_query_length": MIN_QUERY_LENGTH, "groups": groups, "total": 0, "took_ms": 0}
    base = {"q": query, "limit": limit, "t": FUZZY_THRESHOLD, "qwords": query_words(query),
            "fuzzy": len(query) >= FUZZY_MIN_LENGTH}
    futures = {
        "funds": _pool.submit(_funds, engine, base),
        "accounts": _pool.submit(_accounts, engine, base),
        "documents": _pool.submit(_run, engine, _DOCUMENTS_SQL, base),
        "chunks": _pool.submit(_run, engine, _CHUNKS_SQL, {
            **base, "opts": TS_HEADLINE_OPTIONS, "sentinels": HEADLINE_START + HEADLINE_STOP}),
    }
    results = {k: f.result() for k, f in futures.items()}
    for r in results["funds"]:
        groups["funds"].append({**r, "score": _round(r["score"])})
    for r in results["accounts"]:
        groups["accounts"].append({**r, "score": _round(r["score"])})
    for r in results["documents"]:
        groups["documents"].append({**r, "score": _round(r["score"])})
    for r in results["chunks"]:
        headline = r.pop("headline")
        groups["chunks"].append({**r, "headline_html": headline_to_html(headline), "rank": _round(r["rank"], 6)})
    took = int((time.perf_counter() - started) * 1000)
    return {"query": query, "min_query_length": MIN_QUERY_LENGTH, "groups": groups,
            "total": sum(len(v) for v in groups.values()), "took_ms": took}


@router.get("/search", response_model=SearchResponse, responses={422: {"model": ErrorResponse}})
def search(q: Annotated[SearchQuery, Query()], engine: Engine = Depends(get_engine)):
    return run_search(engine, q.q, q.limit)
