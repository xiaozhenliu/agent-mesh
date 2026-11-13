#!/usr/bin/env bash
# agent-mesh: install the Mac side in one run.
#   ./install.sh --name <team-name> --relay-name <address> --grok-name <name> [--machine mac] [--yes] [--lang en|zh-CN]
#   ./install.sh --grok-name <name>  record or change the Bot's name in Grok (a label; the address stays)
#   ./install.sh --webhook        enter (or change) the routine's Webhook URL and key, then (re)start the relay
#   ./install.sh --cloud-prompt   print the invite and the text to send to Grok Bot again
#   ./install.sh --rotate-key     replace the signing key, restart the relay, print how to copy it to the cloud
#   ./install.sh --purge-copies   delete the relay's kept copies of forwarded messages (re-forward then says no-copy)
#   --repo-url https://...       public distribution URL accessible from the Bot's cloud computer
#   ./install.sh --version      print agent-mesh and pinned Reason Network versions; no installation
#   ./install.sh --restart-relay  (used by update.sh) install this checkout's relay and restart it; rolls back if it
#                                 does not come up or does not register under its name
#   ./install.sh --preflight      (used by update.sh) check this Mac and the existing installation; changes nothing
#   ./install.sh --fetch-engine DIR  (used by update.sh) download the pinned Reason Network for this Mac, verify its
#                                 sha256 and version, and unpack it into DIR; changes nothing else
# --lang selects prompt language in every mode. Default: AGENT_MESH_LANG, otherwise en. Diagnostics stay English.
# The team name is also your name inside the team: 1-40 chars, lowercase letters and digits joined by single '-',
# not your real name. It is fixed once the team exists.
# --relay-name picks the Bot's address <team-name>/<name> (the cloud uses <name>-cloud and <name>-inbox): 1-34
# chars, the same character rules, not terminal. Use the Bot's own name so agents can tell Bots apart. A new
# installation needs it; an existing one keeps its name (including bot). It cannot be changed once chosen.
# --grok-name is the Bot's name as Grok shows it, in any language; it is stored as given and not used for routing.
# check.sh and the reply convention show <team-name>/<address> → Grok: <name>.
# What it does: installs Reason Network, creates your private team, wires your local agents (after showing the
# plan), generates the signing key, asks for the routine's webhook, installs the relay as a login service, then
# prints what to send to Grok Bot. Re-running is safe.
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

NAME=""; MACHINE="mac"; YES=0; MODE=full; DOC_LANG="${AGENT_MESH_LANG:-en}"; RELAY_NAME_ARG=""; GROK_NAME=""; GROK_SET=0
REPO_URL="https://github.com/xiaozhenliu/agent-mesh.git"
while [ $# -gt 0 ]; do
  case "$1" in
    --name) NAME="${2:?}"; shift 2 ;;
    --machine) MACHINE="${2:?}"; shift 2 ;;
    --relay-name) RELAY_NAME_ARG="${2:?Bot address name}"; shift 2 ;;
    --grok-name) [ $# -ge 2 ] || { echo "install: --grok-name needs the Bot's name in Grok" >&2; exit 2; }
      GROK_NAME="$2"; GROK_SET=1; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    --webhook) MODE=webhook; shift ;;
    --cloud-prompt) MODE=cloud; shift ;;
    --rotate-key) MODE=rotate; shift ;;
    --purge-copies) MODE=purge; shift ;;
    --restart-relay) MODE=restart; shift ;;
    --preflight) MODE=preflight; shift ;;
    --fetch-engine) MODE=fetch; FETCH_DIR="${2:?directory}"; shift 2 ;;
    --repo-url) REPO_URL="${2:?public HTTPS repository URL}"; shift 2 ;;
    --version) echo "agent-mesh $PROJECT_VERSION (Reason Network ${VERSION#v})"; exit 0 ;;
    --lang) [ $# -ge 2 ] || { echo "install: --lang needs en or zh-CN" >&2; exit 2; }; DOC_LANG="$2"; shift 2 ;;
    -h|--help) sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1"; sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
  esac
