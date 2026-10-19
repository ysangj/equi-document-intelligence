"""Hybrid document retrieval for Ask's `search_documents` (§8, FR-11).

1. FTS: the top 20 chunks by `ts_rank_cd` for `websearch_to_tsquery('english', q)`.
2. Vector: the top 20 chunks by cosine distance (`<=>`, HNSW index) to
   `embed_query(q)`, restricted to chunks embedded with the current model.
3. Merge with Reciprocal Rank Fusion: `score = sum over lists of 1 / (60 + rank)`,
   rank 1-based. Ties: best single rank, then chunk id.
4. Return the top k (default 8, max 20).

Only chunks of visible documents (latest version, not removed) are searched.

Filters (`document_type`, `fund`) are applied inside both candidate queries, so
each list is the top 20 of the filtered set. This returns the same passages as
filtering after the merge whenever the filtered set fits in the candidate lists,
and never returns fewer.

Fund filter: `fund` is matched against canonical fund names and aliases (a
case-insensitive substring either way, or pg_trgm `word_similarity` >= 0.5). A
document belongs to a matching fund when it has returns / fund facts / statements
rows for it, or when its text mentions the fund's canonical name or an alias
(this covers commentary-only documents such as a PM email with no table). If no
fund matches, the search returns no passages and the known fund names, so Claude
can retry with a correct name.

If the embedding model is unavailable, the search falls back to FTS only and says
so in `mode`.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine, text

from app.schemas.common import DocumentType
from app.search.embedding import EmbeddingError, get_embedder

log = logging.getLogger("app.ask.retrieval")

RRF_K = 60
CANDIDATES = 20
DEFAULT_K = 8
MAX_K = 20
FUND_SIMILARITY = 0.5
DOCUMENT_TYPES: tuple[str, ...] = DocumentType.__args__  # type: ignore[attr-defined]


@dataclass(frozen=True)
class Passage:
    chunk_id: int
    document_id: int
    document_name: str
    document_type: str | None
    page: int | None
    text: str
    web_view_link: str | None
    score: float
    fts_rank: int | None
    vector_rank: int | None


@dataclass
class SearchResult:
    passages: list[Passage]
    mode: str  # "hybrid" | "fts_only"
    fts_hits: int
    vector_hits: int
    matched_funds: list[str] = field(default_factory=list)
    note: str | None = None


class SearchInputError(ValueError):
    """Bad tool input (unknown document_type, empty query). Safe to show Claude."""


def rrf_merge(ranked_lists: Sequence[Sequence[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion. Each list holds ids, best first (rank 1).

    Returns (id, score) sorted by score desc, then best single rank, then id. An id
    repeated inside one list counts once, at its best rank.
    """
    scores: dict[int, float] = {}
    best: dict[int, int] = {}
    for lst in ranked_lists:
        seen: set[int] = set()
        for rank, item in enumerate(lst, start=1):
            if item in seen:
                continue
            seen.add(item)
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
            best[item] = min(best.get(item, rank), rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], best[kv[0]], kv[0]))


_VISIBLE = "d.is_latest AND d.status <> 'removed'"

_FUND_IDS_SQL = text("""
WITH names AS (
  SELECT f.id AS fund_id, f.canonical_name AS name FROM funds f
  UNION ALL SELECT a.fund_id, a.alias FROM fund_aliases a
)
SELECT DISTINCT n.fund_id FROM names n
WHERE lower(n.name) LIKE '%' || lower(:fund) || '%'
   OR lower(:fund) LIKE '%' || lower(n.name) || '%'
   OR word_similarity(lower(:fund), lower(n.name)) >= :sim
""")

_FUND_NAMES_SQL = text("SELECT canonical_name FROM funds WHERE id = ANY(:ids) ORDER BY canonical_name")
_ALL_FUNDS_SQL = text("SELECT fund_name FROM v_funds ORDER BY fund_name")

# Documents linked to the matched funds: through value rows, or by name in the text.
_FUND_DOCS = """
  AND d.id IN (
    SELECT document_id FROM returns WHERE fund_id = ANY(:fund_ids)
    UNION SELECT document_id FROM fund_facts WHERE fund_id = ANY(:fund_ids)
    UNION SELECT document_id FROM statements WHERE fund_id = ANY(:fund_ids)
    UNION SELECT d2.id FROM documents d2
      JOIN (SELECT canonical_name AS name FROM funds WHERE id = ANY(:fund_ids)
            UNION SELECT alias FROM fund_aliases WHERE fund_id = ANY(:fund_ids)) n
        ON length(n.name) >= 4 AND d2.text_content ILIKE '%' || n.name || '%'
  )"""


