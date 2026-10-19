#!/usr/bin/env bash
# First-time setup for .env (PRD FR-0, FR-1, FR-14, §11). Run ./scripts/init_env.sh --help.
#
# Secrets rule (FR-0): this script never prints a secret value, never takes one as a
# command-line argument, and never passes one in another program's argv. Values
# go to scripts/env_tool.py on stdin and to curl through `--config -` (stdin).
# Works with macOS's bash 3.2 and with Linux bash.
set -eo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENV_FILE="$ROOT/.env"
EXAMPLE_FILE="$ROOT/.env.example"
TOOL="$ROOT/scripts/env_tool.py"
DEFAULT_REDIRECT="http://localhost:8000/api/auth/google/callback"
ALT_PORT_START=55432
SUMMARY_KEYS="ANTHROPIC_API_KEY GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET FERNET_KEY SESSION_SECRET POSTGRES_PORT DATABASE_URL DATABASE_READONLY_URL"

usage() {
  cat <<'EOF'
Usage: ./scripts/init_env.sh [options]

Creates or updates .env for Fund Document Intelligence. Run it from anywhere.

  * .env missing: it is created from .env.example.
  * .env present: it is kept. Only the values you enter are filled in or replaced,
    and a backup is saved first as .env.bak.<timestamp> (git-ignored), but only
    if something actually changes.

Steps:
  1. Anthropic API key   hidden prompt; Enter keeps the current one. Optionally
                         checked with one GET https://api.anthropic.com/v1/models.
  2. Google OAuth client the client JSON you downloaded from Google Cloud
                         (Google Auth Platform -> Clients -> Download JSON), or type
                         the ID and secret, or skip for demo mode.
  3. Local secrets       FERNET_KEY and SESSION_SECRET are generated if empty.
                         An existing FERNET_KEY is kept (see --rotate-keys).
  4. Postgres port       if the configured Postgres port (POSTGRES_PORT, default 5432)
                         is used by another program, offers a free port and updates
                         POSTGRES_PORT, DATABASE_URL and DATABASE_READONLY_URL.
  5. Write .env          in place (comments and order kept), chmod 600, then a
                         summary of key names and their status. Values are never shown.

Options:
  --anthropic-key-file PATH   read the Anthropic key from a file (first line, or a
                              line ANTHROPIC_API_KEY=...)
  --google-client-json PATH   use this downloaded OAuth client JSON
  --demo                      skip Google, so the app runs in demo mode on
                              ./sample_docs. On an existing .env this clears
                              GOOGLE_CLIENT_ID/SECRET (the backup keeps the old values).
  --yes, -y                   don't prompt; accept the default for every question
  --no-validate               don't call the Anthropic API to check the key
  --rotate-keys               generate a NEW FERNET_KEY and SESSION_SECRET even if
                              set. This invalidates the stored Google connection:
                              you'll have to click "Connect Google Drive" again.
  -h, --help                  show this help

Environment:
  ANTHROPIC_API_KEY           used if set and no --anthropic-key-file is given
                              (asked first when running interactively)

Secret values are never accepted as arguments (they'd be visible in `ps` and kept
in your shell history). Pass file paths instead. Without a terminal on stdin the
script runs non-interactively, as with --yes.

Examples:
  ./scripts/init_env.sh                          # interactive, recommended
  ./scripts/init_env.sh --demo --yes             # demo mode, no keys needed
  ./scripts/init_env.sh --yes --no-validate \
      --anthropic-key-file ~/secrets/anthropic.txt \
      --google-client-json ~/Downloads/client_secret_123.apps.googleusercontent.com.json
EOF
}

