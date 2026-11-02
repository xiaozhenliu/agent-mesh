#!/usr/bin/env bash
# Grok Bot runs this ONCE on the box, after the user approves, with the link from `reason-network invite --me` on the Mac.
#   rn-cloud-setup.sh '<link>' <team-name>   # the name you gave install.sh --name on the Mac
# Installs the engine under /workspace/agent-mesh/box (persistent), joins as the user's second machine, no agent
# wiring, no service, auto-update/LAN/tray off. Nothing stays running: the daemon starts on demand while Grok Bot is awake.
set -euo pipefail
LINK="${1:?team link}"; PERSON="${2:?team/person name from the Mac is required}"
MACHINE_NAME="${MACHINE_NAME:-cloud}"           # shown in the roster instead of the box hostname
VERSION="v0.10.7"
BASE=/workspace/agent-mesh/box
die() { echo "cloud-setup: $*" >&2; exit 1; }
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
[[ "$PERSON" =~ ^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$ ]] || die "invalid team/person name"
[ "$MACHINE_NAME" = cloud ] || die "this deployment requires machine name cloud to match the Mac relay"
[ "$(cd "$(dirname "$0")" && pwd)" = "$BASE" ] \
  || die "clone the public distribution at /workspace/agent-mesh first; cloud prompts require that persistent path"
if [ -f "$BASE/person" ] && [ "$(cat "$BASE/person")" != "$PERSON" ]; then
  die "this cloud directory belongs to another person; review the existing deployment before changing it"
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
"$BASE/bin/reason-network" stop >/dev/null 2>&1 || true   # a daemon started without REASON_NETWORK_MACHINE would use the hostname
"$BASE/bin/reason-network" join "$LINK" --as "$PERSON" --no-setup
echo "$PERSON" > "$BASE/person"
CFG="$BASE/state/config.toml"
for kv in "auto_update = false" "lan = false" "tray = false"; do
  k="${kv%% *}"; if grep -q "^$k " "$CFG"; then sed -i "s/^$k = .*/$kv/" "$CFG"; else echo "$kv" >> "$CFG"; fi
done
"$BASE/bin/reason-network" stop >/dev/null 2>&1 || true
"$BASE/bin/reason-network" doctor
echo "Now copy the Mac's ~/.config/agent-relay/outbound.key to $BASE/outbound.key (CopyToBox) and chmod 600 it."
