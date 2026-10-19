"""Ask hybrid retrieval (§8, §14 "RRF merge"): FTS top 20 + vector top 20, RRF k=60, filters."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.ask.retrieval import CANDIDATES, RRF_K, SearchInputError, rrf_merge, search_documents
from app.search import embedding
from tests.embedding_fakes import FakeEmbedder

# ---- RRF (pure) ------------------------------------------------------------------


def test_rrf_scores_follow_the_formula():
    merged = dict(rrf_merge([[10, 20, 30], [30, 10]]))
    assert merged[10] == pytest.approx(1 / 61 + 1 / 62)
    assert merged[30] == pytest.approx(1 / 63 + 1 / 61)
    assert merged[20] == pytest.approx(1 / 62)


def test_rrf_order_and_ties():
    # 1 and 2 both appear once at rank 1 -> tie on score and best rank -> id order.
    assert [i for i, _ in rrf_merge([[2, 5], [1, 6]])] == [1, 2, 5, 6]
    # In both lists beats rank 1 in one list.
    assert [i for i, _ in rrf_merge([[1, 2], [3, 2]])][0] == 2


def test_rrf_one_list_or_empty():
    assert rrf_merge([[], []]) == []
    assert [i for i, _ in rrf_merge([[4, 3, 9], []])] == [4, 3, 9]


def test_rrf_duplicate_in_one_list_counts_once():
    merged = dict(rrf_merge([[7, 7, 8]]))
    assert merged[7] == pytest.approx(1 / 61)
    assert merged[8] == pytest.approx(1 / 63)


def test_rrf_custom_k():
    assert dict(rrf_merge([[1]], k=0))[1] == 1.0
    assert RRF_K == 60 and CANDIDATES == 20


# ---- DB retrieval ----------------------------------------------------------------

DOCS = {
    # name: (document_type, status, is_latest, text_content, chunks)
    "meridian_rv_tearsheet_2026-01.pdf": ("factsheet", "done", True, "Meridian Rates Relative Value Fund tear sheet",
                                          ["We reduced duration from 3.4 to 2.1 years ahead of the March meeting.",
                                           "Monthly returns table for the fund."]),
    "meridian_pm_note_feb2026.eml": ("email", "done", True,
                                     "PM note, Meridian Rates Relative Value Fund. Our rate sensitivity is low.",
                                     ["Our rate sensitivity remains low and interest-rate exposure is hedged."]),
    "kestrel_factsheet_2026-01.pdf": ("factsheet", "done", True, "Kestrel Quant Equity Fund LP",
                                      ["January gains came from short-term reversal signals in US large caps."]),
    "harbor_letter.pdf": ("performance_report", "done", True, "Harbor Credit letter",
                          ["Spread duration is modest; we prefer short duration credit."]),
    "removed.pdf": ("factsheet", "removed", True, "removed", ["duration duration removed document"]),
    "old_version.pdf": ("factsheet", "done", False, "old", ["duration in an old superseded version"]),
}


@pytest.fixture(scope="module")
def corpus(test_engine):
    from tests.api_helpers import truncate

    truncate(test_engine)
    fake = FakeEmbedder()
    ids: dict[str, int] = {}
    chunk_ids: dict[str, int] = {}
    with test_engine.begin() as c:
        for name, (dtype, status, latest, body, chunks) in DOCS.items():
            drive_id = "old" if name == "old_version.pdf" else name
            ids[name] = c.execute(text("""
                INSERT INTO documents (source, drive_file_id, name, mime_type, md5_checksum, status, document_type,
                                       is_latest, version, text_content, web_view_link)
                VALUES ('local', :drive, :n, 'application/pdf', :md5, :st, :t, :latest, 1, :body, :link) RETURNING id"""),
                {"drive": drive_id, "n": name, "md5": name, "st": status, "t": dtype, "latest": latest, "body": body,
                 "link": f"https://drive.example/{name}"}).scalar()
            for i, chunk in enumerate(chunks):
                vec = "[" + ",".join(str(x) for x in fake.embed_query(chunk)) + "]"
                chunk_ids[chunk] = c.execute(text("""
                    INSERT INTO document_chunks (document_id, page, chunk_index, text, embedding, embedding_model)
                    VALUES (:d, :p, :i, :t, CAST(:v AS vector), :m) RETURNING id"""),
                    {"d": ids[name], "p": i + 1, "i": i, "t": chunk, "v": vec, "m": fake.model_name}).scalar()
        meridian = c.execute(text("INSERT INTO funds (canonical_name) VALUES ('Meridian Rates Relative Value Fund') "
                                  "RETURNING id")).scalar()
        c.execute(text("INSERT INTO fund_aliases (fund_id, alias, confirmed) VALUES (:f, 'Meridian Rates RV Fund', "
                       "false)"), {"f": meridian})
        kestrel = c.execute(text("INSERT INTO funds (canonical_name) VALUES ('Kestrel Quant Equity Fund LP') "
                                 "RETURNING id")).scalar()
        for fund, doc in ((meridian, "meridian_rv_tearsheet_2026-01.pdf"), (kestrel, "kestrel_factsheet_2026-01.pdf")):
            c.execute(text("""INSERT INTO returns (fund_id, document_id, period_end, return_type, net_gross, value,
                              page, source_snippet, confidence, status)
                              VALUES (:f, :d, '2026-01-31', 'MTD', 'net', 0.01, 1, 'x', 0.9, 'ok')"""),
                      {"f": fund, "d": ids[doc]})
        # A chunk embedded with another model: FTS only.
        c.execute(text("""INSERT INTO document_chunks (document_id, page, chunk_index, text, embedding, embedding_model)
                          VALUES (:d, 3, 2, 'duration other model chunk', CAST(:v AS vector), 'other-model')"""),
                  {"d": ids["meridian_rv_tearsheet_2026-01.pdf"], "v": "[" + ",".join(["0.05"] * 384) + "]"})
    yield {"ids": ids, "chunks": chunk_ids, "engine": test_engine}
    truncate(test_engine)


def names(res):
    return [p.document_name for p in res.passages]


def test_hybrid_returns_fts_and_vector_hits(corpus):
    res = search_documents(corpus["engine"], "duration")
    assert res.mode == "hybrid"
    assert res.fts_hits >= 3 and res.vector_hits >= 1
    got = names(res)
    assert "removed.pdf" not in got and "old_version.pdf" not in got
    assert {"meridian_rv_tearsheet_2026-01.pdf", "harbor_letter.pdf"} <= set(got)
    scores = [p.score for p in res.passages]
    assert scores == sorted(scores, reverse=True)
    for p in res.passages:
        expected = sum(1 / (RRF_K + r) for r in (p.fts_rank, p.vector_rank) if r is not None)
        assert p.score == pytest.approx(expected, abs=1e-6)


def test_other_model_chunks_only_via_fts(corpus):
    res = search_documents(corpus["engine"], "duration other model")
    other = [p for p in res.passages if p.text.startswith("duration other model")]
    assert other and other[0].vector_rank is None and other[0].fts_rank is not None


def test_passage_fields(corpus):
    res = search_documents(corpus["engine"], "reversal signals", k=1)
    assert len(res.passages) == 1
    p = res.passages[0]
    assert p.document_name == "kestrel_factsheet_2026-01.pdf" and p.document_type == "factsheet"
    assert p.document_id == corpus["ids"]["kestrel_factsheet_2026-01.pdf"]
    assert p.page == 1 and p.web_view_link == "https://drive.example/kestrel_factsheet_2026-01.pdf"
    assert p.chunk_id == corpus["chunks"][p.text]


def test_synonym_phrasing_found_via_or_query(corpus):
    """The prompt's synonym strategy: one query with alternatives finds passages that never say "rate risk"."""
    res = search_documents(corpus["engine"], 'rate risk or duration or "rate sensitivity" or "interest-rate exposure"')
    got = set(names(res))
    assert {"meridian_pm_note_feb2026.eml", "meridian_rv_tearsheet_2026-01.pdf", "harbor_letter.pdf"} <= got
    assert all("rate risk" not in p.text.lower() for p in res.passages)


