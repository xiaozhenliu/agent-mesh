#!/usr/bin/env bash
# Grok Bot runs this ONCE on the box, after the user approves, with the link from `reason-network invite --me` on the Mac.
#   rn-cloud-setup.sh '<link>' <team-name> [relay-name]   # the names you gave install.sh --name and --relay-name
#                                                         # on the Mac; relay-name defaults to bot
# Installs the engine at /workspace/agent-mesh/box/bin/engine (persistent) with a launcher at box/bin/reason-network
# for commands run by hand, joins as the user's second machine, no agent wiring, no service, auto-update/LAN/tray off.
# Records the team name in box/person and the Bot address name in
# box/relay. Running it again with the same invite and names is safe; the Bot address name cannot be changed.
# Nothing stays running: the daemon starts on demand while Grok Bot is awake.
#   rn-cloud-setup.sh --upgrade-engine   # after an update: install the pinned engine version on a joined cloud.
#                                        # Backs up the binary and box/state, replaces the binary, restarts the
#                                        # daemon and checks it runs the new version; on failure only the old binary
#                                        # is put back. The same version again only checks the running daemon.
#                                        # An engine still at box/bin/reason-network is moved to box/bin/engine. An
#                                        # engine whose bytes are not a pinned build is replaced without being run
#                                        # (kept in the backup, never put back).
# The engine download is checked against the sha256 written below (the same table is in install.sh), and the binary
# in it against the pinned builds in rn-engine.py.
set -euo pipefail
die() { echo "cloud-setup: $*" >&2; exit 1; }
MODE=setup
if [ "${1:-}" = --upgrade-engine ]; then
  [ $# = 1 ] || die "--upgrade-engine takes no other arguments"
  MODE=upgrade; LINK=""; PERSON=""; RELAY_NAME=bot
else
  LINK="${1:?team link}"; PERSON="${2:?team/person name from the Mac is required}"; RELAY_NAME="${3:-bot}"
fi
MACHINE_NAME="${MACHINE_NAME:-cloud}"           # shown in the roster instead of the box hostname
VERSION="v0.12.17"
BASE="${RN_CLOUD_BASE:-/workspace/agent-mesh/box}"
RN="$BASE/bin/engine/reason-network"
LAUNCHER="$BASE/bin/reason-network"
engine() { python3 -I "$BASE/rn-engine.py" "$@"; }

pinned_sha256() {   # the same table is in install.sh; change both with VERSION
  case "$1" in
    v0.12.17/aarch64-apple-darwin) echo 6676e806abba74b313ee027b5b4020634077b0ef4c01c0a9eb4dd61ec77ea705 ;;
    v0.12.17/x86_64-apple-darwin) echo e71fbd5c7c080048c1220025e2aa716c3af6b18901f18e5beece1323cfcdd96f ;;
    v0.12.17/aarch64-unknown-linux-musl) echo e53ef46ee57eb9e9cd3b13d45acfe5ce4766c18861b46926560e34903adaa151 ;;
    v0.12.17/x86_64-unknown-linux-musl) echo 2234527ff9330c78bff8581cc060bf1396e15d8091c27fb5acdb989010f5018f ;;
    *) return 1 ;;
  esac
}

fetch_engine() {   # $1: empty directory; leaves the verified binary at $1/reason-network
  local want got
  want="$(pinned_sha256 "$VERSION/$t")" || die "no pinned sha256 for Reason Network $VERSION on $t"
  curl -fsSL "https://github.com/reason-machines/reason-network/releases/download/$VERSION/reason-network-$t.tar.gz" \
    -o "$1/e.tgz" || die "could not download Reason Network $VERSION; nothing was changed"
  got="$(sha256sum "$1/e.tgz" | cut -d' ' -f1)"
  [ "$got" = "$want" ] || die "checksum mismatch for reason-network-$t.tar.gz (expected $want, got $got); nothing was changed. Run again to download it again; if it keeps failing, stop and tell the user"
  tar --no-same-owner -xzf "$1/e.tgz" -C "$1" reason-network || die "the downloaded archive has no reason-network binary; nothing was changed"
  rm -f "$1/e.tgz"
  chmod 755 "$1/reason-network"
  [ "$("$1/reason-network" --version 2>&1 | awk '{print $2}')" = "${VERSION#v}" ] \
    || die "the downloaded binary does not report Reason Network ${VERSION#v}; nothing was changed"
  [ "$(engine build --bin "$1/reason-network" | cut -d' ' -f1)" = "${VERSION#v}" ] \
    || die "the downloaded binary is not the pinned Reason Network ${VERSION#v} build; nothing was changed"
}

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
python3 -I -c 'import sys, ssl, sqlite3; sys.exit(sys.version_info < (3, 9))' \
  || die "[BOT-CLOUD] requires Python 3.9+ with ssl and sqlite3"
