# agent-mesh

[English](README.md) | **简体中文**

agent-mesh **v0.1.0** · [更新日志](CHANGELOG.zh-CN.md) · [版本文件](VERSION)。运行 `./install.sh --version` 可离线查询；依赖的 Reason Network 使用独立版本号。

把你电脑上的 Claude Code、Codex、pi、dsh 会话和云端的 Grok Bot 放进同一个 Reason Network 私有团队。两边可以多轮对话，任何一方都能主动发起，谁回了信，对方都会被立刻叫醒。整套不需要 Reason 账号、服务器、公网 IP、Docker 或 Node。

安装可以交给 agent 代劳：把本仓库的链接发给 Claude Code 或 Codex，说一句「读 [AGENT-INSTALL.zh-CN.md](AGENT-INSTALL.zh-CN.md)，照着装」。本安装流程支持 macOS，固定使用 Reason Network v0.10.7，采用 MIT 许可证。

## 文档与语言

| 文档 | English | 简体中文 |
|---|---|---|
| 使用指南 | [README](README.md) | [使用指南](README.zh-CN.md) |
| Agent 安装 | [Runbook](AGENT-INSTALL.md) | [安装手册](AGENT-INSTALL.zh-CN.md) |
| DeepSeek Harness | [Bridge](dsh/README.md) | [插件指南](dsh/README.zh-CN.md) |
| Grok Bot routine | [Prompt](prompts/routine.md) | [提示词](prompts/routine.zh-CN.md) |
| 云端接入 | [Prompt](prompts/cloud-setup.md) | [提示词](prompts/cloud-setup.zh-CN.md) |
| 回复约定 | [Template](templates/AGENTS.reason-network.md) | [模板](templates/AGENTS.reason-network.zh-CN.md) |
| 翻译维护 | [Contributing](CONTRIBUTING.md) | [维护说明](CONTRIBUTING.zh-CN.md) |

每次运行安装器时，用 `--lang en` 或 `--lang zh-CN` 选择生成的提示词语言。默认英文；环境变量 `AGENT_MESH_LANG` 可以设置当前 shell 的默认值，`--lang` 优先。终端诊断、配置项和协议字段保持英文，agent 按用户偏好的语言交流。多语言支持不改变操作系统支持范围和网络要求。

## 什么时候需要它

- **不需要**：如果任务在 Grok Bot 的云电脑上就能干完，比如跑脚本、做调研、写文件，那直接让它在云电脑上装各家 CLI 就够了，用不上本项目。
- **需要**：如果你想让 Grok Bot 指挥你自己电脑上**正在开着**的 agent 会话，比如改本地仓库、用你自己的订阅额度、在一个长会话里多轮来回，就需要它。注意 `claude -p` 这类命令只会起一个新进程，碰不到正在干活的那个会话。

## 执行位置标识

本指南、安装手册和提示词统一使用以下标识。标识是操作说明，不要输入终端。

| 标识 | 指哪一端 | 在这里做什么 |
|---|---|---|
| `[LOCAL]` | 用户本地电脑（Mac），通常机器名为 `mac` | 本地 coding agent、Reason Network 引擎、relay；运行 `install.sh`、`check.sh`、`uninstall.sh` |
| `[BOT-CLOUD]` | Grok Bot 的云电脑，机器名为 `cloud` | Grok Bot 运行 `/workspace/agent-mesh/box/rn-cloud*.sh`，保存云端状态和收件正文 |
| `[GROK-UI]` | 用户打开的 Grok Bot 界面 | 创建或暂停 routine、粘贴提示词、批准 CopyToBox；这里的操作不是终端命令 |

两台电脑各有一份仓库，文件不会自动同步。`[LOCAL]` 的 `~` 是用户 Mac 的 home；`[BOT-CLOUD]` 的 `/workspace/agent-mesh/` 是 Bot 云电脑的目录。下文未另行标注的终端命令和 `~/…` 路径均属于 `[LOCAL]`。

## 怎么工作

一共三个角色：