# ---------------------------------------------------------------------------- output
if [ -t 1 ]; then
  C_B=$'\033[1m'; C_DIM=$'\033[2m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_R=$'\033[31m'; C_C=$'\033[36m'; C_0=$'\033[0m'
else
  C_B=""; C_DIM=""; C_G=""; C_Y=""; C_R=""; C_C=""; C_0=""
fi
say()  { printf '%s\n' "$*"; }
step() { printf '\n%s%s%s\n' "$C_B$C_C" "$*" "$C_0"; }
ok()   { printf '  %s✓%s %s\n' "$C_G" "$C_0" "$*"; }
note() { printf '  %s\n' "$*"; }
warn() { printf '  %swarning:%s %s\n' "$C_Y" "$C_0" "$*" >&2; }
die()  { printf '%serror:%s %s\n' "$C_R" "$C_0" "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------- arguments
ANTHROPIC_KEY_FILE=""
GOOGLE_JSON=""
DEMO=0
YES=0
VALIDATE=1
ROTATE=0

reject_secret_arg() {
  case "$1" in
    *sk-ant-*|*GOCSPX-*)
      die "that argument looks like a secret value. Secrets are never accepted on the command line (they show up in 'ps' and your shell history). Put the key in a file and use --anthropic-key-file PATH, or run without options to be prompted. If it was a real key, consider rotating it."
      ;;
  esac
}

while [ $# -gt 0 ]; do
  reject_secret_arg "$1"
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --anthropic-key-file) [ $# -ge 2 ] || die "--anthropic-key-file needs a PATH"; reject_secret_arg "$2"; ANTHROPIC_KEY_FILE="$2"; shift 2 ;;
    --anthropic-key-file=*) ANTHROPIC_KEY_FILE="${1#*=}"; shift ;;
    --google-client-json) [ $# -ge 2 ] || die "--google-client-json needs a PATH"; reject_secret_arg "$2"; GOOGLE_JSON="$2"; shift 2 ;;
    --google-client-json=*) GOOGLE_JSON="${1#*=}"; shift ;;
    --demo) DEMO=1; shift ;;
    -y|--yes) YES=1; shift ;;
    --no-validate) VALIDATE=0; shift ;;
    --rotate-keys) ROTATE=1; shift ;;
    --anthropic-key|--anthropic-key=*|--anthropic-api-key*|--google-client-secret*|--client-secret*|--key|--key=*)
      die "secret values are not accepted as arguments. Use --anthropic-key-file PATH or --google-client-json PATH, or run without options to be prompted." ;;
    *) die "unknown option: ${1%%=*} (see --help)" ;;
  esac
done
[ "$DEMO" -eq 1 ] && [ -n "$GOOGLE_JSON" ] && die "--demo and --google-client-json can't be combined"

INTERACTIVE=0
if [ -t 0 ] && [ "$YES" -eq 0 ]; then INTERACTIVE=1; fi

# ---------------------------------------------------------------------------- helpers
PY=""
for cand in python3.12 python3; do
  if command -v "$cand" >/dev/null 2>&1; then PY="$(command -v "$cand")"; break; fi
done
[ -n "$PY" ] || die "python3 not found. Install Python 3.12 (https://www.python.org/downloads/)."
[ -f "$TOOL" ] || die "missing $TOOL"

WROTE=0
on_int() {
  printf '\n'
  if [ "$WROTE" -eq 0 ]; then say "Cancelled. .env was not changed."; fi
  exit 130
}
trap on_int INT TERM

tool() { "$PY" "$TOOL" "$@"; }

trim_var() {  # trim_var NAME -> strip whitespace and CR from $NAME in place
  local _v="${!1}"
  _v="${_v//$'\r'/}"
  _v="${_v#"${_v%%[![:space:]]*}"}"
  _v="${_v%"${_v##*[![:space:]]}"}"
  printf -v "$1" '%s' "$_v"
}

# ask_yn QUESTION DEFAULT(y|n) -> 0 for yes. Non-interactive: the default.
ask_yn() {
  local q="$1" def="$2" hint ans
  if [ "$def" = y ]; then hint="Y/n"; else hint="y/N"; fi
  if [ "$INTERACTIVE" -eq 0 ]; then
    if [ "$def" = y ]; then note "$q [$hint] -> yes (default)"; return 0; fi
    note "$q [$hint] -> no (default)"; return 1
  fi
  while true; do
    read -r -p "  $q [$hint] " ans || ans=""
    case "$(printf '%s' "$ans" | tr '[:upper:]' '[:lower:]')" in
      "") [ "$def" = y ] && return 0 || return 1 ;;
      y|yes) return 0 ;;
      n|no) return 1 ;;
      *) note "Please answer y or n." ;;
    esac
  done
}

# clean_path NAME -> strip quotes/trailing spaces from drag-and-drop paths, expand ~.
# Locals are prefixed (_cp) so they never shadow the caller's NAME (bash is dynamically scoped).
clean_path() {
  local _cp="${!1}"
  trim_var _cp
  case "$_cp" in
    \'*\') _cp="${_cp#\'}"; _cp="${_cp%\'}" ;;
    \"*\") _cp="${_cp#\"}"; _cp="${_cp%\"}" ;;
  esac
  # macOS Terminal escapes spaces and some characters when a file is dropped in.
  if [ ! -e "$_cp" ] && [ "${_cp#*\\}" != "$_cp" ]; then
    _cp="$(printf '%s' "$_cp" | sed -e 's/\\\(.\)/\1/g')"
  fi
  case "$_cp" in
    "~") _cp="$HOME" ;;
    "~/"*) _cp="$HOME/${_cp#\~/}" ;;
  esac
  printf -v "$1" '%s' "$_cp"
}

