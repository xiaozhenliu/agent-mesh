# 更新日志

[English](CHANGELOG.md) | **简体中文**

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## v0.5.0 — 2026-11-09

relay 与云端脚本的若干修复、可自选的 Bot 地址名，以及分级卸载器。

### 修复

- relay：webhook POST 不再跟随 HTTP 重定向。收到 3xx 按 POST 失败处理，和其他失败一样重试；日志会提示改用最终地址。`WEBHOOK_URL` 必须填最终地址：relay 日志出现 `webhook POST failed: 30x` 时，从 routine 重新复制 webhook URL，再运行 `./install.sh --webhook`。
- relay：RN-OUT 收件地址改为白名单。`to` 必须是 `<person>/<session>[@<machine>]`，每一段由小写字母、数字和短横线组成且首字符是字母或数字；非空的 `reply_to` 必须是普通消息 id。其他值在发送前被拒并发出提醒；该 nonce 不会被占用，改正后的信封可以正常发送。
- relay：拒发或失败过的 nonce 再试、结果变为未知时，撤回先前的拒发或失败提醒，结果未知的提醒注明取代了先前的报告，Bot 不会再对同一条消息同时看到"可以重发"和"不要重发"（#15）。
- relay：RN-OUT 回复已经结束某条消息的待办后，`done` 回执也会撤掉该消息仍在排队的门铃，不再多唤醒一次 Bot（#17）。
- 云端脚本：只按引擎的状态词判断发送结果。收件名或团队名里含 `held`、`error` 等词（例如团队名 `sheldon`）时，已排队的回执、重投请求和 `rn-cloud.sh send` 不再被误判为失败。回执和重投请求中，`rn send` 退出码为 0 但没有 `→` 行时改判为失败，与 relay 原有要求一致（#18）。
- 安装器：团队名和机器名中连用 `-`（如 `a--b`）时，在下载任何内容之前拒绝。引擎会合并连续的 `-`，团队最终的名字与安装器和提示词使用的名字不一致（#25）。
- `box/rn-cloud-setup.sh` 可以在已加入团队的云电脑上重跑：完整的邀请链接表明是同一个团队、云端已经是 `<team-name>/cloud` 时，跳过 `join`，不再停在这一步。短邀请码无法确认这一点，这时照旧由引擎决定，报错会说明原因。

### 新增

- `box/rn-fetch.py send-status`：从 stdin 读 `rn send` 的输出，只打印其中的状态词；`rn-cloud.sh send` 用它判断结果。
- `install.sh --relay-name <name>` 选择 Bot 地址名：relay 用 `<team-name>/<name>` 收信，云端用 `<name>-cloud` 和 `<name>-inbox`。默认 `bot`；1 到 34 个小写字母和数字，用单个 `-` 连接，不能是 `terminal`。名字不能修改，安装没完成时也不行。安装器拒绝团队里已被占用的名字，原子写入 relay 配置，并核对引擎实际注册的正是这个地址；`check.sh` 报告名字和注册情况；`update.sh` 和部署记录拒绝改过的名字。routine 和云端设置提示词带上这个名字；名字为 `bot` 时，routine 提示词与 v0.4.1 逐字节相同。云端的 `rn-cloud-setup.sh` 把名字记在 `box/relay`，`rn-cloud.sh` 和 `rn-fetch.py` 从中读取（没有这个文件时为 `bot`；文件为空或损坏时停止），并在每次注册后核对引擎用的正是这个名字。
- 回归测试：引擎已接受 RN-OUT、结果保存前 relay 停止时，重启后该发送只按结果未知报告一次，不会再发（#14）。
- `box/rn-cloud-uninstall.sh` 和提示词 `prompts/cloud-uninstall.zh-CN.md`：卸载器填好云端脚本，连同它的 SHA-256 校验值嵌进发给 Grok Bot 的提示词。在 `[BOT-CLOUD]` 上，脚本核对团队、云端机器名、`box/person` 和 `box/relay`，以及 Mac 是否在线；`check` 不改动任何东西，打印用这套部署的签名密钥算出的 `PROOF` 值；只有拿到 Mac 对一致的 `PROOF` 给出的确认码，`apply <确认码>` 才会运行，退出团队并删除云端的身份、密钥和名字。它先查找还在用 `box/` 的其他程序（`SHARED:` 行，经用户同意才加 `accept-shared`）；`all` 级别下，删除 `inbox/`、`threads/` 和克隆目录之前先列出用户自己的文件，Git 不能列全，或者某个本地分支、标签或 `HEAD` 上有远端分支都没有的提交时，保留克隆目录。`/workspace` 下任何引用 `agent-mesh/box` 的地方（包括相对路径）以及大到无法检查的文件都算作共用。云端身份还在而引擎已经不在时，以 `INCOMPLETE` 结束，不报 `DONE`。

