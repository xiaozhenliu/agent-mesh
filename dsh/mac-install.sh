#!/bin/bash
# Install the reason-network-bridge plugin into DeepSeek Harness (dsh) on this Mac.
#   dsh/mac-install.sh [<team-name>]    default: read from ~/.reason/network/config.toml
# Backs up ~/.dsh/cordis.patch.yml and restores it if any step fails. Quit the app completely (⌘Q) and reopen it afterwards.
set -eu
APP_NODE="/Applications/DeepSeek Harness.app/Contents/MacOS/DeepSeek Harness"
RN="$HOME/.local/bin/reason-network"
PATCH="$HOME/.dsh/cordis.patch.yml"
SRC="$(cd "$(dirname "$0")" && pwd)"
BK="$HOME/.dsh/backup-reason-network-bridge"
PLUG="$HOME/.dsh/plugins/reason-network-bridge.mjs"
PERSON="${1:-$(sed -n 's/^person = "\(.*\)"/\1/p' "$HOME/.reason/network/config.toml" 2>/dev/null | head -n 1)}"
[ -n "$PERSON" ] || { echo "usage: $0 <team-name>"; exit 2; }
[ -x "$APP_NODE" ] || { echo "DeepSeek Harness.app not found in /Applications"; exit 1; }
[ -x "$RN" ] || { echo "reason-network not installed; run ./install.sh first"; exit 1; }
[ -f "$PATCH" ] || { echo "$PATCH not found; run 'reason-network setup' first so dsh has the reason-network block"; exit 1; }
run() { ELECTRON_RUN_AS_NODE=1 "$APP_NODE" "$@"; }

echo "== 1. unit tests with the app's own runtime"
out=$(run "$SRC/reason-network-bridge.test.mjs" 2>&1) || { echo "$out"; echo "unit tests failed; nothing changed"; exit 1; }
echo "$out" | grep -E 'ℹ (pass|fail)' || true

echo "== 2. backup"
mkdir -p "$BK"; LAST="$BK/cordis.patch.yml.$(date +%Y%m%d%H%M%S)"; cp -p "$PATCH" "$LAST"
restore() { cp -p "$LAST" "$PATCH"; echo "restored $PATCH from $LAST"; }
trap restore EXIT

echo "== 3. install plugin, rewrite patch (allowPeople: $PERSON)"
mkdir -p "$HOME/.dsh/plugins" "$HOME/.dsh/logs"; chmod 700 "$HOME/.dsh/logs"
cp "$SRC/reason-network-bridge.mjs" "$PLUG"; chmod 644 "$PLUG"
/usr/bin/python3 - "$PATCH" "$PLUG" "$RN" "$PERSON" <<'PY'
import sys
p, plug, rn, person = sys.argv[1:]
t = open(p).read()
B, E = "# === reason-network MCP BEGIN ===", "# === reason-network MCP END ==="
b = t.find(B); e = t.find(E, b)
assert b >= 0 and e > b, "reason-network MCP block not found in the patch"
e += len(E)
if t[e:e + 1] == "\n": e += 1
block = (
 "# === reason-network-bridge BEGIN (replaces reason-network's MCP block) ===\n"
 "- insert:\n"
 "    - id: reason-network-bridge\n"
 f"      name: '{plug}'\n"
 "      config:\n"
 "        serverName: reason-network\n"
 f"        command: '{rn}'\n"
 "        allowPeople:\n"
 f"          - '{person}'\n"
 "# === reason-network-bridge END ===\n")
open(p, "w").write(t[:b] + block + t[e:])
PY
grep -q 'reason-network MCP BEGIN' "$PATCH" && { echo "old block still present"; exit 1; }
"$RN" setup --dry-run 2>&1 | grep -i -E 'dsh|deepseek' || true

trap - EXIT
echo "== done. Quit DeepSeek Harness completely (⌘Q) and reopen it. Log: ~/.dsh/logs/reason-network-bridge.log"
