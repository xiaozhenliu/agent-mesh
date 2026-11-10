#!/usr/bin/env bash
# Grok Bot runs this ONCE on the box, after the user approves, with the link from `reason-network invite --me` on the Mac.
#   rn-cloud-setup.sh '<link>' <team-name> [relay-name]   # the names you gave install.sh --name and --relay-name
#                                                         # on the Mac; relay-name defaults to bot
# Installs the engine under /workspace/agent-mesh/box (persistent), joins as the user's second machine, no agent
# wiring, no service, auto-update/LAN/tray off. Records the team name in box/person and the Bot address name in
# box/relay. Running it again with the same invite and names is safe; the Bot address name cannot be changed.
# Nothing stays running: the daemon starts on demand while Grok Bot is awake.
set -euo pipefail
LINK="${1:?team link}"; PERSON="${2:?team/person name from the Mac is required}"; RELAY_NAME="${3:-bot}"
MACHINE_NAME="${MACHINE_NAME:-cloud}"           # shown in the roster instead of the box hostname
VERSION="v0.10.7"
BASE="${RN_CLOUD_BASE:-/workspace/agent-mesh/box}"
die() { echo "cloud-setup: $*" >&2; exit 1; }
valid_relay_name() { [[ "$1" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] && [ "${#1}" -le 34 ] && ! [[ "$1" =~ ^terminal(-[0-9]+)?$ ]]; }
[ "$(uname -s)" = Linux ] || die "[BOT-CLOUD] requires Linux; do not run this on the user's Mac"
case "$(uname -m)" in
  x86_64) t=x86_64-unknown-linux-musl ;;
  aarch64|arm64) t=aarch64-unknown-linux-musl ;;
  *) die "unsupported cloud CPU $(uname -m); requires x86_64 or ARM64" ;;
esac
for tool in python3 curl tar sha256sum; do
  command -v "$tool" >/dev/null || die "missing $tool on [BOT-CLOUD]; install it with the cloud system's package manager before retrying"
done
python3 -c 'import sys, ssl, sqlite3; sys.exit(sys.version_info < (3, 9))' \
  || die "[BOT-CLOUD] requires Python 3.9+ with ssl and sqlite3"
