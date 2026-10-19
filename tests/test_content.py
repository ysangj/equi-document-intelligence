"""MIME detection and content handling (FR-4, FR-5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.extraction import content as c
from tests.ingest_helpers import EML_HTML, EML_PLAIN, minimal_pdf

SAMPLE = Path(__file__).resolve().parent.parent / "sample_docs"


@pytest.mark.parametrize("name,mime", [
    ("a.pdf", "application/pdf"), ("A.PDF", "application/pdf"), ("x.html", "text/html"), ("x.htm", "text/html"),
    ("m.eml", "message/rfc822"), ("d.csv", "text/csv"),
])
def test_supported_mime_types(name, mime):
    assert c.mime_for_filename(name) == mime
    assert c.is_supported(mime)


@pytest.mark.parametrize("name", ["notes.txt", "sheet.xlsx", "image.png", "README.md", "noext", "deck.pptx"])
def test_other_files_are_unsupported(name):
    assert not c.is_supported(c.mime_for_filename(name))


def test_eml_html_body_is_extracted():
    e = c.parse_eml(EML_HTML)
    assert e.is_html and "<table>" in e.body and "plain version" not in e.body
    assert e.subject == "Fund update" and e.sender.startswith("IR")
    text = c.text_content_for(EML_HTML, c.EML)
    assert "Subject: Fund update" in text
    assert "Jan 2026\t+1.89%" in text
    assert "ignored()" not in text and "<td>" not in text


def test_eml_without_html_falls_back_to_plain_text():
    e = c.parse_eml(EML_PLAIN)
    assert not e.is_html
    assert "Rate sensitivity was reduced." in c.text_content_for(EML_PLAIN, c.EML)


def test_text_for_model_sends_eml_html_body_with_headers():
    t = c.text_for_model(EML_HTML, c.EML)
    assert t.startswith("Subject: Fund update")
    assert "<td>+1.89%</td>" in t  # HTML is sent as text (FR-5)


def test_sample_eml_bodies():
    for name in ("northwind_flash_update_jan2026.eml", "meridian_pm_note_feb2026.eml"):
        e = c.parse_eml((SAMPLE / name).read_bytes())
        assert e.is_html and e.body.strip()
    assert "+1.89%" in c.text_content_for((SAMPLE / "northwind_flash_update_jan2026.eml").read_bytes(), c.EML)


def test_html_to_text_layout_and_entities():
    out = c.html_to_text("<h1>Title</h1><p>A&amp;B&nbsp;fund</p><table><tr><td>Jan</td><td>1.2%</td></tr></table>"
                         "<style>p{}</style>")
    assert out.splitlines() == ["Title", "A&B fund", "Jan\t1.2%"]


def test_pdf_text_layer_and_page_separator():
    data = minimal_pdf("Net return 1.92%")
    assert "Net return 1.92%" in c.pdf_text_layer(data)
    kestrel = (SAMPLE / "kestrel_factsheet_2026-01.pdf").read_bytes()
    assert c.pdf_text_layer(kestrel).count(c.PAGE_SEPARATOR) == 1  # two pages


def test_scanned_pdf_uses_transcribed_pages():
    scanned = (SAMPLE / "northwind_dec25_scanned.pdf").read_bytes()
    assert c.pdf_text_layer(scanned) == ""
    assert c.text_content_for(scanned, c.PDF, ["page one ", "page two"]) == "page one\fpage two"
    assert c.text_content_for(minimal_pdf(""), c.PDF, None) == ""


def test_csv_and_html_text_content():
    assert c.text_content_for(b"a,b\r\n1,2\r\n", c.CSV) == "a,b\n1,2"
    assert c.text_content_for(b"\xef\xbb\xbfa,b", c.CSV) == "a,b"  # BOM stripped
    assert c.text_content_for(b"<p>Hi</p>", c.HTML) == "Hi"
    assert c.decode_text(b"caf\xe9") == "café"  # cp1252 fallback
