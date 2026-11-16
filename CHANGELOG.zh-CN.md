# 更新日志

[English](CHANGELOG.md) | **简体中文**

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## v0.9.1 — 2026-11-17

升级更省事，并包含安全加固（#38）。用 `./update.sh` 更新；本版不更新云端，routine 提示词不用重贴。

### 新增

- `./install.sh --python <path>` 指定 relay 使用的 Python。

### 变更

- relay 使用的 Python 固定下来，在别的终端或虚拟环境里运行 `./update.sh` 不再改动它；`./check.sh` 会显示它。
- 检出里有未提交的改动时，`./update.sh` 列出这些文件，并可以选择用当前检出继续。
- 升级说明新增按来源版本查找的表格。

### 修复

- 安全加固；`./update.sh` 可能因此重启一次引擎和 relay。

## v0.9.0 — 2026-11-16

一台 Mac 可以接多个 Grok Bot（#32）。用 `./update.sh` 更新；本版云端脚本有变化，它会发一次云端更新请求。已有的安装成为第一个 Bot，路径和 routine 提示词都不变。

### 新增

- `./install.sh --add-bot` 添加一个 Grok Bot：只问它在 Grok 里的名字，新 Bot 自己在云端登记。
- `./uninstall.sh --level bot --relay-name <name>` 只卸载一个 Bot，保留其他 Bot。卸载过的名字不能再添加。
- `./check.sh` 按 Bot 分节显示。

### 变更

- `./update.sh` 覆盖所有 Bot，不再要求暂停 routine；`--manual` 仍会要求。
- 有多个 Bot 时，按 Bot 生效的选项需要 `--relay-name <name>`；`--restart-relay` 和 `--rotate-key` 作用于所有 Bot。
- 引擎升级时引擎和所有 relay 一起切换，任何一个失败就全部回滚。
- 云端有多个 Bot 时拒绝 `rn-cloud.sh purge`。

## v0.8.3 — 2026-11-13

从 v0.8.1 或更早版本第一次更新云端，不再停在拒绝上。用 `./update.sh` 更新；本版 routine 提示词有变化，`update.sh` 提示时把新提示词粘贴进 Grok 一次。

### 修复

- routine 提示词新增首次更新命令块的规则：云端上次以 v0.8.1 或更早版本确认时，`./update.sh` 发的是命令块；Grok Bot 不在 routine 运行里执行它，而是告诉你消息 id 和一句确认用的话，你在 Grok 对话里说了之后它才运行并回信。v0.8.2 的提示词会拒绝命令块，`./update.sh` 却说 Grok Bot 会问你，更新要等你自己弄清该怎么做（#37）。
- `./update.sh` 发出首次命令块时、以及停止等待回信时，说明要在 Grok 里做什么。
- `llms.txt` 写明当前版本。

## v0.8.2 — 2026-11-13

更新只需一条命令：`./update.sh` 只问一次，更新 Mac 一侧，并请 Grok Bot 自己更新云端，不用暂停 routine，也不用复制回复。在终端运行 `./update.sh` 更新；本版 routine 提示词有变化，`update.sh` 提示时把新提示词粘贴进 Grok 一次。

### 新增

- `box/rn-cloud-update.sh <tag> <commit> <request>` 用一条命令把云端更新到核对过的发布版本；出问题时输出 `STOP: <原因>`，不做改动。
- routine 提示词收到主人发来的单行 `agent-mesh update <tag> <commit> <request>` 消息时，不再询问，直接运行并回复输出；其他类似更新的消息不运行。
- 终端提示依次按 `--lang en|zh-CN`、安装时记录的语言、`AGENT_MESH_LANG`、英文选择语言；`check.sh` 新增 `--lang`。

### 变更

- `./update.sh` 列出计划后只问一次"继续？[Y/n]"（`--yes` 跳过）。
- 云端需要更新时，`./update.sh` 向 Grok Bot 发送更新消息，最多等待 15 分钟（`AGENT_MESH_REPLY_WAIT`）等它回信；超时未收到时重跑 `./update.sh`。
- 从没有新脚本的版本第一次更新时，改为发送一个命令块；`./update.sh --manual` 保留 v0.8.1 的引导流程。

