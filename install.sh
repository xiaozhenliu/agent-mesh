#!/usr/bin/env bash
set -euo pipefail

VERSION="v0.12.17"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROJECT_VERSION="$(cat "$HERE/VERSION")"
BIN_DIR="$HOME/.local/bin"; RN="$BIN_DIR/reason-network"
CONF_DIR="$HOME/.config/agent-relay"; ENV_FILE="$CONF_DIR/env"; KEY_FILE="$CONF_DIR/outbound.key"
LIB_DIR="$HOME/.local/lib/agent-mesh"; RELAY="$LIB_DIR/agent_relay.py"
LABEL="com.local.agent-relay"; PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/agent-relay.log"
ROLLBACK="$LIB_DIR/rollback"
MARKER="$HOME/Library/Application Support/agent-relay/registered"
CFG="$HOME/.reason/network/config.toml"
export REASON_NETWORK_NO_PROMPT=1
. "$HERE/scripts/msg.sh"

NAME=""; MACHINE="mac"; YES=0; MODE=full; DOC_LANG=""; RELAY_NAME_ARG=""; GROK_NAME=""; GROK_SET=0
lang_next=0
for arg in "$@"; do
  [ "$lang_next" = 0 ] || DOC_LANG="$arg"
  if [ "$arg" = --lang ]; then lang_next=1; else lang_next=0; fi
done
if [ -z "$DOC_LANG" ] && command -v python3 >/dev/null; then
  DOC_LANG="$(python3 "$HERE/scripts/deploy_state.py" lang 2>/dev/null)" || DOC_LANG=""
fi
[ -n "$DOC_LANG" ] || DOC_LANG="${AGENT_MESH_LANG:-en}"
export AGENT_MESH_LANG="$DOC_LANG"
both_langs() { printf '%s / %s\n' "$(AGENT_MESH_LANG=en tq "$@")" "$(AGENT_MESH_LANG=zh-CN tq "$@")"; }
REPO_URL="https://github.com/xiaozhenliu/agent-mesh.git"
value() { [ "$1" -ge 2 ] && [ -n "$3" ] || { t install.option_value "$2" >&2; exit 2; }; }
while [ $# -gt 0 ]; do
  case "$1" in
    --name) value $# "$1" "${2:-}"; NAME="$2"; shift 2 ;;
    --machine) value $# "$1" "${2:-}"; MACHINE="$2"; shift 2 ;;
    --relay-name) value $# "$1" "${2:-}"; RELAY_NAME_ARG="$2"; shift 2 ;;
    --grok-name) [ $# -ge 2 ] || { t install.grok_name_value >&2; exit 2; }
      GROK_NAME="$2"; GROK_SET=1; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    --webhook) MODE=webhook; shift ;;
    --cloud-prompt) MODE=cloud; shift ;;
    --rotate-key) MODE=rotate; shift ;;
    --purge-copies) MODE=purge; shift ;;
    --restart-relay) MODE=restart; shift ;;
    --preflight) MODE=preflight; shift ;;
    --fetch-engine) value $# "$1" "${2:-}"; MODE=fetch; FETCH_DIR="$2"; shift 2 ;;
    --repo-url) value $# "$1" "${2:-}"; REPO_URL="$2"; shift 2 ;;
    --version) echo "agent-mesh $PROJECT_VERSION (Reason Network ${VERSION#v})"; exit 0 ;;
    --lang) [ $# -ge 2 ] || { both_langs install.lang_value >&2; exit 2; }; DOC_LANG="$2"; shift 2 ;;
    -h|--help) t install.help; exit 0 ;;
    *) { t install.unknown_option "$1"; t install.see_help; } >&2; exit 2 ;;
  esac
done
case "$DOC_LANG" in
  en|zh-CN) ;;
  *) both_langs install.lang_unsupported "$DOC_LANG" >&2; exit 2 ;;
esac
export AGENT_MESH_LANG="$DOC_LANG"
if [ "$GROK_SET" = 1 ]; then
  case "$MODE" in
    full) [ -n "$NAME" ] || MODE=label ;;
    webhook|cloud) ;;
    *) t install.grok_name_mode >&2; exit 2 ;;
  esac
fi