[[ "$PERSON" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] && [ "${#PERSON}" -le 40 ] \
  || die "invalid team/person name: use the team name the Mac installer printed (lowercase letters and digits, single '-')"
valid_relay_name "$RELAY_NAME" \
  || die "invalid Bot address name '${RELAY_NAME:0:60}': use the name the Mac installer printed (1-34 lowercase letters and digits, groups joined by a single '-', not terminal)"
[ "$MACHINE_NAME" = cloud ] || die "this deployment requires machine name cloud to match the Mac relay"
[ "$(cd "$(dirname "$0")" && pwd)" = "$BASE" ] \
  || die "clone the public distribution at /workspace/agent-mesh first; cloud prompts require that persistent path"
if [ -f "$BASE/person" ] && [ "$(cat "$BASE/person")" != "$PERSON" ]; then
  die "this cloud directory belongs to another person; review the existing deployment before changing it"
fi

used_name() {
  python3 - "$BASE/state/network.db" "$1" <<'PY'
import sqlite3, sys
from pathlib import Path
db, name = Path(sys.argv[1]), sys.argv[2]
try:
    con = sqlite3.connect(f"{db.absolute().as_uri()}?mode=ro", uri=True, timeout=5)
    try:
        row = con.execute("SELECT 1 FROM sessions WHERE key IN (?, ?) LIMIT 1",
                          (f"cli:{name}-inbox", f"cli:{name}-cloud")).fetchone()
    finally:
        con.close()
except sqlite3.Error:
    row = None
sys.exit(0 if row else 1)
PY
}

damaged=""
if [ -e "$BASE/relay" ]; then
  old="$(cat "$BASE/relay")" || die "cannot read $BASE/relay"
  if valid_relay_name "$old"; then
    [ "$old" = "$RELAY_NAME" ] \
      || die "this cloud directory uses the Bot address name $old; renaming is not supported (README: Troubleshooting)"
  else
    damaged=1
    echo "cloud-setup: box/relay is damaged ($(printf '%q' "${old:0:60}")); treating it as missing" >&2
  fi
fi
if { [ ! -e "$BASE/relay" ] || [ -n "$damaged" ]; } && [ -f "$BASE/person" ] && [ "$RELAY_NAME" != bot ]; then
  used_name "$RELAY_NAME" \
    || die "this cloud was set up with the default name bot or the name cannot be proven; renaming is not supported (README: Troubleshooting)"
fi

export REASON_NETWORK_HOME="$BASE/state" REASON_NETWORK_NO_PROMPT=1 REASON_NETWORK_MACHINE="$MACHINE_NAME"
mkdir -p "$BASE/bin" "$BASE/state" || die "[BOT-CLOUD] /workspace/agent-mesh must be writable and persistent"
chmod 700 "$BASE/state"
base="https://github.com/reason-machines/reason-network/releases/download/$VERSION"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
curl -fsSL "$base/reason-network-$t.tar.gz" -o "$tmp/e.tgz"
curl -fsSL "$base/reason-network-$t.tar.gz.sha256" -o "$tmp/sum"
[ "$(cut -d' ' -f1 "$tmp/sum")" = "$(sha256sum "$tmp/e.tgz" | cut -d' ' -f1)" ] || { echo "checksum mismatch"; exit 1; }
tar xzf "$tmp/e.tgz" -C "$BASE/bin"
RN="$BASE/bin/reason-network"
"$RN" stop >/dev/null 2>&1 || true   # a daemon started without REASON_NETWORK_MACHINE would use the hostname

decision="$(python3 - "$LINK" "$BASE/state/network.db" <<'PY'
import base64, binascii, json, sqlite3, sys
from pathlib import Path
link, db = sys.argv[1], Path(sys.argv[2])


def joined():
    if not db.exists():
        return "none"
    try:
        con = sqlite3.connect(f"{db.absolute().as_uri()}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("SELECT value FROM kv WHERE key = 'team'").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    if row is None:
        return "none"
    try:
        team = json.loads(row[0]).get("id")
    except (ValueError, AttributeError):
        return None
    return team.lower() if isinstance(team, str) and team else None


def invited():
    ticket = link.rsplit("#", 1)[-1].strip()
    if not ticket.startswith("tincan1"):
        return None
    raw = ticket[len("tincan1"):]
    try:
        data = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        return None
    return data[:16].hex() if len(data) >= 16 else None


team = joined()
if team == "none":
    print("none")
elif team is None or invited() is None:
    print("unknown")
else:
    print("same" if invited() == team else "different")
PY
)" || decision=unknown
skip_join=""
if [ "$decision" = same ]; then
  team_out="$("$RN" team 2>&1)" || team_out=""
  me="$(awk '/this machine$/ { print $1 "/" $2; exit }' <<<"$team_out")"
  if [ "$me" = "$PERSON/$MACHINE_NAME" ]; then
    skip_join=1
    echo "already joined this team as $PERSON/$MACHINE_NAME; the invite is not used"
  elif [ -n "$me" ]; then
    die "this cloud already joined the team in the invite, but as $me, not $PERSON/$MACHINE_NAME; review the existing deployment before changing it"
  else
    decision=unknown
  fi
fi
if [ -z "$skip_join" ]; then
  if ! out="$("$RN" join "$LINK" --as "$PERSON" --no-setup 2>&1)"; then
    case "$decision" in
      different) die "this cloud is already in a different team (its team id does not match the invite); see README: Troubleshooting. The engine said: $out" ;;
      unknown) die "this cloud is already in a team and the setup could not confirm it is the team in the invite; run again with the full invite link (the one containing tincan1...) or see README: Troubleshooting. The engine said: $out" ;;
      *) die "joining the team failed. The engine said: $out" ;;
    esac
  fi
  [ -z "$out" ] || printf '%s\n' "$out"
fi

put_file() {
  local file
  file="$(mktemp "$BASE/.$1.XXXXXX")" || die "cannot create a temporary file in $BASE"
  if ! { printf '%s\n' "$2" > "$file" && chmod 644 "$file" && mv -f "$file" "$BASE/$1"; }; then
    rm -f "$file"
    die "could not write $BASE/$1"
  fi
}
rm -f "$BASE"/.relay.?????? "$BASE"/.person.??????
if [ -n "$damaged" ]; then
  cp -p "$BASE/relay" "$BASE/relay.damaged-$(date -u +%Y%m%dT%H%M%SZ)" || die "could not keep a copy of the damaged $BASE/relay"
fi
put_file relay "$RELAY_NAME"
put_file person "$PERSON"
CFG="$BASE/state/config.toml"
for kv in "auto_update = false" "lan = false" "tray = false"; do
  k="${kv%% *}"; if grep -q "^$k " "$CFG"; then sed -i "s/^$k = .*/$kv/" "$CFG"; else echo "$kv" >> "$CFG"; fi
done
"$RN" stop >/dev/null 2>&1 || true
"$RN" doctor
echo "Bot address: $PERSON/$RELAY_NAME (this cloud uses $RELAY_NAME-cloud and $RELAY_NAME-inbox)"
echo "Now copy the Mac's ~/.config/agent-relay/outbound.key to $BASE/outbound.key (CopyToBox) and chmod 600 it."
