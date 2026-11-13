#!/usr/bin/env bash
# Grok Bot's side of the agent mesh, run on the cloud box only while Grok Bot is awake.
# Usage:
#   rn-cloud.sh who                      # list the team's agents (waits for the Mac to connect)
#   rn-cloud.sh send <to> [reply_to] < body.txt
#                                         # signed RN-OUT envelope -> <team-name>/<name> on the Mac, which re-sends it.
#                                         # With reply_to: records "replying" for that id before the engine sees the
#                                         # message (if that fails, nothing is sent), and "done" after "handed to
#                                         # the Mac" (if only that fails: a warning, exit 0; the id later shows as
#                                         # uncertain, never as new). With reply_to the envelope nonce is derived from
#                                         # to, reply_to and the body, so the relay drops a resend of the same file.
#                                         # "still queued on the box" is not a failure: do not resend.
#   rn-cloud.sh fetch [id...]            # doorbell wake: wait for the relay's RN-FWD copies of these message ids,
#                                         # verify, write inbox/<id>/full.txt + meta.json, claim them for this turn,
#                                         # print JSON lines (status complete|handled|claimed|uncertain|missing|
#                                         # mismatch); no ids = everything not yet fetched. Also hands out orphans
#                                         # (verified bodies whose doorbell was lost; "orphan": true).
#                                         # Ids still missing after FETCH_TIMEOUT (60 s) get one signed RN-REFWD
#                                         # request to the Mac relay (max 2 per id ever), then REFWD_TIMEOUT (90 s)
#                                         # more; results carry "refwd". REFWD=0 turns this off.
#                                         # When the Mac is connected, ends with one signed receipt (RN-GOT) to the
#                                         # relay: stored or done per id (results carry "got"); waits <=30 s for it
#                                         # to leave the box.
#   rn-cloud.sh working <id>             # record that this turn starts acting on <id> (after its duplicate check,
#                                         # before doing anything); non-zero exit = do not process it
#   rn-cloud.sh done <id>...             # record these ids as handled without a reply and send the relay a done
#                                         # receipt (RN-GOT); relay-alerts-* notice batches get no folder or marker,
#                                         # only the receipt that confirms them (non-zero exit if it was not sent)
#   rn-cloud.sh init-inbox               # (re)register <team-name>/<name>-inbox so the Mac can address it
#   rn-cloud.sh purge <days>             # only when the user asks: stop the engine, then delete received messages
#                                         # (network.db), inbox/<id>/, inbox/_stray/*.log, inbox/_mismatch/ and
#                                         # threads/*.md older than <days> (0 = all). Prints one JSON line with the
#                                         # counts.
# Environment (optional): RN_CLAIM_LEASE  seconds a claim/working/replying mark holds an id (default 3600)
#   RN_ORPHANS=0      no orphan scan          RN_ORPHAN_DAYS     orphan scan window in days (default 7)
#   RN_DONE_LOCK_WAIT seconds working/done/send wait for the inbox lock (default 300)
#   RN_GOT=0          no receipts (RN-GOT) and no "caps: got1" in RN-REFWD; also set GOT_TIMEOUT_SECONDS=0 on the relay
#   FETCH_TIMEOUT, FETCH_TIMEOUT_OFFLINE (15 s), REFWD_TIMEOUT, REFWD (see fetch)
# Names: <name> is the Bot address name in box/relay, written by rn-cloud-setup.sh (bot if the file does not exist;
# an empty or damaged file stops every command). <name>-cloud is SEND-only (registered with `listen --wait 0`, which
# marks its mail read, so nothing should ever be sent to it); <name>-inbox is RECEIVE-only (rn-fetch reads its mail
# from network.db, which keeps messages after they are marked read, so registering it never loses anything). After
# each registration the engine's session table must show exactly that name, else the command stops.
# State lives under /workspace/agent-mesh/box (survives box restarts). Nothing here runs in the background
# on purpose: the engine daemon is started on demand and dies with the box.
# The engine is box/bin/engine/reason-network; every command first checks its bytes against the builds pinned in
# rn-engine.py and stops if they differ. box/bin/reason-network is only a launcher for commands run by hand.
set -euo pipefail
die() { echo "rn-cloud: $*" >&2; exit 1; }
[ "$(uname -s)" = Linux ] || die "[BOT-CLOUD] run this on the Bot's Linux cloud computer, not the user's Mac"
command -v python3 >/dev/null || die "Python 3.9+ is required on [BOT-CLOUD]; see README prerequisites"
BASE="${RN_CLOUD_BASE:-/workspace/agent-mesh/box}"   # override only for tests
export REASON_NETWORK_HOME="$BASE/state"
export REASON_NETWORK_NO_UPDATE=1
export REASON_NETWORK_NO_PROMPT=1
export REASON_NETWORK_MACHINE="${MACHINE_NAME:-cloud}"   # same machine name as rn-cloud-setup.sh used to join
case "${1:-}" in who|init-inbox|fetch|send|working|done|purge) ;; *) sed -n '2,/^# Names:/p' "$0" | sed '$d'; exit 2 ;; esac
RN="$(python3 -I "$BASE/rn-engine.py" cloud-bin --base "$BASE")" \
  || die "the engine was not checked, so nothing was run (see the line above)"