say() { printf '\n== %s\n' "$*"; }
die() { echo "install: $*" >&2; exit 1; }
valid_name() { [[ "$1" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] && [ "${#1}" -le 40 ]; }
current_person() { if [ -f "$CFG" ]; then sed -n '/^person = "\(.*\)"/{s//\1/p;q;}' "$CFG"; fi; }
render_prompt() { "$PYTHON" "$HERE/scripts/render_prompt.py" "$@" --lang "$DOC_LANG"; }
deploy_state() { "$PYTHON" "$HERE/scripts/deploy_state.py" "$@"; }
relay_names() { "$PYTHON" "$HERE/scripts/relay_names.py" "$@"; }
rn_engine() { "$PYTHON" "$HERE/box/rn-engine.py" "$@"; }

pinned_sha256() {   # the same table is in box/rn-cloud-setup.sh; change both with VERSION
  case "$1" in
    v0.12.17/aarch64-apple-darwin) echo 6676e806abba74b313ee027b5b4020634077b0ef4c01c0a9eb4dd61ec77ea705 ;;
    v0.12.17/x86_64-apple-darwin) echo e71fbd5c7c080048c1220025e2aa716c3af6b18901f18e5beece1323cfcdd96f ;;
    v0.12.17/aarch64-unknown-linux-musl) echo e53ef46ee57eb9e9cd3b13d45acfe5ce4766c18861b46926560e34903adaa151 ;;
    v0.12.17/x86_64-unknown-linux-musl) echo 2234527ff9330c78bff8581cc060bf1396e15d8091c27fb5acdb989010f5018f ;;
    *) return 1 ;;
  esac
}

fetch_engine() {   # $1: empty directory; leaves the verified binary at $1/reason-network
  local target want got
  case "$(uname -m)" in
    arm64) target=aarch64-apple-darwin ;;
    x86_64) target=x86_64-apple-darwin ;;
    *) die "$(tq install.cpu "$(uname -m)")" ;;
  esac
  want="$(pinned_sha256 "$VERSION/$target")" || die "$(tq install.no_pin "$VERSION" "$target")"
  curl -fsSL "https://github.com/reason-machines/reason-network/releases/download/$VERSION/reason-network-$target.tar.gz" \
    -o "$1/e.tgz" || die "$(tq install.download_failed "$VERSION")"
  got="$(shasum -a 256 "$1/e.tgz" | cut -d' ' -f1)"
  [ "$got" = "$want" ] || die "$(tq install.checksum "$target" "$want" "$got")"
  tar xzf "$1/e.tgz" -C "$1" reason-network || die "$(tq install.no_binary)"
  rm -f "$1/e.tgz"
  chmod 755 "$1/reason-network"
  [ "$("$1/reason-network" --version 2>&1 | awk '{print $2}')" = "${VERSION#v}" ] \
    || die "$(tq install.binary_version "${VERSION#v}")"
}

rename_refused() {
  die "$(tq install.rename_refused "$1" "$1")"
}
have_webhook() { [ -f "$ENV_FILE" ] && grep -Eq '^WEBHOOK_URL=https://' "$ENV_FILE" && grep -Eq '^WEBHOOK_KEY=.{8,}' "$ENV_FILE"; }

relay_env() {   # NAME=value for each given name set in the env file, parsed like agent_relay.load_env (last wins)
  "$PYTHON" - "$ENV_FILE" "$@" <<'PY'
import os, sys
cfg = {}
for line in open(sys.argv[1], encoding="utf-8").read().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip().strip('"').strip("'")
for k in sys.argv[2:]:
    if k in cfg:
        v = os.path.expanduser(cfg[k]) if k == "OUTBOUND_KEY_FILE" else cfg[k]
        print(f"{k}={v}")
PY
}

env_text() {
  cat <<EOF
# agent-relay config (mode 600). Never paste these values into a chat.
RN_BIN=$RN
PERSON=$(current_person)
EOF
  [ -z "$1" ] || printf 'WEBHOOK_URL=%s\nWEBHOOK_KEY=%s\n' "$1" "$2"
  cat <<EOF
OUTBOUND_KEY_FILE=$KEY_FILE
# The Bot address name, chosen with install.sh --relay-name; renaming is not supported, do not edit these three.
LISTEN_NAME=$RELAY_NAME
CLOUD_NAME=$RELAY_NAME-cloud
INBOX_NAME=$RELAY_NAME-inbox
# Optional, shown with defaults:
# CLOUD_MACHINE=cloud
# MAX_WAKES_PER_HOUR=20  MIN_SECONDS_BETWEEN_WAKES=20  MAX_BATCH=10  MAX_PROXY_PER_HOUR=60  MAX_SPOOL=1000
# WEBHOOK_MAX_BODY_CHARS=3500  DOORBELL_PREVIEW_CHARS=200  FWD_COPY_DAYS=7  REFWD_MAX_PER_ID=2  REFWD_MAX_PER_HOUR=20
# GOT_TIMEOUT_SECONDS=900  GOT_DONE_SECONDS=7200  GOT_MAX_RERINGS=2  NONCE_KEEP_SECONDS=86400
EOF
}

