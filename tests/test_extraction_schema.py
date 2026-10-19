"""§9 canonical schema, wire schema and conversion (T4)."""

from __future__ import annotations

import typing

import pytest
from pydantic import ValidationError

from app.extraction import schema as sch
from tests.ingest_helpers import make_extraction

# The §9 field lists, verbatim.
SECTION_9 = {
    "SourcedValue": ["value", "page", "source_snippet", "confidence"],
    "ReturnEntry": ["period", "value", "return_type", "net_gross"],
    "FundExtraction": ["fund_name", "manager", "strategy", "currency", "as_of_date", "aum", "returns", "other_metrics"],
    "Holding": ["name", "identifier", "quantity", "market_value", "weight"],
    "StatementExtraction": [
        "account_name", "account_number_masked", "custodian_or_manager", "fund_name", "currency", "period_start",
        "period_end", "opening_balance", "contributions", "withdrawals", "pnl", "fees", "closing_balance", "holdings",
    ],
    "PageText": ["page", "text"],
    "Extraction": ["document_type", "document_type_confidence", "funds", "statements", "pages", "extra"],
}


@pytest.mark.parametrize("model_name,fields", SECTION_9.items())
def test_models_have_exactly_the_section_9_fields(model_name, fields):
    assert list(getattr(sch, model_name).model_fields) == fields


def test_enums_match_section_9():
    assert typing.get_args(sch.ReturnType) == ("MTD", "QTD", "YTD", "ITD", "annualized", "unknown")
    assert typing.get_args(sch.NetGross) == ("net", "gross", "unknown")
    assert typing.get_args(sch.DocumentType) == ("factsheet", "account_statement", "performance_report", "email", "other")


def test_enums_match_db_constants():
    from app import models

    assert typing.get_args(sch.ReturnType) == models.RETURN_TYPES
    assert typing.get_args(sch.NetGross) == models.NET_GROSS
    assert typing.get_args(sch.DocumentType) == models.DOCUMENT_TYPES


def test_prompt_version_is_a_nonempty_string():
    assert isinstance(sch.PROMPT_VERSION, str) and sch.PROMPT_VERSION


def test_extraction_round_trips_and_rejects_unknown_fields():
    ex = make_extraction()
    again = sch.Extraction.model_validate(ex.model_dump(mode="json"))
    assert again == ex
    data = ex.model_dump(mode="json")
    data["surprise"] = 1
    with pytest.raises(ValidationError):
        sch.Extraction.model_validate(data)


def test_bad_enum_rejected():
    data = make_extraction().model_dump(mode="json")
    data["document_type"] = "invoice"
    with pytest.raises(ValidationError):
        sch.Extraction.model_validate(data)


def _walk(node, path="$"):
    if isinstance(node, dict):
        yield path, node
        for k, v in node.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}[{i}]")


def test_wire_schema_is_strict():
    s = sch.wire_json_schema()
    objects = [(p, n) for p, n in _walk(s) if n.get("type") == "object"]
    assert objects
    for path, node in objects:
        assert node.get("additionalProperties") is False, path
        assert sorted(node.get("required", [])) == sorted(node["properties"]), path
    # No dict-typed (free-form) objects and no unsupported numeric constraints.
    for path, node in _walk(s):
        assert "minimum" not in node and "maximum" not in node, path


def _wv(value=None, page=None, snippet=None, confidence=0.0):
    return {"value": value, "page": page, "source_snippet": snippet, "confidence": confidence}


def _wire_payload():
    empty = _wv()
    return {
        "document_type": "factsheet",
        "document_type_confidence": 1.4,  # clamped
        "funds": [{
            "fund_name": _wv("Kestrel Quant Equity Fund LP", 1, "Kestrel Quant Equity Fund LP", 0.98),
            "manager": empty, "strategy": _wv("Market neutral", 1, "Strategy: Market neutral", 0.9),
            "currency": empty, "as_of_date": _wv("Jan-26", 1, "As of Jan-26", 0.9), "aum": empty,
            "returns": [{"period": "Jan-26", "value": _wv("+2.84%", 1, "MTD % (net) +2.84%", 0.97),
                         "return_type": "MTD", "net_gross": "net"}],
            "other_metrics": [
                {"name": "sharpe", "value": _wv("1.42", 1, "Sharpe 1.42", 0.9)},
                {"name": "sharpe", "value": _wv("9.99", 1, "dup", 0.1)},
                {"name": "beta", "value": empty},
            ],
        }],
        "statements": [{
            "account_name": _wv("Acme", 1, "Account: Acme", 0.9),
            **{k: empty for k in ("account_number_masked", "custodian_or_manager", "fund_name", "currency",
                                  "period_start", "opening_balance", "contributions", "withdrawals", "pnl", "fees")},
            "period_end": _wv("31 Jan 2026", 1, "Period ending 31 Jan 2026", 0.9),
            "closing_balance": _wv("13.10mm", 1, "Closing 13.10mm", 0.9),
            "holdings": [{"name": _wv("UST 2y", 1, "UST 2y", 0.8), "identifier": empty, "quantity": empty,
                          "market_value": _wv("1.2mm", 1, "1.2mm", 0.8), "weight": empty}],
        }],
        "pages": [{"page": 2, "text": "second"}, {"page": 1, "text": "first"}],
        "extra": [{"key": "estimate", "value": "Jan is an estimate"}, {"key": "estimate", "value": "dup"}],
    }


def test_wire_to_extraction_converts_to_canonical_form():
    wire = sch.WireExtraction.model_validate(_wire_payload())
    ex = sch.wire_to_extraction(wire)
    assert isinstance(ex, sch.Extraction)
    assert ex.document_type_confidence == 1.0
    f = ex.funds[0]
    assert f.manager is None and f.currency is None and f.aum is None  # absent -> None
    assert f.strategy.value == "Market neutral"
    assert f.returns[0].value.value == "+2.84%"  # kept as written
    assert f.other_metrics == {"sharpe": sch.SourcedValue(value="1.42", page=1, source_snippet="Sharpe 1.42",
                                                          confidence=0.9)}
    st = ex.statements[0]
    assert st.closing_balance.value == "13.10mm" and st.fees is None
    assert st.holdings[0].market_value.value == "1.2mm" and st.holdings[0].identifier is None
    assert ex.extra == {"estimate": "Jan is an estimate"}
    assert [p.page for p in ex.pages] == [2, 1]
    # canonical output itself validates as §9
    sch.Extraction.model_validate(ex.model_dump(mode="json"))


def test_wire_model_rejects_missing_fields():
    payload = _wire_payload()
    del payload["funds"][0]["returns"]
    with pytest.raises(ValidationError):
        sch.WireExtraction.model_validate(payload)
