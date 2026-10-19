"""Application settings (PRD §11), loaded from `.env` and the environment.

Precedence: a variable present in the process environment wins over `.env`, even when
its value is empty (so `GOOGLE_CLIENT_ID= ./start.sh` forces demo mode). A value that is
empty or whitespace-only, from either source, counts as unset: the field's default
applies (None for keys without one). `scripts/lib.sh` follows the same rules so
`start.sh` and the api/worker never disagree.

Demo mode (FR-14) = `DEMO_MODE=true` or `GOOGLE_CLIENT_ID` unset.
Fail-fast (FR-1): `Settings.missing_required()` names the variables that are required
in the current mode; `require_settings()` raises `ConfigError` listing them.

Run `python -m app.config` to print the detected mode and any missing variable
names (never values).
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from typing import Any

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"
DEFAULT_DATABASE_URL = "postgresql+psycopg://app:app@localhost:5432/docintel"
DEFAULT_DATABASE_READONLY_URL = (
    "postgresql+psycopg://app_readonly:app_readonly@localhost:5432/docintel"
)
DEFAULT_GOOGLE_REDIRECT_URI = "http://localhost:8000/api/auth/google/callback"

# Variables always required (they have defaults, so this only fails if set to junk/None).
ALWAYS_REQUIRED = ("DATABASE_URL", "DATABASE_READONLY_URL")
# Required only outside demo mode: Drive OAuth + token encryption + extraction.
DRIVE_MODE_REQUIRED = (
    "ANTHROPIC_API_KEY",
    "GOOGLE_CLIENT_ID",
    "GOOGLE_CLIENT_SECRET",
    "GOOGLE_REDIRECT_URI",
    "FERNET_KEY",
    "SESSION_SECRET",
)

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}


class ConfigError(RuntimeError):
    """Raised when required configuration is missing. Message names keys only."""

    def __init__(self, missing: list[str], demo_mode: bool):
        self.missing = missing
        self.demo_mode = demo_mode
        mode = "demo mode" if demo_mode else "Google Drive mode"
        super().__init__(
            f"Missing required configuration for {mode}: {', '.join(missing)}. "
            "Set them in .env (see .env.example; generate FERNET_KEY and "
            "SESSION_SECRET with `python scripts/gen_keys.py`), or leave "
            "GOOGLE_CLIENT_ID unset / set DEMO_MODE=true to run in demo mode."
        )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        # False: an empty env var must override a .env value (BUG-Q1-01). Blank
        # values are then dropped by `_drop_blank_values`, so defaults apply.
        env_ignore_empty=False,
        case_sensitive=False,
        extra="ignore",
    )

    # Anthropic
    anthropic_api_key: SecretStr | None = None
    claude_model: str = DEFAULT_CLAUDE_MODEL

    # Google OAuth
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_redirect_uri: str | None = DEFAULT_GOOGLE_REDIRECT_URI
    oauthlib_insecure_transport: str | None = "1"

    # Database
    database_url: str = DEFAULT_DATABASE_URL
    database_readonly_url: str = DEFAULT_DATABASE_READONLY_URL

    # Local secrets
    fernet_key: SecretStr | None = None
    session_secret: SecretStr | None = None

    # Tuning
    sync_interval_seconds: int = 30
    worker_concurrency: int = 4
    max_attempts: int = 3
    review_confidence_threshold: float = 0.7
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    ask_max_steps: int = 6
    # Env var is DEMO_MODE; field renamed so the `demo_mode` property can be derived.
    demo_mode_flag: bool = Field(default=False, validation_alias="DEMO_MODE")

    @model_validator(mode="before")
    @classmethod
    def _drop_blank_values(cls, data: Any) -> Any:
        """Empty or whitespace-only values count as unset (BUG-Q1-01, BUG-Q1-02).

        Runs after sources are merged (environment over .env), so a blank env var
        still hides the .env value and the field falls back to its default.
        """
        if isinstance(data, dict):
            return {
                k: v for k, v in data.items()
                if not (isinstance(v, (str, SecretStr)) and not _is_set(v))
            }
        return data

    @field_validator("demo_mode_flag", mode="before")
    @classmethod
    def _parse_demo_flag(cls, v: object) -> bool:
        if v is None or isinstance(v, bool):
            return bool(v)
        s = str(v).strip().lower()
        if s in _TRUTHY:
            return True
        if s in _FALSY or s == "":
            return False
        raise ValueError("DEMO_MODE must be true/false")

    @field_validator("sync_interval_seconds", "worker_concurrency", "max_attempts", "ask_max_steps")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("must be >= 1")
        return v

    @field_validator("review_confidence_threshold")
    @classmethod
    def _unit_interval(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("must be between 0 and 1")
        return v

    # ---- derived -------------------------------------------------------

    @property
    def demo_mode(self) -> bool:
        return self.demo_mode_flag or not _is_set(self.google_client_id)

    @property
    def demo_reason(self) -> str | None:
        if self.demo_mode_flag:
            return "DEMO_MODE"
        if not _is_set(self.google_client_id):
            return "GOOGLE_CLIENT_ID_UNSET"
        return None

    @property
    def ask_enabled(self) -> bool:
        return _is_set(self.anthropic_api_key)

    @property
    def use_fixtures(self) -> bool:
        """Demo mode without an Anthropic key loads committed fixture extractions."""
        return self.demo_mode and not self.ask_enabled

    def missing_required(self) -> list[str]:
        required = list(ALWAYS_REQUIRED)
        if not self.demo_mode:
            required += DRIVE_MODE_REQUIRED
        return [name for name in required if not _is_set(getattr(self, name.lower()))]

    def secret_values(self) -> list[str]:
        """Raw secret values, for log redaction only. Never log the result."""
        out = []
        for name in ("anthropic_api_key", "google_client_secret", "fernet_key", "session_secret"):
            v = getattr(self, name)
            if _is_set(v):
                out.append(v.get_secret_value())
        return out


def _is_set(v: object) -> bool:
    if v is None:
        return False
    if isinstance(v, SecretStr):
        v = v.get_secret_value()
    return str(v).strip() != ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


def require_settings(settings: Settings | None = None) -> Settings:
    """Return settings or raise ConfigError naming the missing variables (FR-1)."""
    s = settings or get_settings()
    missing = s.missing_required()
    if missing:
        raise ConfigError(missing, s.demo_mode)
    return s


def main() -> int:
    s = get_settings()
    mode = "demo" if s.demo_mode else "drive"
    print(f"mode: {mode}" + (f" (reason: {s.demo_reason})" if s.demo_reason else ""))
    print(f"ask: {'enabled' if s.ask_enabled else 'disabled (ANTHROPIC_API_KEY not set)'}")
    if s.demo_mode:
        print(f"extraction: {'fixtures' if s.use_fixtures else 'claude'}")
    missing = s.missing_required()
    if missing:
        print(f"missing required variables: {', '.join(missing)}", file=sys.stderr)
        return 1
    print("config: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