# Status per key, for the summary (bash 3.2: no associative arrays).
set_status() { printf -v "ST_$1" '%s' "$2"; }
get_status() { local n="ST_$1"; printf '%s' "${!n}"; }
has() { local n="HAS_$1"; [ "${!n}" = 1 ]; }

# Pending writes: parallel arrays, sent to env_tool.py on stdin.
OP_T=(); OP_K=(); OP_V=()
add_op() { OP_T+=("$1"); OP_K+=("$2"); OP_V+=("$3"); }

# value_ok KEY NAME -> 0 if the value in $NAME can be written to .env as KEY exactly.
# Otherwise prints why (the reason names the key, never the value) and returns 1.
# Values are checked when they're collected, so nothing is backed up or written for
# a value that would be refused, and an interactive user can re-enter it (BUG-Q7-03).
value_ok() {
  local _vo_msg
  if _vo_msg="$(printf '%s' "${!2}" | tool check "$1" 2>&1)"; then return 0; fi
  warn "can't save that value: $_vo_msg."
  return 1
}

port_in_use() {
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
  else
    tool port-in-use "$1"
  fi
}

# 0 if PORT is published by this project's own (running) Postgres container.
port_is_ours() {
  command -v docker >/dev/null 2>&1 || return 1
  local mapped
  mapped="$(docker compose port postgres 5432 2>/dev/null | head -n 1)" || return 1
  [ -n "$mapped" ] && [ "${mapped##*:}" = "$1" ]
}

# ---------------------------------------------------------------------------- start
say "${C_B}Fund Document Intelligence: .env setup${C_0}"
say "${C_DIM}Values you type are never shown, and nothing secret is printed.${C_0}"

# ---- safety: .env must never reach git (FR-0) -------------------------------------
step "Checking that .env stays out of git"
if command -v git >/dev/null 2>&1 && git -C "$ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if git -C "$ROOT" ls-files --error-unmatch .env >/dev/null 2>&1; then
    die ".env is TRACKED by git, so your keys would be committed. Remove it from the index (your file stays on disk):
    git rm --cached .env && git commit -m \"Stop tracking .env\"
  Then re-run this script. If .env was ever pushed, rotate the keys it contained."
  fi
  git -C "$ROOT" check-ignore -q .env || die ".env would NOT be ignored by git. Add these lines to .gitignore, then re-run:
    .env
    .env.*
    !.env.example"
  git -C "$ROOT" check-ignore -q .env.bak.check || die "backups (.env.bak.*) would NOT be ignored by git. Add '.env.*' and '!.env.example' to .gitignore, then re-run."
  ok ".env and its backups are git-ignored"
else
  warn "not inside a git repository, so the ignore check was skipped"
fi

if [ -f "$ENV_FILE" ]; then
  BASE="$ENV_FILE"; CREATING=0
  ok "Found an existing .env. It is kept; only what you enter below changes."
else
  [ -f "$EXAMPLE_FILE" ] || die ".env.example not found in $ROOT"
  BASE="$EXAMPLE_FILE"; CREATING=1
  ok "No .env yet. It will be created from .env.example."
fi

for k in $SUMMARY_KEYS DEMO_MODE; do printf -v "HAS_$k" '%s' 0; done
while read -r k v; do
  [ -n "$k" ] && printf -v "HAS_$k" '%s' "$v"
done < <(tool status "$BASE" $SUMMARY_KEYS DEMO_MODE)
for k in $SUMMARY_KEYS; do
  if has "$k"; then set_status "$k" kept; else set_status "$k" missing; fi
done
if [ "$CREATING" -eq 1 ]; then
  for k in POSTGRES_PORT DATABASE_URL DATABASE_READONLY_URL; do has "$k" && set_status "$k" default; done
fi

REDIRECT="$(tool get "$BASE" GOOGLE_REDIRECT_URI)"
[ -n "$REDIRECT" ] || REDIRECT="$DEFAULT_REDIRECT"