- **`[LOCAL]` 用户本地电脑（一直开着）**：运行 Reason Network 引擎和 relay。relay 用 `<team-name>/bot` 这个名字收信，所以在本地 agent 看来，它就是 Grok Bot。地址中的 `bot` 代表在 Mac 上收信的 relay，不是 Bot 云电脑。
- **`[BOT-CLOUD]` Bot 云电脑（会睡）**：不留任何常驻进程，只在被叫醒的那一轮里临时启动引擎。它用 `<team-name>/bot-cloud` 发信，用 `<team-name>/bot-inbox` 收正文，机器名是 `cloud`。
- **`[GROK-UI]` routine**：在 Grok Bot 界面创建的 webhook 任务，醒来后由 Bot 在 `[BOT-CLOUD]` 执行提示词。`[LOCAL]` 的 relay 向这个 webhook 发一次 POST，Grok Bot 就会醒来。

```
[LOCAL] 本地 agent ──回信──► [LOCAL] relay ──正文走 Reason Network（RN-FWD）──► [BOT-CLOUD] bot-inbox
                              └──门铃：webhook 只带 id、长度、sha256──► Bot 醒来 ──[BOT-CLOUD] rn-cloud.sh fetch 取正文并核对

[BOT-CLOUD] Grok Bot ──签名信封 RN-OUT──► [LOCAL] relay 以 <team-name>/bot 代发 ──► [LOCAL] 本地会话；回信回到 relay
```

背后有四个设计决定：

1. **谁一直开着，谁就是中心。** 云端发出的每一封信都交给 Mac 代发，所以本机会话的回信总是落回 Mac，而不是落到一台可能已经睡着的云电脑上。
2. **webhook 只当门铃，不当快递。** 原部署中观察到 Grok Bot 醒来时只保留 webhook body 的前 4000 字左右，所以正文不走 webhook，而是通过 Reason Network 投进云端收件箱；webhook 携带消息编号、长度、校验码和可选预览。routine 醒来后按编号取回正文、核对无误再处理。在引擎字节限制内，一条 10000 字的消息也只需要叫醒一次。
3. **补寄由收件方发起。** relay 为每条转发过的正文保留 7 天副本。云端 `fetch` 取不到正文时，会自动发出一条带签名的重投请求，relay 按副本重发，每条消息最多重发 2 次。第一次转发就失败的消息，也靠这条路补回来。
4. **所有跨端消息都签名。** 同一个团队里的任何会话都可以用别人的名字发信，所以 relay 代发的信封、转发的正文和重投请求都带 HMAC 签名；信封和重投请求还有 15 分钟时效并防重放。密钥只有两份，Mac 和云电脑各一份。

## 准备

