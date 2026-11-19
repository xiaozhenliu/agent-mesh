#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/scripts/msg.sh"
. "$HERE/scripts/lock.sh"

die() { printf 'update: %s\n' "$(tq "$@")" >&2; exit 1; }
fail() { printf 'update: %s\n' "$*" >&2; exit 1; }
ask() { local a=""; read -r -p "$(tq "$@") [y/N] " a || true; [[ "$a" == [yY]* ]]; }
ask_go() { local a=""; read -r -p "$(tq update.continue) [Y/n] " a || return 1; [[ -z "$a" || "$a" == [yY]* ]]; }
state() { "$PYTHON" "$HERE/scripts/deploy_state.py" --prompt-mode "$PROMPT_MODE" "$@"; }
state_full() { "$PYTHON" "$HERE/scripts/deploy_state.py" --prompt-mode full "$@"; }
installer() { "$BASH" "$HERE/install.sh" "$@" --lang "$DOC_LANG"; }
service() { echo "gui/$(id -u)/$LABEL"; }
relay_loaded() { launchctl print "$(service)" >/dev/null 2>&1; }
engine() { "$PYTHON" "$HERE/box/rn-engine.py" "$@" 9>&-; }
label_loaded() { launchctl print "gui/$(id -u)/$1" >/dev/null 2>&1; }
label_pid() { launchctl print "gui/$(id -u)/$1" 2>/dev/null | sed -n 's/^[[:space:]]*pid = \([0-9][0-9]*\)$/\1/p' | head -n 1; }
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
      --full-prompt) PROMPT_MODE=full; shift ;;
      --repo-url) [ $# -ge 2 ] && [ -n "$2" ] || usage_error update.url_needs; REPO_URL="$2"; shift 2 ;;
      -h|--help) t update.help; exit 0 ;;
      *) usage_error update.unknown_option "$1" ;;
    esac
  done
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
  [ "$CHECK_FAILED" = 1 ] || return 0
  if [ "$FAILED" = 0 ]; then
    t update.steps_done
    t update.check_failed_only
  else
    t update.check_failed
  fi
}