def test_k_default_and_clamp(corpus):
    assert len(search_documents(corpus["engine"], "duration", k=1).passages) == 1
    assert len(search_documents(corpus["engine"], "the fund", k=0).passages) == 1  # clamped up to 1
    assert len(search_documents(corpus["engine"], "duration", k=500).passages) <= 20


def test_document_type_filter(corpus):
    res = search_documents(corpus["engine"], "duration rate sensitivity", document_type="email")
    assert names(res) == ["meridian_pm_note_feb2026.eml"]
    res = search_documents(corpus["engine"], "duration", document_type="performance_report")
    assert set(names(res)) == {"harbor_letter.pdf"}


def test_invalid_input(corpus):
    with pytest.raises(SearchInputError):
        search_documents(corpus["engine"], "duration", document_type="memo")
    with pytest.raises(SearchInputError):
        search_documents(corpus["engine"], "   ")


def test_fund_filter_by_values_and_by_text(corpus):
    res = search_documents(corpus["engine"], "duration rate sensitivity", fund="Meridian")
    got = set(names(res))
    # tear sheet via its returns rows, PM note via its text naming the fund; Harbor excluded.
    assert got == {"meridian_rv_tearsheet_2026-01.pdf", "meridian_pm_note_feb2026.eml"}
    assert res.matched_funds == ["Meridian Rates Relative Value Fund"]


def test_fund_filter_by_alias_and_full_name(corpus):
    assert set(names(search_documents(corpus["engine"], "reversal", fund="Kestrel Quant Equity Fund LP"))) == {
        "kestrel_factsheet_2026-01.pdf"}
    res = search_documents(corpus["engine"], "duration", fund="meridian rates rv fund")
    assert "harbor_letter.pdf" not in names(res) and res.passages


def test_unknown_fund_returns_note_with_known_funds(corpus):
    res = search_documents(corpus["engine"], "duration", fund="Zebra Capital")
    assert res.passages == []
    assert "No fund matches 'Zebra Capital'" in res.note


def test_embedding_failure_falls_back_to_fts(corpus):
    embedding.set_embedder_override(FakeEmbedder(fail=True))
    try:
        res = search_documents(corpus["engine"], "duration")
    finally:
        embedding.set_embedder_override(FakeEmbedder())
    assert res.mode == "fts_only" and res.vector_hits == 0 and res.passages
    assert all(p.vector_rank is None for p in res.passages)


def test_stopword_query_uses_vectors_only(corpus):
    res = search_documents(corpus["engine"], "the")
    assert res.fts_hits == 0 and res.vector_hits > 0 and res.passages
