"""Shared builders for the T4 ingestion tests (not a test module)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.extraction.fixture import FIXTURE_FORMAT
from app.extraction.schema import (
    PROMPT_VERSION,
    Extraction,
    FundExtraction,
    PageText,
    ReturnEntry,
    SourcedValue,
    StatementExtraction,
)


def sv(value, page=1, snippet=None, confidence=0.9) -> SourcedValue:
    return SourcedValue(value=value, page=page, source_snippet=snippet if snippet is not None else str(value),
                        confidence=confidence)


def make_extraction(document_type="factsheet", *, fund="Test Fund", text="page one text",
                    statement=False) -> Extraction:
    funds = [] if statement else [
        FundExtraction(
            fund_name=sv(fund), manager=None, strategy=None, currency=sv("USD"), as_of_date=sv("Jan-26"), aum=None,
            returns=[ReturnEntry(period="Jan-26", value=sv("+2.84%"), return_type="MTD", net_gross="net")],
            other_metrics={"sharpe": sv("1.4")},
        )
    ]
    statements = [
        StatementExtraction(
            account_name=sv("Acme Family Office"), account_number_masked=sv("****4821"), custodian_or_manager=None,
            fund_name=None, currency=sv("USD"), period_start=None, period_end=sv("31 Jan 2026"),
            opening_balance=sv("12.40mm"), contributions=sv("0.50mm"), withdrawals=None, pnl=sv("0.18mm"),
            fees=None, closing_balance=sv("13.10mm"), holdings=[],
        )
    ] if statement else []
    return Extraction(
        document_type=document_type, document_type_confidence=0.95, funds=funds, statements=statements,
        pages=[PageText(page=1, text=text)], extra={},
    )


def write_fixture(fixtures_dir: Path, name: str, content: bytes, extraction: Extraction, *,
                  input_tokens=1234, output_tokens=567, mime="application/pdf") -> Path:
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    path = fixtures_dir / f"{name}.json"
    path.write_text(json.dumps({
        "fixture_format": FIXTURE_FORMAT,
        "source_file": name,
        "source_md5": hashlib.md5(content).hexdigest(),
        "mime_type": mime,
        "model": "claude-opus-5-5",
        "prompt_version": PROMPT_VERSION,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": 1000,
        "generated_at": "2026-10-16T00:00:00Z",
        "extraction": extraction.model_dump(mode="json"),
    }))
    return path


EML_HTML = b"""Content-Type: multipart/alternative; boundary="b1"
MIME-Version: 1.0
From: IR <ir@example.com>
Subject: Fund update
Date: Tue, 03 Feb 2026 14:05:00 +0000

--b1
Content-Type: text/plain; charset="us-ascii"

plain version
--b1
Content-Type: text/html; charset="us-ascii"

<html><body><p>Dear Investor,</p><table><tr><th>Period</th><th>Net</th></tr>
<tr><td>Jan 2026</td><td>+1.89%</td></tr></table><script>ignored()</script></body></html>
--b1--
"""

EML_PLAIN = b"""Content-Type: text/plain; charset="utf-8"
MIME-Version: 1.0
From: PM <pm@example.com>
Subject: Note

Rate sensitivity was reduced.
"""


def minimal_pdf(text: str = "Hello PDF") -> bytes:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data