- `[LOCAL]` 支持 Apple Silicon 或 Intel Mac，需要已登录 macOS 桌面的普通用户；不要用 `sudo`，纯 SSH/无桌面登录环境不在本流程支持范围内。Windows 和 Linux 本地电脑不支持。
- `[LOCAL]` 先准备 Git、Python **3.9+**（含 `ssl`、`sqlite3` 标准库模块）。用 `git --version` 和 `python3 -c 'import sys, ssl, sqlite3; print(sys.version)'` 检查；缺少 Git 或 Python 时可先运行 `xcode-select --install`，完成后重新检查，Python 版本仍不足则另装符合要求的 Python 并放入 PATH。脚本也会检查 `curl`、`tar`、`shasum`、`openssl`、`launchctl`、`plutil`；缺少时按报错补齐。无需单独安装 Node 或 pip 包。
- `[LOCAL]` 至少先安装并登录一个要接入的 Claude Code、Codex、pi 或 DeepSeek Harness，确认它能独立正常工作；本项目不负责安装这些 agent、配置模型账号或订阅。接入哪些工具以 `reason-network setup --dry-run` 在这台电脑上打印的计划为准，不沿用作者电脑的配置。
- `[LOCAL]` 确保 `~/.local/bin` 在 PATH 里。当前 Bash/zsh 可运行 `export PATH="$HOME/.local/bin:$PATH"`；需要持久生效时，zsh 写入 `~/.zshrc`，Bash 按实际启动方式写入 `~/.bash_profile` 或 `~/.bashrc`，fish 使用 `fish_add_path "$HOME/.local/bin"`。不要一律修改 `~/.zshrc`。relay 服务会记录安装时检测到的 Python 绝对路径；如果以后移动或卸载该 Python，需要修复 Python 后重新运行安装器来更新服务。
- `[LOCAL]` 安装前检查已有 Reason Network：本流程使用 `~/.local/bin/reason-network`、默认状态目录 `~/.reason/network`，固定 v0.10.7。其他 PATH 安装、不同版本或自定义 `REASON_NETWORK_HOME` 会导致安装器停止，请先评估已有部署，避免覆盖。已有同名 person 的团队可复用；不同 person 不能直接改名。`--all` 卸载会移除这套默认引擎及其接入，可能影响已有用途，不能把它当作通用排错步骤。
- `[BOT-CLOUD]` 需要 Linux x86_64 或 ARM64、Bash、Git、Python 3.9+（含 `ssl`、`sqlite3`）、`curl`、`tar`、`sha256sum`，以及可写且持久的 `/workspace/agent-mesh/`。缺少工具时由 Bot 按云电脑实际系统的包管理方式准备；需要权限时向用户说明，不要改在用户 Mac 上安装。云端脚本会在下载前检查系统、CPU 和运行依赖；准备目录和 Git 的检查见云端提示词。
- `[LOCAL]` 和 `[BOT-CLOUD]` 都需能通过 HTTPS 访问 GitHub 仓库及 release 下载，并能连接 iroh 中继。默认使用公开仓库的 HTTPS 地址，无需 GitHub 账号或 SSH 密钥；维护公开 fork 时可在每次生成云端提示词的安装命令中传 `--repo-url https://…`，该地址必须允许云电脑无凭据读取。代码可克隆到本地任意目录；Bot 云电脑仍须使用 `/workspace/agent-mesh/`，因为提示词引用该固定路径。
- 你的 Grok Bot 能创建由 webhook 触发的 routine，并且能用 CopyToBox 读取你电脑上的文件。
- Mac 需要一直开着、不睡眠：在系统设置里打开「接通电源时防止自动进入睡眠」，或者开一个终端跑 `caffeinate`。注意合上盖子整套就会停。
- 想好一个团队名，它同时也是你在团队里的名字：1 到 40 个字符，只能用 ASCII 小写字母、数字和 `-`，首尾不能是 `-`，别用真名。团队建好后名字不能改，只能删掉重建。
- 如果你用 VPN 或代理软件，先确认它没有拦住 Reason Network 联机要用的 `*.iroh.link`。用下面这条命令测，返回 200 才算通；不通的话，给 `iroh.link` 整个域名加一条直连规则，并把它排除出 HTTPS 解密，具体排查步骤见「常见问题」。
  `curl -sS -o /dev/null -m 10 -w 'code=%{http_code}\n' https://aps1-1.relay.n0.iroh.link/`

这条探测只检查一个中继端点，不代表已经验证全球各地的可用性。

## 安装

**`[LOCAL]` 用户或本地安装 agent 执行：**

```
git clone https://github.com/xiaozhenliu/agent-mesh.git ~/agent-mesh
cd ~/agent-mesh
./install.sh --name <team-name> --lang zh-CN
```

先把团队名占位符换成实际值。目标目录已存在时先核对它是不是同一仓库、有无未提交改动，不要覆盖或盲目 pull。以上克隆的是公开发行仓库。云端默认克隆同一个公开发行仓库，其根目录必须包含 `box/rn-cloud-setup.sh`；安装器不会读取本地 origin 来推断云端仓库地址。

这一条命令会做完 Mac 这边的全部工作，中途会停两次等你动手：

1. 安装 Reason Network v0.10.7（会校验 sha256），创建一个只有你一个人的私有团队，并关掉自动更新和局域网广播。
2. 显示 `reason-network setup --dry-run` 的计划，**等你确认后**再把本机 agent 接入团队。
3. `[LOCAL]` 生成签名密钥，并打印 routine 提示词。**用户在 `[GROK-UI]` 新建一个由 webhook 触发的 routine，把提示词贴进去**，再把面板给出的 Webhook URL 和 key 贴回 `[LOCAL]` 的终端。脚本随后写好本地配置，把 relay 装成 Mac 登录时自动启动的服务。
4. `[LOCAL]` 打印团队邀请链接和给 Grok Bot 的云端提示词。**用户在 `[GROK-UI]` 把这两段一起发给 Grok Bot**。Bot 在 `[BOT-CLOUD]` 克隆仓库、加入团队，然后用 CopyToBox 把密钥从 `[LOCAL]` 复制到 `[BOT-CLOUD]`；用户在 `[GROK-UI]` 批准这次复制。

