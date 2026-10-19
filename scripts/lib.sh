# Shared helpers for start.sh, stop.sh and scripts/test.sh. Source, don't execute.
# Requires ROOT to be set to the repository root.
#
# Secrets rule (FR-0): these helpers may read .env to check whether a key is set,
# but must never print, copy, or log any value.

VENV="$ROOT/.venv"
RUN_DIR="$ROOT/.run"
REQ_HASH_FILE="$VENV/.requirements.sha256"
# Config lookup rules, shared with app/config.py (BUG-Q1-01, BUG-Q1-02):
#  - A variable present in the process environment wins over .env, even when empty.
#  - A value that is empty or whitespace-only, from either source, counts as unset.
# These helpers never print values; _env_lookup stores the value in _ENV_VAL.

# _trim VAR_NAME -> strips leading/trailing whitespace from $VAR_NAME in place.
_trim() {
  local _v="${!1}"
  _v="${_v#"${_v%%[![:space:]]*}"}"
  _v="${_v%"${_v##*[![:space:]]}"}"
  printf -v "$1" '%s' "$_v"
}

# _env_file_raw KEY -> sets _ENV_VAL to KEY's value in .env (last assignment wins;
# optional `export`, surrounding quotes and ` # comment` stripped, then trimmed).
_env_file_raw() {
  local key="$1" line
  _ENV_VAL=""
  [ -f "$ROOT/.env" ] || return 0
  line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}[[:space:]]*=" "$ROOT/.env" | tail -n 1 || true)"
  [ -n "$line" ] || return 0
  _ENV_VAL="${line#*=}"
  _ENV_VAL="$(printf '%s' "$_ENV_VAL" | sed -E -e 's/[[:space:]]+#.*$//')"
  _trim _ENV_VAL
  case "$_ENV_VAL" in
    \"*\") _ENV_VAL="${_ENV_VAL#\"}"; _ENV_VAL="${_ENV_VAL%\"}" ;;
    \'*\') _ENV_VAL="${_ENV_VAL#\'}"; _ENV_VAL="${_ENV_VAL%\'}" ;;
  esac
  _trim _ENV_VAL
}

# _env_lookup KEY -> sets _ENV_VAL to the effective, trimmed value ("" = unset).
_env_lookup() {
  local key="$1"
  if printenv "$key" >/dev/null 2>&1; then
    _ENV_VAL="$(printenv "$key")"
    _trim _ENV_VAL
  else
    _env_file_raw "$key"
  fi
}

# env_file_value KEY -> prints the effective value (environment > .env). Only call
# this for NON-secret keys such as POSTGRES_PORT; never for anything secret.
env_file_value() {
  _env_lookup "$1"
  printf '%s' "$_ENV_VAL"
}

# Host port for Postgres: environment wins (an empty value means "unset", so the
# default applies), then .env, then 5432. Exported so docker compose interpolates
# ${POSTGRES_PORT} with exactly this value.
_env_lookup POSTGRES_PORT
PG_PORT="${_ENV_VAL:-5432}"
_ENV_VAL=""
case "$PG_PORT" in
  *[!0-9]*) printf '\033[1;31merror:\033[0m POSTGRES_PORT must be a number\n' >&2; exit 1 ;;
esac
export POSTGRES_PORT="$PG_PORT"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

sha256_of() { shasum -a 256 "$1" | awk '{print $1}'; }

# env_is_set KEY -> 0 if KEY has a non-blank effective value. Never echoes it.
env_is_set() {
  _env_lookup "$1"
  local rc=1
  [ -n "$_ENV_VAL" ] && rc=0
  _ENV_VAL=""
  return "$rc"
}

