#!/usr/bin/env bash
# Start everything locally (PRD FR-1): Postgres (Docker), api + worker (.venv), web.
# Options (env): NO_BROWSER=1 skips opening the browser; POSTGRES_PORT overrides 5432.
set -eo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

API_PORT=8000
WEB_PORT=4200
HAS_WEB=0
[ -f "$ROOT/web/package.json" ] && HAS_WEB=1

# ---- 1. prerequisites ------------------------------------------------------
info "Checking prerequisites"
command -v docker >/dev/null 2>&1 || die "docker not found. Install Docker Desktop."
docker info >/dev/null 2>&1 || die "Docker is not running. Start Docker Desktop and retry."
command -v python3.12 >/dev/null 2>&1 || die "python3.12 not found. Install Python 3.12."
if ! command -v node >/dev/null 2>&1; then
  if [ "$HAS_WEB" -eq 1 ]; then die "node not found. Install Node.js LTS (https://nodejs.org)."; fi
  warn "node not found (not needed yet: web/ does not exist)"
fi
check_env_vars

# ---- 2. virtual environment -------------------------------------------------
ensure_venv

# ---- 3. UI dependencies ----------------------------------------------------
if [ "$HAS_WEB" -eq 1 ]; then
  LOCK="$ROOT/web/package-lock.json"
  LOCK_HASH_FILE="$ROOT/web/node_modules/.package-lock.sha256"
  want=""; have=""
  [ -f "$LOCK" ] && want="$(sha256_of "$LOCK")"
  [ -f "$LOCK_HASH_FILE" ] && have="$(cat "$LOCK_HASH_FILE")"
  if [ ! -d "$ROOT/web/node_modules" ] || [ "$want" != "$have" ]; then
    info "Installing UI dependencies (npm ci)"
    (cd "$ROOT/web" && npm ci) || die "npm ci failed"
    [ -n "$want" ] && printf '%s\n' "$want" > "$LOCK_HASH_FILE"
  else
    info "Reusing web/node_modules (package-lock.json unchanged)"
  fi
else
  info "web/ not present yet: skipping UI"
fi

# ---- 4. Postgres -------------------------------------------------------------
ensure_postgres

# ---- 5. api, worker, web -------------------------------------------------------
mkdir -p "$RUN_DIR"

start_bg() {  # name, port-or-empty, command...
  local name="$1" port="$2"; shift 2
  local pidfile="$RUN_DIR/$name.pid"
  if pid_alive "$pidfile"; then
    info "$name already running (pid $(cat "$pidfile"))"
    return 0
  fi
  if [ -n "$port" ] && port_listening "$port"; then
    die "port $port is already in use by another process; cannot start $name (run ./stop.sh or free the port)"
  fi
  nohup "$@" >"$RUN_DIR/$name.log" 2>&1 &
  echo $! >"$pidfile"
  info "Started $name (pid $!, log .run/$name.log)"
}

start_bg api "$API_PORT" bash -c "cd '$ROOT' && '$VENV/bin/alembic' upgrade head && exec '$VENV/bin/uvicorn' app.main:app --port $API_PORT"
start_bg worker "" bash -c "cd '$ROOT' && exec '$VENV/bin/python' -m app.worker"
if [ "$HAS_WEB" -eq 1 ]; then
  start_bg web "$WEB_PORT" bash -c "cd '$ROOT/web' && exec npm run start"
fi

# ---- 6. wait for health -------------------------------------------------------
info "Waiting for http://localhost:$API_PORT/api/health"
for i in $(seq 1 90); do
  if curl -fsS "http://localhost:$API_PORT/api/health" >/dev/null 2>&1; then
    info "api is healthy"
    break
  fi
  if ! pid_alive "$RUN_DIR/api.pid"; then
    warn "api exited during startup. Last lines of .run/api.log:"
    tail -n 30 "$RUN_DIR/api.log" >&2 || true
    die "api failed to start"
  fi
  [ "$i" -eq 90 ] && die "api did not become healthy within 90s (see .run/api.log)"
  sleep 1
done

# ---- 7. open the browser ------------------------------------------------------
if [ "$HAS_WEB" -eq 1 ]; then
  URL="http://localhost:$WEB_PORT"
  info "Waiting for web on $URL"
  for i in $(seq 1 120); do
    curl -fsS "$URL" >/dev/null 2>&1 && break
    pid_alive "$RUN_DIR/web.pid" || { tail -n 30 "$RUN_DIR/web.log" >&2 || true; die "web failed to start"; }
    sleep 1
  done
else
  URL="http://localhost:$API_PORT/docs"
fi

if [ "${NO_BROWSER:-0}" != "1" ]; then
  if command -v open >/dev/null 2>&1; then open "$URL"
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" >/dev/null 2>&1 || true
  fi
fi
info "Ready: $URL   (logs in .run/, stop with ./stop.sh)"
