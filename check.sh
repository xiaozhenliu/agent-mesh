#!/usr/bin/env bash
# Read-only self-check for agent-mesh on the Mac. Changes nothing.
#   ./check.sh      exit 0 = all PASS, 1 = at least one FAIL (each FAIL line says what to do)
#   --lang en|zh-CN  language of the text after PASS/FAIL/SKIP; default: the language recorded at install, then
#                    AGENT_MESH_LANG, then en
# Agents: run it after install.sh and after the cloud side joins; paste the output to the user as is.
# It changes no deployment, configuration or state file and stops nothing. Engine commands it runs (team, who) may
# start a stopped engine daemon, as any Reason Network command does.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
. "$HERE/scripts/msg.sh"
both_langs() { printf '%s / %s\n' "$(AGENT_MESH_LANG=en tq "$@")" "$(AGENT_MESH_LANG=zh-CN tq "$@")"; }
CHECK_LANG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --lang) [ $# -ge 2 ] || { both_langs check.lang_value >&2; exit 2; }; CHECK_LANG="$2"; shift 2
      case "$CHECK_LANG" in en|zh-CN) ;; *) both_langs check.lang_unsupported "$CHECK_LANG" >&2; exit 2 ;; esac ;;
    --lang=*) CHECK_LANG="${1#--lang=}"; shift
      case "$CHECK_LANG" in en|zh-CN) ;; *) both_langs check.lang_unsupported "$CHECK_LANG" >&2; exit 2 ;; esac ;;
    *) both_langs check.unknown_option "$1" >&2; exit 2 ;;
  esac
done
if [ -z "$CHECK_LANG" ] && command -v python3 >/dev/null; then
  CHECK_LANG="$(python3 "$HERE/scripts/deploy_state.py" lang 2>/dev/null)" || CHECK_LANG=""
fi
if [ -z "$CHECK_LANG" ]; then
  case "${AGENT_MESH_LANG:-}" in zh-CN) CHECK_LANG=zh-CN ;; *) CHECK_LANG=en ;; esac
fi
export AGENT_MESH_LANG="$CHECK_LANG"
ENGINE="$HERE/box/rn-engine.py"
LIB="$HOME/.local/lib/agent-mesh"
RN="${RN_BIN:-$HOME/.local/bin/reason-network}"
CFG="$HOME/.reason/network/config.toml"
LABEL="com.local.agent-relay"
PERSON="$(sed -n 's/^person = "\(.*\)"/\1/p' "$CFG" 2>/dev/null | head -n 1)"
fail=0
ok()   { printf 'PASS  %s\n' "$1"; }
bad()  { printf 'FAIL  %s\n      -> %s\n' "$1" "$2"; fail=1; }
skip() { printf 'SKIP  %s\n' "$(tq check.skip_reason "$1" "$2")"; }

# prerequisites
[ "$(uname -s)" = Darwin ] || { bad "$(tq check.not_macos)" "$(tq check.not_macos_fix)"; exit 1; }
ok "macOS"
[ "${REASON_NETWORK_HOME:-$HOME/.reason/network}" = "$HOME/.reason/network" ] \
  || { bad "$(tq check.custom_rn_home)" "$(tq check.custom_rn_home_fix)"; exit 1; }
export REASON_NETWORK_HOME="$HOME/.reason/network"
if command -v python3 >/dev/null && python3 -c 'import sys, ssl, sqlite3; sys.exit(sys.version_info < (3, 9))'; then
  ok "$(tq check.python_ok)"
else
  bad "$(tq check.python_bad)" "$(tq check.python_fix)"
fi
case ":$PATH:" in *":$HOME/.local/bin:"*) ok "$(tq check.path_ok)";; *) bad "$(tq check.path_bad)" "$(tq check.path_fix)";; esac
relay_hint="$(tq check.relay_hint)"
pinned="$(sed -n 's/^VERSION="\(.*\)"$/\1/p' "$(dirname "$0")/install.sh" | head -n 1)"
if relays="$(python3 "$(dirname "$0")/scripts/relay_targets.py" --rn "$RN" --home "$HOME/.reason/network" --pinned "${pinned:-unknown}" 2>/dev/null)"; then
  source="$(printf '%s\n' "$relays" | head -n 1)"
  down=""
  for url in $(printf '%s\n' "$relays" | tail -n +2); do
    curl -sS -o /dev/null -m 10 -w '%{http_code}' "$url" 2>/dev/null | grep -q '^200$' || down="$down $url"
  done
  [ -z "$down" ] && ok "$(tq check.relay_ok "$source")" || bad "$(tq check.relay_bad "$source" "$down")" "$relay_hint"
else
  skip "$(tq check.relay_unknown)" "$(tq check.relay_unknown_why "${relays:-$(tq check.targets_failed)}" "$relay_hint")"
fi

