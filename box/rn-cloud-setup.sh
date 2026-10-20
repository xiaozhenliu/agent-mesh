#!/usr/bin/env bash
# Grok Bot runs this ONCE on the box, after the user approves, with the link from `reason-network invite --me` on the Mac.
#   rn-cloud-setup.sh '<link>' <team-name>   # the name you gave install.sh --name on the Mac
# Installs the engine under /workspace/agent-mesh/box (persistent), joins as the user's second machine, no agent
# wiring, no service, auto-update/LAN/tray off. Nothing stays running: the daemon starts on demand while Grok Bot is awake.
set -euo pipefail
LINK="${1:?team link}"; PERSON="${2:-demo}"     # must equal the Mac's name: `invite --me` only admits that person
MACHINE_NAME="${MACHINE_NAME:-cloud}"           # shown in the roster instead of the box hostname
VERSION="${VERSION:-v0.10.7}"
BASE=/workspace/agent-mesh/box
export REASON_NETWORK_HOME="$BASE/state" REASON_NETWORK_NO_PROMPT=1 REASON_NETWORK_MACHINE="$MACHINE_NAME"
mkdir -p "$BASE/bin" "$BASE/state"; chmod 700 "$BASE/state"
t=x86_64-unknown-linux-musl; [ "$(uname -m)" = aarch64 ] && t=aarch64-unknown-linux-musl
base="https://github.com/reason-machines/reason-network/releases/download/$VERSION"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
curl -fsSL "$base/reason-network-$t.tar.gz" -o "$tmp/e.tgz"
curl -fsSL "$base/reason-network-$t.tar.gz.sha256" -o "$tmp/sum"
[ "$(cut -d' ' -f1 "$tmp/sum")" = "$(sha256sum "$tmp/e.tgz" | cut -d' ' -f1)" ] || { echo "checksum mismatch"; exit 1; }
tar xzf "$tmp/e.tgz" -C "$BASE/bin"
echo "$PERSON" > "$BASE/person"
"$BASE/bin/reason-network" stop >/dev/null 2>&1 || true   # a daemon started without REASON_NETWORK_MACHINE would use the hostname
"$BASE/bin/reason-network" join "$LINK" --as "$PERSON" --no-setup
CFG="$BASE/state/config.toml"
for kv in "auto_update = false" "lan = false" "tray = false"; do
  k="${kv%% *}"; if grep -q "^$k " "$CFG"; then sed -i "s/^$k = .*/$kv/" "$CFG"; else echo "$kv" >> "$CFG"; fi
done
"$BASE/bin/reason-network" stop >/dev/null 2>&1 || true
"$BASE/bin/reason-network" doctor
echo "Now copy the Mac's ~/.config/agent-relay/outbound.key to $BASE/outbound.key (CopyToBox) and chmod 600 it."