put_env() {
  local tmp
  rm -f "$CONF_DIR"/.env.??????
  tmp="$(mktemp "$CONF_DIR/.env.XXXXXX")" || die "$(tq install.tmp_failed "$CONF_DIR" "$ENV_FILE")"
  if ! { env_text "$1" "$2" > "$tmp" && { [ -z "$3" ] || printf '%s\n' "$3" >> "$tmp"; } \
      && chmod 600 "$tmp" && mv -f "$tmp" "$ENV_FILE"; }; then
    rm -f "$tmp"
    die "$(tq install.env_write_failed "$ENV_FILE")"
  fi
}

seed_env() {
  [ ! -f "$ENV_FILE" ] || return 0
  put_env "" "" ""
}

write_env() {   # $1 url, $2 key; keeps the user's optional settings (e.g. DOORBELL_PREVIEW_CHARS=0) from the old file
  local kept=""
  [ ! -f "$ENV_FILE" ] || kept="$(relay_env CLOUD_MACHINE MAX_WAKES_PER_HOUR \
    MIN_SECONDS_BETWEEN_WAKES MAX_BATCH MAX_PROXY_PER_HOUR MAX_SPOOL WEBHOOK_MAX_BODY_CHARS DOORBELL_PREVIEW_CHARS \
    FWD_COPY_DAYS REFWD_MAX_PER_ID REFWD_MAX_PER_HOUR GOT_TIMEOUT_SECONDS GOT_DONE_SECONDS GOT_MAX_RERINGS \
    NONCE_KEEP_SECONDS)" || die "$(tq install.env_read_failed "$ENV_FILE")"
  put_env "$1" "$2" "$kept"
}

bot_label() {
  deploy_state label --person "$(current_person)" --relay-name "$RELAY_NAME" 2>/dev/null \
    || echo "$(current_person)/$RELAY_NAME"
}

reply_templates() {
  "$PYTHON" "$HERE/scripts/reply_convention.py" install \
    || t install.reply_failed "$LIB_DIR/templates"
}

new_install_stop() {
  local hint="" suggested=""
  if [ "$GROK_SET" = 1 ]; then
    if suggested="$(relay_names suggest "$GROK_NAME")"; then
      hint=" $(tq install.hint_suggested "$suggested")"
    else
      hint=" $(tq install.hint_no_latin)"
    fi
  fi
  die "$(tq install.need_relay_name "$hint")"
}

record_pending() {
  deploy_state mark "$1" pending --lang "$DOC_LANG" --relay-name "$RELAY_NAME" \
    || t install.record_failed "$LIB_DIR/deployed.json"
}

service() { echo "gui/$(id -u)/$LABEL"; }
relay_loaded() { launchctl print "$(service)" >/dev/null 2>&1; }
relay_running() { launchctl print "$(service)" 2>/dev/null | grep 'state = running' >/dev/null; }
relay_pid() { launchctl print "$(service)" 2>/dev/null | sed -n 's/^[[:space:]]*pid = \([0-9][0-9]*\)$/\1/p' | head -n 1; }

log_inode() { "$PYTHON" -c 'import os, sys; print(os.stat(sys.argv[1]).st_ino)' "$LOG"; }

log_mark() {
  LOG_INODE=""; LOG_SIZE=0
  if [ -f "$LOG" ]; then LOG_INODE="$(log_inode)"; LOG_SIZE="$(wc -c < "$LOG")"; fi
}

log_since() {
  [ -f "$LOG" ] || return 0
  if [ -n "$LOG_INODE" ] && [ "$(log_inode)" = "$LOG_INODE" ]; then
    tail -c +"$((LOG_SIZE + 1))" "$LOG"
  else
    cat "$LOG"
  fi
}

bootstrap_and_wait() {
  log_mark
  launchctl bootstrap "gui/$(id -u)" "$PLIST" || { t install.bootstrap_failed; return 1; }
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    if log_since | grep "agent-relay up" >/dev/null; then
      sleep 3
      relay_running && return 0
      t install.up_then_died
      return 1
    fi
  done
  t install.no_up
  return 1
}

save_copy() {
  [ -f "$2" ] || return 0
  cp -p "$2" "$1/$3" && cmp -s "$2" "$1/$3" && ( cd "$1" && shasum -a 256 "$3" >> SHA256SUMS )
}