# ---- 1. Anthropic ------------------------------------------------------------------
step "Step 1/4  Anthropic API key"
note "Used to extract data from documents and to answer questions (Ask)."
note "Create one at https://console.anthropic.com -> API keys. It's separate from a Claude.ai plan."
NEW_KEY=""
KEY_SOURCE=""
CHECK_RESULT=""

read_key_file() {
  local f="$1" line
  clean_path f
  [ -f "$f" ] || die "Anthropic key file not found: $f"
  [ -r "$f" ] || die "can't read the Anthropic key file: $f"
  NEW_KEY=""
  while IFS= read -r line || [ -n "$line" ]; do
    trim_var line
    case "$line" in ""|\#*) continue ;; esac
    case "$line" in
      ANTHROPIC_API_KEY=*|export\ ANTHROPIC_API_KEY=*) line="${line#*=}"; trim_var line ;;
    esac
    case "$line" in \'*\') line="${line#\'}"; line="${line%\'}" ;; \"*\") line="${line#\"}"; line="${line%\"}" ;; esac
    NEW_KEY="$line"
    break
  done < "$f"
  [ -n "$NEW_KEY" ] || die "the Anthropic key file is empty: $f"
}

# validate_key -> sets CHECK_RESULT to valid | rejected | unknown, prints the outcome.
validate_key() {
  CHECK_RESULT="unknown"
  if [ "$VALIDATE" -eq 0 ]; then note "Not checked (--no-validate)."; CHECK_RESULT="skipped"; return 0; fi
  if ! command -v curl >/dev/null 2>&1; then warn "could not check the key (curl not found)"; return 0; fi
  local esc code
  esc="${NEW_KEY//\\/\\\\}"; esc="${esc//\"/\\\"}"
  # The key goes to curl on stdin as a config file, never in curl's argv.
  code="$(printf 'url = "https://api.anthropic.com/v1/models"\nheader = "x-api-key: %s"\nheader = "anthropic-version: 2023-06-01"\n' "$esc" \
          | curl -sS -o /dev/null -w '%{http_code}' --max-time 10 --config - 2>/dev/null)" || code="000"
  esc=""
  case "$code" in
    200) CHECK_RESULT="valid"; ok "Key check: valid. (A valid key still needs credits: Console -> Settings -> Billing.)" ;;
    401) CHECK_RESULT="rejected"; warn "Key check: rejected (401). Anthropic doesn't recognise this key." ;;
    403) CHECK_RESULT="rejected"; warn "Key check: rejected (403). The key isn't allowed to use the API." ;;
    000) warn "Key check: could not check (no connection to api.anthropic.com). The key is saved anyway." ;;
    *)   warn "Key check: could not check (HTTP $code). The key is saved anyway." ;;
  esac
}

prompt_key() {
  local hint
  if has ANTHROPIC_API_KEY; then hint="currently set; Enter keeps it"; else hint="Enter leaves it empty"; fi
  read -r -s -p "  Paste your Anthropic API key (input hidden; $hint): " NEW_KEY || NEW_KEY=""
  printf '\n'
  trim_var NEW_KEY
}

if [ -n "$ANTHROPIC_KEY_FILE" ]; then
  read_key_file "$ANTHROPIC_KEY_FILE"; KEY_SOURCE="file"
  ok "Read the key from the file you gave."
elif [ -n "${ANTHROPIC_API_KEY:-}" ] && { NEW_KEY="$ANTHROPIC_API_KEY"; trim_var NEW_KEY; [ -n "$NEW_KEY" ]; }; then
  if ask_yn "ANTHROPIC_API_KEY is set in your shell environment. Use that key?" y; then
    KEY_SOURCE="environment"
  else
    NEW_KEY=""
    [ "$INTERACTIVE" -eq 1 ] && prompt_key
  fi
elif [ "$INTERACTIVE" -eq 1 ]; then
  prompt_key
fi

while [ -n "$NEW_KEY" ]; do
  if ! value_ok ANTHROPIC_API_KEY NEW_KEY; then
    NEW_KEY=""
    [ "$INTERACTIVE" -eq 1 ] || die "the Anthropic key can't be saved (see above). .env was not changed."
    note "Real Anthropic keys never contain such characters. Please paste the key again."
    KEY_SOURCE=""
    prompt_key
    continue
  fi
  case "$NEW_KEY" in
    sk-ant-*) ;;
    *) warn "this doesn't look like an Anthropic API key (they start with sk-ant-). It's used as given." ;;
  esac
  case "$NEW_KEY" in *[[:space:]]*) warn "the key contains spaces; check that you pasted only the key." ;; esac
  validate_key
  if [ "$CHECK_RESULT" = rejected ] && [ "$INTERACTIVE" -eq 1 ] && ask_yn "Enter a different key?" y; then
    prompt_key
    continue
  fi
  [ "$CHECK_RESULT" = rejected ] && warn "saving the rejected key anyway. Extraction and Ask will fail until it's replaced."
  break
