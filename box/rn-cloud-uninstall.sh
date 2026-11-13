#!/usr/bin/env bash
set -euo pipefail
TEAM="<team-name>"; MAC="<mac-machine>"; RELAY_NAME="<relay-name>"; LEVEL="<level>"; MODE="<mode>"; CHALLENGE="<challenge>"
W=/workspace
A="$W/agent-mesh"; B="$A/box"; RN="$B/bin/reason-network"
stop() { echo "STOP: $*"; exit 1; }
usage() { stop "usage: check | apply <code> [force] [accept-shared] | apply-no-proof [force] [accept-shared]"; }
case "$TEAM$MAC$RELAY_NAME$LEVEL$MODE$CHALLENGE" in *'<'*|*'>'*) stop "this script was not filled in; print it with ./uninstall.sh on the user's Mac" ;; esac
name_re='^[a-z0-9]+(-[a-z0-9]+)*$'
if ! [[ "$TEAM" =~ $name_re ]] || ! [[ "$MAC" =~ $name_re ]] || ! [[ "$RELAY_NAME" =~ $name_re ]]; then stop "invalid names in this script"; fi
case "$LEVEL" in identity|all) ;; *) stop "invalid level $LEVEL" ;; esac
case "$MODE" in normal|residual) ;; *) stop "invalid mode $MODE" ;; esac
if ! [[ "$CHALLENGE" =~ ^[0-9a-f]{16}$ ]]; then stop "invalid challenge"; fi
PHASE="${1:-}"; ARG=""; FORCE=no; SHARED_OK=no
if [ $# -gt 0 ]; then shift; fi
if [ "$PHASE" = apply ]; then
  ARG="${1:-}"
  if [ $# -gt 0 ]; then shift; fi
fi
for w in "$@"; do
  case "$w" in
    force) FORCE=yes ;;
    accept-shared) SHARED_OK=yes ;;
    *) usage ;;
  esac
done
case "$PHASE" in
  check) if [ "$FORCE$SHARED_OK" != nono ]; then usage; fi ;;
  apply) if ! [[ "$ARG" =~ ^[0-9a-f]{16}$ ]]; then usage; fi ;;
  apply-no-proof) ;;
  *) usage ;;
esac
if [ "$(uname -s)" != Linux ]; then stop "this is not the Linux cloud computer; never run it on the user's Mac"; fi
for p in "$W" "$A" "$B" "$B/state" "$B/bin" "$B/bin/engine" "$B/person" "$B/relay" "$B/outbound.key" "$A/inbox" "$A/threads"; do
  if [ -L "$p" ]; then stop "$p is a symlink; not touching it"; fi
done
if [ ! -d "$A" ]; then
  echo "STATE: nothing at $A"
  echo "DONE: nothing to remove on this computer; the user must still delete the routine in the Grok UI"
  exit 0
fi
if [ ! -f "$B/rn-cloud.sh" ] || [ ! -f "$B/rn-cloud-setup.sh" ]; then stop "$A is not the agent-mesh distribution"; fi
cd /

