"""Contract tests (T3): docs/progress.md -> API Contract <-> app/schemas.

No DB or running stack is needed. What it checks:
- every JSON example in the contract is tagged with a model, validates, and round-trips unchanged
- every §10 endpoint in the PRD has a documented heading with a status
- the schema enums match app.models (the single source of truth for the DB)
- the query/request validators and the safety helpers behave as documented
"""

from __future__ import annotations

import json
import re
import typing
from pathlib import Path
from typing import Annotated

import pytest
from fastapi import FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from app import models
from app.schemas import (
    AskRequest,
    AskResponse,
    DocumentsQuery,
    FundFactsQuery,
    HoldingsQuery,
    ReturnsQuery,
    ReviewActionRequest,
    ReviewItem,
    ReviewQuery,
    SearchQuery,
    StatementsQuery,
    example_models,
)
from app.schemas import common, data, review, search
from app.schemas.ask import CITATION_MARKER_RE
from app.schemas.search import headline_to_html, is_safe_headline

ROOT = Path(__file__).resolve().parent.parent
PROGRESS = ROOT / "docs" / "progress.md"
PRD = ROOT / "PRD-document-intelligence.md"

TAG_RE = re.compile(r"<!--\s*schema:\s*(\w+)\s*-->\s*\n```json\n(.*?)\n```", re.S)
JSON_BLOCK_RE = re.compile(r"```json\n(.*?)\n```", re.S)
HEADING_RE = re.compile(r"^### (GET|POST|PUT|PATCH|DELETE) (/api/\S+)\s+\((planned|implemented)\b", re.M)


def contract_section() -> str:
    text = PROGRESS.read_text()
    start = text.index("## API Contract\n")
    end = text.index("\n## ", start + 1)
    return text[start:end]


def tagged_examples() -> list[tuple[str, str, int]]:
    sec = contract_section()
    return [(m.group(1), m.group(2), sec[: m.start()].count("\n") + 1) for m in TAG_RE.finditer(sec)]


EXAMPLES = tagged_examples()


# ---- documented examples ------------------------------------------------------------


def test_contract_has_examples():
    assert len(EXAMPLES) >= 30


def test_every_json_block_is_tagged():
    sec = contract_section()
    assert len(JSON_BLOCK_RE.findall(sec)) == len(EXAMPLES), "every ```json block needs a <!-- schema: Name --> tag"


def test_every_tag_names_a_known_model():
    known = example_models()
    unknown = sorted({name for name, _, _ in EXAMPLES} - set(known))
    assert not unknown


@pytest.mark.parametrize(("name", "body", "line"), EXAMPLES, ids=[f"{n}@L{ln}" for n, _, ln in EXAMPLES])
def test_example_validates_and_round_trips(name, body, line):
    adapter: TypeAdapter = example_models()[name]
    raw = json.loads(body)
    obj = adapter.validate_python(raw)
    # exclude_unset: request examples may omit optional fields; responses have no defaults.
    assert adapter.dump_python(obj, mode="json", exclude_unset=True) == raw


def test_response_examples_cover_every_documented_response_model():
    used = {name for name, _, _ in EXAMPLES}
    expected = set(example_models()) - {"Citation", "HealthResponse"}  # health is inline; Citation is in AskResponse
    assert expected <= used, f"no example for: {sorted(expected - used)}"


def test_no_tbd_in_contract():
    assert "TBD" not in contract_section()


# ---- endpoint coverage --------------------------------------------------------------


def prd_endpoints() -> set[tuple[str, str]]:
    prd = PRD.read_text()
    sec = prd[prd.index("## 10. API Summary") : prd.index("## 11.")]
    rows = re.findall(r"^\| (GET|POST|PUT|PATCH|DELETE) \| `([^`]+)` \|", sec, re.M)
    return {(m, p.split("?")[0]) for m, p in rows}


def test_prd_table_parsed():
    assert len(prd_endpoints()) == 24


def test_every_prd_endpoint_is_documented_with_status():
    documented = {(m, p) for m, p, _ in HEADING_RE.findall(contract_section())}
    missing = prd_endpoints() - documented
    assert not missing, f"undocumented endpoints: {sorted(missing)}"
    assert not documented - prd_endpoints(), "contract documents endpoints that are not in §10"


# ---- enums match the DB constants -----------------------------------------------------


@pytest.mark.parametrize(
    ("literal", "expected"),
    [
        (common.DocumentType, models.DOCUMENT_TYPES),
        (common.DocumentSource, models.DOCUMENT_SOURCES),
        (common.ValueStatus, models.VALUE_STATUSES),
        (common.ReturnType, models.RETURN_TYPES),
        (common.NetGross, models.NET_GROSS),
        (common.ReviewReason, models.REVIEW_REASONS),
        (common.AliasMatchMethod, models.ALIAS_MATCH_METHODS),
        (common.ExtractionSource, models.EXTRACTION_SOURCES),
        (review.ReviewTargetTable, models.REVIEW_TARGET_TABLES),
    ],
)
def test_enum_matches_models(literal, expected):
    assert typing.get_args(literal) == expected


