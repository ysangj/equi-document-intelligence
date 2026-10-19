"""GET /api/search (FR-10): grouped keyword results, safe highlights, minimum length,
FTS only (no synonyms, no LLM), visibility and latency."""

from __future__ import annotations

import statistics
import time

import pytest

from app.schemas import SearchResponse
from app.schemas.search import HEADLINE_START, HEADLINE_STOP, is_safe_headline
from tests.api_helpers import api_client, assert_error, exec_sql, ingest_into, q1, truncate, validate

EVIL = (f'Rate note <script>alert("x")</script> & <b>bold</b> it\'s fine, 2 < 3 > 1. {HEADLINE_START}fake{HEADLINE_STOP} '
        "We kept zircalloy exposure flat and zircalloy hedges in place.")


@pytest.fixture(scope="module")
def engine(test_engine, tmp_path_factory):
    ingest_into(test_engine, tmp_path_factory.mktemp("samples"))
    doc = q1(test_engine, """INSERT INTO documents (source, drive_file_id, name, mime_type, md5_checksum, status,
                             document_type) VALUES ('local', 'evil.html', 'evil <img> note.html', 'text/html', 'x',
                             'done', 'email') RETURNING id""")
    exec_sql(test_engine, "INSERT INTO document_chunks (document_id, page, chunk_index, text) VALUES (:d, 1, 0, :t)",
             d=doc, t=EVIL)
    gone = q1(test_engine, """INSERT INTO documents (source, drive_file_id, name, mime_type, md5_checksum, status)
                              VALUES ('local', 'gone.pdf', 'gone zircalloy.pdf', 'application/pdf', 'y', 'removed')
                              RETURNING id""")
    exec_sql(test_engine, "INSERT INTO document_chunks (document_id, page, chunk_index, text) VALUES (:d, 1, 0, :t)",
             d=gone, t="zircalloy from a removed document")
    yield test_engine
    truncate(test_engine)


@pytest.fixture(scope="module")
def client(engine):
    with api_client() as c:
        yield c


def search(client, q, **params):
    r = client.get("/api/search", params={"q": q, **params})
    assert r.status_code == 200, r.text
    validate(SearchResponse, r.json())
    return r.json()


def test_grouped_results(client):
    body = search(client, "meridian duration")
    g = body["groups"]
    assert body["query"] == "meridian duration" and body["min_query_length"] == 2
    assert [f["fund_name"] for f in g["funds"]] == ["Meridian Rates Relative Value Fund"]
    assert any(d["document_name"].startswith("meridian") for d in g["documents"])
    assert g["chunks"], "FTS hits expected"
    for ch in g["chunks"]:
        assert "<mark>" in ch["headline_html"] and is_safe_headline(ch["headline_html"])
        assert ch["rank"] > 0 and ch["web_view_link"] is None and ch["document_name"]
    ranks = [ch["rank"] for ch in g["chunks"]]
    assert ranks == sorted(ranks, reverse=True)
    assert body["total"] == sum(len(v) for v in g.values())


def test_duration_highlighted(client):
    chunks = search(client, "duration")["groups"]["chunks"]
    assert chunks and all("<mark>duration</mark>" in c["headline_html"].lower() for c in chunks)


def test_fund_alias_and_account_matches(client):
    funds = search(client, "Northwind Macro Fund Ltd")["groups"]["funds"]
    assert funds[0]["fund_name"] == "Northwind Global Macro Fund"
    assert funds[0]["matched_alias"] == "Northwind Macro Fund Ltd" and 0.3 <= funds[0]["score"] <= 1
    assert {"alias": "Northwind Macro Fund Ltd", "confirmed": False} in funds[0]["aliases"]
    tie = search(client, "Northwind")["groups"]["funds"][0]  # canonical and alias tie: no matched_alias...
    assert tie["matched_alias"] is None
    assert [a["alias"] for a in tie["aliases"]] == ["Northwind Macro Fund Ltd"]  # ...but the aliases are listed
    canonical = search(client, "Kestrel Quant")["groups"]["funds"]
    assert canonical[0]["fund_name"] == "Kestrel Quant Equity Fund LP" and canonical[0]["matched_alias"] is None
    acc = search(client, "Acme")["groups"]["accounts"]
    assert [a["account_name"] for a in acc] == ["Acme Family Office LLC"] and acc[0]["aliases"] == []
    assert search(client, "4471")["groups"]["accounts"][0]["account_number_masked"] == "XXXX-4471"


def test_headline_is_escaped(client):
    chunks = search(client, "zircalloy")["groups"]["chunks"]
    assert len(chunks) == 1  # the removed document's chunk is not searched
    h = chunks[0]["headline_html"]
    # ts_headline drops tag-like tokens itself; whatever text remains is escaped
    assert "<script" not in h and "<b>" not in h and "&lt;" in h and "&gt;" in h and "&amp;" in h
    assert "&quot;x&quot;" in h and "&#x27;" in h
    assert "<mark>zircalloy</mark>" in h and HEADLINE_START not in h and HEADLINE_STOP not in h
    assert "<mark>fake</mark>" not in h  # sentinels in the text never become markup
    assert is_safe_headline(h)
    docs = search(client, "evil note")["groups"]["documents"]
    assert docs and docs[0]["document_name"] == "evil <img> note.html"  # plain text in JSON; UI escapes


