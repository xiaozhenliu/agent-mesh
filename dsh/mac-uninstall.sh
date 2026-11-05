#!/bin/bash
# Remove the reason-network-bridge plugin from DeepSeek Harness (dsh) on this Mac.
#   dsh/mac-uninstall.sh               put Reason Network's own MCP block back (dsh returns to pull mode)
#   dsh/mac-uninstall.sh --no-restore  only drop the bridge block (uninstall.sh --all, before setup --remove)
# Only the bridge block in ~/.dsh/cordis.patch.yml changes; the file is backed up first and restored if a step fails.
# Quit the app completely (⌘Q) and reopen it afterwards.
set -eu
RESTORE=1
case "${1:-}" in
  "") ;;
  --no-restore) RESTORE=0 ;;
  *) echo "usage: $0 [--no-restore]"; exit 2 ;;
esac
PATCH="$HOME/.dsh/cordis.patch.yml"
BK="$HOME/.dsh/backup-reason-network-bridge"
PLUG="$HOME/.dsh/plugins/reason-network-bridge.mjs"
[ "$(uname -s)" = Darwin ] || { echo "[LOCAL] this plugin uninstaller requires macOS"; exit 1; }
command -v python3 >/dev/null || { echo "python3 missing; see the main guide prerequisites"; exit 1; }

EDIT='
import glob, os, shutil, sys
mode, p, bk, restore = sys.argv[1:]
BEGIN, END = "# === reason-network-bridge BEGIN", "# === reason-network-bridge END ==="
OWN_BEGIN, OWN_END = "# === reason-network MCP BEGIN ===", "# === reason-network MCP END ==="
def span(t, begin, end):
    b = t.find(begin)
    e = t.find(end, b) if b >= 0 else -1
    if e < 0:
        return None
    e += len(end)
    return b, e + 1 if t[e:e + 1] == "\n" else e
t = open(p).read()
if "reason-network-bridge" not in t:
    print("absent")
    sys.exit(0)
cut = span(t, BEGIN, END)
if t.count(BEGIN) != 1 or t.count(END) != 1 or not cut or "reason-network-bridge" in t[:cut[0]] + t[cut[1]:]:
    sys.exit(p + ": the bridge is not exactly one BEGIN/END block; edit the file by hand")
if mode == "check":
    print("present")
    sys.exit(0)
block = ""
if restore == "1":
    for c in [os.path.join(bk, "reason-network-block.yml")] + sorted(glob.glob(os.path.join(bk, "cordis.patch.yml.*"))):
        try:
            s = open(c).read()
        except OSError:
            continue
        own = span(s, OWN_BEGIN, OWN_END)
        if own:
            block = s[own[0]:own[1]]
            block += "" if block.endswith("\n") else "\n"
            break
tmp = p + ".reason-network-bridge.tmp"
with open(tmp, "w") as f:
    f.write(t[:cut[0]] + block + t[cut[1]:])
shutil.copymode(p, tmp)
os.replace(tmp, p)
print("restored" if block else "removed")
'

state=absent
[ -f "$PATCH" ] && state=$(python3 -c "$EDIT" check "$PATCH" "$BK" "$RESTORE")
if [ "$state" = absent ]; then
  rm -f "$PLUG"
  echo "no bridge in $PATCH; nothing to change"
  exit 0
fi

mkdir -p "$BK"; LAST="$(mktemp "$BK/cordis.patch.yml.$(date +%Y%m%d%H%M%S).XXXXXX")"; cp -p "$PATCH" "$LAST"
restore() { cp -p "$LAST" "$PATCH"; echo "restored $PATCH from $LAST"; }
trap restore EXIT
result=$(python3 -c "$EDIT" apply "$PATCH" "$BK" "$RESTORE")
trap - EXIT
rm -f "$PLUG"

echo "== bridge removed; backup: $LAST"
if [ "$RESTORE" = 1 ] && [ "$result" = restored ]; then
  echo "   Reason Network's own MCP block is back: dsh sessions share one address and check their inbox themselves."
elif [ "$RESTORE" = 1 ]; then
  echo "   Reason Network's own block was not found in $BK."
  echo "   Run 'reason-network setup' to wire dsh again (it also re-checks the other agents)."
fi
echo "== Quit DeepSeek Harness completely (⌘Q) and reopen it."
