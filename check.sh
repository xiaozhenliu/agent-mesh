#!/usr/bin/env bash
# Read-only self-check for agent-mesh on the Mac. Changes nothing.
#   ./check.sh      exit 0 = all PASS, 1 = at least one FAIL (each FAIL line says what to do)
# Agents: run it after install.sh and after the cloud side joins; paste the output to the user as is.
set -u
RN="${RN_BIN:-$HOME/.local/bin/reason-network}"
CFG="$HOME/.reason/network/config.toml"
LABEL="com.local.agent-relay"
PERSON="$(sed -n 's/^person = "\(.*\)"/\1/p' "$CFG" 2>/dev/null | head -n 1)"
fail=0
ok()   { printf 'PASS  %s\n' "$1"; }
bad()  { printf 'FAIL  %s\n      -> %s\n' "$1" "$2"; fail=1; }
skip() { printf 'SKIP  %s (%s)\n' "$1" "$2"; }

# prerequisites
[ "$(uname -s)" = Darwin ] && ok "macOS" || bad "not macOS" "this project is tested on macOS only"
command -v python3 >/dev/null && ok "python3" || bad "python3 missing" "xcode-select --install"
case ":$PATH:" in *":$HOME/.local/bin:"*) ok "~/.local/bin in PATH";; *) bad "~/.local/bin not in PATH" "add  export PATH=\"\$HOME/.local/bin:\$PATH\"  to ~/.zshrc";; esac
if curl -sS -o /dev/null -m 10 -w '%{http_code}' https://aps1-1.relay.n0.iroh.link/ 2>/dev/null | grep -q '^200$'; then
  ok "iroh relay reachable"
else
  bad "iroh relay not reachable" "almost always a VPN/proxy app: add a direct (bypass) rule for the whole iroh.link domain and exclude it from HTTPS decryption; see README 常见问题"
fi

# engine and team
if [ -x "$RN" ]; then
  ok "reason-network $("$RN" --version 2>/dev/null | awk '{print $2}')"
  if team="$("$RN" team 2>/dev/null)"; then
    [ -n "$PERSON" ] && ok "team name: $PERSON" || bad "no person in $CFG" "run ./install.sh"
    echo "$team" | grep -Eq "^\s+$PERSON\s+cloud\s" && ok "cloud machine joined" || skip "cloud machine" "not joined yet: send the text from ./install.sh --cloud-prompt to Grok Bot"
  else
    bad "not in a team" "run ./install.sh"
  fi
  [ -f "$CFG" ] && grep -q '^auto_update = false' "$CFG" && ok "auto_update off" || bad "auto_update not off" "set  auto_update = false  in $CFG"
  [ -f "$CFG" ] && grep -q '^lan = false' "$CFG" && ok "lan broadcast off" || bad "lan not off" "set  lan = false  in $CFG"
else
  bad "reason-network not installed" "run ./install.sh"
fi

# relay
env="$HOME/.config/agent-relay/env"; key="$HOME/.config/agent-relay/outbound.key"
if [ -f "$env" ]; then
  [ "$(stat -f %Lp "$env" 2>/dev/null)" = "600" ] && ok "env file mode 600" || bad "env file not mode 600" "chmod 600 $env"
  grep -Eq '^WEBHOOK_URL=https://' "$env" && grep -Eq '^WEBHOOK_KEY=.{8,}' "$env" && ok "webhook configured" || bad "webhook not configured" "run ./install.sh --webhook"
else
  bad "relay not configured" "run ./install.sh --webhook"
fi
[ -s "$key" ] && ok "signing key present" || bad "signing key missing" "run ./install.sh"
[ -f "$HOME/.local/lib/agent-mesh/agent_relay.py" ] && ok "relay installed" || bad "relay file missing" "run ./install.sh --webhook"
launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 && ok "relay service loaded" || bad "relay service not loaded" "run ./install.sh --webhook"
log="$HOME/Library/Logs/agent-relay.log"
if [ -f "$log" ]; then
  grep -q "agent-relay up" "$log" && ok "relay log shows 'agent-relay up'" || bad "relay never came up" "tail $log"
  if tail -n 50 "$log" | grep -q "webhook POST failed"; then bad "recent webhook POST failures" "check the Webhook URL and key: ./install.sh --webhook"; fi
fi

# sessions
if [ -x "$RN" ] && who="$("$RN" who 2>/dev/null)"; then
  echo "$who" | grep -Eq "^\s+bot\s+cli\s" && ok "relay listening as $PERSON/bot" || skip "relay session" "appears a few seconds after the relay starts"
  n=$(echo "$who" | grep -Ec 'claude-code|codex|pi |dsh')
  [ "$n" -gt 0 ] && ok "$n agent session(s) on the roster" || skip "agent sessions" "open a Claude Code / Codex session, then rerun"
fi

echo
[ "$fail" = 0 ] && echo "RESULT ok" || echo "RESULT fix the FAIL lines above"
exit "$fail"
