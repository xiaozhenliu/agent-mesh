#!/usr/bin/env bash
# Grok Bot's side of the agent mesh, run on the cloud box only while Grok Bot is awake.
# Usage:
#   rn-cloud.sh who                      # list the team's agents (waits for the Mac to connect)
#   rn-cloud.sh send <to> [reply_to] < body.txt
#                                         # signed RN-OUT envelope -> <team-name>/bot on the Mac, which re-sends it
#   rn-cloud.sh fetch [id...]            # doorbell wake: wait for the relay's RN-FWD copies of these message ids,
#                                         # verify, write inbox/<id>/full.txt + meta.json, print JSON lines
#                                         # (status complete|missing|mismatch); no ids = everything not yet fetched.
#                                         # Ids still missing after FETCH_TIMEOUT (60 s) get one signed RN-REFWD
#                                         # request to the Mac relay (max 2 per id ever), then REFWD_TIMEOUT (90 s)
#                                         # more; results carry "refwd". REFWD=0 turns this off.
#   rn-cloud.sh init-inbox               # (re)register <team-name>/bot-inbox so the Mac can address it
# Names: bot-cloud is SEND-only (registered with `listen --wait 0`, which marks its mail read, so nothing
# should ever be sent to it); bot-inbox is RECEIVE-only (rn-fetch reads its mail from network.db, which keeps
# messages after they are marked read, so registering it never loses anything).
# State lives under /workspace/agent-mesh/box (survives box restarts). Nothing here runs in the background
# on purpose: the engine daemon is started on demand and dies with the box.
set -euo pipefail
BASE="${RN_CLOUD_BASE:-/workspace/agent-mesh/box}"   # override only for tests
export REASON_NETWORK_HOME="$BASE/state"
export REASON_NETWORK_NO_PROMPT=1
export REASON_NETWORK_MACHINE="${MACHINE_NAME:-cloud}"   # same machine name as rn-cloud-setup.sh used to join
RN="$BASE/bin/reason-network"
PERSON="$(cat "$BASE/person")"            # written by rn-cloud-setup.sh; the same name as the Mac
CLOUD="${RN_CLOUD_NAME:-bot-cloud}"
INBOX="${RN_INBOX_NAME:-bot-inbox}"
STRAY="${RN_INBOX_DIR:-/workspace/agent-mesh/inbox}/_stray"
export RN_BOX="$BASE" RN_PERSON="$PERSON" RN_INBOX_NAME="$INBOX" RN_KEY_FILE="$BASE/outbound.key"
KEY_FILE="$BASE/outbound.key"             # same secret as OUTBOUND_KEY on the Mac, mode 0600

die() { echo "rn-cloud: $*" >&2; exit 1; }
[ -x "$RN" ] || die "engine not installed at $RN"
[ -s "$KEY_FILE" ] || die "missing $KEY_FILE"

is_connected() {     # starts the daemon if needed and waits (<=90s) until the Mac is connected
  for _ in $(seq 1 45); do
    if "$RN" doctor 2>/dev/null | grep -Eq '[1-9][0-9]* connected'; then return 0; fi
    sleep 2
  done
  return 1
}
wait_connected() { is_connected || die "the Mac is not connected (is it awake and is reason-network running there?)"; }

register() {         # makes <team-name>/$CLOUD a live sender here; anything mistakenly sent to it is kept, not dropped
  local out; out="$("$RN" listen --as "$CLOUD" --wait 0)"
  case "$out" in
    "No messages."*) ;;
    *) mkdir -p "$STRAY"; { echo "== $(date -Iseconds)"; echo "$out"; } >> "$STRAY/$CLOUD.log"
       echo "rn-cloud: note: mail sent to $PERSON/$CLOUD (send-only) saved to $STRAY/$CLOUD.log" >&2 ;;
  esac
}
register_inbox() { "$RN" listen --as "$INBOX" --wait 0 >/dev/null; }   # bodies stay in network.db for rn-fetch

outbox_count() {
  python3 - "$REASON_NETWORK_HOME/network.db" <<'PY'
import sqlite3, sys
print(sqlite3.connect(sys.argv[1]).execute("select count(*) from outbox").fetchone()[0])
PY
}

case "${1:-}" in
  who)
    wait_connected; register_inbox; "$RN" who ;;
  init-inbox)
    wait_connected; register_inbox; sleep 3
    echo "rn-cloud: $PERSON/$INBOX registered on this box (the Mac learns it from presence)" ;;
  fetch)
    shift; connected=1
    is_connected || connected=0
    register_inbox
    t="${FETCH_TIMEOUT:-60}"; [ "$connected" = 1 ] || t="${FETCH_TIMEOUT_OFFLINE:-15}"
    rc=0
    if [ "$#" -gt 0 ] && [ "$connected" = 1 ] && [ "${REFWD:-1}" != 0 ]; then
      register            # bot-cloud must be a live sender here before an RN-REFWD request can go out
      RN_BIN="$RN" RN_CLOUD_NAME="$CLOUD" RN_RELAY_NAME="${AGENT_RELAY_NAME:-bot}" \
        python3 "$BASE/rn-fetch.py" fetch --timeout "$t" --refwd-timeout "${REFWD_TIMEOUT:-90}" "$@" || rc=$?
    else
      python3 "$BASE/rn-fetch.py" wait --timeout "$t" "$@" || rc=$?
    fi
    register_inbox      # marks them read in the engine (the rows stay in network.db)
    [ "$connected" = 1 ] || echo "rn-cloud: the Mac was not connected; anything missing is still in its outbox" >&2
    exit "$rc" ;;
  send)
    to="${2:?to}"; reply_to="${3:-}"
    body="$(cat)"; [ -n "$body" ] || die "empty body"
    wait_connected; register; register_inbox
    envelope="$(python3 - "$KEY_FILE" "$to" "$reply_to" "$body" <<'PY'
import hmac, hashlib, os, sys, time
key = open(sys.argv[1]).read().strip()
to, reply_to = sys.argv[2], sys.argv[3]
body = sys.argv[4].rstrip()  # the engine trims message text, so sign exactly what the Mac will see
ts, nonce = str(int(time.time())), os.urandom(12).hex()
sig = hmac.new(key.encode(), "\n".join(["v1", to, reply_to, ts, nonce, body]).encode(), hashlib.sha256).hexdigest()
print(f"RN-OUT v1\nto: {to}\nreply_to: {reply_to}\nts: {ts}\nnonce: {nonce}\nsig: {sig}\n---\n{body}", end="")
PY
)"
    out="$(printf '%s' "$envelope" | "$RN" send "$PERSON/bot" --from "$PERSON/$CLOUD" -)" || die "send failed: $out"
    echo "$out"
    case "$out" in *refused*|*held*) die "the engine did not accept the message: $out" ;; esac
    # Don't end the turn while the message still sits in the box's outbox (the box may sleep right after).
    for _ in $(seq 1 30); do
      [ "$(outbox_count)" = "0" ] && { echo "rn-cloud: handed to the Mac"; exit 0; }
      sleep 2
    done
    die "message still queued on the box after 60s; it goes out the next time the box is awake and connected" ;;
  *) sed -n 2,14p "$0"; exit 2 ;;
esac