helper() {
  python3 -I - "$@" <<'PY'
import hashlib, hmac, os, re, subprocess, sys

mode = sys.argv[1]
if mode == "tag":
    key = open(sys.argv[2], encoding="utf-8").read().strip()
    msg = "agent-mesh-uninstall-v1\n" + sys.argv[3] + "\n" + sys.argv[4]
    print(hmac.new(key.encode(), msg.encode(), hashlib.sha256).hexdigest()[:16])
    sys.exit(0)

W, A = sys.argv[2], sys.argv[3]
B = os.path.join(A, "box")
RUNTIME = re.compile(r"^box/(bin|state)/|^box/(person|relay|outbound\.key)$|^box/relay\.damaged-|^box/engine-backup-|"
                     r"^box/\.(relay|person)\.[A-Za-z0-9]{6}$|^inbox/|^threads/|(^|/)__pycache__/|\.pyc$")
INSIDE = (b"agent-mesh/box", b"box/state", b"box/bin")
LIMIT = 20 * 1024 * 1024


def git(*args):
    return subprocess.run(["git", "-C", A, *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def has(path, needles):
    try:
        if os.path.getsize(path) > LIMIT:
            return "big"
        with open(path, "rb") as stream:
            data = stream.read()
    except OSError:
        return None
    return any(n in data for n in needles)


def into_box(root, link):
    try:
        target = os.readlink(link)
    except OSError:
        return None
    full = os.path.normpath(os.path.join(root, target))
    return target if full == B or full.startswith(B + os.sep) or "agent-mesh/box" in target else False


def files_mode():
    out, keep = [], []
    for args in (("ls-files", "--others", "--exclude-standard"),
                 ("ls-files", "--others", "--ignored", "--exclude-standard")):
        r = git(*args)
        if r.returncode:
            keep.append("git " + " ".join(args) + " failed: " + r.stderr.decode(errors="replace").strip()[:300])
            continue
        out += [p for p in r.stdout.decode(errors="replace").splitlines() if p and not RUNTIME.search(p)]
    r = git("diff", "--name-only", "HEAD")
    if r.returncode:
        keep.append("git diff HEAD failed: " + r.stderr.decode(errors="replace").strip()[:300])
    else:
        out += [p for p in r.stdout.decode(errors="replace").splitlines() if p]
    r = git("rev-parse", "--verify", "-q", "@{upstream}")
    if r.returncode == 0:
        r = git("diff", "--name-only", "@{upstream}", "HEAD")
        if r.returncode:
            keep.append("git diff @{upstream} failed: " + r.stderr.decode(errors="replace").strip()[:300])
        else:
            out += [p for p in r.stdout.decode(errors="replace").splitlines() if p]
    r = git("for-each-ref", "--format=%(refname)", "refs/heads", "refs/tags")
    if r.returncode:
        keep.append("git for-each-ref failed: " + r.stderr.decode(errors="replace").strip()[:300])
    else:
        for ref in [x for x in r.stdout.decode(errors="replace").splitlines() if x] + ["HEAD"]:
            c = git("rev-list", "--count", ref, "--not", "--remotes")
            count = c.stdout.decode(errors="replace").strip()
            if c.returncode or not count.isdigit():
                keep.append(f"git rev-list {ref} failed: " + c.stderr.decode(errors="replace").strip()[:300])
            elif int(count):
                out.append(f"commits: {ref} has {count} commit(s) that no remote branch has")
                keep.append(f"{ref} has commits that no remote branch has; removing the clone would lose them")
    r = git("stash", "list")
    if r.returncode:
        keep.append("git stash list failed: " + r.stderr.decode(errors="replace").strip()[:300])
    else:
        out += ["stash: " + line for line in r.stdout.decode(errors="replace").splitlines() if line]
    for path in sorted(set(out)):
        print("FILE: " + path)
    for reason in keep:
        print("KEEP: " + reason)
    return 3 if keep else 0


def shared_mode():
    hits, problems = [], []
    for root, dirs, files in os.walk(W, onerror=lambda e: problems.append(f"cannot read {e.filename}: {e.strerror}")):
        dirs[:] = [d for d in dirs if os.path.join(root, d) != A and d != ".git"]
        for name in dirs + files:
            p = os.path.join(root, name)
            if os.path.islink(p):
                t = into_box(root, p)
                if t:
                    hits.append(f"{p} -> {t}")
        for name in files:
            p = os.path.join(root, name)
            if not os.path.islink(p) and os.path.isfile(p):
                found = has(p, (b"agent-mesh/box",))
                if found is None:
                    problems.append(f"cannot read {p}")
                elif found == "big":
                    problems.append(f"not checked, larger than 20 MiB: {p}")
                elif found:
                    hits.append(p)
    r = git("ls-files", "-z", "--others", "--exclude-standard")
    r2 = git("ls-files", "-z", "--others", "--ignored", "--exclude-standard")
    if r.returncode or r2.returncode:
        problems.append("cannot list the files in " + A + " that git does not track: "
                        + (r.stderr + r2.stderr).decode(errors="replace").strip()[:300])
    else:
        for rel in sorted(set((r.stdout + b"\0" + r2.stdout).decode(errors="replace").split("\0"))):
            if not rel or RUNTIME.search(rel):
                continue
            p = os.path.join(A, rel)
            if os.path.islink(p):
                hits.append(f"{p} -> {os.readlink(p)}")
            elif os.path.isfile(p):
                found = has(p, INSIDE)
                if found is None:
                    problems.append(f"cannot read {p}")
                elif found == "big":
                    problems.append(f"not checked, larger than 20 MiB: {p}")
                elif found:
                    hits.append(p)
    for line in hits[:50]:
        print("SHARED: " + line)
    if len(hits) > 50:
        print(f"SHARED: ... and {len(hits) - 50} more")
    for line in problems[:20]:
        print("SHARED: could not check everything: " + line)
    return 1 if hits or problems else 0


sys.exit(files_mode() if mode == "files" else shared_mode())
PY
}

tag() { helper tag "$B/outbound.key" "$1" "$CHALLENGE"; }

PRESENT=no
if [ -f "$B/person" ]; then
  if [ "$(cat "$B/person")" != "$TEAM" ]; then stop "box/person is not $TEAM; this cloud belongs to another deployment"; fi
  PRESENT=yes
else
  for p in "$B/state" "$B/outbound.key" "$B/bin" "$B/relay"; do
    if [ -e "$p" ]; then stop "box/person is missing but $p remains; ask the user"; fi
  done
  echo "STATE: cloud identity already removed"
fi
if [ -f "$B/relay" ]; then
  have="$(cat "$B/relay")"
  if [ "$MODE" = residual ]; then
    echo "STATE: box/relay holds ${have:0:60}"
  elif [ "$have" != "$RELAY_NAME" ]; then
    if [[ "$have" =~ $name_re ]]; then stop "box/relay is $have, not $RELAY_NAME; this cloud belongs to another deployment"; fi
    echo "NOTE: box/relay is damaged"
  fi
elif [ "$PRESENT" = yes ] && [ "$MODE" = normal ] && [ "$RELAY_NAME" != bot ]; then
  echo "NOTE: box/relay is missing, so this cloud used bot, not $RELAY_NAME"
fi
HAVEKEY=no
if [ -s "$B/outbound.key" ]; then HAVEKEY=yes; fi
if [ -e "$B/bin/engine/reason-network" ] || [ -L "$B/bin/engine/reason-network" ]; then
  if [ ! -f "$B/rn-engine.py" ]; then stop "box/rn-engine.py is missing, so the engine at box/bin/engine cannot be checked; not running it"; fi
  if ! RN="$(python3 -I "$B/rn-engine.py" cloud-bin --base "$B" 2>&1)"; then
    stop "the engine was not run: $RN. Repair the engine first (README: Troubleshooting), then uninstall"
  fi
  RN="$(printf '%s\n' "$RN" | tail -n 1)"
elif [ -e "$RN" ]; then
  echo "NOTE: the engine is still at box/bin/reason-network (from before it moved to box/bin/engine)"
fi
export REASON_NETWORK_HOME="$B/state" REASON_NETWORK_NO_UPDATE=1 REASON_NETWORK_NO_PROMPT=1 REASON_NETWORK_MACHINE=cloud
INTEAM=none
if [ -f "$B/state/machine.key" ] && [ -x "$RN" ]; then
  if out="$("$RN" team 2>&1)"; then
    out="$(printf '%s\n' "$out" | sed -E 's/^([[:space:]]+[^[:space:]]+) \(verified\)([[:space:]])/\1\2/')"
    INTEAM=yes
    printf '%s\n' "$out"
    first="$(awk 'NR == 1 {print $2}' <<<"$out")"
    if [ "$first" != "$TEAM" ]; then stop "this cloud is in the team $first, not $TEAM"; fi
    me="$(awk '/this machine$/ {print $1 "/" $2}' <<<"$out")"
    if [ "$me" != "$TEAM/cloud" ]; then stop "this computer is ${me:-not listed} in the team, not $TEAM/cloud"; fi
    others="$(awk -v p="$TEAM" 'NR > 1 && NF >= 3 && $1 != p {n++} END {print n + 0}' <<<"$out")"
    if [ "$MODE" = normal ]; then
      online="$(awk -v p="$TEAM" -v m="$MAC" 'NR > 1 && !/this machine$/ && $1 == p && $2 == m && $3 != "offline" {n++} END {print n + 0}' <<<"$out")"
      if [ "$online" = 0 ]; then stop "the Mac $TEAM/$MAC is not online in this cloud's team; keep the Mac on and try again"; fi
    elif [ "$others" != 0 ]; then
      stop "other people are in this team; ask the user before going on"
    fi
  else
    case "$out" in
      *"not in a team"*) INTEAM=no; echo "STATE: already left the team" ;;
      *) stop "reason-network team failed: $out" ;;
    esac
  fi