### 变更

- `./uninstall.sh` 改为同时处理两台电脑的分级卸载器。不带参数时只列出已安装的内容，包括团队、Bot 地址名、队列、dsh 插件和不是它安装的部署；`--plan` 列出每个级别要删除和保留的内容。`--level program` 只删除 relay 程序，保留身份、配置、签名密钥、队列和日志，`./install.sh --restart-relay` 即可装回，不用动云端。`--level identity` 另外让两台电脑都退出团队，删除密钥和身份；`--level all`（`--all`）另外删除日志、引擎、dsh 备份和云端数据。更深的两个级别只能在终端里运行，要求输入团队名（`all` 级别输入 `delete <团队名>`）；名字不一致或路径不安全时在改动任何东西之前停下；从不动其他 relay 或登录服务（`--plan` 显示 `I accept` 这道关；进程不显示参数），从不停止从其他 home 加载的 `com.local.agent-relay` 服务；先完成云端这一步，本机再退出团队。dsh 临时文件是符号链接时，在动 dsh 之前就停下，`dsh/mac-uninstall.sh` 也不再经由它写入。`--residual` 在运行过旧版 `--all` 之后清理云端；`--cloud-prompt` 重做云端这一步。
- `./uninstall.sh` 不带参数时不再删除任何东西，`-y` 只能和 `--level program` 一起用：`./uninstall.sh -y` 和 `./uninstall.sh --all -y` 现在以用法错误结束。

### 升级说明

relay 和云端脚本有改动，routine 提示词不变。运行 `git pull`，然后在终端运行 `./update.sh --lang <en|zh-CN>`，按引导完成各步。云端脚本更新之前，云端仍按旧规则判断，团队名含 `held` 时的误判也照旧。更新 relay 之前，先确认 `WEBHOOK_URL` 是最终地址。

卸载器有改动：运行 `./uninstall.sh -y` 或 `./uninstall.sh --all -y` 的脚本或笔记，要改成 `--level program --yes`，或在终端里运行 `--level identity` / `--level all`；见 README 的卸载一节。卸载旧安装之前先运行 `git pull`。

已有安装保持 Bot 地址名 `bot`，不需要迁移。云端设置脚本有改动，`update.sh` 会问一次云端是否已按这个版本设置：回答"是"即可。下一次运行 `./install.sh --webhook` 时，relay 配置会补上三行名字，取值不变。用了其他 Bot 地址名的安装不能回退到更早的版本：旧的云端脚本忽略 `box/relay`，发往 `<team-name>/bot`。任何情况下都不支持改名；要换名字只能完全卸载后重新安装。

## v0.4.1 — 2026-11-08

Mac 明明在线，云端脚本却报告离线的问题已修复。

### 修复

- `box/rn-cloud.sh`：连接检查在 `set -o pipefail` 下用的是 `doctor | grep -q`。`grep` 匹配到第一行就退出，`doctor` 可能因 SIGPIPE 被终止，于是 Mac 已连接，检查却在约 90 秒后判定失败：`send`、`who`、`init-inbox` 报 "the Mac is not connected" 退出，`fetch` 也跳过了重投请求。现在检查会读完 `doctor` 的全部输出；`doctor` 本身失败时仍判定为未连接。

### 变更

- README：卸载一节改为两台电脑和 routine 的分步顺序，说明哪些东西是共用的、卸载前要先检查什么，以及怎样确认已经卸干净。

### 升级说明

