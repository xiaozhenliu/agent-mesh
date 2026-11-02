#!/bin/bash
# Install the reason-network-bridge plugin into DeepSeek Harness (dsh) on this Mac.
#   dsh/mac-install.sh [<team-name>]    default: read from ~/.reason/network/config.toml
# Backs up ~/.dsh/cordis.patch.yml and restores it if any step fails. Quit the app completely (⌘Q) and reopen it afterwards.
set -eu
APP_NODE=""
RN="$HOME/.local/bin/reason-network"
PATCH="$HOME/.dsh/cordis.patch.yml"
SRC="$(cd "$(dirname "$0")" && pwd)"
BK="$HOME/.dsh/backup-reason-network-bridge"
PLUG="$HOME/.dsh/plugins/reason-network-bridge.mjs"
PERSON="${1:-$(sed -n 's/^person = "\(.*\)"/\1/p' "$HOME/.reason/network/config.toml" 2>/dev/null | head -n 1)}"
[ "$(uname -s)" = Darwin ] || { echo "[LOCAL] this plugin installer requires macOS"; exit 1; }
if [ -n "${DSH_APP:-}" ]; then
  APP_NODE="$DSH_APP/Contents/MacOS/DeepSeek Harness"
else
  for app_dir in "$HOME/Applications/DeepSeek Harness.app" "/Applications/DeepSeek Harness.app"; do
    if [ -x "$app_dir/Contents/MacOS/DeepSeek Harness" ]; then APP_NODE="$app_dir/Contents/MacOS/DeepSeek Harness"; break; fi
  done
fi
[ -n "$PERSON" ] || { echo "usage: $0 <team-name>"; exit 2; }
[[ "$PERSON" =~ ^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$ ]] || { echo "invalid team/person name"; exit 2; }
current_person="$(sed -n 's/^person = "\(.*\)"/\1/p' "$HOME/.reason/network/config.toml" 2>/dev/null | head -n 1)"
[ "$PERSON" = "$current_person" ] || { echo "team/person must match the current Mac deployment: $current_person"; exit 1; }
[ -x "$APP_NODE" ] || { echo "Install and open DeepSeek Harness first; or set DSH_APP to the absolute path of DeepSeek Harness.app"; exit 1; }
command -v python3 >/dev/null || { echo "python3 missing; see the main guide prerequisites"; exit 1; }
[ -x "$RN" ] || { echo "reason-network not installed; run ./install.sh first"; exit 1; }
[ -f "$PATCH" ] || { echo "$PATCH not found; run 'reason-network setup' first so dsh has the reason-network block"; exit 1; }
run() { ELECTRON_RUN_AS_NODE=1 "$APP_NODE" "$@"; }

echo "== 1. unit tests with the app's own runtime"
out=$(run "$SRC/reason-network-bridge.test.mjs" 2>&1) || { echo "$out"; echo "unit tests failed; nothing changed"; exit 1; }
echo "$out" | grep -E 'ℹ (pass|fail)' || true

echo "== 2. backup"
mkdir -p "$BK"; LAST="$(mktemp "$BK/cordis.patch.yml.$(date +%Y%m%d%H%M%S).XXXXXX")"; cp -p "$PATCH" "$LAST"
restore() { cp -p "$LAST" "$PATCH"; echo "restored $PATCH from $LAST"; }
trap restore EXIT

echo "== 3. install plugin, rewrite patch (allowPeople: $PERSON)"
mkdir -p "$HOME/.dsh/plugins" "$HOME/.dsh/logs"; chmod 700 "$HOME/.dsh/logs"
cp "$SRC/reason-network-bridge.mjs" "$PLUG"; chmod 644 "$PLUG"
python3 - "$PATCH" "$PLUG" "$RN" "$PERSON" <<'PY'
import sys
p, plug, rn, person = sys.argv[1:]
t = open(p).read()
B, E = "# === reason-network MCP BEGIN ===", "# === reason-network MCP END ==="
if B not in t:
 B, E = "# === reason-network-bridge BEGIN (replaces reason-network's MCP block) ===", "# === reason-network-bridge END ==="
b = t.find(B); e = t.find(E, b)
assert b >= 0 and e > b, "reason-network MCP block not found in the patch"
e += len(E)
if t[e:e + 1] == "\n": e += 1
plug, rn, person = (value.replace("'", "''") for value in (plug, rn, person))
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