backup_relay() {
  local tmp="$ROLLBACK.tmp"
  rm -rf "$tmp"
  mkdir -p "$tmp" || return 1
  if relay_loaded; then echo 1 > "$tmp/loaded"; else echo 0 > "$tmp/loaded"; fi
  ( cd "$tmp" && shasum -a 256 loaded > SHA256SUMS ) \
    && save_copy "$tmp" "$RELAY" agent_relay.py && save_copy "$tmp" "$PLIST" agent.plist \
    && mv "$tmp" "$ROLLBACK"
}

restore_file() {
  if [ -f "$ROLLBACK/$1" ]; then
    cp -p "$ROLLBACK/$1" "$2.restore.$$" && mv -f "$2.restore.$$" "$2"
  else
    rm -f "$2"
  fi
}

restore_relay() {
  ( cd "$ROLLBACK" && shasum -a 256 -c --status SHA256SUMS ) \
    || { t install.rollback_bad "$ROLLBACK"; return 1; }
  launchctl bootout "$(service)" 2>/dev/null || true
  if relay_loaded; then t install.unload_failed "$ROLLBACK"; return 1; fi
  rm -f "$MARKER"
  restore_file agent_relay.py "$RELAY" && restore_file agent.plist "$PLIST" \
    || { t install.restore_files_failed "$ROLLBACK"; return 1; }
  if [ "$(cat "$ROLLBACK/loaded")" = 1 ] && [ -f "$PLIST" ]; then
    bootstrap_and_wait || { t install.previous_not_up "$LOG"; return 1; }
  fi
  rm -rf "$ROLLBACK"
}

relay_interrupted() {
  trap '' INT TERM HUP
  echo
  t install.interrupted
  restore_relay || t install.not_fully_restored
  exit "$1"
}

start_relay() {
  mkdir -p "$LIB_DIR" "$HOME/Library/Logs" "$HOME/Library/LaunchAgents"
  if [ -d "$ROLLBACK" ]; then
    t install.unfinished_switch
    restore_relay || return 1
  fi
  if ! backup_relay; then
    rm -rf "$ROLLBACK.tmp" "$ROLLBACK"
    t install.backup_failed
    return 1
  fi
  local new_relay="" new_plist=""
  if ! { new_relay="$(mktemp "$LIB_DIR/.agent_relay.py.XXXXXX")" \
      && new_plist="$(mktemp "$HOME/Library/LaunchAgents/.$LABEL.XXXXXX")" \
      && install -m 644 "$HERE/agent_relay.py" "$new_relay" \
      && "$PYTHON" "$HERE/scripts/write_launch_agent.py" "$RELAY" "$new_plist" \
      && chmod 644 "$new_plist" && plutil -lint "$new_plist" >/dev/null; }; then
    rm -f "$new_relay" "$new_plist"
    rm -rf "$ROLLBACK"
    t install.prepare_failed
    return 1
  fi
  trap 'rm -f "$new_relay" "$new_plist"; relay_interrupted 130' INT
  trap 'rm -f "$new_relay" "$new_plist"; relay_interrupted 143' TERM
  trap 'rm -f "$new_relay" "$new_plist"; relay_interrupted 129' HUP
  launchctl bootout "$(service)" 2>/dev/null || true
  rm -f "$MARKER"
  if mv -f "$new_relay" "$RELAY" && mv -f "$new_plist" "$PLIST" && bootstrap_and_wait \
      && { [ "${1:-}" != verify ] || verify_registration; }; then
    trap - INT TERM HUP
    rm -rf "$ROLLBACK"
    t install.relay_up "$LOG"
    return 0
  fi
  trap '' INT TERM HUP
  rm -f "$new_relay" "$new_plist"
  t install.last_log
  tail -n 10 "$LOG" "$HOME/Library/Logs/agent-relay.launchd.log" 2>/dev/null || true
  t install.restoring
  if restore_relay; then t install.restored; else t install.not_fully_restored; fi
  trap - INT TERM HUP
  return 1
}

