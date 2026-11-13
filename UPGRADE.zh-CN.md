# 升级

[English](UPGRADE.md) | **简体中文**

本文是 [agent-mesh 使用指南](README.zh-CN.md)的升级部分。`[LOCAL]`、`[BOT-CLOUD]`、`[GROK-UI]` 的含义见[执行位置标识](README.zh-CN.md#执行位置标识)。

本机仓库、Bot 云电脑上的仓库和保存的 routine 提示词各自独立，修改 Mac 文件不会自动更新另外两处；已追加到项目 `AGENTS.md` 的回复约定也不会更新：从 `~/.local/lib/agent-mesh/templates/` 重新复制填好的模板；v0.8.0 及以后的副本也可以运行 `python3 scripts/reply_convention.py refresh <仓库>/AGENTS.md`。`./update.sh` 会自己刷新 `~/.local/lib/agent-mesh/templates/` 里的副本。每个版本需要更新哪些组件，见[更新日志](CHANGELOG.zh-CN.md)。

## 用 update.sh 升级

在 `[LOCAL]` 的仓库里运行：

```bash
./update.sh --lang <en|zh-CN>
```

它先执行 `git pull --ff-only`（加 `--no-pull` 跳过），再逐项与本机已部署的版本比较：

| 组件 | 比较方式 | 不一致时 |
|---|---|---|
| relay | 已安装的 `agent_relay.py` 和登录服务文件与当前仓库比较 | 确认一次后安装并重启（加 `--yes` 或不在终端运行时不确认）；新 relay 起不来时换回原来的版本 |
| 引擎 | `~/.local/bin/reason-network` 与本版本固定的版本比较，并看正在运行的守护进程是否在二进制最后一次替换之后启动 | 引擎较旧时，在终端询问后升级，加 `--yes` 也会问（[Reason Network 引擎](#reason-network-引擎)）；守护进程还在跑被替换前的二进制时，询问后重启；引擎更新或认不出版本时停止更新 |
| dsh 插件 | 已安装 bridge 时，比较已安装的插件文件 | 只替换插件文件，`~/.dsh/cordis.patch.yml` 和你的 bridge 设置保持不变；之后退出并重开 dsh |
| 云端脚本 | `box/rn-cloud.sh`、`box/rn-fetch.py` 与上次确认的版本比较 | 引导步骤，见下文；Grok Bot 核对发布 tag 后切换到它 |
| 云端初始化 | `box/rn-cloud-setup.sh` 与上次确认的版本比较 | 询问云端是否已按本版本完成初始化；需要迁移时，更新日志会写明 |
| 云端引擎 | 本机上次确认的 `[BOT-CLOUD]` Reason Network 版本与固定版本比较 | 询问这次是否做云端引擎这一步；它在引导步骤里进行，见下文 |
| routine 提示词 | 所选语言渲染出的提示词与上次确认的版本比较 | 引导步骤，见下文 |

上次确认的版本记录在 `~/.local/lib/agent-mesh/deployed.json`。只有 relay 或 dsh 插件变化时，更新可以无人值守：在终端运行时确认一次，`--yes` 可跳过；不在终端运行时不提问。云端脚本或 routine 提示词变化时需要在终端运行，脚本按"旧组件不会被唤醒"的顺序引导：停止 relay；请你在 `[GROK-UI]` 暂停 routine；打印一段话，内含让 Grok Bot 在 `[BOT-CLOUD]` 运行的命令块，以及 Bot 回复中应出现的提交号和校验值；请你粘贴 Grok Bot 的回复并自动比对；打印新的 routine 提示词供你粘贴；请你恢复 routine；最后启动 relay。

云端只安装已发布的版本。改动任何东西之前，`./update.sh` 先在线核对：云端安装来源仓库（公开仓库，或 `--repo-url` 指定的仓库）里存在发布 tag `v<VERSION>`，且本地仓库中的 `box/rn-cloud.sh`、`box/rn-fetch.py`、`box/rn-cloud-setup.sh`、`box/rn-engine.py` 与它一致；本地仓库的其他文件可以不同，也不要求已提交或等于上游。tag 取不到或有文件不同时，更新在停止 relay、动引擎之前就停下，什么都不改。`--plan` 会显示核对结果。只有更新涉及云端脚本或云端引擎时才需要联网核对。发给 Grok Bot 的命令块在 `[BOT-CLOUD]` 取回该 tag，核对它指向发布提交、四个文件的校验值符合预期，之后才切换到该 tag（分离 HEAD）并再次核对文件；不符时停止且不改动仓库，或者恢复到原来的提交，并打印 `STOP:` 行，不打印结尾的结果行。

- `./update.sh --plan` 只显示比较结果和将执行的步骤，不改动部署；不加 `--no-pull` 时仍可能先快进本地仓库。
- 退出码 3 表示需要在终端作答，部署未改动。
- 回答"否"、按 Ctrl-C 或某一步失败时，原先运行的 relay 会被重新启动；routine 仍处于暂停状态时会提醒你恢复。再次运行 `./update.sh` 从停下的步骤继续；已完成的步骤不会重做，除非期间版本又有变化。
- 更新还没有 Bot 地址名的旧安装：名字保持 `bot`，routine 提示词不变。云端设置脚本有改动，`update.sh` 会问一次云端是否已按这个版本设置；不需要迁移云端，直接回答"是"。用了其他 Bot 地址名的安装不能回退到没有 Bot 地址名的版本。
- 更新到 v0.8.0：已有安装的地址不变，叫 `bot` 的也一样。用 `./install.sh --grok-name '<它在 Grok 里的名字>'` 记录 Bot 在 Grok 里的名字；它只改变地址旁显示的标签。
- 从 v0.3.0 或更早版本第一次更新：先自己运行一次 `git pull`，因为旧仓库里还没有 `update.sh`。没有部署记录时，云端脚本和提示词按"未知"处理，引导步骤会走一遍。
- 云端步骤：把 Grok Bot 的回复整段粘贴进来，再输入只有一个点（`.`）的一行。回复中有命令块自己打印的结尾几行，每行以 `agent-mesh-result` 开头，分别是发布提交号、这段话列出的每个校验值和 `DONE <tag>`（这段话本身不含这些行，只是复述它的回复不会被记录），云端引擎这一步还要有固定版本、固定构建和 `launcher ok` 三行 `engine:`，才记录云端已更新；只回答"是"、什么都不贴、有 `STOP:` 行、提交号不同或校验值不同，都不会记录，routine 保持暂停，下次运行 `./update.sh` 会再次请你粘贴回复。Grok Bot 不得做 reset、clean 或 stash；云端仓库有本地改动时命令块会停止。
- 更新到 v0.8.1：云端脚本和云端引擎都有变化，引导式云端步骤会走一次，并询问是否做云端引擎这一步，回答"是"。这一步把云端引擎移到 `box/bin/engine` 并核对它（[Reason Network 引擎](#reason-network-引擎)）。routine 提示词不变。回答"否"时，云端运行新脚本，引擎仍在 `box/bin/reason-network`，下次运行 `./update.sh` 会再问。
- 脚本看不到 routine 和云电脑，只能依据你的回答，以及 Grok Bot 报告的提交号和校验值。

## Reason Network 引擎

agent-mesh 0.7.0 把固定的引擎从 Reason Network 0.10.7 换成 0.12.17（0.10.7 在上游安全公告 GHSA-84r9-56w3-xqp9 的影响范围内）。`./update.sh` 会升级两台电脑上的引擎。从任何更早的 agent-mesh 版本都能一次升级完成：它先在旧引擎上装好新 relay，所以不需要先装 0.6.0。

`[LOCAL]` 上的引擎升级需要在终端运行，切换前一定会询问，`--yes` 不会跳过这个问题。顺序如下：

1. 在现有引擎上安装当前仓库的 relay，确认 relay 以自己的名字注册。失败时换回原来的 relay，引擎不动。
2. 列出其他正在使用引擎的进程，例如开着的 Claude Code、Codex 会话。它们不阻止升级，守护进程重启后会继续工作；升级完成后重开这些会话，让它们用上新版本。
3. 下载 0.12.17，按写在 `install.sh` 里的 SHA-256 校验（`box/rn-cloud-setup.sh` 里是同一张表），下载内容被改动就拒绝。下载或校验失败时引擎不变；第 1 步装好的 relay 保持安装并继续运行。
4. 停止 relay，把二进制和 `~/.reason/network` 复制到 `~/.local/lib/agent-mesh/engine-backup-<UTC 时间>/`（权限 700），替换二进制，重启守护进程，并核对正在运行的守护进程是在替换之后启动的、报告 0.12.17、团队里的机器还在、`who` 能正常返回。
5. 启动 relay，确认 relay 已在新的守护进程上注册。

第 4、5 步失败或你按了 Ctrl-C 时，只换回旧二进制，在它上面重启守护进程，再启动 relay。状态目录不恢复：新引擎只加表加列，0.10.7 能打开升级后的库，切换之后收到的信都保留。守护进程在 `reason-network stop` 之后不退出时，先发 SIGTERM，再发 SIGKILL 结束它。换回 0.10.7 不会找回 0.12 已经丢掉的、或 0.10.7 不认识的内容：0.12 运行期间被遗忘的普通会话及其未读信（relay 和云端的信箱不会被遗忘）；以及较新的团队签名证明和 `[folders]` 设置，0.10.7 下次保存团队状态时会丢弃它们。回滚后在 0.10.7 下新建的 CLI 会话不算信箱，下次升级后由 0.12 的 `listen` 重新标记。如果确认不了旧引擎已经在运行，脚本会打印手动处理步骤，并保留记录 `~/.local/lib/agent-mesh/engine-upgrade.json`；这时 `./check.sh` 报 FAIL，下次运行 `./update.sh` 会先回滚这次切换。用备份恢复状态目录只是最后一步手动操作，会丢掉切换之后收到的信。

备份里有这台电脑的引擎身份和收到的消息。`./check.sh` 全部通过后删掉它；`./uninstall.sh --level identity` 也会连同部署记录一起删除它。

`./update.sh` 从不降级：引擎比固定版本新或认不出版本时，它停止运行；`./install.sh` 遇到旧引擎会停止，并提示运行 `./update.sh`。二进制已经是 0.12.17、但正在运行的守护进程早于二进制替换启动时，`./update.sh` 会询问是否重启守护进程。

`[BOT-CLOUD]` 上的引擎升级是引导式云端更新的一步，在 routine 暂停期间进行。发给 Grok Bot 的命令块里多一步 `bash /workspace/agent-mesh/box/rn-cloud-setup.sh --upgrade-engine`，它检查云端引擎并按同样的方式升级：按固定校验值校验、备份到 `box/engine-backup-<UTC 时间>/`、切换、核对正在运行的守护进程，失败时，旧二进制属于固定构建才换回。云端引擎已经是 0.12.17 时只做检查，需要时启动或重启守护进程。

云端引擎放在 `box/bin/engine/reason-network`；`box/bin/reason-network` 是手动运行命令用的启动器，没有 `REASON_NETWORK_HOME` 时拒绝运行，并始终关闭自动更新。这一步把仍在 `box/bin/reason-network` 的引擎（v0.8.0 及更早）移到 `box/bin/engine`，并写入启动器。现有引擎是否可信只看字节、不运行它：不属于 `box/rn-engine.py` 固定构建的二进制（例如被自动更新替换过的，或早先固定的 0.10.7，它没有固定构建值）不运行就直接换成固定版本，原二进制留在备份里，永不放回。固定版本已经放好之后才失败时，打印只使用新二进制的手动步骤；固定版本没能放好时，启动引擎的文件都不改动，这一步提示排除原因后重新运行。Grok Bot 的回复必须包含 `engine: reason-network 0.12.17 running (daemon pid …)`、属于固定构建的 `engine: pinned build <sha256> at box/bin/engine/reason-network` 和 `engine: launcher ok` 三行；`./update.sh` 自动比对，一致才记录云端引擎已确认。`engine: note: other engine daemons run from box/bin` 一行列出此前从旧路径启动的守护进程；它们已无法替换引擎，这一步也不会停止它们（[故障排除](README.zh-CN.md#常见问题)）。云端引擎这一步回答"否"时，汇总里会写明未完成，下次运行 `./update.sh` 会再问。云端引擎是比 0.12.17 新的固定构建时保持不动，这一步失败。

一边是 0.10.7、另一边是 0.12.17 时（哪边先升级都一样），收发、回执和重投都正常：agent-mesh 0.6.0 起能读懂两个引擎的输出。能在同一次运行里升级 Mac 和云端最好。升级之后适用 Reason Network 0.12 的规则：默认中继在 `compute.reasonmachines.com` 下（[网络](README.zh-CN.md#网络)），离线超过一天的普通会话会连同未读信一起被遗忘（[网络与用量](README.zh-CN.md#网络与用量)）。

用 `./check.sh` 检查结果。它报告引擎版本是否与固定版本一致、正在运行的守护进程是否运行已安装的二进制、有没有未完成的引擎升级、relay 是否已在正在运行的守护进程上注册，以及本机上次确认的云端引擎版本。守护进程或 relay 重启后最多 11 分钟内，relay 正在重新注册，注册这一项报 SKIP。

已知限制：

- 守护进程的启动时间和二进制的变更时间按整秒比较，所以旧二进制恰好在替换的同一秒启动的守护进程会被当作新的。Linux 上，仍在运行已被替换的二进制的守护进程，其程序会显示为已删除，因此也能识别；macOS 没有这项检查。
- relay 在 `listen` 返回后才记录注册。守护进程恰好在这之间重启时，`./check.sh` 可能报告过时的注册，几秒后的下一次 `listen` 会纠正。
- relay 首次注册前的启动工作（补收错过的信、补发、webhook）可能耗尽 45 秒的注册等待，这次重启就按失败处理并回滚。用 `AGENT_MESH_REGISTER_WAIT=<秒数>` 放宽等待。
- 回滚成功但 relay 没能重新启动时，升级记录已经删除，下次运行 `./update.sh` 不会重试；按提示运行 `./install.sh --restart-relay`。
- `./check.sh` 不比对云端脚本的校验值，只报告本机上次确认的云端引擎版本。`./update.sh` 引导云端更新时会比对校验值，`[BOT-CLOUD]` 上的每条 `rn-cloud.sh` 命令都会核对引擎字节。

## 手动升级

relay、云端脚本和 routine 提示词都有变化时，按下列顺序一起更新，保证部分组件仍是旧版本时不会有门铃发出：

1. 在 `[LOCAL]` 停止 relay：`launchctl bootout "gui/$(id -u)/com.local.agent-relay"`。此后到达 Mac 的消息留在本机引擎中未读，不会按门铃。
2. 在 `[GROK-UI]` 暂停 routine，等没有运行中的任务。
3. 让 Grok Bot 在 `[BOT-CLOUD]` 把 `/workspace/agent-mesh` 更新到发布 tag（在对话中直接让 Bot 执行，不经 routine）。使用 `./update.sh` 打印的命令块，它先核对 tag 的提交号和云端脚本的校验值再切换；直接 `git pull` 会运行未经核对的文件。
4. 在 `[GROK-UI]` 替换保存的 routine 提示词并恢复 routine。此时 relay 仍停止，不会有门铃。
5. 在 `[LOCAL]` 更新仓库后运行 `./install.sh --restart-relay`，或重新运行完整安装：`./install.sh --name <team-name> --lang <en|zh-CN>`。两者都不升级较旧的 Reason Network：`--restart-relay` 保留现有引擎，完整安装遇到较旧的引擎会停止。引擎用 `./update.sh` 升级（[Reason Network 引擎](#reason-network-引擎)），云端也可以在 routine 暂停时手动运行 `bash /workspace/agent-mesh/box/rn-cloud-setup.sh --upgrade-engine`。已配置的 webhook 不会再次询问；安装器会复制新的 `agent_relay.py` 并启动 relay 服务，它先从 `network.db` 对账，再取回停机期间到达的消息。新 relay 起不来时，安装器会换回原来的版本。完整安装还会再次显示 agent 接入计划并要求确认。

relay 停止期间 Mac 收不到推送，消息留在本机引擎里。停机时间要短：云端签名超过 15 分钟才被 relay 转发的回复会因过期被拒绝，不再投递。若第 3、4 步失败，用 `launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.local.agent-relay.plist` 重新启动旧 relay，即回到升级前状态。

日后要回退回执与补按时，按下列顺序操作，避免旧版 relay 收到回执：

1. 在 `[LOCAL]` 的 `~/.config/agent-relay/env` 里设 `GOT_TIMEOUT_SECONDS=0`，用 `launchctl kickstart -k "gui/$(id -u)/com.local.agent-relay"` 重启 relay，保持 routine 启用，等 `~/Library/Logs/agent-relay.log` 里出现 `pending: 0` 和 `alerts: 0`（webhook 能正常送达时，最长约 `GOT_DONE_SECONDS × (GOT_MAX_RERINGS + 1)`）。
2. 在 `[GROK-UI]` 暂停 routine，等没有运行中的任务。
3. 让 Grok Bot 在 `[BOT-CLOUD]` 把 `/workspace/agent-mesh` 回退到上一版，并检查云端引擎的 outbox 为空，再等几分钟，让仍在途中的回执由新版 relay 处理完。
4. 换回上一版 routine 提示词并恢复 routine。
5. 最后回退 `[LOCAL]` 的 relay。
