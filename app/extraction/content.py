"""Deterministic content handling shared by the sources, extractors and pipeline.

- MIME detection for local files (FR-4 supported types; everything else `unsupported`).
- `.eml` (message/rfc822): pull out the HTML body (or plain text if there is no HTML).
- HTML -> plain text for `documents.text_content`.
- PDF text layer via PyMuPDF (`""` when the PDF is image-only, e.g. a scan).

Page separator: multi-page text is joined with a form feed (`PAGE_SEPARATOR`), so
`text_content.split("\\f")` gives one string per page (T5 chunking relies on this).
"""

from __future__ import annotations

import email
import email.policy
import html
import re
from html.parser import HTMLParser
from pathlib import PurePath

PDF = "application/pdf"
HTML = "text/html"
EML = "message/rfc822"
CSV = "text/csv"
SUPPORTED_MIME_TYPES = frozenset({PDF, HTML, EML, CSV})
PAGE_SEPARATOR = "\f"

_EXTENSION_MIME = {
    ".pdf": PDF,
    ".html": HTML,
    ".htm": HTML,
    ".eml": EML,
    ".csv": CSV,
}


def mime_for_filename(name: str) -> str:
    """MIME type from the extension. Unknown extensions get a generic type, which is
    not in SUPPORTED_MIME_TYPES and so is recorded as `unsupported`."""
    ext = PurePath(name).suffix.lower()
    if ext in _EXTENSION_MIME:
        return _EXTENSION_MIME[ext]
    import mimetypes

    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


def is_supported(mime: str) -> bool:
    return mime in SUPPORTED_MIME_TYPES


# ---- email ----------------------------------------------------------------------------


class EmailBody:
    __slots__ = ("subject", "sender", "date", "body", "is_html")

    def __init__(self, subject: str | None, sender: str | None, date: str | None, body: str, is_html: bool):
        self.subject, self.sender, self.date, self.body, self.is_html = subject, sender, date, body, is_html

    def header_lines(self) -> str:
        parts = [f"{k}: {v}" for k, v in (("Subject", self.subject), ("From", self.sender), ("Date", self.date)) if v]
        return "\n".join(parts)


def parse_eml(content: bytes) -> EmailBody:
    """Headers plus the HTML body (preferred) or plain-text body of an RFC 822 message."""
    msg = email.message_from_bytes(content, policy=email.policy.default)
    part = msg.get_body(preferencelist=("html", "plain"))
    body, is_html = "", False
    if part is not None:
        try:
            body = part.get_content()
        except (LookupError, UnicodeDecodeError):
            payload = part.get_payload(decode=True) or b""
            body = payload.decode("utf-8", errors="replace")
        is_html = part.get_content_subtype() == "html"
    return EmailBody(
        subject=str(msg["subject"]) if msg["subject"] else None,
        sender=str(msg["from"]) if msg["from"] else None,
        date=str(msg["date"]) if msg["date"] else None,
        body=body,
        is_html=is_html,
    )


# ---- html -----------------------------------------------------------------------------

_BLOCK_TAGS = {
    "p", "div", "br", "tr", "li", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "header", "footer", "blockquote", "pre", "hr", "thead", "tbody",
}
_CELL_TAGS = {"td", "th"}
_SKIP_TAGS = {"script", "style", "head", "title"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.out.append("\n")
        elif tag in _CELL_TAGS:
            self.out.append("\t")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.out.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.out.append(data)


def html_to_text(markup: str) -> str:
    """Readable plain text: block elements become lines, table cells are tab-separated,
    whitespace is collapsed. Entities are decoded."""
    p = _TextExtractor()
    p.feed(markup)
    p.close()
    raw = html.unescape("".join(p.out))
    lines = []
    for line in raw.split("\n"):
        cells = [re.sub(r"[ \r ]+", " ", c).strip() for c in line.split("\t")]
        joined = "\t".join(c for c in cells if c)
        if joined:
            lines.append(joined)
    return "\n".join(lines)


# ---- pdf ------------------------------------------------------------------------------


def pdf_page_texts(content: bytes) -> list[str]:
    """Text layer per page (empty strings for image-only pages)."""
    import pymupdf

    with pymupdf.open(stream=content, filetype="pdf") as doc:
        return [page.get_text() for page in doc]


def pdf_text_layer(content: bytes) -> str:
    """Joined text layer, or "" when no page has text (scanned PDF)."""
    pages = pdf_page_texts(content)
    if not any(p.strip() for p in pages):
        return ""
    return PAGE_SEPARATOR.join(p.strip() for p in pages)


def decode_text(content: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


# ---- what the extractor sees / what we persist -------------------------------------------


def text_for_model(content: bytes, mime: str) -> str:
    """Text payload for non-PDF documents (HTML and CSV are sent as text, FR-5).
    For .eml this is the headers plus the HTML body."""
    if mime == EML:
        e = parse_eml(content)
        return f"{e.header_lines()}\n\n{e.body}".strip()
    return decode_text(content)


def text_content_for(content: bytes, mime: str, transcribed_pages: list[str] | None = None) -> str:
    """`documents.text_content`: PDF text layer if present, otherwise the extractor's
    per-page transcription; HTML (incl. the .eml body) as plain text; CSV as is."""
    return text_content_and_source(content, mime, transcribed_pages)[0]


def text_content_and_source(content: bytes, mime: str, transcribed_pages: list[str] | None = None) -> tuple[str, str]:
    """(`documents.text_content`, `documents.text_source`). The source is 'file' when
    the text is the file's own, 'transcription' when it is the extractor's (a PDF with
    no text layer). T5 verifies snippets only against 'file' text."""
    if mime == PDF:
        layer = pdf_text_layer(content)
        if layer:
            return layer, "file"
        return PAGE_SEPARATOR.join(t.strip() for t in (transcribed_pages or [])), "transcription"
    return _non_pdf_text(content, mime), "file"


def _non_pdf_text(content: bytes, mime: str) -> str:
    if mime == EML:
        e = parse_eml(content)
        body = html_to_text(e.body) if e.is_html else e.body.strip()
        head = e.header_lines()
        return f"{head}\n\n{body}".strip() if head else body
    if mime == HTML:
        return html_to_text(decode_text(content))
    return decode_text(content).replace("\r\n", "\n").strip()
