# 更新日志

[English](CHANGELOG.md) | **简体中文**

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

## v0.8.2 — 2026-11-13

更新只需一条命令。`./update.sh` 先列出要更新的内容，只问一次，然后更新 Mac 一侧，并请 Grok Bot 自己更新云端：不用暂停 routine，也不用复制命令块或回复。终端提示跟随所选语言。在终端运行 `./update.sh` 更新；本版 routine 提示词有变化，`update.sh` 提示时把新提示词粘贴进 Grok 一次。

### 新增

- `box/rn-cloud-update.sh <tag> <commit> <request>`：写好的云端更新命令。它核对云端仓库没有本地改动、不会退回旧版本，取回发布 tag，核对 tag 指向发布提交后切换过去，再运行新 checkout 里的同名脚本，由它执行云端引擎步骤并输出 `agent-mesh-result` 行。切换之前发现问题时输出 `STOP: <原因>`，不做改动；引擎步骤失败时保留已核对并切换好的新版本。它不会自行修复任何问题。
- routine 提示词在"生产变更先问用户"之外增加一个例外：主人（团队名与 `box/person` 相同）发来的单行 `agent-mesh update <tag> <commit> <request>` 消息，直接运行 `box/rn-cloud-update.sh`，不再询问，并回复完整输出。其他类似更新的消息不运行。
- `install.sh`、`update.sh`、`check.sh`、`uninstall.sh`、dsh 脚本和辅助脚本的终端提示依次按 `--lang en|zh-CN`、安装时记录的语言、`AGENT_MESH_LANG`、英文选择语言。`check.sh` 新增 `--lang`。供程序读取的输出（状态词、`PASS`/`FAIL`/`WARN`/`RESULT`、`agent-mesh-result`、`STOP:`、云端脚本输出、日志）保持英文。文本放在 `scripts/messages/<lang>/*.tsv`。

### 变更

- `./update.sh` 列出计划后只问一次"继续？[Y/n]"（`--yes` 跳过）。不再询问云端是否已为本版本配置好、是否执行云端引擎步骤：云端更新总会执行引擎步骤，以后的云端迁移放进 `rn-cloud-setup.sh --upgrade-engine` 或 `rn-cloud-update.sh`。本机引擎步骤也不再单独询问。
- 云端需要更新时，`./update.sh` 向 Grok Bot 的地址发送 `agent-mesh update <tag> <commit> <request>`，最多等待 15 分钟（`AGENT_MESH_REPLY_WAIT`），等带同一请求号的回信。只有完整且与发布一致的回信才会被记录；`STOP` 回信会显示出来，不做记录。超时未收到回信时重跑 `./update.sh`，它会先检查已保存请求的回信是否已到。
- 从没有新脚本的版本第一次更新时，发送的是一个命令块：核对并切换到发布版本，再运行新脚本。`./update.sh --manual` 保留 v0.8.1 的引导流程，提示同样按所选语言输出。
- 发布核对另外比对 `box/rn-cloud-update.sh` 与已发布版本；只改动这个文件也会触发云端更新。
- `rn-cloud-setup.sh` 下载引擎时限制连接 20 秒、总时长 3600 秒。

## v0.8.1 — 2026-11-13

云端安全修复。云端只安装和更新经过核对的已发布版本，云端引擎也不会再被另一个自我更新的引擎守护进程替换。在终端运行 `./update.sh` 更新：云端脚本和云端引擎步骤在同一个引导式云端步骤里完成；routine 提示词不变。

### 安全