done
case "$DOC_LANG" in
  en|zh-CN) ;;
  *) echo "install: unsupported language '$DOC_LANG'; choose en or zh-CN" >&2; exit 2 ;;
esac
if [ "$GROK_SET" = 1 ]; then
  case "$MODE" in
    full) [ -n "$NAME" ] || MODE=label ;;
    webhook|cloud) ;;
    *) echo "install: --grok-name works with a full install, --webhook or --cloud-prompt, or on its own" >&2; exit 2 ;;
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
    *) die "unsupported CPU $(uname -m)" ;;
  esac
  want="$(pinned_sha256 "$VERSION/$target")" || die "no pinned sha256 for Reason Network $VERSION on $target"
  curl -fsSL "https://github.com/reason-machines/reason-network/releases/download/$VERSION/reason-network-$target.tar.gz" \
    -o "$1/e.tgz" || die "could not download Reason Network $VERSION; nothing was installed"
  got="$(shasum -a 256 "$1/e.tgz" | cut -d' ' -f1)"
  [ "$got" = "$want" ] || die "checksum mismatch for reason-network-$target.tar.gz (expected $want, got $got); nothing was installed. Run again to download it again; if it keeps failing, do not install it"
  tar xzf "$1/e.tgz" -C "$1" reason-network || die "the downloaded archive has no reason-network binary; nothing was installed"
  rm -f "$1/e.tgz"
  chmod 755 "$1/reason-network"
  [ "$("$1/reason-network" --version 2>&1 | awk '{print $2}')" = "${VERSION#v}" ] \
    || die "the downloaded binary does not report Reason Network ${VERSION#v}; nothing was installed"
}

rename_refused() {
  die "this installation uses the Bot address name '$1'. Renaming is not supported, also not for an unfinished
installation: queued mail, the routine prompt and the cloud setup all use the current name. Run again without
--relay-name to keep '$1', or remove the installation including its identity (README: Uninstall) and install again."
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
  tmp="$(mktemp "$CONF_DIR/.env.XXXXXX")" || die "cannot create a temporary file in $CONF_DIR; $ENV_FILE is unchanged"
  if ! { env_text "$1" "$2" > "$tmp" && { [ -z "$3" ] || printf '%s\n' "$3" >> "$tmp"; } \
      && chmod 600 "$tmp" && mv -f "$tmp" "$ENV_FILE"; }; then
    rm -f "$tmp"
    die "could not write $ENV_FILE; it is unchanged"
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
    NONCE_KEEP_SECONDS)" || die "cannot read $ENV_FILE; nothing changed"
  put_env "$1" "$2" "$kept"
}

bot_label() {
  deploy_state label --person "$(current_person)" --relay-name "$RELAY_NAME" 2>/dev/null \
    || echo "$(current_person)/$RELAY_NAME"
}

reply_templates() {
  "$PYTHON" "$HERE/scripts/reply_convention.py" install \
    || echo "note: could not write the reply convention with this Bot's address to $LIB_DIR/templates"
}

new_install_stop() {
  local hint="" suggested=""
  if [ "$GROK_SET" = 1 ]; then
    if suggested="$(relay_names suggest "$GROK_NAME")"; then
      hint=" From --grok-name, the suggested address is: --relay-name $suggested"
    else
      hint=" The Grok name has no Latin letters or digits to build an address from: give this Bot a letter address yourself (for example its name in pinyin or English)."
    fi
  fi
  die "a new installation needs the Bot's address name. What is your Grok Bot called? Run again with
--relay-name <address> --grok-name '<its name in Grok>'. The address is lowercase letters and digits joined by
single '-', at most 34 characters (for example nova for Nova); it is how your agents find this Bot and cannot be
changed later.$hint"
}

