"""The Ask tool-use loop (FR-11).

`run_ask(question, history, client=..., executor=...)` returns a dict that
validates as `app.schemas.ask.AskResponse`.

- Claude (`CLAUDE_MODEL`) gets the constant system prompt, the two tools and
  `tool_choice: auto` (claude-opus-5-5 rejects forced tool choice). Effort is
  `ASK_EFFORT`; thinking stays adaptive (it cannot be disabled on this model), and
  every assistant turn is appended back unchanged so thinking blocks round-trip.
- A tool-call round is one assistant response with `stop_reason == "tool_use"`.
  After `max_steps` rounds the tool results go back with a note, and one final
  call with `tool_choice: none` asks for the best answer from what was gathered
  (`stop_reason: "max_steps"`).
- Every tool call is a step. `query_sql` goes through the SQL guard; guard and
  execution errors go back to Claude as `is_error` tool results so it can retry.
- Citable sources get provisional refs (one per SQL result, one per distinct
  chunk). The answer's `[ref]` markers are renumbered 1..N; `citations[]` holds
  only cited sources.
- `uses_flagged_data`: a cited SQL result has a row whose `status` is
  needs_review/conflict, or a cited row id (`return_id`, `fact_id`,
  `statement_id`, `holding_id`) points to such a row. If the answer does not
  mention it, `FLAGGED_DATA_NOTE` is appended.
- `unverified_numbers`: the citation check over every SQL row and passage returned
  during the run (`citations.verify_numbers`).

Logging: ids, step counts, tools, tokens, latency and stop reasons only; never the
question, the answer, SQL rows or passage text (§12).
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import Engine, text

from app.ask.citations import collect_values, renumber_markers, verify_numbers
from app.ask.prompt import MAX_STEPS_NOTE, SYSTEM_PROMPT, TOOLS
from app.ask.retrieval import Passage, SearchInputError, SearchResult, search_documents
from app.ask.sql_guard import SqlExecutionError, SqlGuardError, SqlResult, execute_readonly, guard_sql
from app.schemas.ask import FLAGGED_DATA_NOTE

log = logging.getLogger("app.ask")

ASK_EFFORT = "medium"
MAX_TOKENS = 8000
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FLAGGED_STATUSES = ("needs_review", "conflict")
ID_COLUMN_VIEWS = {"return_id": "v_returns", "fact_id": "v_fund_facts", "statement_id": "v_statements",
                   "holding_id": "v_holdings"}
_FLAG_MENTION_RE = re.compile(r"\b(flag(?:ged|s)?|conflict(?:s|ing)?|needs[_ ]review|under review|for review)\b",
                              re.I)


class AskUpstreamError(RuntimeError):
    """Anthropic failed after the SDK's bounded retries. Message is safe to show."""


class ToolExecutor(Protocol):
    def query_sql(self, sql: str) -> SqlResult: ...

    def search(self, query: str, k: int | None, document_type: str | None, fund: str | None) -> SearchResult: ...

    def any_flagged(self, ids_by_view: dict[str, list[int]]) -> bool: ...


class DbToolExecutor:
    """Real tools: SQL as `app_readonly`, retrieval and flag lookups on the app engine."""

    def __init__(self, engine: Engine, readonly_engine: Engine):
        self.engine = engine
        self.readonly_engine = readonly_engine

    def query_sql(self, sql: str) -> SqlResult:
        return execute_readonly(self.readonly_engine, guard_sql(sql))

    def search(self, query, k, document_type, fund) -> SearchResult:
        return search_documents(self.engine, query, k, document_type, fund)

    def any_flagged(self, ids_by_view: dict[str, list[int]]) -> bool:
        id_col = {v: c for c, v in ID_COLUMN_VIEWS.items()}
        with self.engine.connect() as conn:
            for view, ids in ids_by_view.items():
                if not ids or view not in id_col:
                    continue
                hit = conn.execute(text(f"SELECT 1 FROM {view} WHERE {id_col[view]} = ANY(:ids) "
                                        "AND status IN ('needs_review', 'conflict') LIMIT 1"), {"ids": ids}).first()
                if hit:
                    return True
        return False


# ---------------------------------------------------------------------------
# sources and steps