elif [ -f "$B/state/machine.key" ]; then
  INTEAM=unknown
  echo "STATE: box/state has an engine identity, but $RN is missing or not executable, so this cloud's team membership can be neither checked nor left"
elif [ -e "$B/state" ]; then
  echo "STATE: no engine identity in box/state; the engine is not started"
fi
SHARED=no
if ! helper shared "$W" "$A"; then SHARED=yes; fi
NOUP=no
if [ "$LEVEL" = all ]; then
  echo "FILES the user added or changed in $A (paths only):"
  if ! helper files "$W" "$A"; then NOUP=yes; fi
fi
echo "WILL REMOVE: box/state box/bin box/engine-backup-* box/relay box/outbound.key box/person (the cloud's engine identity and its backups, key and names)"
if [ "$LEVEL" = all ]; then
  echo "WILL REMOVE: inbox/ threads/ (received bodies and thread notes)"
  if [ "$NOUP" = yes ]; then echo "WILL KEEP: $A (see the KEEP lines)"; else echo "WILL REMOVE: $A"; fi
fi
if [ "$PHASE" = check ]; then
  if [ "$SHARED" = yes ]; then echo "NOTE: SHARED lines found; apply needs accept-shared, and only after the user confirms nothing there still uses box/"; fi
  if [ "$HAVEKEY" = yes ]; then echo "PROOF: $(tag proof)"; else echo "PROOF: none (no box/outbound.key)"; fi
  echo "CHECK DONE: give the PROOF line to the user; run apply only with the code the user's Mac prints"
  exit 0
