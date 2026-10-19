"""ClaudeExtractor request builder and response handling, with the client mocked."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.extraction import claude as cl
from app.extraction.base import PermanentExtractionError, TransientExtractionError
from app.extraction.prompt import SYSTEM_PROMPT
from app.extraction.schema import PROMPT_VERSION, Extraction
from tests.ingest_helpers import EML_HTML, minimal_pdf
from tests.test_extraction_schema import _wire_payload

MODEL = "claude-opus-5-5"


def test_pdf_goes_as_native_base64_document_block():
    pdf = minimal_pdf("hello")
    req = cl.build_request(MODEL, pdf, "application/pdf", "f.pdf")
    blocks = req["messages"][0]["content"]
    assert blocks[0]["type"] == "document"
    assert blocks[0]["source"]["media_type"] == "application/pdf"
    assert base64.standard_b64decode(blocks[0]["source"]["data"]) == pdf
    assert "\n" not in blocks[0]["source"]["data"]
    assert blocks[-1]["type"] == "text" and "f.pdf" in blocks[-1]["text"]


@pytest.mark.parametrize("mime,content,needle", [
    ("text/csv", b"Fund,Return\nA,0.0071\n", "A,0.0071"),
    ("text/html", b"<p>Net 1.2%</p>", "<p>Net 1.2%</p>"),
    ("message/rfc822", EML_HTML, "<td>+1.89%</td>"),
])
def test_text_documents_go_as_text(mime, content, needle):
    req = cl.build_request(MODEL, content, mime, "x")
    blocks = req["messages"][0]["content"]
    assert all(b["type"] == "text" for b in blocks)
    assert needle in blocks[0]["text"] and blocks[0]["text"].startswith("<document kind=")


def test_request_shape_single_call_structured_output():
    req = cl.build_request(MODEL, b"a,b\n", "text/csv")
    assert req["model"] == MODEL
    assert req["system"] == SYSTEM_PROMPT
    assert req["max_tokens"] == cl.MAX_TOKENS
    fmt = req["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert set(fmt["schema"]["properties"]) == {"document_type", "document_type_confidence", "funds",
                                                "statements", "pages", "extra"}
    assert req["output_config"]["effort"] == cl.EFFORT
    assert req["fallbacks"] == "default" and req["betas"] == [cl.FALLBACK_BETA]
    assert "tools" not in req and "tool_choice" not in req and "thinking" not in req


def test_prompt_covers_the_brief():
    p = SYSTEM_PROMPT
    for phrase in ('"unknown"', "account_statement", "transcription", "holdings", "fees", "(0.36)", "120bps",
                   "Jan-26", "source_snippet", "untrusted"):
        assert phrase in p, phrase


def test_prompt_rules_from_fixture_validation():
    """Rules added after validating fixtures against sample_docs/MANIFEST.md (T4b)."""
    p = " ".join(SYSTEM_PROMPT.split())
    for phrase in (
        "other_metrics.share_class",  # "X Fund Ltd (Class A USD)" -> fund_name "X Fund Ltd"
        "most complete form of the name",  # not a subject-line abbreviation
        "as_of_date: the date or period",  # year-less "Jan" columns need it
        "one entry per return type",  # "Q1 / YTD" -> QTD + YTD
        "period still in progress",  # mid-month running figures are not returns
        "extra.fees_components",  # fees = all fee/incentive lines
        'never add separators such as " | "',  # snippets must be verbatim
    ):
        assert phrase in p, phrase


def test_unsupported_mime_rejected():
    with pytest.raises(PermanentExtractionError):
        cl.build_request(MODEL, b"x", "image/png")


class FakeStream:
    def __init__(self, message=None, exc=None):
        self.message, self.exc = message, exc

    def __enter__(self):
        if self.exc:
            raise self.exc
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.message


class FakeClient:
    def __init__(self, message=None, exc=None):
        self.calls = []
        outer = self

        class _Msgs:
            def stream(self, **kw):
                outer.calls.append(kw)
                return FakeStream(message, exc)

        self.beta = SimpleNamespace(messages=_Msgs())


def _message(text, stop="end_turn", model=MODEL):
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
        stop_reason=stop, stop_details=None, model=model,
        usage=SimpleNamespace(input_tokens=1000, cache_creation_input_tokens=0, cache_read_input_tokens=200,
                              output_tokens=345),
    )


def test_extract_with_meta_parses_validates_and_reports_usage():
    client = FakeClient(_message(json.dumps(_wire_payload()), model="claude-opus-5-5"))
    ex = cl.ClaudeExtractor("k", MODEL, client=client)
    result = ex.extract_with_meta(b"a,b\n", "text/csv", name="d.csv")
    assert len(client.calls) == 1  # single call per document
    assert isinstance(result.extraction, Extraction)
    assert result.source == "claude" and result.model == MODEL and result.prompt_version == PROMPT_VERSION
    assert (result.input_tokens, result.output_tokens) == (1200, 345)
    assert ex.extract(b"a,b\n", "text/csv").document_type == "factsheet"  # PRD interface


def test_refusal_and_truncation_are_permanent():
    for stop in ("refusal", "max_tokens"):
        ex = cl.ClaudeExtractor("k", MODEL, client=FakeClient(_message("{}", stop=stop)))
        with pytest.raises(PermanentExtractionError):
            ex.extract(b"a\n", "text/csv")


def test_invalid_output_is_retryable_and_message_has_no_content():
    secret_value = "SECRET-CONTENT-12345"
    bad_json = cl.ClaudeExtractor("k", MODEL, client=FakeClient(_message('{"document_type": "' + secret_value)))
    with pytest.raises(TransientExtractionError) as e1:
        bad_json.extract(b"a\n", "text/csv")
    assert secret_value not in e1.value.message
    payload = _wire_payload()
    payload["funds"][0]["returns"][0]["return_type"] = secret_value
    bad_schema = cl.ClaudeExtractor("k", MODEL, client=FakeClient(_message(json.dumps(payload))))
    with pytest.raises(TransientExtractionError) as e2:
        bad_schema.extract(b"a\n", "text/csv")
    assert secret_value not in e2.value.message and "schema validation" in e2.value.message


def _resp(status, headers=None):
    return httpx2.Response(status, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"),
                           headers=headers or {})


@pytest.mark.parametrize("exc,retryable", [
    (anthropic.RateLimitError("m", response=_resp(429, {"retry-after": "7"}),
                              body={"error": {"type": "rate_limit_error", "message": "slow down"}}), True),
    (anthropic.InternalServerError("m", response=_resp(529), body={"error": {"type": "overloaded_error"}}), True),
    (anthropic.APIStatusError("m", response=_resp(500), body=None), True),
    (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")), True),
    (anthropic.APITimeoutError(request=httpx2.Request("POST", "https://x")), True),
    (anthropic.BadRequestError("m", response=_resp(400),
                               body={"error": {"type": "invalid_request_error",
                                               "message": "Your credit balance is too low"}}), False),
    (anthropic.AuthenticationError("m", response=_resp(401), body={"error": {"type": "authentication_error"}}), False),
])
def test_api_errors_are_classified(exc, retryable):
    ex = cl.ClaudeExtractor("sk-ant-test-key-0000", MODEL, client=FakeClient(exc=exc))
    with pytest.raises((TransientExtractionError, PermanentExtractionError)) as info:
        ex.extract(b"a\n", "text/csv")
    assert info.value.retryable is retryable
    assert "sk-ant-test-key-0000" not in info.value.message


def test_rate_limit_carries_retry_after_and_400_carries_api_message():
    m = cl.map_api_error(anthropic.RateLimitError("m", response=_resp(429, {"retry-after": "7"}),
                                                  body={"error": {"type": "rate_limit_error"}}))
    assert m.retry_after == 7.0 and m.message == "Anthropic API 429 rate_limit_error"
    m = cl.map_api_error(anthropic.BadRequestError(
        "m", response=_resp(400), body={"error": {"type": "invalid_request_error", "message": "credit balance low"}}))
    assert m.message == "Anthropic API 400 invalid_request_error: credit balance low"


def test_unknown_exceptions_propagate():
    ex = cl.ClaudeExtractor("k", MODEL, client=FakeClient(exc=KeyError("x")))
    with pytest.raises(KeyError):
        ex.extract(b"a\n", "text/csv")