done

if [ -n "$NEW_KEY" ]; then
  add_op set ANTHROPIC_API_KEY "$NEW_KEY"; set_status ANTHROPIC_API_KEY set
  ok "ANTHROPIC_API_KEY will be saved${KEY_SOURCE:+ (from the $KEY_SOURCE)}."
elif has ANTHROPIC_API_KEY; then
  ok "ANTHROPIC_API_KEY kept (currently set)."
else
  set_status ANTHROPIC_API_KEY "empty"
  note "No key: the app uses the committed fixture extractions, and Ask is disabled."
fi
NEW_KEY=""

# ---- 2. Google ---------------------------------------------------------------------
step "Step 2/4  Google Drive access (OAuth client)"
note "The app reads one Drive folder through your own Google Cloud OAuth client."
GOOGLE_RESULT=""   # json | manual | kept | demo

# check_client_json PATH -> 0 if it's usable (warnings printed), 1 otherwise.
check_client_json() {
  local path="$1" out rc line kind="" redirect="" hint="" idok=1
  out="$(tool client-json "$path" "$REDIRECT" 2>&1)" && rc=0 || rc=$?
  if [ "$rc" -ne 0 ]; then warn "can't use that file: $out"; return 1; fi
  while read -r line; do
    case "$line" in
      "kind "*) kind="${line#kind }" ;;
      "redirect "*) redirect="${line#redirect }" ;;
      "redirect_hint "*) hint="${line#redirect_hint }" ;;
      "client_id_looks_ok 0") idok=0 ;;
    esac
  done <<< "$out"
  ok "Read client_id and client_secret from the JSON."
  [ "$idok" -eq 1 ] || warn "the client_id doesn't end in .apps.googleusercontent.com; is this the right file?"
  if [ "$kind" = installed ]; then
    warn "this is a \"Desktop app\" (installed) client. The app needs a \"Web application\" client."
    note "Fix: Google Auth Platform -> Clients -> Create client -> Application type: Web application,"
    note "add the redirect URI $REDIRECT, then download that client's JSON."
    if [ "$INTERACTIVE" -eq 1 ] && ! ask_yn "Use this Desktop client anyway?" n; then return 1; fi
  fi
  case "$redirect" in
    ok) ok "Redirect URI matches: $REDIRECT" ;;
    *)
      if [ "$redirect" = missing ]; then
        warn "the client has no Authorized redirect URIs."
      else
        warn "the client's Authorized redirect URIs don't include $REDIRECT${hint:+ ($hint)}."
      fi
      note "Google will refuse the sign-in with \"redirect_uri_mismatch\" until you fix it:"
      note "Google Cloud console -> Google Auth Platform -> Clients -> your client ->"
      note "Authorized redirect URIs -> Add URI -> $REDIRECT -> Save."
      note "(Exactly that: http, localhost, port 8000, no trailing slash. No need to re-download the JSON.)"
      ;;
  esac
  return 0
}

use_json() {
  local p="$1"
  clean_path p
  if check_client_json "$p"; then
    add_op clientjson - "$p"
    GOOGLE_RESULT="json"
    return 0
  fi
  return 1
}

manual_google() {
  local id="" secret=""
  while true; do
    read -r -p "  Client ID (ends in .apps.googleusercontent.com): " id || id=""
    trim_var id
    [ -n "$id" ] || { note "No client ID entered."; return 1; }
    value_ok GOOGLE_CLIENT_ID id && break
    note "Please enter it again (Enter goes back to the menu)."
  done
  case "$id" in *.apps.googleusercontent.com) ;; *) warn "that doesn't end in .apps.googleusercontent.com; is it the client ID?" ;; esac
  while true; do
    read -r -s -p "  Client secret (input hidden): " secret || secret=""
    printf '\n'
    trim_var secret
    [ -n "$secret" ] || { note "No client secret entered."; return 1; }
    value_ok GOOGLE_CLIENT_SECRET secret && break
    secret=""
    note "Please enter it again (Enter goes back to the menu)."
  done
  add_op set GOOGLE_CLIENT_ID "$id"
  add_op set GOOGLE_CLIENT_SECRET "$secret"
  secret=""
  GOOGLE_RESULT="manual"
  note "Make sure the client's Authorized redirect URIs include exactly $REDIRECT."
}

