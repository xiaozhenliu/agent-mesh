# 安全策略

[English](SECURITY.md) | **简体中文**

## 支持的版本

只有最新发布版本会收到安全修复。当前版本见 [VERSION](VERSION)，也可以在 `[LOCAL]` 运行 `./install.sh --version` 查询。修复随新版本发布，不回补旧版本。

## 报告漏洞

请通过 GitHub 的私密漏洞上报提交：打开仓库的 **Security** 页，点 **Report a vulnerability**，或直接访问 <https://github.com/xiaozhenliu/agent-mesh/security/advisories/new>。报告只有你和维护者能看到。

不要为安全问题公开提 Issue、发讨论或提 pull request，也不要在聊天群里公开细节。

报告里**不要附上真实的凭据**：签名密钥 `outbound.key`、Webhook URL 和 key、团队邀请链接或邀请码。附日志前先删掉这些内容。如果凭据已经出现在别处，先按[怀疑凭据泄露时](#怀疑凭据泄露时)处理，再提交报告。

## 报告里写什么

- agent-mesh 版本和 Reason Network 版本（`./install.sh --version` 会同时打印两者）。
- 涉及的组件和执行位置：`[LOCAL]`、`[BOT-CLOUD]` 或 `[GROK-UI]`。
- 复现步骤，最好能离线复现，例如基于 `tests/relay/` 或 `tests/cloud/` 的测试用例。
- 影响：攻击者需要什么前提，能做到什么。
- 已经知道的缓解办法（如果有）。

## 范围

### 属于本项目

- `[LOCAL]` relay：`agent_relay.py`，包括签名校验、时效与防重放、代发地址限制、速率限制、spool 与正文副本的存储权限。
- `[BOT-CLOUD]` 云端脚本：`box/rn-cloud-setup.sh`、`box/rn-cloud.sh`、`box/rn-fetch.py`、`box/rn-engine.py`、`box/rn-routine.py`、`box/rn-names.py`、`box/rn-cloud-update.sh`、`box/rn-cloud-add-bot.sh`、`box/rn-cloud-uninstall.sh`。
- 安装、自检和卸载脚本：`install.sh`、`check.sh`、`uninstall.sh`、`scripts/`，以及 dsh 插件 `dsh/`。
- [协议参考](PROTOCOL.zh-CN.md#消息格式)中的消息格式：RN-OUT、RN-FWD、RN-REFWD、RN-REFWD-ACK。
- 由安装器渲染或复制的提示词与模板：`prompts/`、`templates/`。例如：提示词让 Grok Bot 执行了它本应先问用户的操作，或者没有指示它把来信正文当作外部数据。

### 请报告给上游

- Reason Network 引擎、iroh 中继和团队加入机制：报告给 [Reason Network](https://github.com/reason-machines/reason-network)。
- Grok Bot、routine、webhook 服务和 CopyToBox：报告给 xAI。
- Claude Code、Codex、pi、DeepSeek Harness 自身的权限和审批行为：报告给各自的开发方。

如果不确定问题属于哪一方，照样私下报告给本项目，我们会协助转交。

### 已知限制，不按漏洞处理

[安全与隐私](README.zh-CN.md#安全与隐私)一节已经写明以下情况：

- Grok Bot 是信任根：它如果被读到的网页或文档诱导（提示词注入），能发出签名有效的消息。签名只防拿不到密钥的会话冒名。
- 同一个 macOS 用户下运行的任何程序都能读到 `~/.config/agent-relay/` 里的密钥和 webhook key。
- 开了自动批准的本地 agent（例如 Claude Code 的 `--dangerously-skip-permissions`、Codex 的 `--yolo`）会把收到的文字当作命令执行，无人值守被叫醒时也一样。
- 消息正文以明文保存在两端的引擎数据库、relay 副本和云端收件目录里，保留时间和清理办法见 README 的[正文以明文保存](README.zh-CN.md#正文以明文保存)。
- 消息可能经过 iroh 公共中继，本项目没有独立审计这些中继。
- 每次叫醒都会消耗 Grok Bot 的用量；relay 只做速率限制。
- 任何团队成员都能给 Grok Bot 发消息并叫醒它；relay 不在团队内部限制发件人。边界在于谁能进团队：`./check.sh` 会报告本地电脑和 `cloud` 以外的成员。

同一台 Mac 上有多个 Grok Bot 时（见[多个 Grok Bot](README.zh-CN.md#多个-grok-bot)），另外还有：

- 这些 Bot 处在同一个信任域。每个 Bot 的 routine 都能读到云电脑上的一切，包括签名密钥，可以冒充别的 Bot；共用的云端账户、引擎、数据库和签名密钥不在 Bot 之间提供隔离。引擎、数据库或共用脚本出故障时，所有 Bot 一起受影响。
- 引擎对整台云电脑只统计一个 outbox。别的 Bot 排队的消息可能让一封已送达的回信停在 `replying`，之后显示为结果不明；这样的回信不会自动重发。
- `./install.sh --rotate-key` 替换所有 Bot 的密钥并重启每个 relay；重启失败时，新密钥不回滚。用旧密钥签过的正文，以及云端拿到新密钥之前的窗口，和只有一个 Bot 时一样存在。
- 云端有多个 Bot 时拒绝 `rn-cloud.sh purge`。
- `agent-mesh rename` 只凭用户自己 Mac 发出的请求修改 Bot 的地址名，云端像核对其他维护请求一样核对它；接收被搁置的历史消息也是如此。30 天内，relay 仍接受 Bot 曾用名下签名有效的控制报文，并取走发往曾用名的信；之后发往旧地址的信在引擎里排队、没人读。收回已卸载 Bot 或早先部署留下的名字，必须由安装器列出证据（会话、所在机器、密钥、没有进程或登录服务在用它），并由你在终端里确认；这些证据排除不了你自己遗忘、当时恰好离线的另一套部署。收回的名字在云端收件箱里已有的消息会被搁置，直到你接受它们。
- 云端维护锁只保护单个命令，不覆盖模型在两次命令之间的处理，也不覆盖它直接读写 thread 笔记。云端更新期间，其他命令最多等它 600 秒，之后报错，什么都不改；routine 会告诉用户，下次叫醒再处理这些信。
- 从 v0.8.2 或 v0.8.3 第一次更新到 v0.9.0 时，云端的旧检出和已经在运行的旧命令不遵守维护锁。这只影响单 Bot 部署，那些版本也只支持单 Bot。
- 旧版本和本版本的管理命令同时启动时，不能完全互相隔开。旧版本的管理工具不能管理多个 Bot。
- 等待添加的 Bot 的云端登记超时、叫醒请求失败、routine 的第一次运行没有完成登记（桩的第 1 条，或完整提示词的第 0 步），或者 Grok 用量不足时，这个 Bot 保持未完成状态，relay 不启动；用 `./install.sh --relay-name <name> --wake-bot` 重试。它永远不会被记为已添加。
- relay 已注册不能证明对应的 Grok routine 可用。负责传递云端更新请求的 Bot 的 routine 失效时，`./update.sh` 会一直等到超时；这时用 `./update.sh --manual` 退出这种状态。
- 任何团队成员都能叫醒其中任何一个 Bot，和只有一个 Bot 时一样。

如果能绕过上述文档写明的防护，比如绕过签名、时效、防重放、地址限制或速率限制，仍属于本项目范围。

## 处理流程

本项目由个人维护，会尽力处理，不承诺固定的响应时间。

1. 确认收到报告，判断是否属于本项目范围。
2. 在私密的安全公告里复现问题，并与报告人沟通。
3. 修复随新版本发布。如果修复涉及 `[BOT-CLOUD]` 或 routine 提示词，发布说明会写明用户需要在哪一端更新。
4. 发布后公开这份安全公告。报告人愿意的话，会在公告中署名致谢。

## 怀疑凭据泄露时

以下命令都在 `[LOCAL]` 执行，除非另有标注。

**签名密钥 `outbound.key`**：生成新密钥并重启 relay（有多个 Bot 时它们共用这一把密钥，每个 relay 都会重启）：

```bash
./install.sh --rotate-key --lang zh-CN
```

然后按它打印的步骤，在 `[GROK-UI]` 让 Grok Bot 用 CopyToBox 把新密钥从 `[LOCAL]` 的 `~/.config/agent-relay/outbound.key` 复制到 `[BOT-CLOUD]` 的 `/workspace/agent-mesh/box/outbound.key`，复制后执行 `chmod 600`，与[云端接入提示词](prompts/cloud-setup.zh-CN.md)第 3 步相同。在云端换上新密钥之前，两个方向的签名校验都会失败。复制完成后运行 `./check.sh`，再发一条测试消息。换钥前已转发、云端还没取回的消息无法再通过校验，`fetch` 报 `mismatch`；它们可能是用泄露的旧密钥伪造的，请发送方重发，旧消息按 README 的[正文以明文保存](README.zh-CN.md#正文以明文保存)一节清理。

**Webhook URL 或 key**：在 `[GROK-UI]` 重新生成该 routine 的 key；界面不支持时，删除 routine 后重建。然后在 `[LOCAL]` 运行 `./install.sh --webhook --lang zh-CN`，填入新值；有多个 Bot 时，加上 `--relay-name <name>` 指定这个 routine 所属的 Bot。

**邀请链接或邀请码**：运行 `reason-network team`（`./check.sh` 也会检查），确认只有你自己的本地电脑和 `cloud`。发现陌生的人或机器，用 `reason-network remove <person>/<machine>` 移除，再按上文更换签名密钥。

**怀疑 Grok Bot 已被诱导执行异常操作**：先在 `[GROK-UI]` 暂停 routine。需要逐条审核来信时，在 `~/.reason/network/config.toml` 里设置 `[inbound] default = "hold"`，用 `reason-network approve` 放行。不再需要的消息正文按 README 的[正文以明文保存](README.zh-CN.md#正文以明文保存)一节清理。
