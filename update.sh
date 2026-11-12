#!/usr/bin/env bash
# agent-mesh: update an installed Mac to the version in this checkout.
#   ./update.sh [--lang en|zh-CN] [--yes] [--no-pull] [--plan]
#   --plan     show what differs and what would run, then exit; the deployment is not changed
#   --no-pull  use this checkout as is; otherwise git pull --ff-only runs first
#   --yes      skip the confirmation when only the relay or the dsh plugin changes (never the engine upgrade)
#   --lang     routine prompt language; default: the recorded one, then AGENT_MESH_LANG, then en
# Relay and dsh plugin updates run unattended. A Reason Network upgrade on this Mac asks first (terminal), switches
# the relay before the engine, keeps a backup and rolls back on failure. When the cloud scripts, the cloud engine or
# the routine prompt changed, it walks you through the [GROK-UI] steps in order (terminal required) and starts the
# previous relay again if you stop.
# Exit: 0 done or already up to date, 1 not fully done (see the summary), 2 usage, 3 needs a terminal (nothing changed).
set -euo pipefail

die() { echo "update: $*" >&2; exit 1; }
ask() { local a=""; read -r -p "$1 [y/N] " a || true; [[ "$a" == [yY]* ]]; }
state() { "$PYTHON" "$HERE/scripts/deploy_state.py" "$@"; }
service() { echo "gui/$(id -u)/$LABEL"; }
relay_loaded() { launchctl print "$(service)" >/dev/null 2>&1; }
need_has() { case ",$NEED," in *,"$1",*) return 0 ;; *) return 1 ;; esac; }
engine() { "$PYTHON" "$HERE/box/rn-engine.py" "$@"; }
relay_pid() { launchctl print "$(service)" 2>/dev/null | sed -n 's/^[[:space:]]*pid = \([0-9][0-9]*\)$/\1/p' | head -n 1; }

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --lang) [ $# -ge 2 ] || { echo "update: --lang needs en or zh-CN" >&2; exit 2; }; LANG_ARG="$2"; shift 2 ;;
      --yes|-y) YES=1; shift ;;
      --no-pull) PULL=0; shift ;;
      --plan) PLAN=1; shift ;;
      -h|--help) sed -n '2,10p' "$HERE/update.sh" | sed 's/^# \{0,1\}//'; exit 0 ;;
      *) echo "update: unknown option: $1 (see ./update.sh --help)" >&2; exit 2 ;;
    esac
  done
  case "${LANG_ARG:-en}" in en|zh-CN) ;; *) echo "update: unsupported language '$LANG_ARG'; choose en or zh-CN" >&2; exit 2 ;; esac
}

take_lock() {
  mkdir -p "$LIB_DIR"
  if ! mkdir "$LOCK" 2>/dev/null; then
    local pid cmd
    pid="$(cat "$LOCK/pid" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      cmd="$(ps -p "$pid" -o command= 2>/dev/null)" || cmd="update.sh"
      case "$cmd" in *update.sh*) die "another ./update.sh is running (pid $pid)" ;; esac
    fi
    echo "note: removing the lock of an earlier ./update.sh that did not finish"
    rm -rf "$LOCK"
    mkdir "$LOCK" || die "cannot create $LOCK"
  fi
  echo $$ > "$LOCK/pid"
  LOCKED=1
}

release_lock() {
  if [ "$LOCKED" = 1 ]; then rm -rf "$LOCK"; LOCKED=0; fi
}

summary() {
  echo
  echo "== summary"
  printf '  %-15s %s\n' "relay" "$R_RELAY" "engine" "$R_ENGINE" "dsh plugin" "$R_DSH" "cloud scripts" "$R_CLOUD" \
    "cloud setup" "$R_SETUP" "cloud engine" "$R_CENGINE" "routine prompt" "$R_PROMPT"
}