# engine and team
if [ -x "$RN" ]; then
  have="$("$RN" --version 2>/dev/null | awk '{print $2}')"
  case "$(python3 "$ENGINE" compare "$have" "${pinned#v}")" in
    same) ok "$(tq check.engine_same "$have")" ;;
    older) bad "$(tq check.engine_older "$have" "${pinned#v}")" "$(tq check.engine_older_fix)" ;;
    *) bad "$(tq check.engine_other "${have:-$(tq check.unknown_version)}" "${pinned#v}")" "$(tq check.engine_other_fix "${pinned#v}")" ;;
  esac
  if [ -f "$LIB/engine-upgrade.json" ]; then
    backup="$(python3 "$ENGINE" txn-get --txn "$LIB/engine-upgrade.json" backup 2>&1)"
    bad "$(tq check.upgrade_unfinished)" "$(tq check.upgrade_unfinished_fix "${backup:-$(tq check.unknown)}")"
  fi
  if team="$("$RN" team 2>/dev/null)"; then
    team="$(printf '%s\n' "$team" | sed -E 's/^([[:space:]]+[^[:space:]]+) \(verified\)([[:space:]])/\1\2/')"
    [ -n "$PERSON" ] && ok "$(tq check.team_name "$PERSON")" || bad "$(tq check.no_person "$CFG")" "$(tq check.run_install)"
    echo "$team" | grep -Eq "^\s+$PERSON\s+cloud\s" && ok "$(tq check.cloud_joined)" || skip "$(tq check.cloud_machine)" "$(tq check.cloud_not_joined)"
    # Rows are "  <person>  <machine>  <link>"; only this machine and the person's cloud belong here.
    # Members are endpoints, so a second machine may reuse the label <person> cloud: count those rows as well.
    extra="$(echo "$team" | awk -v p="$PERSON" '
      NR > 1 && NF >= 3 && !/this machine$/ {
        if ($1 == p && $2 == "cloud" && !clouds++) next
        printf "%s%s/%s", sep, $1, $2; sep = ", "
      }')"
    [ -z "$extra" ] && ok "$(tq check.team_only)" \
      || bad "$(tq check.team_extra "$extra")" "$(tq check.team_extra_fix)"
  else
    bad "$(tq check.no_team)" "$(tq check.run_install)"
  fi
  daemon="$(python3 "$ENGINE" info --home "$HOME/.reason/network" --bin "$RN" 2>&1)"
  case "$daemon" in
    *state=fresh) ok "$(tq check.daemon_fresh "${daemon%% *}")" ;;
    *state=stale) bad "$(tq check.daemon_stale "${daemon%% *}")" "$(tq check.daemon_stale_fix)" ;;
    *state=none) skip "$(tq check.daemon)" "$(tq check.daemon_none)" ;;
    *) skip "$(tq check.daemon)" "$(tq check.daemon_unknown "$daemon")" ;;
  esac
  [ -f "$CFG" ] && grep -q '^auto_update = false' "$CFG" && ok "$(tq check.auto_update_ok)" || bad "$(tq check.auto_update_bad)" "$(tq check.auto_update_fix "$CFG")"
  [ -f "$CFG" ] && grep -q '^lan = false' "$CFG" && ok "$(tq check.lan_ok)" || bad "$(tq check.lan_bad)" "$(tq check.lan_fix "$CFG")"
else
  bad "$(tq check.no_engine)" "$(tq check.run_install)"
fi

# relay
env="$HOME/.config/agent-relay/env"; key="$HOME/.config/agent-relay/outbound.key"
names="$HERE/scripts/relay_names.py"; state="$HERE/scripts/deploy_state.py"
NAME=""
if [ -f "$env" ] && ! name="$(python3 "$names" env "$env" 2>&1)"; then
  bad "$(tq check.env_names)" "$(tq check.env_names_fix "$name")"
else
  rc=0
  if [ -f "$env" ]; then NAME="$name"; else NAME="$(python3 "$state" recorded-name 2>&1)" || rc=$?; fi
  if [ "$rc" != 0 ] && [ "$rc" != 3 ]; then
    bad "$(tq check.bot_address)" "$NAME"; NAME=""
  elif [ -z "$NAME" ] || [ "$rc" = 3 ]; then
    NAME=""
    skip "$(tq check.bot_address)" "$(tq check.no_address)"
  elif label="$(python3 "$state" label --person "$PERSON" --relay-name "$NAME" 2>&1)"; then
    case "$label" in
      *" → Grok: "*) ok "$(tq check.bot_address_is "$label")" ;;
      *) ok "$(tq check.bot_address_no_grok "$label")" ;;
    esac
  else
    ok "$(tq check.bot_address_grok_unreadable "$PERSON/$NAME" "$label")"
  fi
fi
if [ -f "$env" ]; then
  [ "$(stat -f %Lp "$env" 2>/dev/null)" = "600" ] && ok "$(tq check.env_mode_ok)" || bad "$(tq check.env_mode_bad)" "chmod 600 $env"
  grep -Eq '^WEBHOOK_URL=https://' "$env" && grep -Eq '^WEBHOOK_KEY=.{8,}' "$env" && ok "$(tq check.webhook_ok)" || bad "$(tq check.webhook_bad)" "$(tq check.run_webhook)"
