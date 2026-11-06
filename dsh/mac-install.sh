#!/bin/bash
# Install the reason-network-bridge plugin into DeepSeek Harness (dsh) on this Mac.
#   dsh/mac-install.sh [<team-name>]    default: read from ~/.reason/network/config.toml
#   dsh/mac-install.sh --plugin-only    replace only the installed plugin file; ~/.dsh/cordis.patch.yml stays as is
# Backs up the plugin and ~/.dsh/cordis.patch.yml and restores both if any step fails; keeps Reason Network's own
# block for dsh/mac-uninstall.sh. Quit the app completely (⌘Q) and reopen it afterwards.
set -eu
PLUGIN_ONLY=0
if [ "${1:-}" = --plugin-only ]; then PLUGIN_ONLY=1; shift; fi
APP_NODE=""
RN="$HOME/.local/bin/reason-network"
PATCH="$HOME/.dsh/cordis.patch.yml"
SRC="$(cd "$(dirname "$0")" && pwd)"
BK="$HOME/.dsh/backup-reason-network-bridge"
PLUG="$HOME/.dsh/plugins/reason-network-bridge.mjs"
BRIDGE_BEGIN="# === reason-network-bridge BEGIN (replaces reason-network's MCP block) ==="
BRIDGE_END="# === reason-network-bridge END ==="
PERSON="${1:-$(sed -n 's/^person = "\(.*\)"/\1/p' "$HOME/.reason/network/config.toml" 2>/dev/null | head -n 1)}"
[ "$(uname -s)" = Darwin ] || { echo "[LOCAL] this plugin installer requires macOS"; exit 1; }
if [ -n "${DSH_APP:-}" ]; then
  APP_NODE="$DSH_APP/Contents/MacOS/DeepSeek Harness"
else
  for app_dir in "$HOME/Applications/DeepSeek Harness.app" "/Applications/DeepSeek Harness.app"; do
    if [ -x "$app_dir/Contents/MacOS/DeepSeek Harness" ]; then APP_NODE="$app_dir/Contents/MacOS/DeepSeek Harness"; break; fi
  done
fi
[ -n "$PERSON" ] || { echo "usage: $0 [--plugin-only] [<team-name>]"; exit 2; }
[[ "$PERSON" =~ ^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$ ]] || { echo "invalid team/person name"; exit 2; }
current_person="$(sed -n 's/^person = "\(.*\)"/\1/p' "$HOME/.reason/network/config.toml" 2>/dev/null | head -n 1)"
[ "$PERSON" = "$current_person" ] || { echo "team/person must match the current Mac deployment: $current_person"; exit 1; }
[ -x "$APP_NODE" ] || { echo "Install and open DeepSeek Harness first; or set DSH_APP to the absolute path of DeepSeek Harness.app"; exit 1; }
command -v python3 >/dev/null || { echo "python3 missing; see the main guide prerequisites"; exit 1; }
[ -x "$RN" ] || { echo "reason-network not installed; run ./install.sh first"; exit 1; }
[ -f "$PATCH" ] || { echo "$PATCH not found; run 'reason-network setup' first so dsh has the reason-network block"; exit 1; }
if [ "$PLUGIN_ONLY" = 1 ]; then
  [ -f "$PLUG" ] || { echo "the bridge is not installed; run dsh/mac-install.sh first"; exit 1; }
  if [ "$(grep -cF "$BRIDGE_BEGIN" "$PATCH")" != 1 ] || [ "$(grep -cF "$BRIDGE_END" "$PATCH")" != 1 ] \
    || grep -qF 'reason-network MCP BEGIN' "$PATCH"; then
    echo "$PATCH does not hold exactly one bridge block; run dsh/mac-install.sh instead"
    exit 1
  fi
fi
run() { ELECTRON_RUN_AS_NODE=1 "$APP_NODE" "$@"; }

echo "== 1. unit tests with the app's own runtime"
out=$(run "$SRC/reason-network-bridge.test.mjs" 2>&1) || { echo "$out"; echo "unit tests failed; nothing changed"; exit 1; }
echo "$out" | grep -E 'ℹ (pass|fail)' || true