choose_demo() {
  GOOGLE_RESULT="demo"
  if has GOOGLE_CLIENT_ID || has GOOGLE_CLIENT_SECRET; then
    add_op set GOOGLE_CLIENT_ID ""
    add_op set GOOGLE_CLIENT_SECRET ""
    note "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET will be cleared (the backup keeps the old values)."
  fi
  note "Demo mode: no Google sign-in. The app watches the local ./sample_docs folder instead of Drive."
  note "Run this script again any time to connect Drive."
}

if [ "$DEMO" -eq 1 ]; then
  choose_demo
elif [ -n "$GOOGLE_JSON" ]; then
  # Explicit file: used even with warnings (they're printed). A file that can't be
  # used (unreadable, or a value .env can't hold) stops a non-interactive run before
  # anything is written; interactively, the menu below lets you choose again.
  p="$GOOGLE_JSON"; clean_path p
  if check_client_json "$p"; then
    add_op clientjson - "$p"; GOOGLE_RESULT="json"
  else
    [ "$INTERACTIVE" -eq 1 ] || die "the Google client JSON can't be used (see above). .env was not changed."
    note "Choose another file or option below."
  fi
fi
if [ -n "$GOOGLE_RESULT" ]; then
  :
elif [ "$INTERACTIVE" -eq 1 ]; then
  while [ -z "$GOOGLE_RESULT" ]; do
    say ""
    note "How do you want to set up Google?"
    note "  1) Use the client JSON downloaded from Google Cloud (recommended)"
    note "     Google Auth Platform -> Clients -> your Web application client -> Download JSON"
    note "  2) Type the client ID and secret"
    note "  3) Skip: demo mode (local ./sample_docs folder, no Google sign-in)"
    if has GOOGLE_CLIENT_ID; then
      note "  4) Keep the current client (currently set)"; def=4
    else
      def=1
    fi
    read -r -p "  Choose [$def]: " choice || choice=""
    trim_var choice
    [ -n "$choice" ] || choice="$def"
    case "$choice" in
      1)
        read -r -e -p "  Path to the client JSON (drag the file here; Tab completes): " jpath || jpath=""
        clean_path jpath
        if [ -z "$jpath" ]; then note "No path entered."; continue; fi
        use_json "$jpath" || true
        ;;
      2) manual_google || true ;;
      3) choose_demo ;;
      4) if has GOOGLE_CLIENT_ID; then GOOGLE_RESULT="kept"; else note "Please choose 1, 2 or 3."; fi ;;
      *) note "Please choose 1, 2 or 3 (or press Enter for $def)." ;;
    esac
  done
else
  if has GOOGLE_CLIENT_ID; then
    GOOGLE_RESULT="kept"
  else
    choose_demo
  fi
fi

case "$GOOGLE_RESULT" in
  json|manual) set_status GOOGLE_CLIENT_ID set; set_status GOOGLE_CLIENT_SECRET set ;;
  kept) ok "Google client kept (currently set)." ;;
  demo) set_status GOOGLE_CLIENT_ID "empty (demo)"; set_status GOOGLE_CLIENT_SECRET "empty (demo)" ;;
esac

if [ "$GOOGLE_RESULT" != demo ] && [ "$HAS_DEMO_MODE" = 1 ]; then
  dm="$(tool get "$BASE" DEMO_MODE | tr '[:upper:]' '[:lower:]')"
  case "$dm" in
    true|1|yes|on)
      warn "DEMO_MODE is on in .env, which forces demo mode even with a Google client."
      if ask_yn "Clear DEMO_MODE so the app uses Google Drive?" y; then add_op set DEMO_MODE ""; DEMO_MODE_CLEARED=1; fi
      ;;
  esac
fi

# ---- 3. Local secrets --------------------------------------------------------------
step "Step 3/4  Local secrets (FERNET_KEY, SESSION_SECRET)"
note "FERNET_KEY encrypts the Google refresh token stored in Postgres; SESSION_SECRET signs"
note "the short-lived OAuth state cookie. Both are random and generated here."
ROTATED=0
if [ "$ROTATE" -eq 1 ] && { has FERNET_KEY || has SESSION_SECRET; }; then
  warn "--rotate-keys: new keys make the stored Google connection unreadable. After this you"
  warn "must click \"Connect Google Drive\" again (your data and watched folder are kept)."
  if [ "$INTERACTIVE" -eq 0 ] || ask_yn "Generate new FERNET_KEY and SESSION_SECRET?" n; then
    ROTATED=1
  else
    note "Keeping the current keys."
  fi
