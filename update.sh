#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/scripts/msg.sh"

die() { printf 'update: %s\n' "$(tq "$@")" >&2; exit 1; }
fail() { printf 'update: %s\n' "$*" >&2; exit 1; }
ask() { local a=""; read -r -p "$(tq "$@") [y/N] " a || true; [[ "$a" == [yY]* ]]; }
ask_go() { local a=""; read -r -p "$(tq update.continue) [Y/n] " a || return 1; [[ -z "$a" || "$a" == [yY]* ]]; }
state() { "$PYTHON" "$HERE/scripts/deploy_state.py" "$@"; }
installer() { "$BASH" "$HERE/install.sh" "$@" --lang "$DOC_LANG"; }
service() { echo "gui/$(id -u)/$LABEL"; }
relay_loaded() { launchctl print "$(service)" >/dev/null 2>&1; }
engine() { "$PYTHON" "$HERE/box/rn-engine.py" "$@"; }
relay_pid() { launchctl print "$(service)" 2>/dev/null | sed -n 's/^[[:space:]]*pid = \([0-9][0-9]*\)$/\1/p' | head -n 1; }
word() { tq "st.$1"; }

recorded_lang() {
  python3 - "$LIB_DIR/deployed.json" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as stream:
        lang = json.load(stream).get("lang")
except Exception:
    lang = None
print(lang if lang in ("en", "zh-CN") else "")
PY
}

pick_lang() {
  local prev="" a
  for a in "$@"; do
    [ "$prev" != --lang ] || LANG_ARG="$a"
    prev="$a"
  done
  case "$LANG_ARG" in
    ""|en|zh-CN) ;;
    *) printf "update: unsupported language '%s'; choose en or zh-CN\nupdate：不支持的语言 '%s'，请选择 en 或 zh-CN\n" \
         "$LANG_ARG" "$LANG_ARG" >&2; exit 2 ;;
  esac
  DOC_LANG="$LANG_ARG"
  if [ -z "$DOC_LANG" ] && command -v python3 >/dev/null; then DOC_LANG="$(recorded_lang)" || DOC_LANG=""; fi
  [ -n "$DOC_LANG" ] || DOC_LANG="${AGENT_MESH_LANG:-en}"
  case "$DOC_LANG" in
    en|zh-CN) ;;
    *) printf "update: unsupported AGENT_MESH_LANG '%s'; use --lang en or --lang zh-CN\nupdate：不支持的 AGENT_MESH_LANG '%s'，请用 --lang en 或 --lang zh-CN\n" \
         "$DOC_LANG" "$DOC_LANG" >&2; exit 1 ;;
  esac
  export AGENT_MESH_LANG="$DOC_LANG"
}

usage_error() { printf 'update: %s\n' "$(tq "$@")" >&2; exit 2; }

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --lang) [ $# -ge 2 ] || usage_error update.lang_needs; shift 2 ;;
      --yes|-y) YES=1; shift ;;
      --no-pull) PULL=0; shift ;;
      --plan) PLAN=1; shift ;;
      --manual) MANUAL=1; shift ;;
      --repo-url) [ $# -ge 2 ] && [ -n "$2" ] || usage_error update.url_needs; REPO_URL="$2"; shift 2 ;;
      -h|--help) t update.help; exit 0 ;;
      *) usage_error update.unknown_option "$1" ;;
    esac
  done
}

take_lock() {
  mkdir -p "$LIB_DIR"
  if ! mkdir "$LOCK" 2>/dev/null; then
    local pid cmd
    pid="$(cat "$LOCK/pid" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      cmd="$(ps -p "$pid" -o command= 2>/dev/null)" || cmd="update.sh"
      case "$cmd" in *update.sh*) die update.locked "$pid" ;; esac
    fi
    t update.stale_lock
    rm -rf "$LOCK"
    mkdir "$LOCK" || die update.no_lock "$LOCK"
  fi
  echo $$ > "$LOCK/pid"
  LOCKED=1
}

release_lock() {
  if [ "$LOCKED" = 1 ]; then rm -rf "$LOCK"; LOCKED=0; fi
}

