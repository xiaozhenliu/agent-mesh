# Cloud setup prompt for Grok Bot

**English** | [简体中文](cloud-setup.zh-CN.md)

`[LOCAL]`, the user's local computer (Mac): the last installer step prints this prompt with the team name, Bot address name, Mac home directory, repository URL, release tag, release commit, and cloud script checksums filled in; it checks the release online first. The user sends it with the invite to the Bot in `[GROK-UI]`, the Grok Bot interface. Reprint it on `[LOCAL]` with `./install.sh --cloud-prompt --lang en`. The template below has unfilled placeholders; keep their names unchanged in translations.

```
You are Grok Bot. Execution location labels: [LOCAL] = the user's local computer (Mac); [BOT-CLOUD] = your Bot cloud computer (machine name cloud); [GROK-UI] = the Grok Bot interface opened by the user. Labels are not part of commands.
Join my Reason Network team on [BOT-CLOUD], using the machine name cloud. Run all shell commands below on [BOT-CLOUD], not on [LOCAL]. Keep all cloud files under the persistent directory /workspace/agent-mesh/ and do not leave any process running in the background. Reply to me in my preferred language.
1. [BOT-CLOUD] First check for Linux x86_64 or ARM64, Bash, Git, Python 3.9+ with ssl and sqlite3, curl, tar, sha256sum, writable persistent /workspace storage, HTTPS access to GitHub, and iroh connectivity. Prepare missing dependencies for the cloud system first and tell the user if permissions are needed. Once ready, run the block below exactly as written, as one bash command. It puts the release <release-tag> of <repo-url> at /workspace/agent-mesh (a new clone, or the existing clean clone there) after checking the release commit and the checksums of the cloud scripts, and it never overwrites local changes. Continue only if its last line is READY <release-tag>; otherwise stop, send the user its full output, and do not go on to step 2. Do not edit the block, retry with other commands, or reset, clean or stash anything. The HTTPS URL needs no GitHub account or SSH key; do not request the user's SSH private key.
bash -s <<'AGENT_MESH_SETUP'
set -u
stop() { echo "STOP: $*"; exit 1; }
export GIT_TERMINAL_PROMPT=0
TAG='<release-tag>'; COMMIT='<release-commit>'
case "$TAG$COMMIT" in *'<'*) stop "this block was not filled in; print it with ./install.sh --cloud-prompt on the Mac" ;; esac
A=/workspace/agent-mesh; URL=<repo-url>
check_tag() {
  git -C "$1" fetch -q --no-tags origin "refs/tags/$TAG:refs/tags/$TAG" </dev/null \
    || { why="could not fetch $TAG, or a different tag $TAG is already there"; return 1; }
  got="$(git -C "$1" rev-parse -q --verify "refs/tags/$TAG^{commit}")" || { why="$TAG is not a commit"; return 1; }
  [ "$got" = "$COMMIT" ] || { why="$TAG points to $got, not $COMMIT"; return 1; }
  n=0
  while read -r sum file; do
    [ -n "$file" ] || continue
    have="$(git -C "$1" cat-file blob "$COMMIT:$file" | sha256sum | cut -d' ' -f1)"
    [ "$have" = "$sum" ] || { why="$file in $TAG does not match"; return 1; }
    n=$((n + 1))
  done <<'AGENT_MESH_SUMS'
<cloud-checksums>
AGENT_MESH_SUMS
  [ "$n" = 4 ] || { why="the checksum list is incomplete"; return 1; }
}
check_files() {
  (cd "$1" && sha256sum --strict --quiet -c -) <<'AGENT_MESH_SUMS'
<cloud-checksums>
AGENT_MESH_SUMS
}
if [ ! -e "$A" ] && [ ! -L "$A" ]; then
  S="$(mktemp -d /workspace/.agent-mesh-setup.XXXXXX)" || stop "cannot create a directory in /workspace"
  fail() { rm -rf -- "$S"; stop "$1; nothing was kept, $A was not created"; }
  git clone -q --no-checkout "$URL" "$S/agent-mesh" </dev/null || fail "git clone $URL failed"
  check_tag "$S/agent-mesh" || fail "$why"
  git -C "$S/agent-mesh" checkout -q --detach "$COMMIT" || fail "checking out $TAG failed"
  check_files "$S/agent-mesh" || fail "the files after checking out $TAG do not match it"
  [ ! -e "$A" ] && [ ! -L "$A" ] || fail "$A appeared meanwhile"
  mv "$S/agent-mesh" "$A" || fail "could not move the clone to $A"
  rmdir "$S" || echo "note: could not remove the empty directory $S"
else
  st="$(git -C "$A" status --porcelain)" || stop "$A exists but is not a git clone; nothing was changed"
  [ -z "$st" ] || stop "$A has local changes; nothing was changed"
  echo "origin: $(git -C "$A" remote get-url origin)"
  check_tag "$A" || stop "$why; nothing was changed"
  old="$(git -C "$A" rev-parse HEAD)" || stop "cannot read HEAD of $A; nothing was changed"
  restore() {
    if git -C "$A" checkout -q -f --detach "$old" && [ "$(git -C "$A" rev-parse HEAD)" = "$old" ] \
        && [ -z "$(git -C "$A" status --porcelain)" ]; then
      echo "restored $A to $old"
    else
      echo "could not restore $A to $old; do not run any script in it"
    fi
  }
  git -C "$A" checkout -q --detach "$COMMIT" || { restore; stop "checking out $TAG failed"; }
  check_files "$A" || { restore; stop "the files after checking out $TAG do not match it"; }
fi
echo "READY $TAG"
AGENT_MESH_SETUP
2. [BOT-CLOUD] Run /workspace/agent-mesh/box/rn-cloud-setup.sh '<invite-link>' <team-name> <relay-name>. The script downloads the same reason-network version to box/bin/engine (box/bin/reason-network is only a launcher), joins with box/state as the engine directory, and disables auto_update, lan, and tray. Use the invite supplied above this prompt. Then run cat /workspace/agent-mesh/box/relay. Continue if it prints <relay-name>, or if the file does not exist and <relay-name> is bot. Otherwise stop and tell the user that the cloud clone is older than the Mac's installer.
3. [LOCAL] → [BOT-CLOUD] Use CopyToBox to copy the key: source <mac-home>/.config/agent-relay/outbound.key on the user's local computer, destination /workspace/agent-mesh/box/outbound.key on your Bot cloud computer. Use an absolute source path; the user approves the copy in [GROK-UI]. Then run chmod 600 on the destination on [BOT-CLOUD]. The source path belongs to the Mac; do not try to read it directly on the cloud computer. Keep the key contents out of chat.
4. [BOT-CLOUD] Run /workspace/agent-mesh/box/rn-cloud.sh who. You should see the user's sessions on [LOCAL]. After checking, tell me that the cloud has joined. This confirms cloud team membership; messaging in both directions still needs the runbook's tests.
```
