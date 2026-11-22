# agent-mesh：给安装 agent 的操作手册

[English](AGENT-INSTALL.md) | **简体中文**

你是在用户 Mac 上运行的 coding agent（如 Claude Code、Codex）。用户希望把本地会话和云端 Grok Bot 接入同一个 Reason Network 私有团队，实现双向叫醒。按本手册执行；原理和参考见[中文指南](README.zh-CN.md)或[英文指南](README.md)。

使用用户的语言交流，简短汇报。从公开发行版根目录运行命令。按用户偏好选择 `--lang en` 或 `--lang zh-CN`，每次调用安装器都传入；下例使用中文。语言参数控制生成的提示词，终端诊断仍为英文。

## 基本规则

- `[LOCAL]` = 用户本地电脑（Mac）；`[BOT-CLOUD]` = Grok Bot 的云电脑（机器名 `cloud`）；`[GROK-UI]` = 用户打开的 Grok Bot 界面。标识不是命令的一部分。
- 你是 `[LOCAL]` 的安装 agent。本手册中未另行标注的命令都在 `[LOCAL]` 执行；云端命令由 Grok Bot 在 `[BOT-CLOUD]` 执行。两端各有仓库和状态目录，不能互相代用。
- 步骤同时区分执行位置和负责人。用户步骤绝不自己代做，agent 步骤绝不让用户去做。`[GROK-UI]` 的操作由用户完成。
- 不读取、打印或粘贴 `~/.config/agent-relay/env`、`~/.config/agent-relay-bots/<name>/env`、`~/.config/agent-relay/outbound.key` 或 Webhook key。可以检查文件是否存在。
- `install.sh` 在非交互终端里会在需要用户操作的位置停下并打印操作说明，把说明原样交给用户。
- 每个阶段要求时运行 `./check.sh`，原样展示结果。`FAIL` 行给出修复办法，照做，不要即兴发挥。
- 不安装额外软件（缺命令行开发者工具时，由用户在第 0 阶段自己装），除第 0 阶段的 PATH 外不改 shell 配置。其他 agent 的配置由 `reason-network setup` 在用户确认计划后修改。
- `install.sh` 只支持 macOS。Linux 上改按 [Linux 手工安装（实验性）](#linux-手工安装实验性) 操作，代替第 0–3 阶段，并且要用户先同意它的限制；其他系统一律停止。

## 第 0 阶段：准备（[LOCAL] 安装 agent）

1. 先按指南「准备」检查 `[LOCAL]` 和 `[BOT-CLOUD]` 的依赖。`[LOCAL]` 的 `uname -s` 应输出 `Darwin`，CPU 为 arm64 或 x86_64；当前用户应已登录桌面，不用 sudo。已有 agent 也须先安装、登录并能独立工作。不要假定任何开发工具已经装好，按下面的顺序检查：
   - 运行 `xcode-select -p`。输出路径说明已装 Apple 命令行开发者工具（或 Xcode）。失败时，`/usr/bin/git` 和 `/usr/bin/python3` 只是占位程序：`command -v` 照样找得到，但一运行就会弹出 macOS 安装窗口并报错退出。新 Mac 就是这种状态。
   - `xcode-select -p` 失败，且 `command -v git` 或 `command -v python3` 输出 `/usr/bin` 下的路径时，需要装命令行开发者工具。运行 `xcode-select --install`，并告诉用户：「macOS 会弹窗询问是否安装命令行开发者工具。点「安装」，同意许可协议，等下载装完，可能要一阵子。装好后告诉我。」没有弹窗时，请用户在「终端」里运行 `xcode-select --install`。弹窗说无法安装该软件时，请用户登录 Apple ID，从 https://developer.apple.com/download/all/ 下载与自己 macOS 版本对应的 Command Line Tools for Xcode 并安装。用户说装好后，再运行一次 `xcode-select -p`，不把“已发起安装”当作已就绪。不需要 Homebrew。
   - 运行 `git --version` 和 `python3 -c 'import sys, ssl, sqlite3; print(sys.version)'`，要求 Git 可用、Python 3.9+。较旧的命令行开发者工具自带的 Python 可能低于 3.9；这时告诉用户，由用户自己装 Python 3.9+（比如用 python.org 的安装包或 Homebrew）并放进 PATH。不要自己装 Python 或 Homebrew。
   - `curl`、`tar`、`shasum`、`openssl`、`launchctl`、`plutil` 是 macOS 自带的，用 `command -v` 检查。缺任何一个都报告并停下。
2. 运行 `curl -sS -o /dev/null -m 10 -w '%{http_code}\n' https://aps1-1.relay.n0.iroh.link/`，应输出 `200`。失败时先让用户关掉 VPN/代理重试；若恢复，给 `iroh.link` 及所有子域添加直连规则（TUN、虚拟网卡、增强或全局模式也需要），并排除 HTTPS 解密/MITM。用 `curl -sv -o /dev/null https://aps1-1.relay.n0.iroh.link/ 2>&1 | grep issuer` 查看，应是公共 CA（目前是 Let's Encrypt）而非代理自己的 CA。引擎可能使用多台中继，规则应覆盖整个域名；`reasonmachines.com` 及其子域也按同样方式放行，因为 Reason Network 0.12 默认使用那里的 Reason 中继。关闭代理后仍失败则排查 DNS、防火墙和网络。返回 200 后再继续。
3. 如果 `$HOME/.local/bin` 不在 PATH，后续 Bash/zsh 命令加上 `export PATH="$HOME/.local/bin:$PATH";` 前缀。持久配置前确认用户实际使用的 shell：zsh 用 `~/.zshrc`，Bash 用实际读取的启动文件，fish 用 `fish_add_path "$HOME/.local/bin"`，不要统一修改 zsh 配置。检查现有 Reason Network 的路径、版本和默认状态目录；不兼容时先报告，不覆盖另一套安装，也不直接卸载。还没有代码时，Git 可用后再克隆，例如 `git clone https://github.com/xiaozhenliu/agent-mesh.git ~/agent-mesh`（目录可以自选），之后的命令都在它的根目录运行。目录已存在时先核对 origin 和本地改动。`./check.sh` 显示这台 Mac 已经运行 agent-mesh、且就是用户说的那个 Bot 时，不要重新安装：按[更新](#更新)处理；要再接一个 Bot 时按第 5 步。
4. 一次向用户确认：使用 Claude Code、Codex、pi、DeepSeek Harness 中的哪些（其他工具不在本手册支持范围）；团队名（同时是 person 名，1–40 个 ASCII 小写字母、数字或 `-`，首尾不能是 `-`、不能连用，不用真名，建好不能直接改）；Grok Bot 在 Grok 里叫什么（任何语言都可以，原样保存；最多 64 个字符，首尾不能有空格，不能有换行或其他控制字符），再由它推导 Bot 地址名（小写字母、数字，用单个 `-` 分隔，最多 34 个字符，不能是 terminal；安装完成后可以用 `agent-mesh rename` 修改）：英文名转小写（`Nova` → `nova`），中文或其他非拉丁字母的名字给出拼音或英文建议（「小助手」→ `xiaozhushou`），把地址给用户看，用户确认或修改后再用；没有默认名，全新安装不带 `--relay-name` 时会停下并给出建议；Grok Bot 能否创建 webhook routine 并使用 CopyToBox（都必须支持）；Mac 是否会保持唤醒（接电防睡眠，合盖会让整套停止）。
5. **在已有安装上再接一个 Grok Bot。** 第 3 步发现这台 Mac 已有 agent-mesh 安装（`./check.sh` 会打印它的 `Bot address:` 行），而用户想再接一个 Grok Bot 时，不要重做第 1、2 阶段，也不要问团队名。只问新 Bot 在 Grok 里叫什么，按第 4 步给出拼音或英文的地址建议，用户确认或修改后再用。然后运行 `./install.sh --add-bot --grok-name '<grok-name>' --relay-name <relay-name> --lang zh-CN`。绝不加 `--yes`：`--add-bot` 不接受它，计划必须由用户在终端里确认。没有终端时，安装器打印计划并以退出码 3 停下，什么都不改；把计划给用户看，请用户在自己的终端里运行同一条命令、回答 `y`，在 `[GROK-UI]` 用它打印的提示词新建由 webhook 触发的 routine，再把 Webhook URL 和 key 贴到那个终端。不需要云端提示词、邀请链接或 CopyToBox：新 Bot 会在共用的云电脑上自己登记，安装器负责确认（见[多个 Grok Bot](README.zh-CN.md#多个-grok-bot)）。安装器提示 `run ./update.sh first` 时，先按[更新](#更新)处理。它报告这个 Bot 保持 pending 时，之后运行 `./install.sh --relay-name <relay-name> --wake-bot --lang zh-CN`，从添加停下的地方继续。最后运行 `./check.sh`，按第 3 阶段测试新地址。

## 第 1 阶段：本地安装（[LOCAL] 安装 agent）

1. 进入公开发行目录，运行 `./install.sh --name <team-name> --relay-name <relay-name> --grok-name '<grok-name>' --lang zh-CN`（Grok 名字要加 shell 引号）。它安装引擎、建团队、打印 `reason-network setup --dry-run` 计划；非交互终端不能确认，所以以退出码 3 停下。
2. 展示计划并请求用户确认。确认后运行 `./install.sh --name <team-name> --relay-name <relay-name> --grok-name '<grok-name>' --yes --lang zh-CN`。它应用接入配置、生成签名密钥、打印 routine 提示词，并在 webhook 步骤以退出码 0 停下。
3. 把分隔线之间的提示词原样交给用户，说明：「在 `[GROK-UI]` 创建由 webhook 触发的 routine，把提示词贴进去。面板会显示 Webhook URL 和 key。请在 `[LOCAL]` 用户本地电脑的终端进入这个仓库目录，运行 `./install.sh --webhook --lang zh-CN`，把两项粘贴进去；出现 `relay up` 后告诉我。」这份 routine 提示词将由 Bot 在 `[BOT-CLOUD]` 执行，不要让本地安装 agent 执行其中的命令。
4. 等用户完成，再运行 `./check.sh`：relay 项应全部 PASS，cloud machine 可以暂时 SKIP。

## 第 2 阶段：接入 Bot 云电脑（跨端交接）

1. **`[LOCAL]` 安装 agent：**运行 `./install.sh --cloud-prompt --lang zh-CN`。它打印邀请（Mac 在线时有效 24 小时）及填好团队名、Mac home 目录和仓库地址的云端提示词。核对仓库地址指向公开发行版，其根目录应有 `box/rn-cloud-setup.sh`；若地址缺失，或该仓库根目录没有这个脚本，先替换再交给用户。若地址是 SSH 且 `[BOT-CLOUD]` 没有 GitHub 凭据，改用同一公开仓库的 HTTPS 地址；不要把用户的 SSH 私钥复制到云端。
2. **`[GROK-UI]` 用户 → `[BOT-CLOUD]` Grok Bot：**把整段输出交给用户，说明：「在 Grok Bot 界面把它作为一条消息发给 Bot。Bot 会在自己的云电脑核对并克隆发布版本、安装引擎、加入团队，并请你批准一次 CopyToBox：来源是 `[LOCAL]` 的 `<mac-home>/.config/agent-relay/outbound.key`，目标是 `[BOT-CLOUD]` 的 `/workspace/agent-mesh/box/outbound.key`。它确认 cloud 已接入后告诉我。」
3. **`[LOCAL]` 安装 agent：**等用户完成，再运行 `./check.sh`：`cloud machine joined` 应为 PASS。这里检查的是 Bot 云电脑已入队，不代表双向消息已经验证成功。

## 第 3 阶段：会话与连通验证

1. **`[LOCAL]` 用户：**关闭并重新打开第 1 阶段之前启动的 Claude Code、Codex 会话。Codex 会询问一次，选 Trust all and continue。
2. **`[LOCAL]` 安装 agent：**运行 `reason-network send <team-name>/<relay-name> "test 1"`。这里的 `<relay-name>` 是 Bot 地址名（安装器把 Bot 地址以 `Bot address:` 打印；v0.8.0 以前的安装通常是 `bot`）。预期 30 秒内本地 `~/Library/Logs/agent-relay.log` 新增 `forwarded` 和 `POST … code=200`。Grok Bot 醒来，在 `[BOT-CLOUD]` 取正文并回复；回复可以进入用户本地会话，也可由 `[LOCAL]` 的 `reason-network listen --as terminal --wait 120` 收取。
3. **`[GROK-UI]` 用户 → `[BOT-CLOUD]` Grok Bot：**请 Bot 在它的云电脑运行 `/workspace/agent-mesh/box/rn-cloud.sh who` 并给具体的 `[LOCAL]` 会话（如 `<team-name>/<folder>-claude`）发一句话。预期本地空闲会话也收到；本地会话回复后 Grok Bot 再次被叫醒。
4. 按实际观察汇报两项结果，未观察到的测试不宣称成功。

## 可选

以下操作都在 `[LOCAL]` 用户本地电脑执行，修改的也是本地 App 或仓库文件。

- dsh 自动叫醒：`dsh/mac-install.sh <team-name>`，然后用户完全退出并重开 App。见[插件指南](dsh/README.zh-CN.md)。
- 回复约定：把填好的副本 `~/.local/lib/agent-mesh/templates/AGENTS.reason-network.zh-CN.md`（或 `AGENTS.reason-network.md`）追加到用户常用仓库的 `AGENTS.md`，副本里写明本团队的 Bot 地址和 Bot 在 Grok 里的名字；Claude Code 还需在该仓库 `CLAUDE.md` 加上 `@AGENTS.md`。没有安装时用[中文模板](templates/AGENTS.reason-network.zh-CN.md)或[英文模板](templates/AGENTS.reason-network.md)，自己填地址。
- Bot 地址名：要修改时运行 `agent-mesh rename <新名字> --lang <en|zh-CN> </dev/null`（有多个 Bot 时加 `--relay-name <当前名字>`）。不在终端里时，它打印计划和加上 `--yes` 的同一条命令，以退出码 3 结束；把计划给用户看，用户同意后再运行那条命令。收回已卸载 Bot 或早先部署留下的名字，或者本机有发往这个名字的未读消息时，需要用户自己的终端：请用户在那里运行这条命令。等 Bot 回信超时时，再运行同一条命令；用户决定停止时运行 `agent-mesh rename --cancel`。之后运行 `./check.sh`，并按下一条刷新回复约定副本。见[给 Bot 改名](README.zh-CN.md#给-bot-改名)。
- Grok 名字：已有安装用 `./install.sh --grok-name '<grok-name>'` 记录或修改，地址不变。之后对每个放了回复约定副本的仓库运行 `python3 scripts/reply_convention.py refresh <仓库>/AGENTS.md`，它只替换 `agent-mesh:bot-address` 标记之间的行。

## 出错时

以下修复命令均在 `[LOCAL]` 执行；邀请重试和 `fetch` 属于 `[BOT-CLOUD]`，由 Grok Bot 执行。

- 云端报 `couldn't reach the machine that made the invite`：第 0 阶段第 2 步又失效了（Mac 的 VPN/代理又在拦截 `*.iroh.link`）。按第 0 阶段第 2 步修好，在 Mac 上运行 `reason-network stop`，请 Grok Bot 在邀请仍有效时用同一链接重试。
- 日志报 `webhook POST failed`：请用户运行 `./install.sh --webhook --lang zh-CN` 重新核对 URL 和 key，不读取 env 文件。
- Mac 已用另一个名字入队：不能直接改名。仅在用户明确决定后，请用户在自己的终端里运行 `./uninstall.sh --level identity`（需要输入确认，并有一步在 Grok Bot 里完成），再从第 1 阶段重来。
- 其他问题，包括本手册没写到的停止或退出码 1：原样展示输出，按[指南](README.zh-CN.md)、[升级](UPGRADE.zh-CN.md)或[卸载](UNINSTALL.zh-CN.md)中对应的章节处理，不要即兴修复。

## 更新

`[LOCAL]`，用于已有安装。

1. 从 v0.8.1 或更早版本来时，先运行 `git pull --ff-only`（原因见[从哪个版本来](UPGRADE.zh-CN.md#从哪个版本来)）。拉取因本地改动被拒绝时，把改动给用户看并停下；绝不 stash、reset 或丢弃这些改动。
2. 运行 `./update.sh --plan --lang <en|zh-CN>`（用户所选语言），把输出给用户看。它列出 checkout 里未提交的改动时，不要自己加 `--no-pull`：那样会装上没人审过的代码。只有用户明确决定后才加。
3. 用户同意计划后，运行 `./update.sh --yes --lang <en|zh-CN> </dev/null`，确保标准输入不是终端；不要给它分配交互终端，也不要替用户回答它的问题。`--yes` 只跳过用户刚才已经给出的确认；需要用户做的事（必须在云端更新之前在 `[GROK-UI]` 粘贴的 routine 提示词、未完成的云端初始化或手动更新）仍会让它以退出码 3 停下，更新不会执行。其他有变化的 routine 提示词这次不换：更新照常完成，汇总里写明提示词这次不换。计划把 routine 提示词标为 `待读回`（`--lang en` 显示 `read-back`）时，不用粘贴：更新会经 relay 请 Bot 确认 routine 桩，并等待回信。计划里某个 Bot 附有 `./install.sh --relay-name <name> --wake-bot` 时，之后运行这条命令并补上所选的 `--lang <en|zh-CN>`，单独报告这个 Bot。
4. 按退出码处理：
   - `0`：完成或已是最新。运行 `./check.sh`。汇总写明 routine 提示词这次不换时，告诉用户 Grok Bot 继续用现有提示词工作，想换时在自己的终端运行 `./update.sh --lang <en|zh-CN>`，它会显示新的提示词。
   - `3`：需要用户的终端；更新没有执行，但 checkout 可能已经前进到最新的发布版。请用户在自己的终端运行 `./update.sh --lang <en|zh-CN>`；routine 桩有变化时它显示要粘贴的桩（有多个 Bot 时逐个显示），之后请每个 Bot 确认。
   - `1`：没有完成。看错误信息和汇总。写的是 Grok Bot 还没回信（更新请求或读回请求）时，请用户在 Grok 里查看 Bot 做了什么，再运行 `./update.sh --no-pull --yes --lang <en|zh-CN> </dev/null`：它按请求号找到回信并记录。汇总写明各部分都已完成、只有最后的检查失败时，再运行一次 `./check.sh` 并报告结果。其他原因（引擎比固定版本新、要求手工修改设置、relay 队列里还有未送达的消息、云端克隆有本地改动、取不到发布 tag、有 Bot 正在卸载）按[用 update.sh 升级](UPGRADE.zh-CN.md#用-updatesh-升级)、[多个 Bot](UPGRADE.zh-CN.md#多个-bot) 或 [Reason Network 引擎](UPGRADE.zh-CN.md#reason-network-引擎)中对应的部分处理。中断的更新再次运行同一条命令即可继续。
   - `2`：参数用错，检查命令。

不要为了绕过停止而修改 `~/.local/lib/agent-mesh/deployed.json` 或 Reason Network 的设置；`./update.sh` 要求手工修改时，把它的说明交给用户。

## 卸载

`[LOCAL]`：先核对 checkout 的 origin 和本地改动。有本地改动时，把改动给用户看并停下，除非用户明确选择用这个 checkout 卸载；绝不 stash、reset 或丢弃这些改动。否则运行 `git pull --ff-only`（失败时展示输出并停下），再运行 `./uninstall.sh --plan`，把输出给用户看；它列出已安装的内容和每个级别要删除的内容，不改动任何东西。经用户同意，你可以只删除 relay 程序：先由用户在 `[GROK-UI]` 暂停 routine（每个 Bot 的 routine），再由你运行 `./uninstall.sh --level program --yes`。`./install.sh --restart-relay` 可以装回；`./check.sh` 通过后，由用户恢复 routine。`identity` 级别（另外让两台电脑都退出团队，删除密钥和身份）和 `all` 级别（另外删除日志、引擎和云端数据）需要输入确认，并有一步在 Grok Bot 里完成：请用户在自己的终端里运行 `./uninstall.sh --level identity` 或 `--level all`，按[卸载指南](UNINSTALL.zh-CN.md)操作。有多个 Bot 时，除 `bot` 以外的级别都覆盖所有 Bot；只卸载一个 Bot、留下其他 Bot 时，请用户在自己的终端里运行 `./uninstall.sh --level bot --relay-name <relay-name>`，它同样需要输入确认，并有一步在 Grok Bot 里完成；它不卸载最后一个 Bot，那要用 `identity` 或 `all`。这样卸载的 Bot 地址只能通过收回再次使用，收回需要用户自己的终端。已经运行过旧版 `./uninstall.sh --all` 时，`--residual` 的用法见[卸载指南](UNINSTALL.zh-CN.md#卸载身份或全部)。没有终端时它们以退出码 3 结束。所有级别都保留当前代码目录。不要改为手动删除文件，也不要动卸载器列为 foreign 的部署。

## Linux 手工安装（实验性）

`install.sh`、`check.sh`、`update.sh` 和 `uninstall.sh` 只能在 macOS 上运行（[#29](https://github.com/xiaozhenliu/agent-mesh/issues/29)）。在 Linux 电脑上可以手工装好本地端，做法是照着 `install.sh` 首次安装的步骤做一遍。Debian 上的测试覆盖过引擎、建团队，以及 relay 与云端的真实双向消息；整套步骤、systemd 服务和从 Linux 用 CopyToBox 都没有验证过。开始之前先告诉用户，装完以后：

- `check.sh`、`update.sh`、`uninstall.sh` 和 `agent-mesh` 都不能用：不能检查、不能更新、不能改名，也不能再加 Bot；
- 云端同样不能更新，因为云端更新由 `update.sh` 发起；云端会一直停在现在装的版本。

前提，另加第 0 阶段第 2、4 步（网络检查，以及用户的工具、团队名、Bot 名字和地址）：

- x86_64 或 aarch64 的 Linux，普通用户（不是 root），有 Git、Python 3.9+（含 `ssl`、`sqlite3`）、`curl`、`tar`、`sha256sum` 和 `openssl`。用 `command -v` 和第 0 阶段第 1 步的 Python 命令检查。缺了就告诉用户，由用户用发行版的包管理器安装，例如 Debian 上 `sudo apt install git python3 curl openssl ca-certificates`。用不用 sudo 由用户决定。
- 没有别的 Reason Network：`command -v reason-network` 找不到，`~/.reason/network` 不存在。否则停下并报告，同第 0 阶段第 3 步。
- 代码检出在发布 tag 上。还没有代码时按第 0 阶段第 3 步克隆，再在里面运行 `git checkout "v$(cat VERSION)"`。`git describe --tags --exact-match` 必须输出 `v` 加 `VERSION` 文件的内容。
- 装有 Linux 版 Grok Bot 桌面应用，已连接，并允许在本机执行。CopyToBox 从这台电脑复制签名密钥，只能按绝对路径读取 home 文件夹里的文件。没有它，密钥送不到云端：停下。绝不要把密钥贴进聊天。
- 用户使用 Bot 期间，这台电脑一直开着、联网。

`<team-name>`、`<relay-name>` 用第 0 阶段第 4 步得到的值，再给这台电脑起一个机器名 `<machine>`（小写字母和数字，分段之间用单个 `-` 连接，例如 `linux`）。命令都在代码根目录、同一个 shell 里运行。

1. **引擎。** 从 `install.sh` 取版本（开头附近的 `VERSION=`）和你这个平台的校验值（`install.sh` 里的 `pinned_sha256`），然后下载并校验：

   ```bash
   case "$(uname -m)" in x86_64) target=x86_64-unknown-linux-musl ;; aarch64|arm64) target=aarch64-unknown-linux-musl ;; esac
   tmp="$(mktemp -d)"
   curl -fsSL "https://github.com/reason-machines/reason-network/releases/download/<version>/reason-network-$target.tar.gz" -o "$tmp/e.tgz"
   sha256sum "$tmp/e.tgz"
   ```

   校验值必须和固定值相同，否则停下。然后：

   ```bash
   tar --no-same-owner -xzf "$tmp/e.tgz" -C "$tmp" reason-network
   python3 box/rn-engine.py build --bin "$tmp/reason-network"
   mkdir -p ~/.local/bin && install -m 755 "$tmp/reason-network" ~/.local/bin/reason-network
   export PATH="$HOME/.local/bin:$PATH"
   ```

   `rn-engine.py build` 输出固定版本和二进制的校验值；退出码为 1 说明这不是固定版本的构建：停下。PATH 这一行按第 0 阶段第 3 步写进 shell 配置。
2. **团队。**

   ```bash
   python3 scripts/relay_names.py check <relay-name>
   export REASON_NETWORK_NO_PROMPT=1 REASON_NETWORK_MACHINE=<machine>
   reason-network init <team-name> --as <team-name> --no-setup
   python3 box/rn-engine.py config-off --path ~/.reason/network/config.toml auto_update lan
   reason-network stop; reason-network team
   reason-network who --all --json | python3 scripts/relay_names.py conflicts <relay-name>
   ```

   `config-off` 关掉引擎的自动更新，否则它会替换掉固定版本的引擎；失败就停下。`conflicts` 的退出码必须是 0。
3. **接入 agent。** 运行 `reason-network setup --dry-run`，把计划原样给用户看并问"可以执行吗？"；同意后运行 `reason-network setup`。Linux 上的计划可能还会加一个引擎的用户服务，或者说没找到支持的 agent；照原样报告。之后用户按第 3 阶段第 1 步重开已打开的 agent 会话。
4. **密钥和 relay 配置。**

   ```bash
   mkdir -p ~/.config/agent-relay && chmod 700 ~/.config/agent-relay
   (umask 077; openssl rand -hex 32 > ~/.config/agent-relay/outbound.key)
   (umask 077; cat > ~/.config/agent-relay/env <<CONF
   RN_BIN=$HOME/.local/bin/reason-network
   PERSON=<team-name>
   OUTBOUND_KEY_FILE=$HOME/.config/agent-relay/outbound.key
   LISTEN_NAME=<relay-name>
   CLOUD_NAME=<relay-name>-cloud
   INBOX_NAME=<relay-name>-inbox
   CONF
   )
   ```

   两个文件的权限必须是 600、文件夹 700，否则 relay 拒绝启动。
5. **routine。** 运行 `python3 scripts/render_prompt.py routine --lang zh-CN`，把打印出的整段原样交给用户；里面写的是"Mac"，没有关系。告诉用户："在 `[GROK-UI]` 新建一个由 webhook 触发的 routine，把这段贴进去作为提示词。然后在这台电脑的终端里运行下面几行，按提示贴入 Webhook URL（以 `https://` 开头）和 key。"等用户做完。

   ```bash
   read -r -p 'Webhook URL: ' url; read -r -s -p 'Webhook key: ' key; echo
   (umask 077; printf 'WEBHOOK_URL=%s\nWEBHOOK_KEY=%s\n' "$url" "$key" >> ~/.config/agent-relay/env)
   unset url key
   ```

6. **relay。** 复制过去，在前台启动，比如放在 `tmux` 里；用系统 Python 3.9+ 的绝对路径（不要用虚拟环境）：

   ```bash
   mkdir -p ~/.local/lib/agent-mesh && install -m 644 agent_relay.py ~/.local/lib/agent-mesh/agent_relay.py
   AGENT_RELAY_STATE="$HOME/.local/state/agent-relay" AGENT_RELAY_LOG="$HOME/.local/state/agent-relay/agent-relay.log" \
     REASON_NETWORK_HOME="$HOME/.reason/network" PATH="$HOME/.local/bin:/usr/bin:/bin" \
     /usr/bin/python3 ~/.local/lib/agent-mesh/agent_relay.py
   ```

   它一直运行到被停掉；关掉终端或重启电脑它就停了。日志里出现 `agent-relay up: listening as <team-name>/<relay-name>`，并且 `python3 scripts/relay_names.py registered <relay-name>` 输出 `<relay-name>`，就是起来了。

   可选，未经测试：改成 `systemd --user` 服务，停了会自己再起来。先停掉前台的 relay，写好 `~/.config/systemd/user/agent-relay.service`，再运行 `systemctl --user daemon-reload && systemctl --user enable --now agent-relay`。这个服务在用户登录期间运行；没人登录时也要运行，需要 `loginctl enable-linger "$USER"`，这只能由用户决定。

   ```ini
   [Unit]
   Description=agent-mesh relay

   [Service]
   ExecStart=/usr/bin/python3 %h/.local/lib/agent-mesh/agent_relay.py
   Environment=PATH=%h/.local/bin:/usr/bin:/bin
   Environment=REASON_NETWORK_HOME=%h/.reason/network
   Environment=AGENT_RELAY_STATE=%h/.local/state/agent-relay
   Environment=AGENT_RELAY_LOG=%h/.local/state/agent-relay/agent-relay.log
   Restart=always
   RestartSec=30

   [Install]
   WantedBy=default.target
   ```

7. **云端。** 像 `install.sh --cloud-prompt` 那样生成云端提示词：

   ```bash
   repo=https://github.com/xiaozhenliu/agent-mesh.git
   rel="$(python3 scripts/deploy_state.py release --repo-url "$repo")"
   tag="$(printf '%s\n' "$rel" | awk '$1=="tag"{print $2}')"
   commit="$(printf '%s\n' "$rel" | awk '$1=="commit"{print $2}')"
   sums="$(printf '%s\n' "$rel" | awk '$1=="sum"{print $2"  "$3}')"
   reason-network invite --me
   python3 scripts/render_prompt.py cloud-setup --lang zh-CN --team-name <team-name> --mac-home "$HOME" --repo-url "$repo" \
     --relay-name <relay-name> --release-tag "$tag" --release-commit "$commit" --cloud-checksums "$sums"
   ```

   `release` 核对这份代码的云端脚本和发布 tag 一致；失败就停下。从 `invite --me` 的输出里，找以 `For a cloud agent of yours` 开头的那一行，取 `send it:` 后面的链接。把链接和打印出的提示词一起交给用户，然后照第 2 阶段第 2 步继续；CopyToBox 的来源是这台电脑的 `$HOME/.config/agent-relay/outbound.key`，提示词里已经写成完整路径。用户说云端已加入后，`reason-network team` 里必须有一行同时含 `<team-name>` 和 `cloud`。
8. **冒烟测试。** 同第 3 阶段，relay 日志在 `~/.local/state/agent-relay/agent-relay.log`。回复约定：`python3 scripts/reply_convention.py install` 把填好的副本写到 `~/.local/lib/agent-mesh/templates/`，用法同[可选](#可选)。

Linux 上没有卸载器。要关掉时，用户先在 `[GROK-UI]` 暂停 routine，再停掉 relay（Ctrl-C，或 `systemctl --user disable --now agent-relay`）。退出团队、删除密钥或云端数据不在这里：把 [UNINSTALL](UNINSTALL.zh-CN.md) 里各级别会删什么给用户看，不要自己想办法。
