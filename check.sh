#!/usr/bin/env bash
# Read-only self-check for agent-mesh on the Mac. Changes nothing.
#   ./check.sh      exit 0 = all PASS, 1 = at least one FAIL (each FAIL line says what to do)
# Agents: run it after install.sh and after the cloud side joins; paste the output to the user as is.
# It changes no deployment, configuration or state file and stops nothing. Engine commands it runs (team, who) may
# start a stopped engine daemon, as any Reason Network command does.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ENGINE="$HERE/box/rn-engine.py"
LIB="$HOME/.local/lib/agent-mesh"
RN="${RN_BIN:-$HOME/.local/bin/reason-network}"
CFG="$HOME/.reason/network/config.toml"
LABEL="com.local.agent-relay"
PERSON="$(sed -n 's/^person = "\(.*\)"/\1/p' "$CFG" 2>/dev/null | head -n 1)"
fail=0
ok()   { printf 'PASS  %s\n' "$1"; }
bad()  { printf 'FAIL  %s\n      -> %s\n' "$1" "$2"; fail=1; }
skip() { printf 'SKIP  %s (%s)\n' "$1" "$2"; }

# prerequisites
[ "$(uname -s)" = Darwin ] || { bad "not macOS" "run this check on [LOCAL], the user's Mac"; exit 1; }
ok "macOS"
[ "${REASON_NETWORK_HOME:-$HOME/.reason/network}" = "$HOME/.reason/network" ] \
  || { bad "custom REASON_NETWORK_HOME" "this installer supports the default user profile only"; exit 1; }
export REASON_NETWORK_HOME="$HOME/.reason/network"
if command -v python3 >/dev/null && python3 -c 'import sys, ssl, sqlite3; sys.exit(sys.version_info < (3, 9))'; then
  ok "Python 3.9+ with ssl and sqlite3"
else
  bad "Python 3.9+ missing or incomplete" "install Python 3.9+ and ensure python3 is on PATH; see README prerequisites"
fi
case ":$PATH:" in *":$HOME/.local/bin:"*) ok "PATH includes ~/.local/bin";; *) bad "PATH lacks ~/.local/bin" "add ~/.local/bin to PATH using your shell's startup file; see README prerequisites";; esac
relay_hint="allow direct connections (no HTTPS decryption) to the whole iroh.link and reasonmachines.com domains; see README.md: Troubleshooting (Chinese: README.zh-CN.md)"
pinned="$(sed -n 's/^VERSION="\(.*\)"$/\1/p' "$(dirname "$0")/install.sh" | head -n 1)"
if relays="$(python3 "$(dirname "$0")/scripts/relay_targets.py" --rn "$RN" --home "$HOME/.reason/network" --pinned "${pinned:-unknown}" 2>/dev/null)"; then
  source="$(printf '%s\n' "$relays" | head -n 1)"
  down=""
  for url in $(printf '%s\n' "$relays" | tail -n +2); do
    curl -sS -o /dev/null -m 10 -w '%{http_code}' "$url" 2>/dev/null | grep -q '^200$' || down="$down $url"
  done
  [ -z "$down" ] && ok "relay reachable: $source" || bad "relay not reachable ($source):$down" "$relay_hint"
else
  skip "relay reachability unknown" "${relays:-relay_targets.py failed}; $relay_hint"
fi

