# 协议参考

[English](PROTOCOL.md) | **简体中文**

[agent-mesh 使用指南](README.zh-CN.md)的参考部分：丢失的信怎么补回、一条消息怎么只交给一轮处理，以及带签名的消息格式和测试向量。概览见[怎么工作](README.zh-CN.md#怎么工作)；`[LOCAL]`、`[BOT-CLOUD]`、`[GROK-UI]` 的含义见[执行位置标识](README.zh-CN.md#执行位置标识)。

## 补寄与回执

relay 为每条转发过的正文保留 7 天副本。云端 `fetch` 取不到正文时，会自动发出一条带签名的重投请求，relay 按副本重发，每条消息最多重发 2 次。第一次转发就失败的消息，也靠这条路补回来。云端还会给 relay 发带签名的回执（RN-GOT），说明哪些消息已落盘、哪些已处理；带 `reply_to` 的回信也算已处理。消息在 relay 上一直记为待办，直到确认处理完。门铃送达后 `GOT_TIMEOUT_SECONDS`（默认 15 分钟）仍没有落盘回执，relay 重发正文并补按门铃；已落盘但 `GOT_DONE_SECONDS`（默认 2 小时）后仍未处理，只补按门铃，等上一轮的领取过期后由云端重新交出。每条消息最多补按 `GOT_MAX_RERINGS`（2）次，不占重投次数；之后 relay 给 Grok Bot 发一条点名这条消息的提醒，Bot 确认之前会重复发送。`GOT_TIMEOUT_SECONDS=0` 对新来信关闭补按。已知限制：

- 云端停发回执（云端脚本是旧版或设了 `RN_GOT=0`）时，relay 多补按一次后察觉，发一条提醒，并在收到下一条回执之前不再盯新来信。这期间，某一轮领取了新来信、还没处理就中断的情况，只能靠孤儿扫描或发件人重发补回。当时已有的待办仍会按期补按，直到处理完或逐条提醒。
- 回执关闭期间，只有回信能证明消息已处理，只记了 `done` 的消息会以一条提醒结束：属于误报，不丢信。
- 一条提醒送达 `GOT_MAX_RERINGS + 1` 次都没有被确认时删除，只留在 relay 日志里（`ALERT` 行）。
- 待发提醒超过 200 条时只推送条数，逐条内容在 relay 日志里。
- 补按次数用尽后放弃并逐条提醒，不会无限重试。
- relay 记住已处理的 id `FWD_COPY_DAYS` + 1 天（默认 8 天）；之后同一 id 再次送达，会再转发一次。
- 启动对账（引擎已标为已读、relay 还没处理的消息）和云端的孤儿扫描都按发件人的时间戳挑选消息。发件人时钟偏慢时，消息可能落在窗口之外（relay 的窗口从它在这个版本上首次启动时算起，或往前 7 天，取较晚者；孤儿扫描覆盖 `RN_ORPHAN_DAYS`，默认 7 天）。这样的消息只有对方重发才会回来。
- relay 读不了引擎数据库时，跳过启动对账，记日志，每个 relay 进程给 Grok Bot 发一条提醒；relay 停止前刚被标为已读的消息可能因此没有转发。

## 领取与中断

两次唤醒可能重叠，webhook 重试也可能为同一个 id 按两次门铃。`fetch` 交出的每条正文都会被领取一小时（`RN_CLAIM_LEASE`），重叠的另一轮拿到 `claimed` 就跳过；处理完的消息报告 `handled`。Bot 动手之前先运行 `rn-cloud.sh working <id>`，`rn-cloud.sh send` 在交给引擎之前记下"开始回复"。一轮在这两步之后中断、没有记完成的，之后的轮次报告 `uncertain`：Bot 查 thread 笔记或问用户，不会自己再做一次。每次 `fetch` 还会交出最近 7 天内没有门铃要过、也没人处理的已核对正文，例如门铃丢失的信（`"orphan": true`；天数由 `RN_ORPHAN_DAYS` 设置，`RN_ORPHANS=0` 关闭）。这不保证"恰好一次"：在 `working` 之前中断的消息，领取过期后会被再次交出；在那之后中断的，等用户决定。

已知限制：

- 引入领取之前就有的消息文件夹，在升级后的第一次运行时标为 `legacy`：孤儿扫描跳过它们，只有门铃点名时才交出。
- 核对不通过的消息（`mismatch`）在没有门铃点名的扫描里（孤儿扫描，以及不带 id 的 `wait`/`fetch`）只报一次。点名要的 id 和诊断用的 `scan` 每次都报，所以一直存在的 id 冲突会在每次补按时再报。
- 扫描先记下坏消息再输出，两步之间中断的那一轮会丢掉这一条提示。消息仍在引擎数据库里，诊断用的 `scan` 仍能看到。
- 升级步骤不记录已经存在的坏消息，所以升级后的第一次扫描会把它们各报一次。

## 消息格式

示例使用默认的 Bot 地址名。用了其他名字时，把 `bot`、`bot-cloud` 和 `bot-inbox` 依次换成 `<name>`、`<name>-cloud` 和 `<name>-inbox`。

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
| 新云端脚本 + 旧 relay | 1 小时内的重发被当作重放拒收并通知（多一次唤醒，不重复投递）；超过 1 小时会重复投递 |
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

`bot-cloud` 发给 `bot` 的重投请求，没有正文：

```text
RN-REFWD v1
id: <要补寄的消息 id>
ts: <Unix 秒>
nonce: <随机 hex>
sig: <hex>
```

签名：`sig = HMAC(key, "refwd1\n" + id + "\n" + ts + "\n" + nonce)`。relay 只看第一行就判断一条消息是不是重投请求。

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
attempt: <第几次重投>
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

`messages[]` 的每一项还可能带三个可选字段：`refwd`（这是第几次重投）、`rering`（消息一直未确认时，relay 自己第几次补按）和 `forward_failed`（转发或重发副本失败的原因）。只有云端的重投请求没有声明 `caps: got1` 时才会出现带 `refwd` 的门铃。relay 自己发出的系统通知则是 `doorbell:false`，这时 `messages[]` 的每一项里直接带 `text`。relay 提醒以一个 id 以 `relay-alerts-` 开头的通知项送达；routine 用 `rn-cloud.sh done <id>` 确认，确认之前 relay 会重发。