最后，把已经开着的 Claude Code、Codex 会话关掉重开。Codex 第一次启动会询问，选 Trust all and continue。

可选：
- dsh 自动唤醒：运行 `dsh/mac-install.sh <team-name>`，详见[插件指南](dsh/README.zh-CN.md)。
- 回信约定：把[中文模板](templates/AGENTS.reason-network.zh-CN.md)或[英文模板](templates/AGENTS.reason-network.md)整段追加到你常用仓库的 `AGENTS.md` 里（Claude Code 还需要在 `CLAUDE.md` 里写一行 `@AGENTS.md`）。本机 agent 默认会按给人看的完整报告来写回信；这份模板要求经这条通道发出的消息结论先行、四项必填，细节留在会话里不发。

## 验证

执行位置：自检、发送测试消息和查看 relay 日志在 `[LOCAL]`；Grok Bot 的 `who` 和 `fetch` 使用 `[BOT-CLOUD]` 的 `/workspace/agent-mesh/box/rn-cloud.sh`。

- 运行 `./check.sh` 做只读自检，每一条 FAIL 后面都写了怎么修。
- 在终端发 `reason-network send <team-name>/bot "测试 1"`，再另开一个窗口运行 `reason-network listen --as terminal --wait 600` 等回复。relay 日志（`~/Library/Logs/agent-relay.log`）里应出现 `forwarded` 和 `POST … code=200` 两行。
- 让 Grok Bot 先运行 `who`，再给某个 Claude Code 会话发一句话。即使那个会话正空闲，也会被叫醒；它回复之后，Grok Bot 会再次被叫醒。
- 测长消息：从本机发一条 10000 字的消息。Grok Bot 应该只被叫醒一次，并报告 `fetch` 结果为 complete、长度和 sha256 都一致。完整签名消息须在 relay 的 60 KiB 转发上限内；UTF-8 字节数随文本内容而变。

## 各 agent 怎么收信

`reason-network who` 输出的最后一列是每个会话的收信方式：

- **push**（空闲时也会被叫醒）：Claude Code 是直接把消息写进会话；Codex 是通过 `codex exec resume`；pi 是靠扩展每 5 秒轮询一次。
- **turn**（要等到下一次工具调用或这一轮结束才看到）：dsh 装了插件后显示为 `turn`，但实际上它空闲时约 5 秒内就会自动开一轮来处理。
- **pull**（不会被叫醒）：没装插件的 dsh 属于这一档，得让它自己去查收件箱。

会话一关掉就会从名单里消失，这期间发给它的信会留在收件箱里；重开之后，你需要对它说一句「看下收件箱」它才会读。所以想让消息马上被处理，就得一直开着一个会话。

## 安全与隐私

- 这条桥接通道只给本地 agent 发文字，不直接授予 Grok Bot 文件访问权，也不绕过本地 agent 的权限。要不要执行、怎么执行由接收会话决定。Mac 不开任何入站端口。Grok Bot App 里的「本机永远批准」和「通过本机网络连接」两个开关都不用开。
- 上一条成立的前提是本机 agent 没开自动批准。对开了 `--full-auto` 之类模式的 agent 来说，收到的文字就等于命令，这种会话只能在沙盒文件夹里开。
- 签名防的是拿不到密钥的会话冒名发信。但同一个 macOS 用户下运行的任何 agent 都读得到 `~/.config/agent-relay/` 里的密钥和 webhook key，项目文件夹并不是权限边界。
- 如果你不想让云端来信直接投递，可以在 `~/.reason/network/config.toml` 里设置 `[inbound] default = "hold"`，这样每条来信都要你 `reason-network approve` 之后才会投递。
- 团队成员能看到每个会话的名字、所在目录的最后一级、仓库名和分支。所以别在 `~` 下直接开 agent。团队邀请链接只在终端里用，不要在浏览器里打开。
- 引擎开源、固定版本，以无账号模式在本机运行，无账号模式下不向 Reason 官方发送数据。消息可能经过 iroh 公共中继，上游将消息内容描述为加密传输；本项目没有独立审计这些中继。用 `reason-network relay <URL>` 可以换成自建中继。
- 每叫醒一次 Grok Bot 就消耗一次它的用量。relay 默认每小时最多叫醒 20 次。

