"""Extractor interface (FR-5) and the errors the job queue understands.

PRD interface: `extract(content: bytes, mime: str) -> Extraction`. The pipeline also
needs the model, token counts and raw output for the `extractions` row, so every
extractor implements `extract_with_meta()`, and `extract()` is defined on top of it.
Tests mock either method (subclass `Extractor` and override `extract_with_meta`, or use
`FunctionExtractor(lambda content, mime: Extraction(...))`).

Error messages are built to be safe for `documents.last_error` and logs: they never
contain document contents, model output or API keys (PRD §12).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.extraction.schema import PROMPT_VERSION, Extraction


class ExtractionError(Exception):
    """An extraction attempt failed. `retryable` decides between backoff and `failed`.
    `message` must be safe to store and show (no contents, no secrets)."""

    def __init__(self, message: str, *, retryable: bool, retry_after: float | None = None):
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.retry_after = retry_after


class TransientExtractionError(ExtractionError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message, retryable=True, retry_after=retry_after)


class PermanentExtractionError(ExtractionError):
    def __init__(self, message: str):
        super().__init__(message, retryable=False)


@dataclass
class ExtractionResult:
    extraction: Extraction
    source: str  # 'claude' | 'fixture' (extractions.source)
    model: str | None
    prompt_version: str | None = PROMPT_VERSION
    input_tokens: int | None = None
    output_tokens: int | None = None
    # Raw output as returned (Claude's wire JSON); None means "same as extraction".
    raw_output: dict[str, Any] | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class Extractor:
    """Base class. Subclasses implement `extract_with_meta`."""

    source: str = "claude"

    def extract_with_meta(self, content: bytes, mime: str, *, name: str | None = None) -> ExtractionResult:
        raise NotImplementedError

    def extract(self, content: bytes, mime: str) -> Extraction:
        return self.extract_with_meta(content, mime).extraction


class FunctionExtractor(Extractor):
    """Wraps a plain `extract(content, mime) -> Extraction` callable (for tests/mocks)."""

    def __init__(self, fn: Callable[[bytes, str], Extraction], *, source: str = "claude", model: str | None = "mock"):
        self._fn = fn
        self.source = source
        self.model = model

    def extract_with_meta(self, content: bytes, mime: str, *, name: str | None = None) -> ExtractionResult:
        return ExtractionResult(extraction=self._fn(content, mime), source=self.source, model=self.model,
                                input_tokens=0, output_tokens=0)
