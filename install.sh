#!/usr/bin/env bash
# agent-mesh: install the Mac side in one run.
#   ./install.sh --name <team-name> [--machine mac] [--yes] [--lang en|zh-CN]
#   ./install.sh --webhook        enter (or change) the routine's Webhook URL and key, then (re)start the relay
#   ./install.sh --cloud-prompt   print the invite and the text to send to Grok Bot again
#   --repo-url https://...       public distribution URL accessible from the Bot's cloud computer
#   ./install.sh --version      print agent-mesh and pinned Reason Network versions; no installation
# --lang selects prompt language in every mode. Default: AGENT_MESH_LANG, otherwise en. Diagnostics stay English.
# The team name is also your name inside the team: 1-40 chars, lowercase letters, digits and '-', not your real
# name. It is fixed once the team exists.
# What it does: installs Reason Network, creates your private team, wires your local agents (after showing the
# plan), generates the signing key, asks for the routine's webhook, installs the relay as a login service, then
# prints what to send to Grok Bot. Re-running is safe.
set -euo pipefail

VERSION="v0.10.7"
HERE="$(cd "$(dirname "$0")" && pwd)"
PROJECT_VERSION="$(cat "$HERE/VERSION")"
BIN_DIR="$HOME/.local/bin"; RN="$BIN_DIR/reason-network"
CONF_DIR="$HOME/.config/agent-relay"; ENV_FILE="$CONF_DIR/env"; KEY_FILE="$CONF_DIR/outbound.key"
LIB_DIR="$HOME/.local/lib/agent-mesh"; RELAY="$LIB_DIR/agent_relay.py"
LABEL="com.local.agent-relay"; PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/agent-relay.log"
CFG="$HOME/.reason/network/config.toml"
export REASON_NETWORK_NO_PROMPT=1

NAME=""; MACHINE="mac"; YES=0; MODE=full; DOC_LANG="${AGENT_MESH_LANG:-en}"
REPO_URL="https://github.com/xiaozhenliu/agent-mesh.git"
while [ $# -gt 0 ]; do
  case "$1" in
    --name) NAME="${2:?}"; shift 2 ;;
    --machine) MACHINE="${2:?}"; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    --webhook) MODE=webhook; shift ;;
    --cloud-prompt) MODE=cloud; shift ;;
    --repo-url) REPO_URL="${2:?public HTTPS repository URL}"; shift 2 ;;
    --version) echo "agent-mesh $PROJECT_VERSION (Reason Network ${VERSION#v})"; exit 0 ;;
    --lang) [ $# -ge 2 ] || { echo "install: --lang needs en or zh-CN" >&2; exit 2; }; DOC_LANG="$2"; shift 2 ;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1"; sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
  esac
done
case "$DOC_LANG" in
  en|zh-CN) ;;
  *) echo "install: unsupported language '$DOC_LANG'; choose en or zh-CN" >&2; exit 2 ;;
esac

say() { printf '\n== %s\n' "$*"; }
die() { echo "install: $*" >&2; exit 1; }
valid_name() { [[ "$1" =~ ^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$ ]]; }
current_person() { if [ -f "$CFG" ]; then sed -n 's/^person = "\(.*\)"/\1/p' "$CFG" | head -n 1; fi; }
render_prompt() { "$PYTHON" "$HERE/scripts/render_prompt.py" "$@" --lang "$DOC_LANG"; }
have_webhook() { [ -f "$ENV_FILE" ] && grep -Eq '^WEBHOOK_URL=https://' "$ENV_FILE" && grep -Eq '^WEBHOOK_KEY=.{8,}' "$ENV_FILE"; }

write_env() {   # $1 url, $2 key
  ( umask 077; cat > "$ENV_FILE" <<EOF
# agent-relay config (mode 600). Never paste these values into a chat.
RN_BIN=$RN
PERSON=$(current_person)
WEBHOOK_URL=$1
WEBHOOK_KEY=$2
OUTBOUND_KEY_FILE=$KEY_FILE
# Optional, shown with defaults:
# LISTEN_NAME=bot  CLOUD_NAME=bot-cloud  INBOX_NAME=bot-inbox  CLOUD_MACHINE=cloud
# MAX_WAKES_PER_HOUR=20  MIN_SECONDS_BETWEEN_WAKES=20  MAX_BATCH=10  MAX_PROXY_PER_HOUR=60  MAX_SPOOL=1000
# WEBHOOK_MAX_BODY_CHARS=3500  DOORBELL_PREVIEW_CHARS=200  FWD_COPY_DAYS=7  REFWD_MAX_PER_ID=2  REFWD_MAX_PER_HOUR=20
EOF
  )
}

write_plist() {
  "$PYTHON" "$HERE/scripts/write_launch_agent.py" "$RELAY" "$PLIST"
  plutil -lint "$PLIST" >/dev/null
}

start_relay() {
  install -m 644 "$HERE/agent_relay.py" "$RELAY"
  write_plist
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  before=0; [ -f "$LOG" ] && before=$(wc -l < "$LOG")
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    if tail -n +"$((before + 1))" "$LOG" 2>/dev/null | grep -q "agent-relay up"; then echo "relay up (log: $LOG)"; return 0; fi
  done
  echo "relay did not report 'agent-relay up' within 10 s. Last log lines:"
  tail -n 10 "$LOG" "$HOME/Library/Logs/agent-relay.launchd.log" 2>/dev/null || true
  return 1
}