echo "== 2. backup"
mkdir -p "$BK" "$HOME/.dsh/plugins"
stamp="$(date +%Y%m%d%H%M%S)"
OLD_PLUG=""; LAST=""; NEW_PLUG=""
if [ -f "$PLUG" ]; then
  OLD_PLUG="$(mktemp "$BK/reason-network-bridge.mjs.$stamp.XXXXXX")"
  cp -p "$PLUG" "$OLD_PLUG"; cmp -s "$PLUG" "$OLD_PLUG"
fi
if [ "$PLUGIN_ONLY" = 0 ]; then
  LAST="$(mktemp "$BK/cordis.patch.yml.$stamp.XXXXXX")"
  cp -p "$PATCH" "$LAST"; cmp -s "$PATCH" "$LAST"
fi
restore() {
  [ -z "$NEW_PLUG" ] || rm -f "$NEW_PLUG"
  if [ -n "$OLD_PLUG" ]; then
    cp -p "$OLD_PLUG" "$PLUG.restore.$$" && mv -f "$PLUG.restore.$$" "$PLUG" && echo "restored $PLUG from $OLD_PLUG"
  else
    rm -f "$PLUG"
  fi
  if [ -n "$LAST" ]; then cp -p "$LAST" "$PATCH" && echo "restored $PATCH from $LAST"; fi
}
trap restore EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

echo "== 3. install plugin"
NEW_PLUG="$(mktemp "$HOME/.dsh/plugins/.reason-network-bridge.mjs.XXXXXX")"
cp "$SRC/reason-network-bridge.mjs" "$NEW_PLUG"; cmp -s "$SRC/reason-network-bridge.mjs" "$NEW_PLUG"
chmod 644 "$NEW_PLUG"; mv -f "$NEW_PLUG" "$PLUG"; NEW_PLUG=""
if [ "$PLUGIN_ONLY" = 1 ]; then
  trap - EXIT
  echo "== done. Plugin updated; $PATCH was not changed, so your bridge settings are kept."
  echo "   Quit DeepSeek Harness completely (⌘Q) and reopen it."
  exit 0
fi

echo "== 4. rewrite patch (allowPeople: $PERSON)"
mkdir -p "$HOME/.dsh/logs"; chmod 700 "$HOME/.dsh/logs"
python3 - "$PATCH" "$PLUG" "$RN" "$PERSON" "$BK/reason-network-block.yml" <<'PY'
import os, sys, tempfile
p, plug, rn, person, saved = sys.argv[1:]
t = open(p).read()
B, E = "# === reason-network MCP BEGIN ===", "# === reason-network MCP END ==="
own = B in t
if not own:
 B, E = "# === reason-network-bridge BEGIN (replaces reason-network's MCP block) ===", "# === reason-network-bridge END ==="
b = t.find(B); e = t.find(E, b)
assert b >= 0 and e > b, "reason-network MCP block not found in the patch"
assert t.count(B) == 1 and t.count(E) == 1, "the reason-network block markers appear more than once in the patch"
e += len(E)
if t[e:e + 1] == "\n": e += 1
if own:
 open(saved, "w").write(t[b:e] if t[b:e].endswith("\n") else t[b:e] + "\n")
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
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p), prefix=".cordis.patch.", suffix=".tmp")
try:
 with os.fdopen(fd, "w") as out:
  out.write(t[:b] + block + t[e:])
 os.chmod(tmp, os.stat(p).st_mode & 0o7777)
 os.replace(tmp, p)
except BaseException:
 os.path.exists(tmp) and os.unlink(tmp)
 raise
PY
grep -q 'reason-network MCP BEGIN' "$PATCH" && { echo "old block still present"; exit 1; }
"$RN" setup --dry-run 2>&1 | grep -i -E 'dsh|deepseek' || true

trap - EXIT
echo "== done. Quit DeepSeek Harness completely (⌘Q) and reopen it. Log: ~/.dsh/logs/reason-network-bridge.log"
echo "   To remove the bridge later: dsh/mac-uninstall.sh"