- 云端引擎移到 `box/bin/engine/reason-network`。`box/bin/reason-network` 改为手动运行命令用的小启动器：没有 `REASON_NETWORK_HOME`（或 `TINCAN_HOME`）时拒绝运行，拒绝 `upgrade`、`setup` 以及不带 `--no-setup` 的 `join`/`init`，并始终设置 `REASON_NETWORK_NO_UPDATE=1`。此前，在云端手动运行引擎命令，可能导致固定的引擎被换成未经核对的版本。
- `rn-cloud.sh` 的每条命令、`rn-fetch.py` 和云端卸载脚本在运行引擎前，都先核对引擎字节是否属于 `box/rn-engine.py` 固定的构建，不符就停止。`rn-cloud.sh`、`rn-cloud-setup.sh` 和 `rn-engine.py` 给每条引擎命令设置 `REASON_NETWORK_NO_UPDATE=1`，`config.toml` 损坏时也不会重新打开自动更新。
- 云端接入提示词和更新消息把云端仓库固定到发布 tag `v<VERSION>`：Grok Bot 运行一个命令块，取回该 tag，核对它指向发布提交、四个云端脚本（`box/rn-cloud.sh`、`box/rn-fetch.py`、`box/rn-cloud-setup.sh`、`box/rn-engine.py`）的校验值符合预期，之后才切换到该 tag 并再次核对文件。任何一项不符都会停止且不改动仓库，或者恢复到原来的提交。此前，云端先克隆或拉取默认分支并运行其中的脚本，Mac 之后才比对校验值。
- 云端脚本以隔离模式运行 Python（`python3 -I`），放在脚本旁边的文件无法冒充标准库模块被导入。

### 变更

- `install.sh --cloud-prompt` 和 `./update.sh` 先在线核对：云端安装来源仓库里存在该发布 tag，且本地仓库中的四个云端脚本与它一致。核对失败时，它们在给 Grok Bot 打印任何内容、改动部署之前就停止（`./update.sh` 可能已经拉取了本地仓库）。`./update.sh` 不再要求本地仓库已提交或等于上游；新增 `--repo-url` 选项（默认是公开仓库），`--plan` 会显示核对结果。
- `rn-cloud-setup.sh --upgrade-engine` 把仍在 `box/bin/reason-network` 的引擎移到 `box/bin/engine`；引擎字节不属于固定构建时，不运行它就直接替换（副本留在备份里，永不放回）。它会打印 `engine: pinned build <sha256>` 和 `engine: launcher ok`，并列出从 `box/bin` 运行的其他引擎守护进程，但不停止它们。云端引擎步骤中，Grok Bot 的回复必须包含这些行。
- `rn-cloud-setup.sh` 和 `install.sh` 把 `auto_update`、`lan`（云端另有 `tray`）写成引擎 `config.toml` 的顶层键：已有的键原地替换，缺失的键加在文件开头。此前缺失的键追加在文件末尾，会落进最后一个表里而不生效。
- 更新消息的命令块以 `agent-mesh-result` 开头的几行结尾：提交号、四个校验值和 `DONE <tag>`。`./update.sh` 只读这几行，只是复述消息或含 `STOP` 行的回复不会被记录。云端步骤没有记录之前，routine 保持暂停。
- 不带命令运行 `rn-cloud.sh` 时，先打印用法，再核对引擎。

## v0.8.0 — 2026-11-12

Bot 地址就是 Bot 自己的名字：全新安装会问这个名字，不再默认 `bot`；地址旁显示 Bot 在 Grok 里的名字。已有安装保留原来的地址（#31）。

### 新增

- `install.sh --grok-name <名字>`：Bot 在 Grok 里显示的名字，任何语言都可以（最多 64 个字符，首尾不能有空格，不能有换行或其他控制字符）。它原样保存在 `~/.local/lib/agent-mesh/deployed.json`，不写入 relay 的 env，也不参与路由。单独使用时，只记录或修改已有安装的 Grok 名字，不动地址、relay 和服务（#31）。
- 安装器的 `Bot address:` 行和 `./check.sh` 显示 `<team-name>/<地址> → Grok: <名字>`（#31）。
- 回复约定模板新增一个生成块，位于 `<!-- agent-mesh:bot-address lang=… -->` 和 `<!-- /agent-mesh:bot-address -->` 之间。安装器和 `./update.sh` 把填好本团队 Bot 地址和 Grok 名字的副本写到 `~/.local/lib/agent-mesh/templates/`；`python3 scripts/reply_convention.py refresh <仓库>/AGENTS.md` 更新仓库里的副本，只替换标记之间的行（#31）。