# engine and team
if [ -x "$RN" ]; then
  have="$("$RN" --version 2>/dev/null | awk '{print $2}')"
  case "$(python3 "$ENGINE" compare "$have" "${pinned#v}")" in
    same) ok "reason-network $have (the version this release pins)" ;;
    older) bad "reason-network $have is older than ${pinned#v}, the version this release pins" "run ./update.sh: it upgrades the engine with a backup and rolls back on failure (UPGRADE.md)" ;;
    *) bad "reason-network ${have:-of an unknown version} is not supported by this release (it pins ${pinned#v})" "agent-mesh never downgrades the engine; use the agent-mesh release made for this engine version, or reinstall ${pinned#v}" ;;
  esac
  if [ -f "$LIB/engine-upgrade.json" ]; then
    backup="$(python3 "$ENGINE" txn-get --txn "$LIB/engine-upgrade.json" backup 2>&1)"
    bad "an engine upgrade did not finish" "run ./update.sh in a terminal: it rolls the switch back first (backup: ${backup:-unknown})"
  fi
  if team="$("$RN" team 2>/dev/null)"; then
    team="$(printf '%s\n' "$team" | sed -E 's/^([[:space:]]+[^[:space:]]+) \(verified\)([[:space:]])/\1\2/')"
    [ -n "$PERSON" ] && ok "team name: $PERSON" || bad "no person in $CFG" "run ./install.sh"
    echo "$team" | grep -Eq "^\s+$PERSON\s+cloud\s" && ok "cloud machine joined" || skip "cloud machine" "not joined yet: send the text from ./install.sh --cloud-prompt to Grok Bot"
    # Rows are "  <person>  <machine>  <link>"; only this machine and the person's cloud belong here.
    # Members are endpoints, so a second machine may reuse the label <person> cloud: count those rows as well.
    extra="$(echo "$team" | awk -v p="$PERSON" '
      NR > 1 && NF >= 3 && !/this machine$/ {
        if ($1 == p && $2 == "cloud" && !clouds++) next
        printf "%s%s/%s", sep, $1, $2; sep = ", "
      }')"
    [ -z "$extra" ] && ok "team holds only this machine and cloud" \
      || bad "unexpected team members: $extra" "if you did not add them: reason-network remove <person>/<machine> for each, then ./install.sh --rotate-key (README: Security and privacy)"
  else
    bad "not in a team" "run ./install.sh"
  fi
  daemon="$(python3 "$ENGINE" info --home "$HOME/.reason/network" --bin "$RN" 2>&1)"
  case "$daemon" in
    *state=fresh) ok "engine daemon runs the installed binary (${daemon%% *})" ;;
    *state=stale) bad "the engine daemon (${daemon%% *}) started before the binary was replaced, so it still runs the old version" "reason-network stop, then ./check.sh again (the next command starts it on the installed binary)" ;;
    *state=none) skip "engine daemon" "not running" ;;
    *) skip "engine daemon" "could not check: $daemon" ;;
  esac
  [ -f "$CFG" ] && grep -q '^auto_update = false' "$CFG" && ok "auto_update off" || bad "auto_update not off" "set  auto_update = false  in $CFG"
  [ -f "$CFG" ] && grep -q '^lan = false' "$CFG" && ok "lan broadcast off" || bad "lan not off" "set  lan = false  in $CFG"
else
  bad "reason-network not installed" "run ./install.sh"
fi

# relay
env="$HOME/.config/agent-relay/env"; key="$HOME/.config/agent-relay/outbound.key"
names="$(dirname "$0")/scripts/relay_names.py"
NAME=bot
if [ -f "$env" ] && ! name="$(python3 "$names" env "$env" 2>&1)"; then
  bad "relay names in env are not derived from one name" "$name; make LISTEN_NAME/CLOUD_NAME/INBOX_NAME <name>, <name>-cloud, <name>-inbox (README: Install)"
else
  [ ! -f "$env" ] || NAME="${name:-bot}"
  if [ "$NAME" = bot ]; then ok "relay name: bot (default)"; else ok "relay name: $NAME"; fi
fi
if [ -f "$env" ]; then
  [ "$(stat -f %Lp "$env" 2>/dev/null)" = "600" ] && ok "env file mode 600" || bad "env file not mode 600" "chmod 600 $env"
  grep -Eq '^WEBHOOK_URL=https://' "$env" && grep -Eq '^WEBHOOK_KEY=.{8,}' "$env" && ok "webhook configured" || bad "webhook not configured" "run ./install.sh --webhook"
else
  bad "relay not configured" "run ./install.sh --webhook"
fi
[ -s "$key" ] && ok "signing key present" || bad "signing key missing" "run ./install.sh"
installed="$LIB/agent_relay.py"
if [ ! -f "$installed" ]; then
  bad "relay file missing" "run ./install.sh --webhook"
elif cmp -s "$installed" "$HERE/agent_relay.py"; then
  ok "relay installed (this checkout's version)"
elif ! grep -qF '/terminal(-[0-9]+|@' "$installed"; then
  bad "the installed relay predates Reason Network 0.12 terminal addresses" "run ./update.sh (it installs this checkout's relay)"
else
  skip "relay version" "the installed relay differs from this checkout; ./update.sh installs this one"
