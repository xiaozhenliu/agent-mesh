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
- 复现步骤，最好能离线复现，例如基于 `test_relay.py` 或 `box/test_rn_fetch.py` 的测试用例。
- 影响：攻击者需要什么前提，能做到什么。
- 已经知道的缓解办法（如果有）。

## 范围

### 属于本项目

- `[LOCAL]` relay：`agent_relay.py`，包括签名校验、时效与防重放、代发地址限制、速率限制、spool 与正文副本的存储权限。
- `[BOT-CLOUD]` 云端脚本：`box/rn-cloud-setup.sh`、`box/rn-cloud.sh`、`box/rn-fetch.py`。
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
- 开了自动批准的本地 agent（例如 Claude Code 的 `--dangerously-skip-permissions`、Codex 的 `--yolo`）会把收到的文字当作命令执行，无人值守被唤醒时也一样。
- 消息正文以明文保存在两端的引擎数据库、relay 副本和云端收件目录里，保留时间和清理办法见 README 的[正文以明文保存](README.zh-CN.md#正文以明文保存)。
- 消息可能经过 iroh 公共中继，本项目没有独立审计这些中继。
- 每次唤醒都会消耗 Grok Bot 的用量；relay 只做速率限制。

如果能绕过上述文档写明的防护，比如绕过签名、时效、防重放、地址限制或速率限制，仍属于本项目范围。

## 处理流程

本项目由个人维护，会尽力处理，不承诺固定的响应时间。

1. 确认收到报告，判断是否属于本项目范围。
2. 在私密的安全公告里复现问题，并与报告人沟通。
3. 修复随新版本发布。如果修复涉及 `[BOT-CLOUD]` 或 routine 提示词，发布说明会写明用户需要在哪一端更新。
4. 发布后公开这份安全公告。报告人愿意的话，会在公告中署名致谢。

## 怀疑凭据泄露时

以下命令都在 `[LOCAL]` 执行，除非另有标注。

**签名密钥 `outbound.key`**：生成新密钥并重启 relay：

```bash
./install.sh --rotate-key --lang zh-CN
```

然后按它打印的步骤，在 `[GROK-UI]` 让 Grok Bot 用 CopyToBox 把新密钥从 `[LOCAL]` 的 `~/.config/agent-relay/outbound.key` 复制到 `[BOT-CLOUD]` 的 `/workspace/agent-mesh/box/outbound.key`，复制后执行 `chmod 600`，与[云端接入提示词](prompts/cloud-setup.zh-CN.md)第 3 步相同。在云端换上新密钥之前，两个方向的签名校验都会失败。复制完成后运行 `./check.sh`，再发一条测试消息。换钥前已转发、云端还没取回的消息无法再通过校验，`fetch` 报 `mismatch`；它们可能是用泄露的旧密钥伪造的，请发送方重发，旧消息按 README 的[正文以明文保存](README.zh-CN.md#正文以明文保存)一节清理。

**Webhook URL 或 key**：在 `[GROK-UI]` 重新生成该 routine 的 key；界面不支持时，删除 routine 后重建。然后在 `[LOCAL]` 运行 `./install.sh --webhook --lang zh-CN`，填入新值。

**邀请链接或邀请码**：运行 `reason-network team`（`./check.sh` 也会检查），确认只有你自己的本地电脑和 `cloud`。发现陌生的人或机器，用 `reason-network remove <person>/<machine>` 移除，再按上文更换签名密钥。

**怀疑 Grok Bot 已被诱导执行异常操作**：先在 `[GROK-UI]` 暂停 routine。需要逐条审核来信时，在 `~/.reason/network/config.toml` 里设置 `[inbound] default = "hold"`，用 `reason-network approve` 放行。不再需要的消息正文按 README 的[正文以明文保存](README.zh-CN.md#正文以明文保存)一节清理。