### 变更

- 全新安装（没有 relay env、没有记录的地址、本机没有团队）必须给 `--relay-name`。不给时，`install.sh` 在改动任何东西之前停下，询问 Grok Bot 叫什么，并根据 `--grok-name` 给出建议地址（转小写、去掉重音，其他字符换成单个 `-`）。没有拉丁字母和数字的名字（例如中文名）不给建议，由安装 agent 提出，例如用拼音，再请用户确认。已有安装（包括叫 `bot` 的）不带 `--relay-name` 时保留原地址（#31）。
- AGENT-INSTALL 第 0 阶段改为询问 Bot 在 Grok 里的名字，并由它推导地址，不再提供默认的 `bot`（#31）。
- relay env 缺失时，`./check.sh` 不再假定地址是 `bot`：它读取 `deployed.json` 记录的地址；不知道地址时跳过注册检查。
- relay env 缺失时，`install.sh` 也从 `deployed.json` 读取地址，不再先假定 `bot`、再以“改名”为由拒绝（#31）。

### 修复

- `./update.sh --help` 只打印了部分说明。

### 升级说明

运行 `./update.sh`。relay、云端脚本、云端引擎和 routine 提示词都没有改动，所以它只写入填好的回复约定副本。想在地址旁显示 Bot 在 Grok 里的名字，运行一次 `./install.sh --grok-name '<它在 Grok 里的名字>'`。v0.8.0 以前追加到各仓库的回复约定没有生成块：用 `~/.local/lib/agent-mesh/templates/` 里填好的副本替换那一节。地址本身仍然不能改；换名字仍然要按 `identity` 级别卸载后重新安装。

## v0.7.0 — 2026-11-12

固定 Reason Network 0.12.17，并升级 Mac 和云端上已安装的引擎：先备份，失败时回滚。0.10.7 在上游安全公告 GHSA-84r9-56w3-xqp9 的影响范围内（0.12.12 修复）。

### 新增