bot_names() {
  local i out=""
  for ((i = 0; i < ${#B_NAMES[@]}; i++)); do out="${out:+$out, }${B_NAMES[$i]}"; done
  printf '%s' "$out"
}

participant_names() {
  local i out=""
  for ((i = 0; i < ${#P_NAMES[@]}; i++)); do out="${out:+$out, }${P_NAMES[$i]}"; done
  printf '%s' "$out"
}

set_participants_now() {
  local i
  P_NAMES=(); P_LABELS=(); P_PLISTS=(); P_WAS=(); P_PIDS=()
  for ((i = 0; i < ${#B_NAMES[@]}; i++)); do
    [ "${B_STAGES[$i]}" = relay ] || continue
    P_NAMES+=("${B_NAMES[$i]}"); P_LABELS+=("${B_LABELS[$i]}"); P_PLISTS+=("${B_PLISTS[$i]}")
    if label_loaded "${B_LABELS[$i]}"; then
      P_WAS+=(1); P_PIDS+=("$(label_pid "${B_LABELS[$i]}")")
    else
      P_WAS+=(0)
    fi
  done
}

participants_json() {
  local i
  for ((i = 0; i < ${#P_NAMES[@]}; i++)); do
    printf '%s\t%s\t%s\t%s\n' "${P_NAMES[$i]}" "${P_LABELS[$i]}" "${P_PLISTS[$i]}" "${P_WAS[$i]}"
  done | "$PYTHON" -c 'import json, sys
rows = [line.split("\t") for line in sys.stdin.read().splitlines() if line]
print(json.dumps([{"name": a, "label": b, "plist": c, "was_loaded": d == "1"} for a, b, c, d in rows]))'
}

load_participants() {
  local out n l p w
  out="$(state "$1")" || return 1
  P_NAMES=(); P_LABELS=(); P_PLISTS=(); P_WAS=(); P_PIDS=()
  while IFS=$'\t' read -r n l p w; do
    [ -n "$n" ] || continue
    P_NAMES+=("$n"); P_LABELS+=("$l"); P_PLISTS+=("$p"); P_WAS+=("$w")
  done <<< "$out"
}

stop_participants() {
  local i
  for ((i = 0; i < ${#P_LABELS[@]}; i++)); do
    launchctl bootout "gui/$(id -u)/${P_LABELS[$i]}" 2>/dev/null || true
  done
  for ((i = 0; i < ${#P_LABELS[@]}; i++)); do
    if label_loaded "${P_LABELS[$i]}"; then return 1; fi
  done
  return 0
}

relay_back() {
  if [ "$MULTI" = 1 ]; then t "update.bot_$1" "${P_NAMES[$2]}"; else t "update.relay_$1"; fi
}

start_participants() {
  local i rc=0
  for ((i = 0; i < ${#P_LABELS[@]}; i++)); do
    if [ "${P_WAS[$i]}" != 1 ]; then
      if label_loaded "${P_LABELS[$i]}"; then launchctl bootout "gui/$(id -u)/${P_LABELS[$i]}" 2>/dev/null || true; fi
      continue
    fi
    label_loaded "${P_LABELS[$i]}" && continue
    if [ -f "${P_PLISTS[$i]}" ] && launchctl bootstrap "gui/$(id -u)" "${P_PLISTS[$i]}" 2>/dev/null 9>&-; then
      relay_back running_again "$i"
    else
      relay_back not_back "$i"
      rc=1
    fi
  done
  return "$rc"
}

engine_recover() {
  local phase
  if [ ! -f "$ETXN" ]; then ENGINE_TXN=0; return 0; fi
  if ! load_participants etxn-participants; then
    t update.engine_not_undone "$ETXN"
    return 1
  fi
  phase="$(engine txn-get --txn "$ETXN" phase)" || phase=""
  if [ "$phase" = committed ]; then
    rm -rf "$ROLLBACK"
    rm -f "$ETXN"
    ENGINE_TXN=0
    return 0
  fi
  if [ "$phase" != prepared ] && ! stop_participants; then
    t update.engine_not_undone "$ETXN"
    return 1
  fi
  if ! engine rollback --txn "$ETXN"; then
    t update.engine_not_undone "$ETXN"
    return 1
  fi
  if [ -d "$ROLLBACK" ] && ! installer --undo-relay; then
    t update.engine_not_undone "$ETXN"
    return 1
  fi
  if ! start_participants; then
    t update.engine_not_undone "$ETXN"
    return 1
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
    local stage
    stage="$(state txn-get stage 2>/dev/null || true)"
    if [ "$MULTI" = 1 ]; then
      case "$stage" in
        routine_paused) t update.exit_paused_cloud_bots "$(bot_names)" ;;
        cloud_done|prompt_done) t update.exit_paused_bots "$(bot_names)" ;;
      esac
    else
      case "$stage" in
        routine_paused) t update.exit_paused_cloud ;;
        cloud_done|prompt_done) t update.exit_paused ;;
      esac
    fi
    if load_participants txn-participants 2>/dev/null; then start_participants || true; fi
    t update.stopped_at "$stage"
  fi
  [ "$CONFIG_OPEN" = 0 ] || t update.config_again
  [ "$ACTING" = 0 ] || summary
  exit "$rc"
}

no_pull_command() {
  local cmd="./update.sh --no-pull --lang $DOC_LANG"
  [ "$YES" = 0 ] || cmd="$cmd --yes"
  [ "$PLAN" = 0 ] || cmd="$cmd --plan"
  [ "$MANUAL" = 0 ] || cmd="$cmd --manual"
  [ "$PROMPT_MODE" = thin ] || cmd="$cmd --full-prompt"
  [ "$REPO_URL" = "$DEFAULT_REPO_URL" ] || cmd="$cmd --repo-url $(printf '%q' "$REPO_URL")"
  printf '%s' "$cmd"
}

dirty_files() {
  local line n=0
  t update.dirty_list >&2
  while IFS= read -r line; do
    n=$((n + 1))
    [ "$n" -gt 20 ] || printf '  %s\n' "$line" >&2
  done <<< "$1"
  [ "$n" -le 20 ] || t update.dirty_more "$n" >&2
}

pull_checkout() {
  git -C "$HERE" rev-parse --is-inside-work-tree >/dev/null 2>&1 || die update.not_git
  git -C "$HERE" rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1 || die update.no_upstream
  local old new dirty tag target
  dirty="$(git -C "$HERE" status --porcelain --untracked-files=no)" || die update.not_git
  if [ -n "$dirty" ]; then
    dirty_files "$dirty"
    if [ "$YES" = 1 ] || [ ! -t 0 ]; then die update.dirty_checkout "$(no_pull_command)"; fi
    ask update.ask_dirty_continue || die update.stopped_nothing
    return 1
  fi
  old="$(git -C "$HERE" rev-parse HEAD)" || die update.not_git
  git -C "$HERE" fetch --quiet --tags || die update.pull_failed
  tag="$(latest_tag)" || die update.pull_failed
  if [ -z "$tag" ]; then t update.no_release_tag; return 1; fi
  target="$(git -C "$HERE" rev-list -n 1 "refs/tags/$tag")" || die update.pull_failed
  if git -C "$HERE" merge-base --is-ancestor "$target" HEAD; then t update.at_latest "$tag"; return 1; fi
  git -C "$HERE" merge-base --is-ancestor HEAD "$target" || die update.tag_diverged "$tag" "$(no_pull_command)"
  git -C "$HERE" merge --ff-only -q "$target" || die update.merge_failed "$tag"
  new="$(git -C "$HERE" rev-parse HEAD)" || die update.not_git
  t update.pulled "${old:0:12}" "${new:0:12}" "$tag"
}

latest_tag() {
  local tags tag best=""
  tags="$(git -C "$HERE" tag --merged '@{u}' -l 'v*')" || return 1
  while IFS= read -r tag; do
    [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || continue
    if [ -z "$best" ] || ! at_least "${best#v}" "${tag#v}"; then best="$tag"; fi
  done <<< "$tags"
  printf '%s' "$best"
}

checkout_label() { git -C "$HERE" rev-parse --short HEAD 2>/dev/null || tq update.no_checkout; }

cloud_needed() { [ "$S_CLOUD" != unchanged ] || [ "$S_SETUP" != unchanged ] || [ "$S_CENGINE" != unchanged ]; }
cloud_pending() { [ "$S_CLOUD" = pending ] || [ "$S_SETUP" = pending ] || [ "$S_CENGINE" = pending ]; }
stale() { [ "$1" != unchanged ] && [ "$1" != pending ]; }
cloud_stale() { stale "$S_CLOUD" || stale "$S_SETUP" || stale "$S_CENGINE"; }
paste_needed() { [ "$S_PROMPT" != unchanged ] && [ "$S_PROMPT" != readback ]; }

defer_paste() {
  local i
  [ "$MANUAL" = 0 ] && [ "$ORDER" = cloud_first ] && { [ "$YES" = 1 ] || [ ! -t 0 ]; } || return 1
  for ((i = 0; i < ${#B_NAMES[@]}; i++)); do
    prompt_bot "$i" || continue
    case "${B_PROMPT[$i]}" in unchanged|readback|changed) ;; *) return 1 ;; esac
  done
}

wait_text() {
  local s="${AGENT_MESH_REPLY_WAIT:-900}"
  if [ "$((s % 60))" = 0 ]; then tq update.n_minutes "$((s / 60))"; else tq update.n_seconds "$s"; fi
}

at_least() {
  local IFS=. a b i
  [[ "$1" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || return 1
  read -r -a a <<< "$1"
  read -r -a b <<< "$2"
  for i in 0 1 2; do
    [ "$((10#${a[$i]}))" -gt "$((10#${b[$i]}))" ] && return 0
    [ "$((10#${a[$i]}))" -lt "$((10#${b[$i]}))" ] && return 1
  done
  return 0
}

pick_order() {
  ORDER=cloud_first
  if [ "$MANUAL" = 1 ]; then
    ORDER=manual
  elif cloud_needed && ! cloud_pending && [ "$S_CLOUD" = unknown ] && [ "$S_CREL" = - ]; then
    ORDER=manual; MANUAL=1; AUTO_MANUAL=1
  elif cloud_needed && ! at_least "$S_CREL" 0.8.2; then
    ORDER=stub_first
  fi
}

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

prompt_bot() { [ "${B_KINDS[$1]}" = legacy ] || [ "${B_CLOUD[$1]}" = confirmed ]; }

prompt_names() {
  local i out=""
  for ((i = 0; i < ${#B_NAMES[@]}; i++)); do
    prompt_bot "$i" || continue
    out="${out:+$out,}${B_NAMES[$i]}"
  done
  printf '%s' "$out"
}

merge_prompt() {
  local i changed=0 unknown=0 pending=0 readback=0
  for ((i = 0; i < ${#B_NAMES[@]}; i++)); do
    prompt_bot "$i" || continue
    case "${B_PROMPT[$i]}" in
      unchanged) ;;
      pending) pending=1 ;;
      readback) readback=1 ;;
      changed) changed=1 ;;
      *) unknown=1 ;;
    esac
  done
  if [ "$changed" = 1 ]; then S_PROMPT=changed
  elif [ "$unknown" = 1 ]; then S_PROMPT=unknown
  elif [ "$pending" = 1 ]; then S_PROMPT=pending
  elif [ "$readback" = 1 ]; then S_PROMPT=readback
  else S_PROMPT=unchanged; fi
}

read_paths() {
  local out n _k _s _c _r _y l p m i
  out="$("$PYTHON" "$HERE/scripts/bots.py" list --paths)" || die update.no_status
  while IFS=$'\t' read -r n _k _s _c _r _y l p m; do
    for ((i = 0; i < ${#B_NAMES[@]}; i++)); do
      if [ "${B_NAMES[$i]}" = "$n" ]; then B_LABELS[i]="$l"; B_PLISTS[i]="$p"; B_MARKERS[i]="$m"; fi
    done
  done <<< "$out"
}

read_python() {
  local out rc=0 k v
  RELAY_PYTHON=""; PY_SOURCE=""; PY_STALE=""; PY_ODD=0; PY_MIXED=0
  out="$(state relay-python)" || rc=$?
  while read -r k v; do
    case "$k" in
      python) RELAY_PYTHON="$v" ;; source) PY_SOURCE="$v" ;; stale) PY_STALE="$v" ;;
      note) if [ "$v" = mixed ]; then PY_MIXED=1; else PY_ODD=1; fi ;;
    esac
  done <<< "$out"
  if [ "$rc" != 0 ] || [ -z "$RELAY_PYTHON" ]; then
    [ -z "$PY_STALE" ] || t update.python_stale "$PY_STALE" >&2
    die update.no_python
  fi
}

read_status() {
  local out k v w i name_value
  out="$(state status --lang "$DOC_LANG" --python "$RELAY_PYTHON")" || die update.no_status
  B_NAMES=(); B_KINDS=(); B_STAGES=(); B_RELAY=(); B_PROMPT=(); B_CLOUD=(); B_REMOVING=()
  B_LABELS=(); B_PLISTS=(); B_MARKERS=()
  while read -r k v; do
    case "$k" in
      relay) S_RELAY="$v" ;; dsh) S_DSH="$v" ;; cloud) S_CLOUD="$v" ;;
      cloud_setup) S_SETUP="$v" ;; prompt) S_PROMPT="$v" ;; txn) S_TXN="$v" ;; name) S_NAME="$v" ;;
      engine) read -r S_ENGINE S_EVER S_EPIN <<< "$v" ;; etxn) S_ETXN="$v" ;; cloud_engine) S_CENGINE="$v" ;;
      cloud_release) S_CREL="$v" ;; config) S_CONFIG="$v" ;; recover) S_RECOVER="$v" ;;
      bot)
        read -r -a w <<< "$v"
        B_NAMES+=("${w[0]}"); B_KINDS+=("${w[2]}"); B_STAGES+=("${w[4]}"); B_RELAY+=("${w[8]}")
        B_PROMPT+=("${w[10]}"); B_CLOUD+=("${w[12]}"); B_REMOVING+=("${w[14]}")
        B_LABELS+=("$LABEL"); B_PLISTS+=("$PLIST"); B_MARKERS+=("$MARKER") ;;
    esac
  done <<< "$out"
  [ "$S_ENGINE" != refused ] || die update.engine_refused "$S_EVER" "$S_EPIN"
  case "$S_NAME" in
    "changed "*)
      local recorded current
      read -r _ recorded current <<< "$S_NAME"
      die update.renamed "$current" "$recorded" ;;
  esac
  for ((i = 0; i < ${#B_NAMES[@]}; i++)); do
    [ "${B_REMOVING[$i]}" = - ] || die update.bot_removing "${B_NAMES[$i]}" "${B_NAMES[$i]}"
  done
  if [ "${#B_NAMES[@]}" = 0 ]; then
    read -r _ name_value <<< "$S_NAME"
    [ -n "$name_value" ] && [ "$name_value" != - ] || name_value=bot
    B_NAMES=("$name_value"); B_KINDS=(legacy); B_STAGES=(relay); B_RELAY=("$S_RELAY"); B_PROMPT=("$S_PROMPT")
    B_CLOUD=(-); B_REMOVING=(-); B_LABELS=("$LABEL"); B_PLISTS=("$PLIST"); B_MARKERS=("$MARKER")
  else
    read_paths
  fi
  MULTI=0
  [ "${#B_NAMES[@]}" -le 1 ] || MULTI=1
  for ((i = 0; i < ${#B_KINDS[@]}; i++)); do [ "${B_KINDS[$i]}" = legacy ] || MULTI=1; done
  merge_prompt
}

plan_line() {
  local what="$3"
  [ "$2" != unchanged ] || what="-"
  "$PYTHON" "$HERE/scripts/messages.py" row 15 "$(tq "label.$1")" 10 "$(word "$2")" "$what"
}

plan_bots() {
  local i
  t update.plan_bots
  for ((i = 0; i < ${#B_NAMES[@]}; i++)); do
    if [ "${B_KINDS[$i]}" = added ] && [ "${B_CLOUD[$i]}" != confirmed ]; then
      t update.plan_bot_waiting "${B_NAMES[$i]}" "${B_NAMES[$i]}"
    else
      t update.plan_bot "${B_NAMES[$i]}" "$(word "${B_RELAY[$i]}")" "$(word "${B_PROMPT[$i]}")"
    fi
  done
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
  if [ -n "$PY_STALE" ]; then t update.plan_python_replaced "$PY_STALE" "$RELAY_PYTHON"
  elif [ "$PY_MIXED" = 1 ]; then t update.plan_python_mixed "$RELAY_PYTHON"; fi
  [ "$PY_ODD" = 0 ] || t update.plan_python_odd "$RELAY_PYTHON"
  case "$S_ETXN" in
    -) ;;
    committed) t update.plan_etxn_committed ;;
    *) t update.plan_etxn "$S_ETXN" ;;
  esac
  case "$S_ENGINE" in
    older) plan_line engine changed "$(tq update.plan_engine_older "$S_EVER" "$S_EPIN")" ;;
    stale) plan_line engine changed "$(tq update.plan_engine_stale)" ;;
    *) plan_line engine unchanged "-" ;;
  esac
  case "$S_CONFIG" in
    repair|reload)
      t update.plan_config
      [ "$BACKLOG" = 0 ] || t update.plan_backlog "$BACKLOG" ;;
    unsupported) t update.plan_config_unsupported "$CFG" ;;
  esac
  if [ "$S_DSH" = absent ]; then plan_line dsh absent "$(tq update.plan_dsh_absent)"
  else plan_line dsh "$S_DSH" "$(tq update.plan_dsh)"; fi
  plan_line cloud "$S_CLOUD" ""
  plan_line setup "$S_SETUP" ""
  plan_line cengine "$S_CENGINE" ""
  if cloud_pending; then
    t update.plan_cloud_pending
  elif cloud_needed; then
    if [ "$MANUAL" = 1 ]; then
      t update.plan_cloud_manual
      [ "$AUTO_MANUAL" = 0 ] || t update.plan_no_record
    elif [ "$ORDER" = stub_first ]; then t update.plan_cloud_stub_first "$S_EPIN"
    else t update.plan_cloud_auto "$S_EPIN"; fi
  fi
  [ "$S_RECOVER" = 0 ] || t update.plan_recover
  case "$S_PROMPT" in
    pending) plan_line prompt pending "$(tq update.plan_prompt_pending)" ;;
    readback) plan_line prompt readback "$(tq update.plan_prompt_readback)" ;;
    *)
      plan_line prompt "$S_PROMPT" "$(tq update.plan_prompt)"
      [ "$DEFER" = 0 ] || t update.plan_prompt_later ;;
  esac
  [ "$MULTI" = 0 ] || plan_bots
  if [ "$S_ENGINE" != unchanged ] || config_needed; then t update.plan_engine_order; fi
  if [ "$MANUAL" = 1 ] && { cloud_needed || paste_needed; }; then
    if [ "$MULTI" = 1 ]; then t update.plan_manual_order_bots; else t update.plan_manual_order; fi
  fi
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
[ "$n" = 8 ] || stop "the checksum list is incomplete; nothing was changed"
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

new_req() { "$PYTHON" -c 'import secrets, time; print("%08x%s" % (int(time.time()), secrets.token_hex(4)))'; }

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
  local i="$1" rn=()
  [ "${B_KINDS[$i]}" != added ] || rn=(--relay-name "${B_NAMES[$i]}")
  echo
  if [ "$MULTI" = 1 ]; then t update.prompt_replace_bot "${B_NAMES[$i]}" "$DOC_LANG"
  else t update.prompt_replace "$DOC_LANG"; fi
  echo "----------------------------------------------------------------"
  state show-prompt --lang "$DOC_LANG" ${rn[@]+"${rn[@]}"}
  echo
  echo "----------------------------------------------------------------"
}

prompt_one() {
  local i="$1" rn=() need recover=0
  [ "${B_KINDS[$i]}" != added ] || rn=(--relay-name "${B_NAMES[$i]}")
  need="$(state prompt-need --lang "$DOC_LANG" ${rn[@]+"${rn[@]}"})" || die update.no_status
  if [ "$need" = stub ] && cloud_needed && [ "$ORDER" = cloud_first ]; then
    t update.prompt_after_cloud "${B_NAMES[$i]}"
    return 0
  fi
  SHOWN="$(state digest prompt --lang "$DOC_LANG" ${rn[@]+"${rn[@]}"})"
  if [ "$need" = readback ]; then
    state mark prompt pending --lang "$DOC_LANG" --expect "$SHOWN" ${rn[@]+"${rn[@]}"}
    B_PROMPT[i]=readback
    return 0
  fi
  if [ "$need" = stub ] && cloud_needed; then
    recover=1
    state mark prompt pending --recover --lang "$DOC_LANG" --expect "$SHOWN" ${rn[@]+"${rn[@]}"}
  fi
  prompt_text "$i"
  if [ "$MULTI" = 1 ]; then
    if ! ask update.ask_bot_prompt_saved_now "${B_NAMES[$i]}"; then
      [ "$recover" = 0 ] || state prompt-undo ${rn[@]+"${rn[@]}"}
      die update.bot_prompt_not_recorded "${B_NAMES[$i]}"
    fi
  elif ! ask update.ask_prompt_saved_now; then
    [ "$recover" = 0 ] || state prompt-undo ${rn[@]+"${rn[@]}"}
    die update.prompt_not_recorded
  fi
  if [ "$need" = full ]; then
    state mark prompt confirmed --lang "$DOC_LANG" --expect "$SHOWN" ${rn[@]+"${rn[@]}"}
    B_PROMPT[i]=unchanged
  else
    [ "$recover" = 1 ] || state mark prompt pending --lang "$DOC_LANG" --expect "$SHOWN" ${rn[@]+"${rn[@]}"}
    B_PROMPT[i]=readback
  fi
}

prompt_step() {
  local i
  for ((i = 0; i < ${#B_NAMES[@]}; i++)); do
    prompt_bot "$i" && [ "${B_PROMPT[$i]}" != unchanged ] && [ "${B_PROMPT[$i]}" != readback ] || continue
    prompt_one "$i"
  done
  merge_prompt
  R_PROMPT="$(tq r.updated)"
  [ "$S_PROMPT" != readback ] || R_PROMPT="$(tq r.readback_waiting)"
}

full_back() {
  local i rn shown=0
  for ((i = 0; i < ${#B_NAMES[@]}; i++)); do
    rn=()
    [ "${B_KINDS[$i]}" != added ] || rn=(--relay-name "${B_NAMES[$i]}")
    state prompt-recover ${rn[@]+"${rn[@]}"} || continue
    shown=1
    echo
    if [ "$MULTI" = 1 ]; then t update.full_back_bot "${B_NAMES[$i]}" "$DOC_LANG"
    else t update.full_back "$DOC_LANG"; fi
    echo "----------------------------------------------------------------"
    state_full show-prompt --lang "$DOC_LANG" ${rn[@]+"${rn[@]}"}
    echo
    echo "----------------------------------------------------------------"
    SHOWN="$(state_full digest prompt --lang "$DOC_LANG" ${rn[@]+"${rn[@]}"})"
    if [ -t 0 ] && ask update.ask_full_back; then
      state_full mark prompt confirmed --lang "$DOC_LANG" --expect "$SHOWN" ${rn[@]+"${rn[@]}"}
      R_PROMPT="$(tq r.full_back)"
    else
      R_PROMPT="$(tq r.full_back_pending)"
      t update.full_back_later
    fi
  done
  [ "$shown" = 1 ] || return 1
  t update.full_back_retry
}

cloud_fail() {
  printf 'update: %s\n' "$(tq "$@")" >&2
  full_back || true
  exit 1
}

recover_step() {
  local rc=0
  echo
  t update.recover_check
  if [ "$REL_STATE" = ok ] || release_check; then
    request_check || rc=$?
    if [ "$rc" = 0 ]; then
      read_status
      return 0
    fi
  else
    printf 'update: %s\n' "$REL_NOTE" >&2
  fi
  full_back || true
  exit 1
}

stub_first_ready() {
  if [ "$MULTI" = 1 ]; then
    TARGET=""
    pick_target || die update.no_target
  else
    relay_loaded || die update.relay_down
  fi
}

readback_step() {
  local i rn rc out to req waited limit="${AGENT_MESH_REPLY_WAIT:-900}"
  read_status
  for ((i = 0; i < ${#B_NAMES[@]}; i++)); do
    prompt_bot "$i" && [ "${B_PROMPT[$i]}" = readback ] || continue
    if cloud_needed; then
      t update.readback_after_cloud "${B_NAMES[$i]}"
      continue
    fi
    rn=()
    [ "${B_KINDS[$i]}" != added ] || rn=(--relay-name "${B_NAMES[$i]}")
    if [ "${B_STAGES[$i]}" != relay ] || ! label_loaded "${B_LABELS[$i]}"; then
      t update.readback_relay_down "${B_NAMES[$i]}"
      FAILED=1
      continue
    fi
    to="$(state bot-address ${rn[@]+"${rn[@]}"})" || { FAILED=1; continue; }
    echo
    t update.readback_heading "$to"
    rc=0; out="$(state stub-check --lang "$DOC_LANG" ${rn[@]+"${rn[@]}"} 2>&1)" || rc=$?
    if [ "$rc" = 3 ]; then
      req="$(state stub-new --lang "$DOC_LANG" ${rn[@]+"${rn[@]}"})" || die update.no_request
      if ! "$RN" send "$to" - < <(printf 'agent-mesh stub %s\nlang=%s' "$req" "$DOC_LANG") >/dev/null 9>&-; then
        t update.readback_send_failed "$to"
        FAILED=1
        continue
      fi
      t update.readback_sent "$to" "$(wait_text)"
      waited=0
      while :; do
        rc=0; out="$(state stub-check --lang "$DOC_LANG" ${rn[@]+"${rn[@]}"} 2>&1)" || rc=$?
        [ "$rc" = 3 ] && [ "$waited" -lt "$limit" ] || break
        sleep 5
        waited=$((waited + 5))
        [ $((waited % 60)) != 0 ] || t update.still_waiting "$((waited / 60))"
      done
    fi
    [ -z "$out" ] || printf '%s\n' "$out"
    case "$rc" in
      0) B_PROMPT[i]=unchanged ;;
      3)
        state stub-timeout ${rn[@]+"${rn[@]}"} || true
        B_PROMPT[i]=changed
        t update.readback_timeout "$(wait_text)" "$to"
        FAILED=1 ;;
      5) B_PROMPT[i]=changed; FAILED=1 ;;
      *) FAILED=1 ;;
    esac
  done
  merge_prompt
  case "$S_PROMPT" in
    unchanged) R_PROMPT="$(tq r.stub_confirmed)" ;;
    readback) R_PROMPT="$(tq r.readback_waiting)" ;;
    changed) R_PROMPT="$(tq r.stub_missing)" ;;
  esac
}

guided() {
  local stage paused=0
  NEED=""
  cloud_needed && NEED="cloud"
  ! paste_needed || NEED="${NEED:+$NEED,}prompt"
  if [ -n "$NEED" ] && cloud_needed && [ "$REL_STATE" != ok ]; then
    release_check || fail "$REL_NOTE"
  fi
  if [ "$S_TXN" = - ]; then
    set_participants_now
    TXN_ACTIVE=1
    state txn-start --lang "$DOC_LANG" --need "$NEED" --participants "$(participants_json)" \
      --prompt-bots "$(prompt_names)" --commit "$(git -C "$HERE" rev-parse HEAD 2>/dev/null || echo none)"
    stage=stopping
  else
    TXN_ACTIVE=1
    stage="$S_TXN"
    t update.continuing "$stage"
    state txn-set need "$NEED" --prompt-bots "$(prompt_names)"
    load_participants txn-participants || die update.no_status
  fi

  if [ "$MULTI" = 1 ]; then t update.g_stop_relays "$(participant_names)"; else t update.g_stop_relay; fi
  stop_participants || die update.relay_no_stop
  if [ "$stage" = stopping ]; then state txn-set stage relay_stopped; stage=relay_stopped; fi
  case "$stage" in routine_paused|cloud_done|prompt_done) paused=1 ;; esac

  if [ -n "$NEED" ]; then
    if [ "$MULTI" = 1 ]; then
      if [ "$paused" = 1 ]; then ask update.g_still_paused_all "$(bot_names)" || die update.stopped
      else ask update.g_pause_all "$(bot_names)" || die update.stopped; fi
    elif [ "$paused" = 1 ]; then
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
    if paste_needed; then
      t update.g_prompt
      prompt_step
      state txn-set stage prompt_done
    fi
    resume_routines
  elif [ "$paused" = 1 ]; then
    resume_routines
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

resume_routines() {
  if [ "$MULTI" = 1 ]; then ask update.g_resume_all "$(bot_names)" || die update.stopped
  else ask update.g_resume || die update.stopped; fi
  state txn-set stage routine_resumed
}

engine_others() {
  local i skip=() others
  for ((i = 0; i < ${#P_PIDS[@]}; i++)); do
    [ -z "${P_PIDS[$i]}" ] || skip+=(--skip-parent "${P_PIDS[$i]}")
  done
  others="$(engine others --bin "$RN" ${skip[@]+"${skip[@]}"})" || others=""
  if [ -n "$others" ]; then
    t update.e_others
    printf '%s\n' "$others" | sed 's/^/  /'
  fi
}

engine_prepare() {
  ENGINE_TXN=1
  if ! engine prepare --txn "$ETXN" --home "$RN_HOME" --bin "$RN" --kind "$1" --keep "participants=$(participants_json)"
  then
    ENGINE_TXN=0
    rm -f "$ETXN"
    die update.e_no_txn "$ETXN"
  fi
  echo
  t update.e_stop_relays "$(participant_names)"
  if ! stop_participants; then
    engine_recover || true
    die update.e_relay_no_stop
  fi
  engine phase --txn "$ETXN" stopped
}

engine_relays() {
  [ "${#P_NAMES[@]}" -gt 0 ] || return 0
  installer --restart-relay --keep-rollback
}

engine_commit() {
  if ! engine phase --txn "$ETXN" committed; then
    R_ENGINE="$(tq r.failed)"
    die update.e_not_committed "$ETXN"
  fi
  rm -rf "$ROLLBACK"
  rm -f "$ETXN"
  ENGINE_TXN=0
  R_RELAY="$(tq r.updated)"
  S_RELAY=unchanged
}

config_needed() { [ "$S_CONFIG" = repair ] || [ "$S_CONFIG" = reload ]; }

config_begin() {
  state config-pending || die update.config_no_record "$LIB_DIR/deployed.json"
  CONFIG_OPEN=1
}

config_write() {
  local out
  if out="$("$PYTHON" "$HERE/scripts/engine_config.py" clean --path "$CFG" auto_update lan 9>&-)"; then
    [ "${out%% *}" = 0 ] || t update.config_cleaned "${out%% *}" "$CFG" "${out#* }"
    if engine config-off --path "$CFG" auto_update lan && state config-written; then return 0; fi
  fi
  R_ENGINE="$(tq r.failed)"
  engine_recover || { ENGINE_TXN=0; die update.e_not_undone; }
  die update.config_write_failed "$CFG"
}

config_done() {
  if state config-verified; then
    CONFIG_OPEN=0
  else
    t update.config_not_loaded
    FAILED=1
  fi
}

backlog_wait() {
  local i n waited=0 limit="${AGENT_MESH_PENDING_WAIT:-900}" loaded=0
  for ((i = 0; i < ${#B_NAMES[@]}; i++)); do
    if [ "${B_STAGES[$i]}" = relay ] && label_loaded "${B_LABELS[$i]}"; then loaded=1; fi
  done
  [ "$loaded" = 1 ] || return 0
  while :; do
    n="$(state relay-pending)" || n=0
    [ "$n" != 0 ] || return 0
    [ "$waited" != 0 ] || t update.backlog_waiting "$n" "$((limit / 60))"
    [ "$waited" -lt "$limit" ] || die update.backlog_timeout "$n"
    sleep 5; waited=$((waited + 5))
  done
}

engine_step() {
  local tmp rc=0 backup base n fix=0
  if config_needed; then fix=1; fi
  echo
  set_participants_now
  engine_others
  if [ "$S_ENGINE" != older ]; then
    if [ "$fix" = 1 ]; then t update.e_config "$S_EVER"; else t update.e_restart "$S_EVER"; fi
    [ "$fix" = 0 ] || config_begin
    engine_prepare restart
    [ "$fix" = 0 ] || config_write
    if ! engine restart --home "$RN_HOME" --bin "$RN" --txn "$ETXN"; then
      R_ENGINE="$(tq r.restart_failed)"
      engine_recover || { ENGINE_TXN=0; die update.e_not_undone; }
      die update.e_restart_failed
    fi
    t update.e_start_relay "$S_EVER"
    if ! engine_relays; then
      R_ENGINE="$(tq r.daemon_restarted)"; R_RELAY="$(tq r.failed)"
      engine_recover || { ENGINE_TXN=0; die update.e_relay_not_undone; }
      die update.e_relay_after_restart
    fi
    engine_commit
    R_ENGINE="$(tq r.daemon_restarted_on "$S_EVER")"
    [ "$fix" = 0 ] || config_done
    return 0
  fi
  t update.e_upgrade "$S_EVER" "$S_EPIN"
  t update.e_upgrade_explain "$S_EPIN"
  [ "$fix" = 0 ] || t update.e_config_upgrade
  tmp="$(mktemp -d "$LIB_DIR/.engine-new.XXXXXX")" || die update.e_no_tmp "$LIB_DIR"
  ENGINE_TMP="$tmp"
  if ! installer --fetch-engine "$tmp" >/dev/null; then
    R_ENGINE="$(tq r.download_failed)"
    die update.e_download_failed
  fi
  [ "$fix" = 0 ] || config_begin
  engine_prepare upgrade
  [ "$fix" = 0 ] || config_write
  backup="$LIB_DIR/engine-backup-$(date -u +%Y%m%dT%H%M%SZ)"
  base="$backup"; n=1
  while [ -e "$backup" ]; do n=$((n + 1)); backup="$base-$n"; done
  engine switch --home "$RN_HOME" --bin "$RN" --new "$tmp/reason-network" --pinned "$S_EPIN" \
    --backup "$backup" --txn "$ETXN" || rc=$?
  rm -rf "$tmp"; ENGINE_TMP=""
  if [ "$rc" != 0 ]; then
    R_ENGINE="$(tq r.failed)"
    engine_recover || { ENGINE_TXN=0; die update.e_not_undone; }
    R_ENGINE="$(tq r.rolled_back "$S_EVER" "$backup")"
    die update.e_failed_back "$S_EVER"
  fi
  t update.e_start_relay "$S_EPIN"
  if ! engine_relays; then
    R_ENGINE="$(tq r.failed)"; R_RELAY="$(tq r.failed)"
    engine_recover || { ENGINE_TXN=0; die update.e_relay_not_undone; }
    R_ENGINE="$(tq r.relay_rolled_back "$S_EPIN" "$S_EVER" "$backup")"
    die update.e_relay_failed_back "$S_EVER"
  fi
  engine_commit
  state set-engine "$S_EPIN" || t update.e_not_recorded "$LIB_DIR/deployed.json"
  R_ENGINE="$(tq r.upgraded "$S_EVER" "$S_EPIN" "$backup")"
  [ "$fix" = 0 ] || config_done
}

update_dsh() {
  if "$BASH" "$HERE/dsh/mac-install.sh" --plugin-only 9>&-; then
    R_DSH="$(tq r.dsh_updated)"
  else
    R_DSH="$(tq r.dsh_failed)"
    FAILED=1
  fi
}

add_pending_step() {
  local i rc out again=0
  for ((i = 0; i < ${#B_NAMES[@]}; i++)); do
    [ "${B_KINDS[$i]}" = added ] && [ "${B_CLOUD[$i]}" = pending ] || continue
    rc=0; out="$(state add-reply --relay-name "${B_NAMES[$i]}" --lang "$DOC_LANG" 2>&1)" || rc=$?
    case "$rc" in
      0) [ -z "$out" ] || printf '%s\n' "$out"; again=1 ;;
      3) ;;
      *) [ -z "$out" ] || printf '%s\n' "$out" ;;
    esac
  done
  [ "$again" = 0 ] || read_status
}

late_stub_step() {
  local i rn rc out again=0
  for ((i = 0; i < ${#B_NAMES[@]}; i++)); do
    prompt_bot "$i" && [ "${B_PROMPT[$i]}" = changed ] || continue
    rn=()
    [ "${B_KINDS[$i]}" != added ] || rn=(--relay-name "${B_NAMES[$i]}")
    rc=0; out="$(state stub-check --late --lang "$DOC_LANG" ${rn[@]+"${rn[@]}"} 2>&1)" || rc=$?
    [ "$rc" = 3 ] || { [ -z "$out" ] || printf '%s\n' "$out"; }
    [ "$rc" != 0 ] || again=1
  done
  [ "$again" = 0 ] || read_status
}

pending_step() {
  local i rn rc
  if cloud_pending; then
    ask update.ask_setup_done "$S_EPIN" || die update.setup_first
    [ "$S_CLOUD" != pending ] || state mark cloud confirmed --if-pending
    [ "$S_SETUP" != pending ] || state mark cloud_setup confirmed --if-pending
    [ "$S_CENGINE" != pending ] || state mark cloud_engine confirmed --if-pending
    read_status
    cloud_needed || { R_CLOUD="$(tq r.confirmed)"; R_SETUP="$(tq r.confirmed)"; R_CENGINE="$(tq r.confirmed)"; }
  fi
  [ "$S_PROMPT" = pending ] || return 0
  for ((i = 0; i < ${#B_NAMES[@]}; i++)); do
    prompt_bot "$i" && [ "${B_PROMPT[$i]}" = pending ] || continue
    rn=()
    [ "${B_KINDS[$i]}" != added ] || rn=(--relay-name "${B_NAMES[$i]}")
    if [ "$MULTI" = 1 ]; then
      ask update.ask_bot_prompt_saved "${B_NAMES[$i]}" "$DOC_LANG" && rc=0 || rc=1
    else
      ask update.ask_prompt_saved "$DOC_LANG" && rc=0 || rc=1
    fi
    if [ "$rc" = 0 ]; then
      state mark prompt confirmed --lang "$DOC_LANG" --if-pending ${rn[@]+"${rn[@]}"}
      B_PROMPT[i]=unchanged
    else
      B_PROMPT[i]=changed
    fi
  done
  merge_prompt
  [ "$S_PROMPT" != unchanged ] || R_PROMPT="$(tq r.confirmed)"
}

cloud_recorded() {
  S_CLOUD=unchanged; S_SETUP=unchanged; S_CENGINE=unchanged
  R_CLOUD="$(tq r.cloud_updated "$REL_TAG")"; R_SETUP="$(tq r.confirmed)"; R_CENGINE="$(tq r.engine_confirmed "$S_EPIN")"
}

pick_target() {
  local i addr mark m_name m_pid _ pid
  for ((i = 0; i < ${#B_NAMES[@]}; i++)); do
    [ "${B_STAGES[$i]}" = relay ] && prompt_bot "$i" && label_loaded "${B_LABELS[$i]}" || continue
    if [ "${B_KINDS[$i]}" = added ]; then
      addr="$(state bot-address --relay-name "${B_NAMES[$i]}" 2>/dev/null)" || continue
    else
      addr="$(state bot-address 2>/dev/null)" || continue
    fi
    mark="$("$PYTHON" "$HERE/scripts/relay_names.py" marker "${B_MARKERS[$i]}" 2>/dev/null)" || continue
    read -r m_name m_pid _ <<< "$mark"
    pid="$(label_pid "${B_LABELS[$i]}")"
    [ "$m_name" = "$addr" ] && [ -n "$pid" ] && [ "$pid" = "$m_pid" ] || continue
    TARGET="$addr"
    TARGET_NAME=""
    [ "${B_KINDS[$i]}" != added ] || TARGET_NAME="${B_NAMES[$i]}"
    return 0
  done
  return 1
}

request_check() {
  local rc=0 out
  out="$(state request-check --tag "$REL_TAG" --commit "$REL_COMMIT" 2>&1)" || rc=$?
  LAST_CHECK="$out"
  case "$rc" in
    0) printf '%s\n' "$out"; cloud_recorded ;;
    1) printf '%s\n' "$out"
       R_CLOUD="$(tq r.cloud_stopped)"
       cloud_fail update.cloud_stopped ;;
    3) [ -z "$out" ] || printf '%s\n' "$out" ;;
  esac
  return "$rc"
}

cloud_auto() {
  local addr="" req i=0 rounds rc body lang=()
  [ "$REL_STATE" = ok ] || release_check || { printf 'update: %s\n' "$REL_NOTE" >&2; full_back || true; exit 1; }
  [ "$MULTI" = 1 ] || addr="$(state bot-address)" || cloud_fail update.no_address
  echo
  t update.cloud_heading "$REL_TAG"
  rc=0; request_check || rc=$?
  [ "$rc" != 0 ] || { t update.cloud_earlier_reply; return 0; }
  TARGET_NAME=""
  if [ "$MULTI" = 1 ]; then
    TARGET=""
    pick_target || cloud_fail update.no_target
    addr="$TARGET"
  else
    relay_loaded || cloud_fail update.relay_down
  fi
  body="agent-mesh update $REL_TAG $REL_COMMIT"
  [ "$ORDER" = cloud_first ] || lang=(--lang "$DOC_LANG")
  req="$(state request-new --tag "$REL_TAG" --commit "$REL_COMMIT" --mode line --address "$addr" \
    ${TARGET_NAME:+--relay-name "$TARGET_NAME"} ${lang[@]+"${lang[@]}"})" || cloud_fail update.no_request
  body="$body $req"
  [ "${#lang[@]}" = 0 ] || body="$body"$'\n'"lang=$DOC_LANG"
  "$RN" send "$addr" - < <(printf '%s' "$body") >/dev/null 9>&- || cloud_fail update.send_failed "$addr"
  t update.sent "$addr"
  t update.waiting "$(wait_text)"
  rounds=$(( ${AGENT_MESH_REPLY_WAIT:-900} / 5 ))
  while :; do
    rc=0; request_check || rc=$?
    [ "$rc" != 0 ] || return 0
    if [ "$i" -ge "$rounds" ]; then
      [ "$rc" != 4 ] || printf '%s\n' "$LAST_CHECK"
      R_CLOUD="$(tq r.cloud_waiting)"
      t update.timeout "$(wait_text)"
      FAILED=1
      if full_back; then exit 1; fi
      return 0
    fi
    i=$((i + 1))
    [ $((i % 12)) != 0 ] || t update.still_waiting "$((i * 5 / 60))"
    sleep 5
  done
}

main() {
  LIB_DIR="$HOME/.local/lib/agent-mesh"; ROLLBACK="$LIB_DIR/rollback"
  LABEL="com.local.agent-relay"; PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  MARKER="$HOME/Library/Application Support/agent-relay/registered"
  RN="$HOME/.local/bin/reason-network"; RN_HOME="$HOME/.reason/network"; ETXN="$LIB_DIR/engine-upgrade.json"
  CFG="$RN_HOME/config.toml"; DEFAULT_REPO_URL="https://github.com/xiaozhenliu/agent-mesh.git"
  LANG_ARG=""; YES=0; PULL=1; PLAN=0; MANUAL=0; REPO_URL="$DEFAULT_REPO_URL"
  REL_STATE=""; REL_NOTE=""; REL_TAG=""; REL_COMMIT=""; REL_SUMS=""; LAST_CHECK=""; TARGET=""
  FAILED=0; NEED=""; SHOWN=""; SHOWN_SETUP=""; SHOWN_ENGINE=""; S_NAME=""; REPLY_TEXT=""; S_CREL=-
  S_RECOVER=0; ORDER=cloud_first; AUTO_MANUAL=0; TARGET_NAME=""; DEFER=0
  S_ENGINE=unchanged; S_EVER=""; S_EPIN=""; S_ETXN=-; S_CENGINE=unchanged; S_CONFIG=ok; BACKLOG=0
  pick_lang "$@"
  parse_args "$@"
  R_RELAY="$(tq r.unchanged)"; R_DSH="$R_RELAY"; R_CLOUD="$R_RELAY"; R_SETUP="$R_RELAY"; R_PROMPT="$R_RELAY"
  R_ENGINE="$R_RELAY"; R_CENGINE="$R_RELAY"

  installer --preflight || die update.preflight_failed
  PYTHON="$(python3 -c 'import sys; print(sys.executable)')"
  mesh_lock_take "$PYTHON" || exit 1

  if [ "$PULL" = 1 ] && pull_checkout; then
    trap - EXIT INT TERM
    exec "$BASH" "$HERE/update.sh" --no-pull "$@"
  fi

  read_python
  read_status
  [ "$PLAN" = 1 ] || add_pending_step
  [ "$PLAN" = 1 ] || late_stub_step
  pick_order
  local terminal=0 confirm=0
  ! paste_needed || ! defer_paste || DEFER=1
  if [ "$S_TXN" != - ] || { paste_needed && [ "$DEFER" = 0 ]; } || cloud_pending || [ "$S_RECOVER" != 0 ] \
    || { [ "$MANUAL" = 1 ] && cloud_needed; }; then
    terminal=1
  fi
  if cloud_needed || [ "$S_ENGINE" != unchanged ] || [ "$S_ETXN" != - ] || config_needed; then
    confirm=1
  fi
  if config_needed; then BACKLOG="$(state relay-pending)" || BACKLOG=0; fi
  if cloud_stale; then
    release_check || [ "$PLAN" = 1 ] || fail "$REL_NOTE"
  fi
  print_plan
  [ "$PLAN" = 0 ] || exit 0
  if [ "$S_CONFIG" = unsupported ]; then
    state config-pending || die update.config_no_record "$LIB_DIR/deployed.json"
    die update.config_unsupported "$CFG"
  fi
  if { [ "$terminal" = 1 ] || { [ "$confirm" = 1 ] && [ "$YES" != 1 ]; }; } && [ ! -t 0 ]; then
    t update.needs_terminal "$DOC_LANG"
    exit 3
  fi
  state entry --checkout "$HERE" --lang "$DOC_LANG" || t update.entry_failed
  if [ "$PY_SOURCE" = plist ] && [ "$S_RELAY" = unchanged ]; then
    state relay-python --record >/dev/null || t update.python_not_recorded "$LIB_DIR/deployed.json"
  fi
  [ "$S_CONFIG" != ok ] || state config-verified || true
  "$PYTHON" "$HERE/scripts/reply_convention.py" install || t update.convention_failed "$LIB_DIR/templates"
  if [ "$terminal" = 0 ] && [ "$confirm" = 0 ] && [ "$S_RELAY" = unchanged ] && [ "$S_DSH" != changed ] \
    && [ "$S_PROMPT" != readback ] && ! paste_needed; then
    t update.up_to_date
    exit 0
  fi
  pending_step
  if [ "$S_TXN" = - ] && [ "$S_ETXN" = - ] && [ "$S_ENGINE" = unchanged ] && [ "$S_PROMPT" = unchanged ] \
    && [ "$S_RELAY" = unchanged ] && [ "$S_DSH" != changed ] && ! cloud_needed && ! config_needed; then
    summary
    exit 0
  fi
  if [ "$YES" != 1 ] && [ -t 0 ]; then
    if paste_needed && [ "$MANUAL" = 0 ]; then t update.will_paste; fi
    ask_go || die update.stopped_nothing
  fi
  ACTING=1
  if [ "$S_RECOVER" != 0 ]; then recover_step; pick_order; fi

  local changed=0
  if [ "$S_ETXN" != - ]; then
    if [ "$S_ETXN" = committed ]; then t update.etxn_committed; else t update.etxn_first "$S_ETXN"; fi
    ENGINE_TXN=1
    engine_recover || { ENGINE_TXN=0; die update.etxn_failed; }
    changed=1
    read_status
  fi
  if [ "$S_TXN" != - ]; then
    guided
    changed=1
    read_status
  fi
  if [ "$S_ENGINE" != unchanged ] || config_needed; then
    if config_needed; then backlog_wait; fi
    engine_step
    changed=1
  fi
  if [ "$MANUAL" = 1 ] && { cloud_needed || paste_needed; }; then
    guided
    changed=1
  elif [ "$S_RELAY" = changed ]; then
    if installer --restart-relay; then R_RELAY="$(tq r.updated)"; else R_RELAY="$(tq r.failed)"; FAILED=1; fi
    changed=1
  fi
  if [ "$S_DSH" = changed ]; then update_dsh; changed=1; fi
  if [ "$ORDER" = cloud_first ] && cloud_needed; then
    cloud_auto
    changed=1
  fi
  if paste_needed && [ "$DEFER" = 1 ]; then
    echo
    t update.prompt_later "$DOC_LANG"
    R_PROMPT="$(tq r.prompt_later)"
  elif paste_needed; then
    [ "$ORDER" != stub_first ] || ! cloud_needed || stub_first_ready
    prompt_step
    changed=1
  fi
  if [ "$ORDER" != cloud_first ] && cloud_needed; then
    cloud_auto
    changed=1
  fi
  if [ "$S_PROMPT" = readback ]; then
    readback_step
    changed=1
  fi

  if [ "$changed" = 1 ]; then
    echo
    t update.check
    "$BASH" "$HERE/check.sh" --lang "$DOC_LANG" 9>&- || CHECK_FAILED=1
  fi
  [ "$FAILED" = 0 ] && [ "$CHECK_FAILED" = 0 ] || exit 1
}

TXN_ACTIVE=0; ACTING=0; ENGINE_TXN=0; ENGINE_TMP=""; MULTI=0; CONFIG_OPEN=0; PROMPT_MODE=thin; CHECK_FAILED=0
B_NAMES=(); P_NAMES=(); P_LABELS=(); P_PLISTS=(); P_WAS=(); P_PIDS=()
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
main "$@"
