#!/usr/bin/env bash
# Run the Python test suite from the shared .venv (PRD §14).
# Creates .venv if missing (same logic as start.sh), ensures Postgres is up, and
# runs pytest against a separate test database (TEST_DATABASE_NAME, default
# docintel_test), which tests/conftest.py recreates per session.
# Extra arguments are passed to pytest, e.g. scripts/test.sh -k health -q
set -eo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

ensure_venv
ensure_postgres

export TEST_DATABASE_NAME="${TEST_DATABASE_NAME:-docintel_test}"
if ! compose exec -T postgres psql -U app -d postgres -tAc \
     "SELECT 1 FROM pg_database WHERE datname='${TEST_DATABASE_NAME}'" | grep -q 1; then
  info "Creating test database ${TEST_DATABASE_NAME}"
  compose exec -T postgres psql -U app -d postgres -qc "CREATE DATABASE ${TEST_DATABASE_NAME}" >/dev/null
fi

info "Running pytest"
exec "$VENV/bin/pytest" "$@"
