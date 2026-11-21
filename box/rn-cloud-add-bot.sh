#!/usr/bin/env bash
# An added Grok Bot runs this on [BOT-CLOUD], the cloud computer it shares with the Bots already set up there.
#   rn-cloud-add-bot.sh <name> <team-name> <team-hash> [reclaim]
#       step 0 of the added Bot's routine prompt: checks this cloud (box/person is <team-name>, the engine is the
#       pinned build of the pinned version, it is in the team whose team_id hashes to <team-hash> (first 16 hex
#       digits of its sha256) as <team-name>/cloud, box/outbound.key is not empty, <name> is a valid Bot address
#       name, no other Bot here uses or used it, and it was not removed from this cloud), claims bots/<name>/
#       (written with .claim in a temporary folder, then renamed; only when the session table has never seen
#       <name>-cloud or <name>-inbox), registers both sessions with RN_BOT=<name> rn-cloud.sh register-bot and then
#       writes bots/<name>/.registered. With reclaim, a removed Bot's name or sessions this cloud's key already
#       holds are taken back: bots/<name>/exclude.json lists the messages held back until the user accepts them
#       (for a removed Bot's name, those box/bots-removed/<name> recorded at the removal plus any an earlier fetch
#       marked read, so mail the Mac queued for the name later reaches the Bot as new; for a name left by an earlier
#       setup, every message in <name>-inbox), and box/bots-removed/<name> goes after the registration. Running it
#       again continues an unfinished registration and only checks a finished one. Prints "Bot address:
#       <team-name>/<name> (this cloud uses <name>-cloud and <name>-inbox)".
#   rn-cloud-add-bot.sh --check <name> <req>
#       answer to the Mac's one-line request "agent-mesh add-bot <name> <req>": read-only, prints
#       "agent-mesh-result add-bot <req> ok <name> <team-hash>" when the Bot is registered and both sessions exist,
#       after "agent-mesh-result held add <count>" when earlier messages are held back.
# Every refusal prints "STOP: <reason>" and changes nothing. Registration holds the cloud maintenance lock
# (/workspace/agent-mesh/.maint.lock) exclusively, --check holds it shared; RN_MAINT_WAIT sets the wait (default 600 seconds).
set -euo pipefail
stop() { echo "STOP: $*"; exit 1; }
usage() { echo "usage: rn-cloud-add-bot.sh <name> <team-name> <team-hash> [reclaim] | --check <name> <req>" >&2; exit 2; }
BASE="${RN_CLOUD_BASE:-/workspace/agent-mesh/box}"
ROOT="$(dirname "$BASE")"
BOTS="$ROOT/bots"
if [ "${1:-}" = --check ]; then
  [ $# = 3 ] || usage
  MODE=check; NAME="$2"; REQ="$3"; LOCK=shared
else
  [ $# = 3 ] || { [ $# = 4 ] && [ "$4" = reclaim ]; } || usage
  MODE=register; NAME="$1"; TEAM="$2"; HASH="$3"; LOCK=exclusive; RECLAIM="${4:-}"
fi
[ "$(uname -s)" = Linux ] || stop "[BOT-CLOUD] run this on the Bot's Linux cloud computer, not the user's Mac"
command -v python3 >/dev/null || stop "Python 3.9+ is required on [BOT-CLOUD]"
valid_name() { [[ "$1" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] && [ "${#1}" -le 34 ] && ! [[ "$1" =~ ^terminal(-[0-9]+)?$ ]]; }
valid_name "$NAME" \
  || stop "invalid Bot address name '${NAME:0:60}': 1-34 lowercase letters and digits, groups joined by a single '-', not terminal; nothing was changed"
if [ "$MODE" = check ]; then
  [[ "$REQ" =~ ^[0-9a-f]{8,32}$ ]] || stop "the request id must be 8 to 32 lowercase hex characters"
else
  [[ "$TEAM" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] && [ "${#TEAM}" -le 40 ] \
    || stop "invalid team name '${TEAM:0:60}'; nothing was changed"
  [[ "$HASH" =~ ^[0-9a-f]{16}$ ]] || stop "the team hash must be 16 lowercase hex characters; nothing was changed"
fi
[ -f "$BASE/rn-engine.py" ] && [ -f "$BASE/rn-cloud.sh" ] \
  || stop "no agent-mesh clone at $ROOT; nothing was changed"
if [ -z "${RN_MAINT_FD:-}" ]; then
  exec python3 -I "$BASE/rn-engine.py" hold --"$LOCK" --wait "${RN_MAINT_WAIT:-600}" "$ROOT/.maint.lock" -- "$BASH" "$0" "$@"
fi
python3 -I "$BASE/rn-engine.py" held --"$LOCK" "$ROOT/.maint.lock" \
  || stop "the cloud maintenance lock this command inherited is not enough (see the line above); nothing was changed"
export REASON_NETWORK_HOME="$BASE/state" REASON_NETWORK_NO_UPDATE=1 REASON_NETWORK_NO_PROMPT=1 REASON_NETWORK_MACHINE=cloud
engine() { python3 -I "$BASE/rn-engine.py" "$@"; }
D="$BOTS/$NAME"

sessions() {
  python3 -I - "$BASE/state/network.db" "$NAME-cloud" "$NAME-inbox" <<'PY'
import sqlite3, sys
from pathlib import Path
db, names = Path(sys.argv[1]), sys.argv[2:]
try:
    con = sqlite3.connect(f"{db.absolute().as_uri()}?mode=ro", uri=True, timeout=5)
    try:
        for name in names:
            row = con.execute("SELECT name FROM sessions WHERE key = ? ORDER BY last_seen DESC LIMIT 1",
                              ("cli:" + name,)).fetchone()
            other = con.execute("SELECT key FROM sessions WHERE name = ? AND key != ? LIMIT 1",
                                (name, "cli:" + name)).fetchone()
            print(name, row[0] if row else "-", "taken" if other else "free")
    finally:
        con.close()
except sqlite3.Error as e:
    print(f"the session table cannot be read: {e}", file=sys.stderr)
    sys.exit(2)
PY
}

both_registered() {
  local table n have
  table="$(sessions)" || return 1
  while read -r n have _; do
    [ "$have" = "$n" ] || return 1
  done <<<"$table"
}

claim_hash() { awk '$1 == "team" { print $2; exit }' "$D/.claim" 2>/dev/null; }

live_team() {
  local facts
  facts="$(engine team-facts --home "$BASE/state" --bin "$RN")" \
    || stop "reason-network team --json did not show this cloud in a team (see the line above); nothing was changed"
  TEAM_HASH="${facts%% *}"; ME="${facts#* }"
}

engine_ok() {
  local want build payload="$BASE/bin/engine/reason-network"
  [ -e "$payload" ] || [ -L "$payload" ] \
    || stop "no engine at box/bin/engine/reason-network; the cloud update must finish first (nothing was changed)"
  want="$(sed -n 's/^VERSION="v\([0-9][0-9.]*\)"$/\1/p' "$BASE/rn-cloud-setup.sh" 2>/dev/null | head -n 1)"
  [ -n "$want" ] || stop "cannot read the pinned engine version from box/rn-cloud-setup.sh; nothing was changed"
  build="$(engine build --bin "$payload")" \
    || stop "box/bin/engine/reason-network is not a Reason Network build pinned by agent-mesh; stop and tell the user (nothing was changed)"
  [ "${build%% *}" = "$want" ] \
    || stop "this cloud runs Reason Network ${build%% *}, but this release needs $want; the cloud update must finish first (nothing was changed)"
  RN="$(engine cloud-bin --base "$BASE")" || stop "the engine was not checked (see the line above); nothing was changed"
}

for p in "$BOTS" "$D" "$BASE/bots-removed" "$BASE/bots-removed/$NAME"; do
  [ ! -L "$p" ] || stop "$p is a symlink; not touching it"
done
removed=no
if [ -e "$BASE/bots-removed/$NAME" ]; then removed=yes; fi

if [ "$MODE" = check ]; then
  [ "$removed" = no ] || stop "the Bot $NAME was removed from this cloud"
  [ -s "$BASE/person" ] || stop "cloud setup is incomplete (no box/person)"
  PERSON="$(cat "$BASE/person")"
  [ -f "$D/.registered" ] || stop "the Bot $NAME is not registered on this cloud yet (no bots/$NAME/.registered)"
  both_registered || stop "the session table of this cloud does not show both $NAME-cloud and $NAME-inbox"
  held=0
  if [ -e "$D/exclude.json" ]; then
    held="$(python3 -I "$BASE/rn-names.py" held "$NAME" add)" || stop "bots/$NAME/exclude.json cannot be read: $held"
  fi
  [ -x "$BASE/bin/engine/reason-network" ] || stop "no engine at box/bin/engine/reason-network"
  RN="$(engine cloud-bin --base "$BASE")" || stop "the engine was not checked (see the line above)"
  live_team
  [ "$ME" = "$PERSON/cloud" ] || stop "this computer is $ME in the team, not $PERSON/cloud"
  [ "$TEAM_HASH" = "$(claim_hash)" ] || stop "bots/$NAME was claimed for another team than the one this cloud is in"
  [ "$held" = 0 ] || echo "agent-mesh-result held add $held"
  echo "agent-mesh-result add-bot $REQ ok $NAME $TEAM_HASH"
  exit 0
fi

[ -s "$BASE/person" ] || stop "cloud setup is incomplete (no box/person); nothing was changed"
have="$(cat "$BASE/person")"
[ "$have" = "$TEAM" ] || stop "box/person is ${have:0:60}, not $TEAM; this cloud belongs to another deployment (nothing was changed)"
[ -s "$BASE/outbound.key" ] || stop "box/outbound.key is missing or empty; the cloud setup must finish first (nothing was changed)"
engine_ok
live_team
[ "$ME" = "$TEAM/cloud" ] || stop "this computer is $ME in the team, not $TEAM/cloud; nothing was changed"
[ "$TEAM_HASH" = "$HASH" ] \
  || stop "this cloud is in another team (team hash $TEAM_HASH, the Mac expects $HASH); nothing was changed"
first=""
if [ -e "$BASE/relay" ]; then
  first="$(cat "$BASE/relay")" || stop "cannot read box/relay; nothing was changed"
  valid_name "$first" || stop "box/relay is damaged; ask the user (nothing was changed)"
elif ! [ -e "$BASE/relay.removed" ]; then
  first=bot
fi
[ "$NAME" != "$first" ] || stop "$NAME is the first Bot on this cloud (box/relay); choose another name (nothing was changed)"
renamed=no
for f in "$BASE/former" "$BASE/rename.pending" "$BOTS"/*/name "$BOTS"/*/former "$BOTS"/*/rename.pending; do
  if [ -e "$f" ] || [ -L "$f" ]; then renamed=yes; fi
done
if [ "$renamed" = yes ]; then
  holder="$(python3 -I "$BASE/rn-names.py" owner "$NAME")" || stop "the Bot names on this cloud cannot be read: ${holder#STOP: }"
  [ -z "$holder" ] || stop "$NAME is an address name of $holder on this cloud; choose another name (nothing was changed)"
fi
if [ "$removed" = yes ] && [ "$RECLAIM" != reclaim ]; then
  stop "the Bot $NAME was removed from this cloud; its name can be taken back only when the Mac confirms it (./install.sh --add-bot shows that step). Nothing was changed"
fi
taking=no

if [ -e "$D" ]; then
  [ -d "$D" ] || stop "bots/$NAME is not a folder; ask the user (nothing was changed)"
  [ -f "$D/.claim" ] || stop "bots/$NAME exists but was not created by rn-cloud-add-bot.sh (no .claim); ask the user (nothing was changed)"
  [ "$(claim_hash)" = "$HASH" ] || stop "bots/$NAME was claimed for another team; ask the user (nothing was changed)"
  if [ "$removed" = yes ] && [ ! -f "$D/.reclaim" ]; then
    stop "removing the Bot $NAME from this cloud did not finish (bots/$NAME is still there); finish uninstalling it first (nothing was changed)"
  fi
  if [ -f "$D/.registered" ]; then
    both_registered || stop "bots/$NAME is registered, but the session table does not show both $NAME-cloud and $NAME-inbox; ask the user"
    if [ "$removed" = yes ]; then rm -f "$BASE/bots-removed/$NAME"; fi
    echo "Bot address: $TEAM/$NAME (this cloud uses $NAME-cloud and $NAME-inbox)"
    exit 0
  fi
  if [ -f "$D/.reclaim" ]; then taking=yes; fi
  echo "add-bot: continuing the unfinished registration of $NAME"
else
  table="$(sessions)" || stop "the session table cannot be read, so $NAME cannot be checked; nothing was changed"
  while read -r n have taken; do
    [ "$taken" = free ] && { [ "$have" = - ] || [ "$have" = "$n" ]; } \
      || stop "the session $TEAM/$n on this cloud is used by another key; choose another name. Nothing was registered"
    if [ "$have" != - ]; then
      [ "$RECLAIM" = reclaim ] \
        || stop "the session $TEAM/$n already exists on this cloud (an earlier Bot used this name); choose another name, or take it back when ./install.sh --add-bot offers it. Nothing was registered"
      taking=yes
    fi
  done <<<"$table"
  if [ "$removed" = yes ]; then taking=yes; fi
fi

for t in "$BOTS"/.tmp-*; do
  if [ -d "$t" ] && [ ! -L "$t" ]; then rm -rf "${t:?}"; fi
done
mkdir -p "$BOTS"
chmod 700 "$BOTS"
if [ ! -d "$D" ]; then
  tmp="$(mktemp -d "$BOTS/.tmp-XXXXXX")" || stop "cannot create a folder in $BOTS; nothing was changed"
  chmod 700 "$tmp"
  printf 'team %s\ntime %s\n' "$HASH" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$tmp/.claim"
  if [ "$taking" = yes ]; then
    out="$(python3 -I "$BASE/rn-names.py" claim-rows "$tmp" "$NAME")" \
      || stop "the earlier messages of $NAME could not be listed (${out#STOP: }); nothing was registered"
    printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$tmp/.reclaim"
  fi
  mv "$tmp" "$D" || stop "could not rename the claim folder to bots/$NAME"
fi
mkdir -p "$D/inbox" "$D/threads"
chmod 700 "$D" "$D/inbox" "$D/threads"
reclaiming=""
if [ "$taking" = yes ]; then reclaiming=1; fi
env -u AGENT_RELAY_NAME -u RN_CLOUD_NAME -u RN_INBOX_NAME -u RN_INBOX_DIR -u RN_THREADS_DIR \
  RN_BOT="$NAME" RN_RECLAIMING="$reclaiming" "$BASH" "$BASE/rn-cloud.sh" register-bot \
  || stop "registering $NAME-cloud and $NAME-inbox did not finish (see the lines above); run this again to continue"
both_registered || stop "the session table does not show both $NAME-cloud and $NAME-inbox after registering; run this again to continue"
printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$D/.registered.tmp"
mv -f "$D/.registered.tmp" "$D/.registered"
if [ "$removed" = yes ]; then rm -f "$BASE/bots-removed/$NAME"; fi
echo "Bot address: $TEAM/$NAME (this cloud uses $NAME-cloud and $NAME-inbox)"