summary() {
  echo
  t update.summary
  local label value
  while read -r label value; do
    "$PYTHON" "$HERE/scripts/messages.py" row 15 "$(tq "label.$label")" "$value"
  done <<EOF
relay $R_RELAY
engine $R_ENGINE
dsh $R_DSH
cloud $R_CLOUD
setup $R_SETUP
cengine $R_CENGINE
prompt $R_PROMPT
EOF
}

engine_recover() {
  local was="$ENGINE_WAS"
  if [ -f "$ETXN" ]; then
    was="$(engine txn-get --txn "$ETXN" relay_was_loaded)" || was=1
    if ! engine rollback --txn "$ETXN"; then
      t update.engine_not_undone "$ETXN"
      return 1
    fi
  fi
  if [ "$was" = 1 ] && ! relay_loaded; then
    installer --restart-relay || t update.relay_not_back
  fi
  rm -f "$ETXN"
  ENGINE_TXN=0
}

on_exit() {
  local rc=$?
  trap '' INT TERM HUP
  [ -z "$ENGINE_TMP" ] || rm -rf "$ENGINE_TMP"
  if [ "$ENGINE_TXN" = 1 ]; then
    echo
    t update.engine_rolling_back
    engine_recover || true
  fi
  if [ "$TXN_ACTIVE" = 1 ]; then
    local stage was
    stage="$(state txn-get stage 2>/dev/null || true)"
    was="$(state txn-get relay_was_loaded 2>/dev/null || true)"
    case "$stage" in
      routine_paused) t update.exit_paused_cloud ;;
      cloud_done|prompt_done) t update.exit_paused ;;
    esac
    if [ "$was" = 1 ] && ! relay_loaded; then
      if launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null; then
        t update.relay_running_again
      else
        t update.relay_not_back
      fi
    fi
    t update.stopped_at "$stage"
  fi
  [ "$ACTING" = 0 ] || summary
  release_lock
  exit "$rc"
}

pull_checkout() {
  git -C "$HERE" rev-parse --is-inside-work-tree >/dev/null 2>&1 || die update.not_git
  git -C "$HERE" rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1 || die update.no_upstream
  [ -z "$(git -C "$HERE" status --porcelain --untracked-files=no)" ] || die update.dirty_checkout
  local old new
  old="$(git -C "$HERE" rev-parse HEAD)"
  git -C "$HERE" pull --ff-only || die update.pull_failed
  new="$(git -C "$HERE" rev-parse HEAD)"
  if [ "$old" = "$new" ]; then t update.at_latest; return 1; fi
  t update.pulled "${old:0:12}" "${new:0:12}"
}

checkout_label() { git -C "$HERE" rev-parse --short HEAD 2>/dev/null || tq update.no_checkout; }

cloud_needed() { [ "$S_CLOUD" != unchanged ] || [ "$S_SETUP" != unchanged ] || [ "$S_CENGINE" != unchanged ]; }
cloud_pending() { [ "$S_CLOUD" = pending ] || [ "$S_SETUP" = pending ] || [ "$S_CENGINE" = pending ]; }
stale() { [ "$1" != unchanged ] && [ "$1" != pending ]; }
cloud_stale() { stale "$S_CLOUD" || stale "$S_SETUP" || stale "$S_CENGINE"; }

release_check() {
  local out k a b
  if ! out="$(state release --repo-url "$REPO_URL" 2>&1)"; then
    REL_STATE="failed"; REL_NOTE="${out#update: }"
    return 1
  fi
  REL_SUMS=""
  while read -r k a b; do
    case "$k" in
      tag) REL_TAG="$a" ;; commit) REL_COMMIT="$a" ;; sum) REL_SUMS="$REL_SUMS$a  $b"$'\n' ;;
    esac
  done <<< "$out"
  REL_STATE="ok"; REL_NOTE="$(tq update.release_ok "$REL_TAG" "${REL_COMMIT:0:12}")"
}