@pytest.mark.parametrize("q", ["", " ", "d", "  d  ", "\t"])
def test_short_queries_return_empty_groups(client, q):
    body = search(client, q)
    assert body["groups"] == {"funds": [], "accounts": [], "documents": [], "chunks": []}
    assert body["total"] == 0 and body["took_ms"] == 0 and body["query"] == q.strip()


def test_missing_q_and_limits(client):
    r = client.get("/api/search")
    assert r.status_code == 200 and r.json()["total"] == 0
    assert search(client, " du ")["query"] == "du"
    assert len(search(client, "duration", limit=1)["groups"]["chunks"]) == 1
    assert_error(client.get("/api/search?q=duration&limit=21"), 422, "validation_error", "query.limit")
    assert_error(client.get("/api/search?q=" + "a" * 201), 422, "validation_error", "query.q")


def test_fts_only_no_synonyms(client):
    """Search is keyword-only: "rate sensitivity" never matches a passage that only says
    "duration" (that is Ask's hybrid retrieval, §8)."""
    sens = search(client, "rate sensitivity")["groups"]["chunks"]
    dur = search(client, "duration")["groups"]["chunks"]
    for c in sens:
        assert "<mark>" in c["headline_html"]
        assert "sensitiv" in c["headline_html"].lower() or "rate" in c["headline_html"].lower()
    only_duration = {c["chunk_id"] for c in dur} - {c["chunk_id"] for c in sens}
    assert only_duration, "some passages mention duration but not rate sensitivity"


def test_websearch_syntax_and_stopwords(client):
    assert search(client, "the and of")["groups"]["chunks"] == []
    phrase = search(client, '"spread duration"')["groups"]["chunks"]
    assert phrase and all("spread" in c["headline_html"].lower() for c in phrase)
    excl = search(client, "duration -spread", limit=20)["groups"]["chunks"]
    assert {c["chunk_id"] for c in excl}.isdisjoint({c["chunk_id"] for c in phrase})
    search(client, "duration or (zircalloy")  # odd syntax never errors


def test_latency_under_300ms(client):
    """§12: search results < 300 ms (fixture-sized data)."""
    took, wall = [], []
    for q in ["duration", "meridian duration", "kestrel", "rate sensitivity", "northwind macro", "acme 4471"]:
        for _ in range(3):
            t = time.perf_counter()
            took.append(search(client, q)["took_ms"])
            wall.append((time.perf_counter() - t) * 1000)
    assert statistics.median(took) < 300 and max(took) < 300
    assert statistics.median(wall) < 300


# ---- name matching noise (Q2: "duration" returned Birchwood Foundation) ------------------------------------------


def test_common_word_does_not_fuzzy_match_unrelated_names(client):
    g = search(client, "duration")["groups"]
    assert g["accounts"] == []  # "duration" vs "Birchwood Foundation" is word_similarity 0.44
    assert not any("birchwood" in d["document_name"] for d in g["documents"])
    assert g["funds"] == []
    assert g["chunks"]  # the passages still match


@pytest.mark.parametrize("q,fund", [
    ("Northwind", "Northwind Global Macro Fund"),
    ("northwind", "Northwind Global Macro Fund"),
    ("Kestrel", "Kestrel Quant Equity Fund LP"),
    ("kestrel quant", "Kestrel Quant Equity Fund LP"),
    ("Alder", "Alder Asia Fund"),
    ("alder dubai", "Alder Dubai Fund"),
    ("north", "Northwind Global Macro Fund"),  # a word prefix
    ("nortwind", "Northwind Global Macro Fund"),  # a typo still matches fuzzily
    ("meridian duration", "Meridian Rates Relative Value Fund"),  # name word + topic word
])
def test_fund_names_still_match(client, q, fund):
    funds = search(client, q)["groups"]["funds"]
    assert funds and funds[0]["fund_name"] == fund, [f["fund_name"] for f in funds]


def test_alder_matches_both_alder_funds_and_their_documents(client):
    g = search(client, "Alder")["groups"]
    assert {"Alder Asia Fund", "Alder Dubai Fund"} <= {f["fund_name"] for f in g["funds"]}
    assert g["documents"] and all(d["document_name"].startswith("alder") for d in g["documents"])


def test_account_and_document_names_still_match(client):
    assert [a["account_name"] for a in search(client, "Acme")["groups"]["accounts"]] == ["Acme Family Office LLC"]
    assert [a["account_name"] for a in search(client, "birchwood")["groups"]["accounts"]] == ["Birchwood Foundation"]
    assert any(d["document_name"].startswith("birchwood") for d in search(client, "foundation")["groups"]["documents"])
    assert search(client, "kestrel factsheet")["groups"]["documents"][0]["document_name"].startswith("kestrel_factsheet")


def test_short_fragments_match_by_prefix_only(client):
    g = search(client, "ac")["groups"]  # 2-3 characters: never fuzzy
    assert [a["account_name"] for a in g["accounts"]] == ["Acme Family Office LLC"]
    assert search(client, "zq")["total"] == 0


def test_query_words():
    from app.api.search import query_words

    assert query_words("Kestrel Quant") == ["kestrel", "quant"]
    assert query_words("fund performance in q4") == ["fund", "performance", "q4"]  # "in" dropped
    assert query_words("ac") == ["ac"]  # a single short word is kept
    assert query_words("--") == []