else
  bad "$(tq check.relay_unconfigured)" "$(tq check.run_webhook)"
fi
[ -s "$key" ] && ok "$(tq check.key_ok)" || bad "$(tq check.key_bad)" "$(tq check.run_install)"
installed="$LIB/agent_relay.py"
if [ ! -f "$installed" ]; then
  bad "$(tq check.relay_missing)" "$(tq check.run_webhook)"
elif cmp -s "$installed" "$HERE/agent_relay.py"; then
  ok "$(tq check.relay_current)"
elif ! grep -qF '/terminal(-[0-9]+|@' "$installed"; then
  bad "$(tq check.relay_old)" "$(tq check.relay_old_fix)"
else
  skip "$(tq check.relay_version)" "$(tq check.relay_differs)"
fi
launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 && ok "$(tq check.service_ok)" || bad "$(tq check.service_bad)" "$(tq check.run_webhook)"
log="$HOME/Library/Logs/agent-relay.log"
if [ -f "$log" ]; then
  grep -q "agent-relay up" "$log" && ok "$(tq check.log_up)" || bad "$(tq check.log_never_up)" "tail $log"
  codes="$(grep -E ' POST [0-9]+ chars [0-9]+ bytes sha256=[0-9a-f]+ code=.* items=' "$log" | tail -n 2 \
    | sed -E 's/.* code=(.*) items=.*/\1/')"
  if [ -n "$codes" ]; then
    code="$(printf '%s\n' "$codes" | tail -n 1)"
    before=""
    [ "$(printf '%s\n' "$codes" | wc -l)" -lt 2 ] || before="$(printf '%s\n' "$codes" | head -n 1)"
    case "$code" in
      2[0-9][0-9])
        case "$before" in
          ""|2[0-9][0-9]) ok "$(tq check.post_ok "$code")" ;;
          *) ok "$(tq check.post_recovered "$code")" ;;
        esac ;;
      *) bad "$(tq check.post_failed "$code")" "$(tq check.post_fix)" ;;
    esac
  elif tail -n 50 "$log" | grep -q "webhook POST failed"; then
    bad "$(tq check.post_failures)" "$(tq check.post_fix)"
  fi
fi

# sessions
if [ -z "$NAME" ]; then
  skip "$(tq check.registration)" "$(tq check.name_unknown)"
elif [ -x "$RN" ] && who="$("$RN" who 2>/dev/null)"; then
  registered="$(python3 "$names" registered "$NAME" 2>&1)"; rc=$?
  relay_pid="$(launchctl print "gui/$(id -u)/$LABEL" 2>/dev/null | sed -n 's/^[[:space:]]*pid = \([0-9][0-9]*\)$/\1/p' | head -n 1)"
  if [ -z "$relay_pid" ]; then
    reg="fail $(tq check.no_process)"
  else
    reg="$(python3 "$ENGINE" registered --home "$HOME/.reason/network" \
      --marker "$HOME/Library/Application Support/agent-relay/registered" --name "$PERSON/$NAME" \
      --relay-pid "$relay_pid" 2>&1)" || reg="fail $reg"
  fi
  if [ "$rc" = 0 ] && [ -n "$registered" ] && [ "$registered" != "$NAME" ]; then
    bad "$(tq check.registered_other "$PERSON/$registered")" "$(tq check.registered_other_fix "$NAME" "$PERSON/$NAME")"
  else
    case "$reg" in
      ok\ *) ok "$(tq check.listening "$PERSON/$NAME")" ;;
      wait\ *) skip "$(tq check.registration)" "${reg#wait }" ;;
      *) bad "$(tq check.not_registered "${reg#fail }")" "$(tq check.not_registered_fix)" ;;
    esac
  fi
  n=$(echo "$who" | grep -Ec 'claude-code|codex|pi |dsh')
  [ "$n" -gt 0 ] && ok "$(tq check.sessions_ok "$n")" || skip "$(tq check.sessions)" "$(tq check.sessions_none)"
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
  "confirmed ${pinned#v}") ok "$(tq check.cloud_engine_ok "${pinned#v}")" ;;
  confirmed\ *) bad "$(tq check.cloud_engine_old "${cengine#confirmed }" "${pinned#v}")" "$(tq check.cloud_engine_old_fix)" ;;
  pending\ *) skip "$(tq check.cloud_engine)" "$(tq check.cloud_engine_pending)" ;;
  none) skip "$(tq check.cloud_engine)" "$(tq check.cloud_engine_none)" ;;
  *) skip "$(tq check.cloud_engine)" "$(tq check.cloud_engine_unreadable "$LIB/deployed.json" "$cengine")" ;;
esac

echo
[ "$fail" = 0 ] && echo "RESULT ok" || echo "RESULT $(tq check.result_fail)"
exit "$fail"