engine_recover() {   # undo an unfinished engine switch (binary only), then bring the relay back as it was
  local was="$ENGINE_WAS"
  if [ -f "$ETXN" ]; then
    was="$(engine txn-get --txn "$ETXN" relay_was_loaded)" || was=1
    if ! engine rollback --txn "$ETXN"; then
      echo "!! The engine switch was not undone; follow the steps above, then run ./update.sh again. Record: $ETXN"
      return 1
    fi
  fi
  if [ "$was" = 1 ] && ! relay_loaded; then
    "$BASH" "$HERE/install.sh" --restart-relay || echo "!! The relay could not be started again: run ./install.sh --restart-relay"
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
    echo "The engine step did not finish; rolling it back."
    engine_recover || true
  fi
  if [ "$TXN_ACTIVE" = 1 ]; then
    local stage was
    stage="$(state txn-get stage 2>/dev/null || true)"
    was="$(state txn-get relay_was_loaded 2>/dev/null || true)"
    case "$stage" in
      routine_paused|cloud_done|prompt_done) echo "!! [GROK-UI] The routine is still paused: resume it in Grok Bot." ;;
    esac
    if [ "$was" = 1 ] && ! relay_loaded; then
      if launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null; then
        echo "The installed relay is running again."
      else
        echo "!! The relay could not be started again: run ./install.sh --restart-relay"
      fi
    fi
    echo "The update stopped at '$stage'. Run ./update.sh again in a terminal to continue from there."
  fi
  [ "$ACTING" = 0 ] || summary
  release_lock
  exit "$rc"
}

pull_checkout() {
  git -C "$HERE" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || die "this directory is not a git clone; get the new version yourself, then run ./update.sh --no-pull"
  git -C "$HERE" rev-parse --abbrev-ref '@{u}' >/dev/null 2>&1 \
    || die "this branch has no upstream; pull it yourself, then run ./update.sh --no-pull"
  [ -z "$(git -C "$HERE" status --porcelain --untracked-files=no)" ] \
    || die "tracked files have uncommitted changes; commit or stash them, or run ./update.sh --no-pull"
  local old new
  old="$(git -C "$HERE" rev-parse HEAD)"
  git -C "$HERE" pull --ff-only || die "git pull --ff-only failed; nothing was changed"
  new="$(git -C "$HERE" rev-parse HEAD)"
  if [ "$old" = "$new" ]; then echo "checkout is already at the latest commit"; return 1; fi
  echo "pulled ${old:0:12}..${new:0:12}"
}

checkout_label() { git -C "$HERE" rev-parse --short HEAD 2>/dev/null || echo "not a git checkout"; }

snapshot_ok() {
  git -C "$HERE" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
    || { echo "update: the cloud scripts changed, but this directory is not a git clone the cloud can pull from" >&2; return 1; }
  [ -z "$(git -C "$HERE" status --porcelain -- box)" ] \
    || { echo "update: box/ has uncommitted changes the cloud cannot pull; commit or revert them first" >&2; return 1; }
  local up
  up="$(git -C "$HERE" rev-parse '@{u}' 2>/dev/null)" \
    || { echo "update: this branch has no upstream, so the cloud cannot pull the same commit" >&2; return 1; }
  [ "$up" = "$(git -C "$HERE" rev-parse HEAD)" ] \
    || { echo "update: this checkout is not at its upstream commit; the cloud would get a different version" >&2; return 1; }
}

read_status() {
  local out k v
  out="$(state status --lang "$DOC_LANG" --python "$PYTHON")" || die "could not read the deployment state"
  while read -r k v; do
    case "$k" in
      relay) S_RELAY="$v" ;; dsh) S_DSH="$v" ;; cloud) S_CLOUD="$v" ;;
      cloud_setup) S_SETUP="$v" ;; prompt) S_PROMPT="$v" ;; txn) S_TXN="$v" ;; name) S_NAME="$v" ;;
      engine) read -r S_ENGINE S_EVER S_EPIN <<< "$v" ;; etxn) S_ETXN="$v" ;; cloud_engine) S_CENGINE="$v" ;;
    esac
  done <<< "$out"
  [ "$S_ENGINE" != refused ] \
    || die "Reason Network $S_EVER is not the version this release supports ($S_EPIN) and is never downgraded or replaced; review that installation first"
  case "$S_NAME" in
    "changed "*)
      local recorded current
      read -r _ recorded current <<< "$S_NAME"
      die "the Bot address name in ~/.config/agent-relay/env ($current) is not the one this Mac installed with ($recorded); renaming is not supported. Put the old name back, or uninstall with identity removal and install again" ;;
  esac
}