record_pending() {
  deploy_state mark "$1" pending --lang "$DOC_LANG" --relay-name "$RELAY_NAME" \
    || echo "note: could not update $LIB_DIR/deployed.json; ./update.sh will ask about this step"
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
  launchctl bootstrap "gui/$(id -u)" "$PLIST" || { echo "launchctl bootstrap failed"; return 1; }
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    if log_since | grep "agent-relay up" >/dev/null; then
      sleep 3
      relay_running && return 0
      echo "the relay reported 'agent-relay up' but is no longer running"
      return 1
    fi
  done
  echo "relay did not report 'agent-relay up' within 10 s"
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
    || { echo "the saved copy in $ROLLBACK failed verification; nothing was restored, restore it by hand"; return 1; }
  launchctl bootout "$(service)" 2>/dev/null || true
  if relay_loaded; then echo "could not unload the relay service; the previous files stay in $ROLLBACK"; return 1; fi
  rm -f "$MARKER"
  restore_file agent_relay.py "$RELAY" && restore_file agent.plist "$PLIST" \
    || { echo "could not put the previous files back from $ROLLBACK"; return 1; }
  if [ "$(cat "$ROLLBACK/loaded")" = 1 ] && [ -f "$PLIST" ]; then
    bootstrap_and_wait || { echo "the previous relay did not come back up; see $LOG"; return 1; }
  fi
  rm -rf "$ROLLBACK"
}

relay_interrupted() {
  trap '' INT TERM HUP
  echo
  echo "interrupted; restoring the previous relay"
  restore_relay || echo "the previous relay was not fully restored; see the lines above"
  exit "$1"
}

start_relay() {
  mkdir -p "$LIB_DIR" "$HOME/Library/Logs" "$HOME/Library/LaunchAgents"
  if [ -d "$ROLLBACK" ]; then
    echo "an earlier relay switch did not finish; restoring the previous relay first"
    restore_relay || return 1
  fi
  if ! backup_relay; then
    rm -rf "$ROLLBACK.tmp" "$ROLLBACK"
    echo "could not save a copy of the current relay; nothing changed"
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
    echo "could not prepare the new relay; nothing changed"
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
    echo "relay up (log: $LOG)"
    return 0
  fi
  trap '' INT TERM HUP
  rm -f "$new_relay" "$new_plist"
  echo "Last log lines:"
  tail -n 10 "$LOG" "$HOME/Library/Logs/agent-relay.launchd.log" 2>/dev/null || true
  echo "restoring the previous relay"
  if restore_relay; then echo "previous relay restored"; else echo "the previous relay was not fully restored; see the lines above"; fi
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
        REG_ACTUAL="nothing (the relay service has no running process)"
      elif [ "$pid" = "$m_pid" ]; then
        rc=0; REG_ACTUAL="$(relay_names registered "$RELAY_NAME" 2>&1)" || rc=$?
        if [ "$rc" = 0 ] && [ "$REG_ACTUAL" = "$RELAY_NAME" ]; then
          echo "relay registered as $want"
          return 0
        fi
        if [ "$rc" != 0 ]; then REG_ACTUAL="nothing ($REG_ACTUAL)"
        elif [ -n "$REG_ACTUAL" ]; then REG_ACTUAL="$(current_person)/$REG_ACTUAL"; return 1; fi
      fi
    elif [ "$rc" != 3 ]; then
      REG_ACTUAL="nothing ($mark)"
    fi
    [ "$waited" -lt "${AGENT_MESH_REGISTER_WAIT:-45}" ] || break
    sleep 1; waited=$((waited + 1))
  done
  [ -n "$REG_ACTUAL" ] || REG_ACTUAL="nothing (the new relay reported no registration within ${AGENT_MESH_REGISTER_WAIT:-45} s)"
  return 1
}

check_registered() {
  registration && return 0
  die "the relay registered as $REG_ACTUAL instead of $(current_person)/$RELAY_NAME; another session in the team may use this name (reason-network who --all). The relay keeps running but gets no mail for $RELAY_NAME; see README: Troubleshooting"
}

verify_registration() {
  registration && return 0
  echo "the new relay registered as $REG_ACTUAL instead of $(current_person)/$RELAY_NAME; another session in the team may use this name (reason-network who --all)"
  return 1
}

