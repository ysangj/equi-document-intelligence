"""Extraction (FR-5, §9). Public surface:

- `schema`: the §9 Pydantic models (`Extraction`, ...) and `PROMPT_VERSION`.
- `Extractor.extract(content, mime) -> Extraction` (and `extract_with_meta`).
- `ClaudeExtractor`, `FixtureExtractor`, and `get_extractor(settings)`.
"""

from __future__ import annotations

from app.config import Settings
from app.extraction.base import (
    ExtractionError,
    ExtractionResult,
    Extractor,
    FunctionExtractor,
    PermanentExtractionError,
    TransientExtractionError,
)
from app.extraction.schema import PROMPT_VERSION, Extraction

__all__ = [
    "PROMPT_VERSION", "Extraction", "ExtractionError", "ExtractionResult", "Extractor",
    "FunctionExtractor", "PermanentExtractionError", "TransientExtractionError", "get_extractor",
]


def get_extractor(settings: Settings) -> Extractor:
    """FixtureExtractor in demo mode without an Anthropic key (FR-14), else Claude."""
    if settings.use_fixtures:
        from app.extraction.fixture import FixtureExtractor

        return FixtureExtractor()
    from app.extraction.claude import ClaudeExtractor

    if settings.anthropic_api_key is None:
        raise RuntimeError("ANTHROPIC_API_KEY is required outside fixture demo mode")
    return ClaudeExtractor(settings.anthropic_api_key.get_secret_value(), settings.claude_model)
