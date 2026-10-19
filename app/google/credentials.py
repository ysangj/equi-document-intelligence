"""The single `oauth_credentials` row (FR-2, §12): Fernet-encrypted refresh token.

- The refresh token is encrypted with `FERNET_KEY` before it is written and decrypted
  only by the caller that is about to use it (one api request or one worker iteration).
- Access tokens are never stored; they are minted from the refresh token per use.
- Nothing in this module logs a token, and error messages never quote one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import Settings

# sync_state columns cleared by disconnect (T3 decision 7: documents are kept).
_SYNC_RESET_SQL = text("""
UPDATE sync_state SET folder_id = NULL, folder_name = NULL, page_token = NULL,
       last_reconciled_at = NULL, last_synced_at = NULL, last_error = NULL,
       sync_requested_at = NULL, reconcile_requested_at = NULL
WHERE id = 1
""")


class CredentialsError(Exception):
    """FERNET_KEY missing/invalid, or a stored token that cannot be decrypted."""


@dataclass(frozen=True)
class StoredCredentials:
    google_email: str | None
    refresh_token_encrypted: str  # also the "marker" for guarded invalidation
    scopes: list[str]
    created_at: datetime


def _fernet(settings: Settings) -> Fernet:
    if settings.fernet_key is None:
        raise CredentialsError("FERNET_KEY is not set")
    try:
        return Fernet(settings.fernet_key.get_secret_value().encode())
    except (ValueError, TypeError):
        raise CredentialsError("FERNET_KEY is not a valid Fernet key") from None


def encrypt_token(settings: Settings, token: str) -> str:
    return _fernet(settings).encrypt(token.encode()).decode()


def decrypt_token(settings: Settings, encrypted: str) -> str:
    try:
        return _fernet(settings).decrypt(encrypted.encode()).decode()
    except InvalidToken:
        raise CredentialsError("the stored Google credentials cannot be decrypted (was FERNET_KEY changed?)") from None


def load_credentials(session: Session) -> StoredCredentials | None:
    row = session.execute(text(
        "SELECT google_email, refresh_token_encrypted, scopes, created_at FROM oauth_credentials WHERE id = 1"
    )).mappings().first()
    if row is None:
        return None
    return StoredCredentials(row["google_email"], row["refresh_token_encrypted"], list(row["scopes"] or []),
                             row["created_at"])


def save_credentials(session: Session, settings: Settings, *, refresh_token: str, email: str | None,
                     scopes: list[str]) -> None:
    """Upsert the singleton row (id 1). Does not commit.

    If the account changed (a different known email), the watched folder belongs to the
    other account, so sync_state is reset and the user picks a folder again. Otherwise
    the folder is kept and a reconcile is requested, so anything that happened while
    the credentials were unusable is caught up.
    """
    previous = session.execute(text("SELECT google_email FROM oauth_credentials WHERE id = 1 FOR UPDATE")).first()
    session.execute(text("""
        INSERT INTO oauth_credentials (id, google_email, refresh_token_encrypted, scopes, created_at)
        VALUES (1, :email, :enc, :scopes, now())
        ON CONFLICT (id) DO UPDATE SET google_email = EXCLUDED.google_email,
            refresh_token_encrypted = EXCLUDED.refresh_token_encrypted, scopes = EXCLUDED.scopes,
            created_at = now()
    """), {"email": email, "enc": encrypt_token(settings, refresh_token), "scopes": sorted(set(scopes))})
    session.execute(text("INSERT INTO sync_state (id, source) VALUES (1, 'drive') ON CONFLICT (id) DO NOTHING"))
    account_changed = (previous is not None and previous[0] is not None and email is not None
                       and previous[0].lower() != email.lower())
    if account_changed:
        session.execute(_SYNC_RESET_SQL)
    else:
        session.execute(text(
            "UPDATE sync_state SET reconcile_requested_at = now() WHERE id = 1 AND folder_id IS NOT NULL"))


def delete_credentials(session: Session) -> None:
    """Disconnect (FR-2): remove the credentials and reset sync_state. Does not commit."""
    session.execute(text("DELETE FROM oauth_credentials WHERE id = 1"))
    session.execute(_SYNC_RESET_SQL)


def invalidate_credentials(session: Session, marker: str, message: str) -> bool:
    """Google rejected the stored refresh token (revoked/expired): drop it so the worker
    stops calling Google until the user reconnects, and record `message` (safe to show)
    in `sync_state.last_error`. The watched folder is kept.

    Guarded by `marker` (the encrypted token that failed): if the user reconnected in
    the meantime, the new credentials are left alone. Returns True if a row was removed.
    Does not commit.
    """
    n = session.execute(text(
        "DELETE FROM oauth_credentials WHERE id = 1 AND refresh_token_encrypted = :m"), {"m": marker}).rowcount
    if n:
        session.execute(text("INSERT INTO sync_state (id, source) VALUES (1, 'drive') ON CONFLICT (id) DO NOTHING"))
        session.execute(text("UPDATE sync_state SET last_error = :msg WHERE id = 1"), {"msg": message[:500]})
    return bool(n)