def _filters(document_type: str | None, fund_ids: list[int] | None) -> tuple[str, dict[str, Any]]:
    sql, params = "", {}
    if document_type:
        sql += " AND d.document_type = :document_type"
        params["document_type"] = document_type
    if fund_ids is not None:
        sql += _FUND_DOCS
        params["fund_ids"] = fund_ids
    return sql, params


def _fts_ids(conn, query: str, where: str, params: dict) -> list[int]:
    stmt = text(f"""
        SELECT c.id FROM document_chunks c JOIN documents d ON d.id = c.document_id,
             websearch_to_tsquery('english', :q) AS tsq
        WHERE c.tsv @@ tsq AND {_VISIBLE} {where}
        ORDER BY ts_rank_cd(c.tsv, tsq) DESC, c.id
        LIMIT :n""")
    return [r[0] for r in conn.execute(stmt, {**params, "q": query, "n": CANDIDATES})]


def _vector_ids(conn, vector: list[float], model: str, where: str, params: dict) -> list[int]:
    stmt = text(f"""
        SELECT c.id FROM document_chunks c JOIN documents d ON d.id = c.document_id
        WHERE c.embedding IS NOT NULL AND c.embedding_model = :model AND {_VISIBLE} {where}
        ORDER BY c.embedding <=> CAST(:v AS vector), c.id
        LIMIT :n""")
    literal = "[" + ",".join(f"{x:.7g}" for x in vector) + "]"
    return [r[0] for r in conn.execute(stmt, {**params, "v": literal, "model": model, "n": CANDIDATES})]


_PASSAGES_SQL = text("""
SELECT c.id AS chunk_id, c.document_id, d.name AS document_name, d.document_type, c.page, c.text,
       d.web_view_link
FROM document_chunks c JOIN documents d ON d.id = c.document_id
WHERE c.id = ANY(:ids)
""")


def search_documents(engine: Engine, query: str, k: int | None = DEFAULT_K, document_type: str | None = None,
                     fund: str | None = None) -> SearchResult:
    query = (query or "").strip()
    if not query:
        raise SearchInputError("query must not be empty.")
    k = DEFAULT_K if k is None else max(1, min(int(k), MAX_K))
    if document_type is not None and document_type not in DOCUMENT_TYPES:
        raise SearchInputError(f"document_type must be one of: {', '.join(DOCUMENT_TYPES)} (got {document_type!r}).")
    fund = (fund or "").strip() or None

    vector: list[float] | None
    try:
        embedder = get_embedder()
        model = embedder.model_name  # chunks embedded with another model are skipped (§8)
        vector = embedder.embed_query(query)
    except EmbeddingError as e:
        log.warning("query embedding failed; FTS only", extra={"event": "ask_embedding_error", "error": str(e)})
        vector, model = None, ""

    with engine.connect() as conn:
        fund_ids: list[int] | None = None
        matched: list[str] = []
        if fund:
            fund_ids = [r[0] for r in conn.execute(_FUND_IDS_SQL, {"fund": fund, "sim": FUND_SIMILARITY})]
            if not fund_ids:
                known = [r[0] for r in conn.execute(_ALL_FUNDS_SQL)]
                conn.rollback()
                return SearchResult([], "hybrid" if vector else "fts_only", 0, 0, [],
                                    note=f"No fund matches {fund!r}. Known funds: {', '.join(known)}. "
                                         "Retry with one of these names, or without the fund filter.")
            matched = [r[0] for r in conn.execute(_FUND_NAMES_SQL, {"ids": fund_ids})]
        where, params = _filters(document_type, fund_ids)
        fts = _fts_ids(conn, query, where, params)
        vec = _vector_ids(conn, vector, model, where, params) if vector is not None else []
        merged = rrf_merge([fts, vec])[:k]
        ids = [cid for cid, _ in merged]
        by_id = {r["chunk_id"]: dict(r) for r in conn.execute(_PASSAGES_SQL, {"ids": ids}).mappings()} if ids else {}
        conn.rollback()

    fts_rank = {cid: i for i, cid in enumerate(fts, start=1)}
    vec_rank = {cid: i for i, cid in enumerate(vec, start=1)}
    passages = [
        Passage(**by_id[cid], score=round(score, 6), fts_rank=fts_rank.get(cid), vector_rank=vec_rank.get(cid))
        for cid, score in merged if cid in by_id
    ]
    return SearchResult(passages, "hybrid" if vector is not None else "fts_only", len(fts), len(vec), matched)