plan_line() {
  local what="$3"
  [ "$2" != unchanged ] || what="-"
  printf '  %-15s %-10s %s\n' "$1" "$2" "$what"
}

print_plan() {
  echo "== plan (checkout $(checkout_label), prompt language $DOC_LANG)"
  [ "$S_TXN" = - ] || echo "  unfinished update stopped at '$S_TXN': it is continued first (terminal)"
  local name_state name_value
  read -r name_state name_value <<< "$S_NAME"
  if [ "$name_state" = ok ]; then plan_line "address name" ok "$name_value"
  else plan_line "address name" unknown "$name_value (recorded with the next step this script records)"; fi
  plan_line "relay" "$S_RELAY" "install this checkout's relay and restart it"
  [ "$S_ETXN" = - ] || echo "  an engine upgrade stopped at '$S_ETXN': it is rolled back first (binary only), then offered again"
  case "$S_ENGINE" in
    older) plan_line "engine" changed "upgrade Reason Network $S_EVER to $S_EPIN: relay first, backup, switch, verify, rollback on failure (asks in a terminal)" ;;
    stale) plan_line "engine" changed "restart the daemon: it still runs the binary from before the last replacement (asks in a terminal)" ;;
    *) plan_line "engine" unchanged "-" ;;
  esac
  if [ "$S_DSH" = absent ]; then plan_line "dsh plugin" absent "- (not installed)"
  else plan_line "dsh plugin" "$S_DSH" "replace the plugin file; bridge settings stay"; fi
  case "$S_CLOUD" in
    pending) plan_line "cloud scripts" pending "asks whether Grok Bot finished the setup the installer printed" ;;
    *) plan_line "cloud scripts" "$S_CLOUD" "Grok Bot pulls this commit on [BOT-CLOUD]; you paste its reply to compare (terminal)" ;;
  esac
  case "$S_SETUP" in
    pending) plan_line "cloud setup" pending "asks whether Grok Bot finished the setup the installer printed" ;;
    *) plan_line "cloud setup" "$S_SETUP" "asks whether the CHANGELOG migration for box/rn-cloud-setup.sh is done" ;;
  esac
  case "$S_CENGINE" in
    unchanged) plan_line "cloud engine" unchanged "-" ;;
    pending) plan_line "cloud engine" pending "asks whether Grok Bot finished the setup the installer printed" ;;
    *) plan_line "cloud engine" "$S_CENGINE" "Grok Bot runs box/rn-cloud-setup.sh --upgrade-engine on [BOT-CLOUD]; you paste its reply to compare (terminal)" ;;
  esac
  case "$S_PROMPT" in
    pending) plan_line "routine prompt" pending "asks whether the prompt the installer printed is saved" ;;
    *) plan_line "routine prompt" "$S_PROMPT" "you replace the routine's prompt in [GROK-UI] (terminal)" ;;
  esac
  if [ "$S_ENGINE" != unchanged ]; then
    echo "  engine order: switch the relay on the current engine, stop it, switch the engine, start the relay"
  fi
  if [ "$S_CLOUD" != unchanged ] || [ "$S_PROMPT" != unchanged ] || [ "$S_CENGINE" != unchanged ]; then
    echo "  order: stop relay, pause routine, update cloud and/or prompt, resume routine, start relay"
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

