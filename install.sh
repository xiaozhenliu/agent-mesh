#!/usr/bin/env bash
# agent-mesh: install the Mac side in one run.
#   ./install.sh --name <team-name> [--machine mac] [--yes]
#   ./install.sh --webhook        enter (or change) the routine's Webhook URL and key, then (re)start the relay
#   ./install.sh --cloud-prompt   print the invite and the text to send to Grok Bot again
# The team name is also your name inside the team: 1-40 chars, lowercase letters, digits and '-', not your real
# name. It is fixed once the team exists.
# What it does: installs Reason Network, creates your private team, wires your local agents (after showing the
# plan), generates the signing key, asks for the routine's webhook, installs the relay as a login service, then
# prints what to send to Grok Bot. Re-running is safe.
set -euo pipefail

VERSION="${VERSION:-v0.10.7}"
HERE="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$HOME/.local/bin"; RN="$BIN_DIR/reason-network"
CONF_DIR="$HOME/.config/agent-relay"; ENV_FILE="$CONF_DIR/env"; KEY_FILE="$CONF_DIR/outbound.key"
LIB_DIR="$HOME/.local/lib/agent-mesh"; RELAY="$LIB_DIR/agent_relay.py"
LABEL="com.local.agent-relay"; PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/agent-relay.log"
CFG="$HOME/.reason/network/config.toml"
export REASON_NETWORK_NO_PROMPT=1

NAME=""; MACHINE="mac"; YES=0; MODE=full
while [ $# -gt 0 ]; do
  case "$1" in
    --name) NAME="${2:?}"; shift 2 ;;
    --machine) MACHINE="${2:?}"; shift 2 ;;
    --yes|-y) YES=1; shift ;;
    --webhook) MODE=webhook; shift ;;
    --cloud-prompt) MODE=cloud; shift ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1"; sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
  esac
done

say() { printf '\n== %s\n' "$*"; }
die() { echo "install: $*" >&2; exit 1; }
valid_name() { [[ "$1" =~ ^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$ ]]; }
current_person() { sed -n 's/^person = "\(.*\)"/\1/p' "$CFG" 2>/dev/null | head -n 1; }
code_block() { sed -n '/^```$/,/^```$/p' "$1" | sed '1d;$d'; }
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
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>$RELAY</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$BIN_DIR:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/agent-relay.launchd.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/agent-relay.launchd.log</string>
</dict>
</plist>
EOF
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
  if [ "$MODE" = webhook ] || ! have_webhook; then
    echo "In Grok Bot, create a routine that is triggered by a webhook, and paste this as its prompt:"
    echo "----------------------------------------------------------------"
    code_block "$HERE/prompts/routine.md"
    echo "----------------------------------------------------------------"
    echo "The routine panel then shows a Webhook URL and a key."
    if [ ! -t 0 ]; then
      echo "This is not a terminal, so the values cannot be entered here."
      echo "Run   ./install.sh --webhook   in your own terminal and paste them there (never into a chat)."
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
  repo="$(git -C "$HERE" remote get-url origin 2>/dev/null || echo '<repo url>')"
  local invite
  invite="$("$RN" invite --me | sed -n 's/^For a cloud agent of yours.*send it: //p')"
  [ -n "$invite" ] || invite="$("$RN" invite --me)"
  echo "Send this to Grok Bot as one message (the first line is the invite, valid 24 hours while this Mac is online):"
  echo "----------------------------------------------------------------"
  echo "$invite"
  echo
  code_block "$HERE/prompts/cloud-setup.md" | sed -e "s|<team-name>|$person|g" -e "s|<用户>|$(whoami)|g" -e "s|<仓库地址>|$repo|g"
  echo "----------------------------------------------------------------"
  echo "Grok Bot will ask you once to approve copying $KEY_FILE with CopyToBox. Afterwards run ./check.sh."
}

case "$MODE" in
  webhook) [ -x "$RN" ] || die "Reason Network is not installed; run the full install first"; webhook_step; exit 0 ;;
  cloud) [ -x "$RN" ] || die "Reason Network is not installed; run the full install first"; cloud_prompt; exit 0 ;;
esac

[ -n "$NAME" ] || die "need --name <team-name> (run with --help)"
valid_name "$NAME" && valid_name "$MACHINE" \
  || die "the name must be 1-40 chars of lowercase letters, digits and '-', not starting or ending with '-'"
[ "$(uname -s)" = Darwin ] || die "this installer is for macOS"
command -v python3 >/dev/null || die "python3 missing: run  xcode-select --install"

say "1/6 network"
code="$(curl -sS -o /dev/null -m 10 -w '%{http_code}' https://aps1-1.relay.n0.iroh.link/ 2>/dev/null || true)"
[ "$code" = 200 ] || die "cannot reach the iroh relay (HTTP '$code'). A VPN or proxy app is almost always the cause: add a direct (bypass) rule for the whole iroh.link domain, exclude it from HTTPS decryption, confirm the curl check in README prints 200, then run this again. See README, 常见问题."
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
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "note: add $BIN_DIR to your PATH, e.g.  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc" ;; esac

say "3/6 team"
export REASON_NETWORK_MACHINE="$MACHINE"
if "$RN" team >/dev/null 2>&1; then
  have="$(current_person)"
  [ -z "$have" ] || [ "$have" = "$NAME" ] || die "this Mac is already '$have' in a team and names cannot change. Run ./uninstall.sh --all to start over."
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