read_status() {
  local out k v
  out="$(state status --lang "$DOC_LANG" --python "$PYTHON")" || die update.no_status
  while read -r k v; do
    case "$k" in
      relay) S_RELAY="$v" ;; dsh) S_DSH="$v" ;; cloud) S_CLOUD="$v" ;;
      cloud_setup) S_SETUP="$v" ;; prompt) S_PROMPT="$v" ;; txn) S_TXN="$v" ;; name) S_NAME="$v" ;;
      engine) read -r S_ENGINE S_EVER S_EPIN <<< "$v" ;; etxn) S_ETXN="$v" ;; cloud_engine) S_CENGINE="$v" ;;
      cloud_release) S_CREL="$v" ;;
    esac
  done <<< "$out"
  [ "$S_ENGINE" != refused ] || die update.engine_refused "$S_EVER" "$S_EPIN"
  case "$S_NAME" in
    "changed "*)
      local recorded current
      read -r _ recorded current <<< "$S_NAME"
      die update.renamed "$current" "$recorded" ;;
  esac
}

plan_line() {
  local what="$3"
  [ "$2" != unchanged ] || what="-"
  "$PYTHON" "$HERE/scripts/messages.py" row 15 "$(tq "label.$1")" 10 "$(word "$2")" "$what"
}

print_plan() {
  t update.plan_head "$(checkout_label)" "$DOC_LANG"
  [ "$S_TXN" = - ] || t update.plan_txn "$S_TXN"
  local name_state name_value
  read -r name_state name_value <<< "$S_NAME"
  if [ "$name_state" = ok ]; then plan_line name ok "$name_value"
  else plan_line name unknown "$(tq update.plan_name_unknown "$name_value")"; fi
  case "$REL_STATE" in
    ok) plan_line release ok "$REL_NOTE" ;;
    failed) plan_line release failed "$(tq update.plan_release_failed "$REL_NOTE")" ;;
  esac
  plan_line relay "$S_RELAY" "$(tq update.plan_relay)"
  [ "$S_ETXN" = - ] || t update.plan_etxn "$S_ETXN"
  case "$S_ENGINE" in
    older) plan_line engine changed "$(tq update.plan_engine_older "$S_EVER" "$S_EPIN")" ;;
    stale) plan_line engine changed "$(tq update.plan_engine_stale)" ;;
    *) plan_line engine unchanged "-" ;;
  esac
  if [ "$S_DSH" = absent ]; then plan_line dsh absent "$(tq update.plan_dsh_absent)"
  else plan_line dsh "$S_DSH" "$(tq update.plan_dsh)"; fi
  plan_line cloud "$S_CLOUD" ""
  plan_line setup "$S_SETUP" ""
  plan_line cengine "$S_CENGINE" ""
  if cloud_pending; then
    t update.plan_cloud_pending
  elif cloud_needed; then
    if [ "$MANUAL" = 1 ]; then t update.plan_cloud_manual
    elif [ "$S_CREL" = - ]; then t update.plan_cloud_first "$S_EPIN"
    else t update.plan_cloud_auto "$S_EPIN"; fi
  fi
  case "$S_PROMPT" in
    pending) plan_line prompt pending "$(tq update.plan_prompt_pending)" ;;
    *) plan_line prompt "$S_PROMPT" "$(tq update.plan_prompt)" ;;
  esac
  [ "$S_ENGINE" = unchanged ] || t update.plan_engine_order
  if [ "$MANUAL" = 1 ] && { cloud_needed || [ "$S_PROMPT" != unchanged ]; }; then t update.plan_manual_order; fi
}

read_reply() {
  local line
  REPLY_TEXT=""
  while IFS= read -r line; do
    [ "$line" != "." ] || break
    REPLY_TEXT="$REPLY_TEXT$line"$'\n'
  done
}