[ -s "$BASE/person" ] || die "cloud setup is incomplete; run rn-cloud-setup.sh with the Mac invite and team name first"
PERSON="$(cat "$BASE/person")"            # written by rn-cloud-setup.sh; the same name as the Mac
BASE_NAME=bot
if [ -e "$BASE/relay" ]; then
  BASE_NAME="$(cat "$BASE/relay")" || die "cannot read $BASE/relay"
  if ! [[ "$BASE_NAME" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] || [ "${#BASE_NAME}" -gt 34 ] \
     || [[ "$BASE_NAME" =~ ^terminal(-[0-9]+)?$ ]]; then
    die "$BASE/relay is damaged; it must hold the Bot address name the Mac uses (README: Troubleshooting)"
  fi
fi
RELAY="${AGENT_RELAY_NAME:-$BASE_NAME}"
CLOUD="${RN_CLOUD_NAME:-$BASE_NAME-cloud}"
INBOX="${RN_INBOX_NAME:-$BASE_NAME-inbox}"
for n in "$CLOUD" "$INBOX"; do
  [[ "$n" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] && [ "${#n}" -le 40 ] \
    || die "invalid session name '${n:0:60}' in RN_CLOUD_NAME/RN_INBOX_NAME: lowercase letters and digits joined by single '-', at most 40 characters"
done
STRAY="${RN_INBOX_DIR:-/workspace/agent-mesh/inbox}/_stray"
export RN_BOX="$BASE" RN_PERSON="$PERSON" RN_INBOX_NAME="$INBOX" RN_KEY_FILE="$BASE/outbound.key"
export RN_BIN="$RN" RN_RELAY_NAME="$RELAY" RN_CLOUD_NAME="$CLOUD"
KEY_FILE="$BASE/outbound.key"             # same secret as OUTBOUND_KEY on the Mac, mode 0600

[ -x "$RN" ] || die "engine not installed at $RN"
[ -s "$KEY_FILE" ] || die "missing $KEY_FILE"

is_connected() {     # starts the daemon if needed and waits (<=90s) until the Mac is connected
  for _ in $(seq 1 45); do
    if "$RN" doctor 2>/dev/null | grep -E '[1-9][0-9]* connected' >/dev/null; then return 0; fi
    sleep 2
  done
  return 1
}
wait_connected() { is_connected || die "the Mac is not connected (is it awake and is reason-network running there?)"; }


check_registered() {
  local actual
  actual="$(python3 -I - "$REASON_NETWORK_HOME/network.db" "$1" <<'PY'
import sqlite3, sys
from pathlib import Path
db, name = Path(sys.argv[1]), sys.argv[2]
try:
    con = sqlite3.connect(f"{db.absolute().as_uri()}?mode=ro", uri=True, timeout=5)
    try:
        row = con.execute("SELECT name FROM sessions WHERE key = ? ORDER BY last_seen DESC LIMIT 1",
                          ("cli:" + name,)).fetchone()
    finally:
        con.close()
except sqlite3.Error as e:
    print(f"nothing (the session table cannot be read: {e})")
    sys.exit(0)
print(row[0] if row is not None else "nothing")
PY
)" || actual="nothing"
  [ "$actual" = "$1" ] \
    || die "the engine registered $actual instead of $PERSON/$1; another session may use this name (README: Troubleshooting)"
}

register() {         # makes <team-name>/$CLOUD a live sender here; anything mistakenly sent to it is kept, not dropped
  local out; out="$("$RN" listen --as "$CLOUD" --wait 0)"
  case "$out" in
    "No messages."*) ;;
    *) mkdir -p "$STRAY"; { echo "== $(date -Iseconds)"; echo "$out"; } >> "$STRAY/$CLOUD.log"
       echo "rn-cloud: note: mail sent to $PERSON/$CLOUD (send-only) saved to $STRAY/$CLOUD.log" >&2 ;;
  esac
  check_registered "$CLOUD"
}
register_inbox() {   # bodies stay in network.db for rn-fetch
  "$RN" listen --as "$INBOX" --wait 0 >/dev/null
  check_registered "$INBOX"
}