## v0.8.1 — 2026-11-13

云端安全加固：云端只安装和更新经过核对的已发布版本，云端引擎也不会再被另一个自我更新的引擎守护进程替换。在终端运行 `./update.sh` 更新；云端脚本和云端引擎步骤在同一个引导式云端步骤里完成，routine 提示词不变。

### 安全

- 在云端手动运行引擎命令不会再替换固定的引擎；引擎不属于固定构建时，云端脚本停止运行。
- 云端仓库固定到发布 tag `v<VERSION>`，运行其中的脚本前先核对。

### 变更

- `install.sh --cloud-prompt` 和 `./update.sh` 先在线核对发布 tag 与本地仓库的云端脚本一致；`./update.sh` 新增 `--repo-url`（默认是公开仓库），不再要求本地仓库已提交。
- `rn-cloud-setup.sh --upgrade-engine` 还会替换不属于固定构建的引擎；Grok Bot 的回复必须包含它输出的 `engine: pinned build` 和 `engine: launcher ok` 两行。
- `./update.sh` 只根据 Grok Bot 回复里的 `agent-mesh-result` 行记录云端步骤。

### 修复

- 引擎设置 `auto_update` 和 `lan` 写到能生效的位置。

## v0.8.0 — 2026-11-12

Bot 地址就是 Bot 自己的名字：全新安装会问这个名字，不再默认 `bot`；地址旁显示 Bot 在 Grok 里的名字（#31）。已有安装保留原来的地址。用 `./update.sh` 更新；它只写入填好的回复约定副本，routine 提示词不变。

### 新增

- `install.sh --grok-name <名字>` 记录 Bot 在 Grok 里显示的名字，任何语言都可以；单独使用时只修改这个名字。更新后运行一次即可显示名字（#31）。
- 安装器和 `./check.sh` 显示 `<team-name>/<地址> → Grok: <名字>`（#31）。
- 回复约定模板带上本团队的 Bot 地址和 Grok 名字；`python3 scripts/reply_convention.py refresh <仓库>/AGENTS.md` 更新仓库里的副本。v0.8.0 以前追加的副本，用 `~/.local/lib/agent-mesh/templates/` 里的副本替换（#31）。

### 变更

- 全新安装必须给 `--relay-name`；不给时 `install.sh` 停下，并根据 `--grok-name` 给出建议地址；AGENT-INSTALL 改为询问 Bot 在 Grok 里的名字，不再提供 `bot`（#31）。
- relay env 缺失时，`./check.sh` 和 `install.sh` 读取记录的地址，不再假定是 `bot`（#31）。

### 修复

- `./update.sh --help` 只打印了部分说明。

## v0.7.0 — 2026-11-12

固定 Reason Network 0.12.17，并升级 Mac 和云端上已安装的引擎，先备份、可回滚（#20）；0.10.7 在上游安全公告 GHSA-84r9-56w3-xqp9 的影响范围内。先在 `*.iroh.link` 之外放行 `*.compute.reasonmachines.com`，再在终端运行 `./update.sh`；它的引导式云端步骤在 routine 暂停期间让 Grok Bot 运行 `box/rn-cloud-setup.sh --upgrade-engine`。routine 提示词不变。

### 新增