- `./update.sh` 升级 Mac 上较旧的引擎：先在现有引擎上切换 relay；列出其他正在使用引擎的进程（不阻止升级）；下载 0.12.17 并按固定的 SHA-256 校验；把二进制和 `~/.reason/network` 备份到 `~/.local/lib/agent-mesh/engine-backup-<时间>/`；替换二进制，重启守护进程，核对正在运行的是新守护进程；然后启动 relay，确认它已在新守护进程上注册。失败或按 Ctrl-C 时只换回旧二进制并重新启动 relay，切换之后收到的信都保留。撤销不了的切换记在 `~/.local/lib/agent-mesh/engine-upgrade.json`，下次运行时先回滚。它一定在终端询问，加 `--yes` 也会问；从不降级；守护进程还在跑被替换前的二进制时，询问后重启（[UPGRADE.zh-CN.md](UPGRADE.zh-CN.md#reason-network-引擎)）（#20）。
- `box/rn-cloud-setup.sh --upgrade-engine` 按同样的方式检查或升级云端引擎（备份在 `box/engine-backup-<时间>/`）。`./update.sh` 把它加进引导式云端步骤，并比对 Grok Bot 的回复：要包含 `box/rn-cloud-setup.sh`、`box/rn-engine.py` 的校验值和 `engine: reason-network 0.12.17 running (daemon pid …)` 这一行。`deployed.json` 记录云端引擎版本（#20）。
- `box/rn-engine.py`：`update.sh`、`check.sh` 和云端初始化共用的引擎检查与切换。只有持有引擎的锁、并且在二进制替换之后启动的守护进程，才算新守护进程。守护进程在 `reason-network stop` 之后不退出时，先发 SIGTERM，再发 SIGKILL 结束它；确认不了回滚结果时，打印的手动步骤里每个路径都加了引号。
- `check.sh` 报告：引擎版本是否与固定版本一致；正在运行的守护进程是否运行已安装的二进制；有没有未完成的引擎升级及其备份位置；relay 是否已在正在运行的守护进程上注册（重启后最多 11 分钟内报 SKIP）；已安装的 relay 能否识别 0.12 的终端地址；本机上次确认的云端引擎版本（#20、#30）。
- `./install.sh --fetch-engine <空目录>` 下载并校验固定版本的引擎，但不安装；`./update.sh` 用它下载。

### 变更

- 固定的 Reason Network 从 0.10.7 换成 0.12.17；新安装在两台电脑上都装 0.12.17（#20）。
- 引擎下载按写在 `install.sh` 和 `box/rn-cloud-setup.sh` 里的 SHA-256 校验，覆盖全部四个平台，不再用发布包旁边的 `.sha256` 文件。
- `install.sh` 只在需要时检查引擎版本：完整安装遇到较旧的引擎会停止并提示 `./update.sh`，遇到更新或认不出的版本也会停止；`--restart-relay`、`--webhook`、`--cloud-prompt` 和 `--preflight` 在较旧的引擎上也能运行，所以 `./update.sh` 可以先换 relay 再换引擎。以前版本不一致时每种模式都会停止（#20）。
- 完整安装在打印云端初始化步骤时，把云端引擎一并记为待确认；`./update.sh` 会一起确认。
- `./uninstall.sh --level identity` 也会删除引擎备份：本机的随部署记录删除，`[BOT-CLOUD]` 上删除 `box/engine-backup-*`。

### 修复

- `box/rn-cloud-setup.sh` 不再把固定版本解压覆盖到已有的引擎上。重跑时，固定版本的引擎保持不动；较旧的引擎会让它停止并提示 `--upgrade-engine`；更新或认不出的版本也会让它停止。以前每次运行都用固定版本的包替换二进制，连更新的引擎也会被覆盖。
- `./install.sh --restart-relay`（因而 `./update.sh`）可能接受一个从未 listen 的 relay：旧 relay 停止后引擎还留着会话行，这一行就满足了注册检查。现在 relay 每次 `listen` 报出自己的名字时写一个标记；安装器在新 relay 启动前删除它，要等标记重新出现、名字和进程都对应新 relay，才核对会话行。`listen` 失败时（例如守护进程重启），relay 立即重新注册，不再等下一次长轮询（#30）。

### 升级说明

在终端运行 `./update.sh`。它按这个顺序进行：在现有引擎上装新 relay，升级 Mac 引擎，然后是引导式云端步骤：你暂停 routine，Grok Bot 拉取这个提交并运行 `box/rn-cloud-setup.sh --upgrade-engine`，你粘贴它的回复，再恢复 routine。云端脚本 `box/rn-cloud.sh`、`box/rn-fetch.py` 和 routine 提示词都没有变化。升级前先在 `*.iroh.link` 之外放行 `*.compute.reasonmachines.com`（README：网络）。升级后重开正在运行的 Claude Code 和 Codex 会话，用 `./check.sh` 检查，全部通过后删掉引擎备份。升级之后，Reason Network 0.12 会遗忘离线超过一天的普通会话，连同未读信一起删除；relay 和云端的信箱不受影响。

## v0.6.0 — 2026-11-11

为 Reason Network 0.12 做准备：relay、自检和云端脚本能读懂新旧两个引擎版本的输出。固定的引擎版本仍是 0.10.7，`install.sh` 和 `update.sh` 仍要求这个版本；换引擎留到之后的版本。

### 新增

- `scripts/relay_targets.py`：告诉 `check.sh` 和 `install.sh` 已装引擎实际使用哪台中继。团队用 `reason-network relay <URL>` 指定了中继时以它为准；否则按引擎版本判断（0.10.x 用 n0 的中继；0.12.x 用守护进程日志里最后记下的中继，没有时读 `relays.json` 里的列表）；引擎还没装时用固定版本的默认中继。`reason-network team` 第一行的默认中继文字由客户端二进制生成，不拿来判断。判断不出来时，`check.sh` 报 SKIP 并说明要放行什么，不把备用地址可达当作通过（#20）。
- README：Reason Network 0.12 的默认中继（`*.compute.reasonmachines.com`）；团队怎样指定或换回中继；消息端到端加密，中继只转发；0.12 会遗忘离线超过一天的普通会话及其未读信（relay 和云端的信箱不受影响，0.10.7 从不遗忘）。常见问题覆盖两个中继域名（#20）。

### 变更

- relay：云端回执（`RN-GOT v1`）也接受来自 `<team-name>/terminal@<CLOUD_MACHINE>` 的，这是新版引擎给终端发件人的写法。只接受配置的云端机器名，`terminal@<其他机器>` 仍然拒绝；`terminal` 和 `terminal-<n>` 照旧接受（[PROTOCOL.zh-CN.md](PROTOCOL.zh-CN.md#补寄与回执)）（#20）。
- `check.sh` 和 `install.sh`：网络检查探测按上面方法找到的中继，并在结果里写出是哪一台，不再总是探测 n0 的 `aps1-1`（#20）。
- `./install.sh --restart-relay`（`update.sh` 调用它）：新 relay 还要以自己的名字注册成功，切换才算完成。注册成别的名字时，恢复原来的 relay 和它的 launchd 状态，命令返回失败；以前切换照样保留，要等之后的检查才发现（#20）。
- DeepSeek Harness 插件：来自 `terminal`、`terminal-<n>` 或 `terminal@<机器>` 的消息都显示为在终端里输入（#20）。

### 修复

- `check.sh`、`box/rn-cloud-setup.sh`、`box/rn-cloud-uninstall.sh` 和 `./uninstall.sh` 能读懂人名后带 `(verified)` 的 `reason-network team` 列表，Reason Network 0.12 会这样标出用 Reason 登录过的人。以前遇到这种列表，`check.sh` 找不到云端机器，还把它报成多余的成员；云端安装和卸载步骤会认错自己所在的机器（#20）。

### 升级说明

改动的有 relay、`check.sh`、`install.sh`、`scripts/uninstall.py`、新增的 `scripts/relay_targets.py`、`box/rn-cloud-setup.sh`、`box/rn-cloud-uninstall.sh` 和 DeepSeek Harness 插件。云端脚本 `box/rn-cloud.sh`、`box/rn-fetch.py` 和 routine 提示词没有变，云端不用更新。`./update.sh` 会重启 relay；因为 `box/rn-cloud-setup.sh` 变了，它会问云端是否已按这个版本设置好：这一版没有云端设置迁移，回答 yes 即可。云端下次运行安装步骤时会用到新的安装脚本。

## v0.5.2 — 2026-11-11

让引导式云端更新和自检如实反映实际情况。

### 变更

- 文档：README 变短了。卸载步骤、升级步骤和消息格式原样移到 [UNINSTALL.zh-CN.md](UNINSTALL.zh-CN.md)、[UPGRADE.zh-CN.md](UPGRADE.zh-CN.md) 和 [PROTOCOL.zh-CN.md](PROTOCOL.zh-CN.md)，各有英文版；README 在原标题下保留摘要和链接。「核心亮点」并入「解决什么问题」。行为没有变化。

### 修复

- `update.sh`：云端步骤不再凭一个"是"就记录云端脚本已更新。改为请你粘贴 Grok Bot 的回复，回复中的提交号和 `box/rn-cloud.sh`、`box/rn-fetch.py` 的校验值与当前仓库完全一致，才记录云端已更新。只回答"是"、什么都不贴、提交号不同或校验值不同，都不会记录，routine 保持暂停，下次运行 `./update.sh` 会再问。此前，即使那段话根本没发给 Grok Bot，回答"是"也会把云端记为最新，而云端仍在运行旧脚本。发给 Grok Bot 的话也覆盖了云端仓库处于分离 HEAD 的情况：先切换到当前仓库跟踪的分支，再执行 `git pull --ff-only`，遇到问题就停下，不做任何重置（#28）。
- `check.sh`：webhook 一项改为看 relay 日志里最近一次 webhook POST。失败之后重试已送达（`code=200`）的，判为通过并注明已恢复，不再让你为正常工作的 webhook 去运行 `./install.sh --webhook`。最近一次 POST 失败（包括 3xx 应答）时仍判为失败（#26）。

### 升级说明

只改了 `update.sh`、`check.sh` 和 `scripts/deploy_state.py`；relay、云端脚本、云端初始化脚本和 routine 提示词都没有变化，所以从已确认的 v0.5.1 升级、且没有未完成的更新时，云端不需要做任何事。云端从未确认过、上次更新中途停下或跨版本升级时，`./update.sh` 照常走相应的步骤，云端那一步改为粘贴回复。

## v0.5.1 — 2026-11-11

修复 v0.5.0 新增的注册检查：它使云端在 fetch 之后无法回信。

### 修复

- 云端脚本与安装器：同一个引擎持续运行期间，名称第二次注册时，注册检查不再失败。引擎每次运行只在名称首次注册时记录时间，v0.5.0 新增的检查因此拒绝了之后的每一次注册：云端 `rn-cloud.sh send` 在 `fetch` 之后失败，已安装的机器上 `./install.sh --webhook` 失败。现在只要引擎为该名称保存的会话恰好就是这个名称，检查即通过；带编号的名称或没有会话仍会被拒绝（#27）。

### 升级说明

只有云端脚本和安装器有改动，relay、routine 提示词和云端安装脚本不变。v0.5.0 的云端在 `fetch` 之后无法发出回信，请尽快更新：运行 `git pull`，然后在终端运行 `./update.sh --lang <en|zh-CN>`，按引导让 Grok Bot 拉取新提交。

## v0.5.0 — 2026-11-09

relay 与云端脚本的若干修复、可自选的 Bot 地址名，以及分级卸载器。

### 修复

- relay：webhook POST 不再跟随 HTTP 重定向。收到 3xx 按 POST 失败处理，和其他失败一样重试；日志会提示改用最终地址。`WEBHOOK_URL` 必须填最终地址：relay 日志出现 `webhook POST failed: 30x` 时，从 routine 重新复制 webhook URL，再运行 `./install.sh --webhook`。
- relay：RN-OUT 收件地址改为白名单。`to` 必须是 `<person>/<session>[@<machine>]`，每一段由小写字母、数字和短横线组成且首字符是字母或数字；非空的 `reply_to` 必须是普通消息 id。其他值在发送前被拒并发出提醒；该 nonce 不会被占用，改正后的信封可以正常发送。
- relay：拒发或失败过的 nonce 再试、结果变为未知时，撤回先前的拒发或失败提醒，结果未知的提醒注明取代了先前的报告，Bot 不会再对同一条消息同时看到"可以重发"和"不要重发"（#15）。
- relay：RN-OUT 回复已经结束某条消息的待办后，`done` 回执也会撤掉该消息仍在排队的门铃，不再多唤醒一次 Bot（#17）。
- 云端脚本：只按引擎的状态词判断发送结果。收件名或团队名里含 `held`、`error` 等词（例如团队名 `sheldon`）时，已排队的回执、重投请求和 `rn-cloud.sh send` 不再被误判为失败。回执和重投请求中，`rn send` 退出码为 0 但没有 `→` 行时改判为失败，与 relay 原有要求一致（#18）。
- relay：`reason-network listen` 改写已签名 RN-OUT 信封的显示形式时（会改写形似其消息标签的文本，如 `<reason-message …>`），relay 不再以签名错误拒收。显示的信封无法解析或验签时，relay 改为校验引擎为该消息 id 保存的原文：只读，只接受云端会话发给本 relay、未被扣留且恰好一行的消息。时效、nonce、频率和地址检查照旧，被篡改的消息仍会被拒绝（#11）。
- 安装器：另外两处在 `set -o pipefail` 下可能提前结束的管道（查询团队名，以及 `--rotate-key` 时检查签名密钥）不再误报失败，与 v0.4.1 对云端连接检查的修复相同（#24）。
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