outbox_count() {
  python3 -I - "$REASON_NETWORK_HOME/network.db" <<'PY'
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
    got=""; [ "$connected" = 0 ] || got=1
    rc=0
    if [ "$#" -gt 0 ] && [ "$connected" = 1 ] && [ "${REFWD:-1}" != 0 ]; then
      register            # <name>-cloud must be a live sender here before an RN-REFWD request can go out
      python3 -I "$BASE/rn-fetch.py" fetch --timeout "$t" \
        --refwd-timeout "${REFWD_TIMEOUT:-90}" ${got:+--got} "$@" || rc=$?
    else
      python3 -I "$BASE/rn-fetch.py" wait --timeout "$t" ${got:+--got} "$@" || rc=$?
    fi
    register_inbox      # marks them read in the engine (the rows stay in network.db)
    [ "$connected" = 1 ] || echo "rn-cloud: the Mac was not connected; anything missing is still in its outbox" >&2
    exit "$rc" ;;
  send)
    to="${2:?to}"; reply_to="${3:-}"
    body="$(cat)"; [ -n "$body" ] || die "empty body"
    wait_connected; register; register_inbox
    # Mark the reply as started BEFORE the engine sees it, so a crash after this point is never redone silently.
    if [ -n "$reply_to" ] && ! python3 -I "$BASE/rn-fetch.py" replying "$reply_to" >/dev/null; then
      die "could not record $reply_to as replying; nothing was sent, so it is safe to try again"
    fi
    envelope="$(python3 -I - "$KEY_FILE" "$to" "$reply_to" "$body" <<'PY'
import hmac, hashlib, os, sys, time
key = open(sys.argv[1]).read().strip()
to, reply_to = sys.argv[2], sys.argv[3]
body = sys.argv[4].rstrip()  # the engine trims message text, so sign exactly what the Mac will see
ts = str(int(time.time()))
if reply_to:
    nonce = hmac.new(key.encode(), "\n".join(["idem1", to, reply_to, body]).encode(), hashlib.sha256).hexdigest()[:24]
else:
    nonce = os.urandom(12).hex()
sig = hmac.new(key.encode(), "\n".join(["v1", to, reply_to, ts, nonce, body]).encode(), hashlib.sha256).hexdigest()
print(f"RN-OUT v1\nto: {to}\nreply_to: {reply_to}\nts: {ts}\nnonce: {nonce}\nsig: {sig}\n---\n{body}", end="")
PY
)"
    out="$(printf '%s' "$envelope" | "$RN" send "$PERSON/$RELAY" --from "$PERSON/$CLOUD" -)" || die "send failed: $out"
    echo "$out"
    status="$(python3 -I "$BASE/rn-fetch.py" send-status <<<"$out")" || die "could not classify the engine output: $out"
    case "$status" in *refused*|*held*) die "the engine did not accept the message: $out" ;; esac
    # Don't end the turn while the message still sits in the box's outbox (the box may sleep right after).
    for _ in $(seq 1 30); do
      if [ "$(outbox_count)" = "0" ]; then
        echo "rn-cloud: handed to the Mac"
        if [ -n "$reply_to" ] && ! err="$(python3 -I "$BASE/rn-fetch.py" "done" "$reply_to" --via send 2>&1 >/dev/null)"; then
          # Already sent: exit 0 so nobody retries; the id stays "replying" and later shows as uncertain.
          echo "rn-cloud: sent, but could not record $reply_to as handled: ${err:-unknown error}" >&2
        fi
        exit 0
      fi
      sleep 2
    done
    # Not recorded as done: the id keeps its "replying" mark and later shows as uncertain.
    die "message still queued on the box; it will go out the next time the box connects. Do not resend." ;;
  working)
    [ "$#" = 2 ] && [ -n "$2" ] || die "usage: rn-cloud.sh working <id>"
    python3 -I "$BASE/rn-fetch.py" working "$2" >/dev/null || die "could not record $2 as working; do not process it now"
    echo "rn-cloud: $2 marked working" ;;
  done)
    shift
    [ "$#" -gt 0 ] || die "usage: rn-cloud.sh done <id>..."
    python3 -I "$BASE/rn-fetch.py" "done" "$@" >/dev/null || die "could not record $* as done"
    echo "rn-cloud: recorded as done: $*" ;;
  purge)
    days="${2:-}"
    [[ "$days" =~ ^[0-9]{1,5}$ ]] || die "usage: rn-cloud.sh purge <days>   (whole days; 0 = everything)"
    # rn-fetch takes the inbox lock (no fetch can overlap), then stops the engine and refuses if it still runs;
    # the daemon restarts on demand with the next who/fetch/send.
    RN_BIN="$RN" RN_INBOX_DIR="${RN_INBOX_DIR:-/workspace/agent-mesh/inbox}" \
      RN_THREADS_DIR="${RN_THREADS_DIR:-/workspace/agent-mesh/threads}" \
      python3 -I "$BASE/rn-fetch.py" purge "$days" ;;
  *) sed -n '2,/^# Names:/p' "$0" | sed '$d'; exit 2 ;;
esac