- `./update.sh` 升级 Mac 上较旧的引擎，失败或按 Ctrl-C 时换回旧引擎，从不降级（[UPGRADE.zh-CN.md](UPGRADE.zh-CN.md#reason-network-引擎)）。升级后重开正在运行的 Claude Code 和 Codex 会话，`./check.sh` 全部通过后删掉引擎备份。
- `box/rn-cloud-setup.sh --upgrade-engine` 在云端做同样的事（#20）。
- `check.sh` 报告引擎版本、未完成的引擎升级和 relay 的注册情况（#20、#30）。

### 变更

- Reason Network 0.12 会遗忘离线超过一天的普通会话，连同未读信一起删除；relay 和云端的信箱不受影响。
- 完整安装遇到较旧的引擎会停止并提示 `./update.sh`（#20）；`./uninstall.sh --level identity` 也会删除引擎备份。

### 修复

- `box/rn-cloud-setup.sh` 不再把固定版本解压覆盖到已有的引擎上，即使那是更新的引擎。
- `./install.sh --restart-relay` 可能接受一个从未 listen 的 relay（#30）。

## v0.6.0 — 2026-11-11

为 Reason Network 0.12 做准备：relay、自检和云端脚本能读懂新旧两个引擎版本的输出；固定的引擎版本仍是 0.10.7（#20）。用 `./update.sh` 更新；云端脚本和 routine 提示词没有变化，它问云端是否已按这个版本设置好时，回答 yes。

### 新增

- README：Reason Network 0.12 的默认中继（`*.compute.reasonmachines.com`）、团队怎样指定或换回中继，以及 0.12 会遗忘离线超过一天的普通会话（#20）。

### 变更

- `check.sh` 和 `install.sh` 探测已装引擎实际使用的中继；判断不出来时，`check.sh` 报 SKIP 并说明要放行什么（#20）。
- relay 也接受来自 `<team-name>/terminal@<CLOUD_MACHINE>` 的云端回执，这是新版引擎给终端发件人的写法（#20）。
- 新 relay 注册成别的名字时，`./install.sh --restart-relay` 恢复原来的 relay 并返回失败（#20）。
- DeepSeek Harness 插件把来自 `terminal@<机器>` 的消息显示为在终端里输入（#20）。

### 修复

- `check.sh`、云端安装和卸载脚本以及 `./uninstall.sh` 能读懂人名后带 `(verified)` 的团队列表，Reason Network 0.12 会这样标出（#20）。

## v0.5.2 — 2026-11-11

让引导式云端更新和自检如实反映实际情况（#26、#28）。用 `./update.sh` 更新；relay、云端脚本和 routine 提示词都没有变化，从已确认的 v0.5.1 升级、且没有未完成的更新时，云端不需要做任何事。

### 变更

- README 变短了：卸载步骤、升级步骤和消息格式原样移到 [UNINSTALL.zh-CN.md](UNINSTALL.zh-CN.md)、[UPGRADE.zh-CN.md](UPGRADE.zh-CN.md) 和 [PROTOCOL.zh-CN.md](PROTOCOL.zh-CN.md)。行为没有变化。

### 修复

- `update.sh` 只有在你粘贴 Grok Bot 的回复、且回复中的提交号和云端脚本校验值与当前仓库一致时，才记录云端已更新；只回答"是"不再算数。发给 Grok Bot 的话也覆盖了云端仓库处于分离 HEAD 的情况（#28）。
- `check.sh` 按 relay 日志里最近一次 webhook POST 判断，失败之后重试已送达的，不再让你去运行 `./install.sh --webhook`（#26）。

## v0.5.1 — 2026-11-11

修复 v0.5.0 新增的注册检查：它使云端在 fetch 之后无法回信。

### 修复

- 云端脚本与安装器：同一个引擎持续运行期间，名称第二次注册时，注册检查不再失败。引擎每次运行只在名称首次注册时记录时间，v0.5.0 新增的检查因此拒绝了之后的每一次注册：云端 `rn-cloud.sh send` 在 `fetch` 之后失败，已安装的机器上 `./install.sh --webhook` 失败。现在只要引擎为该名称保存的会话恰好就是这个名称，检查即通过；带编号的名称或没有会话仍会被拒绝（#27）。

### 升级说明

只有云端脚本和安装器有改动，relay、routine 提示词和云端安装脚本不变。v0.5.0 的云端在 `fetch` 之后无法发出回信，请尽快更新：运行 `git pull`，然后在终端运行 `./update.sh --lang <en|zh-CN>`，按引导让 Grok Bot 拉取新提交。

## v0.5.0 — 2026-11-09

relay 与云端脚本的若干修复、可自选的 Bot 地址名，以及分级卸载器。运行 `git pull`，然后在终端运行 `./update.sh --lang <en|zh-CN>`，问云端是否已设置好时回答"是"；routine 提示词不变。

### 新增

- `install.sh --relay-name <name>` 选择 Bot 地址名（默认 `bot`，已有安装保持不变）。名字不能修改，用了其他名字的安装不能回退到更早的版本。
- 云端卸载提示词只有拿到 Mac 显示的确认码，才删除云端身份。

### 变更

- `./uninstall.sh` 改为同时处理两台电脑的分级卸载器：不带参数时只列出已安装的内容，`--plan` 列出每个级别，`--level program`、`--level identity` 和 `--level all`（`--all`）删除的内容逐级增多。`--residual` 在运行过旧版 `--all` 之后清理云端。
- `-y` 只能和 `--level program` 一起用：把 `./uninstall.sh -y` 或 `./uninstall.sh --all -y` 换成 `--level program --yes`。

### 修复

- relay：webhook POST 不再跟随重定向，`WEBHOOK_URL` 必须填最终地址；relay 日志出现 `webhook POST failed: 30x` 时，从 routine 重新复制 URL，再运行 `./install.sh --webhook`。
- relay：收件地址改为白名单核对，同一条消息不再出现互相矛盾的重发提醒或多余的唤醒（#15、#17），`reason-network listen` 改写显示的信封不再被拒收（#11）。
- 云端脚本：团队名里含 `held` 等词（例如 `sheldon`）时，发送不再被误判为失败（#18）。
- 安装器：`set -o pipefail` 下不再误报失败（#24），连用 `-` 的名字（如 `a--b`）会被拒绝（#25）。

## v0.4.1 — 2026-11-08

Mac 明明在线，云端脚本却报告离线的问题已修复。

### 修复

- `box/rn-cloud.sh`：连接检查在 `set -o pipefail` 下用的是 `doctor | grep -q`。`grep` 匹配到第一行就退出，`doctor` 可能因 SIGPIPE 被终止，于是 Mac 已连接，检查却在约 90 秒后判定失败：`send`、`who`、`init-inbox` 报 "the Mac is not connected" 退出，`fetch` 也跳过了重投请求。现在检查会读完 `doctor` 的全部输出；`doctor` 本身失败时仍判定为未连接。

### 变更

- README：卸载一节改为两台电脑和 routine 的分步顺序，说明哪些东西是共用的、卸载前要先检查什么，以及怎样确认已经卸干净。

### 升级说明

relay 和 routine 提示词没有变化，只有云端脚本更新了。先 `git pull`，再在终端运行 `./update.sh --lang <en|zh-CN>`，按引导让 Grok Bot 拉取新提交。

## v0.4.0 — 2026-11-06

`./update.sh` 直接更新已有安装，不必重装。先运行一次 `git pull`，再在终端运行 `./update.sh --lang <en|zh-CN>`；第一次运行会走一遍引导式云端步骤，正常运行中的安装在被问到云端是否已设置好时可以回答是。relay、云端脚本和 routine 提示词都没有变化。

### 新增

- `./update.sh` 把 relay、dsh 插件、云端脚本和 routine 提示词与已部署的版本比较，relay 和 dsh 插件由它自己更新，其余部分引导你完成。`--plan` 只显示比较结果；中断的更新再次运行时从停下的步骤继续。
- `./install.sh --restart-relay` 安装当前仓库的 relay 并重启；新 relay 起不来时换回原来的版本。
- `dsh/mac-install.sh --plugin-only` 只替换已安装的插件文件。

### 变更

- 新 relay 起不来时，`install.sh` 换回原来的 relay；`dsh/mac-install.sh` 某一步失败时，也会恢复原来的插件。
- `install.sh` 把它打印的云端脚本和 routine 提示词记为待确认，由 `./update.sh` 询问是否已应用。

## v0.3.0 — 2026-11-05

dsh 插件可以干净卸载；DeepSeek Harness 升级导致插件失效时，日志会写明原因。

### 新增

- `dsh/mac-uninstall.sh` 卸载 dsh 插件：只把 `~/.dsh/cordis.patch.yml` 里插件那一块换回 `dsh/mac-install.sh` 保存的 Reason Network 原块，旧安装从最早的备份里取；patch 的其他改动保留。
- dsh 插件加载时检查 DeepSeek Harness 是否具备所需能力；缺了什么就在日志里写明缺项和恢复方法，不兼容的会话跳过，不再在上面出错。

### 变更

- `./uninstall.sh --all` 在 `setup --remove` 之前删除 dsh 插件块，dsh 不会再指向已删除的引擎；这一步失败时会停下来，不删除 Reason Network。

### 升级说明

relay、云端脚本和 routine 提示词没有变化。用了 dsh 插件的，更新本地仓库后重新运行 `dsh/mac-install.sh <team-name>`，再完全退出并重开 DeepSeek Harness。这样会装上带兼容性检查的插件，并为卸载脚本保存 Reason Network 原块；不重装的话，`dsh/mac-uninstall.sh` 会从最早的备份里取原块。其他部分无需更新。

## v0.2.1 — 2026-11-03

维护版本，行为没有变化。

### 变更

- CI 的 shellcheck 作业改为失败即判定构建失败，所有 shell 脚本均已通过。
- `./check.sh` 的 PATH 检查改为提示 "PATH includes ~/.local/bin" 或 "PATH lacks ~/.local/bin"。

### 升级说明

已有部署无需更新。

## v0.2.0 — 2026-11-03

可靠投递：relay 逐条隔离、云端幂等取信、回执与补按门铃、回复去重。relay、云端脚本和 routine 提示词须一起更新，并按 [README 的升级顺序](README.zh-CN.md#升级)进行。

### 新增

- 云端回执与 relay 补按门铃：消息超时未落盘或未处理时，relay 补按门铃，多次无果后提醒用户（#7）。
- 云端重发的回复只投递一次（#8）。
- 门铃丢失的消息会在下次取信时交出。
- 命令 `rn-cloud.sh working` 和 `rn-cloud.sh done`；relay 配置项 `GOT_TIMEOUT_SECONDS`、`GOT_DONE_SECONDS`、`GOT_MAX_RERINGS`、`NONCE_KEEP_SECONDS`；云端配置项 `RN_CLAIM_LEASE`、`RN_ORPHAN_DAYS`、`RN_ORPHANS`、`RN_GOT`。

### 变更

- 云端每条消息只交给一轮 routine 处理，中断过的消息交给用户决定。
- relay 逐条处理消息，启动时补回已标读但未处理的消息。
- 新版云端的重投请求不再多按一次门铃，代发问题改为 relay 提醒。

### 修复

- relay 把所有 HTTP 2xx 的 webhook 响应视为已送达，不再重复唤醒。
- 代发超时不再中断整批消息（#1、#5）。

## v0.1.1 — 2026-11-03

安全加固版本。

### 新增

- 中英文[安全策略](SECURITY.zh-CN.md)。
- `./install.sh --rotate-key`：更换签名密钥。
- 清理命令：本机 `./install.sh --purge-copies`，云端 `rn-cloud.sh purge <天数>`。
- `./check.sh` 检查团队成员，发现异常成员时报 FAIL。
- 回复约定模板增加安全边界。

### 变更

- README 的[安全与隐私](README.zh-CN.md#安全与隐私)一节重写：信任根、本地 agent 的自动批准模式、明文存放位置、团队成员核对。
- 云端取信与清理共用一把锁。
- `./install.sh --webhook` 重写配置时保留用户的可选设置。

### 修复

- 文档中 Codex 的唤醒方式更正为 `codex queue`。

### 升级说明

relay 程序没有变化。云端仓库和已复制到项目里的回复约定需要另行更新。

## v0.1.0 — 2026-11-02

首个带 tag 的版本。

### 新增

- 通过私有 Reason Network 团队连接本地 Claude Code、Codex、pi、DeepSeek Harness 与 Grok Bot：消息签名、webhook 唤醒、完整正文获取与重投。
- 中英文配套指南和提示词，用 `[LOCAL]`、`[BOT-CLOUD]`、`[GROK-UI]` 标明执行位置。
- 支持 Apple Silicon 和 Intel Mac；云端支持 Linux x86_64 和 ARM64。
- 安装前检查已有部署、依赖、系统与 CPU。
- 隔离安装器测试。
- `VERSION` 文件与 `./install.sh --version`。

Reason Network 固定为 v0.10.7，版本号与 agent-mesh 独立。
