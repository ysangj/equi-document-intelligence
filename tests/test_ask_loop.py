"""Ask tool-use loop and POST /api/ask (FR-11, §14 "Ask: mocked Claude responses exercising
each tool and the guard-error path"). No network: a scripted fake Anthropic client."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from sqlalchemy import text

from app.api.ask import ASK_DISABLED_MESSAGE, get_client_factory
from app.ask import loop as ask_loop
from app.ask.loop import AskUpstreamError, DbToolExecutor, run_ask
from app.ask.prompt import MAX_STEPS_NOTE, SYSTEM_PROMPT, TOOLS
from app.config import get_settings
from app.db import engine_for
from app.main import app, build_config
from app.schemas import AskResponse
from app.schemas.ask import FLAGGED_DATA_NOTE
from app.search import embedding
from tests.api_helpers import api_client, assert_error, demo_settings, ingest_into, truncate, validate
from tests.embedding_fakes import FakeEmbedder

JAN_SQL = ("SELECT return_id, fund_name, value_pct, status, document_name FROM v_returns WHERE period_year = 2026 "
           "AND period_month = 1 AND return_type = 'MTD' AND net_gross = 'net' ORDER BY value_pct DESC")

# ---- fake Anthropic client ---------------------------------------------------------


def tool_use(name: str, inp: dict, tid: str) -> dict:
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def textb(t: str) -> dict:
    return {"type": "text", "text": t}


THINKING = {"type": "thinking", "thinking": "", "signature": "sig-abc"}


def resp(*content, stop: str | None = None, inp: int = 1000, out: int = 50, cache_read: int = 0) -> SimpleNamespace:
    if stop is None:
        stop = "tool_use" if any(c["type"] == "tool_use" for c in content) else "end_turn"
    return SimpleNamespace(content=list(content), stop_reason=stop, model="claude-opus-5-5",
                           usage=SimpleNamespace(input_tokens=inp, output_tokens=out, cache_read_input_tokens=cache_read,
                                                 cache_creation_input_tokens=0))


class FakeMessages:
    def __init__(self, script: list):
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kw):
        self.calls.append(copy.deepcopy(kw))
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeClient:
    def __init__(self, script: list):
        self.messages = FakeMessages(script)
        self.beta = SimpleNamespace(messages=self.messages)


# ---- data ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine(test_engine, tmp_path_factory):
    embedding.set_embedder_override(FakeEmbedder())
    try:
        ingest_into(test_engine, tmp_path_factory.mktemp("ask_samples"))
    finally:
        embedding.set_embedder_override(None)
    yield test_engine
    truncate(test_engine)


@pytest.fixture
def executor(engine):
    return DbToolExecutor(engine, engine_for(get_settings().database_readonly_url))


def ask(script, question="q?", history=(), executor=None, max_steps=6):
    client = FakeClient(script)
    out = run_ask(question, list(history), client=client, executor=executor, model="claude-opus-5-5",
                  max_steps=max_steps)
    validate(AskResponse, out)
    return out, client.messages.calls


def tool_results(call: dict) -> list[dict]:
    return [b for b in call["messages"][-1]["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]


# ---- request shape ---------------------------------------------------------------


def test_request_uses_auto_tool_choice_tools_and_constant_prompt(executor):
    out, calls = ask([resp(textb("Hello."))], executor=executor)
    req = calls[0]
    assert req["tool_choice"] == {"type": "auto"}
    assert req["tools"] == TOOLS and [t["name"] for t in req["tools"]] == ["query_sql", "search_documents"]
    assert req["system"] == [{"type": "text", "text": SYSTEM_PROMPT}]
    assert req["model"] == "claude-opus-5-5" and req["output_config"] == {"effort": ask_loop.ASK_EFFORT}
    assert req["fallbacks"] == "default" and req["betas"] == [ask_loop.FALLBACK_BETA]
    assert req["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in req  # always adaptive on claude-opus-5-5
    assert out["steps"] == [] and out["citations"] == [] and out["stop_reason"] == "answered"


def test_system_prompt_documents_views_rules_and_synonyms():
    from tests.test_schema import VIEW_COLUMNS

    for view, cols in VIEW_COLUMNS.items():
        line = next(ln for ln in SYSTEM_PROMPT.splitlines() if ln.startswith(f"{view}:"))
        for col in cols:
            assert col in line, (view, col)
    for phrase in ("Never state a number", "[n]", "needs_review", "return_id", "fact_id", "statement_id",
                   "duration", "rate sensitivity", "interest-rate exposure", "value_pct", "fraction",
                   "do not add rank", "order the rows instead"):
        assert phrase in SYSTEM_PROMPT, phrase


# ---- sql-only ---------------------------------------------------------------------


def test_sql_only_question(executor):
    script = [
        resp(THINKING, textb("Let me query."), tool_use("query_sql", {"sql": JAN_SQL}, "t1")),
        resp(textb("**Kestrel Quant Equity Fund LP** had the best January 2026 return at **+2.84%** [1].")),
    ]
    out, calls = ask(script, executor=executor)
    assert len(calls) == 2
    # The assistant turn goes back unchanged (thinking blocks round-trip), then the tool result.
    assert calls[1]["messages"][-2] == {"role": "assistant", "content": script[0].content}
    (tr,) = tool_results(calls[1])
    assert tr["tool_use_id"] == "t1" and "is_error" not in tr and '"ref": 1' in tr["content"]
    (c,) = out["citations"]
    assert c["type"] == "sql" and c["id"] == 1
    assert c["sql"] == JAN_SQL + " LIMIT 200"
    assert c["columns"] == ["return_id", "fund_name", "value_pct", "status", "document_name"]
    assert c["rows"][0][1].startswith("Kestrel") and c["rows"][0][2] == 2.84
    assert c["row_count"] == len(c["rows"]) and c["truncated"] is False
    (step,) = out["steps"]
    assert step["index"] == 1 and step["tool"] == "query_sql" and step["input"] == {"sql": JAN_SQL}
    assert step["is_guard_error"] is False and step["error"] is None and step["citation_ids"] == [1]
    assert step["result_summary"] == f"{c['row_count']} rows"
    assert out["unverified_numbers"] == []
    assert out["usage"] == {"input_tokens": 2000, "output_tokens": 100}
    assert out["answer"].startswith("**Kestrel Quant Equity Fund LP** had the best January 2026 return at **+2.84%** [1].")


def test_flagged_rows_set_flag_and_note_is_appended(executor):
    """The Jan rows include the Northwind conflict (§17): the server appends the note."""
    script = [resp(tool_use("query_sql", {"sql": JAN_SQL}, "t1")), resp(textb("Kestrel led at +2.84% [1]."))]
    out, _ = ask(script, executor=executor)
    assert out["citations"][0]["flagged_row_count"] >= 2
    assert out["uses_flagged_data"] is True
    assert out["answer"].endswith(FLAGGED_DATA_NOTE)


def test_flag_already_mentioned_no_note(executor):
    script = [resp(tool_use("query_sql", {"sql": JAN_SQL}, "t1")),
              resp(textb("Kestrel led at +2.84% [1]. Northwind's January value is flagged as a conflict [1]."))]
    out, _ = ask(script, executor=executor)
    assert out["uses_flagged_data"] is True and FLAGGED_DATA_NOTE not in out["answer"]


def test_flag_detected_through_row_ids_without_status_column(executor, engine):
    with engine.connect() as c:
        rid = c.execute(text("SELECT return_id FROM v_returns WHERE status = 'conflict' LIMIT 1")).scalar()
    sql = f"SELECT return_id, fund_name, value_pct FROM v_returns WHERE return_id = {rid}"
    out, _ = ask([resp(tool_use("query_sql", {"sql": sql}, "t1")), resp(textb("Value [1]."))], executor=executor)
    assert out["citations"][0]["flagged_row_count"] == 0  # no status column selected
    assert out["uses_flagged_data"] is True and out["answer"].endswith(FLAGGED_DATA_NOTE)


def test_unflagged_data_no_flag(executor):
    sql = ("SELECT return_id, fund_name, value_pct, status FROM v_returns WHERE status = 'ok' AND fund_name ILIKE "
           "'%kestrel%' AND period_month = 1 AND period_year = 2026 AND return_type = 'MTD'")
    out, _ = ask([resp(tool_use("query_sql", {"sql": sql}, "t1")), resp(textb("Kestrel +2.84% [1]."))],
                 executor=executor)
    assert out["uses_flagged_data"] is False and FLAGGED_DATA_NOTE not in out["answer"]


def test_uncited_flagged_result_does_not_count(executor):
    script = [resp(tool_use("query_sql", {"sql": JAN_SQL}, "t1")), resp(textb("No citation here."))]
    out, _ = ask(script, executor=executor)
    assert out["citations"] == [] and out["uses_flagged_data"] is False
    assert out["steps"][0]["citation_ids"] == []


# ---- search-only -----------------------------------------------------------------


def test_search_only_question(executor):
    q = 'rate risk or duration or "rate sensitivity" or "interest-rate exposure"'
    script = [resp(tool_use("search_documents", {"query": q, "k": 5}, "s1")),
              resp(textb("Meridian cut duration [2]. Others discussed rate sensitivity [1]. Again [2]."))]
    out, calls = ask(script, executor=executor)
    (tr,) = tool_results(calls[1])
    assert '"passages"' in tr["content"] and "is_error" not in tr
    assert [c["type"] for c in out["citations"]] == ["passage", "passage"]
    assert [c["id"] for c in out["citations"]] == [1, 2]
    assert out["answer"].startswith("Meridian cut duration [1]. Others discussed rate sensitivity [2]. Again [1].")
    p = out["citations"][0]
    assert set(p) == {"id", "type", "chunk_id", "document_id", "document_name", "document_type", "page", "text",
                      "web_view_link"}
    (step,) = out["steps"]
    assert step["tool"] == "search_documents" and step["citation_ids"] == [1, 2]
    assert step["result_summary"].endswith("(FTS + vector, RRF)")
    assert out["uses_flagged_data"] is False


def test_same_chunk_from_two_searches_is_one_citation(executor):
    script = [resp(tool_use("search_documents", {"query": "duration", "k": 1}, "s1")),
              resp(tool_use("search_documents", {"query": "duration", "k": 1}, "s2")),
              resp(textb("Duration was cut [1]."))]
    out, calls = ask(script, executor=executor)
    assert '"ref": 1' in tool_results(calls[2])[0]["content"]  # same ref reused
    assert len(out["citations"]) == 1
    assert [s["citation_ids"] for s in out["steps"]] == [[1], [1]]


def test_search_input_errors_go_back_to_claude(executor):
    script = [resp(tool_use("search_documents", {"query": "x", "document_type": "memo"}, "s1"),
                   tool_use("search_documents", {"query": ""}, "s2"),
                   tool_use("search_documents", {"query": "x", "k": "five"}, "s3")),
              resp(textb("Sorry."))]
    out, calls = ask(script, executor=executor)
    trs = tool_results(calls[1])
    assert [t["is_error"] for t in trs] == [True, True, True]
    assert all(s["error"] and not s["is_guard_error"] for s in out["steps"])


def test_unknown_fund_note_reaches_claude(executor):
    script = [resp(tool_use("search_documents", {"query": "duration", "fund": "Zebra"}, "s1")), resp(textb("None."))]
    out, calls = ask(script, executor=executor)
    assert "No fund matches" in tool_results(calls[1])[0]["content"]
    assert out["steps"][0]["result_summary"].startswith("0 passages")


# ---- mixed -----------------------------------------------------------------------


def test_mixed_question_uses_both_tools(executor):
    worst = ("SELECT return_id, fund_name, value_pct, status FROM v_returns WHERE period_year = 2026 AND "
             "period_month = 3 AND return_type = 'MTD' ORDER BY value_pct ASC LIMIT 1")
    script = [resp(tool_use("query_sql", {"sql": worst}, "a"),
                   tool_use("search_documents", {"query": "March performance commentary", "fund": "Alder Asia"}, "b")),
              resp(textb("The worst March return was Alder Asia [1]; the manager said [2]."))]
    out, calls = ask(script, executor=executor)
    trs = tool_results(calls[1])
    assert [t["tool_use_id"] for t in trs] == ["a", "b"]  # both results in ONE user message
    assert [s["tool"] for s in out["steps"]] == ["query_sql", "search_documents"]
    assert [c["type"] for c in out["citations"]] == ["sql", "passage"]
    assert out["steps"][0]["citation_ids"] == [1] and out["steps"][1]["citation_ids"] == [2]


# ---- guard errors ----------------------------------------------------------------


@pytest.mark.parametrize("bad", ["DROP TABLE returns", "DELETE FROM returns WHERE true",
                                 "SELECT f.canonical_name, r.value FROM returns r JOIN funds f ON f.id = r.fund_id"])
def test_guard_error_then_retry(executor, engine, bad):
    script = [resp(tool_use("query_sql", {"sql": bad}, "t1")),
              resp(tool_use("query_sql", {"sql": JAN_SQL}, "t2")),
              resp(textb("Kestrel +2.84% [1]; Northwind is flagged as a conflict [1]."))]  # failed steps get no ref
    out, calls = ask(script, executor=executor)
    (tr1,) = tool_results(calls[1])
    assert tr1["is_error"] is True and tr1["content"].startswith("SQL guard rejected the query:")
    s1, s2 = out["steps"]
    assert s1["is_guard_error"] is True and s1["error"] and s1["result_summary"] == "Rejected by SQL guard"
    assert s1["citation_ids"] == [] and s1["input"] == {"sql": bad}
    assert s2["is_guard_error"] is False and s2["citation_ids"] == [1]
    assert out["answer"].startswith("Kestrel +2.84% [1]")
    with engine.connect() as c:  # nothing was changed
        assert c.execute(text("SELECT count(*) FROM returns")).scalar() > 0


def test_execution_error_is_not_a_guard_error(executor):
    script = [resp(tool_use("query_sql", {"sql": "SELECT nope FROM v_returns"}, "t1")), resp(textb("Failed."))]
    out, calls = ask(script, executor=executor)
    (step,) = out["steps"]
    assert step["is_guard_error"] is False and "nope" in step["error"] and step["result_summary"] == "Query failed"
    assert tool_results(calls[1])[0]["is_error"] is True


def test_unknown_tool_and_bad_sql_input(executor):
    script = [resp(tool_use("rm_rf", {}, "x"), tool_use("query_sql", {"sql": 42}, "y")), resp(textb("ok"))]
    out, calls = ask(script, executor=executor)
    trs = tool_results(calls[1])
    assert [t["is_error"] for t in trs] == [True, True]
    assert len(out["steps"]) == 1 and out["steps"][0]["is_guard_error"] is True  # unknown tools are not steps


# ---- max steps -------------------------------------------------------------------


def test_max_steps_cap_forces_final_answer(executor):
    sql = {"sql": "SELECT fund_id, fund_name FROM v_funds"}
    script = [resp(tool_use("query_sql", sql, "t1")), resp(tool_use("query_sql", sql, "t2")),
              resp(textb("Best effort [2]."))]
    out, calls = ask(script, executor=executor, max_steps=2)
    assert len(calls) == 3
    assert [c["tool_choice"] for c in calls] == [{"type": "auto"}, {"type": "auto"}, {"type": "none"}]
    last_user = calls[2]["messages"][-1]["content"]
    assert last_user[-1] == {"type": "text", "text": MAX_STEPS_NOTE}
    assert out["stop_reason"] == "max_steps" and len(out["steps"]) == 2
    assert [c["id"] for c in out["citations"]] == [1] and out["steps"][1]["citation_ids"] == [1]


def test_default_cap_is_six_rounds(executor):
    sql = {"sql": "SELECT fund_id FROM v_funds"}
    script = [resp(tool_use("query_sql", sql, f"t{i}")) for i in range(6)] + [resp(textb("Done."))]
    out, calls = ask(script, executor=executor)
    assert len(calls) == 7 and calls[-1]["tool_choice"] == {"type": "none"}
    assert out["stop_reason"] == "max_steps" and len(out["steps"]) == 6


def test_answer_within_budget_is_answered(executor):
    sql = {"sql": "SELECT fund_id FROM v_funds"}
    out, _ = ask([resp(tool_use("query_sql", sql, "t1")), resp(textb("Done."))], executor=executor, max_steps=2)
    assert out["stop_reason"] == "answered"


# ---- markers ---------------------------------------------------------------------


def test_marker_renumbering_and_unresolvable_markers(executor):
    script = [resp(tool_use("query_sql", {"sql": "SELECT fund_id, fund_name FROM v_funds"}, "a"),
                   tool_use("query_sql", {"sql": "SELECT account_id, account_name FROM v_accounts"}, "b")),
              resp(textb("Accounts [2]. Funds [1]. Stale [7]. Code `x [1]`."))]
    out, _ = ask(script, executor=executor)
    assert out["answer"] == "Accounts [1]. Funds [2]. Stale. Code `x `[2]."
    assert [c["columns"][0] for c in out["citations"]] == ["account_id", "fund_id"]
    assert [s["citation_ids"] for s in out["steps"]] == [[2], [1]]


def test_answer_text_blocks_joined_and_empty_answer_fallback(executor):
    out, _ = ask([resp(textb("Part one."), textb("Part two."))], executor=executor)
    assert out["answer"] == "Part one.\n\nPart two."
    out, _ = ask([resp(THINKING)], executor=executor)
    assert out["answer"] == "I could not produce an answer from the available data."


def test_unverified_numbers_in_response(executor):
    script = [resp(tool_use("query_sql", {"sql": JAN_SQL}, "t1")),
              resp(textb("Kestrel **+2.84%** [1] led by **0.92** points; conflict noted [1]."))]
    out, _ = ask(script, executor=executor)
    assert out["unverified_numbers"] == ["0.92"]


# ---- history ---------------------------------------------------------------------


def test_history_passed_through_as_prior_turns(executor):
    history = [{"role": "assistant", "content": "orphan greeting"},
               {"role": "user", "content": "List all Alder funds"},
               {"role": "assistant", "content": "There are two Alder funds [1]."}]
    out, calls = ask([resp(textb("Yes [1]."))], question="And their March returns?", history=history,
                     executor=executor)
    assert calls[0]["messages"] == [
        {"role": "user", "content": "List all Alder funds"},
        {"role": "assistant", "content": "There are two Alder funds [1]."},
        {"role": "user", "content": "And their March returns?"},
    ]
    assert out["answer"] == "Yes."  # [1] from history does not resolve in this run


# ---- upstream errors -------------------------------------------------------------

_REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


@pytest.mark.parametrize("exc, fragment", [
    (anthropic.RateLimitError("r", response=httpx2.Response(429, request=_REQ), body=None), "rate-limiting"),
    (anthropic.InternalServerError("s", response=httpx2.Response(529, request=_REQ), body=None), "HTTP 529"),
    (anthropic.APITimeoutError(request=_REQ), "timed out"),
    (anthropic.APIConnectionError(request=_REQ), "Could not reach"),
    (anthropic.AuthenticationError("a", response=httpx2.Response(401, request=_REQ), body=None), "API key"),
])
def test_upstream_errors_map(executor, exc, fragment):
    with pytest.raises(AskUpstreamError) as e:
        ask([exc], executor=executor)
    assert fragment in str(e.value)


def test_refusal_is_upstream_error(executor):
    with pytest.raises(AskUpstreamError):
        ask([resp(textb(""), stop="refusal")], executor=executor)


# ---- HTTP endpoint ---------------------------------------------------------------


@pytest.fixture
def http(engine):
    holder: dict[str, FakeClient] = {}

    def factory_for(script):
        def factory(api_key):
            assert api_key == "sk-test-key"
            holder["client"] = FakeClient(script)
            return holder["client"]
        return factory

    def run(body, script=(), settings=None):
        app.dependency_overrides[get_client_factory] = lambda: factory_for(list(script))
        try:
            with api_client(settings or demo_settings(anthropic_api_key="sk-test-key")) as c:
                return c.post("/api/ask", json=body), holder.get("client")
        finally:
            app.dependency_overrides.pop(get_client_factory, None)

    return run


def test_endpoint_200_contract(http):
    script = [resp(tool_use("query_sql", {"sql": JAN_SQL}, "t1")),
              resp(textb("Kestrel +2.84% [1]; Northwind is a conflict [1]."))]
    r, client = http({"question": "  Which fund had the best January return?  "}, script)
    assert r.status_code == 200, r.text
    body = validate(AskResponse, r.json())
    assert client.messages.calls[0]["messages"][-1] == {"role": "user",
                                                       "content": "Which fund had the best January return?"}
    assert body.citations[0].type == "sql" and body.uses_flagged_data is True
    assert set(r.json()) == set(AskResponse.model_fields)


def test_endpoint_503_without_key(http):
    r, client = http({"question": "hi"}, settings=demo_settings())
    body = assert_error(r, 503, "ask_disabled")
    assert body["error"]["message"] == ASK_DISABLED_MESSAGE
    assert ASK_DISABLED_MESSAGE == build_config(demo_settings()).features.ask.reason
    assert client is None  # never called


def test_endpoint_502_upstream(http):
    exc = anthropic.RateLimitError("r", response=httpx2.Response(429, request=_REQ), body=None)
    r, _ = http({"question": "hi"}, [exc])
    assert_error(r, 502, "upstream_error")


@pytest.mark.parametrize("body, loc", [
    ({"question": "   "}, "body.question"),
    ({}, "body.question"),
    ({"question": "x" * 2001}, "body.question"),
    ({"question": "x", "history": [{"role": "system", "content": "x"}]}, None),
    ({"question": "x", "extra": 1}, None),
])
def test_endpoint_422(http, body, loc):
    r, _ = http(body)
    assert_error(r, 422, "validation_error", loc)


def test_api_key_never_logged(http, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    script = [resp(tool_use("search_documents", {"query": "secret question words"}, "s1")),
              resp(textb("Answer with private words [1]."))]
    r, _ = http({"question": "secret question words"}, script)
    assert r.status_code == 200
    logged = "\n".join(f"{rec.getMessage()} {rec.__dict__}" for rec in caplog.records)
    assert "sk-test-key" not in logged
    assert "secret question words" not in logged and "private words" not in logged
    done = [rec for rec in caplog.records if getattr(rec, "event", None) == "ask_completed"]
    assert done and done[0].steps == 1 and done[0].tools == ["search_documents"]
