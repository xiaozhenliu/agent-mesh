#!/usr/bin/env bash
set -euo pipefail

VERSION="v0.12.17"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROJECT_VERSION="$(cat "$HERE/VERSION")"
BIN_DIR="$HOME/.local/bin"; RN="$BIN_DIR/reason-network"
KEY_FILE="$HOME/.config/agent-relay/outbound.key"
LIB_DIR="$HOME/.local/lib/agent-mesh"; RELAY="$LIB_DIR/agent_relay.py"
ROLLBACK="$LIB_DIR/rollback"
CFG="$HOME/.reason/network/config.toml"
BOT_KIND=""; BOT_NAME=""; CONF_DIR=""; ENV_FILE=""; LABEL=""; PLIST=""; STATE_DIR=""; MARKER=""; LOG=""; LAUNCHD_LOG=""
R_NAMES=(); R_KINDS=(); R_STAGES=(); R_CLOUDS=(); R_REMOVING=(); R_ADDRS=(); C_NAMES=(); C_KINDS=(); NEW_PLISTS=()
SEL_KIND=""; SEL_NAME=""; REPLACE_CODE=1; SLOT=""; ADDRESS=""; FORMER_VALUE=""; RENAME_SLOT=""; RENAME_TO=""
export REASON_NETWORK_NO_PROMPT=1
. "$HERE/scripts/msg.sh"
. "$HERE/scripts/lock.sh"

NAME=""; MACHINE="mac"; YES=0; MODE=full; DOC_LANG=""; RELAY_NAME_ARG=""; GROK_NAME=""; GROK_SET=0; ADD=0; KEEP_ROLLBACK=0
PYTHON_ARG=""; RELAY_PYTHON=""; RENAME=0; RENAME_TO_ARG=""; CANCEL=0; HISTORY=0
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
    --keep-rollback) KEEP_ROLLBACK=1; shift ;;
    --undo-relay) MODE=undo; shift ;;
    --add-bot) ADD=1; shift ;;
    --rename) RENAME=1; MODE=rename; shift
      if [ $# -gt 0 ] && [[ "$1" != -* ]]; then RENAME_TO_ARG="$1"; shift; fi ;;
    --cancel) CANCEL=1; shift ;;
    --accept-history) HISTORY=1; shift ;;
    --wake-bot) MODE=wake; shift ;;
    --preflight) MODE=preflight; shift ;;
    --fetch-engine) value $# "$1" "${2:-}"; MODE=fetch; FETCH_DIR="$2"; shift 2 ;;
    --repo-url) value $# "$1" "${2:-}"; REPO_URL="$2"; shift 2 ;;
    --python) value $# "$1" "${2:-}"; PYTHON_ARG="$2"; shift 2 ;;
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
if [ "$KEEP_ROLLBACK" = 1 ] && [ "$MODE" != restart ]; then t install.keep_rollback_alone >&2; exit 2; fi
if [ "$RENAME" = 1 ] && [ "$MODE" != rename ]; then t install.rename_alone >&2; exit 2; fi
if [ "$CANCEL" = 1 ] || [ "$HISTORY" = 1 ]; then
  [ "$MODE" = rename ] || { t install.rename_only >&2; exit 2; }
  [ -z "$RENAME_TO_ARG" ] && [ "$CANCEL$HISTORY" != 11 ] || { t install.rename_options >&2; exit 2; }
fi
if [ "$ADD" = 1 ]; then
  [ "$MODE" = full ] || { t install.add_alone >&2; exit 2; }
  [ "$YES" = 0 ] || { t install.add_no_yes >&2; exit 2; }
  MODE=add
fi
if [ -n "$PYTHON_ARG" ]; then
  case "$MODE" in
    webhook|restart) ;;
    full) [ -n "$NAME" ] || MODE=restart ;;
    *) t install.python_mode >&2; exit 2 ;;
  esac
fi
if [ "$GROK_SET" = 1 ]; then
  case "$MODE" in
    add) ;;
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
rn_engine() { "$PYTHON" "$HERE/box/rn-engine.py" "$@" 9>&-; }

use_bot() {   # $1 name (empty: the first Bot), $2 kind for a Bot not on this Mac yet, $3 --lenient for read-only modes
  local out k v
  BOT_NAME="$1"; SLOT=""; ADDRESS=""
  out="$("$PYTHON" "$HERE/scripts/bots.py" paths ${1:+"$1"} --kind "$2" "${@:3}" 2>&1)" || die "$out"
  while IFS=$'\t' read -r k v; do
    case "$k" in
      KIND) BOT_KIND="$v" ;; CONF_DIR) CONF_DIR="$v" ;; ENV_FILE) ENV_FILE="$v" ;;
      LABEL) LABEL="$v" ;; PLIST) PLIST="$v" ;; STATE_DIR) STATE_DIR="$v" ;; MARKER) MARKER="$v" ;; LOG) LOG="$v" ;;
      LAUNCHD_LOG) LAUNCHD_LOG="$v" ;; SLOT) SLOT="$v" ;; ADDRESS) ADDRESS="$v" ;;
    esac
  done <<< "$out"
  [ -n "$ENV_FILE" ] && [ -n "$LABEL" ] && [ -n "$STATE_DIR" ] || die "$out"
}

launch_agent() {   # $1 relay, $2 destination: the plist of the current Bot
  if [ "$BOT_KIND" = legacy ]; then
    "$PYTHON" "$HERE/scripts/write_launch_agent.py" "$1" "$2" --python "$RELAY_PYTHON"
  else
    "$PYTHON" "$HERE/scripts/write_launch_agent.py" "$1" "$2" --python "$RELAY_PYTHON" --label "$LABEL" \
      --env "$ENV_FILE" --state "$STATE_DIR" --log "$LOG" --launchd-log "$LAUNCHD_LOG"
  fi
}

relay_python() {
  [ -z "$RELAY_PYTHON" ] || return 0
  if [ -n "$PYTHON_ARG" ]; then RELAY_PYTHON="$PYTHON_ARG"; return 0; fi
  local out rc=0 k v stale=""
  out="$(deploy_state relay-python)" || rc=$?
  while read -r k v; do
    case "$k" in python) RELAY_PYTHON="$v" ;; stale) stale="$v" ;; esac
  done <<< "$out"
  if [ "$rc" != 0 ] || [ -z "$RELAY_PYTHON" ]; then
    [ -z "$stale" ] || t install.python_stale "$stale" >&2
    die "$(tq install.python_none)"
  fi
  [ -z "$stale" ] || t install.python_replaced "$stale" "$RELAY_PYTHON"
}

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
  die "$(tq install.rename_refused "$1" "$RELAY_NAME_ARG" "$RELAY_NAME_ARG")
$(tq install.add_hint)"
}
have_webhook() { relay_names webhook "$ENV_FILE"; }
relay_env() { relay_names env-values "$ENV_FILE" "$@"; }

env_text() {
  cat <<EOF
# agent-relay config (mode 600). Never paste these values into a chat.
RN_BIN=$RN
PERSON=$(current_person)
EOF
  [ -z "$1" ] || printf 'WEBHOOK_URL=%s\nWEBHOOK_KEY=%s\n' "$1" "$2"
  cat <<EOF
OUTBOUND_KEY_FILE=$KEY_FILE
# The Bot address name; change it only with agent-mesh rename, which rewrites these lines.
LISTEN_NAME=$ADDRESS
CLOUD_NAME=$ADDRESS-cloud
INBOX_NAME=$ADDRESS-inbox
EOF
  [ -z "$FORMER_VALUE" ] || printf 'FORMER_NAMES=%s\n' "$FORMER_VALUE"
  cat <<EOF
# Optional, shown with defaults:
# CLOUD_MACHINE=cloud
# MAX_WAKES_PER_HOUR=20  MIN_SECONDS_BETWEEN_WAKES=20  MAX_BATCH=10  MAX_PROXY_PER_HOUR=60  MAX_SPOOL=1000
# WEBHOOK_MAX_BODY_CHARS=3500  DOORBELL_PREVIEW_CHARS=200  FWD_COPY_DAYS=7  REFWD_MAX_PER_ID=2  REFWD_MAX_PER_HOUR=20
# GOT_TIMEOUT_SECONDS=900  GOT_DONE_SECONDS=7200  GOT_MAX_RERINGS=2  NONCE_KEEP_SECONDS=86400  FORMER_POLL_SECONDS=60
EOF
}