# env_is_true KEY -> 0 if KEY's effective value is true/1/yes/on. Never echoes it.
env_is_true() {
  _env_lookup "$1"
  local v
  v="$(printf '%s' "$_ENV_VAL" | tr '[:upper:]' '[:lower:]')"
  _ENV_VAL=""
  case "$v" in
    true|1|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

# Prints the mode and missing variable names (names only). Fails when required
# variables for the detected mode are missing (FR-1, FR-14).
check_env_vars() {
  [ -f "$ROOT/.env" ] || die ".env not found. Run: cp .env.example .env  (then fill it in, or leave GOOGLE_CLIENT_ID empty for demo mode)."
  local demo=0 missing=() optional_missing=() k
  if env_is_true DEMO_MODE || ! env_is_set GOOGLE_CLIENT_ID; then demo=1; fi

  local required=(DATABASE_URL DATABASE_READONLY_URL)
  if [ "$demo" -eq 0 ]; then
    required+=(ANTHROPIC_API_KEY GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET GOOGLE_REDIRECT_URI FERNET_KEY SESSION_SECRET)
  fi
  # DATABASE_* and GOOGLE_REDIRECT_URI have code defaults, so they are only "missing" from .env, not fatal.
  for k in "${required[@]}"; do
    if ! env_is_set "$k"; then
      case "$k" in
        DATABASE_URL|DATABASE_READONLY_URL|GOOGLE_REDIRECT_URI) optional_missing+=("$k (using default)") ;;
        *) missing+=("$k") ;;
      esac
    fi
  done
  if [ "$demo" -eq 1 ]; then
    info "Demo mode (DEMO_MODE=true or GOOGLE_CLIENT_ID unset): watching ./sample_docs"
    env_is_set ANTHROPIC_API_KEY || info "ANTHROPIC_API_KEY not set: fixture extractions will be used; Ask is disabled"
  else
    info "Google Drive mode"
  fi
  [ "${#optional_missing[@]}" -eq 0 ] || warn "not set in .env: ${optional_missing[*]}"
  if [ "${#missing[@]}" -gt 0 ]; then
    die "missing required variables in .env: ${missing[*]}  (generate FERNET_KEY/SESSION_SECRET with: python3.12 scripts/gen_keys.py)"
  fi
}

# FR-1 step 2: create .venv if missing; reinstall only when requirements.txt changed.
ensure_venv() {
  command -v python3.12 >/dev/null 2>&1 || die "python3.12 not found. Install Python 3.12 (https://www.python.org/downloads/)."
  if [ -d "$VENV" ] && [ ! -x "$VENV/bin/python" ]; then
    warn ".venv is broken (no bin/python); recreating"
    rm -rf "$VENV"
  fi
  local want have=""
  want="$(sha256_of "$ROOT/requirements.txt")"
  if [ ! -d "$VENV" ]; then
    info "Creating virtual environment at .venv"
    python3.12 -m venv "$VENV"
  else
    [ -f "$REQ_HASH_FILE" ] && have="$(cat "$REQ_HASH_FILE")"
  fi
  # shellcheck disable=SC1091
  source "$VENV/bin/activate"
  if [ "$want" != "$have" ]; then
    info "Installing Python requirements (requirements.txt changed or fresh .venv)"
    "$VENV/bin/python" -m pip install --quiet --upgrade pip
    "$VENV/bin/python" -m pip install --quiet -r "$ROOT/requirements.txt" || die "pip install failed"
    printf '%s\n' "$want" > "$REQ_HASH_FILE"
  else
    info "Reusing .venv (requirements unchanged)"
  fi
}

compose() { (cd "$ROOT" && docker compose "$@"); }

port_listening() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

# FR-1 step 4: start Postgres and wait until it accepts connections.
ensure_postgres() {
  docker info >/dev/null 2>&1 || die "Docker is not running. Start Docker Desktop and retry."
  if [ -z "$(compose ps --status running -q postgres 2>/dev/null)" ] && port_listening "$PG_PORT"; then
    die "port $PG_PORT is already in use by another process. Stop it, or set POSTGRES_PORT=<free port> and point DATABASE_URL / DATABASE_READONLY_URL at that port."
  fi
  info "Starting Postgres (docker compose up -d postgres) on port $PG_PORT"
  local out
  out="$(compose up -d --quiet-pull postgres 2>&1)" || { printf '%s\n' "$out" >&2; die "docker compose up failed"; }
  local i
  for i in $(seq 1 60); do
    if compose exec -T postgres pg_isready -U app -d docintel -q >/dev/null 2>&1; then
      info "Postgres is accepting connections"
      return 0
    fi
    sleep 1
  done
  die "Postgres did not become ready within 60s (see: docker compose logs postgres)"
}

pid_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

# Kill a process and all its descendants (npm -> ng, bash -> uvicorn).
kill_tree() {
  local pid="$1" sig="${2:-TERM}" child
  for child in $(pgrep -P "$pid" 2>/dev/null); do
    kill_tree "$child" "$sig"
  done
  kill -"$sig" "$pid" 2>/dev/null || true
}
