"""ClaudeExtractor: one Claude call per document returning the §9 schema (FR-5).

- PDFs go as native base64 `document` blocks; HTML, the .eml HTML body and CSV go as text.
- Structured output via `output_config.format` (JSON schema generated from the
  `WireExtraction` Pydantic model), then validated with Pydantic and converted to the
  canonical `Extraction`. Forced tool use (`tool_choice: tool/any`) is rejected by
  claude-opus-5-5, so tool use cannot guarantee a schema-valid call; structured output
  can, in one call.
- Streaming (`.get_final_message()`) so a long transcription never hits HTTP timeouts.
- Server-side refusal fallback (`fallbacks: "default"`): if the model declines, the API
  re-runs the request on a fallback model inside the same call. `extractions.model`
  records the model that actually answered.

Errors are mapped to Transient/PermanentExtractionError with messages that never
include document text, model output or the API key.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from pydantic import ValidationError

from app.extraction.base import (
    ExtractionResult,
    Extractor,
    PermanentExtractionError,
    TransientExtractionError,
)
from app.extraction.content import PDF, SUPPORTED_MIME_TYPES, text_for_model
from app.extraction.prompt import SYSTEM_PROMPT, user_instruction
from app.extraction.schema import PROMPT_VERSION, WireExtraction, wire_json_schema, wire_to_extraction

log = logging.getLogger("app.extraction.claude")

MAX_TOKENS = 64000
EFFORT = "medium"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
REQUEST_TIMEOUT_SECONDS = 600.0
SDK_MAX_RETRIES = 1  # one quick in-call retry; the job queue does the real backoff

_SCHEMA: dict[str, Any] | None = None


def output_schema() -> dict[str, Any]:
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = wire_json_schema()
    return _SCHEMA


def build_request(model: str, content: bytes, mime: str, name: str | None = None) -> dict[str, Any]:
    """Keyword arguments for `client.beta.messages.stream(...)`. Pure: no I/O."""
    if mime not in SUPPORTED_MIME_TYPES:
        raise PermanentExtractionError(f"unsupported MIME type {mime}")
    blocks: list[dict[str, Any]] = []
    if mime == PDF:
        blocks.append({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf",
                       "data": base64.standard_b64encode(content).decode("ascii")},
        })
    else:
        text = text_for_model(content, mime)
        kind = {"text/csv": "csv", "text/html": "html", "message/rfc822": "email"}[mime]
        blocks.append({"type": "text", "text": f"<document kind=\"{kind}\">\n{text}\n</document>"})
    blocks.append({"type": "text", "text": user_instruction(name, mime)})
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": blocks}],
        "output_config": {"effort": EFFORT, "format": {"type": "json_schema", "schema": output_schema()}},
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
    }


def parse_response(message: Any) -> tuple[WireExtraction, dict[str, Any]]:
    """Validate the final message. Returns (wire model, raw JSON dict)."""
    stop = getattr(message, "stop_reason", None)
    if stop == "refusal":
        details = getattr(message, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        raise PermanentExtractionError(f"model declined the request (refusal, category={category})")
    if stop == "max_tokens":
        raise PermanentExtractionError(f"output truncated at max_tokens={MAX_TOKENS}")
    text = next((b.text for b in message.content if getattr(b, "type", None) == "text"), None)
    if text is None:
        raise TransientExtractionError(f"no text block in response (stop_reason={stop})")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as e:
        raise TransientExtractionError(f"response is not valid JSON (line {e.lineno}, column {e.colno})") from None
    try:
        return WireExtraction.model_validate(raw), raw
    except ValidationError as e:
        locs = ", ".join(".".join(str(p) for p in err["loc"]) for err in e.errors()[:5])
        raise TransientExtractionError(f"response failed schema validation ({e.error_count()} errors: {locs})") from None


def map_api_error(e: Exception) -> Exception:
    """anthropic SDK exception -> Transient/PermanentExtractionError (safe message)."""
    import anthropic

    if isinstance(e, anthropic.APIStatusError):
        status = e.status_code
        err_type = api_message = None
        body = getattr(e, "body", None)
        if isinstance(body, dict):
            err = body.get("error") or {}
            err_type = err.get("type")
            api_message = err.get("message")
        msg = f"Anthropic API {status}" + (f" {err_type}" if err_type else "")
        # The API's own message for client errors (e.g. billing, bad model id). It
        # describes the request, never echoes document contents; capped.
        if api_message and 400 <= status < 500 and status != 429:
            msg += f": {str(api_message)[:200]}"
        retry_after = None
        try:
            ra = e.response.headers.get("retry-after")
            retry_after = float(ra) if ra else None
        except (AttributeError, ValueError):
            retry_after = None
        if status in (408, 409, 429) or status >= 500:
            return TransientExtractionError(msg, retry_after=retry_after)
        return PermanentExtractionError(msg)
    if isinstance(e, anthropic.APITimeoutError):
        return TransientExtractionError("Anthropic API timeout")
    if isinstance(e, anthropic.APIConnectionError):
        return TransientExtractionError("Anthropic API connection error")
    return e


class ClaudeExtractor(Extractor):
    source = "claude"

    def __init__(self, api_key: str, model: str, client: Any | None = None):
        self.model = model
        if client is None:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=SDK_MAX_RETRIES)
        self._client = client

    def extract_with_meta(self, content: bytes, mime: str, *, name: str | None = None) -> ExtractionResult:
        request = build_request(self.model, content, mime, name)
        try:
            with self._client.beta.messages.stream(**request) as stream:
                message = stream.get_final_message()
        except Exception as e:  # noqa: BLE001 - mapped below; unknown errors propagate
            mapped = map_api_error(e)
            if mapped is e:
                raise
            raise mapped from None
        wire, raw = parse_response(message)
        usage = getattr(message, "usage", None)
        input_tokens = output_tokens = None
        if usage is not None:
            input_tokens = (
                (getattr(usage, "input_tokens", 0) or 0)
                + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
                + (getattr(usage, "cache_read_input_tokens", 0) or 0)
            )
            output_tokens = getattr(usage, "output_tokens", None)
        return ExtractionResult(
            extraction=wire_to_extraction(wire),
            source="claude",
            model=getattr(message, "model", None) or self.model,
            prompt_version=PROMPT_VERSION,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            raw_output=raw,
        )