cloud_block() {
  echo "bash -s <<'AGENT_MESH_UPDATE'"
  echo "set -u"
  echo 'stop() { echo "STOP: $*"; exit 1; }'
  echo "export GIT_TERMINAL_PROMPT=0"
  echo "A=/workspace/agent-mesh; TAG=$REL_TAG; COMMIT=$REL_COMMIT; REQ=$1"
  cat <<'BLOCK'
echo "agent-mesh-result request $REQ"
cd "$A" || stop "no clone at $A; nothing was changed"
st="$(git status --porcelain)" || stop "$A is not a git clone; nothing was changed"
[ -z "$st" ] || stop "$A has local changes; nothing was changed"
cur="$(tr -d '[:space:]' < VERSION)" || stop "cannot read $A/VERSION; nothing was changed"
[[ "$cur" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || stop "$A/VERSION is not a version; nothing was changed"
[ "$(printf '%s\n%s\n' "$cur" "${TAG#v}" | sort -t. -k1,1n -k2,2n -k3,3n | tail -n 1)" = "${TAG#v}" ] \
  || stop "this clone is at $cur, newer than $TAG; it is never downgraded; nothing was changed"
echo "origin: $(git remote get-url origin)"
git fetch -q --no-tags origin "refs/tags/$TAG:refs/tags/$TAG" </dev/null \
  || stop "could not fetch $TAG, or a different tag $TAG is already here; nothing was changed"
got="$(git rev-parse -q --verify "refs/tags/$TAG^{commit}")" || stop "$TAG is not a commit; nothing was changed"
[ "$got" = "$COMMIT" ] || stop "$TAG points to $got, not $COMMIT; nothing was changed"
n=0
while read -r sum file; do
  [ -n "$file" ] || continue
  have="$(git cat-file blob "$COMMIT:$file" | sha256sum | cut -d' ' -f1)"
  [ "$have" = "$sum" ] || stop "$file in $TAG does not match; nothing was changed"
  n=$((n + 1))
done <<'AGENT_MESH_SUMS'
BLOCK
  printf '%s' "$REL_SUMS"
  cat <<'BLOCK'
AGENT_MESH_SUMS
[ "$n" = 4 ] || stop "the checksum list is incomplete; nothing was changed"
old="$(git rev-parse HEAD)" || stop "cannot read HEAD; nothing was changed"
restore() {
  if git checkout -q -f --detach "$old" && [ "$(git rev-parse HEAD)" = "$old" ] && [ -z "$(git status --porcelain)" ]; then
    echo "restored the clone to $old"
  else
    echo "could not restore the clone to $old; do not run any script in $A"
  fi
}
git checkout -q --detach "$COMMIT" || { restore; stop "checking out $TAG failed"; }
sha256sum --strict --quiet -c - <<'AGENT_MESH_SUMS' || { restore; stop "the files after checking out $TAG do not match it"; }
BLOCK
  printf '%s' "$REL_SUMS"
  cat <<'BLOCK'
AGENT_MESH_SUMS
[ -f box/rn-cloud-update.sh ] || { restore; stop "$TAG has no box/rn-cloud-update.sh"; }
exec bash "$A/box/rn-cloud-update.sh" --after-checkout "$TAG" "$COMMIT" "$REQ"
AGENT_MESH_UPDATE
BLOCK
}

new_req() { "$PYTHON" -c 'import secrets; print(secrets.token_hex(8))'; }

cloud_step() {
  local req
  req="$(new_req)" && [ -n "$req" ] || die update.no_request
  SHOWN="$(state digest cloud)"
  SHOWN_SETUP="$(state digest cloud_setup)"
  SHOWN_ENGINE="$(state digest cloud_engine)"
  echo
  t update.manual_send_this
  echo "----------------------------------------------------------------"
  echo "On [BOT-CLOUD], run this block exactly as written, as one bash command, and reply with its full output. It"
  echo "updates /workspace/agent-mesh to the release $REL_TAG after checking it. If its last line is not the DONE line,"
  echo "stop and send me the output; do not retry, reset, clean, stash or repair anything."
  echo
  cloud_block "$req"
  echo "----------------------------------------------------------------"
  t update.manual_reply_lines "$REL_COMMIT" "$REL_TAG"
  printf '%s' "$REL_SUMS" | sed 's/^/    /'
  t update.manual_reply_engine "$S_EPIN"
  echo
  tq update.paste_reply >&2
  echo >&2
  read_reply
  if ! printf '%s' "$REPLY_TEXT" | state cloud-reply --commit "$REL_COMMIT" --tag "$REL_TAG" --engine; then
    die update.manual_reply_rejected
  fi
}

prompt_text() {
  echo
  t update.prompt_replace "$DOC_LANG"
  echo "----------------------------------------------------------------"
  state show-prompt --lang "$DOC_LANG"
  echo
  echo "----------------------------------------------------------------"
}

prompt_step() {
  SHOWN="$(state digest prompt --lang "$DOC_LANG")"
  prompt_text
  ask update.ask_prompt_saved_now || die update.prompt_not_recorded
}

guided() {
  local stage paused=0 was
  NEED=""
  cloud_needed && NEED="cloud"
  [ "$S_PROMPT" = unchanged ] || NEED="${NEED:+$NEED,}prompt"
  if [ -n "$NEED" ] && cloud_needed && [ "$REL_STATE" != ok ]; then
    release_check || fail "$REL_NOTE"
  fi
  if [ "$S_TXN" = - ]; then
    if relay_loaded; then was=1; else was=0; fi
    TXN_ACTIVE=1
    state txn-start --lang "$DOC_LANG" --need "$NEED" --was "$was" --commit "$(git -C "$HERE" rev-parse HEAD 2>/dev/null || echo none)"
    stage=stopping
  else
    TXN_ACTIVE=1
    stage="$S_TXN"
    t update.continuing "$stage"
    state txn-set need "$NEED"
  fi

  t update.g_stop_relay
  if relay_loaded; then launchctl bootout "$(service)" 2>/dev/null || true; fi
  ! relay_loaded || die update.relay_no_stop
  if [ "$stage" = stopping ]; then state txn-set stage relay_stopped; stage=relay_stopped; fi
  case "$stage" in routine_paused|cloud_done|prompt_done) paused=1 ;; esac

  if [ -n "$NEED" ]; then
    if [ "$paused" = 1 ]; then
      ask update.g_still_paused || die update.stopped
    else
      ask update.g_pause || die update.stopped
    fi
    state txn-set stage routine_paused
    if cloud_needed; then
      t update.g_cloud
      cloud_step
      state mark cloud_engine confirmed --expect "$SHOWN_ENGINE"
      state mark cloud_setup confirmed --expect "$SHOWN_SETUP"
      state mark cloud confirmed --expect "$SHOWN"
      state txn-set stage cloud_done
      S_CLOUD=unchanged; S_SETUP=unchanged; S_CENGINE=unchanged
      R_CLOUD="$(tq r.updated)"; R_SETUP="$(tq r.confirmed)"; R_CENGINE="$(tq r.engine_confirmed "$S_EPIN")"
    fi
    if [ "$S_PROMPT" != unchanged ]; then
      t update.g_prompt
      prompt_step
      state mark prompt confirmed --lang "$DOC_LANG" --expect "$SHOWN"
      state txn-set stage prompt_done
      S_PROMPT=unchanged; R_PROMPT="$(tq r.updated)"
    fi
    ask update.g_resume || die update.stopped
    state txn-set stage routine_resumed
  elif [ "$paused" = 1 ]; then
    ask update.g_resume || die update.stopped
    state txn-set stage routine_resumed
  fi

  t update.g_start_relay
  if ! installer --restart-relay; then
    R_RELAY="$(tq r.failed)"
    die update.relay_no_start
  fi
  if [ "$S_RELAY" = unchanged ]; then R_RELAY="$(tq r.restarted)"; else R_RELAY="$(tq r.updated)"; fi
  S_RELAY=unchanged
  state txn-clear
  TXN_ACTIVE=0
}

engine_step() {
  local tmp rc=0 was backup base n others pid
  echo
  t update.e_switch_relay "$S_EVER"
  if ! installer --restart-relay; then
    R_RELAY="$(tq r.failed)"
    die update.e_relay_no_switch
  fi
  R_RELAY="$(tq r.updated)"
  S_RELAY=unchanged
  pid="$(relay_pid)"
  others="$(engine others --bin "$RN" ${pid:+--skip-parent "$pid"})" || others=""
  if [ -n "$others" ]; then
    t update.e_others
    printf '%s\n' "$others" | sed 's/^/  /'
  fi
  if [ "$S_ENGINE" = stale ]; then
    t update.e_restart "$S_EVER"
    if relay_loaded; then ENGINE_WAS=1; else ENGINE_WAS=0; fi
    ENGINE_TXN=1
    launchctl bootout "$(service)" 2>/dev/null || true
    if ! engine restart --home "$RN_HOME" --bin "$RN"; then
      ENGINE_TXN=0
      installer --restart-relay || true
      R_ENGINE="$(tq r.restart_failed)"
      die update.e_restart_failed
    fi
    installer --restart-relay || { ENGINE_TXN=0; R_ENGINE="$(tq r.daemon_restarted)"; die update.e_relay_after_restart; }
    ENGINE_TXN=0
    R_ENGINE="$(tq r.daemon_restarted_on "$S_EVER")"
    return 0
  fi
  t update.e_upgrade "$S_EVER" "$S_EPIN"
  t update.e_upgrade_explain "$S_EPIN"
  tmp="$(mktemp -d "$LIB_DIR/.engine-new.XXXXXX")" || die update.e_no_tmp "$LIB_DIR"
  ENGINE_TMP="$tmp"
  if ! installer --fetch-engine "$tmp" >/dev/null; then
    R_ENGINE="$(tq r.download_failed)"
    die update.e_download_failed
  fi
  if relay_loaded; then was=1; else was=0; fi
  ENGINE_WAS="$was"
  ENGINE_TXN=1
  launchctl bootout "$(service)" 2>/dev/null || true
  if relay_loaded; then
    ENGINE_TXN=0
    die update.e_relay_no_stop
  fi
  backup="$LIB_DIR/engine-backup-$(date -u +%Y%m%dT%H%M%SZ)"
  base="$backup"; n=1
  while [ -e "$backup" ]; do n=$((n + 1)); backup="$base-$n"; done
  engine switch --home "$RN_HOME" --bin "$RN" --new "$tmp/reason-network" --pinned "$S_EPIN" \
    --backup "$backup" --txn "$ETXN" --keep "relay_was_loaded=$was" || rc=$?
  rm -rf "$tmp"; ENGINE_TMP=""
  if [ "$rc" != 0 ]; then
    R_ENGINE="$(tq r.failed)"
    engine_recover || { ENGINE_TXN=0; die update.e_not_undone; }
    R_ENGINE="$(tq r.rolled_back "$S_EVER" "$backup")"
    die update.e_failed_back "$S_EVER"
  fi
  t update.e_start_relay "$S_EPIN"
  if ! installer --restart-relay; then
    R_ENGINE="$(tq r.failed)"
    engine_recover || { ENGINE_TXN=0; die update.e_relay_not_undone; }
    R_ENGINE="$(tq r.relay_rolled_back "$S_EPIN" "$S_EVER" "$backup")"
    die update.e_relay_failed_back "$S_EVER"
  fi
  rm -f "$ETXN"
  ENGINE_TXN=0
  state set-engine "$S_EPIN" || t update.e_not_recorded "$LIB_DIR/deployed.json"
  R_ENGINE="$(tq r.upgraded "$S_EVER" "$S_EPIN" "$backup")"
}

update_dsh() {
  if "$BASH" "$HERE/dsh/mac-install.sh" --plugin-only; then
    R_DSH="$(tq r.dsh_updated)"
  else
    R_DSH="$(tq r.dsh_failed)"
    FAILED=1
  fi
}

pending_step() {
  if cloud_pending; then
    ask update.ask_setup_done "$S_EPIN" || die update.setup_first
    [ "$S_CLOUD" != pending ] || state mark cloud confirmed --if-pending
    [ "$S_SETUP" != pending ] || state mark cloud_setup confirmed --if-pending
    [ "$S_CENGINE" != pending ] || state mark cloud_engine confirmed --if-pending
    read_status
    cloud_needed || { R_CLOUD="$(tq r.confirmed)"; R_SETUP="$(tq r.confirmed)"; R_CENGINE="$(tq r.confirmed)"; }
  fi
  if [ "$S_PROMPT" = pending ]; then
    if ask update.ask_prompt_saved "$DOC_LANG"; then
      state mark prompt confirmed --lang "$DOC_LANG" --if-pending; S_PROMPT=unchanged; R_PROMPT="$(tq r.confirmed)"
    else
      S_PROMPT=changed
    fi
  fi
}

cloud_recorded() {
  S_CLOUD=unchanged; S_SETUP=unchanged; S_CENGINE=unchanged
  R_CLOUD="$(tq r.cloud_updated "$REL_TAG")"; R_SETUP="$(tq r.confirmed)"; R_CENGINE="$(tq r.engine_confirmed "$S_EPIN")"
}

request_check() {
  local rc=0 out
  out="$(state request-check --tag "$REL_TAG" --commit "$REL_COMMIT" 2>&1)" || rc=$?
  LAST_CHECK="$out"
  case "$rc" in
    0) printf '%s\n' "$out"; cloud_recorded ;;
    1) printf '%s\n' "$out"
       R_CLOUD="$(tq r.cloud_stopped)"
       die update.cloud_stopped ;;
    3) [ -z "$out" ] || printf '%s\n' "$out" ;;
  esac
  return "$rc"
}

