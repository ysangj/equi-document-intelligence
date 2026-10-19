#!/usr/bin/env bash
# Stop api, worker and web (PIDs in .run/), then stop Postgres. Data persists in
# the pgdata volume (PRD FR-1).
set -eo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
# shellcheck source=scripts/lib.sh
source "$ROOT/scripts/lib.sh"

for name in web worker api; do
  pidfile="$RUN_DIR/$name.pid"
  [ -f "$pidfile" ] || continue
  pid="$(cat "$pidfile")"
  if kill -0 "$pid" 2>/dev/null; then
    info "Stopping $name (pid $pid)"
    kill_tree "$pid" TERM
    for _ in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    if kill -0 "$pid" 2>/dev/null; then
      warn "$name did not exit after 10s; sending SIGKILL"
      kill_tree "$pid" KILL
    fi
  else
    info "$name not running (stale pid file)"
  fi
  rm -f "$pidfile"
done

if docker info >/dev/null 2>&1; then
  info "Stopping Postgres (docker compose stop postgres); data is kept in volume pgdata"
  compose stop postgres >/dev/null 2>&1
else
  warn "Docker is not running; skipping Postgres stop"
fi
info "Stopped"
