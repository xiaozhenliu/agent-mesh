# 发给 Grok Bot 的云端接入提示词

[English](cloud-setup.md) | **简体中文**

`[LOCAL]` 用户本地电脑（Mac）：`./install.sh --lang zh-CN` 的最后一步会打印这段提示词，并把团队名、Bot 地址名、本地 home 目录、仓库地址、发布 tag、发布提交号和云端脚本校验值都填好（打印前先在线核对发布版本）；用户在 `[GROK-UI]` Grok Bot 界面把它连同邀请链接一起发给 Bot。想再打印一次，在 `[LOCAL]` 运行 `./install.sh --cloud-prompt --lang zh-CN`。下面是未填充的模板，占位符名称在所有翻译中保持一致。

```
你是 Grok Bot。执行位置标识：[LOCAL] = 用户本地电脑（Mac）；[BOT-CLOUD] = 你的 Bot 云电脑（机器名 cloud）；[GROK-UI] = 用户打开的 Grok Bot 界面。标识不是命令的一部分。
请在 [BOT-CLOUD] 接入我的 Reason Network 团队，机器名用 cloud。以下 shell 命令全部在 [BOT-CLOUD] 执行，不要在 [LOCAL] 执行。所有云端文件都放在持久目录 /workspace/agent-mesh/ 下，不要留任何常驻进程。向我回复时使用我偏好的语言。
1. [BOT-CLOUD] 先检查系统是 Linux x86_64 或 ARM64，已有 Bash、Git、Python 3.9+（含 ssl、sqlite3）、curl、tar、sha256sum，/workspace 可写且持久，并可经 HTTPS 访问 GitHub 和连接 iroh 中继。缺少依赖时先按云电脑系统准备，需要权限时告知用户。就绪后把下面的命令块作为一条 bash 命令原样运行。它先核对发布提交号和云端脚本的校验值，再把 <repo-url> 的发布版本 <release-tag> 放到 /workspace/agent-mesh（新建 clone，或使用那里已有的干净 clone），不会覆盖本地改动。只有最后一行是 READY <release-tag> 才继续；否则停止，把完整输出发给用户，不进行第 2 步。不要修改命令块，不要换别的命令重试，也不要 reset、clean 或 stash。这个 HTTPS 地址无需 GitHub 账号或 SSH 密钥，不要索取用户的 SSH 私钥。
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
2. [BOT-CLOUD] 运行 /workspace/agent-mesh/box/rn-cloud-setup.sh '<invite-link>' <team-name> <relay-name>。这个脚本会把同版本的 reason-network 下载到 box/bin/engine（box/bin/reason-network 只是启动器），以 box/state 作为引擎目录加入团队，并关掉 auto_update、lan、tray。使用这段提示词上方提供的邀请链接。然后运行 cat /workspace/agent-mesh/box/relay：输出是 <relay-name> 时继续；文件不存在而 <relay-name> 是 bot 时也继续；其他情况停止，告诉用户云端仓库比 Mac 上的安装器旧。
3. [LOCAL] → [BOT-CLOUD] 用 CopyToBox 复制密钥：源文件是用户本地电脑的 <mac-home>/.config/agent-relay/outbound.key，目标文件是你的 Bot 云电脑的 /workspace/agent-mesh/box/outbound.key。源路径要写绝对路径；由用户在 [GROK-UI] 批准复制。复制后在 [BOT-CLOUD] 对目标文件执行 chmod 600。源路径属于用户 Mac，不要尝试在云电脑直接读取它，密钥内容不要出现在聊天里。
4. [BOT-CLOUD] 运行 /workspace/agent-mesh/box/rn-cloud.sh who，应该能看到 [LOCAL] 用户本地电脑上的会话。确认后回我一句「cloud 已接入」。这表示云电脑已入队，双向收发还要按安装手册验证。
```
