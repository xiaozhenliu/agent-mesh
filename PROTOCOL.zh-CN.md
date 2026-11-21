# 协议参考

[English](PROTOCOL.md) | **简体中文**

[agent-mesh 使用指南](README.zh-CN.md)的参考部分：丢失的信怎么补回、一条消息怎么只交给一轮处理，以及带签名的消息格式和测试向量。概览见[怎么工作](README.zh-CN.md#怎么工作)；`[LOCAL]`、`[BOT-CLOUD]`、`[GROK-UI]` 的含义见[执行位置标识](README.zh-CN.md#执行位置标识)。

## 补寄与回执

relay 为每条转发过的正文保留 7 天副本。云端 `fetch` 取不到正文时，会自动发出一条带签名的补寄请求，relay 按副本重发，每条消息最多重发 2 次。第一次转发就失败的消息，也靠这条路补回来。云端还会给 relay 发带签名的回执（RN-GOT），说明哪些消息已落盘、哪些已处理；带 `reply_to` 的回信也算已处理。消息在 relay 上一直记为待办，直到确认处理完。门铃送达后 `GOT_TIMEOUT_SECONDS`（默认 15 分钟）仍没有落盘回执，relay 重发正文并补按门铃；已落盘但 `GOT_DONE_SECONDS`（默认 2 小时）后仍未处理，只补按门铃，等上一轮的领取过期后由云端重新交出。每条消息最多补按 `GOT_MAX_RERINGS`（2）次，不占补寄次数；之后 relay 给 Grok Bot 发一条点名这条消息的提醒，Bot 确认之前会重复发送。`GOT_TIMEOUT_SECONDS=0` 对新来信关闭补按。已知限制：

- 云端停发回执（云端脚本是旧版或设了 `RN_GOT=0`）时，relay 多补按一次后察觉，发一条提醒，并在收到下一条回执之前不再盯新来信。这期间，某一轮领取了新来信、还没处理就中断的情况，只能靠孤儿扫描或发件人重发补回。当时已有的待办仍会按期补按，直到处理完或逐条提醒。
- 回执关闭期间，只有回信能证明消息已处理，只记了 `done` 的消息会以一条提醒结束：属于误报，不丢信。
- 一条提醒送达 `GOT_MAX_RERINGS + 1` 次都没有被确认时删除，只留在 relay 日志里（`ALERT` 行）。
- 待发提醒超过 200 条时只推送条数，逐条内容在 relay 日志里。
- 补按次数用尽后放弃并逐条提醒，不会无限重试。
- relay 记住已处理的 id `FWD_COPY_DAYS` + 1 天（默认 8 天）；之后同一 id 再次送达，会再转发一次。
- 启动对账（引擎已标为已读、relay 还没处理的消息）和云端的孤儿扫描都按发件人的时间戳挑选消息。发件人时钟偏慢时，消息可能落在窗口之外（relay 的窗口从它在这个版本上首次启动时算起，或往前 7 天，取较晚者；孤儿扫描覆盖 `RN_ORPHAN_DAYS`，默认 7 天）。这样的消息只有对方重发才会回来。
- relay 读不了引擎数据库时，跳过启动对账，记日志，每个 relay 进程给 Grok Bot 发一条提醒；relay 停止前刚被标为已读的消息可能因此没有转发。

## 领取与中断

两次叫醒可能重叠，webhook 重试也可能为同一个 id 按两次门铃。`fetch` 交出的每条正文都会被领取一小时（`RN_CLAIM_LEASE`），重叠的另一轮拿到 `claimed` 就跳过；处理完的消息报告 `handled`。Bot 动手之前先运行 `rn-cloud.sh working <id>`，`rn-cloud.sh send` 在交给引擎之前记下"开始回复"。一轮在这两步之后中断、没有记完成的，之后的轮次报告 `uncertain`：Bot 查 thread 笔记或问用户，不会自己再做一次。每次 `fetch` 还会交出最近 7 天内没有门铃要过、也没人处理的已核对正文，例如门铃丢失的信（`"orphan": true`；天数由 `RN_ORPHAN_DAYS` 设置，`RN_ORPHANS=0` 关闭）。这不保证"恰好一次"：在 `working` 之前中断的消息，领取过期后会被再次交出；在那之后中断的，等用户决定。

已知限制：

- 引入领取之前就有的消息文件夹，在升级后的第一次运行时标为 `legacy`：孤儿扫描跳过它们，只有门铃点名时才交出。
- 核对不通过的消息（`mismatch`）在没有门铃点名的扫描里（孤儿扫描，以及不带 id 的 `wait`/`fetch`）只报一次。点名要的 id 和诊断用的 `scan` 每次都报，所以一直存在的 id 冲突会在每次补按时再报。
- 扫描先记下坏消息再输出，两步之间中断的那一轮会丢掉这一条提示。消息仍在引擎数据库里，诊断用的 `scan` 仍能看到。
- 升级步骤不记录已经存在的坏消息，所以升级后的第一次扫描会把它们各报一次。

## 多个 Bot

同一台 Mac 上的多个 Grok Bot 共用团队、两台电脑上的 Reason Network 引擎、云电脑及其克隆 `/workspace/agent-mesh`，以及签名密钥 `outbound.key`；每个 Bot 有自己的三个名字、relay、webhook、routine、收件和 thread 笔记（见[多个 Grok Bot](README.zh-CN.md#多个-grok-bot)）。

| | 第一个 Bot | 添加的 Bot，slot 为 `<slot>` |
|---|---|---|
| `[LOCAL]` 上的配置 | `~/.config/agent-relay/env` | `~/.config/agent-relay-bots/<slot>/env`（其中 `OUTBOUND_KEY_FILE` 指向共用的密钥） |
| 登录服务 | `com.local.agent-relay` | `com.local.agent-relay.<slot>` |
| relay 状态 | `~/Library/Application Support/agent-relay/` | `~/Library/Application Support/agent-relay-bots/<slot>/` |
| relay 日志 | `~/Library/Logs/agent-relay.log` | `~/Library/Logs/agent-relay-<slot>.log` |
| `[BOT-CLOUD]` 上的收件和 thread 笔记 | `/workspace/agent-mesh/inbox/`、`threads/` | `/workspace/agent-mesh/bots/<slot>/inbox/`、`threads/` |
| `[BOT-CLOUD]` 上的地址名 | `box/relay` | `bots/<slot>/name`，没有这个文件时就是 slot |
| 曾用名、进行中的改名、暂扣的消息 | `box/former`、`box/rename.pending`、`box/exclude.json` | `bots/<slot>/` 下的同名文件 |

**选择器。** 添加的 Bot 把添加时用的名字保留为它的 slot `<slot>`；改名之后，表中的目录和登录服务仍然用 slot。它的 routine 桩和加载的规则把每条 `rn-cloud.sh` 命令写成 `RN_BOT=<slot> /workspace/agent-mesh/box/rn-cloud.sh …`。这样命令使用 `bots/<slot>/` 下的路径和会话 `<current>-cloud`、`<current>-inbox`，其中 `<current>` 是表中这个 Bot 的地址名，并要求 `bots/<slot>/.registered` 存在。`RN_BOT` 不能和 `AGENT_RELAY_NAME`、`RN_CLOUD_NAME`、`RN_INBOX_NAME`、`RN_INBOX_DIR` 或 `RN_THREADS_DIR` 同时使用，也不会退回到第一个 Bot。不带 `RN_BOT` 时和以前一样使用第一个 Bot。

**登记。** 添加的 Bot 的桩把团队名和团队哈希传给 `instructions`；只要 `bots/<name>/.registered` 不存在，它先运行 `bash /workspace/agent-mesh/box/rn-cloud-add-bot.sh '<name>' '<team-name>' '<team-hash>'`（`--full-prompt` 的完整提示词把这条命令作为第 0 步），它核对团队，登记 `<name>-cloud` 和 `<name>-inbox`，并打印 `Bot address: <team-name>/<name> (this cloud uses <name>-cloud and <name>-inbox)`。第四个参数 `reclaim` 只在用户于 `./install.sh --add-bot` 中确认取回名字后才出现在提示词里，它允许登记已卸载 Bot 的名字或本云端密钥已持有的会话（[取回名字](#取回名字)）。团队哈希是引擎团队 id 的 SHA-256 的前 16 个十六进制数字。随后 `./install.sh --add-bot` 经新 Bot 的 relay 给它发一行 `agent-mesh add-bot <name> <request>`（`<request>` 是小写十六进制）。加载的规则把它交给 `rn-cloud.sh request`：对用户自己的 Mac 发来的这一行，运行 `box/rn-cloud-add-bot.sh --check '<name>' '<request>'` 并回复输出（[请求](#routine-桩与维护请求)）；Mac 核对其中的 `agent-mesh-result add-bot <request> ok <name> <team-hash>` 一行，之后才把这个 Bot 的云端登记记为已确认；其中的 `lang` 和 `stub` 两行同时确认这个 Bot 的桩。别人发来同样的一行不会执行。

**已卸载的名字。** 卸载一个 Bot 时最先为它的当前名字和每个曾用名写 tombstone `box/bots-removed/<name>`，记下时间和 `<name>-inbox` 当时的引擎行；任何指定了带 tombstone 的 Bot 的命令都会被拒绝，只有用户的 Mac 确认取回时这个名字才能再登记。第一个 Bot 卸载时，`box/relay` 改名为 `box/relay.removed`，不带 `RN_BOT` 的命令都会被拒绝。Mac 上的部署记录保存已卸载的名字。

**维护锁。** `rn-cloud.sh` 命令以共享方式持有 `/workspace/agent-mesh/.maint.lock`；云端安装、云端更新、添加 Bot 的登记和云端卸载以独占方式持有它。命令最多等 `RN_MAINT_WAIT` 秒（默认 600），否则以 `STOP: other cloud commands are still running; nothing was changed` 停下。Mac 上的 `install.sh`、`update.sh` 和 `uninstall.sh` 共用锁文件 `~/.local/lib/agent-mesh.lock`，同一时间只能运行其中一个。

云端有多个 Bot 时拒绝 `rn-cloud.sh purge`，因为它会连其他 Bot 的信一起删除。

## 给 Bot 改名

`agent-mesh rename <new-name>`（[给 Bot 改名](README.zh-CN.md#给-bot-改名)）原地更改一个 Bot 的地址名；它的 slot、目录、登录服务、routine 和收件都不变。

**切换。** Mac 经这个 Bot 的 relay 给它发请求 `agent-mesh rename <slot|-> <old> <new> <request>`（第一个 Bot 用 `-`），用户确认取回名字时再跟一行 `reclaim=1`（[请求](#routine-桩与维护请求)）。云端在独占维护锁下运行 `box/rn-names.py apply rename`：核对这个 Bot 正在用 `<old>`、没有别的 Bot 正在用或用过 `<new>`，把这次切换记入 `rename.pending`，登记 `<new>-cloud` 和 `<new>-inbox`，然后回复 `agent-mesh-result rename <request> pending <slot> <old> <new> held <n>`。在第一条 RN-FWD 签名有效的消息到达 `<new>-inbox` 之前，Bot 继续以 `<old>` 应答；之后第一条读取这个 Bot 名字的命令把 `<new>` 写为它的地址名，并把 `<old>` 记入 `former` 保留 30 天。再次发来同一个请求时按记录答复；切换未完成时请求另一个新名字会取代它，被放弃的名字仍算这个 Bot 的曾用名。

**曾用名。** 30 天内 `fetch` 同时读取这个 Bot 曾用名的收件箱，Bot 收件目录里的标记保证同一个源 id 从两边到达时只处理一次。Mac 上 relay 的配置以 `FORMER_NAMES=<name>:<到期时间，Unix 秒>,…` 列出它们；到期之前，每个曾用名都是第二套身份（`<name>`、`<name>-cloud`、`<name>-inbox`）：它发来的签名 RN-OUT、RN-GOT 和 RN-REFWD 照常接受，relay 每隔 `FORMER_POLL_SECONDS` 秒（默认 60）用 `listen --as <name> --wait 0` 取一次 `<name>` 的信，像其他信一样转发。回信和转发始终使用当前名字。30 天之后，曾用名发来的消息重新当作普通消息，它的信也不再被取走。

### 取回名字

团队里已经有会话、但不属于这个 Bot 的名字（已卸载 Bot 的名字，或早先安装留下的名字）需要 `reclaim=1`。登记之前，云端把 `<new>-inbox` 里已有的每条消息按请求号记入 `exclude.json`，记引擎行号和（能读出时）RN-FWD 源 id，`fetch` 跳过它们；`held <n>` 是其中签名有效的不同源 id 的数量。之后到达这个收件箱的消息从不暂扣。名字登记完成后删除它的 tombstone。`./install.sh --add-bot` 经登记参数 `reclaim` 取回名字，批次名为 `add`，这时它的核对回信在 `add-bot` 一行之前多一行 `agent-mesh-result held add <n>`。它的 relay 可能在登记之前启动，并立即转交 Mac 上排队等着发往这个名字的信，所以对已卸载 Bot 的名字，这一批记 tombstone 在 Bot 卸载时记下的消息，以及已被 `fetch` 标为已读的消息；之后到达的，比如这些排队的信，作为新消息交给 Bot。旧版本写的 tombstone 没有这份清单，只暂扣已标为已读的消息；两种都不是的 tombstone（例如空文件或被截断）会让取回停下。早先安装留下的名字，这一批记收件箱里的全部消息，包括 relay 已经转交的信。

`agent-mesh rename --accept-history` 为每个仍被暂扣的批次发一条 `agent-mesh history <slot|-> accept <batch> <request>`，其中 `<batch>` 是改名的请求号或 `add`。云端把这一批移出 `exclude.json`，回复 `agent-mesh-result history <request> accepted <batch> <n>`；Bot 从下一次 `fetch` 起处理这些消息，即使那个名字已不再属于它。

## routine 桩与维护请求

从 v0.10.0 起，routine 提示词就是 routine 桩：一段固定文本，第一行是 `agent-mesh routine stub v<N>`（[routine 桩](UPGRADE.zh-CN.md#routine-桩)）。只有桩本身变化时它的版本 `<N>` 才变。

**规则。**桩的第 1 步运行 `bash /workspace/agent-mesh/box/rn-cloud.sh instructions <N>`；后加 Bot 的桩运行 `RN_BOT=<name> bash … instructions <N> '<team-name>' '<team-hash>'`。这条命令拒绝云端 checkout 不支持的桩版本，以共享方式持有维护锁，按 `box/lang` 里的语言、团队和 Bot 用 `prompts/routine-body.md` 或 `prompts/routine-body.zh-CN.md` 渲染规则，打印在 `agent-mesh-instructions BEGIN <N> v<版本> <语言> <slot>` 和最后一行 `agent-mesh-instructions OK <N>` 之间。只有最后一行恰好是这一行时，Grok Bot 才照输出执行。它还记下桩版本和时间，这样同一次运行里稍后答复的请求可以报告它。`./update.sh --full-prompt` 在 Mac 上把同样的规则渲染成完整提示词，命令相同。

**请求。**规则把本团队用户发来、第一行以 `agent-mesh ` 开头的每条消息交给 `rn-cloud.sh request <id>`。它读取 `fetch` 存下并验证过的消息，要求签名中的发件人与云端是同一个人，只接受下面几种第一行之一，可以再跟表中注明的附加行（`lang=<en|zh-CN>` 或 `reclaim=1`）：

| 第一行 | 附加行 | 运行 |
|---|---|---|
| `agent-mesh update <tag> <commit> <request>` | `lang=` | `box/rn-cloud-update.sh '<tag>' '<commit>' '<request>'` |
| `agent-mesh add-bot <name> <request>` | — | `box/rn-cloud-add-bot.sh --check '<name>' '<request>'`，只在 Bot `<name>` 上运行 |
| `agent-mesh stub <request>` | `lang=` | 不运行什么，只报告这次运行加载了什么 |
| `agent-mesh rename <slot\|-> <old> <new> <request>` | `reclaim=1` | 在独占维护锁下运行 `box/rn-names.py apply rename …`，只在该 Bot 上运行（[给 Bot 改名](#给-bot-改名)） |
| `agent-mesh history <slot\|-> accept <add\|request> <request>` | — | 在独占维护锁下运行 `box/rn-names.py apply history …`，只在该 Bot 上运行 |

`<request>` 是 8 到 32 位小写十六进制数字；Mac 生成请求时把前 8 位设为当时的 Unix 秒数（十六进制），后面跟 8 位随机数字。其他内容打印 `agent-mesh-request ORDINARY`，规则把这条消息当作普通消息处理。带 `lang=` 的请求成功时把这个语言写入 `box/lang`，云端上每个 Bot 从下一次 `instructions` 起按它渲染规则；只有上一次写入语言的请求按时间、再按请求号排序不在它之后时才写入，否则回信多一行 `box/lang stays <lang>`。每个请求号只执行一次：执行前先记录在 `.git/agent-mesh/requests/` 下，结果也保存在那里，同一请求号的重复消息再次得到同样的回信，同一请求号配上不同的值会被拒绝。中途被打断的执行在下次处理这条消息时从头再执行；每一步都可以安全重复（更新从不让 clone 后退）。按请求号里的时间生成于 30 天以前的请求被拒绝，回信里是 `STOP:` 行，这类请求的记录会被删除，所以删掉的记录不会让它的请求再执行一次。回信经 `rn-cloud.sh send` 发回，`reply_to` 指向请求所在的消息：

```
agent-mesh-result request <request>
<这一步的输出>
agent-mesh-result lang <lang>
agent-mesh-result stub <N>
agent-mesh-result DONE <tag|add-bot|stub|rename|history>
```

`lang` 一行是请求之后云端的语言。只有这条消息在该 Bot 运行 `instructions` 之后 15 分钟内被领取时才有 `stub` 一行，所以没有经桩加载规则的运行发出的回信里没有这一行。失败时回信里有 `STOP:` 行，没有 `lang`、`stub` 和 `DONE` 行。Mac 只根据 `DONE stub` 回信、且其中 `lang` 和 `stub` 两行与它显示的一致时，才把桩记为已确认。

**引导。**比 v0.10.0 旧的云端还没有 `instructions`，第 1 步会失败，于是桩的第 3 步用本次运行的消息 id 运行一条固定命令。它在 `.git/agent-mesh/boot/` 下建一个运行目录，对这些 id 运行 `rn-cloud.sh fetch`，保留这次运行所做领取的副本；对第一条存下的正文以 `agent-mesh update <tag>` 开头的消息，取回该发布 tag（最多 120 秒），把这个 tag 里的 `box/rn-cloud-update.sh` 经管道交给 `bash -s -- --stdin <N> <运行目录> <id>`。这个脚本拒绝在本次运行的 fetch 里不完整的 id，然后与 `request` 一样核对发件人和请求，更新云端仓库，发出回信，释放这次运行的领取，并打印 `agent-mesh-bootstrap RELOAD`；Grok Bot 随即再运行一次第 1 步，用新规则处理这次运行的消息。没有这样的消息时，命令打印 `agent-mesh-bootstrap SKIP`，不打开任何消息。引导失败后贴回的完整提示词对单条消息用 `--stdin 0 - <id>` 运行同一个脚本。

## 消息格式

示例使用 Bot 地址名 `bot`（v0.8.0 以前的默认名）。用了其他名字时，把 `bot`、`bot-cloud` 和 `bot-inbox` 依次换成 `<name>`、`<name>-cloud` 和 `<name>-inbox`。有多个 Bot 时，每个 Bot 用自己的名字，它们都用同一把密钥签名。

### 签名规则

- 密钥是 `outbound.key` 去掉首尾空白后的那个 64 位 hex **字符串**：把它的 UTF-8 字节直接当作 HMAC-SHA256 的 key，不做 hex 解码。
- 签名结果用小写 hex 表示。
- 引擎会去掉消息的首尾空白，所以发送端在签名前要先去掉正文末尾的空白。
- 正文里不要出现 `<reason-message` 这个字样。
- 协议字段和测试向量在所有语言中保持一致，下面向量里的中文也必须原样保留。
- 下面所有测试样例使用同一个 key：`0123456789abcdef` 重复 4 次。

### RN-OUT v1

`bot-cloud` 发给 `bot` 的代发信封：

```text
RN-OUT v1
to: <收件地址>
reply_to: <被回复的消息 id，可为空>
ts: <Unix 秒>
nonce: <随机 hex，建议 12 字节；带 reply_to 时按下文派生>
sig: <hex>
---
<正文>
```

relay 只代发到合规地址 `<person>/<session>[@<machine>]`：每一段由小写字母、数字和短横线组成，首字符是字母或数字。非空的 `reply_to` 必须是普通消息 id：由字母、数字、`.`、`_` 和 `-` 组成，首字符是字母或数字，最长 120 个字符。其他值在发送前被拒，并发出提醒。

签名：`sig = HMAC(key, "v1\n" + to + "\n" + reply_to + "\n" + ts + "\n" + nonce + "\n" + body)`。接收端以第一个 `\n---\n` 为界分开头部和正文。

测试样例：

- to=`demo/claude-session`，reply_to=`01JTESTREPLYTO0000000000`，ts=`1700000000`，nonce=`0123456789abcdef01234567`
- body 为 `hello` 换行 `第二行`
- 期望 sig=`652fbb03f1fd8626fd4f5db689b5983bdd57c797d5ba1085c340aaf699003088`

`reply_to` 非空时，`rn-cloud.sh send` 不再随机生成 nonce，而是派生：`nonce = HMAC(key, "idem1\n" + to + "\n" + reply_to + "\n" + body)` 取前 24 位 hex，body 与签名用的一致（已去掉末尾空白）。同一个回复文件重发，nonce 相同、`ts` 不同。不带 `reply_to` 的消息仍用随机 nonce，因为两条内容相同的主动消息可能是有意的。

派生 nonce 测试样例：

- to=`demo/claude-session`，reply_to=`01JTESTREPLYTO0000000000`，body 为 `hello` 换行 `第二行`
- 期望 nonce=`21dfab75a1eabc427d7655db`

relay 为每个代发过的 nonce 保存一条发送记录：调用引擎之前先落盘，保留 `NONCE_KEEP_SECONDS`（默认 86400，即 24 小时）。已发出的 nonce 再次到达时静默丢弃。发送结果未知的 nonce（引擎超时、回答 `held` 或没有明确结果，或 relay 在发送过程中停止）再次到达时不代发，relay 提醒一次；Bot 应向收件方确认，确需再发就改动内容。引擎明确拒发的 nonce，以及 relay 在发送前拒绝的 nonce（过期、超出每小时上限、群组或自身地址、地址不合规），可以重发；地址不合规时改正地址之前每次都会被拒，用同一文件重发没有用。这些拒发、失败和结果未知都以 relay 提醒的形式通知 routine。同一 nonce 后来再试、结果变为未知时，relay 撤回该 nonce 先前的拒发或失败提醒，结果未知的提醒开头会注明它取代了先前的报告。

去重只是兜底，消息仍在排队时 Bot 依然不应重发。去重生效需要同时满足：relay 和云端脚本都是本版本或更新，回复带 `reply_to`，重发内容逐字节相同（同一个回复文件），距第一次代发不超过 `NONCE_KEEP_SECONDS`。不满足时：

| 情况 | 结果 |
|---|---|
| 原信封 15 分钟内到达并代发，之后重发到达 | 重发被静默丢弃 |
| 重发先到并代发，原信封后到（新鲜或已过期） | 原信封被静默丢弃 |
| 原信封滞留超过 15 分钟 | 原信封因时效被拒（提醒）；没有记录，重发正常代发 |
| relay 明确拒发（引擎拒绝、超出限额、群组地址） | 提醒；重发正常处理 |
| relay 无法确定是否发出 | 提醒一次；重发不代发，改动内容后可发 |
| 同一 `reply_to` 的两次不同回复 | nonce 不同，都会代发 |
| 主动联系（无 `reply_to`） | 随机 nonce，都会代发 |
| 超过 `NONCE_KEEP_SECONDS` 后重发 | 当作新消息代发 |
| 新云端脚本 + 旧 relay | 1 小时内的重发被当作重放拒收并通知（多一次叫醒，不重复投递）；超过 1 小时会重复投递 |
| 旧云端脚本 + 新 relay | 随机 nonce，与以前相同 |
| relay 升级前 1 小时内记下的 nonce，升级后同 nonce 重发 | 按结果未知处理：不代发，提醒一次；改动内容后可发 |
| 引擎回答 `held`，或 relay 无法判断其结果 | 结果未知：提醒一次，同 nonce 重发不代发 |
| 新 routine 提示词 + 旧云端脚本 | 提示词不要求超时后重发，不比以前差 |

### RN-FWD v1

relay 发给 `bot-inbox` 的正文：

```text
RN-FWD v1
id: <消息 id>
from: <发件地址>
agent: <发件会话的 agent 类型，如 claude-code>
thread: <线程 id，可为空>
text_len: <正文字符数>
text_sha256: <正文的 sha256>
ts: <Unix 秒>
sig: <hex>
caps: got1
---
<原文>
--- RN-FWD END
```

签名：`sig = HMAC(key, "fwd1\n" + id + "\n" + from + "\n" + agent + "\n" + thread + "\n" + text_len + "\n" + text_sha256 + "\n" + ts)`。text_len 按 Unicode 码点计数（Python `len`），不是 UTF-8 字节数或视觉上的字形数量；sha256 按 UTF-8 字节计算。

`caps` 行可选，不在签名内。relay 加上 `caps: got1`，表示它接受 RN-GOT 回执；云端把它记进该消息的 `meta.json`，只对带这一行的消息发回执。读取方忽略不认识的头部行，所以旧版 relay 发来的副本只是没有 `caps` 行。

测试样例：

- id=`01JTESTMSG0000000000000000`，from=`demo/claude-session`，agent=`claude-code`，thread 为空，ts=`1700000000`
- text 为 `你好` 换行 ` x `
- 期望 sig=`a48eb3bbb089a3d6b31fed070d76063bb3b3ddabfac08b7651f4093ce358c576`

### RN-REFWD v1

`bot-cloud` 发给 `bot` 的补寄请求，没有正文：

```text
RN-REFWD v1
id: <要补寄的消息 id>
ts: <Unix 秒>
nonce: <随机 hex>
sig: <hex>
```

签名：`sig = HMAC(key, "refwd1\n" + id + "\n" + ts + "\n" + nonce)`。relay 只看第一行就判断一条消息是不是补寄请求。

除非设了 `RN_GOT=0`，云端会在 `sig:` 之后加一行可选、不签名的 `caps: got1`。带这一行时，relay 只重发副本，因为云端这次 fetch 本身就在等它；不带时（旧版云端或 `RN_GOT=0`），relay 还会排一个带 `refwd` 的门铃。

测试样例：

- id=`01JTESTMSG0000000000000000`，ts=`1700000000`，nonce=`0123456789abcdef01234567`
- 期望 sig=`eb9e0a80d3126e9f8f026e584927e864871d390c39b5dec8cc02a7a6e2922668`

### RN-REFWD-ACK v1

relay 回给 `bot-inbox` 的回执：

```text
RN-REFWD-ACK v1
id: <消息 id>
status: <resent | no-copy | limit | failed>
attempt: <第几次补寄>
reason: <原因，可为空>
ts: <Unix 秒>
sig: <hex>
```

签名：`sig = HMAC(key, "refwdack1\n" + id + "\n" + status + "\n" + attempt + "\n" + ts)`。

测试样例：

- id 同上，status=`resent`，attempt=`1`，ts=`1700000000`
- 期望 sig=`faa3cd29c693eb142c3f3bbb38fa0be8c46bcea4869f90ce9842aa41aa83f6ea`

### RN-GOT v1

云端发给 `bot` 的回执，不带 `--from` 发送（即以 `<team-name>/terminal` 会话身份；旧版引擎可能给它加编号，例如 `terminal-3`，新版引擎在收信端显示为 `terminal@<CLOUD_MACHINE>`）；relay 接受来自 `terminal`、`terminal-<n>`、`terminal@<CLOUD_MACHINE>`（仅限配置的云端机器名）、`bot-cloud` 和 `bot-inbox` 的回执：

```text
RN-GOT v1
stored: <逗号分隔的消息 id，无空格，可为空>
done: <逗号分隔的消息 id，无空格，可为空>
ts: <Unix 秒>
nonce: <随机 hex，建议 12 字节>
sig: <hex>
```

签名：`sig = HMAC(key, "got1\n" + stored + "\n" + done + "\n" + ts + "\n" + nonce)`；空列表在签名串里是空字符串。每个 id 匹配 `[A-Za-z0-9._-]{1,120}`。两个列表合计最多 100 个 id（更多时拆成多条），至少一个列表非空；同一 id 同时出现在两个列表时按已处理算。时效（15 分钟）和防重放同 RN-REFWD。`stored` 列出 `fetch` 已验签落盘、已交给某一轮或报告为 `uncertain` 的消息；`done` 列出已处理的消息。云端只报告其 RN-FWD 带 `caps: got1` 的 id，以及 relay 提醒批次（`relay-alerts-…`），后者由 `rn-cloud.sh done` 确认。

测试样例：

- stored=`01JTESTMSG0000000000000000`，done=`01JTESTMSG0000000000000001`，ts=`1700000000`，nonce=`0123456789abcdef01234567`
- 期望 sig=`26666d313c6c3dae158d5f8b3a6228f2be554c6ec58401662647f51aca361518`

### 门铃

relay POST 给 routine 的 JSON（值有省略）：

```text
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

`messages[]` 的每一项还可能带三个可选字段：`refwd`（这是第几次补寄）、`rering`（消息一直未确认时，relay 自己第几次补按）和 `forward_failed`（转发或重发副本失败的原因）。只有云端的补寄请求没有声明 `caps: got1` 时才会出现带 `refwd` 的门铃。relay 自己发出的系统通知则是 `doorbell:false`，这时 `messages[]` 的每一项里直接带 `text`。relay 提醒以一个 id 以 `relay-alerts-` 开头的通知项送达；routine 用 `rn-cloud.sh done <id>` 确认，确认之前 relay 会重发。