bootstrap_message() {
  echo "This is my update request from my Mac (sent by ./update.sh). On [BOT-CLOUD], run this block exactly as"
  echo "written, as one bash command, and reply to this message with its full output. It updates"
  echo "/workspace/agent-mesh to the release $REL_TAG after checking it. If its last line is not the DONE line, stop"
  echo "and reply with the output; do not retry, reset, clean, stash or repair anything."
  echo
  cloud_block "$1"
}

cloud_auto() {
  local mode=line addr req i=0 rounds rc
  [ "$S_CREL" != - ] || mode=block
  [ "$REL_STATE" = ok ] || release_check || fail "$REL_NOTE"
  addr="$(state bot-address)" || die update.no_address
  echo
  t update.cloud_heading "$REL_TAG"
  rc=0; request_check || rc=$?
  [ "$rc" != 0 ] || { t update.cloud_earlier_reply; return 0; }
  relay_loaded || die update.relay_down
  req="$(state request-new --tag "$REL_TAG" --commit "$REL_COMMIT" --mode "$mode")" || die update.no_request
  if [ "$mode" = line ]; then
    "$RN" send "$addr" - < <(printf '%s' "agent-mesh update $REL_TAG $REL_COMMIT $req") >/dev/null || die update.send_failed "$addr"
    t update.sent "$addr"
  else
    "$RN" send "$addr" - < <(bootstrap_message "$req") >/dev/null || die update.send_failed "$addr"
    t update.sent_first "$addr"
  fi
  t update.waiting
  rounds=$(( ${AGENT_MESH_REPLY_WAIT:-900} / 5 ))
  while :; do
    rc=0; request_check || rc=$?
    [ "$rc" != 0 ] || return 0
    if [ "$i" -ge "$rounds" ]; then
      [ "$rc" != 4 ] || printf '%s\n' "$LAST_CHECK"
      R_CLOUD="$(tq r.cloud_waiting)"
      if [ "$mode" = block ]; then t update.timeout_first; else t update.timeout; fi
      FAILED=1
      return 0
    fi
    i=$((i + 1))
    [ $((i % 12)) != 0 ] || t update.still_waiting "$((i * 5 / 60))"
    sleep 5
  done
}