cloud_step() {   # $1 = 1 when the cloud engine is checked or upgraded in the same message
  SHOWN="$(state digest cloud)"
  SHOWN_SETUP="$(state digest cloud_setup)"
  SHOWN_ENGINE="$(state digest cloud_engine)"
  local commit branch files="box/rn-cloud.sh box/rn-fetch.py" n=4 eflag=""
  commit="$(git -C "$HERE" rev-parse HEAD)"
  branch="$(git -C "$HERE" rev-parse --abbrev-ref '@{u}')"
  branch="${branch#*/}"
  [ "$1" != 1 ] || { files="$files box/rn-cloud-setup.sh box/rn-engine.py"; eflag=--engine; }
  echo
  echo "[GROK-UI] Send this to Grok Bot directly in the chat (not through the routine):"
  echo "----------------------------------------------------------------"
  echo "On [BOT-CLOUD], update the agent-mesh clone at /workspace/agent-mesh:"
  echo "1. Run git -C /workspace/agent-mesh status --porcelain and git -C /workspace/agent-mesh remote get-url origin. If there are uncommitted changes, or it is not the public agent-mesh repository cloned during setup, stop and tell me; do not overwrite anything."
  echo "2. If git -C /workspace/agent-mesh symbolic-ref -q HEAD prints nothing (a detached HEAD), run git -C /workspace/agent-mesh checkout $branch first. If that fails, stop and tell me; do not reset, clean or stash."
  echo "3. Run git -C /workspace/agent-mesh pull --ff-only. If it fails, stop and tell me."
  if [ "$1" = 1 ]; then
    echo "4. Run bash /workspace/agent-mesh/box/rn-cloud-setup.sh --upgrade-engine. It checks or upgrades the Reason Network engine (it keeps a backup and rolls back on failure). If it fails, stop and send me its full output; do not retry or repair anything."
    n=5
  fi
  echo "$n. Reply with the output of: git -C /workspace/agent-mesh rev-parse HEAD; cd /workspace/agent-mesh && sha256sum $files"
  [ "$1" != 1 ] || echo "   and every line of step 4's output that starts with engine:"
  echo "----------------------------------------------------------------"
  echo "Grok Bot's reply must show exactly:"
  echo "  $commit"
  (cd "$HERE" && shasum -a 256 $files) | sed 's/^/  /'
  [ "$1" != 1 ] || echo "  engine: reason-network $S_EPIN running (daemon pid <n>, started <time>, binary replaced <time>)"
  echo
  echo "Paste Grok Bot's reply here, then type a line with only a dot (.) and press Enter:" >&2
  read_reply
  if ! printf '%s' "$REPLY_TEXT" | state cloud-reply --commit "$commit" ${eflag:+"$eflag"}; then
    die "the cloud side was not recorded as updated. Sort it out with Grok Bot (the routine stays paused), then run ./update.sh again to paste the new reply"
  fi
}

prompt_step() {
  SHOWN="$(state digest prompt --lang "$DOC_LANG")"
  echo
  echo "[GROK-UI] Replace the prompt saved in the routine with this one ($DOC_LANG):"
  echo "----------------------------------------------------------------"
  state show-prompt --lang "$DOC_LANG"
  echo
  echo "----------------------------------------------------------------"
  ask "Is the new prompt saved in the routine?" || die "the routine prompt was not recorded as updated"
}