registration() {   # the new relay's own marker (removed before it started), then the engine's session row
  local rc=0 waited=0 want mark m_name m_pid _ pid
  want="$(current_person)/$RELAY_NAME"
  REG_ACTUAL=""
  while :; do
    rc=0; mark="$(relay_names marker "$MARKER" 2>&1)" || rc=$?
    if [ "$rc" = 0 ]; then
      read -r m_name m_pid _ <<< "$mark"
      if [ "$m_name" != "$want" ]; then REG_ACTUAL="$m_name"; return 1; fi
      pid="$(relay_pid)"
      if [ -z "$pid" ]; then
        REG_ACTUAL="$(tq install.reg_no_process)"
      elif [ "$pid" = "$m_pid" ]; then
        rc=0; REG_ACTUAL="$(relay_names registered "$RELAY_NAME" 2>&1)" || rc=$?
        if [ "$rc" = 0 ] && [ "$REG_ACTUAL" = "$RELAY_NAME" ]; then
          t install.registered "$want"
          return 0
        fi
        if [ "$rc" != 0 ]; then REG_ACTUAL="$(tq install.reg_nothing "$REG_ACTUAL")"
        elif [ -n "$REG_ACTUAL" ]; then REG_ACTUAL="$(current_person)/$REG_ACTUAL"; return 1; fi
      fi
    elif [ "$rc" != 3 ]; then
      REG_ACTUAL="$(tq install.reg_nothing "$mark")"
    fi
    [ "$waited" -lt "${AGENT_MESH_REGISTER_WAIT:-45}" ] || break
    sleep 1; waited=$((waited + 1))
  done
  [ -n "$REG_ACTUAL" ] || REG_ACTUAL="$(tq install.reg_timeout "${AGENT_MESH_REGISTER_WAIT:-45}")"
  return 1
}

check_registered() {
  registration && return 0
  die "$(tq install.registered_other "$REG_ACTUAL" "$(current_person)/$RELAY_NAME" "$RELAY_NAME")"
}

verify_registration() {
  registration && return 0
  t install.new_registered_other "$REG_ACTUAL" "$(current_person)/$RELAY_NAME"
  return 1
}

webhook_step() {
  [ -n "$(current_person)" ] && [ -s "$KEY_FILE" ] \
    || die "$(tq install.need_team_key)"
  mkdir -p "$LIB_DIR" "$HOME/Library/Logs" "$HOME/Library/LaunchAgents"
  seed_env
  reply_templates
  if [ "$MODE" = webhook ] || ! have_webhook; then
    record_pending prompt
    t install.routine_intro
    echo "----------------------------------------------------------------"
    deploy_state show-prompt --lang "$DOC_LANG" --relay-name "$RELAY_NAME"
    echo "----------------------------------------------------------------"
    t install.routine_panel
    if [ ! -t 0 ]; then
      t install.webhook_no_tty
      t install.webhook_run_later "./install.sh --webhook --lang $DOC_LANG"
      exit 0
    fi
    read -r -p "$(tq install.ask_url)" url
    read -r -s -p "$(tq install.ask_key)" key; echo
    [[ "$url" == https://* ]] || die "$(tq install.url_https)"
    [ -n "$key" ] || die "$(tq install.both_required)"
    write_env "$url" "$key"
  fi
  chmod 600 "$ENV_FILE"
  start_relay
  check_registered
}

cloud_prompt() {
  local person repo
  person="$(current_person)"; [ -n "$person" ] || die "$(tq install.no_team)"
  repo="$REPO_URL"
  local invite release tag="" commit="" sums="" k a b
  release="$("$PYTHON" "$HERE/scripts/deploy_state.py" release --repo-url "$repo" 2>&1)" \
    || die "$(tq install.release_failed "$release")"
  while read -r k a b; do
    case "$k" in tag) tag="$a" ;; commit) commit="$a" ;; sum) sums="$sums$a  $b"$'\n' ;; esac
  done <<< "$release"
  invite="$("$RN" invite --me | sed -n 's/^For a cloud agent of yours.*send it: //p')"
  [ -n "$invite" ] || invite="$("$RN" invite --me)"
  record_pending cloud
  record_pending cloud_setup
  record_pending cloud_engine
  t install.cloud_intro
  echo "----------------------------------------------------------------"
  echo "$invite"
  echo
  render_prompt cloud-setup --team-name "$person" --mac-home "$HOME" --repo-url "$repo" --relay-name "$RELAY_NAME" \
    --release-tag "$tag" --release-commit "$commit" --cloud-checksums "$sums"
  echo "----------------------------------------------------------------"
  t install.copy_key "$KEY_FILE"
  t install.bot_address_cloud "$(bot_label)" "$RELAY_NAME-cloud" "$RELAY_NAME-inbox"
}

grok_label() {
  t install.bot_address "$(bot_label)"
  [ -z "$(current_person)" ] || reply_templates
  t install.refresh_copies
  echo "  python3 scripts/reply_convention.py refresh <repository>/AGENTS.md"
}

