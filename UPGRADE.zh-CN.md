# 升级

[English](UPGRADE.md) | **简体中文**

本文是 [agent-mesh 使用指南](README.zh-CN.md)的升级部分。`[LOCAL]`、`[BOT-CLOUD]`、`[GROK-UI]` 的含义见[执行位置标识](README.zh-CN.md#执行位置标识)。

本机仓库、Bot 云电脑上的仓库和保存的 routine 提示词各自独立，修改 Mac 文件不会自动更新另外两处；已追加到项目 `AGENTS.md` 的回复约定也要重新复制模板。每个版本需要更新哪些组件，见[更新日志](CHANGELOG.zh-CN.md)。

## 用 update.sh 升级

在 `[LOCAL]` 的仓库里运行：

```bash
./update.sh --lang <en|zh-CN>
```

它先执行 `git pull --ff-only`（加 `--no-pull` 跳过），再逐项与本机已部署的版本比较：

| 组件 | 比较方式 | 不一致时 |
|---|---|---|
| relay | 已安装的 `agent_relay.py` 和登录服务文件与当前仓库比较 | 确认一次后安装并重启（加 `--yes` 或不在终端运行时不确认）；新 relay 起不来时换回原来的版本 |
| dsh 插件 | 已安装 bridge 时，比较已安装的插件文件 | 只替换插件文件，`~/.dsh/cordis.patch.yml` 和你的 bridge 设置保持不变；之后退出并重开 dsh |
| 云端脚本 | `box/rn-cloud.sh`、`box/rn-fetch.py` 与上次确认的版本比较 | 引导步骤，见下文 |
| 云端初始化 | `box/rn-cloud-setup.sh` 与上次确认的版本比较 | 询问云端是否已按本版本完成初始化；需要迁移时，更新日志会写明 |
| routine 提示词 | 所选语言渲染出的提示词与上次确认的版本比较 | 引导步骤，见下文 |

上次确认的版本记录在 `~/.local/lib/agent-mesh/deployed.json`。只有 relay 或 dsh 插件变化时，更新可以无人值守：在终端运行时确认一次，`--yes` 可跳过；不在终端运行时不提问。云端脚本或 routine 提示词变化时需要在终端运行，脚本按"旧组件不会被唤醒"的顺序引导：停止 relay；请你在 `[GROK-UI]` 暂停 routine；打印一段让 Grok Bot 在 `[BOT-CLOUD]` 拉取当前提交的话，以及 Bot 回复中应出现的提交号和校验值；请你粘贴 Grok Bot 的回复并自动比对；打印新的 routine 提示词供你粘贴；请你恢复 routine；最后启动 relay。云端步骤要求本地仓库已提交且与上游一致，保证云端拉到完全相同的文件。

- `./update.sh --plan` 只显示比较结果和将执行的步骤，不改动部署；不加 `--no-pull` 时仍可能先快进本地仓库。
- 退出码 3 表示需要在终端作答，部署未改动。
- 回答"否"、按 Ctrl-C 或某一步失败时，原先运行的 relay 会被重新启动；routine 仍处于暂停状态时会提醒你恢复。再次运行 `./update.sh` 从停下的步骤继续；已完成的步骤不会重做，除非期间版本又有变化。
- 更新还没有 Bot 地址名的旧安装：名字保持 `bot`，routine 提示词不变。云端设置脚本有改动，`update.sh` 会问一次云端是否已按这个版本设置；不需要迁移云端，直接回答"是"。用了其他 Bot 地址名的安装不能回退到没有 Bot 地址名的版本。
- 从 v0.3.0 或更早版本第一次更新：先自己运行一次 `git pull`，因为旧仓库里还没有 `update.sh`。没有部署记录时，云端脚本和提示词按"未知"处理，引导步骤会走一遍。
- 云端步骤：把 Grok Bot 的回复整段粘贴进来，再输入只有一个点（`.`）的一行。回复中的提交号和两个校验值与当前仓库完全一致，才记录云端已更新；只回答"是"、什么都不贴、提交号不同或校验值不同，都不会记录，routine 保持暂停，下次运行 `./update.sh` 会再次请你粘贴回复。云端仓库处于分离 HEAD 时，这段话会让 Grok Bot 先切换到当前仓库跟踪的分支再拉取，遇到问题就停下，不做 reset、clean 或 stash。
- 脚本看不到 routine 和云电脑，只能依据你的回答，以及 Grok Bot 报告的提交号和校验值。

## 手动升级

relay、云端脚本和 routine 提示词都有变化时，按下列顺序一起更新，保证部分组件仍是旧版本时不会有门铃发出：

1. 在 `[LOCAL]` 停止 relay：`launchctl bootout "gui/$(id -u)/com.local.agent-relay"`。此后到达 Mac 的消息留在本机引擎中未读，不会按门铃。
2. 在 `[GROK-UI]` 暂停 routine，等没有运行中的任务。
3. 让 Grok Bot 在 `[BOT-CLOUD]` 更新 `/workspace/agent-mesh`（在对话中直接让 Bot 执行，不经 routine），确认 `rn-cloud.sh` 用法里出现 `done`、`working` 和 `RN_GOT`。
4. 在 `[GROK-UI]` 替换保存的 routine 提示词并恢复 routine。此时 relay 仍停止，不会有门铃。
5. 在 `[LOCAL]` 更新仓库后运行 `./install.sh --restart-relay`，或重新运行完整安装：`./install.sh --name <team-name> --lang <en|zh-CN>`。已配置的 webhook 不会再次询问；安装器会复制新的 `agent_relay.py` 并启动 relay 服务，它先从 `network.db` 对账，再取回停机期间到达的消息。新 relay 起不来时，安装器会换回原来的版本。完整安装还会再次显示 agent 接入计划并要求确认。

relay 停止期间 Mac 收不到推送，消息留在本机引擎里。停机时间要短：云端签名超过 15 分钟才被 relay 转发的回复会因过期被拒绝，不再投递。若第 3、4 步失败，用 `launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.local.agent-relay.plist` 重新启动旧 relay，即回到升级前状态。

日后要回退回执与补按时，按下列顺序操作，避免旧版 relay 收到回执：

1. 在 `[LOCAL]` 的 `~/.config/agent-relay/env` 里设 `GOT_TIMEOUT_SECONDS=0`，用 `launchctl kickstart -k "gui/$(id -u)/com.local.agent-relay"` 重启 relay，保持 routine 启用，等 `~/Library/Logs/agent-relay.log` 里出现 `pending: 0` 和 `alerts: 0`（webhook 能正常送达时，最长约 `GOT_DONE_SECONDS × (GOT_MAX_RERINGS + 1)`）。
2. 在 `[GROK-UI]` 暂停 routine，等没有运行中的任务。
3. 让 Grok Bot 在 `[BOT-CLOUD]` 把 `/workspace/agent-mesh` 回退到上一版，并检查云端引擎的 outbox 为空，再等几分钟，让仍在途中的回执由新版 relay 处理完。
4. 换回上一版 routine 提示词并恢复 routine。
5. 最后回退 `[LOCAL]` 的 relay。