fi
for k in FERNET_KEY SESSION_SECRET; do
  if [ "$ROTATED" -eq 1 ] || ! has "$k"; then
    add_op gen "$k" ""; set_status "$k" generated
    ok "$k will be generated."
  else
    ok "$k kept (currently set)."
  fi
done
if [ "$ROTATED" -eq 0 ] && has FERNET_KEY; then
  note "FERNET_KEY is kept on purpose: changing it invalidates the stored Google connection."
  note "Use --rotate-keys only if you want new keys and are ready to reconnect Drive."
fi

# ---- 4. Postgres port --------------------------------------------------------------
step "Step 4/4  Postgres port"
CUR_PORT="$(tool get "$BASE" POSTGRES_PORT)"
[ -n "$CUR_PORT" ] || CUR_PORT=5432
case "$CUR_PORT" in *[!0-9]*) die "POSTGRES_PORT in .env is not a number" ;; esac
NEW_PORT=""
if port_in_use "$CUR_PORT"; then
  if port_is_ours "$CUR_PORT"; then
    ok "Port $CUR_PORT is used by this project's own Postgres container. That's fine."
  else
    cand="$ALT_PORT_START"
    while port_in_use "$cand" || [ "$cand" = "$CUR_PORT" ]; do
      cand=$((cand + 1))
      [ "$cand" -lt 65535 ] || die "couldn't find a free port"
    done
    warn "port $CUR_PORT is already used by another program (not this project's Postgres)."
    if ask_yn "Use port $cand for this project's Postgres instead?" y; then
      NEW_PORT="$cand"
    else
      note "Keeping $CUR_PORT. start.sh will stop until that port is free."
    fi
  fi
else
  ok "Port $CUR_PORT is free."
fi
if [ -n "$NEW_PORT" ]; then
  add_op port - "$NEW_PORT"
  set_status POSTGRES_PORT set; set_status DATABASE_URL set; set_status DATABASE_READONLY_URL set
  ok "POSTGRES_PORT, DATABASE_URL and DATABASE_READONLY_URL will use port $NEW_PORT."
  PORT_NOW="$NEW_PORT"
else
  PORT_NOW="$CUR_PORT"
  for k in DATABASE_URL DATABASE_READONLY_URL; do
    u="$(tool get "$BASE" "$k")"
    up="$(printf '%s' "$u" | sed -n -E 's#^[^/]*//([^@/]*@)?(localhost|127\.0\.0\.1):([0-9]+)/.*#\3#p')"
    if [ -n "$up" ] && [ "$up" != "$CUR_PORT" ]; then
      warn "$k points at localhost:$up but POSTGRES_PORT is $CUR_PORT. Make them the same."
    fi
  done
fi
# POSTGRES_PORT / DATABASE_* have defaults; report "kept" rather than "missing" for them.
for k in POSTGRES_PORT DATABASE_URL DATABASE_READONLY_URL; do
  [ "$(get_status "$k")" = missing ] && set_status "$k" "default"
done

# ---- write -------------------------------------------------------------------------
step "Saving .env"
if [ "$CREATING" -eq 0 ] && [ "${#OP_T[@]}" -eq 0 ]; then
  chmod 600 "$ENV_FILE"
  ok "Nothing to change. .env left as it was (permissions set to 600)."
else
  # env_tool.py validates every value again (a backstop: they were checked when
  # collected), and only then, if .env exists and its content really changes, copies
  # it to BACKUP before replacing it. A refused value leaves no backup (BUG-Q7-03).
  BACKUP=""
  if [ "$CREATING" -eq 0 ]; then
    BACKUP="$ROOT/.env.bak.$(date +%Y%m%d-%H%M%S)"
    n=1
    while [ -e "$BACKUP" ]; do BACKUP="$ROOT/.env.bak.$(date +%Y%m%d-%H%M%S).$n"; n=$((n + 1)); done
  fi
  i=0
  if ! WRITE_RESULT="$(while [ "$i" -lt "${#OP_T[@]}" ]; do
         printf '%s\0%s\0%s\0' "${OP_T[$i]}" "${OP_K[$i]}" "${OP_V[$i]}"
         i=$((i + 1))
       done | tool write "$BASE" "$ENV_FILE" ${BACKUP:+"$BACKUP"})"; then
    die "could not write .env (it was not changed)"
  fi
  case "$WRITE_RESULT" in
    unchanged)
      chmod 600 "$ENV_FILE"
      ok "Nothing to change. .env left as it was (permissions set to 600)." ;;
    *)
      WROTE=1
      [ "$WRITE_RESULT" = backup ] && ok "Backed up the old .env to ${BACKUP#"$ROOT"/} (git-ignored, mode 600)."
      if [ "$CREATING" -eq 1 ]; then ok "Created .env from .env.example (mode 600)."; else ok "Updated .env in place (mode 600)."; fi ;;
  esac
