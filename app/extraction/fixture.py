"""FixtureExtractor (FR-14): demo mode without ANTHROPIC_API_KEY.

Loads `./fixtures/<source filename>.json`, written once by `scripts/gen_fixtures.py`
from a real ClaudeExtractor run. File format (FIXTURE_FORMAT 1):

    {
      "fixture_format": 1,
      "source_file": "kestrel_factsheet_2026-01.pdf",
      "source_md5": "<md5 of the file the fixture was generated from>",
      "mime_type": "application/pdf",
      "model": "claude-opus-5-5",          # model that produced it
      "prompt_version": "2026-10-16.1",
      "input_tokens": 6420, "output_tokens": 2812, "latency_ms": 21480,  # of that run
      "generated_at": "2026-10-16T19:40:00Z",
      "extraction": { ...§9 Extraction... }
    }

`extraction` is validated as `Extraction`. The extractions row records
source='fixture', the generating model and prompt version, and the generating run's
token counts (so demo mode shows realistic per-document usage; `source` tells them
apart). latency_ms is the local load time, measured by the pipeline.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.config import PROJECT_ROOT
from app.extraction.base import ExtractionResult, Extractor, PermanentExtractionError
from app.extraction.schema import Extraction

log = logging.getLogger("app.extraction.fixture")

FIXTURES_DIR = PROJECT_ROOT / "fixtures"
FIXTURE_FORMAT = 1


def fixture_path(fixtures_dir: Path, name: str) -> Path:
    return fixtures_dir / f"{Path(name).name}.json"


def load_fixture(path: Path) -> tuple[Extraction, dict[str, Any]]:
    """(validated Extraction, metadata) or PermanentExtractionError."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PermanentExtractionError(
            f"no fixture for {path.stem} in fixtures/ (demo mode without ANTHROPIC_API_KEY "
            "only processes files that have a committed fixture)"
        ) from None
    except json.JSONDecodeError as e:
        raise PermanentExtractionError(f"fixture {path.name} is not valid JSON (line {e.lineno})") from None
    if not isinstance(data, dict) or data.get("fixture_format") != FIXTURE_FORMAT or "extraction" not in data:
        raise PermanentExtractionError(f"fixture {path.name} has an unknown format")
    try:
        extraction = Extraction.model_validate(data["extraction"])
    except ValidationError as e:
        raise PermanentExtractionError(f"fixture {path.name} failed §9 validation ({e.error_count()} errors)") from None
    meta = {k: v for k, v in data.items() if k != "extraction"}
    return extraction, meta


class FixtureExtractor(Extractor):
    source = "fixture"

    def __init__(self, fixtures_dir: Path = FIXTURES_DIR):
        self.fixtures_dir = Path(fixtures_dir)
        self.model = None

    def extract_with_meta(self, content: bytes, mime: str, *, name: str | None = None) -> ExtractionResult:
        if not name:
            raise PermanentExtractionError("FixtureExtractor needs the source file name")
        extraction, meta = load_fixture(fixture_path(self.fixtures_dir, name))
        if meta.get("source_md5"):
            import hashlib

            if hashlib.md5(content).hexdigest() != meta["source_md5"]:
                log.warning(
                    "fixture was generated from a different version of the file",
                    extra={"event": "fixture_md5_mismatch", "file": Path(name).name},
                )
        return ExtractionResult(
            extraction=extraction,
            source="fixture",
            model=meta.get("model"),
            prompt_version=meta.get("prompt_version"),
            input_tokens=meta.get("input_tokens"),
            output_tokens=meta.get("output_tokens"),
            meta=meta,
        )