if [ "$MODE" = setup ]; then
  [[ "$PERSON" =~ ^[a-z0-9]+(-[a-z0-9]+)*$ ]] && [ "${#PERSON}" -le 40 ] \
    || die "invalid team/person name: use the team name the Mac installer printed (lowercase letters and digits, single '-')"
fi
valid_relay_name "$RELAY_NAME" \
  || die "invalid Bot address name '${RELAY_NAME:0:60}': use the name the Mac installer printed (1-34 lowercase letters and digits, groups joined by a single '-', not terminal)"
[ "$MACHINE_NAME" = cloud ] || die "this deployment requires machine name cloud to match the Mac relay"
[ "$(cd "$(dirname "$0")" && pwd)" = "$BASE" ] \
  || die "clone the public distribution at /workspace/agent-mesh first; cloud prompts require that persistent path"

joined_team() {
  python3 -I - "$BASE/state/network.db" <<'PY'
import sqlite3, sys
from pathlib import Path
db = Path(sys.argv[1])
try:
    con = sqlite3.connect(f"{db.absolute().as_uri()}?mode=ro", uri=True, timeout=5)
    try:
        row = con.execute("SELECT value FROM kv WHERE key = 'team'").fetchone()
    finally:
        con.close()
except sqlite3.Error:
    row = None
sys.exit(0 if row else 1)
PY
}

report_engine() {
  local build strays
  build="$(engine build --bin "$RN")" || die "$RN is not a pinned Reason Network build; stop and tell the user"
  echo "engine: pinned build ${build#* } at box/bin/engine/reason-network"
  engine launcher --path "$LAUNCHER" || die "could not write the launcher to $LAUNCHER"
  strays="$(engine strays --bin-dir "$BASE/bin" --home "$BASE/state")" || strays=""
  if [ -n "$strays" ]; then
    echo "engine: note: other engine daemons run from box/bin (they cannot replace the pinned engine):"
    printf '%s\n' "$strays" | sed 's/^/engine: note:   /'
    echo "engine: note: stop each one that is not needed with REASON_NETWORK_HOME=<its state> REASON_NETWORK_NO_UPDATE=1 $RN stop"
  fi
}

upgrade_engine() {
  [ -f "$BASE/person" ] && [ -f "$BASE/state/network.db" ] && joined_team \
    || die "this cloud has not joined a team yet; run the setup with the invite first (nothing was changed)"
  export REASON_NETWORK_HOME="$BASE/state" REASON_NETWORK_NO_UPDATE=1 REASON_NETWORK_NO_PROMPT=1 \
    REASON_NETWORK_MACHINE="$MACHINE_NAME"
  local cur old_bin="" untrusted="" build have state backup base n rc=0
  if [ -e "$RN" ] || [ -L "$RN" ]; then cur="$RN"; else cur="$LAUNCHER"; old_bin="$LAUNCHER"; fi
  [ -e "$cur" ] || [ -L "$cur" ] || die "no engine at $cur; run the setup with the invite first (nothing was changed)"
  if build="$(engine build --bin "$cur")"; then
    have="${build%% *}"
    state="$(engine compare "$have" "${VERSION#v}")"
    case "$state" in
      newer|unknown) die "this cloud has Reason Network ${have:-of an unknown version}; this release supports ${VERSION#v} and never downgrades or replaces it (nothing was changed)" ;;
      same)
        if [ -z "$old_bin" ]; then
          [ -n "$(engine pid --home "$BASE/state")" ] || "$RN" team >/dev/null 2>&1 || true
          case "$(engine info --home "$BASE/state" --bin "$RN")" in
            *state=stale)
              echo "engine: Reason Network $have is installed, but the running daemon started before the binary was replaced; restarting it"
              engine restart --home "$BASE/state" --bin "$RN" || die "the daemon did not come back on Reason Network $have"
              ;;
            *state=fresh) engine status --home "$BASE/state" --bin "$RN" ;;
            *) die "could not start the engine daemon (reason-network team); see reason-network doctor" ;;
          esac
          report_engine
          echo "engine: nothing to upgrade"
          return 0
        fi
        echo "engine: moving Reason Network $have to box/bin/engine; box/bin/reason-network becomes a launcher" ;;
      *) echo "engine: upgrading Reason Network $have to ${VERSION#v}" ;;
    esac
  else
    untrusted=1
    echo "engine: $cur is not a Reason Network build pinned by agent-mesh; replacing it with ${VERSION#v} without running it (a copy is kept in the backup)"
  fi
  UPGRADE_TMP="$(mktemp -d)"; trap 'rm -rf "$UPGRADE_TMP"' EXIT
  fetch_engine "$UPGRADE_TMP"
  backup="$BASE/engine-backup-$(date -u +%Y%m%dT%H%M%SZ)"; base="$backup"; n=1
  while [ -e "$backup" ]; do n=$((n + 1)); backup="$base-$n"; done
  engine switch --home "$BASE/state" --bin "$RN" --new "$UPGRADE_TMP/reason-network" --pinned "${VERSION#v}" \
    --backup "$backup" ${old_bin:+--old-bin "$old_bin"} ${untrusted:+--untrusted-old} || rc=$?
  case "$rc" in
    0) report_engine; echo "engine: upgrade done" ;;
    1) [ -z "$untrusted" ] || die "the upgrade did not finish; follow the steps above"
       die "the upgrade failed and the previous engine is back (see the lines above)" ;;
    *) die "the upgrade failed and the previous engine could not be confirmed; follow the steps above" ;;
  esac
}