@dataclass
class _Source:
    ref: int
    kind: str  # "sql" | "passage"
    payload: Any  # SqlResult (+ flagged count) or Passage
    flagged_row_count: int = 0


@dataclass
class _Run:
    sources: dict[int, _Source] = field(default_factory=dict)
    by_chunk: dict[int, int] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    step_refs: list[list[int]] = field(default_factory=list)
    sql_values: list[dict[str, Any]] = field(default_factory=list)
    passage_texts: list[str] = field(default_factory=list)
    next_ref: int = 1

    def add(self, kind: str, payload: Any, flagged: int = 0) -> int:
        if kind == "passage":
            existing = self.by_chunk.get(payload.chunk_id)
            if existing is not None:
                return existing
        ref = self.next_ref
        self.next_ref += 1
        self.sources[ref] = _Source(ref, kind, payload, flagged)
        if kind == "passage":
            self.by_chunk[payload.chunk_id] = ref
        return ref


def flagged_row_count(result: SqlResult) -> int:
    idx = [i for i, c in enumerate(result.columns) if c.lower() == "status"]
    if not idx:
        return 0
    return sum(1 for row in result.rows if any(row[i] in FLAGGED_STATUSES for i in idx))


def _get(block: Any, name: str, default: Any = None) -> Any:
    if isinstance(block, dict):
        return block.get(name, default)
    return getattr(block, name, default)


def _tool_result(tool_use_id: str, content: Any, is_error: bool = False) -> dict[str, Any]:
    body = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
    out: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id, "content": body}
    if is_error:
        out["is_error"] = True
    return out


def _run_query_sql(run: _Run, executor: ToolExecutor, tool_input: dict) -> tuple[dict, dict, list[int]]:
    sql = tool_input.get("sql")
    if not isinstance(sql, str) or not sql.strip():
        msg = "query_sql needs a non-empty `sql` string."
        return {"summary": "Invalid input", "error": msg, "guard": True}, {"error": msg}, []
    try:
        result = executor.query_sql(sql)
    except SqlGuardError as e:
        msg = str(e)
        return ({"summary": "Rejected by SQL guard", "error": msg, "guard": True},
                {"error": f"SQL guard rejected the query: {msg}"}, [])
    except SqlExecutionError as e:
        msg = str(e)
        return {"summary": "Query failed", "error": msg, "guard": False}, {"error": msg}, []
    flagged = flagged_row_count(result)
    ref = run.add("sql", result, flagged)
    run.sql_values.append({"rows": result.rows, "row_count": len(result.rows)})
    summary = f"{len(result.rows)} row{'s' if len(result.rows) != 1 else ''}"
    if result.truncated:
        summary += " (LIMIT reached)"
    content = {"ref": ref, "executed_sql": result.sql, "columns": result.columns, "rows": result.rows,
               "row_count": len(result.rows), "truncated": result.truncated, "flagged_row_count": flagged}
    return {"summary": summary, "error": None, "guard": False}, content, [ref]


def _run_search(run: _Run, executor: ToolExecutor, tool_input: dict) -> tuple[dict, dict, list[int]]:
    query = tool_input.get("query")
    k = tool_input.get("k")
    document_type = tool_input.get("document_type")
    fund = tool_input.get("fund")
    bad = None
    if not isinstance(query, str) or not query.strip():
        bad = "search_documents needs a non-empty `query` string."
    elif k is not None and (isinstance(k, bool) or not isinstance(k, int)):
        bad = "`k` must be an integer between 1 and 20."
    elif document_type is not None and not isinstance(document_type, str):
        bad = "`document_type` must be a string."
    elif fund is not None and not isinstance(fund, str):
        bad = "`fund` must be a string."
    if bad:
        return {"summary": "Invalid input", "error": bad, "guard": False}, {"error": bad}, []
    try:
        res = executor.search(query, k, document_type, fund)
    except SearchInputError as e:
        return {"summary": "Invalid input", "error": str(e), "guard": False}, {"error": str(e)}, []
    refs, items = [], []
    for p in res.passages:
        ref = run.add("passage", p)
        refs.append(ref)
        run.passage_texts.append(p.text)
        items.append({"ref": ref, "document_name": p.document_name, "document_type": p.document_type,
                      "page": p.page, "text": p.text})
    how = "FTS + vector, RRF" if res.mode == "hybrid" else "FTS only: embeddings unavailable"
    n = len(res.passages)
    summary = f"{n} passage{'s' if n != 1 else ''} ({how})"
    content: dict[str, Any] = {"passages": items}
    if res.matched_funds:
        content["matched_funds"] = res.matched_funds
    if res.note:
        content["note"] = res.note
    if not items and not res.note:
        content["note"] = "No passages matched. Try other wording or synonyms, or drop the filters."
    return {"summary": summary, "error": None, "guard": False}, content, refs