put_env() {
  local tmp
  FORMER_VALUE="$(deploy_state former-env --slot "${SLOT:--}")" || die "$(tq install.env_write_failed "$ENV_FILE")"
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
    NONCE_KEEP_SECONDS FORMER_POLL_SECONDS)" || die "$(tq install.env_read_failed "$ENV_FILE")"
  put_env "$1" "$2" "$kept"
}

bot_label() {
  deploy_state label --person "$(current_person)" --relay-name "$RELAY_NAME" 2>/dev/null \
    || echo "$(current_person)/$ADDRESS"
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

use_member() {
  if [ "$2" = legacy ]; then use_bot "" legacy; ADDRESS="${1:-$ADDRESS}"; else use_bot "$1" added; fi
  BOT_NAME="$1"; RELAY_NAME="$1"
  [ -n "$ADDRESS" ] || ADDRESS="$1"
  if [ -n "$RENAME_TO" ] && [ "$SLOT" = "$RENAME_SLOT" ]; then
    ADDRESS="$RENAME_TO"
    [ "$BOT_KIND" != legacy ] || RELAY_NAME="$RENAME_TO"
  fi
}

member_path() {
  if [ "$BOT_KIND" = legacy ]; then printf '%s' "$1"; else printf 'bots/%s/%s' "$BOT_NAME" "$1"; fi
}

save_copy() {
  [ -f "$2" ] || return 0
  cp -p "$2" "$1/$3" && cmp -s "$2" "$1/$3" && ( cd "$1" && shasum -a 256 "$3" >> SHA256SUMS )
}

backup_relay() {
  local tmp="$ROLLBACK.tmp" i rel
  rm -rf "$tmp"
  mkdir -p "$tmp" || return 1
  for ((i = 0; i < ${#C_NAMES[@]}; i++)); do
    use_member "${C_NAMES[$i]}" "${C_KINDS[$i]}"
    rel="$(member_path loaded)"
    mkdir -p "$(dirname "$tmp/$rel")" || return 1
    if relay_loaded; then echo 1 > "$tmp/$rel"; else echo 0 > "$tmp/$rel"; fi
    ( cd "$tmp" && shasum -a 256 "$rel" >> SHA256SUMS ) && save_copy "$tmp" "$PLIST" "$(member_path agent.plist)" \
      || return 1
  done
  save_copy "$tmp" "$RELAY" agent_relay.py && mv "$tmp" "$ROLLBACK"
}

restore_file() {
  if [ -f "$ROLLBACK/$1" ]; then
    cp -p "$ROLLBACK/$1" "$2.restore.$$" && mv -f "$2.restore.$$" "$2"
  else
    rm -f "$2"
  fi
}

rollback_members() {
  local d
  M_NAMES=(); M_KINDS=()
  if [ -f "$ROLLBACK/loaded" ]; then M_NAMES+=(""); M_KINDS+=(legacy); fi
  for d in "$ROLLBACK"/bots/*/; do
    [ -d "$d" ] || continue
    d="${d%/}"; M_NAMES+=("${d##*/}"); M_KINDS+=(added)
  done
}

rollback_live() {
  local out i n k s r found names=() kinds=()
  out="$("$PYTHON" "$HERE/scripts/bots.py" list 2>&1)" || { printf 'install: %s\n' "$out" >&2; return 1; }
  for ((i = 0; i < ${#M_NAMES[@]}; i++)); do
    found=0
    while IFS=$'\t' read -r n k s _ r _; do
      [ "$k" = "${M_KINDS[$i]}" ] && [ "$s" = relay ] && [ "$r" = 0 ] || continue
      [ "$k" = legacy ] || [ "$n" = "${M_NAMES[$i]}" ] || continue
      found=1
    done <<< "$out"
    if [ "$found" = 1 ]; then
      names+=("${M_NAMES[$i]}"); kinds+=("${M_KINDS[$i]}")
    else
      t install.rollback_skipped "${M_NAMES[$i]:-$(tq install.rollback_first_bot)}"
    fi
  done
  M_NAMES=(${names[@]+"${names[@]}"}); M_KINDS=(${kinds[@]+"${kinds[@]}"})
}

restore_relay() {
  local i failed=0
  ( cd "$ROLLBACK" && shasum -a 256 -c --status SHA256SUMS ) \
    || { t install.rollback_bad "$ROLLBACK"; return 1; }
  rollback_members
  rollback_live || return 1
  for ((i = 0; i < ${#M_NAMES[@]}; i++)); do
    use_member "${M_NAMES[$i]}" "${M_KINDS[$i]}"
    launchctl bootout "$(service)" 2>/dev/null || true
    if relay_loaded; then t install.unload_failed "$ROLLBACK"; return 1; fi
  done
  for ((i = 0; i < ${#M_NAMES[@]}; i++)); do
    use_member "${M_NAMES[$i]}" "${M_KINDS[$i]}"
    rm -f "$MARKER"
    restore_file "$(member_path agent.plist)" "$PLIST" || { t install.restore_files_failed "$ROLLBACK"; return 1; }
  done
  restore_file agent_relay.py "$RELAY" || { t install.restore_files_failed "$ROLLBACK"; return 1; }
  for ((i = 0; i < ${#M_NAMES[@]}; i++)); do
    use_member "${M_NAMES[$i]}" "${M_KINDS[$i]}"
    if [ "$(cat "$ROLLBACK/$(member_path loaded)")" = 1 ] && [ -f "$PLIST" ]; then
      bootstrap_and_wait || { t install.previous_not_up "$LOG"; failed=1; }
    fi
  done
  [ "$failed" = 0 ] || return 1
  rm -rf "$ROLLBACK"
}

relay_interrupted() {
  trap '' INT TERM HUP
  echo
  t install.interrupted
  restore_relay || t install.not_fully_restored
  exit "$1"
}

drop_new() { rm -f ${NEW_RELAY:+"$NEW_RELAY"} ${NEW_PLISTS[@]+"${NEW_PLISTS[@]}"}; }

start_relay() {
  local i ok=1
  relay_python
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
  NEW_RELAY=""; NEW_PLISTS=()
  [ -f "$RELAY" ] || REPLACE_CODE=1
  if [ "$REPLACE_CODE" = 1 ]; then
    { NEW_RELAY="$(mktemp "$LIB_DIR/.agent_relay.py.XXXXXX")" && install -m 644 "$HERE/agent_relay.py" "$NEW_RELAY"; } \
      || ok=0
  fi
  for ((i = 0; ok == 1 && i < ${#C_NAMES[@]}; i++)); do
    use_member "${C_NAMES[$i]}" "${C_KINDS[$i]}"
    { NEW_PLISTS[i]="$(mktemp "$HOME/Library/LaunchAgents/.$LABEL.XXXXXX")" && launch_agent "$RELAY" "${NEW_PLISTS[$i]}" \
      && chmod 644 "${NEW_PLISTS[$i]}" && plutil -lint "${NEW_PLISTS[$i]}" >/dev/null; } || ok=0
  done
  if [ "$ok" != 1 ]; then
    drop_new
    rm -rf "$ROLLBACK"
    t install.prepare_failed
    return 1
  fi
  trap 'drop_new; relay_interrupted 130' INT
  trap 'drop_new; relay_interrupted 143' TERM
  trap 'drop_new; relay_interrupted 129' HUP
  for ((i = 0; i < ${#C_NAMES[@]}; i++)); do
    use_member "${C_NAMES[$i]}" "${C_KINDS[$i]}"
    launchctl bootout "$(service)" 2>/dev/null || true
    rm -f "$MARKER"
  done
  [ -z "$NEW_RELAY" ] || mv -f "$NEW_RELAY" "$RELAY" || ok=0
  for ((i = 0; ok == 1 && i < ${#C_NAMES[@]}; i++)); do
    use_member "${C_NAMES[$i]}" "${C_KINDS[$i]}"
    { mv -f "${NEW_PLISTS[$i]}" "$PLIST" && bootstrap_and_wait && { [ "${1:-}" != verify ] || verify_registration; }; } \
      || ok=0
  done
  if [ "$ok" = 1 ]; then
    trap - INT TERM HUP
    [ "$KEEP_ROLLBACK" = 1 ] || rm -rf "$ROLLBACK"
    for ((i = 0; i < ${#C_NAMES[@]}; i++)); do
      use_member "${C_NAMES[$i]}" "${C_KINDS[$i]}"
      t install.relay_up "$LOG"
    done
    deploy_state relay-python --set "$RELAY_PYTHON" || t install.python_not_recorded "$LIB_DIR/deployed.json"
    [ -z "$PYTHON_ARG" ] || t install.python_set "$RELAY_PYTHON"
    return 0
  fi
  trap '' INT TERM HUP
  drop_new
  t install.last_log
  tail -n 10 "$LOG" "$LAUNCHD_LOG" 2>/dev/null || true
  t install.restoring
  if restore_relay; then t install.restored; else t install.not_fully_restored; fi
  trap - INT TERM HUP
  return 1
}

registration() {   # the new relay's own marker (removed before it started), then the engine's session row
  local rc=0 waited=0 want mark m_name m_pid _ pid
  want="$(current_person)/$ADDRESS"
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
        rc=0; REG_ACTUAL="$(relay_names registered "$ADDRESS" 2>&1)" || rc=$?
        if [ "$rc" = 0 ] && [ "$REG_ACTUAL" = "$ADDRESS" ]; then
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
  die "$(tq install.registered_other "$REG_ACTUAL" "$(current_person)/$ADDRESS" "$ADDRESS")"
}

verify_registration() {
  registration && return 0
  t install.new_registered_other "$REG_ACTUAL" "$(current_person)/$ADDRESS"
  return 1
}

webhook_cmd() {
  if [ "$MODE" = add ] || [ "$MODE" = wake ]; then
    printf '%s' "./install.sh --add-bot --relay-name $RELAY_NAME --lang $DOC_LANG"
  elif [ "${#R_NAMES[@]}" -gt 1 ] || [ "$SEL_KIND" = added ]; then
    printf '%s' "./install.sh --webhook --relay-name $RELAY_NAME --lang $DOC_LANG"
  else
    printf '%s' "./install.sh --webhook --lang $DOC_LANG"
  fi
}

webhook_step() {
  [ -n "$(current_person)" ] && [ -s "$KEY_FILE" ] \
    || die "$(tq install.need_team_key)"
  mkdir -p "$LIB_DIR" "$HOME/Library/Logs" "$HOME/Library/LaunchAgents"
  if [ "$BOT_KIND" = added ]; then mkdir -p "$CONF_DIR" && chmod 700 "$CONF_DIR"; fi
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
      t install.webhook_run_later "$(webhook_cmd)"
      [ "$MODE" != add ] || exit 3
      exit 0
    fi
    read -r -p "$(tq install.ask_url)" url
    read -r -s -p "$(tq install.ask_key)" key; echo
    [[ "$url" == https://* ]] || die "$(tq install.url_https)"
    [ -n "$key" ] || die "$(tq install.both_required)"
    write_env "$url" "$key"
  fi
  chmod 600 "$ENV_FILE"
  deploy_state entry --checkout "$HERE" --lang "$DOC_LANG" || t install.record_failed "$LIB_DIR/deployed.json"
  [ "${1:-}" != nostart ] || return 0
  local stage=relay
  if [ "$BOT_KIND" = added ]; then stage="$(bot_field stage)" || exit 1; fi
  if [ "$stage" != relay ]; then
    t install.add_continue "$RELAY_NAME" "./install.sh --add-bot --relay-name $RELAY_NAME --lang $DOC_LANG"
    return 0
  fi
  C_NAMES=("$RELAY_NAME"); C_KINDS=("$BOT_KIND")
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
  invite="$("$RN" invite --me 9>&- | sed -n 's/^For a cloud agent of yours.*send it: //p')"
  [ -n "$invite" ] || invite="$("$RN" invite --me 9>&-)"
  record_pending cloud
  record_pending cloud_setup
  record_pending cloud_engine
  t install.cloud_intro
  echo "----------------------------------------------------------------"
  echo "$invite"
  echo
  render_prompt cloud-setup --team-name "$person" --mac-home "$HOME" --repo-url "$repo" --relay-name "$ADDRESS" \
    --release-tag "$tag" --release-commit "$commit" --cloud-checksums "$sums"
  echo "----------------------------------------------------------------"
  t install.copy_key "$KEY_FILE"
  t install.bot_address_cloud "$(bot_label)" "$ADDRESS-cloud" "$ADDRESS-inbox"
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
  # Rotating $KEY_FILE only helps if that is the key every relay actually loads.
  local src i
  for ((i = 0; i < ${#C_NAMES[@]}; i++)); do
    use_member "${C_NAMES[$i]}" "${C_KINDS[$i]}"
    have_webhook || die "$(tq install.rotate_unconfigured)"
    src="$(relay_env OUTBOUND_KEY OUTBOUND_KEY_FILE)" || die "$(tq install.rotate_read_failed "$ENV_FILE")"
    if grep -Eq '^OUTBOUND_KEY=.' <<<"$src"; then
      die "$(tq install.rotate_inline "$ENV_FILE" "$KEY_FILE")"
    fi
    [ "$(printf '%s\n' "$src" | sed -n 's/^OUTBOUND_KEY_FILE=//p')" = "$KEY_FILE" ] \
      || die "$(tq install.rotate_other_file "$ENV_FILE" "$KEY_FILE")"
  done
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
  if [ "${#C_NAMES[@]}" -gt 1 ]; then
    t install.rotate_key_kept "$KEY_FILE"
    for ((i = 0; i < ${#C_NAMES[@]}; i++)); do
      use_member "${C_NAMES[$i]}" "${C_KINDS[$i]}"
      if relay_running; then t install.rotate_bot_new "$(bot_label)"; else t install.rotate_bot_down "$(bot_label)"; fi
    done
  fi
  echo "----------------------------------------------------------------"
  t install.rotate_copy "$KEY_FILE"
  t install.rotate_after
  t install.rotate_leak "./install.sh --webhook --lang $DOC_LANG"
  [ "$rc" = 0 ] || [ "${#C_NAMES[@]}" -gt 1 ] || die "$(tq install.rotate_restart_failed)"
  [ "$rc" = 0 ] || die "$(tq install.rotate_restart_failed_all)"
}

# Copies exist only so the cloud can ask for a lost message again (RN-REFWD). The relay deletes expired ones
# (FWD_COPY_DAYS) only when later mail passes through, so an idle relay keeps them. Safe while the relay runs: a
# missing copy is answered with no-copy. The loop catches a copy renamed from .tmp to .json while we delete.
purge_copies() {
  local state="$STATE_DIR"
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
if [ -n "$PYTHON_ARG" ]; then
  why="$(deploy_state relay-python --python "$PYTHON_ARG" 2>&1)" || die "--python: $why"
fi
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
load_roster() {
  local out n k s c r a
  R_NAMES=(); R_KINDS=(); R_STAGES=(); R_CLOUDS=(); R_REMOVING=(); R_ADDRS=()
  out="$("$PYTHON" "$HERE/scripts/bots.py" list 2>&1)" || die "$out"
  while IFS=$'\t' read -r n k s c r _ a; do
    [ -z "$n" ] || { R_NAMES+=("$n"); R_KINDS+=("$k"); R_STAGES+=("$s"); R_CLOUDS+=("$c"); R_REMOVING+=("$r")
      R_ADDRS+=("${a:-$n}"); }
  done <<< "$out"
}

roster_at() {
  local i
  for ((i = 0; i < ${#R_NAMES[@]}; i++)); do
    if [ "${R_NAMES[$i]}" = "$1" ]; then printf '%s' "$i"; return 0; fi
  done
  for ((i = 0; i < ${#R_ADDRS[@]}; i++)); do
    if [ "${R_ADDRS[$i]}" = "$1" ]; then printf '%s' "$i"; return 0; fi
  done
  return 1
}

roster_has_legacy() {
  local i
  for ((i = 0; i < ${#R_KINDS[@]}; i++)); do [ "${R_KINDS[$i]}" != legacy ] || return 0; done
  return 1
}

roster_stop() {
  local i note
  {
    echo "install: $1"
    for ((i = 0; i < ${#R_NAMES[@]}; i++)); do
      use_member "${R_NAMES[$i]}" "${R_KINDS[$i]}"
      note=""
      if [ "${R_REMOVING[$i]}" = 1 ]; then
        note=" $(tq install.roster_removing)"
      elif [ "${R_KINDS[$i]}" = added ] && { [ "${R_STAGES[$i]}" != relay ] || [ "${R_CLOUDS[$i]}" != confirmed ]; }; then
        note=" $(tq install.roster_pending)"
      fi
      t install.roster_line "$(bot_label)" "$note"
    done
  } >&2
  exit 1
}

pick_bot() {
  local i n="${#R_NAMES[@]}"
  SEL_KIND=legacy; SEL_NAME=""
  if [ -n "$RELAY_NAME_ARG" ]; then
    if i="$(roster_at "$RELAY_NAME_ARG")"; then
      SEL_KIND="${R_KINDS[$i]}"; SEL_NAME="${R_NAMES[$i]}"
    elif [ "$n" -gt 1 ] || [ "$MODE" = rename ]; then
      roster_stop "$(tq install.not_a_bot "$RELAY_NAME_ARG")"
    elif [ "$n" = 1 ] && ! roster_has_legacy; then
      [ "$MODE" != full ] || die "$(tq install.full_no_first)"
      rename_refused "${R_ADDRS[0]}"
    fi
  elif [ "$n" -gt 1 ]; then
    roster_stop "$(tq install.which_bot)"
  elif [ "$n" = 1 ]; then
    SEL_KIND="${R_KINDS[0]}"; SEL_NAME="${R_NAMES[0]}"
  fi
  if [ "$MODE" = full ] && [ "$n" -gt 0 ] && ! roster_has_legacy; then die "$(tq install.full_no_first)"; fi
  if [ "$MODE" = full ] && [ "$SEL_KIND" = added ]; then die "$(tq install.full_added "$SEL_NAME" "$SEL_NAME")"; fi
  if [ "$MODE" = wake ] && [ "$SEL_KIND" != added ]; then die "$(tq install.wake_legacy)"; fi
  if i="$(roster_at "$SEL_NAME")" && [ "${R_REMOVING[$i]}" = 1 ]; then
    die "$(tq install.bot_removing "$SEL_NAME" "$SEL_NAME")"
  fi
}

cohort_all() {
  local i
  C_NAMES=(); C_KINDS=()
  for ((i = 0; i < ${#R_NAMES[@]}; i++)); do
    if [ "${R_STAGES[$i]}" = relay ] && [ "${R_REMOVING[$i]}" != 1 ]; then
      C_NAMES+=("${R_NAMES[$i]}"); C_KINDS+=("${R_KINDS[$i]}")
    fi
  done
  if [ "${#C_NAMES[@]}" = 0 ]; then
    [ "${#R_NAMES[@]}" = 0 ] || die "$(tq install.no_relays)"
    C_NAMES=("$RELAY_NAME"); C_KINDS=(legacy)
  fi
}

bot_field() {
  local out k v
  out="$(deploy_state bot-get --relay-name "$RELAY_NAME" 2>&1)" || die "$out"
  while read -r k v; do
    if [ "$k" = "$1" ]; then printf '%s' "$v"; return 0; fi
  done <<< "$out"
  die "$out"
}

add_cmd() { printf '%s' "./install.sh --add-bot --relay-name $RELAY_NAME --lang $DOC_LANG"; }
wake_cmd() { printf '%s' "./install.sh --relay-name $RELAY_NAME --wake-bot --lang $DOC_LANG"; }

add_ready() {
  local st line w webhook
  st="$(deploy_state status --lang "$DOC_LANG" --python "$RELAY_PYTHON")" || die "$(tq install.add_status_failed)"
  while IFS= read -r line; do
    w=(); read -r -a w <<< "$line"
    case "${w[0]:-}" in
      cloud|cloud_setup|cloud_engine) [ "${w[1]:-}" = unchanged ] || die "$(tq install.add_update_first)" ;;
      cloud_release) [ "${w[1]:-}" != - ] || die "$(tq install.add_update_first)" ;;
      txn|etxn|request) [ "${w[1]:-}" = - ] || die "$(tq install.add_update_first)" ;;
      name) [ "${w[1]:-}" != changed ] || die "$(tq install.add_update_first)" ;;
      bots) die "$(tq install.add_status_failed)" ;;
      bot)
        [ "${#w[@]}" -ge 18 ] || die "$(tq install.add_status_failed)"
        [ "${w[1]}" != "$1" ] || continue
        [ "${w[15]}" = - ] || die "$(tq install.bot_removing "${w[1]}" "${w[1]}")"
        if [ "${w[3]}" = added ] && { [ "${w[5]}" != relay ] || [ "${w[13]}" != confirmed ]; }; then
          die "$(tq install.add_unfinished "${w[1]}" "./install.sh --add-bot --relay-name ${w[1]} --lang $DOC_LANG")"
        fi
        if [ "${w[7]}" != ok ]; then
          webhook="./install.sh --webhook --lang $DOC_LANG"
          [ "${#R_NAMES[@]}" = 1 ] && [ "${w[3]}" = legacy ] || webhook="./install.sh --webhook --relay-name ${w[1]} --lang $DOC_LANG"
          die "$(tq install.add_finish_first "${w[1]}" "$webhook")"
        fi
        [ "${w[9]}" = unchanged ] && [ "${w[11]}" = unchanged ] || die "$(tq install.add_update_first)" ;;
    esac
  done <<< "$st"
}

add_check() {
  local rc=0
  ADD_WHY="$(relay_names add-check "$person" "$1" <<<"$sessions" 2>&1)" || rc=$?
  [ "$rc" != 2 ] || die "$ADD_WHY"
  return "$rc"
}

add_name() {
  local rc=0 out k v
  ADD_CLASS=""; ADD_EVIDENCE=(); ADD_UNREAD=0; ADD_SENDERS=()
  out="$(relay_names name-check "$person" "$1" <<<"$sessions" 2>&1)" || rc=$?
  [ "$rc" != 2 ] || die "$out"
  if [ "$rc" != 0 ]; then ADD_WHY="$out"; return 1; fi
  while read -r k v; do
    case "$k" in
      class) ADD_CLASS="$v" ;; evidence) ADD_EVIDENCE+=("$v") ;; unread) ADD_UNREAD="$v" ;; sender) ADD_SENDERS+=("$v") ;;
    esac
  done <<< "$out"
}

name_facts() {
  local line
  t "install.name_class_$ADD_CLASS"
  for line in ${ADD_EVIDENCE[@]+"${ADD_EVIDENCE[@]}"}; do echo "  $line"; done
  if [ "$ADD_UNREAD" != 0 ]; then
    if [ "${2:-}" = add ] && [ "$ADD_CLASS" = left ]; then
      t install.name_unread_held "$ADD_UNREAD" "$1"
    else
      t install.name_unread "$ADD_UNREAD" "$1"
    fi
    for line in ${ADD_SENDERS[@]+"${ADD_SENDERS[@]}"}; do echo "  $line"; done
  fi
}

add_default() {
  local c n=1
  ADD_DEFAULT=""
  c="$(relay_names suggest "$GROK_NAME")" || c=""
  while [ "$n" -le 99 ]; do
    if [ -n "$c" ] && add_check "$c"; then ADD_DEFAULT="$c"; return 0; fi
    n=$((n + 1)); c="bot-$n"
  done
  die "$(tq install.add_no_default)"
}

add_mode() {
  local person sessions facts hash machine answer i why others="" reclaim=()
  [ "${#R_NAMES[@]}" != 0 ] || die "$(tq install.add_no_deployment)"
  [ -x "$RN" ] || die "$(tq install.no_engine)"
  person="$(current_person)"
  [ -n "$person" ] && [ -s "$KEY_FILE" ] || die "$(tq install.need_team_key)"
  [ -z "$NAME" ] || [ "$NAME" = "$person" ] || die "$(tq install.add_team_other "$NAME" "$person")"
  if [ -n "$RELAY_NAME_ARG" ] && i="$(roster_at "$RELAY_NAME_ARG")"; then
    [ "${R_REMOVING[$i]}" != 1 ] || die "$(tq install.bot_removing "$RELAY_NAME_ARG" "$RELAY_NAME_ARG")"
    if [ "${R_KINDS[$i]}" != added ] || [ "${R_CLOUDS[$i]}" = confirmed ]; then
      die "$(tq install.add_exists "$RELAY_NAME_ARG" "$RELAY_NAME_ARG")"
    fi
    add_ready "${R_NAMES[$i]}"
    use_member "${R_NAMES[$i]}" added
    CURRENT="$RELAY_NAME"; recorded=ok
    record_grok
    t install.add_resume "$person/$ADDRESS"
    add_continue
    return 0
  fi
  add_ready ""
  facts="$(rn_engine team-facts --home "$HOME/.reason/network" --bin "$RN" 2>&1)" \
    || die "$(tq install.add_team_failed "$facts")"
  read -r hash machine <<< "$facts"
  [ "${machine%%/*}" = "$person" ] || die "$(tq install.add_team_person "${machine%%/*}" "$person")"
  if [ "$GROK_SET" != 1 ]; then
    [ -t 0 ] || die "$(tq install.add_need_grok "./install.sh --add-bot --grok-name '<its name in Grok>' --lang $DOC_LANG")"
    for _ in 1 2 3; do
      answer=""; read -r -p "$(tq install.ask_grok)" answer || true
      if why="$(relay_names grok-check "$answer" 2>&1)"; then GROK_NAME="$answer"; GROK_SET=1; break; fi
      t install.try_again "$why"
    done
    [ "$GROK_SET" = 1 ] || die "$(tq install.add_gave_up)"
  fi
  sessions="$("$RN" who --all --json 9>&-)" || die "$(tq install.add_who_failed)"
  ADD_CLASS=new
  if [ -n "$RELAY_NAME_ARG" ]; then
    add_name "$RELAY_NAME_ARG" || die "--relay-name: $ADD_WHY"
    RELAY_NAME="$RELAY_NAME_ARG"
  else
    add_default
    RELAY_NAME=""
    if [ -t 0 ]; then
      for _ in 1 2 3; do
        answer=""; read -r -p "$(tq install.ask_address "$ADD_DEFAULT")" answer || true
        answer="${answer:-$ADD_DEFAULT}"
        if add_name "$answer"; then RELAY_NAME="$answer"; break; fi
        t install.try_again "$ADD_WHY"
      done
      [ -n "$RELAY_NAME" ] || die "$(tq install.add_gave_up)"
    else
      RELAY_NAME="$ADD_DEFAULT"
    fi
  fi
  for ((i = 0; i < ${#R_NAMES[@]}; i++)); do others="$others${others:+, }$person/${R_ADDRS[$i]}"; done
  echo
  t install.add_plan "$person/$RELAY_NAME" "$GROK_NAME" "$others"
  if [ "$ADD_CLASS" != new ]; then
    name_facts "$person/$RELAY_NAME" add
    reclaim=(--reclaim)
  fi
  if [ -t 0 ]; then
    answer=""; read -r -p "$(tq install.ask_add_apply)" answer || true
    [[ "$answer" == [yY]* ]] || die "$(tq install.add_stopped)"
  else
    t install.add_plan_no_tty "./install.sh --add-bot --grok-name '$GROK_NAME' --relay-name $RELAY_NAME --lang $DOC_LANG"
    exit 3
  fi
  why="$(deploy_state add-record --relay-name "$RELAY_NAME" --team-hash "$hash" --grok-name="$GROK_NAME" \
    ${reclaim[@]+"${reclaim[@]}"} 2>&1)" || die "$why"
  use_member "$RELAY_NAME" added
  add_continue
}

cloud_ready() {
  local listing
  listing="$("$RN" who --all --json 9>&-)" || return 2
  relay_names cloud-sessions "$(current_person)" "$ADDRESS" <<<"$listing"
}

wake_bot() { deploy_state wake-bot --relay-name "$RELAY_NAME" || die "$(tq install.add_wake_failed "$(wake_cmd)")"; }

poll_reply() {
  local limit="$1" waited=0
  shift
  while :; do
    rc=0; out="$("$@")" || rc=$?
    [ "$rc" = 3 ] || return 0
    if [ "$waited" -ge "$limit" ]; then return 1; fi
    sleep 5 || exit $?
    waited=$((waited + 5))
  done
}

wait_sessions() {
  local waited=0 limit="${AGENT_MESH_ADD_WAIT:-600}" rc
  t install.add_waiting "$(current_person)/$ADDRESS-cloud" "$(current_person)/$ADDRESS-inbox" "$((limit / 60))"
  while :; do
    rc=0; cloud_ready || rc=$?
    [ "$rc" != 0 ] || return 0
    [ "$waited" -lt "$limit" ] || return 1
    sleep 5; waited=$((waited + 5))
  done
}

start_added() {
  deploy_state bot-stage --relay-name "$RELAY_NAME" --stage relay || die "$(tq install.record_failed "$LIB_DIR/deployed.json")"
  chmod 600 "$ENV_FILE"
  C_NAMES=("$RELAY_NAME"); C_KINDS=(added)
  start_relay verify && return 0
  use_member "$RELAY_NAME" added
  launchctl bootout "$(service)" 2>/dev/null || true
  rm -f "$PLIST" "$MARKER"
  deploy_state bot-stage --relay-name "$RELAY_NAME" --stage configured \
    || t install.record_failed "$LIB_DIR/deployed.json"
  die "$(tq install.add_start_failed "$(wake_cmd)")"
}

confirm_add() {
  local rc out="" req limit="${AGENT_MESH_ADD_REPLY_WAIT:-600}" to
  to="$(current_person)/$ADDRESS"
  if [ "$(bot_field cloud)" != confirmed ]; then
    rc=3
    if [ "$(bot_field req)" != - ]; then
      rc=0; out="$(deploy_state add-reply --relay-name "$RELAY_NAME" --lang "$DOC_LANG")" || rc=$?
      if [ "$rc" = 1 ]; then printf '%s\n' "$out"; out=""; rc=3; fi
    fi
    if [ "$rc" = 3 ]; then
      req="$(deploy_state add-request --relay-name "$RELAY_NAME")" || die "$(tq install.record_failed "$LIB_DIR/deployed.json")"
      "$RN" send "$to" - < <(printf '%s' "agent-mesh add-bot $RELAY_NAME $req") >/dev/null 9>&- \
        || die "$(tq install.add_send_failed "$to" "$(wake_cmd)")"
      t install.add_sent "$to" "$((limit / 60))"
      if ! poll_reply "$limit" deploy_state add-reply --relay-name "$RELAY_NAME" --lang "$DOC_LANG"; then
        t install.add_reply_timeout "$to" "$(wake_cmd)"
        exit 3
      fi
    fi
    [ -z "$out" ] || printf '%s\n' "$out"
    case "$rc" in
      0) ;;
      1) die "$(tq install.add_reply_bad "$(wake_cmd)")" ;;
      *) die "$(tq install.add_reply_unread "$(wake_cmd)")" ;;
    esac
  fi
  t install.add_done "$to"
  grok_label
}

add_continue() {
  local stage rc
  mkdir -p "$CONF_DIR" && chmod 700 "$CONF_DIR"
  [ -f "$ENV_FILE" ] || put_env "" "" ""
  have_webhook || webhook_step nostart
  stage="$(bot_field stage)" || exit 1
  if [ "$stage" != relay ]; then
    rc=0; cloud_ready || rc=$?
    if [ "$rc" != 0 ]; then
      wake_bot
      if ! wait_sessions; then
        t install.add_wait_timeout "$(current_person)/$ADDRESS" "$(wake_cmd)"
        exit 3
      fi
    fi
    start_added
  elif ! relay_loaded; then
    chmod 600 "$ENV_FILE"
    C_NAMES=("$RELAY_NAME"); C_KINDS=(added)
    start_relay verify || die "$(tq install.add_start_failed "$(wake_cmd)")"
  fi
  confirm_add
}

wake_mode() {
  local stage rc
  [ -x "$RN" ] || die "$(tq install.no_engine)"
  [ -n "$(current_person)" ] && [ -s "$KEY_FILE" ] || die "$(tq install.need_team_key)"
  have_webhook || die "$(tq install.add_finish_first "$RELAY_NAME" "$(add_cmd)")"
  wake_bot
  stage="$(bot_field stage)" || exit 1
  if [ "$stage" != relay ]; then
    rc=0; cloud_ready || rc=$?
    if [ "$rc" != 0 ]; then
      t install.wake_not_yet "$(current_person)/$ADDRESS" "$(wake_cmd)"
      exit 3
    fi
    start_added
  elif [ "$(bot_field cloud)" = confirmed ]; then
    return 0
  elif ! relay_loaded; then
    chmod 600 "$ENV_FILE"
    C_NAMES=("$RELAY_NAME"); C_KINDS=(added)
    start_relay verify || die "$(tq install.add_start_failed "$(wake_cmd)")"
  fi
  confirm_add
}

rename_guard() {
  local out rc=0
  out="$(deploy_state rename-get 2>&1)" || rc=$?
  case "$rc" in
    3) ;;
    0) die "$(tq install.rename_busy)" ;;
    *) die "$out" ;;
  esac
}

rename_get() {
  local out rc=0 k v
  out="$(deploy_state rename-get 2>&1)" || rc=$?
  [ "$rc" = 0 ] || [ "$rc" = 3 ] || die "$out"
  [ "$rc" = 0 ] || return 3
  while read -r k v; do
    case "$k" in
      stage) R_STAGE="$v" ;; slot) R_SLOT="$v" ;; old) R_OLD="$v" ;; new) R_NEW="$v" ;; req) R_REQ="$v" ;;
      reclaim) R_RECLAIM="$v" ;;
    esac
  done <<< "$out"
}

rename_cmd() {
  local cmd="agent-mesh rename $1"
  [ "${#R_NAMES[@]}" -le 1 ] || cmd="$cmd --relay-name $2"
  printf '%s' "$cmd"
}

pair() {
  local key="$1"
  shift
  while [ "$#" -ge 2 ]; do
    if [ "$1" = "$key" ]; then printf '%s\n' "$2"; return 0; fi
    shift 2
  done
  return 1
}

rename_ready() {
  local st line w addr
  st="$(deploy_state status --lang "$DOC_LANG" --python "$RELAY_PYTHON")" || die "$(tq install.rename_status_failed)"
  while IFS= read -r line; do
    w=(); read -r -a w <<< "$line"
    case "${w[0]:-}" in
      cloud|cloud_setup|cloud_engine) [ "${w[1]:-}" = unchanged ] || die "$(tq install.rename_update_first)" ;;
      cloud_release) [ "${w[1]:-}" != - ] || die "$(tq install.rename_update_first)" ;;
      txn|etxn|request) [ "${w[1]:-}" = - ] || die "$(tq install.rename_update_first)" ;;
      engine) [ "${w[1]:-}" != older ] || die "$(tq install.rename_update_first)" ;;
      name) [ "${w[1]:-}" != changed ] || die "$(tq install.rename_update_first)" ;;
      bots) die "$(tq install.rename_status_failed)" ;;
      bot)
        [ "${#w[@]}" -ge 18 ] || die "$(tq install.rename_status_failed)"
        addr="$(pair address "${w[@]}")" || die "$(tq install.rename_status_failed)"
        [ "${w[15]}" = - ] || die "$(tq install.bot_removing "$addr" "$addr")"
        if [ "${w[3]}" = added ] && { [ "${w[5]}" != relay ] || [ "${w[13]}" != confirmed ]; }; then
          die "$(tq install.rename_unfinished "$addr" "./install.sh --add-bot --relay-name ${w[1]} --lang $DOC_LANG")"
        fi
        [ "${w[1]}" = "$1" ] || continue
        [ "${w[7]}" = ok ] || die "$(tq install.rename_webhook_first "$addr")"
        [ "${w[9]}" = unchanged ] || die "$(tq install.rename_update_first)" ;;
    esac
  done <<< "$st"
}

rename_new() {
  local i person sessions out rc=0 pid answer reclaim=() why k v
  [ "${#R_NAMES[@]}" != 0 ] || die "$(tq install.rename_nothing)"
  [ -x "$RN" ] || die "$(tq install.no_engine)"
  person="$(current_person)"
  [ -n "$person" ] && [ -s "$KEY_FILE" ] || die "$(tq install.need_team_key)"
  why="$(relay_names check "$RENAME_TO_ARG" 2>&1)" || die "$why"
  pick_bot
  i="$(roster_at "$SEL_NAME")" || i=0
  use_member "${R_NAMES[$i]}" "${R_KINDS[$i]}"
  [ "$RENAME_TO_ARG" != "$ADDRESS" ] || die "$(tq install.rename_same "$person/$ADDRESS")"
  rename_ready "${R_NAMES[$i]}"
  relay_running || die "$(tq install.rename_relay_down "$(bot_label)" "./install.sh --restart-relay --lang $DOC_LANG")"
  pid="$(relay_pid)"
  sessions="$("$RN" who --all --json 9>&-)" || die "$(tq install.add_who_failed)"
  out="$(relay_names name-check "$person" "$RENAME_TO_ARG" --slot "$SLOT" ${pid:+--relay-pid "$pid"} <<<"$sessions" 2>&1)" \
    || rc=$?
  case "$rc" in
    0) ;;
    1) die "$(tq install.rename_name_refused "$person/$RENAME_TO_ARG")
$out" ;;
    *) die "$out" ;;
  esac
  ADD_CLASS=""; ADD_EVIDENCE=(); ADD_UNREAD=0; ADD_SENDERS=()
  while read -r k v; do
    case "$k" in
      class) ADD_CLASS="$v" ;; evidence) ADD_EVIDENCE+=("$v") ;; unread) ADD_UNREAD="$v" ;; sender) ADD_SENDERS+=("$v") ;;
    esac
  done <<< "$out"
  echo
  t install.rename_plan "$person/$ADDRESS" "$person/$RENAME_TO_ARG"
  name_facts "$person/$RENAME_TO_ARG"
  if [ "$ADD_CLASS" = new ] || [ "$ADD_CLASS" = own ]; then
    if [ "$YES" != 1 ] || [ "$ADD_UNREAD" != 0 ]; then
      if [ -t 0 ]; then
        answer=""; read -r -p "$(tq install.ask_rename)" answer || true
        [[ "$answer" == [yY]* ]] || die "$(tq install.rename_stopped)"
      elif [ "$ADD_UNREAD" = 0 ]; then
        t install.rename_no_tty "$(rename_cmd "$RENAME_TO_ARG" "$ADDRESS") --yes"
        exit 3
      else
        t install.rename_need_tty "$(rename_cmd "$RENAME_TO_ARG" "$ADDRESS")"
        exit 3
      fi
    fi
  else
    if [ ! -t 0 ]; then
      t install.rename_need_tty "$(rename_cmd "$RENAME_TO_ARG" "$ADDRESS")"
      exit 3
    fi
    answer=""; read -r -p "$(tq install.ask_rename)" answer || true
    [[ "$answer" == [yY]* ]] || die "$(tq install.rename_stopped)"
    reclaim=(--reclaim)
  fi
  out="$(deploy_state rename-start --slot "$SLOT" --old "$ADDRESS" --new "$RENAME_TO_ARG" ${reclaim[@]+"${reclaim[@]}"} 2>&1)" \
    || die "$out"
}

rename_member() {
  RENAME_SLOT="$R_SLOT"
  if [ "$R_SLOT" = - ]; then use_member "${RENAME_TO:-$R_OLD}" legacy; else use_member "$R_SLOT" added; fi
}

rename_reply() { deploy_state rename-reply 2>&1; }

rename_request() {
  local rc=0 out limit="${AGENT_MESH_RENAME_REPLY_WAIT:-600}" to body
  to="$(current_person)/$R_OLD"
  out="$(rename_reply)" || rc=$?
  if [ "$rc" = 3 ]; then
    body="agent-mesh rename $R_SLOT $R_OLD $R_NEW $R_REQ"
    [ "$R_RECLAIM" != 1 ] || body="$body"$'\n'"reclaim=1"
    "$RN" send "$to" - < <(printf '%s' "$body") >/dev/null 9>&- || die "$(tq install.rename_send_failed "$to")"
    t install.rename_sent "$to" "$((limit / 60))"
    if ! poll_reply "$limit" rename_reply; then
      t install.rename_reply_timeout "$to"
      exit 3
    fi
  fi
  [ -z "$out" ] || printf '%s\n' "$out"
  case "$rc" in
    0) ;;
    1) die "$(tq install.rename_cloud_refused)" ;;
    *) die "$(tq install.rename_reply_unread)" ;;
  esac
}

rename_env() {
  local cfg url key
  cfg="$(relay_env WEBHOOK_URL WEBHOOK_KEY)" || die "$(tq install.env_read_failed "$ENV_FILE")"
  url="$(printf '%s\n' "$cfg" | sed -n 's/^WEBHOOK_URL=//p')"
  key="$(printf '%s\n' "$cfg" | sed -n 's/^WEBHOOK_KEY=//p')"
  [ -n "$url" ] && [ -n "$key" ] || die "$(tq install.rename_webhook_first "$(current_person)/$R_OLD")"
  write_env "$url" "$key"
  chmod 600 "$ENV_FILE"
}

rename_stop_relay() {
  launchctl bootout "$(service)" 2>/dev/null || true
  if relay_loaded; then die "$(tq install.rename_unload_failed "$(service)")"; fi
  rm -f "$MARKER"
}

rename_forward() {
  local out name
  if [ "$R_STAGE" = requested ]; then rename_request; R_STAGE=replied; fi
  if [ "$R_STAGE" = replied ]; then
    out="$(deploy_state rename-switch 2>&1)" || die "$out"
    R_STAGE=switching
  fi
  RENAME_TO="$R_NEW"
  rename_member
  if [ "$R_STAGE" = switching ]; then
    rename_stop_relay
    rename_env
    deploy_state rename-launched || die "$(tq install.record_failed "$LIB_DIR/deployed.json")"
    R_STAGE=launched
  fi
  name="$(relay_names env "$ENV_FILE" 2>&1)" || die "$(tq install.env_names "$name")"
  [ "$name" = "$R_NEW" ] || rename_env
  C_NAMES=("$RELAY_NAME"); C_KINDS=("$BOT_KIND"); REPLACE_CODE=0
  if ! { relay_running && AGENT_MESH_REGISTER_WAIT=0 registration; }; then
    start_relay verify || die "$(tq install.rename_start_failed "$(current_person)/$R_NEW")"
  fi
  deploy_state rename-commit || die "$(tq install.record_failed "$LIB_DIR/deployed.json")"
  "$PYTHON" "$HERE/scripts/reply_convention.py" install \
    || die "$(tq install.rename_reply_failed "$LIB_DIR/templates")"
  out="$(deploy_state rename-finish 2>&1)" || die "$out"
  t install.rename_done "$(current_person)/$R_OLD" "$(current_person)/$R_NEW"
  [ -z "$out" ] || printf '%s\n' "$out"
  t install.refresh_copies
  echo "  python3 scripts/reply_convention.py refresh <repository>/AGENTS.md"
}

rename_cancel() {
  local out
  case "$R_STAGE" in
    launched) die "$(tq install.rename_cancel_launched "$(current_person)/$R_NEW")" ;;
    switching)
      RENAME_TO="$R_OLD"
      rename_member
      rename_stop_relay
      out="$(deploy_state rename-restore 2>&1)" || die "$out"
      rename_member
      C_NAMES=("$RELAY_NAME"); C_KINDS=("$BOT_KIND"); REPLACE_CODE=0
      start_relay verify || die "$(tq install.rename_cancel_failed)"
      reply_templates ;;
  esac
  deploy_state rename-clear || die "$(tq install.record_failed "$LIB_DIR/deployed.json")"
  t install.rename_cancelled "$(current_person)/$R_OLD" "$(current_person)/$R_NEW"
}

history_mode() {
  local i person out rc=0 k c n req to waited=0 limit="${AGENT_MESH_RENAME_REPLY_WAIT:-600}" answer total=0
  local claims=() left=() rest
  [ "${#R_NAMES[@]}" != 0 ] || die "$(tq install.rename_nothing)"
  [ -x "$RN" ] || die "$(tq install.no_engine)"
  person="$(current_person)"
  [ -n "$person" ] && [ -s "$KEY_FILE" ] || die "$(tq install.need_team_key)"
  pick_bot
  i="$(roster_at "$SEL_NAME")" || i=0
  use_member "${R_NAMES[$i]}" "${R_KINDS[$i]}"
  out="$(deploy_state held --slot "$SLOT" 2>&1)" || rc=$?
  if [ "$rc" = 3 ]; then t install.history_none "$(bot_label)"; return 0; fi
  [ "$rc" = 0 ] || die "$out"
  while read -r k c n; do
    [ "$k" = claim ] || continue
    claims+=("$c"); total=$((total + n))
  done <<< "$out"
  to="$person/$ADDRESS"
  echo
  t install.history_plan "$total" "$(bot_label)"
  if [ "$YES" != 1 ]; then
    if [ -t 0 ]; then
      answer=""; read -r -p "$(tq install.ask_history)" answer || true
      [[ "$answer" == [yY]* ]] || die "$(tq install.history_stopped)"
    else
      t install.rename_no_tty "agent-mesh rename --accept-history$([ "${#R_NAMES[@]}" -le 1 ] || printf ' --relay-name %s' "$ADDRESS") --yes"
      exit 3
    fi
  fi
  for c in "${claims[@]}"; do
    rc=0; out="$(deploy_state history-reply --slot "$SLOT" --claim "$c" 2>&1)" || rc=$?
    if [ "$rc" = 3 ]; then
      req="$(deploy_state history-start --slot "$SLOT" --claim "$c" 2>&1)" || die "$req"
      "$RN" send "$to" - < <(printf '%s' "agent-mesh history $SLOT accept $c $req") >/dev/null 9>&- \
        || die "$(tq install.rename_send_failed "$to")"
      left+=("$c")
      continue
    fi
    history_result "$rc" "$out"
  done
  [ "${#left[@]}" = 0 ] || t install.rename_sent "$to" "$((limit / 60))"
  while [ "${#left[@]}" != 0 ]; do
    rest=()
    for c in "${left[@]}"; do
      rc=0; out="$(deploy_state history-reply --slot "$SLOT" --claim "$c" 2>&1)" || rc=$?
      if [ "$rc" = 3 ]; then rest+=("$c"); else history_result "$rc" "$out"; fi
    done
    left=(${rest[@]+"${rest[@]}"})
    [ "${#left[@]}" != 0 ] || break
    if [ "$waited" -ge "$limit" ]; then
      t install.history_timeout "$to"
      exit 3
    fi
    sleep 5; waited=$((waited + 5))
  done
}

history_result() {
  [ -z "$2" ] || printf '%s\n' "$2"
  case "$1" in
    0) ;;
    1) die "$(tq install.history_refused)" ;;
    *) die "$(tq install.rename_reply_unread)" ;;
  esac
}

rename_mode() {
  local rc=0
  R_STAGE=""; R_SLOT=""; R_OLD=""; R_NEW=""; R_REQ=""; R_RECLAIM=0
  rename_get || rc=$?
  if [ "$HISTORY" = 1 ]; then
    [ "$rc" = 3 ] || die "$(tq install.rename_busy)"
    history_mode
    return 0
  fi
  if [ "$CANCEL" = 1 ]; then
    if [ "$rc" = 3 ]; then t install.rename_none; return 0; fi
    rename_cancel
    return 0
  fi
  if [ "$rc" = 0 ]; then
    [ -z "$RENAME_TO_ARG" ] || [ "$RENAME_TO_ARG" = "$R_NEW" ] \
      || die "$(tq install.rename_other "$(current_person)/$R_OLD" "$(current_person)/$R_NEW")"
    t install.rename_resume "$(current_person)/$R_OLD" "$(current_person)/$R_NEW"
  else
    [ -n "$RENAME_TO_ARG" ] || die "$(tq install.rename_need_name)"
    rename_new
    rename_get || die "$(tq install.record_failed "$LIB_DIR/deployed.json")"
  fi
  rename_forward
}

CURRENT=""; RELAY_NAME=""; recorded=""
if [ "$MODE" = preflight ]; then
  use_bot "" legacy --lenient
  SEL_KIND=legacy
else
  mesh_lock_take "$PYTHON" || exit 1
  load_roster
  [ "$MODE" = rename ] || rename_guard
  case "$MODE" in
    add) SEL_KIND=add ;;
    rename) SEL_KIND=rename ;;
    restart|rotate|undo)
      [ -z "$RELAY_NAME_ARG" ] || die "$(tq install.all_bots_no_name)"
      SEL_KIND=cohort
      if [ "${#R_NAMES[@]}" = 0 ] || roster_has_legacy; then SEL_KIND=legacy; fi ;;
    *) pick_bot ;;
  esac
  case "$MODE" in
    restart|rotate|undo) ;;
    *) if [ "${#R_NAMES[@]}" -gt 1 ] || [ "$MODE" = add ]; then REPLACE_CODE=0; fi ;;
  esac
  [ "$SEL_KIND" != legacy ] || use_bot "" legacy
fi
if [ -n "$RELAY_NAME_ARG" ]; then
  why="$(relay_names check "$RELAY_NAME_ARG" 2>&1)" || die "--relay-name: $why"
fi
if [ "$GROK_SET" = 1 ]; then
  why="$(relay_names grok-check "$GROK_NAME" 2>&1)" || die "--grok-name: $why"
fi
if [ "$SEL_KIND" = legacy ]; then
  CURRENT="$(relay_names env "$ENV_FILE" 2>&1)" \
    || die "$(tq install.env_names "$CURRENT")"
  [ -z "$RELAY_NAME_ARG" ] || [ -z "$CURRENT" ] || [ "$RELAY_NAME_ARG" = "$CURRENT" ] || rename_refused "$CURRENT"
  RECORDED_NAME="$(deploy_state recorded-name 2>/dev/null)" || RECORDED_NAME=""
  RELAY_NAME="${CURRENT:-${RELAY_NAME_ARG:-${RECORDED_NAME:-bot}}}"
  ADDRESS="$RELAY_NAME"
  recorded="$(deploy_state check-name --relay-name "$RELAY_NAME" 2>&1)" || die "$recorded"
  case "$recorded" in
    "changed "*)
      recorded="${recorded#changed }"; recorded="${recorded%% *}"
      [ -n "$CURRENT" ] || die "$(tq install.recorded_other "$recorded" "$LIB_DIR/deployed.json" "$RELAY_NAME" "$recorded")"
      die "$(tq install.env_other "$recorded" "$ENV_FILE" "$CURRENT" "$recorded")" ;;
  esac
elif [ "$SEL_KIND" = added ]; then
  use_member "$SEL_NAME" added
  CURRENT="$SEL_NAME"; recorded=ok
fi

# Never replace a user's different engine version or create a second PATH installation silently.
existing="$(command -v reason-network || true)"
[ -z "$existing" ] || [ "$existing" = "$RN" ] || die "$(tq install.other_engine "$existing" "$RN")"
ENGINE_STATE=absent
if [ -x "$RN" ]; then
  installed_version="$("$RN" --version 2>/dev/null 9>&- | awk '{print $2}')" || installed_version=""
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

case "$MODE" in
  full|webhook)
    [ -z "$PYTHON_ARG" ] || [ "${#R_NAMES[@]}" -le 1 ] || die "$(tq install.python_alone)"
    relay_python ;;
  add|wake|rotate|restart|rename) relay_python ;;
  cloud) [ "$SEL_KIND" != added ] || relay_python ;;
esac

preflight_bots() {
  local out n k s _c r i multi=0 names=() kinds=()
  if out="$("$PYTHON" "$HERE/scripts/bots.py" list 2>&1)"; then
    while IFS=$'\t' read -r n k s _c r _; do
      [ -n "$n" ] || continue
      [ "$k" = legacy ] || multi=1
      if [ "$s" = relay ] && [ "$r" != 1 ]; then names+=("$n"); kinds+=("$k"); fi
    done <<< "$out"
  fi
  if [ "$multi" = 0 ]; then
    have_webhook || die "$(tq install.not_configured)"
    [ -f "$PLIST" ] || die "$(tq install.no_plist)"
    return 0
  fi
  for ((i = 0; i < ${#names[@]}; i++)); do
    use_member "${names[$i]}" "${kinds[$i]}"
    have_webhook || die "$(tq install.bot_not_configured "$(bot_label)" "./install.sh --webhook --relay-name ${names[$i]}")"
    [ -f "$PLIST" ] || die "$(tq install.bot_no_plist "$(bot_label)" "./install.sh --webhook --relay-name ${names[$i]}")"
  done
}

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
    if [ "$SEL_KIND" = added ]; then wake_mode; exit 0; fi
    cloud_prompt; exit 0 ;;
  wake) wake_mode; exit 0 ;;
  add) add_mode; exit 0 ;;
  rename) rename_mode; exit 0 ;;
  rotate) [ -x "$RN" ] || die "$(tq install.no_engine)"; cohort_all; rotate_key; exit 0 ;;
  purge) purge_copies; exit 0 ;;
  preflight)
    [ -x "$RN" ] || die "$(tq install.no_engine)"
    [ -n "$(current_person)" ] && [ -s "$KEY_FILE" ] || die "$(tq install.need_team_key)"
    preflight_bots
    exit 0 ;;
  undo)
    if [ -d "$ROLLBACK" ]; then restore_relay || die "$(tq install.not_fully_restored)"; fi
    exit 0 ;;
  restart)
    [ -x "$RN" ] || die "$(tq install.no_engine)"
    [ -n "$(current_person)" ] && [ -s "$KEY_FILE" ] || die "$(tq install.need_team_key)"
    cohort_all
    for ((i = 0; i < ${#C_NAMES[@]}; i++)); do
      use_member "${C_NAMES[$i]}" "${C_KINDS[$i]}"
      have_webhook || die "$(tq install.not_configured)"
      chmod 600 "$ENV_FILE"
    done
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
if [ -x "$RN" ] && [ "$("$RN" --version 2>/dev/null 9>&- | awk '{print $2}')" = "${VERSION#v}" ]; then
  t install.engine_present "$("$RN" --version 9>&-)"
else
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  fetch_engine "$tmp"
  mkdir -p "$BIN_DIR"; install -m 755 "$tmp/reason-network" "$RN"
  t install.engine_installed "$("$RN" --version 9>&-)" "$RN"
fi
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) t install.path_note "$BIN_DIR" ;; esac

say "$(tq install.step_team)"
export REASON_NETWORK_MACHINE="$MACHINE"
if "$RN" team >/dev/null 2>&1 9>&-; then
  have="$(current_person)"
  [ "$have" = "$NAME" ] || die "$(tq install.team_other "$have" "$NAME")"
  t install.team_present
else
  "$RN" stop >/dev/null 2>&1 9>&- || true
  "$RN" init "$NAME" --as "$NAME" --no-setup 9>&-
fi
rn_engine config-off --path "$CFG" auto_update lan || die "$(tq install.cfg_failed "$CFG")"
"$RN" stop >/dev/null 2>&1 9>&- || true
"$RN" team 9>&-
if [ ! -f "$ENV_FILE" ]; then
  sessions="$("$RN" who --all --json 9>&-)" \
    || die "$(tq install.who_failed "$RELAY_NAME")"
  taken="$(relay_names conflicts "$RELAY_NAME" <<<"$sessions" 2>&1)" || die "$taken"
fi

say "$(tq install.step_wiring)"
"$RN" setup --dry-run 9>&-
if [ "$YES" != 1 ]; then
  if [ -t 0 ]; then
    read -r -p "$(tq install.ask_apply)" a
    [[ "$a" == [yY]* ]] || die "$(tq install.apply_stopped)"
  else
    echo; t install.apply_no_tty
    exit 3
  fi
fi
"$RN" setup 9>&-
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
if [ -n "$PYTHON_ARG" ] && [ ! -f "$PLIST" ]; then
  deploy_state relay-python --set "$PYTHON_ARG" || t install.python_not_recorded "$LIB_DIR/deployed.json"
fi
webhook_step

say "$(tq install.step_cloud)"
cloud_prompt