## 常见问题

- **联不上 iroh 中继**（「准备」里那条 curl 不返回 200，或 install.sh 停在 cannot reach the iroh relay）：几乎都是 VPN 或代理软件造成的，按下面的顺序排查。
  1. 先把 VPN 或代理整个关掉再测一次。能通，就确定是它拦的；还是不通，那是网络本身的问题。
  2. 会拦的设置有两类。一类是接管整台电脑流量的模式，各家客户端叫法不同，常见的有 TUN、虚拟网卡、增强模式、全局模式、系统代理；另一类是 HTTPS 解密（也叫 MITM），它会替换中继的证书，引擎会连不上。
  3. 修法：给 `iroh.link` 这个域名连同所有子域加一条直连规则（规则里通常叫 DIRECT、直连或 bypass），再把这个域名排除出 HTTPS 解密。引擎可能连到 `*.iroh.link` 下不止一台中继，所以要按域名放行，不要只放行 aps1-1 这一台。
  4. 验证：curl 返回 200，并且 `curl -sv -o /dev/null https://aps1-1.relay.n0.iroh.link/ 2>&1 | grep issuer` 显示的签发者是公共 CA（目前是 Let's Encrypt）。如果显示的是你代理软件自己的 CA，说明解密还没排除掉。关闭代理后仍失败，则排查 DNS、防火墙和网络本身。
- **云电脑加入时报 couldn't reach the machine that made the invite**：通常是 Mac 这边的 VPN 或代理又把 `*.iroh.link` 拦住了。按上一条修好，在 Mac 上运行 `reason-network stop`，然后让 Grok Bot 用同一个邀请链接重试。
- **who 里看不到某个会话**：在 `setup` 之前就开着的会话不会登记，关掉重开即可。Codex 还要先跑完一轮才会出现。
- **fetch 报 missing**：先看结果里的 `refwd` 字段。`ack=resent` 表示 relay 已经重发，过一分钟再 `fetch` 一次；`no-copy`、`limit`、`failed` 或 `skipped` 表示补不回来了，请发送方重发；完全没有 `refwd` 字段，说明 Mac 没连上。
- **消息被拒收 refused**：这是引擎的防循环机制，同一对 agent 之间 10 分钟内超过 12 条就会拒收。relay 自己的转发是以 terminal 身份发出的，不受这个限制。
- **正文里的 `<` 变成了 `[`**：0.10.7 版的 `listen` 会对部分标签做防伪改写，字符数不变。重投是按 relay 保存的副本发的，保持原文。

## 卸载

- `./uninstall.sh`：删除 relay 服务、状态和消息副本、日志，以及 `~/.config/agent-relay`。
- `./uninstall.sh --all`：在上面的基础上再执行 `leave --force` 和 `setup --remove`，并删除 `~/.reason/network` 和 reason-network 二进制。
- `[GROK-UI]` 暂停或删除 routine；`[BOT-CLOUD]` 再删除 `/workspace/agent-mesh/`。上面两条 `uninstall.sh` 命令均在 `[LOCAL]` 执行。

## 附录：消息格式

密钥是 `outbound.key` 去掉首尾空白后的那个 64 位 hex **字符串**：把它的 UTF-8 字节直接当作 HMAC-SHA256 的 key，不做 hex 解码。签名结果用小写 hex 表示。引擎会去掉消息的首尾空白，所以发送端在签名前要先去掉正文末尾的空白；正文里不要出现 `<reason-message` 这个字样。

协议字段和测试向量在所有语言中保持一致，下面向量里的中文也必须原样保留。

