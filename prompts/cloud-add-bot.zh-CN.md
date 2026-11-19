# 添加的 Grok Bot 的第 0 步

[English](cloud-add-bot.md) | **简体中文**

`[LOCAL]` 用户本地电脑（Mac）上的 `./install.sh --add-bot` 把又一个 Grok Bot 接到已经服务第一个 Bot 的云电脑上时，这个 Bot 用的是简短的 [routine 提示词](routine.zh-CN.md)（桩）。桩的第 1 条带着团队名和团队哈希（团队 ID 的 SHA-256 的前 16 位十六进制）运行 `rn-cloud.sh instructions`，这条命令第一次运行时自己在云电脑上登记这个 Bot。

只有 `./update.sh --full-prompt` 打印的添加 Bot 的完整提示词里才有下面的代码块：安装器把它作为第 0 步插在[规则](routine-body.zh-CN.md)的 R1 之前，并填好 Bot 地址名、团队名和团队哈希。这段文字不会单独交给 Bot。那份提示词里，每条 `rn-cloud.sh` 命令前面都加上 `RN_BOT=<relay-name>`，threads 文件改到 `/workspace/agent-mesh/bots/<relay-name>/threads/`；第一个 Bot 的提示词不变。

第 0 步在 `[BOT-CLOUD]` Bot 云电脑上运行 [`box/rn-cloud-add-bot.sh`](../box/rn-cloud-add-bot.sh)，直到这个 Bot 在那里登记完成。安装器在这个 Bot 的 relay 启动后发来的一行请求 `agent-mesh add-bot <relay-name> <req>` 是维护请求（R2），回信向 Mac 确认登记已完成。下面是未填充的模板，占位符名称在所有翻译中保持一致。

```
0. R1 之前：/workspace/agent-mesh/bots/<relay-name>/.registered 不存在时，先运行 bash /workspace/agent-mesh/box/rn-cloud-add-bot.sh '<relay-name>' '<team-name>' '<team-hash>'。这个 Bot 是添加到一台已经服务另一个 Bot 的云电脑上的；这条命令只在这台共用的云电脑上登记这个 Bot 自己的两个身份 <team-name>/<relay-name>-cloud 和 <team-name>/<relay-name>-inbox，不需要用户批准。命令失败时，把它的完整输出告诉用户，然后停下，本轮不要继续 R1。
```