fi
if [ "$PHASE" = apply-no-proof ]; then
  if [ "$HAVEKEY" = yes ] && [ "$MODE" = normal ]; then stop "a signing key is present: use apply <code> from the user's Mac"; fi
else
  if [ "$HAVEKEY" != yes ]; then stop "no box/outbound.key: use apply-no-proof, and only after the user confirms in chat"; fi
  if [ "$ARG" != "$(tag ack)" ]; then stop "the code does not match this cloud's key: another deployment, or a mistyped code"; fi
fi
if [ "$SHARED" = yes ] && [ "$SHARED_OK" != yes ]; then
  stop "something else uses box/ (SHARED lines); add accept-shared only after the user confirms it no longer does"
fi
if [ "$INTEAM" = yes ]; then
  if [ "$FORCE" = yes ]; then
    if ! out="$("$RN" leave --force 2>&1)"; then stop "leave --force failed: $out"; fi
  else
    if ! out="$("$RN" leave 2>&1)"; then stop "leave failed: $out. Tell the user; only with their consent run the same command again with force added"; fi
  fi
  printf '%s\n' "$out"
fi
if [ -f "$B/state/machine.key" ] && [ -x "$RN" ]; then
  if ! out="$("$RN" stop 2>&1)"; then echo "NOTE: reason-network stop said: $out"; fi
fi
rm -rf "${B:?}/state" "${B:?}/bin"
for d in "$B"/engine-backup-*; do
  if [ -d "$d" ] && [ ! -L "$d" ]; then rm -rf "${d:?}"; fi
done
for f in "$B/relay" "$B"/relay.damaged-* "$B"/.relay.?????? "$B"/.person.??????; do
  if [ -f "$f" ] && [ ! -L "$f" ]; then rm -f "$f"; fi
done
rm -f "$B/outbound.key"
rm -f "$B/person"
if [ "$LEVEL" = all ]; then
  rm -rf "${A:?}/inbox" "${A:?}/threads"
  if [ "$NOUP" = no ]; then
    rm -rf "$A"
  else
    echo "KEPT: $A; the user checks the FILE and KEEP lines and deletes it"
  fi
fi
if [ "$INTEAM" = unknown ]; then
  echo "INCOMPLETE: level $LEVEL files removed, but this cloud did not leave the team $TEAM; a member still in the team must run reason-network remove $TEAM/cloud (the user's Mac offers it); the user must still delete the routine in the Grok UI"
  exit 2
fi
echo "DONE: level $LEVEL; the user must still delete the routine in the Grok UI"
