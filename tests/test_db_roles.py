"""Carry-over (a): app_readonly's password follows DATABASE_READONLY_URL."""

from __future__ import annotations

import secrets

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from app.db_roles import readonly_password_from_url, sync_readonly_password
from tests.conftest import TEST_READONLY_URL


@pytest.mark.parametrize("url,expected", [
    ("postgresql+psycopg://app_readonly:pw1@localhost:5432/docintel", "pw1"),
    ("postgresql+psycopg://app_readonly:p%40ss@localhost/docintel", "p@ss"),
    ("postgresql+psycopg://app:pw@localhost/docintel", None),  # not the read-only role
    ("postgresql+psycopg://app_readonly@localhost/docintel", None),  # no password
    ("not a url", None),
])
def test_password_from_url(url, expected):
    assert readonly_password_from_url(url) == expected


def _can_connect(url: str) -> bool:
    eng = create_engine(url, connect_args={"connect_timeout": 5})
    try:
        with eng.connect() as c:
            return c.execute(text("SELECT 1")).scalar() == 1
    except OperationalError:
        return False
    finally:
        eng.dispose()


def test_sync_sets_password_from_url_and_is_idempotent(test_engine):
    new_password = "t4-" + secrets.token_hex(8) + "'quote"
    new_url = make_url(TEST_READONLY_URL).set(password=new_password).render_as_string(hide_password=False)
    try:
        with test_engine.begin() as c:
            assert sync_readonly_password(c, new_url) is True
            assert sync_readonly_password(c, new_url) is True  # idempotent
        assert _can_connect(new_url)
        assert not _can_connect(TEST_READONLY_URL)
    finally:
        with test_engine.begin() as c:
            sync_readonly_password(c, TEST_READONLY_URL)
    assert _can_connect(TEST_READONLY_URL)


def test_sync_skips_other_roles(test_engine):
    with test_engine.begin() as c:
        assert sync_readonly_password(c, "postgresql+psycopg://app:x@localhost/docintel") is False


def test_migrations_apply_the_configured_password(test_engine):
    """conftest runs `alembic upgrade head`, whose env.py step sets the password from
    DATABASE_READONLY_URL; the role can log in with exactly that URL."""
    assert _can_connect(TEST_READONLY_URL)