# The relay reads the key only at start, so a new key always means a restart. The old key is not kept: rotation is
# for a key that may have leaked, and the cloud must get the new one either way.
rotate_key() {
  [ -n "$(current_person)" ] && [ -s "$KEY_FILE" ] \
    || die "$(tq install.need_team_key)"
  have_webhook || die "$(tq install.rotate_unconfigured)"
  # Rotating $KEY_FILE only helps if that is the key the relay actually loads.
  local src
  src="$(relay_env OUTBOUND_KEY OUTBOUND_KEY_FILE)" || die "$(tq install.rotate_read_failed "$ENV_FILE")"
  if grep -Eq '^OUTBOUND_KEY=.' <<<"$src"; then
    die "$(tq install.rotate_inline "$ENV_FILE" "$KEY_FILE")"
  fi
  [ "$(printf '%s\n' "$src" | sed -n 's/^OUTBOUND_KEY_FILE=//p')" = "$KEY_FILE" ] \
    || die "$(tq install.rotate_other_file "$ENV_FILE" "$KEY_FILE")"
  t install.rotate_warning "$KEY_FILE"
  if [ "$YES" != 1 ]; then
    if [ -t 0 ]; then
      read -r -p "$(tq install.ask_rotate)" a
      [[ "$a" == [yY]* ]] || die "$(tq install.rotate_stopped)"
    else
      t install.rotate_no_tty
      exit 3
    fi
  fi
  local new="$KEY_FILE.new.$$"
  if ! ( umask 077; openssl rand -hex 32 > "$new" ) || [ "$(wc -c < "$new")" -lt 64 ]; then
    rm -f "$new"; die "$(tq install.keygen_failed)"
  fi
  chmod 600 "$new"; mv -f "$new" "$KEY_FILE"
  t install.key_written "$KEY_FILE"
  mkdir -p "$LIB_DIR" "$HOME/Library/Logs" "$HOME/Library/LaunchAgents"
  local rc=0
  start_relay || rc=$?
  echo "----------------------------------------------------------------"
  t install.rotate_copy "$KEY_FILE"
  t install.rotate_after
  t install.rotate_leak "./install.sh --webhook --lang $DOC_LANG"
  [ "$rc" = 0 ] || die "$(tq install.rotate_restart_failed)"
}