def _execute(run: _Run, executor: ToolExecutor, block: Any) -> dict[str, Any]:
    name = _get(block, "name")
    tool_input = _get(block, "input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    t0 = time.monotonic()
    if name == "query_sql":
        outcome, content, refs = _run_query_sql(run, executor, tool_input)
    elif name == "search_documents":
        outcome, content, refs = _run_search(run, executor, tool_input)
    else:
        msg = f"Unknown tool {name!r}. Use query_sql or search_documents."
        return _tool_result(_get(block, "id"), msg, is_error=True)
    run.steps.append({
        "index": len(run.steps) + 1, "tool": name, "input": tool_input,
        "result_summary": outcome["summary"], "is_guard_error": bool(outcome["guard"] and outcome["error"]),
        "error": outcome["error"], "citation_ids": [], "duration_ms": int((time.monotonic() - t0) * 1000),
    })
    run.step_refs.append(refs)
    if outcome["error"] is not None:
        return _tool_result(_get(block, "id"), content["error"], is_error=True)  # plain text for Claude
    return _tool_result(_get(block, "id"), content)


# ---------------------------------------------------------------------------
# the loop

def build_messages(question: str, history: Sequence[Any]) -> list[dict[str, Any]]:
    """Prior turns as plain text (stateless server), then the question. Leading
    assistant turns are dropped (the API needs a user turn first)."""
    msgs: list[dict[str, Any]] = []
    for h in history:
        role, content = _get(h, "role"), _get(h, "content")
        if not msgs and role != "user":
            continue
        msgs.append({"role": role, "content": content})
    msgs.append({"role": "user", "content": question})
    return msgs


def _request(model: str, messages: list, allow_tools: bool) -> dict[str, Any]:
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": SYSTEM_PROMPT}],
        "tools": TOOLS,
        "tool_choice": {"type": "auto"} if allow_tools else {"type": "none"},
        "messages": messages,
        "output_config": {"effort": ASK_EFFORT},
        "cache_control": {"type": "ephemeral"},  # automatic prefix caching across rounds
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
    }


def _call(client: Any, req: dict[str, Any]) -> Any:
    import anthropic

    try:
        return client.beta.messages.create(**req)
    except anthropic.RateLimitError:
        raise AskUpstreamError("The Anthropic API is rate-limiting requests. Try again shortly.") from None
    except anthropic.AuthenticationError:
        raise AskUpstreamError("The Anthropic API rejected the API key.") from None
    except anthropic.APITimeoutError:
        raise AskUpstreamError("The Anthropic API timed out. Try again.") from None
    except anthropic.APIConnectionError:
        raise AskUpstreamError("Could not reach the Anthropic API.") from None
    except anthropic.APIStatusError as e:
        raise AskUpstreamError(f"The Anthropic API failed (HTTP {e.status_code}).") from None