**RN-OUT v1**（`bot-cloud` 发给 `bot` 的代发信封）：
```
RN-OUT v1
to: <收件地址>
reply_to: <被回复的消息 id，可为空>
ts: <Unix 秒>
nonce: <随机 hex，建议 12 字节>
sig: <hex>
---
<正文>
```
签名：`sig = HMAC(key, "v1\n" + to + "\n" + reply_to + "\n" + ts + "\n" + nonce + "\n" + body)`。接收端以第一个 `\n---\n` 为界分开头部和正文。测试样例：key 为 `0123456789abcdef` 重复 4 次，to=`demo/claude-session`，reply_to=`01JTESTREPLYTO0000000000`，ts=`1700000000`，nonce=`0123456789abcdef01234567`，body 为 `hello` 换行 `第二行`，则 sig=`652fbb03f1fd8626fd4f5db689b5983bdd57c797d5ba1085c340aaf699003088`。

**RN-FWD v1**（relay 发给 `bot-inbox` 的正文）：
```
RN-FWD v1
id: <消息 id>
from: <发件地址>
agent: <发件会话的 agent 类型，如 claude-code>
thread: <线程 id，可为空>
text_len: <正文字符数>
text_sha256: <正文的 sha256>
ts: <Unix 秒>
sig: <hex>
---
<原文>
--- RN-FWD END
```
签名：`sig = HMAC(key, "fwd1\n" + id + "\n" + from + "\n" + agent + "\n" + thread + "\n" + text_len + "\n" + text_sha256 + "\n" + ts)`。text_len 按 Unicode 码点计数（Python `len`），不是 UTF-8 字节数或视觉上的字形数量；sha256 按 UTF-8 字节计算。测试样例：同一个 key，id=`01JTESTMSG0000000000000000`，from=`demo/claude-session`，agent=`claude-code`，thread 为空，text 为 `你好` 换行 ` x `，ts=`1700000000`，则 sig=`a48eb3bbb089a3d6b31fed070d76063bb3b3ddabfac08b7651f4093ce358c576`。

**RN-REFWD v1**（`bot-cloud` 发给 `bot` 的重投请求，没有正文）：
```
RN-REFWD v1
id: <要补寄的消息 id>
ts: <Unix 秒>
nonce: <随机 hex>
sig: <hex>
```
签名：`sig = HMAC(key, "refwd1\n" + id + "\n" + ts + "\n" + nonce)`。relay 只看第一行就判断一条消息是不是重投请求。测试样例：同一个 key，id=`01JTESTMSG0000000000000000`，ts=`1700000000`，nonce=`0123456789abcdef01234567`，则 sig=`eb9e0a80d3126e9f8f026e584927e864871d390c39b5dec8cc02a7a6e2922668`。

**RN-REFWD-ACK v1**（relay 回给 `bot-inbox` 的回执）：
```
RN-REFWD-ACK v1
id: <消息 id>
status: <resent | no-copy | limit | failed>
attempt: <第几次重投>
reason: <原因，可为空>
ts: <Unix 秒>
sig: <hex>
```
签名：`sig = HMAC(key, "refwdack1\n" + id + "\n" + status + "\n" + attempt + "\n" + ts)`。测试样例：同一个 key，id 同上，status=`resent`，attempt=`1`，ts=`1700000000`，则 sig=`faa3cd29c693eb142c3f3bbb38fa0be8c46bcea4869f90ce9842aa41aa83f6ea`。

**门铃**（relay POST 给 routine 的 JSON）：
```
{
  "source": "reason-network",
  "relay_version": 3,
  "doorbell": true,
  "to": "<team-name>/bot",
  "reply_via": "<team-name>/bot-cloud",
  "inbox": "<team-name>/bot-inbox",
  "messages": [
    {"id": …, "from": …, "agent": …, "thread": …, "text_len": …, "text_sha256": …, "preview": …}
  ]
}
```
`messages[]` 的每一项还可能带两个可选字段：`refwd`（这是第几次重投）和 `forward_failed`（第一次转发失败的原因）。relay 自己发出的系统通知则是 `doorbell:false`，这时 `messages[]` 的每一项里直接带 `text`。