def test_document_status_is_db_statuses_minus_removed():
    assert typing.get_args(common.DocumentStatus) == tuple(s for s in models.DOCUMENT_STATUSES if s != "removed")


def test_every_reason_has_label_and_detail_model():
    assert set(common.REVIEW_REASON_LABELS) == set(models.REVIEW_REASONS)
    assert set(review.REVIEW_DETAIL_MODELS) == set(models.REVIEW_REASONS)


def test_statement_fields_and_provenance_keys():
    fields = typing.get_args(data.StatementField)
    assert set(data.STATEMENT_PROVENANCE_KEYS) == set(fields)
    assert set(data.SOURCE_FIELDS["statements"]) == set(fields)
    assert data.STATEMENT_PROVENANCE_KEYS["custodian"] == "custodian_or_manager"
    assert set(data.SOURCE_FIELDS) == set(typing.get_args(data.SourceTable))


def test_sortable_columns_exist_on_items():
    pairs = [
        (ReturnsQuery, data.ReturnItem),
        (FundFactsQuery, data.FundFactItem),
        (StatementsQuery, data.StatementItem),
        (HoldingsQuery, data.HoldingItem),
    ]
    for q, item in pairs:
        assert set(q.SORTABLE) <= set(item.model_fields), q.__name__
    from app.schemas.ingestion import DocumentItem

    assert set(DocumentsQuery.SORTABLE) <= set(DocumentItem.model_fields)


# ---- query params ----------------------------------------------------------------------


def test_list_query_defaults():
    q = ReturnsQuery()
    assert (q.page, q.page_size, q.sort, q.format) == (1, 50, "-period_end,fund_name", "json")
    assert q.sort_keys() == [("period_end", True), ("fund_name", False)]


def test_sort_normalized_and_validated():
    assert ReturnsQuery(sort=" -value_pct , fund_name ").sort == "-value_pct,fund_name"
    assert ReturnsQuery(sort="").sort == "-period_end,fund_name"
    for bad in ("colour", "value_pct,-value_pct", "fund_name,", "-"):
        with pytest.raises(ValidationError):
            ReturnsQuery(sort=bad)


@pytest.mark.parametrize("size,ok", [(1, True), (500, True), (0, False), (501, False)])
def test_page_size_bounds(size, ok):
    if ok:
        assert ReturnsQuery(page_size=size).page_size == size
    else:
        with pytest.raises(ValidationError):
            ReturnsQuery(page_size=size)


def test_page_is_one_based():
    with pytest.raises(ValidationError):
        StatementsQuery(page=0)


def test_date_range_order():
    assert ReturnsQuery(date_from="2026-01-01", date_to="2026-01-01").date_to.isoformat() == "2026-01-01"
    with pytest.raises(ValidationError):
        StatementsQuery(date_from="2026-02-01", date_to="2026-01-31")


def test_filters_are_lists_and_enums_checked():
    q = ReturnsQuery(fund_id=[1, 3], status=["ok", "conflict"], return_type=["MTD"], net_gross=["net"])
    assert q.fund_id == [1, 3]
    with pytest.raises(ValidationError):
        ReturnsQuery(status=["rejected"])
    with pytest.raises(ValidationError):
        ReturnsQuery(document_type=["memo"])


def test_unknown_query_param_rejected():
    with pytest.raises(ValidationError):
        ReturnsQuery(account_id=[1])  # account_id is a statements filter
    with pytest.raises(ValidationError):
        DocumentsQuery(status=["removed"])


def test_csv_format_flag():
    assert FundFactsQuery(format="csv").format == "csv"
    with pytest.raises(ValidationError):
        FundFactsQuery(format="xlsx")


def test_fastapi_query_model_parses_repeated_keys():
    app = FastAPI()

    @app.get("/r")
    def r(q: Annotated[ReturnsQuery, Query()]):
        return {"fund_id": q.fund_id, "sort": q.sort, "status": q.status}

    c = TestClient(app)
    assert c.get("/r?fund_id=1&fund_id=3&status=ok&sort=-value_pct").json() == {
        "fund_id": [1, 3], "sort": "-value_pct", "status": ["ok"],
    }
    assert c.get("/r?sort=nope").status_code == 422
    assert c.get("/r?bogus=1").status_code == 422


def test_review_and_search_query_defaults():
    assert ReviewQuery().sort == "-created_at"
    s = SearchQuery()
    assert (s.q, s.limit) == ("", 5)
    with pytest.raises(ValidationError):
        SearchQuery(q="x" * 201)
    with pytest.raises(ValidationError):
        SearchQuery(limit=21)


# ---- search highlight -------------------------------------------------------------------


def test_headline_to_html_escapes_and_marks():
    raw = f'a <b>&"x\' {search.HEADLINE_START}duration{search.HEADLINE_STOP} end'
    out = headline_to_html(raw)
    assert out == "a &lt;b&gt;&amp;&quot;x&#x27; <mark>duration</mark> end"
    assert is_safe_headline(out)