guided() {
  local stage paused=0 was
  NEED=""
  [ "$S_CLOUD" = unchanged ] || NEED="cloud"
  [ "$S_PROMPT" = unchanged ] || NEED="${NEED:+$NEED,}prompt"
  [ "$DO_CENGINE" != 1 ] || NEED="${NEED:+$NEED,}cloud_engine"
  if need_has cloud || need_has cloud_engine; then snapshot_ok || die "nothing was changed"; fi
  if [ "$S_TXN" = - ]; then
    if relay_loaded; then was=1; else was=0; fi
    TXN_ACTIVE=1
    state txn-start --lang "$DOC_LANG" --need "$NEED" --was "$was" --commit "$(git -C "$HERE" rev-parse HEAD 2>/dev/null || echo none)"
    stage=stopping
  else
    TXN_ACTIVE=1
    stage="$S_TXN"
    echo "== continuing the update that stopped at '$stage'"
    state txn-set need "$NEED"
  fi

  echo "== 1. stop the relay (mail waits in the local engine meanwhile)"
  if relay_loaded; then launchctl bootout "$(service)" 2>/dev/null || true; fi
  ! relay_loaded || die "could not stop the relay service"
  if [ "$stage" = stopping ]; then state txn-set stage relay_stopped; stage=relay_stopped; fi
  case "$stage" in routine_paused|cloud_done|prompt_done) paused=1 ;; esac

  if [ -n "$NEED" ]; then
    if [ "$paused" = 1 ]; then
      ask "== 2. [GROK-UI] Is the routine still paused, with no run in progress?" || die "stopped"
    else
      ask "== 2. [GROK-UI] Pause the routine in Grok Bot and wait until no run is in progress. Done?" || die "stopped"
      state txn-set stage routine_paused
    fi
    if need_has cloud || need_has cloud_engine; then
      if need_has cloud_engine; then
        echo "== 3. update the cloud scripts and the cloud engine"
        cloud_step 1
        state mark cloud_engine confirmed --expect "$SHOWN_ENGINE"
        state mark cloud_setup confirmed --expect "$SHOWN_SETUP"
        R_CENGINE="Reason Network $S_EPIN confirmed"
        [ "$S_SETUP" = unchanged ] || R_SETUP="confirmed"
      else
        echo "== 3. update the cloud scripts"
        cloud_step 0
      fi
      state mark cloud confirmed --expect "$SHOWN"
      state txn-set stage cloud_done
      R_CLOUD="updated"
    fi
    if need_has prompt; then
      echo "== 4. replace the routine prompt"
      prompt_step
      state mark prompt confirmed --lang "$DOC_LANG" --expect "$SHOWN"
      state txn-set stage prompt_done
      R_PROMPT="updated"
    fi
    ask "== 5. [GROK-UI] Resume the routine in Grok Bot. Done?" || die "stopped"
    state txn-set stage routine_resumed
  elif [ "$paused" = 1 ]; then
    ask "== 5. [GROK-UI] Resume the routine in Grok Bot. Done?" || die "stopped"
    state txn-set stage routine_resumed
  fi

  echo "== 6. start the relay"
  if ! "$BASH" "$HERE/install.sh" --restart-relay; then
    R_RELAY="failed; see the messages above"
    die "the relay did not start; fix the problem shown above, then run ./update.sh again"
  fi
  if [ "$S_RELAY" = unchanged ]; then R_RELAY="restarted"; else R_RELAY="updated"; fi
  state txn-clear
  TXN_ACTIVE=0
}