fi
launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 && ok "relay service loaded" || bad "relay service not loaded" "run ./install.sh --webhook"
log="$HOME/Library/Logs/agent-relay.log"
if [ -f "$log" ]; then
  grep -q "agent-relay up" "$log" && ok "relay log shows 'agent-relay up'" || bad "relay never came up" "tail $log"
  codes="$(grep -E ' POST [0-9]+ chars [0-9]+ bytes sha256=[0-9a-f]+ code=.* items=' "$log" | tail -n 2 \
    | sed -E 's/.* code=(.*) items=.*/\1/')"
  if [ -n "$codes" ]; then
    code="$(printf '%s\n' "$codes" | tail -n 1)"
    before=""
    [ "$(printf '%s\n' "$codes" | wc -l)" -lt 2 ] || before="$(printf '%s\n' "$codes" | head -n 1)"
    case "$code" in
      2[0-9][0-9])
        case "$before" in
          ""|2[0-9][0-9]) ok "latest webhook POST succeeded (code=$code)" ;;
          *) ok "latest webhook POST succeeded (code=$code; recovered after an earlier failure)" ;;
        esac ;;
      *) bad "latest webhook POST failed (code=$code)" "check the Webhook URL and key: ./install.sh --webhook" ;;
    esac
  elif tail -n 50 "$log" | grep -q "webhook POST failed"; then
    bad "recent webhook POST failures" "check the Webhook URL and key: ./install.sh --webhook"
  fi
fi

# sessions
if [ -x "$RN" ] && who="$("$RN" who 2>/dev/null)"; then
  registered="$(python3 "$names" registered "$NAME" 2>&1)"; rc=$?
  relay_pid="$(launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | sed -n 's/^[[:space:]]*pid = \([0-9][0-9]*\)$/\1/p' | head -n 1)"
  if [ -z "$relay_pid" ]; then
    reg="fail the relay service has no running process"
  else
    reg="$(python3 "$ENGINE" registered --home "$HOME/.reason/network" \
      --marker "$HOME/Library/Application Support/agent-relay/registered" --name "$PERSON/$NAME" \
      --relay-pid "$relay_pid" 2>&1)" || reg="fail $reg"
  fi
  if [ "$rc" = 0 ] && [ -n "$registered" ] && [ "$registered" != "$NAME" ]; then
    bad "relay registered under another name: $PERSON/$registered" "another session took $NAME; the relay gets no mail for $PERSON/$NAME (README: Troubleshooting)"
  else
    case "$reg" in
      ok\ *) ok "relay listening as $PERSON/$NAME (registered on the running engine daemon)" ;;
      wait\ *) skip "relay registration" "${reg#wait }" ;;
      *) bad "relay not registered on the running engine: ${reg#fail }" "if ./update.sh has not run for this checkout, run it; otherwise see tail -n 30 ~/Library/Logs/agent-relay.log and ./install.sh --restart-relay (README: Troubleshooting)" ;;
    esac
  fi
  n=$(echo "$who" | grep -Ec 'claude-code|codex|pi |dsh')
  [ "$n" -gt 0 ] && ok "$n agent session(s) on the roster" || skip "agent sessions" "open a Claude Code / Codex session, then rerun"
fi

# deployment record
cengine="$(python3 - "$LIB/deployed.json" <<'PY' 2>&1
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except FileNotFoundError:
    print("none")
    raise SystemExit
except (OSError, ValueError) as error:
    print(f"unreadable {error}")
    raise SystemExit
value = data.get("cloud_engine") if isinstance(data, dict) else None
if not isinstance(value, dict):
    print("none")
else:
    print(value.get("state") or "?", value.get("version") or "?")
PY
)"
case "$cengine" in
  "confirmed ${pinned#v}") ok "cloud engine last confirmed at ${pinned#v}" ;;
  confirmed\ *) bad "cloud engine last confirmed at ${cengine#confirmed }, not ${pinned#v}" "run ./update.sh: its cloud engine step has Grok Bot run box/rn-cloud-setup.sh --upgrade-engine" ;;
  pending\ *) skip "cloud engine" "the cloud setup the installer printed is not confirmed yet; ./update.sh asks about it" ;;
  none) skip "cloud engine" "no confirmed cloud engine version recorded; ./update.sh's cloud engine step records it" ;;
  *) skip "cloud engine" "could not read $LIB/deployed.json: $cengine" ;;
esac

echo
[ "$fail" = 0 ] && echo "RESULT ok" || echo "RESULT fix the FAIL lines above"
exit "$fail"