if [ "$MODE" = upgrade ]; then
  upgrade_engine
  exit 0
fi
if [ -f "$BASE/person" ] && [ "$(cat "$BASE/person")" != "$PERSON" ]; then
  die "this cloud directory belongs to another person; review the existing deployment before changing it"
fi

used_name() {
  python3 -I - "$BASE/state/network.db" "$1" <<'PY'
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

export REASON_NETWORK_HOME="$BASE/state" REASON_NETWORK_NO_UPDATE=1 REASON_NETWORK_NO_PROMPT=1 \
  REASON_NETWORK_MACHINE="$MACHINE_NAME"
if [ ! -e "$RN" ] && [ ! -L "$RN" ] && { [ -e "$LAUNCHER" ] || [ -L "$LAUNCHER" ]; } && [ -f "$BASE/state/network.db" ] \
  && joined_team; then
  die "this cloud has an engine at $LAUNCHER from an earlier version; upgrade it with: bash $BASE/rn-cloud-setup.sh --upgrade-engine (nothing was changed)"
fi
mkdir -p "$BASE/bin/engine" "$BASE/state" || die "[BOT-CLOUD] /workspace/agent-mesh must be writable and persistent"
chmod 700 "$BASE/state"
if [ -e "$RN" ] || [ -L "$RN" ]; then
  build="$(engine build --bin "$RN")" \
    || die "$RN is not a Reason Network build pinned by agent-mesh; repair it with: bash $BASE/rn-cloud-setup.sh --upgrade-engine (nothing was changed)"
  have="${build%% *}"
  case "$(engine compare "$have" "${VERSION#v}")" in
    same) echo "Reason Network $have is already installed" ;;
    older) die "this cloud already has Reason Network $have; this setup installs ${VERSION#v}. Upgrade it with: bash $BASE/rn-cloud-setup.sh --upgrade-engine (nothing was changed)" ;;
    *) die "this cloud has Reason Network ${have:-of an unknown version}; this release supports ${VERSION#v} and never replaces it (nothing was changed)" ;;
  esac
else
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  fetch_engine "$tmp"
  install -m 755 "$tmp/reason-network" "$BASE/bin/engine/.reason-network.new.$$"
  mv -f "$BASE/bin/engine/.reason-network.new.$$" "$RN"
fi
engine launcher --path "$LAUNCHER" >/dev/null || die "could not write the launcher to $LAUNCHER"
"$RN" stop >/dev/null 2>&1 || true   # a daemon started without REASON_NETWORK_MACHINE would use the hostname

decision="$(python3 -I - "$LINK" "$BASE/state/network.db" <<'PY'
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
  team_out="$(printf '%s\n' "$team_out" | sed -E 's/^([[:space:]]+[^[:space:]]+) \(verified\)([[:space:]])/\1\2/')"
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
engine config-off --path "$BASE/state/config.toml" auto_update lan tray \
  || die "could not write $BASE/state/config.toml"
"$RN" stop >/dev/null 2>&1 || true
"$RN" doctor
echo "Bot address: $PERSON/$RELAY_NAME (this cloud uses $RELAY_NAME-cloud and $RELAY_NAME-inbox)"
echo "Now copy the Mac's ~/.config/agent-relay/outbound.key to $BASE/outbound.key (CopyToBox) and chmod 600 it."
