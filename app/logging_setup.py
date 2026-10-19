"""Structured JSON logging (PRD §12 Observability) with secret redaction.

Every record is one JSON object per line. Extra fields passed via
`logger.info("msg", extra={"document_id": 1})` are included. Any configured secret
value (API key, client secret, Fernet key, session secret) that appears in a message
or extra field is replaced with `[REDACTED]` as a last line of defence; callers must
still never log secrets or document contents.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Iterable
from datetime import UTC, datetime

REDACTED = "[REDACTED]"

# OAuth values in URLs, e.g. uvicorn's access line for
# `GET /api/auth/google/callback?state=...&code=...` (FR-2, §12: never log tokens or codes).
_SENSITIVE_PARAM_RE = re.compile(
    r"(?<=[?&;])(code|state|code_verifier|code_challenge|access_token|refresh_token|id_token|token)=[^&\s\"']*",
    re.IGNORECASE,
)


def redact_url_params(value: str) -> str:
    return _SENSITIVE_PARAM_RE.sub(lambda m: f"{m.group(1)}={REDACTED}", value)

_RESERVED = set(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime", "taskName", "color_message"}


class JsonFormatter(logging.Formatter):
    def __init__(self, secrets: Iterable[str] = ()):
        super().__init__()
        # Ignore very short values so we never redact common substrings.
        self._secrets = [s for s in secrets if s and len(s) >= 8]

    def _redact(self, value: object) -> object:
        if isinstance(value, str):
            if "=" in value:
                value = redact_url_params(value)
            for s in self._secrets:
                if s in value:
                    value = value.replace(s, REDACTED)
            return value
        if isinstance(value, dict):
            return {k: self._redact(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._redact(v) for v in value]
        return value

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, val in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = val
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        payload = self._redact(payload)  # type: ignore[assignment]
        return json.dumps(payload, default=str)


def configure_logging(secrets: Iterable[str] = (), level: int = logging.INFO) -> None:
    """Install the JSON formatter on the root logger (idempotent)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(secrets))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Uvicorn installs its own handlers; route them through ours.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True