# Copies exist only so the cloud can ask for a lost message again (RN-REFWD). The relay deletes expired ones
# (FWD_COPY_DAYS) only when later mail passes through, so an idle relay keeps them. Safe while the relay runs: a
# missing copy is answered with no-copy. The loop catches a copy renamed from .tmp to .json while we delete.
purge_copies() {
  local state="$HOME/Library/Application Support/agent-relay"
  local dir="$state/fwd-copies" files=() removed=0
  [ ! -L "$state" ] && [ ! -L "$dir" ] || die "$(tq install.purge_symlink "$dir")"
  for _ in 1 2 3 4 5; do
    files=()
    if [ -d "$dir" ]; then
      shopt -s nullglob; files=("$dir"/*.json "$dir"/*.tmp); shopt -u nullglob
    fi
    [ "${#files[@]}" != 0 ] || break
    rm -f -- "${files[@]}"
    removed=$((removed + ${#files[@]}))
  done
  [ "${#files[@]}" = 0 ] || die "$(tq install.purge_racing)"
  t install.purged "$removed" "$dir"
  t install.purge_after
}

# Validate the user's machine in every mode, before downloads or configuration changes.
[ "$MODE" != full ] || [ -n "$NAME" ] || die "$(tq install.need_name)"
[ "$(uname -s)" = Darwin ] || die "$(tq install.need_macos)"
[ "$(id -u)" != 0 ] || die "$(tq install.no_sudo)"
case "$(uname -m)" in arm64|x86_64) ;; *) die "$(tq install.cpu "$(uname -m)")" ;; esac
[[ "$HOME" = /* ]] && [ -d "$HOME" ] && [ -w "$HOME" ] || die "$(tq install.bad_home)"
for tool in python3 curl tar shasum openssl launchctl plutil; do
  command -v "$tool" >/dev/null || die "$(tq install.missing_tool "$tool")"
done
PYTHON="$(python3 -c 'import sys; print(sys.executable)')" \
  || die "$(tq install.python_broken)"
"$PYTHON" -c 'import sys, ssl, sqlite3, plistlib; sys.exit(sys.version_info < (3, 9))' \
  || die "$(tq install.python_old)"
if [ "$MODE" = fetch ]; then
  [ -d "$FETCH_DIR" ] && "$PYTHON" -c 'import os, sys; sys.exit(bool(os.listdir(sys.argv[1])))' "$FETCH_DIR" \
    || die "$(tq install.fetch_dir --fetch-engine)"
  fetch_engine "$FETCH_DIR"
  echo "$FETCH_DIR/reason-network"
  exit 0
fi
launchctl print "gui/$(id -u)" >/dev/null 2>&1 \
  || die "$(tq install.no_gui)"
[ "${REASON_NETWORK_HOME:-$HOME/.reason/network}" = "$HOME/.reason/network" ] \
  || die "$(tq install.custom_rn_home)"
export REASON_NETWORK_HOME="$HOME/.reason/network"
case "$REPO_URL" in https://*) ;; *) die "$(tq install.repo_url --repo-url)" ;; esac
if [ -n "$RELAY_NAME_ARG" ]; then
  why="$(relay_names check "$RELAY_NAME_ARG" 2>&1)" || die "--relay-name: $why"
fi
if [ "$GROK_SET" = 1 ]; then
  why="$(relay_names grok-check "$GROK_NAME" 2>&1)" || die "--grok-name: $why"
fi
CURRENT="$(relay_names env "$ENV_FILE" 2>&1)" \
  || die "$(tq install.env_names "$CURRENT")"
[ -z "$RELAY_NAME_ARG" ] || [ -z "$CURRENT" ] || [ "$RELAY_NAME_ARG" = "$CURRENT" ] || rename_refused "$CURRENT"
RECORDED_NAME="$(deploy_state recorded-name 2>/dev/null)" || RECORDED_NAME=""
RELAY_NAME="${CURRENT:-${RELAY_NAME_ARG:-${RECORDED_NAME:-bot}}}"
recorded="$(deploy_state check-name --relay-name "$RELAY_NAME" 2>&1)" || die "$recorded"
case "$recorded" in
  "changed "*)
    recorded="${recorded#changed }"; recorded="${recorded%% *}"
    [ -n "$CURRENT" ] || die "$(tq install.recorded_other "$recorded" "$LIB_DIR/deployed.json" "$RELAY_NAME" "$recorded")"
    die "$(tq install.env_other "$recorded" "$ENV_FILE" "$CURRENT" "$recorded")" ;;
esac

# Never replace a user's different engine version or create a second PATH installation silently.
existing="$(command -v reason-network || true)"
[ -z "$existing" ] || [ "$existing" = "$RN" ] || die "$(tq install.other_engine "$existing" "$RN")"
ENGINE_STATE=absent
if [ -x "$RN" ]; then
  installed_version="$("$RN" --version 2>/dev/null | awk '{print $2}')" || installed_version=""
  ENGINE_STATE="$(rn_engine compare "$installed_version" "${VERSION#v}")"
  case "$ENGINE_STATE" in
    same) ;;
    older)
      case "$MODE" in
        full) die "$(tq install.engine_older "$installed_version" "${VERSION#v}")" ;;
        preflight) echo "engine: Reason Network $installed_version is older than ${VERSION#v}; ./update.sh upgrades it" ;;
      esac ;;
    *) die "$(tq install.engine_unsupported "${installed_version:-$(tq install.unknown_version)}" "${VERSION#v}")" ;;
  esac
fi

record_grok() {
  [ "$GROK_SET" = 1 ] || return 0
  [ -n "$CURRENT" ] || [ "$recorded" != unknown ] || die "$(tq install.label_nothing)"
  why="$(deploy_state set-grok-name --relay-name "$RELAY_NAME" -- "$GROK_NAME" 2>&1)" || die "--grok-name: $why"
}

case "$MODE" in
  label) record_grok; grok_label; exit 0 ;;
  webhook)
    [ -x "$RN" ] || die "$(tq install.no_engine)"
    [ -n "$(current_person)" ] && [ -s "$KEY_FILE" ] || die "$(tq install.need_team_key)"
    record_grok; webhook_step; exit 0 ;;
  cloud)
    [ -x "$RN" ] || die "$(tq install.no_engine)"
    [ -n "$(current_person)" ] || die "$(tq install.no_team)"
    if [ "$GROK_SET" = 1 ]; then record_grok; reply_templates; fi
    cloud_prompt; exit 0 ;;
  rotate) [ -x "$RN" ] || die "$(tq install.no_engine)"; rotate_key; exit 0 ;;
  purge) purge_copies; exit 0 ;;
  restart|preflight)
    [ -x "$RN" ] || die "$(tq install.no_engine)"
    [ -n "$(current_person)" ] && [ -s "$KEY_FILE" ] || die "$(tq install.need_team_key)"
    have_webhook || die "$(tq install.not_configured)"
    if [ "$MODE" = preflight ]; then
      [ -f "$PLIST" ] || die "$(tq install.no_plist)"
      exit 0
    fi
    chmod 600 "$ENV_FILE"
    start_relay verify || die "$(tq install.not_switched)"
    exit 0 ;;
esac

[ -n "$NAME" ] || die "$(tq install.need_name)"
valid_name "$NAME" && valid_name "$MACHINE" \
  || die "$(tq install.name_rule)"
if [ -z "$CURRENT" ] && [ -z "$RELAY_NAME_ARG" ] && [ "$recorded" = unknown ] && [ -z "$(current_person)" ]; then
  new_install_stop
fi
if [ "$GROK_SET" = 1 ]; then
  why="$(deploy_state set-grok-name --check --relay-name "$RELAY_NAME" -- "$GROK_NAME" 2>&1)" || die "--grok-name: $why"
fi

say "$(tq install.step_network)"
if relays="$("$PYTHON" "$HERE/scripts/relay_targets.py" --rn "$RN" --home "$HOME/.reason/network" --pinned "${VERSION#v}")"; then
  for url in $(printf '%s\n' "$relays" | tail -n +2); do
    code="$(curl -sS -o /dev/null -m 10 -w '%{http_code}' "$url" 2>/dev/null || true)"
    [ "$code" = 200 ] || die "$(tq install.relay_unreachable "$url" "$code" "$(printf '%s\n' "$relays" | head -n 1)")"
  done
  t install.relay_ok "$(printf '%s\n' "$relays" | head -n 1)"
else
  t install.relay_unchecked "${relays:-$(tq install.targets_failed)}"
fi

say "$(tq install.step_engine "$VERSION")"
if [ -x "$RN" ] && [ "$("$RN" --version 2>/dev/null | awk '{print $2}')" = "${VERSION#v}" ]; then
  t install.engine_present "$("$RN" --version)"
else
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  fetch_engine "$tmp"
  mkdir -p "$BIN_DIR"; install -m 755 "$tmp/reason-network" "$RN"
  t install.engine_installed "$("$RN" --version)" "$RN"
fi
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) t install.path_note "$BIN_DIR" ;; esac

say "$(tq install.step_team)"
export REASON_NETWORK_MACHINE="$MACHINE"
if "$RN" team >/dev/null 2>&1; then
  have="$(current_person)"
  [ "$have" = "$NAME" ] || die "$(tq install.team_other "$have" "$NAME")"
  t install.team_present
else
  "$RN" stop >/dev/null 2>&1 || true
  "$RN" init "$NAME" --as "$NAME" --no-setup
fi
rn_engine config-off --path "$CFG" auto_update lan || die "$(tq install.cfg_failed "$CFG")"
"$RN" stop >/dev/null 2>&1 || true
"$RN" team
if [ ! -f "$ENV_FILE" ]; then
  sessions="$("$RN" who --all --json)" \
    || die "$(tq install.who_failed "$RELAY_NAME")"
  taken="$(relay_names conflicts "$RELAY_NAME" <<<"$sessions" 2>&1)" || die "$taken"
fi

say "$(tq install.step_wiring)"
"$RN" setup --dry-run
if [ "$YES" != 1 ]; then
  if [ -t 0 ]; then
    read -r -p "$(tq install.ask_apply)" a
    [[ "$a" == [yY]* ]] || die "$(tq install.apply_stopped)"
  else
    echo; t install.apply_no_tty
    exit 3
  fi
fi
"$RN" setup
t install.reopen_sessions

say "$(tq install.step_key)"
mkdir -p "$CONF_DIR" "$LIB_DIR" "$HOME/Library/Logs" "$HOME/Library/LaunchAgents"; chmod 700 "$CONF_DIR"
[ -s "$KEY_FILE" ] || ( umask 077; openssl rand -hex 32 > "$KEY_FILE" )
chmod 600 "$KEY_FILE"
seed_env
if [ "$GROK_SET" = 1 ]; then
  why="$(deploy_state set-grok-name --relay-name "$RELAY_NAME" -- "$GROK_NAME" 2>&1)" \
    || t install.grok_not_recorded "$why"
fi
webhook_step

say "$(tq install.step_cloud)"
cloud_prompt
