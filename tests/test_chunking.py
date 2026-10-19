"""Unit tests: chunker (FR-7, §14): pages, paragraphs, ~500 tokens with ~50 overlap,
CSV row groups with the header repeated."""

from __future__ import annotations

from app.search.chunking import (
    OVERLAP_TOKENS,
    TARGET_TOKENS,
    chunk_csv,
    chunk_document,
    chunk_page_text,
    chunk_pages,
    estimate_tokens,
)


def words(n: int, prefix="w") -> str:
    return " ".join(f"{prefix}{i}" for i in range(n))


def test_estimate_tokens_counts_words_and_punctuation():
    assert estimate_tokens("Rates were volatile, into the FOMC.") == 8
    assert estimate_tokens("") == 0


def test_chunks_never_span_pages_and_keep_page_numbers():
    chunks = chunk_pages(["Page one text.", "", "Page three text."])
    assert [(c.page, c.chunk_index, c.text) for c in chunks] == [(1, 0, "Page one text."), (3, 1, "Page three text.")]


def test_short_page_is_one_chunk_with_paragraphs_kept():
    page = "Commentary\n\nWe reduced duration.\n\nWe remain cautious."
    assert chunk_page_text(page) == ["Commentary\n\nWe reduced duration.\n\nWe remain cautious."]


def test_paragraphs_packed_up_to_target_with_overlap():
    paras = [words(300, f"p{i}_") for i in range(3)]  # 3 x 300 tokens: each pair exceeds 500
    chunks = chunk_page_text("\n\n".join(paras))
    assert len(chunks) == 3
    # two 200-token paragraphs fit in one chunk with the overlap of the previous one
    assert len(chunk_page_text("\n\n".join(words(200, f"q{i}_") for i in range(4)))) == 2
    assert all(estimate_tokens(c) <= TARGET_TOKENS + OVERLAP_TOKENS for c in chunks)
    # chunk 2 starts with the last ~50 tokens of chunk 1
    tail = chunks[0].split()[-OVERLAP_TOKENS:]
    assert chunks[1].split()[:OVERLAP_TOKENS] == tail
    # nothing lost: every paragraph word appears somewhere
    joined = " ".join(chunks)
    assert all(w in joined for p in paras for w in p.split()[::37])


def test_long_paragraph_split_at_sentences_then_words():
    sentences = ". ".join(words(60, f"s{i}_") for i in range(20)) + "."  # one 1200+ token paragraph
    chunks = chunk_page_text(sentences)
    assert len(chunks) >= 3 and all(estimate_tokens(c) <= TARGET_TOKENS + OVERLAP_TOKENS for c in chunks)
    giant = words(1300)  # one sentence, no breaks: word windows with overlap
    chunks = chunk_page_text(giant)
    assert len(chunks) >= 3
    assert chunks[0].split()[-OVERLAP_TOKENS:] == chunks[1].split()[:OVERLAP_TOKENS]
    assert set(" ".join(chunks).split()) == set(giant.split())


def test_csv_row_groups_repeat_header():
    header = "Fund,Month End,Return (Net) %,Return (Gross) %,Notes"
    rows = [f"Meridian Rates RV Fund,{m}/28/2026,0.{m}1,0.{m}2," for m in range(1, 10)]
    small = chunk_csv("\n".join([header, *rows]))
    assert len(small) == 1 and small[0].text.splitlines()[0] == header and small[0].page is None
    many = [f"HCO-A,Harbor Credit Opps Fund,A,2026-{i:02d},1211.03,0.0071,926915118" for i in range(200)]
    chunks = chunk_csv("\n".join([header, *many]), target=120)
    assert len(chunks) > 5
    assert all(c.text.splitlines()[0] == header for c in chunks)
    body = [ln for c in chunks for ln in c.text.splitlines()[1:]]
    assert body == many  # every row exactly once, in order
    assert all(estimate_tokens(c.text) <= 120 for c in chunks)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_csv_edge_cases():
    assert chunk_csv("") == []
    assert [c.text for c in chunk_csv("only,header\n")] == ["only,header"]
    assert chunk_csv("a,b\r\n1,2\r\n")[0].text == "a,b\n1,2"


def test_chunk_document_sources():
    # PDF/HTML text: pages from text_content ("\f"-separated)
    chunks = chunk_document("first page\fsecond page", "application/pdf", ["ignored"])
    assert [(c.page, c.text) for c in chunks] == [(1, "first page"), (2, "second page")]
    # no text at all: the extractor's transcription
    chunks = chunk_document("", "application/pdf", ["scanned page one", "scanned page two"])
    assert [(c.page, c.text) for c in chunks] == [(1, "scanned page one"), (2, "scanned page two")]
    # CSV: row groups, page None
    chunks = chunk_document("a,b\n1,2", "text/csv")
    assert chunks[0].page is None and chunks[0].text == "a,b\n1,2"
    assert chunk_document(None, "text/html") == []
