# 升级

[English](UPGRADE.md) | **简体中文**

本文是 [agent-mesh 使用指南](README.zh-CN.md)的升级部分。`[LOCAL]`、`[BOT-CLOUD]`、`[GROK-UI]` 的含义见[执行位置标识](README.zh-CN.md#执行位置标识)。

本机仓库、Bot 云电脑上的仓库和保存的 routine 提示词各自独立，修改 Mac 文件不会自动更新另外两处；已追加到项目 `AGENTS.md` 的回复约定也不会更新：从 `~/.local/lib/agent-mesh/templates/` 重新复制填好的模板；v0.8.0 及以后的副本也可以运行 `python3 scripts/reply_convention.py refresh <仓库>/AGENTS.md`。`./update.sh` 会自己刷新 `~/.local/lib/agent-mesh/templates/` 里的副本。每个版本需要更新哪些组件，见[更新日志](CHANGELOG.zh-CN.md)。

## 从哪个版本来

按你现在的版本找到对应的一行；`./install.sh --version` 会打印版本。每一行最后都运行 `./check.sh`。

| 现在的版本 | 运行 | 除更新这台 Mac 之外 |
|---|---|---|
| v0.10.0 及以后 | `agent-mesh update`（或 `./update.sh`） | 云端有变化时发一次云端更新请求，Grok Bot 不用先问就运行。除非 routine 桩本身改版，不用粘贴。 |
| v0.8.2 – v0.9.1 | `./update.sh` | 发一次云端更新请求，Grok Bot 不用先问就运行；然后每个 Bot 粘贴一次 [routine 桩](#routine-桩)，这台 Mac 再请每个 Bot 确认它。 |
| v0.4.0 – v0.8.1 | `git pull --ff-only && ./update.sh` | 先粘贴 routine 桩；然后 Grok Bot 自己运行这台 Mac 发来的更新请求，不用在 Grok 对话里确认（[用这个版本第一次更新云端](#用-updatesh-升级)）。v0.6.0 及更早还会升级两台电脑上的引擎（[Reason Network 引擎](#reason-network-引擎)）。要先拉取：v0.4.0 – v0.6.0 的 `update.sh` 在引擎已自行更新时会在拉取前停下。 |
| v0.3.0 及更早 | `git pull --ff-only && ./update.sh` | v0.3.0 及更早的 checkout 没有 `update.sh`，这台 Mac 也没有云端一侧的记录，所以 `./update.sh` 像 `--manual` 那样手动完成云端步骤：在 Grok 对话里运行云端命令块，然后粘贴 routine 桩。 |

第一次安装在 v0.8.0 及更早的 Mac，`./update.sh` 还可能修正引擎设置，并重启一次引擎和 relay（[引擎设置](#reason-network-引擎)）。

## 用 update.sh 升级

在 `[LOCAL]` 的仓库里运行：

```bash
./update.sh --lang <en|zh-CN>
```

它先取回上游分支及其 tag，把 checkout 前进到该分支上最新的发布 tag `vX.Y.Z`（加 `--no-pull` 跳过），再逐项与本机已部署的版本比较。分支上最新发布版之后的提交不会取用。checkout 已经包含这个发布版，或分支上没有发布 tag 时，按 checkout 现状使用；checkout 有不在发布版里的提交时，在改动任何东西之前停下，并给出按现状使用 checkout 的命令。checkout 里已跟踪的文件有未提交的改动时，它不拉取，而是列出这些文件（最多 20 个，超出时给出总数）：在终端里且没有 `--yes` 时问一次是否用当前的 checkout 继续，等同 `--no-pull`；否则停下，并给出这样做的完整命令。改动涉及云端脚本时，下文的发布版核对仍会在动云端之前停止更新。

| 组件 | 比较方式 | 不一致时 |
|---|---|---|
| relay | 已安装的 `agent_relay.py` 和登录服务文件与当前仓库比较，登录服务文件按 relay 固定使用的 Python 生成 | 安装并重启；新 relay 起不来时换回原来的版本 |
| 引擎 | `~/.local/bin/reason-network` 与本版本固定的版本比较，看正在运行的守护进程是否在二进制最后一次替换之后启动，并看 `auto_update` 和 `lan` 是否在引擎读取的位置关闭 | 引擎较旧时升级（[Reason Network 引擎](#reason-network-引擎)）；守护进程还在跑被替换前的二进制时重启；设置没有关闭时修正并重启守护进程；引擎更新或认不出版本时停止更新 |
| dsh 插件 | 已安装 bridge 时，比较已安装的插件文件 | 只替换插件文件，`~/.dsh/cordis.patch.yml` 和你的 bridge 设置保持不变；之后退出并重开 dsh |
| 云端脚本 | `box/rn-cloud.sh`、`box/rn-fetch.py`、`box/rn-cloud-update.sh`、`box/rn-cloud-add-bot.sh`、`box/rn-routine.py`、`box/rn-names.py` 和规则模板 `prompts/routine-body.md`、`prompts/routine-body.zh-CN.md` 与上次确认的版本比较 | 云端更新，见下文 |
| 云端初始化 | `box/rn-cloud-setup.sh` 与上次确认的版本比较 | 云端更新，见下文；它运行 `box/rn-cloud-setup.sh --upgrade-engine`，云端迁移都放在这里 |
| 云端引擎 | 本机上次确认的 `[BOT-CLOUD]` Reason Network 版本与固定版本比较 | 云端更新，见下文；每次都检查云端引擎 |
| routine 提示词 | 所选语言的 routine 桩（加 `--full-prompt` 时为完整提示词）与上次确认的版本比较 | 打印出来，请你在 `[GROK-UI]` 粘贴进 routine；桩随后经读回确认（[routine 桩](#routine-桩)） |

上次确认的版本记录在 `~/.local/lib/agent-mesh/deployed.json`。计划列出有变化的部分，只问一次 `继续？[Y/n]`；`--yes` 跳过这一问。不在终端运行时，只有不需要回答的情况才会执行：只有 relay 或 dsh 插件变化，或加了 `--yes`。不在终端运行或加了 `--yes` 时，有变化的 routine 提示词这次不换：云端用现有提示词已经认识的请求更新，Grok Bot 继续用现有提示词，下一次在终端不加 `--yes` 运行 `./update.sh` 时再显示新的。必须先粘贴提示词时（上次以 v0.8.1 或更早版本确认的云端，见下文），或之前显示过的提示词一直没有确认时，仍会停下。

顺序是：先更新这台 Mac（回滚未完成的引擎升级、升级引擎、替换 relay 和 dsh 插件），然后是云端和 routine 提示词。上次以 v0.8.2 或更新版本确认的云端先更新，用的是它现有提示词已经认识的请求；然后才显示桩，这样你粘贴时它要加载的规则已经在云端。上次以 v0.8.1 或更早版本确认的云端，旧提示词运行不了这条请求，所以先显示桩，再更新云端（[用这个版本第一次更新云端](#用-updatesh-升级)）。把提示词粘贴进 routine 后回答已保存；桩随后经读回确认。

**relay 的 Python。**relay 的登录服务文件里写着它用哪个 Python 运行。从 v0.9.1 起这个 Python 固定下来：它记录在 `~/.local/lib/agent-mesh/deployed.json`（`relay_python`）里，`./update.sh` 和 `./install.sh` 都按它生成登录服务文件，与运行脚本的 `python3` 无关。所以在别的终端或虚拟环境里运行 `./update.sh`，不再改动登录服务文件，也不重启 relay。已有的安装沿用登录服务文件里现在写的 Python，原样不变，即使它在虚拟环境或临时目录里；这时计划和 `./check.sh` 各提醒一行。新安装依次取 `/opt/homebrew/bin/python3`、`/usr/local/bin/python3`、`/usr/bin/python3`（要求已安装 Xcode 命令行工具），不使用虚拟环境和临时目录里的；都没有时停下，请你用 `--python` 指定。`./install.sh --python <path>` 检查给定的 Python（3.9+，带 `ssl` 和 `sqlite3`），重写所有 Bot 的登录服务文件并重启 relay，relay 起来之后才记录这个路径；有 relay 起不来时，登录服务文件和记录都保持原样。不加 `--python` 而更换 Python 并重启 relay，只发生在两种情况：固定的 Python 不存在或已不能用；各个 Bot 的登录服务文件写的不是同一个（以第一个 Bot 为准）。计划里会说明。

**云端更新。**`./update.sh` 经 relay 从这台 Mac 给 Grok Bot 发一条普通消息：`agent-mesh update <tag> <commit> <请求号>`。routine 桩加载的规则（v0.8.2 – v0.9.1 则是 routine 提示词）里有专门针对这条消息的规则：发件人与云端属于同一个人（也就是用户自己的 Mac），且正文恰好是这一行时，Grok Bot 不用先问，运行 `bash /workspace/agent-mesh/box/rn-cloud-update.sh '<tag>' '<commit>' '<请求号>'`，并把完整输出回复过来。别人发来的同样消息不会被执行。`./update.sh` 最多等 15 分钟，按请求号在这台 Mac 的 Reason Network 数据库里找到回信，核对后记录云端已更新。云端更新期间 routine 照常运行，relay 也不停止。

`[BOT-CLOUD]` 上的 `box/rn-cloud-update.sh` 核对 tag、提交和请求号的格式，拒绝比云端仓库版本更旧的 tag（从不降级），云端仓库有本地改动时停止并列出改动的文件，然后取回该 tag，核对它指向发布提交，切换到它（分离 HEAD），再运行新 checkout 里的同名脚本；后者运行 `box/rn-cloud-setup.sh --upgrade-engine` 并打印结尾的结果行。脚本第一行是 `agent-mesh-result request <请求号>`；任何一步失败都打印 `STOP:` 行，不打印结尾的结果行。引擎步骤失败时保留新 checkout。

回信中有脚本自己打印的结尾几行，每行以 `agent-mesh-result` 开头，分别是发布提交号、九个云端文件各自的校验值和 `DONE <tag>`，并有固定版本、固定构建和 `launcher ok` 三行 `engine:`，才记录云端已更新。有 `STOP:` 行、提交号不同或校验值不同时不记录；`./update.sh` 显示原因，下次运行会发新的请求。15 分钟内没有回信时，Grok Bot 可能还在执行（结果也会出现在 Grok 里）：下次运行 `./update.sh` 先按保存的请求号查找回信，找到就记录，找不到才重新发送。针对其他版本的请求，或在这个 checkout 变化之前发出的请求，它们的回信不会被记录。

**用这个版本第一次更新云端。**上次以 v0.8.1 或更早版本确认的云端还没有 `box/rn-cloud-update.sh`，旧的 routine 提示词也没有更新请求的规则。这时先粘贴 routine 桩，然后 `./update.sh` 发那一行请求，后面跟一行 `lang=<en|zh-CN>`。在这样的旧云端上，桩还加载不出规则，于是按它的第 3 条用本次运行的消息 id 运行一条固定命令：取回这些消息，遇到以 `agent-mesh update <tag>` 开头的消息时，取回该发布 tag 并运行这个 tag 里的 `box/rn-cloud-update.sh`。这个脚本在改动任何东西之前自己核对发件人（用户自己的 Mac）、请求和 tag，按上文更新云端仓库，发出回信，并让 Grok Bot 在同一次运行里加载新规则。不需要在 Grok 对话里确认。重新加载之后，这次运行用新规则处理其他消息。

没有按时收到回信或云端更新失败时，旧云端加载不了桩的规则，所以 `./update.sh` 打印完整提示词，请你贴回 routine；routine 随即照旧工作，下次运行 `./update.sh` 先查找保存的请求的回信，找不到就重试。完全没有云端一侧记录的 Mac（v0.3.0 或更早）像 `--manual` 那样手动完成云端步骤，之后再显示桩。

云端只安装已发布的版本。改动任何东西之前，`./update.sh` 先在线核对：云端安装来源仓库（公开仓库，或 `--repo-url` 指定的仓库）里存在发布 tag `v<VERSION>`，且本地仓库中的 `box/rn-cloud.sh`、`box/rn-fetch.py`、`box/rn-cloud-setup.sh`、`box/rn-engine.py`、`box/rn-cloud-add-bot.sh`、`box/rn-routine.py`、`box/rn-names.py`、`prompts/routine-body.md`、`prompts/routine-body.zh-CN.md`、`box/rn-cloud-update.sh` 与它一致；本地仓库的其他文件可以不同，也不要求已提交或等于上游。tag 取不到或有文件不同时，更新在停止 relay、动引擎之前就停下，什么都不改。`--plan` 会显示核对结果。只有更新涉及云端时才需要联网核对。

- `./update.sh --plan` 只显示比较结果和将执行的步骤，不改动部署；不加 `--no-pull` 时仍可能先快进本地仓库。
- 退出码 3 表示需要在终端作答，部署未改动。退出码 1 表示没有全部完成，例如还没收到 Grok Bot 的回信；汇总里写明是哪一部分。各部分都已完成、只有最后的 `./check.sh` 没通过时，汇总会单独说明。
- **`agent-mesh` 命令。**`./update.sh`（`--plan` 除外）和 `./install.sh` 把这个 checkout 记录在 `~/.local/lib/agent-mesh/deployed.json` 里，并写入 `~/.local/bin/agent-mesh`。之后 `agent-mesh update [选项]` 和 `agent-mesh check [选项]` 可以在任何目录运行所记录 checkout 里的 `./update.sh` 和 `./check.sh`。在另一个 checkout 里运行 `./update.sh` 会把命令指向那里。`~/.local/bin` 不在 `PATH` 里时，脚本会提示。这个位置上不是 agent-mesh 写的文件保持不动。`./uninstall.sh --level program` 会删除这个命令。
- `./update.sh --manual` 改为手动更新云端：停止 relay；请你在 `[GROK-UI]` 暂停 routine；打印一段话，内含上面的命令块（在对话里直接发给 Grok Bot，不经过 routine），以及 Bot 回复中应出现的提交号和校验值；请你粘贴 Grok Bot 的完整回复，再输入只有一个点（`.`）的一行，并自动比对；打印新的 routine 提示词供你粘贴；请你恢复 routine；最后启动 relay。回答"否"、按 Ctrl-C 或某一步失败时，原先运行的 relay 会被重新启动；routine 仍处于暂停状态时会提醒你恢复。再次运行 `./update.sh` 从停下的步骤继续；已完成的步骤不会重做，除非期间版本又有变化。
- 改名没完成时更新会停下：再运行 `agent-mesh rename` 完成它，或运行 `agent-mesh rename --cancel`（见[给 Bot 改名](README.zh-CN.md#给-bot-改名)）。
- Grok Bot 不得做 reset、clean 或 stash；云端仓库有本地改动时更新会停止。和 Grok Bot 一起处理它列出的文件，然后重新运行 `./update.sh`。
- 更新还没有 Bot 地址名的旧安装：名字保持 `bot`。用了其他 Bot 地址名的安装不能回退到没有 Bot 地址名的版本。
- 更新到 v0.8.0：已有安装的地址不变，叫 `bot` 的也一样。用 `./install.sh --grok-name '<它在 Grok 里的名字>'` 记录 Bot 在 Grok 里的名字；它只改变地址旁显示的标签。
- 从 v0.8.0 或更早版本更新：云端更新同时把云端引擎移到 `box/bin/engine` 并核对它（[Reason Network 引擎](#reason-network-引擎)）。
- 从 v0.3.0 或更早版本第一次更新：先自己运行一次 `git pull`，因为旧仓库里还没有 `update.sh`。没有部署记录时，云端各部分和提示词按"未知"处理，云端更新（像 `--manual` 那样手动完成）和提示词步骤会走一遍。
- 脚本看不到 routine 和云电脑，只能依据你的回答，以及 Grok Bot 回信里报告的内容。

## routine 桩

从 v0.10.0 起，routine 提示词是一段固定的短文本，即 routine 桩。每次运行时 Grok Bot 先运行 `bash /workspace/agent-mesh/box/rn-cloud.sh instructions 1`，它的输出就是提示词的其余部分：在 `[BOT-CLOUD]` 上用云端 checkout 里的模板 `prompts/routine-body.md`、`prompts/routine-body.zh-CN.md` 渲染出的规则。所以改动规则的云端更新不用重贴。只有桩本身改版（第一行写着它的版本）时才要再贴。两种语言用的是同一段英文桩，所以改换语言也不用重贴：下面的读回请求把语言带到 `[BOT-CLOUD]`。后加 Bot 的桩还写明是哪个 Bot。

你粘贴桩并回答已保存后，`./update.sh` 给这个 Bot 发一条读回请求：`agent-mesh stub <请求号>` 和一行 `lang=<en|zh-CN>`。加载的规则把它交给 `rn-cloud.sh request`，回信写明那次运行加载的桩版本，以及云端从此渲染规则所用的语言。两者与你粘贴的一致，才把提示词记为已确认；否则 `./update.sh` 显示哪里不同，下次运行发新的读回请求。回信表明那次运行没有经桩加载规则，说明桩还不在 routine 里：下次运行 `./update.sh` 会再次显示桩，供你粘贴。没有按时收到回信时，提示词仍未确认：下次运行 `./update.sh` 先查找保存的请求迟到的回信并记录；没有的话再次显示桩供你粘贴，然后发新的读回请求。读回要求这个 Bot 的 relay 在运行，并且云端更新已经记录。

`./update.sh --full-prompt` 改为显示完整提示词：在这台 Mac 上渲染的、与桩加载的相同的规则，照以前的方式粘贴并确认，没有读回。用完整提示词时，规则每次变化都要重贴。这个选项只对这一次运行有效；之后不带它运行会重新显示桩。

## 多个 Bot

这台 Mac 上有多个 Grok Bot 时（见[多个 Grok Bot](README.zh-CN.md#多个-grok-bot)），一次 `./update.sh` 覆盖所有 Bot；只有一个 Bot 的 Mac 看到的输出和以前一样。

- **计划。** 结果行是所有 Bot 合起来的结果，后面按顺序每个 Bot 一行，写出它的 relay 和提示词状态。还在等云端登记的 Bot 会附上 `./install.sh --relay-name <name> --wake-bot`，用它叫醒。
- **未完成的添加。** 显示计划之前，`./update.sh` 为每个云端登记仍未确认的添加 Bot（例如 `./install.sh --add-bot` 停止等待之后）查找回信，已经到达的就记录下来。
- **Routine 提示词。** 每个 Bot 有自己的 routine 提示词。变化了的提示词逐个显示，并写明属于哪个 Bot；把它粘贴进那个 Bot 的 routine 并确认。每个 Bot 的桩随后经它自己的读回确认。云端登记还没确认的 Bot 会跳过。中途停下时，已确认的提示词保持记录，下次只问其余的。切换语言不用重贴，由每个 Bot 的读回带过去。
- **引擎和 relay。** 一次引擎步骤覆盖每个已安装 Bot 的 relay：一起停止，引擎只切换或重启一次，再一起启动（见 [Reason Network 引擎](#reason-network-引擎)）。失败时每个 relay 都回到之前的状态。
- **云端。** 这些 Bot 共用一台云电脑，所以云端更新只运行一次。请求经由按顺序第一个云端登记已确认、relay 在运行并已登记的 Bot 发出；一个都没有时，`./update.sh` 在发送任何东西之前停下，建议运行 `./install.sh --restart-relay`。Routine 不暂停：更新运行期间云端脚本持有一把维护锁，其他 Bot 的云端命令等它释放，最多 10 分钟。
- **`--manual`**，以及 `./update.sh` 继续执行的未完成 `--manual` 运行，会停止每个 Bot 的 relay，请你暂停、之后再恢复每个 Bot 的 routine。中途停下时，它会列出 routine 仍在暂停的 Bot。
- 有 Bot 正在卸载时，`./update.sh` 会停下，直到 `./uninstall.sh --level bot --relay-name <name>` 完成（见[只卸载一个 Bot](UNINSTALL.zh-CN.md#只卸载一个-bot)）。

## Reason Network 引擎

agent-mesh 0.7.0 把固定的引擎从 Reason Network 0.10.7 换成 0.12.17（0.10.7 在上游安全公告 GHSA-84r9-56w3-xqp9 的影响范围内）。`./update.sh` 会升级两台电脑上的引擎。从任何更早的 agent-mesh 版本都能一次升级完成，不需要先装 0.6.0。

`[LOCAL]` 上的引擎切换包含在 `./update.sh` 列出计划后唯一那次"继续？"里；`--yes` 代为回答，所以没有终端时也能切换。顺序如下：

1. 列出其他正在使用引擎的进程，例如开着的 Claude Code、Codex 会话。它们不阻止升级，守护进程重启后会继续工作；升级完成后重开这些会话，让它们用上新版本。
2. 下载 0.12.17，按写在 `install.sh` 里的 SHA-256 校验（`box/rn-cloud-setup.sh` 里是同一张表），下载内容被改动就拒绝。下载或校验失败时什么都不改；relay 照原样继续运行。
3. 写入升级记录 `~/.local/lib/agent-mesh/engine-upgrade.json`，停止 relay（有多个 Bot 时停止每个已安装 Bot 的 relay）。
4. 把二进制和 `~/.reason/network` 复制到 `~/.local/lib/agent-mesh/engine-backup-<UTC 时间>/`（权限 700），替换二进制，重启守护进程，并核对正在运行的守护进程是在替换之后启动的、报告 0.12.17、团队里的机器还在、`who` 能正常返回。
5. 安装当前仓库的 relay 并启动（每个 Bot 的 relay），确认它已在新的守护进程上以自己的名字注册。原来的 relay 保存在 `~/.local/lib/agent-mesh/rollback/`，直到第 6 步。
6. 把这次升级标记为完成，删除升级记录和原来的 relay。

重启仍在运行已被替换二进制的守护进程时，步骤相同，只是第 4 步换成重启守护进程。从 v0.9.0 起，新 relay 在引擎步骤之后才安装；更早的版本先在旧引擎上装好新 relay。

第 4、5 步失败或你按了 Ctrl-C 时，换回旧二进制并在它上面重启守护进程，原来的 relay 程序和登录服务文件也换回来，每个 relay 回到之前的状态（之前停着的仍然停着）。状态目录不恢复：新引擎只加表加列，0.10.7 能打开升级后的库，切换之后收到的信都保留。守护进程在 `reason-network stop` 之后不退出时，先发 SIGTERM，再发 SIGKILL 结束它。换回 0.10.7 不会找回 0.12 已经丢掉的、或 0.10.7 不认识的内容：0.12 运行期间被遗忘的普通会话及其未读信（relay 和云端的信箱不会被遗忘）；以及较新的团队签名证明和 `[folders]` 设置，0.10.7 下次保存团队状态时会丢弃它们。回滚后在 0.10.7 下新建的 CLI 会话不算信箱，下次升级后由 0.12 的 `listen` 重新标记。如果确认不了旧引擎已经在运行，脚本会打印手动处理步骤，并保留记录 `~/.local/lib/agent-mesh/engine-upgrade.json`；这时 `./check.sh` 报 FAIL，下次运行 `./update.sh` 会先回滚这次切换。用备份恢复状态目录只是最后一步手动操作，会丢掉切换之后收到的信。

备份里有这台电脑的引擎身份和收到的消息。`./check.sh` 全部通过后删掉它；`./uninstall.sh --level identity` 也会连同部署记录一起删除它。

`./update.sh` 从不降级：引擎比固定版本新或认不出版本时，它停止运行；`./install.sh` 遇到旧引擎会停止，并提示运行 `./update.sh`。二进制已经是 0.12.17、但正在运行的守护进程早于二进制替换启动时，`./update.sh` 会询问是否重启守护进程。

`[BOT-CLOUD]` 上，每次云端更新都从新 checkout 运行 `bash /workspace/agent-mesh/box/rn-cloud-setup.sh --upgrade-engine`，它检查云端引擎并按同样的方式升级：按固定校验值校验、备份到 `box/engine-backup-<UTC 时间>/`、切换、核对正在运行的守护进程，失败时，旧二进制属于固定构建才换回。云端引擎已经是 0.12.17 时只做检查，需要时启动或重启守护进程。

云端引擎放在 `box/bin/engine/reason-network`；`box/bin/reason-network` 是手动运行命令用的启动器，没有 `REASON_NETWORK_HOME` 时拒绝运行，并始终关闭自动更新。这一步把仍在 `box/bin/reason-network` 的引擎（v0.8.0 及更早）移到 `box/bin/engine`，并写入启动器。现有引擎是否可信只看字节、不运行它：不属于 `box/rn-engine.py` 固定构建的二进制（例如被自动更新替换过的，或早先固定的 0.10.7，它没有固定构建值）不运行就直接换成固定版本，原二进制留在备份里，永不放回。固定版本已经放好之后才失败时，打印只使用新二进制的手动步骤；固定版本没能放好时，启动引擎的文件都不改动，这一步提示排除原因后重新运行。Grok Bot 的回复必须包含 `engine: reason-network 0.12.17 running (daemon pid …)`、属于固定构建的 `engine: pinned build <sha256> at box/bin/engine/reason-network` 和 `engine: launcher ok` 三行；`./update.sh` 自动比对，一致才记录云端引擎已确认。`engine: note: other engine daemons run from box/bin` 一行列出此前从旧路径启动的守护进程；它们已无法替换引擎，这一步也不会停止它们（[故障排除](README.zh-CN.md#常见问题)）。云端引擎是比 0.12.17 新的固定构建时保持不动，这一步失败。

一边是 0.10.7、另一边是 0.12.17 时（哪边先升级都一样），收发、回执和补寄都正常：agent-mesh 0.6.0 起能读懂两个引擎的输出。能在同一次运行里升级 Mac 和云端最好。升级之后适用 Reason Network 0.12 的规则：默认中继在 `compute.reasonmachines.com` 下（[网络](README.zh-CN.md#网络)），离线超过一天的普通会话会连同未读信一起被遗忘（[网络与用量](README.zh-CN.md#网络与用量)）。

**引擎设置。**agent-mesh 在 `~/.reason/network/config.toml` 里关闭引擎的 `auto_update` 和 `lan`。引擎只读取这个文件第一个 `[table]` 行之前的这两个键。第一次安装在 v0.8.0 及更早的 Mac 上，这两行可能落在某个表里，起不到关闭的作用，所以自动更新和局域网发现一直开着，而 `./check.sh` 报告已关闭。从 v0.9.1 起，`./check.sh` 和 `./update.sh` 检查整个文件：这两个键要在第一个表之前关闭，任何表里都不能再出现它们。不符合时，计划里显示 `安全加固：修正引擎设置并重启引擎`，并问那唯一的一次（`--yes` 代为回答）。这一步和重启守护进程一样进行：写入升级记录，删掉早期版本追加在表里的 `auto_update = false` 和 `lan = false` 两行，把这两行放到文件开头，重启守护进程，重新启动每个 relay 并核对它们的注册。本次运行同时要升级引擎时，设置在切换之前修正。

文件改对了，旧的守护进程用的仍是它启动时加载的内容。所以 `./update.sh` 在写文件之前先在 `~/.local/lib/agent-mesh/deployed.json` 里记下这次修正（`engine_config`），只有正在运行的守护进程是在文件写完之后启动的，或者没有守护进程在运行，才去掉这条记录。要从表里删行时，先把原文件保留在旁边，名为 `config.toml.agent-mesh.bak`，和这条记录一起删除。运行在写文件之后、重启之前中断时，记录还在：`./check.sh` 报 FAIL，下一次 `./update.sh` 显示同一行计划并重启守护进程。这一步回滚时不撤销已修正的文件。重启 relay 会让云端尚未确认的消息再响一次门铃，所以 relay 还有这样的消息时，计划里会写明数量，这一步先等它们交付完（最多 15 分钟，超时就停下、不重启；请稍后再次运行 `./update.sh`）。检查只对引擎自己写出的格式负责。第一个表之前有带引号的键或多行字符串，或者文件的其他位置以别的形式出现 `auto_update` 或 `lan`（true/false 的其他写法、不同的空格或引号，既不是 true/false 也不是带引号字符串的值，带点的键，或者同名的表）时不做猜测：`./update.sh` 在改动任何内容之前停下，`./check.sh` 报 FAIL，`./check.sh` 指向 `./update.sh`，由它说明要手工改什么。`./update.sh` 同时记下这次修正，所以你改完之后的下一次运行会重启守护进程，让它加载改好的文件。成员或文件夹的名字正好是 `auto_update` 或 `lan` 时（表里形如 `lan = "hold"` 的行，值是带引号的字符串），这一行保持原样，也不会让更新停下。

用 `./check.sh` 检查结果。它报告引擎版本是否与固定版本一致、正在运行的守护进程是否运行已安装的二进制、有没有未完成的引擎升级、relay 是否已在正在运行的守护进程上注册，以及本机上次确认的云端引擎版本。守护进程或 relay 重启后最多 11 分钟内，relay 正在重新注册，注册这一项报 SKIP。

已知限制：

- 守护进程的启动时间和二进制的变更时间按整秒比较，所以旧二进制恰好在替换的同一秒启动的守护进程会被当作新的。Linux 上，仍在运行已被替换的二进制的守护进程，其程序会显示为已删除，因此也能识别；macOS 没有这项检查。
- relay 在 `listen` 返回后才记录注册。守护进程恰好在这之间重启时，`./check.sh` 可能报告过时的注册，几秒后的下一次 `listen` 会纠正。
- relay 首次注册前的启动工作（补收错过的信、补发、webhook）可能耗尽 45 秒的注册等待，这次重启就按失败处理并回滚。用 `AGENT_MESH_REGISTER_WAIT=<秒数>` 放宽等待。
- 回滚成功但 relay 没能重新启动时，升级记录已经删除，下次运行 `./update.sh` 不会重试；按提示运行 `./install.sh --restart-relay`。
- `./check.sh` 不比对云端脚本的校验值，只报告本机上次确认的云端引擎版本。`./update.sh` 引导云端更新时会比对校验值，`[BOT-CLOUD]` 上的每条 `rn-cloud.sh` 命令都会核对引擎字节。

## 手动升级

relay、云端脚本和 routine 提示词都有变化时，按下列顺序一起更新，保证部分组件仍是旧版本时不会有门铃发出。有多个 Bot 时，第 1、2、4 步对每个 Bot 都做一遍（添加的 Bot 的服务是 `com.local.agent-relay.<name>`，每个 Bot 有自己的 routine 提示词）；第 5 步的 `./install.sh --restart-relay` 会重启每个 Bot 的 relay：

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