engine_step() {
  local tmp rc=0 was backup base n others pid
  echo
  echo "== engine 1. switch the relay to this checkout's version on the current engine ($S_EVER)"
  if ! "$BASH" "$HERE/install.sh" --restart-relay; then
    R_RELAY="failed; see the messages above"
    die "the relay did not switch, so the engine was not changed; fix the problem above, then run ./update.sh again"
  fi
  R_RELAY="updated"
  S_RELAY=unchanged
  pid="$(relay_pid)"
  others="$(engine others --bin "$RN" ${pid:+--skip-parent "$pid"})" || others=""
  if [ -n "$others" ]; then
    echo "These processes also use Reason Network. They reconnect to the restarted daemon by themselves; restart those agent sessions after the update:"
    printf '%s\n' "$others" | sed 's/^/  /'
  fi
  if [ "$S_ENGINE" = stale ]; then
    ask "== engine 2. Reason Network $S_EVER is installed, but its daemon started before the binary was replaced. Restart the daemon now?" \
      || die "stopped; the engine was not restarted"
    if relay_loaded; then ENGINE_WAS=1; else ENGINE_WAS=0; fi
    ENGINE_TXN=1
    launchctl bootout "$(service)" 2>/dev/null || true
    if ! engine restart --home "$RN_HOME" --bin "$RN"; then
      ENGINE_TXN=0
      "$BASH" "$HERE/install.sh" --restart-relay || true
      R_ENGINE="restart failed; see the messages above"
      die "the daemon did not come back on the new binary; run reason-network stop, then ./check.sh"
    fi
    "$BASH" "$HERE/install.sh" --restart-relay || { ENGINE_TXN=0; R_ENGINE="daemon restarted"; die "the relay did not start on the restarted engine; see the messages above"; }
    ENGINE_TXN=0
    R_ENGINE="daemon restarted on $S_EVER"
    return 0
  fi
  echo "== engine 2. upgrade Reason Network $S_EVER to $S_EPIN"
  echo "It downloads $S_EPIN and checks its pinned sha256, stops the relay, backs up the binary and ~/.reason/network,"
  echo "replaces the binary, restarts the daemon and checks the new one runs, then starts the relay. On failure only"
  echo "the old binary is put back; mail received meanwhile stays."
  ask "Upgrade Reason Network now?" || die "stopped; the engine was not changed"
  tmp="$(mktemp -d "$LIB_DIR/.engine-new.XXXXXX")" || die "cannot create a temporary directory in $LIB_DIR"
  ENGINE_TMP="$tmp"
  if ! "$BASH" "$HERE/install.sh" --fetch-engine "$tmp" >/dev/null; then
    R_ENGINE="not changed (download failed)"
    die "the new engine could not be downloaded or verified; the engine was not changed"
  fi
  if relay_loaded; then was=1; else was=0; fi
  ENGINE_WAS="$was"
  ENGINE_TXN=1
  launchctl bootout "$(service)" 2>/dev/null || true
  if relay_loaded; then
    ENGINE_TXN=0
    die "could not stop the relay service; the engine was not changed"
  fi
  backup="$LIB_DIR/engine-backup-$(date -u +%Y%m%dT%H%M%SZ)"
  base="$backup"; n=1
  while [ -e "$backup" ]; do n=$((n + 1)); backup="$base-$n"; done
  engine switch --home "$RN_HOME" --bin "$RN" --new "$tmp/reason-network" --pinned "$S_EPIN" \
    --backup "$backup" --txn "$ETXN" --keep "relay_was_loaded=$was" || rc=$?
  rm -rf "$tmp"; ENGINE_TMP=""
  if [ "$rc" != 0 ]; then
    R_ENGINE="failed; see the messages above"
    engine_recover || { ENGINE_TXN=0; die "the engine upgrade failed and was not fully undone; see the steps above"; }
    R_ENGINE="failed and rolled back to $S_EVER (backup: $backup)"
    die "the engine upgrade failed; Reason Network $S_EVER is back"
  fi
  echo "== engine 3. start the relay on Reason Network $S_EPIN"
  if ! "$BASH" "$HERE/install.sh" --restart-relay; then
    R_ENGINE="failed; see the messages above"
    engine_recover || { ENGINE_TXN=0; die "the relay did not register on the new engine and the engine was not fully rolled back; see the steps above"; }
    R_ENGINE="relay failed on $S_EPIN; rolled back to $S_EVER (backup: $backup)"
    die "the relay did not register on the new engine; Reason Network $S_EVER is back"
  fi
  rm -f "$ETXN"
  ENGINE_TXN=0
  state set-engine "$S_EPIN" || echo "note: could not record the engine version in $LIB_DIR/deployed.json"
  R_ENGINE="upgraded $S_EVER -> $S_EPIN (backup: $backup; remove it once ./check.sh passes)"
}

update_dsh() {
  if "$BASH" "$HERE/dsh/mac-install.sh" --plugin-only; then
    R_DSH="updated (quit DeepSeek Harness with ⌘Q and reopen it)"
  else
    R_DSH="failed; the previous plugin is kept"
    FAILED=1
  fi
}