fi
OP_V=()

# ---------------------------------------------------------------------------- summary
FINAL_HAS_ANTHROPIC=0
FINAL_HAS_GOOGLE=0
while read -r k v; do
  case "$k" in
    ANTHROPIC_API_KEY) FINAL_HAS_ANTHROPIC="$v" ;;
    GOOGLE_CLIENT_ID) FINAL_HAS_GOOGLE="$v" ;;
  esac
done < <(tool status "$ENV_FILE" ANTHROPIC_API_KEY GOOGLE_CLIENT_ID)
FINAL_DEMO_FLAG="$(tool get "$ENV_FILE" DEMO_MODE | tr '[:upper:]' '[:lower:]')"
DEMO_ON=0
case "$FINAL_DEMO_FLAG" in true|1|yes|on) DEMO_ON=1 ;; esac
[ "$FINAL_HAS_GOOGLE" = 1 ] || DEMO_ON=1
if [ "$DEMO_ON" -eq 1 ] && [ "$(get_status ANTHROPIC_API_KEY)" = empty ]; then
  set_status ANTHROPIC_API_KEY "empty (fixtures)"
elif [ "$(get_status ANTHROPIC_API_KEY)" = empty ]; then
  set_status ANTHROPIC_API_KEY missing
fi

step "Summary"
printf '  %-24s %s\n' "KEY" "STATUS"
printf '  %-24s %s\n' "------------------------" "----------------"
for k in $SUMMARY_KEYS; do
  s="$(get_status "$k")"
  c=""
  case "$s" in missing) c="$C_R" ;; set|generated) c="$C_G" ;; "empty"*) c="$C_Y" ;; esac
  printf '  %-24s %s%s%s\n' "$k" "$c" "$s" "$C_0"
done
[ "${DEMO_MODE_CLEARED:-0}" = 1 ] && printf '  %-24s %s\n' "DEMO_MODE" "cleared"
note "Postgres host port: $PORT_NOW"

say ""
if [ "$DEMO_ON" -eq 1 ]; then
  if [ "$FINAL_HAS_ANTHROPIC" = 1 ]; then
    say "${C_B}Mode: demo with Claude${C_0}: watches ./sample_docs; extraction and Ask use your Anthropic key."
  else
    say "${C_B}Mode: demo with fixtures${C_0}: watches ./sample_docs and loads committed extractions; Ask is disabled."
  fi
else
  say "${C_B}Mode: Google Drive${C_0}: you'll connect Drive and pick a folder in the browser."
  [ "$FINAL_HAS_ANTHROPIC" = 1 ] || warn "ANTHROPIC_API_KEY is empty, but Drive mode needs it. Re-run this script to add it."
fi

for k in ANTHROPIC_API_KEY GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET DEMO_MODE; do
  if printenv "$k" >/dev/null 2>&1; then
    warn "$k is also set in your shell environment. It overrides .env when you run ./start.sh (unset $k to use .env)."
  fi
done
[ "$ROTATED" -eq 1 ] && warn "keys were rotated: after ./start.sh, click \"Connect Google Drive\" again."
[ "$(get_status ANTHROPIC_API_KEY)" = set ] && note "Reminder: a valid Anthropic key still needs credits (Console -> Settings -> Billing)."

say ""
say "${C_B}Next:${C_0} ./start.sh   (macOS: or double-click start.command)"
if [ "$DEMO_ON" -eq 0 ]; then
  say "Before you connect, in the Google Cloud console:"
  note "- Google Auth Platform -> Audience -> Test users: add the Google account whose Drive you'll use"
  note "- APIs & Services -> Library -> Google Drive API: Enable"
  note "- Your client's Authorized redirect URIs include exactly $REDIRECT"
  note "- On Google's consent screen, tick \"See and download all your Google Drive files\""
fi
