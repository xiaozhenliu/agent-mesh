# 卸载

[English](UNINSTALL.md) | **简体中文**

本文是 [agent-mesh 使用指南](README.zh-CN.md)的卸载部分。`[LOCAL]`、`[BOT-CLOUD]`、`[GROK-UI]` 的含义见[执行位置标识](README.zh-CN.md#执行位置标识)。

先更新当前目录（`git pull`），确保 `./uninstall.sh` 是这里介绍的卸载器；v0.4.1 及更早版本的卸载脚本没有分级，不带 `--all` 也会删除 relay 配置和签名密钥，并且不处理云端。

`./uninstall.sh` 在 `[LOCAL]` 运行。不带参数时只列出已安装的内容，不删除任何东西：版本、团队名和本机、云端成员、Bot 地址名、relay 及其队列、dsh 插件、团队里的其他成员，以及不是它安装的部署。`./uninstall.sh --plan` 还会列出每个级别要删除和保留的内容；`--plan --level <级别>` 只看一个级别。两者都不提问，也不改动任何东西。在终端里运行 `./uninstall.sh` 时，列完后会让你选择级别。

| 级别 | 删除 | 保留 |
|---|---|---|
| `--level program` | relay 服务和 plist、relay 程序、它的临时文件和 `rollback/`。 | 其余全部：两台电脑上的团队身份、`~/.config/agent-relay`（webhook 配置和签名密钥）、队列、日志和 Reason Network。 |
| `--level identity` | 另外删除部署记录、`~/.config/agent-relay`、relay 状态（队列、防重放记录、消息副本）和 dsh 插件块；本机退出团队，执行 `setup --remove`，删除 `~/.reason/network`。Bot 退出团队，删除云端的身份、密钥和名字（`box/state`、`box/bin`、`box/person`、`box/relay`、`box/outbound.key`）。 | relay 日志、引擎二进制，以及云端的 `inbox/`、`threads/` 和 `/workspace/agent-mesh`。 |
| `--level all`（或 `--all`） | 另外删除 relay 日志、`~/.local/bin/reason-network`、dsh 插件的备份和日志；`[BOT-CLOUD]` 上另外删除 `inbox/`、`threads/` 和 `/workspace/agent-mesh`。 | 只保留下面列出的内容。 |

任何级别都不会动当前代码目录、shell 启动文件里的 PATH 行、追加到各项目里的回复约定、`[BOT-CLOUD]` 上的外部收件目录，以及 Bot 长期记忆里的内容；`all` 级别会把它们列出来。只运行 `dsh/mac-uninstall.sh` 只会卸载 dsh 插件，见[插件指南](dsh/README.zh-CN.md#卸载)。

## 卸载前检查

1. **`[LOCAL]` 是否还有别的用途共用 Reason Network。** `identity` 和 `all` 级别会退出团队、解除本机所有 agent 的 Reason Network 接入，并删除它的状态。这台电脑如果还用 Reason Network 做别的事，只用 `--level program`。
2. **不是它安装的部署。** 第二个 relay、另一个运行 `*_relay.py` 的登录服务，或手工装的旧布局，会列在 "foreign" 下，并且不会被改动。共用的身份删除后它们就会失效，所以 `identity` 和 `all` 级别会逐个停下，直到你输入 `I accept <编号>`；`--plan` 会把这一点显示为一行 `STOP unless you type I accept <编号>`。进程只显示 PID 和程序名，从不显示参数。
3. **`[BOT-CLOUD]` 上是否还有别的程序用 `/workspace/agent-mesh/box`。** 云端脚本会自己搜索 `/workspace`，对每个引用云端引擎状态或程序的文件、链接打印一行 `SHARED:`。只要还有这样的行，Bot 就不删除任何东西，除非你确认列出的内容都不再使用它们。
4. **还没送达的消息。** 卸载器会显示 relay 队列。`program` 级别为重装保留队列（仍是 `sending` 的条目会变成 `unknown`，不会重发）；更深的级别要你输入 `yes` 才放弃它们。在 `[BOT-CLOUD]` 上，提示词会让 Bot 先问你要不要再运行一次 `rn-cloud.sh fetch`。
5. **会让卸载停下的情况。** 卸载器在改动任何东西之前列出全部原因。任何级别遇到正在运行的 `./update.sh`、被手工改过的登录服务，或者已加载的 `com.local.agent-relay` 服务不是来自本 home 的 plist（它不会被停止），都会停下；`identity` 和 `all` 级别在团队名或 Bot 地址名在配置、团队列表和部署记录之间不一致，或者某个路径是符号链接、属于其他用户时也会停下。这些问题解决之前不会删除任何东西。

## 只卸载 relay

用于重装或换版本；团队、密钥和云端都保持原样。

1. `[GROK-UI]`：暂停 routine。
2. `[LOCAL]`：运行 `./uninstall.sh --level program`，输入 `y`。加 `--yes` 则不询问；这是唯一可以让 agent 代你运行的级别。
3. `[LOCAL]`：重装时先 `git pull`，再运行 `./install.sh --restart-relay`。它用相同的配置、签名密钥和 Bot 地址名重新安装当前目录的 relay，不需要再往 `[BOT-CLOUD]` 复制任何东西。然后恢复 routine。不要重新走完整安装。

## 卸载身份或全部

`identity` 和 `all` 级别只能在你自己的终端里运行；在别处运行时只显示计划，以状态码 3 退出。保持 Grok Bot 界面打开，其中一步在那里完成。

1. **`[LOCAL]` 运行 `./uninstall.sh --level identity` 或 `./uninstall.sh --level all`。** 它展示计划，要求你输入团队名（`identity`）或 `delete <团队名>`（`all`）。
2. **停止 relay**，并重新统计队列。
3. **云端这一步。** 先在 `[GROK-UI]` 暂停 routine，确认没有正在运行的轮次。然后把打印出的提示词作为一条消息发给 Grok Bot。提示词里带有云端脚本和它的 SHA-256 校验值；Bot 在 `[BOT-CLOUD]` 写入脚本、核对校验值、运行 `check`（不改动任何东西），然后报告一个 `PROOF:` 值。
   - 把 `PROOF` 值输入卸载器。只有云端持有这台 Mac 的签名密钥时才会一致，所以不会误删别的部署的云端。卸载器随后显示确认码；把它发给 Bot 并表示同意。Bot 运行 `apply <确认码>`：趁这台 Mac 还在线能收到通知时退出团队，再删除云端文件。Bot 报告 `DONE`（或 `KEPT`，见下文）后输入 `done`；报告 `INCOMPLETE` 时输入 `incomplete`，云端移出团队之前这次卸载一直记为未完成。
   - 输入 `prompt` 可以再显示一次提示词。Bot 打印 `PROOF: none`（云端已经没有签名密钥）时输入 `none`；之后 Bot 要等你在对话里确认这是你自己的部署，才运行 `apply-no-proof`。
   - `[BOT-CLOUD]` 上 `leave` 失败时，Bot 会给你看输出，经你同意才加上 `force`。`all` 级别下，Bot 先列出克隆目录里你自己的文件（`FILE:` 行）。Git 不能列全，或者某个本地分支、标签或 `HEAD` 上有远端分支都没有的提交时，它保留克隆目录并打印 `KEPT:`。云端引擎已经不在、身份文件却还在时，Bot 删除文件后报告 `INCOMPLETE`：云端没能退出团队，卸载器随后会提出在本机把它移出团队。
   - 云端没能完成（输入 `skip`）而它仍在团队里时，卸载器会提出在本机用 `reason-network remove <团队名>/cloud` 把它移出团队。它从不移除其他成员。
4. **dsh 插件**（如果装了）用 `dsh/mac-uninstall.sh --no-restore` 卸载。失败时卸载在动 Reason Network 之前停下。
5. **本机退出团队**，执行 `setup --remove` 并停止引擎。其他成员都离线时，没人能收到这次 `leave`；卸载器会要求你输入 `force`，之后需要他们在自己那边移除这台 Mac。然后删除本级别的文件。
6. **`[GROK-UI]` 删除 routine**，然后输入 `deleted`。只暂停的话，webhook 地址和 key 仍然有效，删除之后才失效。输入 `later` 时，这次卸载记为未完成。
7. 重开各个 agent 会话。

结果逐项列为 `done`、`absent`、`skipped` 或 `failed`，并列出还没完成的事项；只有本级别全部完成时退出状态才是 0。卸载可以重复运行：已完成的步骤显示 `absent` 或 `done`。`./uninstall.sh --cloud-prompt --level identity|all` 只重做云端这一步。

**已经运行过旧版 `./uninstall.sh --all` 时。** 旧版只清理 Mac 一侧，云端可能仍在团队里，密钥也仍然有效。运行 `./uninstall.sh --residual --level identity`（或 `--level all`）。它会询问团队名，并给出一段不带证明的云端提示词；Bot 在你确认后运行 `apply-no-proof`，团队里还有别人时它会停下。

## 确认已卸载干净

- `[LOCAL]`：`launchctl print gui/$(id -u)/com.local.agent-relay` 报错（服务不存在）。`identity` 级别之后，`~/.local/lib/agent-mesh`、`~/.config/agent-relay`、`~/Library/Application Support/agent-relay` 和 `~/.reason/network` 都不存在；`all` 级别之后，`~/.local/bin/reason-network` 和 `~/Library/Logs` 里的 `agent-relay` 日志也不存在。其余情况运行 `./uninstall.sh` 查看。
- `[BOT-CLOUD]`：`identity` 级别之后，`/workspace/agent-mesh` 下的 `box/state`、`box/bin`、`box/person`、`box/relay` 和 `box/outbound.key` 都已删除；`all` 级别之后，`/workspace/agent-mesh` 不存在，除非 Bot 报告了 `KEPT`。
- `[GROK-UI]`：routine 列表里已经没有这个 routine。