webhook_step() {
  [ -n "$(current_person)" ] && [ -s "$KEY_FILE" ] \
    || die "team or signing key missing; run ./install.sh --name <team-name> first"
  mkdir -p "$LIB_DIR" "$HOME/Library/Logs" "$HOME/Library/LaunchAgents"
  seed_env
  reply_templates
  if [ "$MODE" = webhook ] || ! have_webhook; then
    record_pending prompt
    echo "[GROK-UI] User: create a webhook-triggered routine in Grok Bot and paste this prompt. The Bot executes it on [BOT-CLOUD], its cloud computer:"
    echo "----------------------------------------------------------------"
    deploy_state show-prompt --lang "$DOC_LANG" --relay-name "$RELAY_NAME"
    echo "----------------------------------------------------------------"
    echo "The routine panel then shows a Webhook URL and a key."
    if [ ! -t 0 ]; then
      echo "This is not a terminal, so the values cannot be entered here."
      echo "[LOCAL] User: run   ./install.sh --webhook --lang $DOC_LANG   in this repository on your Mac and paste them there (never into a chat)."
      exit 0
    fi
    read -r -p "Webhook URL: " url
    read -r -s -p "Webhook key (not echoed): " key; echo
    [[ "$url" == https://* ]] || die "the Webhook URL must start with https:// (the key travels in a request header)"
    [ -n "$key" ] || die "both values are required"
    write_env "$url" "$key"
  fi
  chmod 600 "$ENV_FILE"
  start_relay
  check_registered
}

cloud_prompt() {
  local person repo
  person="$(current_person)"; [ -n "$person" ] || die "not in a team yet; run the full install first"
  repo="$REPO_URL"
  local invite release tag="" commit="" sums="" k a b
  release="$("$PYTHON" "$HERE/scripts/deploy_state.py" release --repo-url "$repo" 2>&1)" \
    || die "$release. The cloud setup prompt was not printed; once this checkout matches the published release, run ./install.sh --cloud-prompt"
  while read -r k a b; do
    case "$k" in tag) tag="$a" ;; commit) commit="$a" ;; sum) sums="$sums$a  $b"$'\n' ;; esac
  done <<< "$release"
  invite="$("$RN" invite --me | sed -n 's/^For a cloud agent of yours.*send it: //p')"
  [ -n "$invite" ] || invite="$("$RN" invite --me)"
  record_pending cloud
  record_pending cloud_setup
  record_pending cloud_engine
  echo "[GROK-UI] User: send this to Grok Bot as one message. The Bot executes it on [BOT-CLOUD], its cloud computer. The first line is the invite, valid 24 hours while [LOCAL], your Mac, is online:"
  echo "----------------------------------------------------------------"
  echo "$invite"
  echo
  render_prompt cloud-setup --team-name "$person" --mac-home "$HOME" --repo-url "$repo" --relay-name "$RELAY_NAME" \
    --release-tag "$tag" --release-commit "$commit" --cloud-checksums "$sums"
  echo "----------------------------------------------------------------"
  echo "[GROK-UI] User: approve CopyToBox from [LOCAL] $KEY_FILE to [BOT-CLOUD] /workspace/agent-mesh/box/outbound.key. Afterwards the installing agent runs ./check.sh on [LOCAL]."
  echo "Bot address: $(bot_label) (cloud sessions $RELAY_NAME-cloud, $RELAY_NAME-inbox)"
}

grok_label() {
  echo "Bot address: $(bot_label)"
  [ -z "$(current_person)" ] || reply_templates
  echo "Copies of the reply convention in your repositories' AGENTS.md keep the old text until you run"
  echo "  python3 scripts/reply_convention.py refresh <repository>/AGENTS.md"
}