relay 和 routine 提示词没有变化，只有云端脚本更新了。先 `git pull`，再在终端运行 `./update.sh --lang <en|zh-CN>`，按引导让 Grok Bot 拉取新提交。

## v0.4.0 — 2026-11-06

`./update.sh` 直接更新已有安装，不必重装。

### 新增

- `./update.sh` 把 relay、dsh 插件、云端脚本和 routine 提示词与已部署的版本比较。relay 和 dsh 插件由它自己更新；云端脚本或提示词变化时，在终端引导你依次停止 relay、暂停 routine、让 Grok Bot 拉取与本地完全相同的提交、粘贴新提示词、恢复 routine、启动 relay。`--plan` 只显示比较结果；中断的更新再次运行时从停下的步骤继续。已确认的版本记录在 `~/.local/lib/agent-mesh/deployed.json`。
- `./install.sh --restart-relay` 安装当前仓库的 relay 并重启；新 relay 起不来时换回原来的版本。
- `dsh/mac-install.sh --plugin-only` 只替换已安装的插件文件，保留 `~/.dsh/cordis.patch.yml`。

### 变更

- `install.sh` 每次（重新）启动 relay 前都先备份已安装的 relay，等新 relay 报告已启动；没有启动就换回原来的文件。
- `install.sh` 把它打印的云端脚本和 routine 提示词记为待确认，由 `./update.sh` 询问是否已应用。`./uninstall.sh` 删除 relay 时一并删除这份记录。
- `dsh/mac-install.sh` 某一步失败时，除了 patch 也会恢复原来的插件。

### 升级说明

relay、云端脚本和 routine 提示词都没有变化。`update.sh` 是新增的，所以先在本地仓库运行一次 `git pull`，再在终端运行 `./update.sh --lang <en|zh-CN>`。还没有部署记录时，它把云端脚本和提示词视为未知，引导步骤会走一遍，并询问云端是否已按本版本完成初始化（v0.1.0 以来云端初始化无需迁移，正常运行中的安装可以回答是）。此后每次升级都用 `./update.sh`。

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

可靠投递版本：relay 逐条隔离、云端幂等取信、回执与补按门铃、回复去重。

### 新增

- 云端回执与补按门铃（#7）：云端取信后给 relay 发签名回执（新格式 RN-GOT v1）；relay 跟踪每封转发的信，超时未落盘或未处理就补按门铃，多次无果后提醒用户。提醒需要 routine 确认，不会因 spool 溢出丢失。
- 云端重发的回复只投递一次（#8）：带 `reply_to` 的回复由内容派生 nonce，relay 记录每个 nonce 的发送结果，已发出的重复回复直接丢弃，结果未知的不重发。
- 云端孤儿扫描：门铃丢失、无人处理的消息会在下次取信时交出。
- 新命令 `rn-cloud.sh working` 和 `rn-cloud.sh done`。
- relay 配置项 `GOT_TIMEOUT_SECONDS`、`GOT_DONE_SECONDS`、`GOT_MAX_RERINGS`、`NONCE_KEEP_SECONDS`；云端配置项 `RN_CLAIM_LEASE`、`RN_ORPHAN_DAYS`、`RN_ORPHANS`、`RN_GOT`。
- CI：每次推送和 pull request 都在 Linux 和 macOS 上运行离线检查。

### 变更

- 云端每条消息只交给一轮 routine 处理，新增状态 `handled`、`claimed`、`uncertain`；中断过的消息交给用户决定，不自动重做。
- relay 逐条处理消息，一条出错不再影响同批其他消息。
- relay 启动时从本机引擎补回已标读但未处理的消息。
- 新版云端的重投请求不再多按一次门铃。
- 回复被拒、结果未知等代发问题改为 relay 提醒，不再走 spool 通知。
- routine 提示词随上述行为更新。

### 修复

- relay 把所有 HTTP 2xx 的 webhook 响应视为已送达，此前只认 200，会重复唤醒。
- 代发超时不再中断整批消息，改报"未确认"（#1、#5）。

### 升级说明

relay、云端脚本和 routine 提示词须一起更新，并按 [README 的升级顺序](README.zh-CN.md#升级)进行。

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
