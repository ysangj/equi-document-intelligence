"""Keep the `app_readonly` role's password in step with DATABASE_READONLY_URL.

Migration 0002 creates the role with the `.env.example` default password. After every
online `alembic upgrade` (which `start.sh` runs before the api starts),
`migrations/env.py` calls `sync_readonly_password`, so whatever password
DATABASE_READONLY_URL carries is the one the role has. Idempotent. The password is
never logged or echoed (the SQL is executed directly, not through alembic's op log).
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Connection, make_url

from app.models import READONLY_ROLE


def readonly_password_from_url(url: str) -> str | None:
    """The password in DATABASE_READONLY_URL if it is for the app_readonly role."""
    try:
        u = make_url(url)
    except Exception:  # noqa: BLE001 - malformed URL: nothing to sync
        return None
    if u.username != READONLY_ROLE or not u.password:
        return None
    return str(u.password)


def sync_readonly_password(connection: Connection, readonly_url: str) -> bool:
    """ALTER ROLE app_readonly PASSWORD <from url>, if the role exists. Returns True
    when a password was set."""
    password = readonly_password_from_url(readonly_url)
    if password is None:
        return False
    exists = connection.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": READONLY_ROLE}).first()
    if not exists:
        return False
    # Utility statements take no bind parameters; quote as a SQL literal.
    literal = "'" + password.replace("'", "''") + "'"
    connection.exec_driver_sql(f"ALTER ROLE {READONLY_ROLE} WITH LOGIN PASSWORD {literal}")
    return True