# The relay reads the key only at start, so a new key always means a restart. The old key is not kept: rotation is
# for a key that may have leaked, and the cloud must get the new one either way.
rotate_key() {
  [ -n "$(current_person)" ] && [ -s "$KEY_FILE" ] \
    || die "team or signing key missing; run ./install.sh --name <team-name> first"
  have_webhook || die "the relay is not configured yet; finish ./install.sh first (a first install creates a fresh key)"
  # Rotating $KEY_FILE only helps if that is the key the relay actually loads.
  local src
  src="$(relay_env OUTBOUND_KEY OUTBOUND_KEY_FILE)" || die "cannot read $ENV_FILE; the key is unchanged"
  if grep -Eq '^OUTBOUND_KEY=.' <<<"$src"; then
    die "$ENV_FILE sets OUTBOUND_KEY inline, which the relay prefers over $KEY_FILE; remove that line, then rotate again"
  fi
  [ "$(printf '%s\n' "$src" | sed -n 's/^OUTBOUND_KEY_FILE=//p')" = "$KEY_FILE" ] \
    || die "$ENV_FILE does not load the key from $KEY_FILE (OUTBOUND_KEY_FILE); fix that line, then rotate again"
  echo "This replaces $KEY_FILE and restarts the relay. Until [BOT-CLOUD] has the new key,"
  echo "the relay refuses the Bot's replies and the cloud's fetch reports bad signatures."
  if [ "$YES" != 1 ]; then
    if [ -t 0 ]; then
      read -r -p "Rotate the signing key now? [y/N] " a
      [[ "$a" == [yY]* ]] || die "stopped; the key is unchanged"
    else
      echo "This is not a terminal: nothing changed. Run again with --yes to rotate."
      exit 3
    fi
  fi
  local new="$KEY_FILE.new.$$"
  if ! ( umask 077; openssl rand -hex 32 > "$new" ) || [ "$(wc -c < "$new")" -lt 64 ]; then
    rm -f "$new"; die "could not generate a new key; the old key is unchanged"
  fi
  chmod 600 "$new"; mv -f "$new" "$KEY_FILE"
  echo "new signing key written to $KEY_FILE (mode 600)"
  mkdir -p "$LIB_DIR" "$HOME/Library/Logs" "$HOME/Library/LaunchAgents"
  local rc=0
  start_relay || rc=$?
  echo "----------------------------------------------------------------"
  echo "[GROK-UI] User: ask Grok Bot to copy the new key with CopyToBox, and approve the copy:"
  echo "  source [LOCAL] $KEY_FILE"
  echo "  destination [BOT-CLOUD] /workspace/agent-mesh/box/outbound.key (replace the old file), then chmod 600 it there."
  echo "  The key must not appear in the chat."
  echo "Then run ./check.sh on [LOCAL] and send a test message (README: Verify)."
  echo "Mail forwarded before now that [BOT-CLOUD] has not fetched keeps the old signature: fetch reports it as"
  echo "mismatch and it is not redelivered; ask the senders to resend (README: Replacing keys)."
  echo "If the Webhook URL or key may also have leaked: regenerate the key in [GROK-UI] (or recreate the routine),"
  echo "then run   ./install.sh --webhook --lang $DOC_LANG   on [LOCAL]."
  [ "$rc" = 0 ] || die "the relay did not restart; see the log lines above, then run ./install.sh --webhook"
}