main() {
  HERE="$(cd "$(dirname "$0")" && pwd)"
  LIB_DIR="$HOME/.local/lib/agent-mesh"; LOCK="$LIB_DIR/update.lock"
  LABEL="com.local.agent-relay"; PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  RN="$HOME/.local/bin/reason-network"; RN_HOME="$HOME/.reason/network"; ETXN="$LIB_DIR/engine-upgrade.json"
  LANG_ARG=""; YES=0; PULL=1; PLAN=0
  FAILED=0; NEED=""; SHOWN=""; SHOWN_SETUP=""; SHOWN_ENGINE=""; S_NAME=""; REPLY_TEXT=""; DO_CENGINE=0
  S_ENGINE=unchanged; S_EVER=""; S_EPIN=""; S_ETXN=-; S_CENGINE=unchanged
  R_RELAY="unchanged"; R_DSH="unchanged"; R_CLOUD="unchanged"; R_SETUP="unchanged"; R_PROMPT="unchanged"
  R_ENGINE="unchanged"; R_CENGINE="unchanged"
  parse_args "$@"

  "$BASH" "$HERE/install.sh" --preflight || die "fix the problem above, then run ./update.sh again; nothing was changed"
  PYTHON="$(python3 -c 'import sys; print(sys.executable)')"
  take_lock

  if [ "$PULL" = 1 ] && pull_checkout; then
    release_lock
    trap - EXIT INT TERM
    exec "$BASH" "$HERE/update.sh" --no-pull "$@"
  fi

  DOC_LANG="$LANG_ARG"
  [ -n "$DOC_LANG" ] || DOC_LANG="$(state lang)"
  [ -n "$DOC_LANG" ] || DOC_LANG="${AGENT_MESH_LANG:-en}"
  case "$DOC_LANG" in en|zh-CN) ;; *) die "unsupported AGENT_MESH_LANG '$DOC_LANG'; use --lang en or --lang zh-CN" ;; esac

  read_status
  local interactive=0
  if [ "$S_TXN" != - ] || [ "$S_SETUP" != unchanged ] || [ "$S_CLOUD" != unchanged ] || [ "$S_PROMPT" != unchanged ] \
      || [ "$S_ENGINE" != unchanged ] || [ "$S_ETXN" != - ] || [ "$S_CENGINE" != unchanged ]; then
    interactive=1
  fi
  print_plan
  [ "$PLAN" = 0 ] || exit 0
  if [ "$interactive" = 1 ] && [ ! -t 0 ]; then
    echo "This needs answers in a terminal and this is not one: nothing was changed."
    echo "[LOCAL] User: run   ./update.sh --lang $DOC_LANG   in this repository in Terminal."
    exit 3
  fi
  if [ "$interactive" = 0 ] && [ "$S_RELAY" = unchanged ] && [ "$S_DSH" != changed ]; then
    echo "already up to date"
    exit 0
  fi
  ACTING=1

  local changed=0
  if [ "$S_ETXN" != - ]; then
    echo "== an engine upgrade did not finish ('$S_ETXN'); rolling it back first"
    ENGINE_TXN=1
    engine_recover || die "the unfinished engine upgrade could not be rolled back; see the steps above"
    changed=1
    read_status
  fi
  if [ "$S_TXN" != - ]; then
    case "$(state txn-get need)" in *cloud_engine*) [ "$S_CENGINE" = unchanged ] || DO_CENGINE=1 ;; esac
    guided
    changed=1
    DO_CENGINE=0
    read_status
  fi

  if [ "$S_SETUP" = changed ] && [ "$S_CENGINE" != unchanged ] && [ "$S_CENGINE" != pending ]; then
    echo "box/rn-cloud-setup.sh changed; the cloud engine step below runs it and records it."
  elif [ "$S_SETUP" != unchanged ]; then
    if [ "$S_SETUP" = pending ]; then
      ask "Did Grok Bot finish the cloud setup the installer printed (it reported that the cloud joined)?" \
        || die "finish the cloud setup first (./install.sh --cloud-prompt), then run ./update.sh again; nothing was changed"
      if [ "$S_CLOUD" = pending ]; then state mark cloud confirmed --if-pending; S_CLOUD=unchanged; R_CLOUD="confirmed"; fi
      if [ "$S_CENGINE" = pending ]; then
        state mark cloud_engine confirmed --if-pending; S_CENGINE=unchanged; R_CENGINE="confirmed"
      fi
      state mark cloud_setup confirmed --if-pending
    else
      echo "box/rn-cloud-setup.sh differs from the version this Mac last recorded, or nothing was recorded."
      echo "If CHANGELOG.md lists a cloud setup migration for the versions since your last update, do it first."
      SHOWN="$(state digest cloud_setup)"
      ask "Is the cloud side set up for this version?" || die "do the cloud setup migration first; nothing was changed"
      state mark cloud_setup confirmed --expect "$SHOWN"
    fi
    S_SETUP=unchanged; R_SETUP="confirmed"
  fi
  if [ "$S_CLOUD" = pending ]; then
    if ask "Did Grok Bot finish the cloud setup the installer printed?"; then
      state mark cloud confirmed --if-pending; S_CLOUD=unchanged; R_CLOUD="confirmed"
    else
      S_CLOUD=changed
    fi
  fi
  if [ "$S_CENGINE" = pending ]; then
    if ask "Did Grok Bot finish the cloud setup the installer printed (Reason Network $S_EPIN on [BOT-CLOUD])?"; then
      state mark cloud_engine confirmed --if-pending; S_CENGINE=unchanged; R_CENGINE="confirmed"
    else
      S_CENGINE=changed
    fi
  fi
  if [ "$S_CENGINE" != unchanged ]; then
    echo
    echo "The engine on [BOT-CLOUD] has not been confirmed at Reason Network $S_EPIN (Grok Bot checks or upgrades it with"
    echo "box/rn-cloud-setup.sh --upgrade-engine while the routine is paused)."
    if ask "Do the cloud engine step in this run?"; then
      DO_CENGINE=1
    else
      R_CENGINE="not done; run ./update.sh again to do it"
      FAILED=1
    fi
  fi
  if [ "$S_ENGINE" != unchanged ]; then
    engine_step
    changed=1
  fi
  if [ "$S_PROMPT" = pending ]; then
    if ask "Is the routine prompt the installer printed ($DOC_LANG) saved in the routine?"; then
      state mark prompt confirmed --lang "$DOC_LANG" --if-pending; S_PROMPT=unchanged; R_PROMPT="confirmed"
    else
      S_PROMPT=changed
    fi
  fi

  if [ "$S_CLOUD" != unchanged ] || [ "$S_PROMPT" != unchanged ] || [ "$DO_CENGINE" = 1 ]; then
    guided
    changed=1
  elif [ "$S_RELAY" = changed ] || [ "$S_DSH" = changed ]; then
    if [ "$YES" != 1 ] && [ -t 0 ]; then
      ask "Update the parts marked changed now?" || die "stopped; nothing was changed"
    fi
    if [ "$S_RELAY" = changed ]; then
      if "$BASH" "$HERE/install.sh" --restart-relay; then R_RELAY="updated"; else R_RELAY="failed; see the messages above"; FAILED=1; fi
    fi
    changed=1
  fi
  if [ "$S_DSH" = changed ]; then update_dsh; changed=1; fi

  if [ "$changed" = 1 ]; then
    echo
    echo "== check"
    "$BASH" "$HERE/check.sh" || { echo "check.sh reported FAIL lines; the update steps above are kept"; FAILED=1; }
  fi
  [ "$FAILED" = 0 ] || exit 1
}

LOCKED=0; TXN_ACTIVE=0; ACTING=0; ENGINE_TXN=0; ENGINE_WAS=1; ENGINE_TMP=""
trap on_exit EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
main "$@"