main() {
  LIB_DIR="$HOME/.local/lib/agent-mesh"; LOCK="$LIB_DIR/update.lock"
  LABEL="com.local.agent-relay"; PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  RN="$HOME/.local/bin/reason-network"; RN_HOME="$HOME/.reason/network"; ETXN="$LIB_DIR/engine-upgrade.json"
  LANG_ARG=""; YES=0; PULL=1; PLAN=0; MANUAL=0; REPO_URL="https://github.com/xiaozhenliu/agent-mesh.git"
  REL_STATE=""; REL_NOTE=""; REL_TAG=""; REL_COMMIT=""; REL_SUMS=""; LAST_CHECK=""
  FAILED=0; NEED=""; SHOWN=""; SHOWN_SETUP=""; SHOWN_ENGINE=""; S_NAME=""; REPLY_TEXT=""; S_CREL=-
  S_ENGINE=unchanged; S_EVER=""; S_EPIN=""; S_ETXN=-; S_CENGINE=unchanged
  pick_lang "$@"
  parse_args "$@"
  R_RELAY="$(tq r.unchanged)"; R_DSH="$R_RELAY"; R_CLOUD="$R_RELAY"; R_SETUP="$R_RELAY"; R_PROMPT="$R_RELAY"
  R_ENGINE="$R_RELAY"; R_CENGINE="$R_RELAY"

  installer --preflight || die update.preflight_failed
  PYTHON="$(python3 -c 'import sys; print(sys.executable)')"
  take_lock

  if [ "$PULL" = 1 ] && pull_checkout; then
    release_lock
    trap - EXIT INT TERM
    exec "$BASH" "$HERE/update.sh" --no-pull "$@"
  fi

  read_status
  local terminal=0 confirm=0
  if [ "$S_TXN" != - ] || [ "$S_PROMPT" != unchanged ] || cloud_pending || { [ "$MANUAL" = 1 ] && cloud_needed; }; then
    terminal=1
  fi
  if cloud_needed || [ "$S_ENGINE" != unchanged ] || [ "$S_ETXN" != - ]; then
    confirm=1
  fi
  if cloud_stale; then
    release_check || [ "$PLAN" = 1 ] || fail "$REL_NOTE"
  fi
  print_plan
  [ "$PLAN" = 0 ] || exit 0
  if { [ "$terminal" = 1 ] || { [ "$confirm" = 1 ] && [ "$YES" != 1 ]; }; } && [ ! -t 0 ]; then
    t update.needs_terminal "$DOC_LANG"
    exit 3
  fi
  "$PYTHON" "$HERE/scripts/reply_convention.py" install || t update.convention_failed "$LIB_DIR/templates"
  if [ "$terminal" = 0 ] && [ "$confirm" = 0 ] && [ "$S_RELAY" = unchanged ] && [ "$S_DSH" != changed ]; then
    t update.up_to_date
    exit 0
  fi
  pending_step
  if [ "$S_TXN" = - ] && [ "$S_ETXN" = - ] && [ "$S_ENGINE" = unchanged ] && [ "$S_PROMPT" = unchanged ] \
    && [ "$S_RELAY" = unchanged ] && [ "$S_DSH" != changed ] && ! cloud_needed; then
    summary
    exit 0
  fi
  if [ "$YES" != 1 ] && [ -t 0 ]; then
    if [ "$S_PROMPT" != unchanged ] && [ "$MANUAL" = 0 ]; then t update.will_paste; fi
    ask_go || die update.stopped_nothing
  fi
  ACTING=1

  local changed=0
  if [ "$S_ETXN" != - ]; then
    t update.etxn_first "$S_ETXN"
    ENGINE_TXN=1
    engine_recover || die update.etxn_failed
    changed=1
    read_status
  fi
  if [ "$S_TXN" != - ]; then
    guided
    changed=1
    read_status
  fi
  if [ "$S_ENGINE" != unchanged ]; then
    engine_step
    changed=1
  fi
  if [ "$MANUAL" = 1 ] && { cloud_needed || [ "$S_PROMPT" != unchanged ]; }; then
    guided
    changed=1
  elif [ "$S_RELAY" = changed ]; then
    if installer --restart-relay; then R_RELAY="$(tq r.updated)"; else R_RELAY="$(tq r.failed)"; FAILED=1; fi
    changed=1
  fi
  if [ "$S_DSH" = changed ]; then update_dsh; changed=1; fi
  if [ "$S_PROMPT" != unchanged ]; then
    prompt_step
    state mark prompt confirmed --lang "$DOC_LANG" --expect "$SHOWN"
    S_PROMPT=unchanged; R_PROMPT="$(tq r.updated)"
    changed=1
  fi
  if cloud_needed; then
    cloud_auto
    changed=1
  fi

  if [ "$changed" = 1 ]; then
    echo
    t update.check
    "$BASH" "$HERE/check.sh" --lang "$DOC_LANG" || { t update.check_failed; FAILED=1; }
  fi
  [ "$FAILED" = 0 ] || exit 1
}

LOCKED=0; TXN_ACTIVE=0; ACTING=0; ENGINE_TXN=0; ENGINE_WAS=1; ENGINE_TMP=""
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
main "$@"
