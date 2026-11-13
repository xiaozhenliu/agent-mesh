#!/bin/bash
# Remove the reason-network-bridge plugin from DeepSeek Harness (dsh) on this Mac.
#   dsh/mac-uninstall.sh               put Reason Network's own MCP block back (dsh returns to pull mode)
#   dsh/mac-uninstall.sh --no-restore  only drop the bridge block (uninstall.sh --all, before setup --remove)
# Only the bridge block in ~/.dsh/cordis.patch.yml changes; the file is backed up first and restored if a step fails.
# Quit the app completely (⌘Q) and reopen it afterwards.
set -eu
. "$(cd "$(dirname "$0")" && pwd)/../scripts/msg.sh"
RESTORE=1
case "${1:-}" in
  "") ;;
  --no-restore) RESTORE=0 ;;
  *) echo "usage: $0 [--no-restore]"; exit 2 ;;
esac
PATCH="$HOME/.dsh/cordis.patch.yml"
BK="$HOME/.dsh/backup-reason-network-bridge"
PLUG="$HOME/.dsh/plugins/reason-network-bridge.mjs"
[ "$(uname -s)" = Darwin ] || { t dsh.need_macos_uninstall; exit 1; }
command -v python3 >/dev/null || { t dsh.no_python; exit 1; }

EDIT='
import glob, os, shutil, sys
mode, p, bk, restore, ambiguous, tmp_bad = sys.argv[1:]
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
    sys.exit(ambiguous)
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
if os.path.islink(tmp) or (os.path.lexists(tmp) and not os.path.isfile(tmp)):
    sys.exit(tmp_bad)
if os.path.lexists(tmp):
    os.unlink(tmp)
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
with os.fdopen(fd, "w") as f:
    f.write(t[:cut[0]] + block + t[cut[1]:])
shutil.copymode(p, tmp)
os.replace(tmp, p)
print("restored" if block else "removed")
'

MSGS=("$(tq dsh.ambiguous "$PATCH")" "$(tq dsh.tmp_bad "$PATCH.reason-network-bridge.tmp")")
state=absent
[ -f "$PATCH" ] && state=$(python3 -c "$EDIT" check "$PATCH" "$BK" "$RESTORE" "${MSGS[@]}")
if [ "$state" = absent ]; then
  rm -f "$PLUG"
  t dsh.nothing "$PATCH"
  exit 0
fi

mkdir -p "$BK"; LAST="$(mktemp "$BK/cordis.patch.yml.$(date +%Y%m%d%H%M%S).XXXXXX")"; cp -p "$PATCH" "$LAST"
restore() { cp -p "$LAST" "$PATCH"; t dsh.restored "$PATCH" "$LAST"; }
trap restore EXIT
result=$(python3 -c "$EDIT" apply "$PATCH" "$BK" "$RESTORE" "${MSGS[@]}")
trap - EXIT
rm -f "$PLUG"

t dsh.removed "$LAST"
if [ "$RESTORE" = 1 ] && [ "$result" = restored ]; then
  t dsh.own_back
elif [ "$RESTORE" = 1 ]; then
  t dsh.own_missing "$BK"
  t dsh.run_setup
fi
t dsh.reopen
