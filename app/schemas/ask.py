"""Ask (FR-11).

Inline citations: the answer markdown references citations as `[n]`, where n is
a `citations[].id`. Ids are 1..N, numbered in order of first appearance. Only
cited items are in `citations`. Every marker refers to an existing citation (the
server drops markers that do not), and markers never appear inside code spans.

Unverified numbers: `unverified_numbers` lists, de-duplicated, the exact
substrings of `answer` whose number could not be matched to a value in any
returned SQL row or passage. The citation markers themselves, 4-digit years and
the numbers inside ISO dates are not checked.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator

from app.schemas.common import ApiModel, DocumentType

CITATION_MARKER_RE = re.compile(r"\[(\d+)\]")
FLAGGED_DATA_NOTE = "Note: some of the data used is flagged for review (needs_review or conflict)."


class AskHistoryItem(ApiModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)  # assistant: the earlier `answer`, sent back unchanged


class AskRequest(ApiModel):
    question: str = Field(min_length=1, max_length=2000)
    history: list[AskHistoryItem] = Field(default_factory=list, max_length=20)

    @field_validator("question")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question must not be blank")
        return v


class SqlCitation(ApiModel):
    id: int
    type: Literal["sql"]
    sql: str  # the statement as executed, after the guard added or capped LIMIT
    columns: list[str]
    rows: list[list[Any]]  # aligned with columns; dates as YYYY-MM-DD, numerics as numbers
    row_count: int  # == len(rows)
    truncated: bool  # true when the LIMIT (200) was reached
    flagged_row_count: int  # rows whose `status` column is needs_review/conflict (0 if not selected)

    @model_validator(mode="after")
    def _shape(self) -> SqlCitation:
        if any(len(r) != len(self.columns) for r in self.rows):
            raise ValueError("every row must have one value per column")
        if self.row_count != len(self.rows):
            raise ValueError("row_count must equal len(rows)")
        return self


class PassageCitation(ApiModel):
    id: int
    type: Literal["passage"]
    chunk_id: int
    document_id: int
    document_name: str
    document_type: DocumentType | None
    page: int | None
    text: str  # the chunk text, plain
    web_view_link: str | None


Citation = Annotated[SqlCitation | PassageCitation, Field(discriminator="type")]


class AskStep(ApiModel):
    index: int  # 1-based, in call order
    tool: Literal["query_sql", "search_documents"]
    input: dict[str, Any]  # the tool input Claude sent
    result_summary: str  # e.g. "6 rows", "8 passages (FTS + vector, RRF)", or the guard message
    is_guard_error: bool  # query_sql rejected by the SQL guard (Claude may retry)
    error: str | None  # guard or execution error message; null on success
    citation_ids: list[int]  # citations taken from this step's result
    duration_ms: int


class AskUsage(ApiModel):
    input_tokens: int
    output_tokens: int


class AskResponse(ApiModel):
    answer: str  # markdown with [n] markers
    citations: list[Citation]
    steps: list[AskStep]
    unverified_numbers: list[str]
    # Some cited SQL row or source value is needs_review/conflict (§17). The answer then
    # always mentions it. If Claude did not, the server appends FLAGGED_DATA_NOTE.
    uses_flagged_data: bool
    stop_reason: Literal["answered", "max_steps"]  # max_steps: ASK_MAX_STEPS reached, best-effort answer
    model: str
    usage: AskUsage
    duration_ms: int

    @model_validator(mode="after")
    def _consistent(self) -> AskResponse:
        ids = [c.id for c in self.citations]
        if ids != list(range(1, len(ids) + 1)):
            raise ValueError("citation ids must be 1..N in order")
        known = set(ids)
        for m in CITATION_MARKER_RE.findall(self.answer):
            if int(m) not in known:
                raise ValueError(f"answer cites [{m}] but there is no such citation")
        for s in self.steps:
            if not set(s.citation_ids) <= known:
                raise ValueError("step citation_ids must refer to citations")
        for n in self.unverified_numbers:
            if n not in self.answer:
                raise ValueError(f"unverified number {n!r} is not a substring of answer")
        if len(set(self.unverified_numbers)) != len(self.unverified_numbers):
            raise ValueError("unverified_numbers must be unique")
        return self