webhook_step() {
  [ -n "$(current_person)" ] && [ -s "$KEY_FILE" ] \
    || die "team or signing key missing; run ./install.sh --name <team-name> first"
  mkdir -p "$LIB_DIR" "$HOME/Library/Logs" "$HOME/Library/LaunchAgents"
  if [ "$MODE" = webhook ] || ! have_webhook; then
    echo "[GROK-UI] User: create a webhook-triggered routine in Grok Bot and paste this prompt. The Bot executes it on [BOT-CLOUD], its cloud computer:"
    echo "----------------------------------------------------------------"
    render_prompt routine
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
}

cloud_prompt() {
  local person repo
  person="$(current_person)"; [ -n "$person" ] || die "not in a team yet; run the full install first"
  repo="$REPO_URL"
  local invite
  invite="$("$RN" invite --me | sed -n 's/^For a cloud agent of yours.*send it: //p')"
  [ -n "$invite" ] || invite="$("$RN" invite --me)"
  echo "[GROK-UI] User: send this to Grok Bot as one message. The Bot executes it on [BOT-CLOUD], its cloud computer. The first line is the invite, valid 24 hours while [LOCAL], your Mac, is online:"
  echo "----------------------------------------------------------------"
  echo "$invite"
  echo
  render_prompt cloud-setup --team-name "$person" --mac-home "$HOME" --repo-url "$repo"
  echo "----------------------------------------------------------------"
  echo "[GROK-UI] User: approve CopyToBox from [LOCAL] $KEY_FILE to [BOT-CLOUD] /workspace/agent-mesh/box/outbound.key. Afterwards the installing agent runs ./check.sh on [LOCAL]."
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
launchctl print "gui/$(id -u)" >/dev/null 2>&1 \
  || die "no macOS GUI login session; sign in to the Mac desktop as this user first"
[ "${REASON_NETWORK_HOME:-$HOME/.reason/network}" = "$HOME/.reason/network" ] \
  || die "custom REASON_NETWORK_HOME is not supported; use the default user profile or stop to review your existing deployment"
export REASON_NETWORK_HOME="$HOME/.reason/network"
case "$REPO_URL" in https://*) ;; *) die "--repo-url must be a public HTTPS clone URL accessible without cloud credentials" ;; esac

# Never replace a user's different engine version or create a second PATH installation silently.
existing="$(command -v reason-network || true)"
[ -z "$existing" ] || [ "$existing" = "$RN" ] || die "reason-network already exists at $existing; review that installation before using $RN"
if [ -x "$RN" ]; then
  installed_version="$("$RN" --version 2>/dev/null | awk '{print $2}')"
  [ "$installed_version" = "${VERSION#v}" ] || die "existing Reason Network is $installed_version; this release requires ${VERSION#v}. Review an explicit version change before retrying; it has not been overwritten"
fi

case "$MODE" in
  webhook) [ -x "$RN" ] || die "Reason Network is not installed; run the full install first"; webhook_step; exit 0 ;;
  cloud) [ -x "$RN" ] || die "Reason Network is not installed; run the full install first"; cloud_prompt; exit 0 ;;
esac

[ -n "$NAME" ] || die "need --name <team-name> (run with --help)"
valid_name "$NAME" && valid_name "$MACHINE" \
  || die "the name must be 1-40 chars of lowercase letters, digits and '-', not starting or ending with '-'"

say "1/6 network"
code="$(curl -sS -o /dev/null -m 10 -w '%{http_code}' https://aps1-1.relay.n0.iroh.link/ 2>/dev/null || true)"
[ "$code" = 200 ] || die "cannot reach the iroh relay (HTTP '$code'). Check VPN/proxy bypass rules for the whole iroh.link domain and exclude it from HTTPS decryption. Confirm the curl check prints 200, then run this again. See README.md: Troubleshooting (Chinese: README.zh-CN.md)."
echo "ok"

say "2/6 Reason Network $VERSION"
if [ -x "$RN" ] && [ "$("$RN" --version 2>/dev/null | awk '{print $2}')" = "${VERSION#v}" ]; then
  echo "already installed: $("$RN" --version)"
else
  case "$(uname -m)" in
    arm64) target=aarch64-apple-darwin ;;
    x86_64) target=x86_64-apple-darwin ;;
    *) die "unsupported CPU $(uname -m)" ;;
  esac
  base="https://github.com/reason-machines/reason-network/releases/download/$VERSION"
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  curl -fsSL "$base/reason-network-$target.tar.gz" -o "$tmp/e.tgz"
  curl -fsSL "$base/reason-network-$target.tar.gz.sha256" -o "$tmp/sum"
  [ "$(cut -d' ' -f1 "$tmp/sum")" = "$(shasum -a 256 "$tmp/e.tgz" | cut -d' ' -f1)" ] || die "checksum mismatch, not installing"
  tar xzf "$tmp/e.tgz" -C "$tmp"
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
for kv in "auto_update = false" "lan = false"; do
  k="${kv%% *}"
  if grep -q "^$k " "$CFG"; then sed -i '' "s/^$k = .*/$kv/" "$CFG"; else echo "$kv" >> "$CFG"; fi
done
"$RN" stop >/dev/null 2>&1 || true
"$RN" team

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
webhook_step

say "6/6 cloud side"
cloud_prompt