def run_ask(question: str, history: Sequence[Any], *, client: Any, executor: ToolExecutor, model: str,
            max_steps: int = 6) -> dict[str, Any]:
    t_start = time.monotonic()
    run = _Run()
    messages = build_messages(question, history)
    usage_in = usage_out = 0
    rounds = 0
    stop_reason = "answered"
    final_model = model
    answer_text = ""

    while True:
        allow_tools = stop_reason != "max_steps"
        resp = _call(client, _request(model, messages, allow_tools))
        u = _get(resp, "usage")
        if u is not None:
            usage_in += sum(int(_get(u, f, 0) or 0) for f in
                            ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
            usage_out += int(_get(u, "output_tokens", 0) or 0)
        final_model = _get(resp, "model") or final_model
        content = list(_get(resp, "content") or [])
        resp_stop = _get(resp, "stop_reason")
        if resp_stop == "refusal":
            raise AskUpstreamError("The model declined to answer this question.")
        tool_uses = [b for b in content if _get(b, "type") == "tool_use"]
        if resp_stop != "tool_use" or not tool_uses or not allow_tools:
            answer_text = "\n\n".join(_get(b, "text") or "" for b in content if _get(b, "type") == "text").strip()
            break
        rounds += 1
        messages.append({"role": "assistant", "content": content})  # unchanged: thinking blocks round-trip
        results: list[dict[str, Any]] = [_execute(run, executor, b) for b in tool_uses]
        if rounds >= max_steps:
            results.append({"type": "text", "text": MAX_STEPS_NOTE})
            stop_reason = "max_steps"
        messages.append({"role": "user", "content": results})

    if not answer_text:
        answer_text = "I could not produce an answer from the available data."
    answer, mapping = renumber_markers(answer_text, run.sources.keys())

    citations: list[dict[str, Any]] = []
    for ref, new_id in sorted(mapping.items(), key=lambda kv: kv[1]):
        src = run.sources[ref]
        if src.kind == "sql":
            r: SqlResult = src.payload
            citations.append({"id": new_id, "type": "sql", "sql": r.sql, "columns": r.columns, "rows": r.rows,
                              "row_count": len(r.rows), "truncated": r.truncated,
                              "flagged_row_count": src.flagged_row_count})
        else:
            p: Passage = src.payload
            citations.append({"id": new_id, "type": "passage", "chunk_id": p.chunk_id, "document_id": p.document_id,
                              "document_name": p.document_name, "document_type": p.document_type, "page": p.page,
                              "text": p.text, "web_view_link": p.web_view_link})
    for step, refs in zip(run.steps, run.step_refs):
        step["citation_ids"] = sorted(mapping[r] for r in refs if r in mapping)

    uses_flagged = _uses_flagged(executor, [run.sources[r] for r in mapping])
    if uses_flagged and not _FLAG_MENTION_RE.search(answer):
        answer = f"{answer}\n\n{FLAGGED_DATA_NOTE}"

    unverified = verify_numbers(answer, collect_values(run.sql_values, run.passage_texts))
    duration_ms = int((time.monotonic() - t_start) * 1000)
    log.info("ask completed", extra={
        "event": "ask_completed", "rounds": rounds, "steps": len(run.steps),
        "tools": [s["tool"] for s in run.steps], "guard_errors": sum(s["is_guard_error"] for s in run.steps),
        "citations": len(citations), "unverified_numbers": len(unverified), "uses_flagged_data": uses_flagged,
        "stop_reason": stop_reason, "model": final_model, "input_tokens": usage_in, "output_tokens": usage_out,
        "latency_ms": duration_ms,
    })
    return {
        "answer": answer,
        "citations": citations,
        "steps": run.steps,
        "unverified_numbers": unverified,
        "uses_flagged_data": uses_flagged,
        "stop_reason": stop_reason,
        "model": final_model,
        "usage": {"input_tokens": usage_in, "output_tokens": usage_out},
        "duration_ms": duration_ms,
    }


def _uses_flagged(executor: ToolExecutor, cited: list[_Source]) -> bool:
    ids_by_view: dict[str, list[int]] = {}
    for src in cited:
        if src.kind != "sql":
            continue
        if src.flagged_row_count:
            return True
        r: SqlResult = src.payload
        for i, col in enumerate(r.columns):
            view = ID_COLUMN_VIEWS.get(col.lower())
            if view:
                ids_by_view.setdefault(view, []).extend(
                    int(row[i]) for row in r.rows if isinstance(row[i], int) and not isinstance(row[i], bool))
    if not ids_by_view:
        return False
    try:
        return executor.any_flagged(ids_by_view)
    except Exception as e:  # noqa: BLE001 - a lookup failure must not fail the answer
        log.warning("flag lookup failed", extra={"event": "ask_flag_lookup_error", "error_type": type(e).__name__})
        return False
