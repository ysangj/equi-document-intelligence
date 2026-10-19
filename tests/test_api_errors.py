"""C2 error envelope: unknown routes, wrong methods, validation errors, HTTPException
and unexpected exceptions all come back as {"error": {code, message, details}}."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api.errors import ApiError, details_from_errors, format_loc, install_error_handlers
from app.main import app
from tests.api_helpers import assert_error


@pytest.fixture
def client(test_engine):
    return TestClient(app)


def test_unknown_route_is_404_envelope(client):
    body = assert_error(client.get("/api/does-not-exist"), 404, "not_found")
    assert body["error"]["details"] is None
    assert_error(client.get("/nothing/here"), 404, "not_found")


@pytest.mark.parametrize("method,path", [
    ("post", "/api/returns"), ("delete", "/api/funds"), ("get", "/api/sync"), ("put", "/api/review/returns:1"),
    ("post", "/api/config"),
])
def test_wrong_method_is_405_envelope(client, method, path):
    body = assert_error(getattr(client, method)(path), 405, "method_not_allowed")
    assert path in body["error"]["message"]


@pytest.mark.parametrize("url,loc", [
    ("/api/returns?sort=colour", "query.sort"),
    ("/api/returns?sort=value_pct,-value_pct", "query.sort"),
    ("/api/returns?date_from=2026-02-01&date_to=2026-01-01", "query.date_from"),
    ("/api/returns?bogus=1", "query.bogus"),
    ("/api/returns?page=0", "query.page"),
    ("/api/returns?page_size=501", "query.page_size"),
    ("/api/returns?fund_id=abc", "query.fund_id"),
    ("/api/returns?status=bad", "query.status"),
    ("/api/statements?format=xml", "query.format"),
    ("/api/documents/abc/reprocess", "path.document_id"),
    ("/api/sources/nope/1", "path.table"),
    ("/api/search?q=" + "x" * 201, "query.q"),
    ("/api/funds?x=1", "query.x"),
    ("/api/progress?x=1", "query.x"),
    ("/api/review?format=csv", "query.format"),
])
def test_validation_errors_are_422_envelope_with_loc(client, url, loc):
    method = client.post if "reprocess" in url else client.get
    body = assert_error(method(url), 422, "validation_error", loc)
    assert body["error"]["message"] == "Invalid request parameters."
    assert all(not d["message"].startswith("Value error") for d in body["error"]["details"])


def test_sort_error_lists_allowed_columns(client):
    body = assert_error(client.get("/api/returns?sort=colour"), 422, "validation_error")
    assert "allowed: fund_name" in body["error"]["details"][0]["message"]


def test_review_body_errors(client):
    assert_error(client.post("/api/review/returns:1", json={"action": "nope"}), 422, "validation_error",
                 "body.action")
    assert_error(client.post("/api/review/returns:1", json={"action": "accept", "x": 1}), 422, "validation_error",
                 "body.x")
    assert_error(client.post("/api/review/returns:1", content=b"{bad json", headers={"content-type": "application/json"}),
                 422, "validation_error")
    # both / neither of value and value_pct is an invalid_action (contract), not a plain validation error
    assert_error(client.post("/api/review/returns:1", json={"action": "edit"}), 422, "invalid_action", "body.value")
    assert_error(client.post("/api/review/returns:1", json={"action": "edit", "value": 1, "value_pct": 1}), 422,
                 "invalid_action", "body.value")


def test_health_keeps_its_t1_body(client):
    assert client.get("/api/health").json()["status"] == "ok"


# ---- handlers on a throwaway app -------------------------------------------------------


@pytest.fixture
def mini():
    a = FastAPI()
    install_error_handlers(a)

    @a.get("/http")
    def http():
        raise HTTPException(status_code=404, detail="Thing 3 not found.")

    @a.get("/teapot")
    def teapot():
        raise HTTPException(status_code=409, detail="busy")

    @a.get("/api-error")
    def api_error():
        raise ApiError(409, "document_busy", "x.pdf is already queued for processing.")

    @a.get("/boom")
    def boom():
        raise RuntimeError("secret detail sk-ant-123")

    @a.get("/db")
    def db():
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    return TestClient(a, raise_server_exceptions=False)


def test_http_exception_uses_envelope(mini):
    body = assert_error(mini.get("/http"), 404, "not_found")
    assert body["error"]["message"] == "Thing 3 not found."
    # an undocumented status still gets a documented code
    body = assert_error(mini.get("/teapot"), 409, "validation_error")
    assert body["error"]["message"] == "busy"


def test_api_error_passthrough(mini):
    body = assert_error(mini.get("/api-error"), 409, "document_busy")
    assert body["error"]["message"] == "x.pdf is already queued for processing."


def test_unexpected_exception_is_generic_500(mini):
    r = mini.get("/boom")
    body = assert_error(r, 500, "internal_error")
    assert "secret" not in r.text and "sk-ant" not in r.text
    assert body["error"]["message"] == "Internal server error."


def test_db_down_is_503(mini):
    assert_error(mini.get("/db"), 503, "db_unavailable")


def test_format_loc():
    assert format_loc(("query", "sort")) == "query.sort"
    assert format_loc(("query", "fund_id", 0)) == "query.fund_id"
    assert format_loc(("body", "AcceptAction", "x")) == "body.x"
    assert format_loc(("body", "function-after[_one_value(), EditAction]", "value")) == "body.value"
    assert format_loc(("query",), "cannot sort by 'x'") == "query.sort"
    assert format_loc(("query",), "date_from must be on or before date_to") == "query.date_from"
    assert format_loc(()) is None


def test_details_dedupe_and_prefix():
    errs = [{"loc": ("x",), "msg": "Value error, bad"}, {"loc": ("x",), "msg": "Value error, bad"}]
    assert details_from_errors(errs, "body") == [{"loc": "body.x", "message": "bad"}]
