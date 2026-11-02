# 发给 Grok Bot 的云端接入提示词

[English](cloud-setup.md) | **简体中文**

`[LOCAL]` 用户本地电脑（Mac）：`./install.sh --lang zh-CN` 的最后一步会打印这段提示词，并把团队名、本地 home 目录和仓库地址都填好；用户在 `[GROK-UI]` Grok Bot 界面把它连同邀请链接一起发给 Bot。想再打印一次，在 `[LOCAL]` 运行 `./install.sh --cloud-prompt --lang zh-CN`。下面是未填充的模板，占位符名称在所有翻译中保持一致。

```
你是 Grok Bot。执行位置标识：[LOCAL] = 用户本地电脑（Mac）；[BOT-CLOUD] = 你的 Bot 云电脑（机器名 cloud）；[GROK-UI] = 用户打开的 Grok Bot 界面。标识不是命令的一部分。
请在 [BOT-CLOUD] 接入我的 Reason Network 团队，机器名用 cloud。以下 shell 命令全部在 [BOT-CLOUD] 执行，不要在 [LOCAL] 执行。所有云端文件都放在持久目录 /workspace/agent-mesh/ 下，不要留任何常驻进程。向我回复时使用我偏好的语言。
1. [BOT-CLOUD] 先检查系统是 Linux x86_64 或 ARM64，已有 Bash、Git、Python 3.9+（含 ssl、sqlite3）、curl、tar、sha256sum，/workspace 可写且持久，并可经 HTTPS 访问 GitHub 和连接 iroh 中继。缺少依赖时先按云电脑系统准备，需要权限时告知用户。就绪后运行 git clone <repo-url> /workspace/agent-mesh。若目录已存在，先确认它是同一公开发行仓库且没有未提交代码改动，再运行 git -C /workspace/agent-mesh pull --ff-only；不是仓库、来源不同或有改动时停止并说明，不覆盖文件。默认 HTTPS 地址无需 GitHub 账号或 SSH 密钥；自定义的 SSH 地址若缺凭据，改用同一公开仓库的 HTTPS 地址，不要索取用户的 SSH 私钥。
2. [BOT-CLOUD] 运行 /workspace/agent-mesh/box/rn-cloud-setup.sh '<invite-link>' <team-name>。这个脚本会把同版本的 reason-network 下载到 box/bin，以 box/state 作为引擎目录加入团队，并关掉 auto_update、lan、tray。使用这段提示词上方提供的邀请链接。
3. [LOCAL] → [BOT-CLOUD] 用 CopyToBox 复制密钥：源文件是用户本地电脑的 <mac-home>/.config/agent-relay/outbound.key，目标文件是你的 Bot 云电脑的 /workspace/agent-mesh/box/outbound.key。源路径要写绝对路径；由用户在 [GROK-UI] 批准复制。复制后在 [BOT-CLOUD] 对目标文件执行 chmod 600。源路径属于用户 Mac，不要尝试在云电脑直接读取它，密钥内容不要出现在聊天里。
4. [BOT-CLOUD] 运行 /workspace/agent-mesh/box/rn-cloud.sh who，应该能看到 [LOCAL] 用户本地电脑上的会话。确认后回我一句「cloud 已接入」。这表示云电脑已入队，双向收发还要按安装手册验证。
```