def test_headline_to_html_unbalanced_sentinels():
    s, e = search.HEADLINE_START, search.HEADLINE_STOP
    assert headline_to_html(f"{s}a{s}b") == "<mark>ab</mark>"
    assert headline_to_html(f"x{e}y") == "xy"


@pytest.mark.parametrize(
    "s,ok",
    [
        ("plain", True),
        ("<mark>a</mark> b <mark>c</mark>", True),
        ("<mark>a", False),
        ("a</mark>", False),
        ("<mark><mark>a</mark></mark>", False),
        ('<mark class="x">a</mark>', False),
        ("<script>alert(1)</script>", False),
        ("a > b", False),
    ],
)
def test_is_safe_headline(s, ok):
    assert is_safe_headline(s) is ok


def test_chunk_hit_rejects_unsafe_headline():
    base = {
        "chunk_id": 1, "document_id": 1, "document_name": "d.pdf", "document_type": None, "page": 1,
        "chunk_index": 0, "rank": 0.1, "web_view_link": None,
    }
    search.ChunkHit(**base, headline_html="<mark>ok</mark>")
    with pytest.raises(ValidationError):
        search.ChunkHit(**base, headline_html="<img src=x onerror=alert(1)>")


# ---- ask ----------------------------------------------------------------------------------


def _ask_example() -> dict:
    return json.loads(next(b for n, b, _ in EXAMPLES if n == "AskResponse"))


def test_ask_markers_resolve():
    ex = _ask_example()
    ids = {c["id"] for c in ex["citations"]}
    assert {int(m) for m in CITATION_MARKER_RE.findall(ex["answer"])} == ids


def test_ask_rejects_dangling_marker():
    ex = _ask_example()
    ex["answer"] += " [3]"
    with pytest.raises(ValidationError):
        AskResponse.model_validate(ex)


def test_ask_rejects_unverified_number_not_in_answer():
    ex = _ask_example()
    ex["unverified_numbers"] = ["9.99"]
    with pytest.raises(ValidationError):
        AskResponse.model_validate(ex)


def test_ask_rejects_bad_citation_numbering_and_row_shape():
    ex = _ask_example()
    ex["citations"][0]["id"] = 5
    with pytest.raises(ValidationError):
        AskResponse.model_validate(ex)
    ex = _ask_example()
    ex["citations"][0]["rows"][0] = ["only one"]
    with pytest.raises(ValidationError):
        AskResponse.model_validate(ex)


def test_ask_request_validation():
    assert AskRequest(question="  Which fund?  ").question == "Which fund?"
    assert AskRequest(question="q").history == []
    for bad in ({"question": "   "}, {"question": ""}, {"question": "q", "history": [{"role": "system", "content": "x"}]}):
        with pytest.raises(ValidationError):
            AskRequest.model_validate(bad)


# ---- review --------------------------------------------------------------------------------

ACTION = TypeAdapter(ReviewActionRequest)


def test_review_action_discriminator():
    assert type(ACTION.validate_python({"action": "choose", "chosen_id": 214})).__name__ == "ChooseAction"
    with pytest.raises(ValidationError):
        ACTION.validate_python({"action": "delete"})
    with pytest.raises(ValidationError):
        ACTION.validate_python({"action": "choose"})  # chosen_id required


@pytest.mark.parametrize(
    "body",
    [
        {"action": "edit"},
        {"action": "edit", "value": 1.0, "value_pct": 1.0},
    ],
)
def test_edit_requires_exactly_one_value(body):
    with pytest.raises(ValidationError):
        ACTION.validate_python(body)


def _review_item() -> dict:
    return json.loads(next(b for n, b, _ in EXAMPLES if n == "ReviewItem"))


def test_review_item_id_scheme_enforced():
    item = _review_item()
    item["id"] = "statements-12"
    with pytest.raises(ValidationError):
        ReviewItem.model_validate(item)


def test_review_item_allowed_actions_follow_kind():
    item = _review_item()
    item["allowed_actions"] = ["choose", "edit"]
    with pytest.raises(ValidationError):
        ReviewItem.model_validate(item)


def test_review_item_detail_shape_enforced():
    item = _review_item()
    item["detail"] = {"rollforward_mismatch": {"opening_balance": 1.0}}
    with pytest.raises(ValidationError):
        ReviewItem.model_validate(item)
    item = _review_item()
    item["detail"] = {"made_up_reason": {}}
    with pytest.raises(ValidationError):
        ReviewItem.model_validate(item)


def test_conflict_item_needs_two_candidates():
    lst = json.loads(next(b for n, b, _ in EXAMPLES if n == "ReviewListResponse"))
    conflict = next(i for i in lst["items"] if i["kind"] == "conflict")
    conflict["candidates"] = conflict["candidates"][:1]
    with pytest.raises(ValidationError):
        ReviewItem.model_validate(conflict)


def test_response_models_forbid_extra_fields():
    item = _review_item()
    item["surprise"] = 1
    with pytest.raises(ValidationError):
        ReviewItem.model_validate(item)


def test_allowed_actions_cover_action_enum():
    acts = {a for v in review.ALLOWED_ACTIONS.values() for a in v}
    assert acts == set(typing.get_args(review.ReviewActionName))