# Copies exist only so the cloud can ask for a lost message again (RN-REFWD). The relay deletes expired ones
# (FWD_COPY_DAYS) only when later mail passes through, so an idle relay keeps them. Safe while the relay runs: a
# missing copy is answered with no-copy. The loop catches a copy renamed from .tmp to .json while we delete.
purge_copies() {
  local state="$HOME/Library/Application Support/agent-relay"
  local dir="$state/fwd-copies" files=() removed=0
  [ ! -L "$state" ] && [ ! -L "$dir" ] || die "refusing to delete through a symlink: $dir; nothing was removed"
  for _ in 1 2 3 4 5; do
    files=()
    if [ -d "$dir" ]; then
      shopt -s nullglob; files=("$dir"/*.json "$dir"/*.tmp); shopt -u nullglob
    fi
    [ "${#files[@]}" != 0 ] || break
    rm -f -- "${files[@]}"
    removed=$((removed + ${#files[@]}))
  done
  [ "${#files[@]}" = 0 ] || die "copies kept appearing while deleting (mail is arriving); run --purge-copies again"
  echo "removed $removed kept message copies from $dir"
  echo "Re-forward requests for those messages now answer no-copy; the sender has to send again."
  echo "Bodies also stay in the engine database (~/.reason/network/network.db) and on [BOT-CLOUD]; see README: Security and privacy."
}

# Validate the user's machine in every mode, before downloads or configuration changes.
[ "$MODE" != full ] || [ -n "$NAME" ] || die "need --name <team-name> (run with --help)"
[ "$(uname -s)" = Darwin ] || die "[LOCAL] this installer requires macOS (Apple Silicon or Intel)"
[ "$(id -u)" != 0 ] || die "run as the logged-in Mac user, without sudo"
case "$(uname -m)" in arm64|x86_64) ;; *) die "unsupported CPU $(uname -m)" ;; esac
[[ "$HOME" = /* ]] && [ -d "$HOME" ] && [ -w "$HOME" ] || die "HOME must be an absolute, writable user home directory"
for tool in python3 curl tar shasum openssl launchctl plutil; do
  command -v "$tool" >/dev/null || die "missing $tool; see README prerequisites before retrying"
done
PYTHON="$(python3 -c 'import sys; print(sys.executable)')" \
  || die "python3 cannot start; finish or repair its installation before retrying"
"$PYTHON" -c 'import sys, ssl, sqlite3, plistlib; sys.exit(sys.version_info < (3, 9))' \
  || die "Python 3.9+ with ssl and sqlite3 is required; install it and put python3 on PATH"
if [ "$MODE" = fetch ]; then
  [ -d "$FETCH_DIR" ] && "$PYTHON" -c 'import os, sys; sys.exit(bool(os.listdir(sys.argv[1])))' "$FETCH_DIR" \
    || die "--fetch-engine needs an existing empty directory"
  fetch_engine "$FETCH_DIR"
  echo "$FETCH_DIR/reason-network"
  exit 0
fi
launchctl print "gui/$(id -u)" >/dev/null 2>&1 \
  || die "no macOS GUI login session; sign in to the Mac desktop as this user first"
[ "${REASON_NETWORK_HOME:-$HOME/.reason/network}" = "$HOME/.reason/network" ] \
  || die "custom REASON_NETWORK_HOME is not supported; use the default user profile or stop to review your existing deployment"
export REASON_NETWORK_HOME="$HOME/.reason/network"
case "$REPO_URL" in https://*) ;; *) die "--repo-url must be a public HTTPS clone URL accessible without cloud credentials" ;; esac
if [ -n "$RELAY_NAME_ARG" ]; then
  why="$(relay_names check "$RELAY_NAME_ARG" 2>&1)" || die "--relay-name: $why"
fi
if [ "$GROK_SET" = 1 ]; then
  why="$(relay_names grok-check "$GROK_NAME" 2>&1)" || die "--grok-name: $why"
fi
CURRENT="$(relay_names env "$ENV_FILE" 2>&1)" \
  || die "$CURRENT. The installer manages only <name>, <name>-cloud, <name>-inbox; make LISTEN_NAME/CLOUD_NAME/INBOX_NAME match or remove all three, then run again"
[ -z "$RELAY_NAME_ARG" ] || [ -z "$CURRENT" ] || [ "$RELAY_NAME_ARG" = "$CURRENT" ] || rename_refused "$CURRENT"
RECORDED_NAME="$(deploy_state recorded-name 2>/dev/null)" || RECORDED_NAME=""
RELAY_NAME="${CURRENT:-${RELAY_NAME_ARG:-${RECORDED_NAME:-bot}}}"
recorded="$(deploy_state check-name --relay-name "$RELAY_NAME" 2>&1)" || die "$recorded"
case "$recorded" in
  "changed "*)
    recorded="${recorded#changed }"; recorded="${recorded%% *}"
    [ -n "$CURRENT" ] || die "this Mac was installed with the Bot address name '$recorded' (recorded in $LIB_DIR/deployed.json), not '$RELAY_NAME'. Renaming is not supported: run again with --relay-name $recorded or without --relay-name, or remove the installation including its identity (README: Uninstall) and install again."
    die "this Mac was installed with the Bot address name '$recorded', but $ENV_FILE now gives '$CURRENT'. Renaming is not supported: put LISTEN_NAME/CLOUD_NAME/INBOX_NAME back to '$recorded', or remove the installation including its identity (README: Uninstall) and install again." ;;
esac

# Never replace a user's different engine version or create a second PATH installation silently.
existing="$(command -v reason-network || true)"
[ -z "$existing" ] || [ "$existing" = "$RN" ] || die "reason-network already exists at $existing; review that installation before using $RN"
ENGINE_STATE=absent
if [ -x "$RN" ]; then
  installed_version="$("$RN" --version 2>/dev/null | awk '{print $2}')" || installed_version=""
  ENGINE_STATE="$(rn_engine compare "$installed_version" "${VERSION#v}")"
  case "$ENGINE_STATE" in
    same) ;;
    older)
      case "$MODE" in
        full) die "existing Reason Network is $installed_version; this release requires ${VERSION#v}. Run ./update.sh to upgrade it (it keeps a backup and rolls back on failure); it has not been overwritten" ;;
        preflight) echo "engine: Reason Network $installed_version is older than ${VERSION#v}; ./update.sh upgrades it" ;;
      esac ;;
    *) die "existing Reason Network is ${installed_version:-of an unknown version}; this release supports ${VERSION#v} and never downgrades or replaces a newer or unrecognised engine. Review that installation first; it has not been changed" ;;
  esac
fi

record_grok() {
  [ "$GROK_SET" = 1 ] || return 0
  [ -n "$CURRENT" ] || [ "$recorded" != unknown ] || die "no installation to label yet: install with ./install.sh --name <team-name> --relay-name <address> --grok-name '<its name in Grok>' first; nothing was recorded"
  why="$(deploy_state set-grok-name --relay-name "$RELAY_NAME" -- "$GROK_NAME" 2>&1)" || die "--grok-name: $why"
}

case "$MODE" in
  label) record_grok; grok_label; exit 0 ;;
  webhook)
    [ -x "$RN" ] || die "Reason Network is not installed; run the full install first"
    [ -n "$(current_person)" ] && [ -s "$KEY_FILE" ] || die "team or signing key missing; run ./install.sh --name <team-name> first"
    record_grok; webhook_step; exit 0 ;;
  cloud)
    [ -x "$RN" ] || die "Reason Network is not installed; run the full install first"
    [ -n "$(current_person)" ] || die "not in a team yet; run the full install first"
    if [ "$GROK_SET" = 1 ]; then record_grok; reply_templates; fi
    cloud_prompt; exit 0 ;;
  rotate) [ -x "$RN" ] || die "Reason Network is not installed; run the full install first"; rotate_key; exit 0 ;;
  purge) purge_copies; exit 0 ;;
  restart|preflight)
    [ -x "$RN" ] || die "Reason Network is not installed; run the full install first"
    [ -n "$(current_person)" ] && [ -s "$KEY_FILE" ] || die "team or signing key missing; run ./install.sh --name <team-name> first"
    have_webhook || die "the relay is not configured yet; run ./install.sh --webhook"
    if [ "$MODE" = preflight ]; then
      [ -f "$PLIST" ] || die "the relay login service is missing; run ./install.sh --webhook"
      exit 0
    fi
    chmod 600 "$ENV_FILE"
    start_relay verify || die "the relay was not switched; see the lines above"
    exit 0 ;;
esac

[ -n "$NAME" ] || die "need --name <team-name> (run with --help)"
valid_name "$NAME" && valid_name "$MACHINE" \
  || die "the name must be 1-40 chars of lowercase letters and digits, joined by single '-' (no '-' at the start or end, none repeated: Reason Network would merge them)"
if [ -z "$CURRENT" ] && [ -z "$RELAY_NAME_ARG" ] && [ "$recorded" = unknown ] && [ -z "$(current_person)" ]; then
  new_install_stop
fi
if [ "$GROK_SET" = 1 ]; then
  why="$(deploy_state set-grok-name --check --relay-name "$RELAY_NAME" -- "$GROK_NAME" 2>&1)" || die "--grok-name: $why"
fi

say "1/6 network"
if relays="$("$PYTHON" "$HERE/scripts/relay_targets.py" --rn "$RN" --home "$HOME/.reason/network" --pinned "${VERSION#v}")"; then
  for url in $(printf '%s\n' "$relays" | tail -n +2); do
    code="$(curl -sS -o /dev/null -m 10 -w '%{http_code}' "$url" 2>/dev/null || true)"
    [ "$code" = 200 ] || die "cannot reach the relay $url (HTTP '$code'; $(printf '%s\n' "$relays" | head -n 1)). Allow direct connections to the whole iroh.link and reasonmachines.com domains and exclude them from HTTPS decryption. Confirm the curl check prints 200, then run this again. See README.md: Troubleshooting (Chinese: README.zh-CN.md)."
  done
  echo "ok ($(printf '%s\n' "$relays" | head -n 1))"
else
  echo "relay not checked: ${relays:-relay_targets.py failed}. If the engine cannot connect, allow direct connections to the whole iroh.link and reasonmachines.com domains; see README.md: Troubleshooting (Chinese: README.zh-CN.md)."
fi

say "2/6 Reason Network $VERSION"
if [ -x "$RN" ] && [ "$("$RN" --version 2>/dev/null | awk '{print $2}')" = "${VERSION#v}" ]; then
  echo "already installed: $("$RN" --version)"
else
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  fetch_engine "$tmp"
  mkdir -p "$BIN_DIR"; install -m 755 "$tmp/reason-network" "$RN"
  echo "installed $("$RN" --version) at $RN"
fi
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "note: add $BIN_DIR to PATH using your shell startup file; see README prerequisites" ;; esac

say "3/6 team"
export REASON_NETWORK_MACHINE="$MACHINE"
if "$RN" team >/dev/null 2>&1; then
  have="$(current_person)"
  [ "$have" = "$NAME" ] || die "existing team person is '$have', requested '$NAME'; review the existing deployment before changing it. Nothing can be renamed in place"
  echo "already in a team"
else
  "$RN" stop >/dev/null 2>&1 || true
  "$RN" init "$NAME" --as "$NAME" --no-setup
fi
rn_engine config-off --path "$CFG" auto_update lan || die "could not write $CFG"
"$RN" stop >/dev/null 2>&1 || true
"$RN" team
if [ ! -f "$ENV_FILE" ]; then
  sessions="$("$RN" who --all --json)" \
    || die "could not list the team's sessions (reason-network who --all --json failed), so the Bot address name '$RELAY_NAME' was not checked; nothing else was changed, run again"
  taken="$(relay_names conflicts "$RELAY_NAME" <<<"$sessions" 2>&1)" || die "$taken"
fi

say "4/6 wiring your local agents"
"$RN" setup --dry-run
if [ "$YES" != 1 ]; then
  if [ -t 0 ]; then
    read -r -p "Apply these changes? [y/N] " a
    [[ "$a" == [yY]* ]] || die "stopped before applying. Run again with --yes to apply."
  else
    echo; echo "This is not a terminal: nothing applied. Review the plan above and run again with --yes."
    exit 3
  fi
fi
"$RN" setup
echo "Sessions of Claude Code / Codex that were already open must be reopened."

say "5/6 signing key and routine webhook"
mkdir -p "$CONF_DIR" "$LIB_DIR" "$HOME/Library/Logs" "$HOME/Library/LaunchAgents"; chmod 700 "$CONF_DIR"
[ -s "$KEY_FILE" ] || ( umask 077; openssl rand -hex 32 > "$KEY_FILE" )
chmod 600 "$KEY_FILE"
seed_env
if [ "$GROK_SET" = 1 ]; then
  why="$(deploy_state set-grok-name --relay-name "$RELAY_NAME" -- "$GROK_NAME" 2>&1)" \
    || echo "note: the Grok name was not recorded ($why); run ./install.sh --grok-name '<name>' later"
fi
webhook_step

say "6/6 cloud side"
cloud_prompt
