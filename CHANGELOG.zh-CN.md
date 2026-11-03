# 更新日志

[English](CHANGELOG.md) | **简体中文**

## 未发布

- 新增 CI：每次推送到 `main` 和每个 pull request 都在 Linux 和 macOS 上运行 [CONTRIBUTING](CONTRIBUTING.zh-CN.md#离线检查) 中的离线检查，覆盖 Python 3.9 与较新版本的 Python，以及 Node 20。CI 不需要任何密钥，不安装软件，也不连接 Reason Network、Grok 或 iroh。
- 修复：relay 现在把所有 HTTP 2xx 的 webhook 响应都视为已送达，包括 201、202 和 204。此前只认 200，已被接受的批次会留在 spool 中再次 POST，多消耗一次唤醒。其他响应和网络错误仍保留批次等待重试。
- 修复（#1、#5）：一条消息出错不再连累同批的其他消息。`reason-network listen` 一返回就把整批消息标为已读；此前代发超过 60 秒超时或出现其他异常时，整批处理中断，排在后面的消息既没有转发，也不会按门铃。现在 relay 逐条处理：出错的消息记日志，并以 relay 通知告诉 routine，同批其余消息照常处理。代发超时报告为"未确认"，因为引擎可能仍已发出。对普通来信，relay 先留副本、存好门铃，再转发正文；中途被停止时门铃照样会响，云端 fetch 会请求副本。relay 启动时，以及 `listen` 超时或失败后，只读打开本机引擎的 `network.db`，找出发给自己、已标读但没处理完的消息并处理，最多回溯 `FWD_COPY_DAYS` 天，且不早于本版本首次启动的时间；仍在等待入站审批的消息不动。数据库读不了时记日志、发一条通知，然后照常运行。`listen` 超时现在按超时记日志。`state.json` 中已处理的消息 id 改为保留 `FWD_COPY_DAYS` + 1 天，不再按最近 2000 条截断，并新增 `seen_t` 和 `recv_floor` 字段；上一版 relay 仍能读取。
- 云端取信现在把每条消息只交给一轮 routine 处理，并能找回门铃丢失的消息。`rn-cloud.sh fetch` 交出的每条正文都会被领取 `RN_CLAIM_LEASE` 秒（默认 3600），并报告新状态：`handled`（已处理）、`claimed`（另一轮持有）和 `uncertain`（之前一轮开始处理或回复后没有记完成；Bot 查 thread 笔记或问用户，不自己重做）。新增 `rn-cloud.sh working <id>` 和 `rn-cloud.sh done <id>...`；`rn-cloud.sh send <to> <id>` 在交给引擎之前记下"开始回复"，看到 handed to the Mac 后记为已处理。每次 fetch 还会返回最近 `RN_ORPHAN_DAYS` 天（默认 7）内没有门铃要过、也没人处理的已核对正文，标记 `"orphan": true`（`RN_ORPHANS=0` 关闭）；这样发现的坏消息只报告一次。首次运行把已有收件目录标为 `legacy`，不会被当作孤儿报告。`rn-cloud.sh purge` 一并清理记下的坏消息，计数多出 `mismatch`。routine 提示词对所有 `complete` 结果先查 thread 笔记，动手前运行 `working`，只是确认收到时运行 `done`，并在 thread 笔记里写明处理过的 id。消息格式不变。
- 新增（#7）：云端回执与 relay 补按门铃。每次 fetch 结束，云端给 relay 发一条带签名的回执（新格式 RN-GOT v1），列出已落盘和已处理的消息；`rn-cloud.sh done` 发一条已处理回执，带 `reply_to` 的回信也算已处理。relay 把每封转发过的信记为待办，直到确认处理完。门铃送达后 `GOT_TIMEOUT_SECONDS`（默认 900）仍没有落盘回执，relay 重发正文并补按门铃；已落盘但 `GOT_DONE_SECONDS`（默认 7200）后仍未处理，只补按门铃。每条消息最多补按 `GOT_MAX_RERINGS`（默认 2）次，不占重投次数，之后给 routine 发一条点名这条消息的提醒。提醒存在 `state.json` 中、不进 spool，spool 溢出也不会丢；它以一个 id 以 `relay-alerts-` 开头的通知项送达，routine 用 `rn-cloud.sh done <id>` 确认之前会重发。两端在 RN-FWD 和 RN-REFWD 里用一行不签名的 `caps: got1` 声明这项能力，旧版云端不会发回执，旧版 relay 忽略这一行；新版云端的重投请求不再多按一个带 `refwd` 的门铃。云端停发回执时，relay 多补按一次后察觉，发一条提醒，并在收到下一条回执之前不再盯新来信。门铃项可能带 `rering`。relay 新增配置 `GOT_TIMEOUT_SECONDS`（`0` 对新来信关闭补按）、`GOT_DONE_SECONDS` 和 `GOT_MAX_RERINGS`，写在 `~/.config/agent-relay/env`，`./install.sh --webhook` 会保留它们；云端 `RN_GOT=0` 关闭回执（同时应在 relay 设 `GOT_TIMEOUT_SECONDS=0`）。relay 日志新增 `pending: N`、`alerts: N` 和 `ALERT` 行。routine 提示词把 relay 提醒转告用户并确认，每条处理过的消息都以 `send` 或 `done` 收尾，并忽略误投过来的协议控制消息。`state.json` 新增 `pending`、`alerts`、`got_last`、`caps_lost`、`got_probe` 和 `last_batch` 字段；上一版 relay 仍能读取，并丢弃这些字段。
- 新增（#8）：云端重发的回复只投递一次。带 `reply_to` 时，`rn-cloud.sh send` 改由收件地址、`reply_to` 和正文派生信封 nonce（见 [RN-OUT v1](README.zh-CN.md#rn-out-v1)），同一个回复文件重发时 nonce 相同。relay 在 `state.json` 中为每个代发过的 nonce 保存发送记录（新字段 `sends`），调用引擎之前先落盘，保留 `NONCE_KEEP_SECONDS`（默认 86400）：已发出的 nonce 再次到达时静默丢弃；结果未知（引擎超时、`held`、没有明确结果，或 relay 在发送过程中停止）的不代发，只提醒一次；明确拒发或失败的可以重发。启动时仍记为发送中的记录转为结果未知并提醒。信封过期、超出每小时上限、群组或自身地址、发送失败和结果未知，现在都以 relay 提醒而不是 spool 通知报告，spool 溢出也不会丢；签名无效的信封仍是普通通知。旧版 relay 记下的 nonce 在升级后按结果未知处理。消息 60 秒后仍在云电脑排队时，`rn-cloud.sh send` 现在提示它会在下次连上时自动发出、不要重发；routine 提示词只在 relay 提醒说回复被拒后才用同一个文件重发一次，结果未知时告诉用户。去重只是兜底，消息排队时 Bot 仍不应重发。relay 新增配置 `NONCE_KEEP_SECONDS`，写在 `~/.config/agent-relay/env`，`./install.sh --webhook` 会保留它。上一版 relay 仍能读取 `state.json`，丢弃 `sends`，并凭 `nonces` 保留一小时的防重放。

已有用户：relay、云端脚本和 routine 提示词都有变化，须按下列顺序一起更新，保证部分组件仍是旧版本时不会有门铃发出：

1. 在 `[LOCAL]` 停止 relay：`launchctl bootout "gui/$(id -u)/com.local.agent-relay"`。此后到达 Mac 的消息留在本机引擎中未读，不会按门铃。
2. 在 `[GROK-UI]` 暂停 routine，等没有运行中的任务。
3. 让 Grok Bot 在 `[BOT-CLOUD]` 更新 `/workspace/agent-mesh`（在对话中直接让 Bot 执行，不经 routine），确认 `rn-cloud.sh` 用法里出现 `done`、`working` 和 `RN_GOT`。
4. 在 `[GROK-UI]` 替换保存的 routine 提示词并恢复 routine。此时 relay 仍停止，不会有门铃。
5. 在 `[LOCAL]` 更新仓库后重新运行完整安装：`./install.sh --name <team-name> --lang <en|zh-CN>`。已配置的 webhook 不会再次询问；安装器会复制新的 `agent_relay.py` 并启动 relay 服务，它先从 `network.db` 对账，再取回停机期间到达的消息。agent 接入步骤会再次显示计划并要求确认。

relay 停止期间 Mac 收不到推送，消息只是延后。若第 3、4 步失败，用 `launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.local.agent-relay.plist` 重新启动旧 relay，即回到升级前状态。

日后要回退回执与补按（#7）时，按下列顺序操作，避免旧版 relay 收到回执：（1）在 `[LOCAL]` 的 `~/.config/agent-relay/env` 里设 `GOT_TIMEOUT_SECONDS=0`，用 `launchctl kickstart -k "gui/$(id -u)/com.local.agent-relay"` 重启 relay，保持 routine 启用，等 `~/Library/Logs/agent-relay.log` 里出现 `pending: 0` 和 `alerts: 0`（webhook 能正常送达时，最长约 `GOT_DONE_SECONDS × (GOT_MAX_RERINGS + 1)`）；（2）在 `[GROK-UI]` 暂停 routine，等没有运行中的任务；（3）让 Grok Bot 在 `[BOT-CLOUD]` 把 `/workspace/agent-mesh` 回退到上一版，并检查云端引擎的 outbox 为空，再等几分钟，让仍在途中的回执由新版 relay 处理完；（4）换回上一版 routine 提示词并恢复 routine；（5）最后回退 `[LOCAL]` 的 relay。

## v0.1.1 — 2026-11-03

安全加固版本。

- 新增中英文[安全策略](SECURITY.zh-CN.md)：私密漏洞上报方式、范围与上游归属、处理流程，以及怀疑签名密钥、webhook key 或邀请链接泄露时的处理步骤。README 的安全小节和文档表加入链接。
- README 的[安全与隐私](README.zh-CN.md#安全与隐私)一节重写：写明 Grok Bot 是信任根，被提示词注入时仍能发出签名有效的消息；逐一列出 Claude Code 和 Codex 的自动批准模式；说明 Reason Network v0.10.7 实际用 `codex queue` 唤醒 Codex（此前误写为 `codex exec resume`），并记录无人值守时审批的实测表现；列出正文以明文保存的位置和保留时间（包括 relay `state.json` 里等待送出的门铃预览），提醒不要经这条通道发送机密；要求核对团队成员。
- 新增 `./install.sh --rotate-key`：生成新的签名密钥、重启 relay，并打印把新密钥复制到云端的 CopyToBox 步骤。relay 配置改从别处读取密钥（`OUTBOUND_KEY` 或其他 `OUTBOUND_KEY_FILE`）时拒绝执行，密钥保持不变。README 和安全策略说明：换钥前已转发、云端未取回的消息换钥后报 `mismatch`，需要发送方重发。安全策略里的手动命令改为这条命令。
- 新增清理命令：`[LOCAL]` 的 `./install.sh --purge-copies` 删除 relay 保留的正文副本，目录是符号链接时拒绝执行；`[BOT-CLOUD]` 的 `rn-cloud.sh purge <天数>` 先停云端引擎，再删除早于该天数的已收消息、收件目录、误投记录和 thread 笔记；`0` 表示全部。同一消息的重投按最新一份计算，留下的副本与收件目录保持一致，删掉的消息也不会被当成新消息再次取回。清理先取得收件锁再停引擎，在同一个写事务里选出并删除消息；删除收件目录前再核对一次，清理期间新到的重投不会丢失目录。正在取信时清理会等待，目录是符号链接或引擎仍在运行时拒绝执行。云端取信与清理共用一把锁，避免两者同时改动收件目录。
- `./check.sh` 新增团队成员检查，出现本机和 `cloud` 以外的成员，或出现多个 `cloud` 时报 FAIL。
- `./install.sh --webhook` 重写配置时保留用户设置的可选项，例如 `DOORBELL_PREVIEW_CHARS=0`，不再悄悄恢复默认值。
- 回复约定模板增加安全边界：Bot 来信按外部输入处理；难以撤回或对外的操作先问用户；不在消息里写机密；发现陌生成员时告诉用户。

已有用户：relay 程序 `agent_relay.py` 没有变化，在 `[LOCAL]` 更新仓库就能使用新命令，不需要重装。要用云端清理命令和取信锁，需要让 Grok Bot 在 `[BOT-CLOUD]` 的 `/workspace/agent-mesh` 更新仓库。已经追加到你仓库 `AGENTS.md` 的回复约定不会自动更新，需要重新复制模板。routine 提示词没有变化。

## v0.1.0 — 2026-11-02

agent-mesh 首次带 tag 的发布；此前的公开提交仍保留在仓库历史中。

- 通过私有 Reason Network 团队连接用户本地的 Claude Code、Codex、pi、DeepSeek Harness 会话和 Grok Bot，提供消息签名、webhook 唤醒、完整正文获取与重投。
- 提供中英文配套指南和提示词，用 `[LOCAL]`、`[BOT-CLOUD]`、`[GROK-UI]` 标明命令和文件位置，包括密钥复制的源端与目标端。
- 使用 Mac 实际 home 目录和检测到的 Python，生成正确转义的 launchd 配置。支持 Apple Silicon 和 Intel Mac，在用户或系统 Applications 目录查找 dsh，也支持通过 `DSH_APP` 指定。
- 云端默认使用公开 HTTPS 克隆地址，提供显式 `--repo-url` 参数。继续安装前检查已有安装、依赖、系统与 CPU；引擎版本不兼容、PATH 存在其他安装或使用自定义本地状态目录时停止。
- 明确 agent 安装与登录、Git、Python 3.9+ 及 `ssl`/`sqlite3`、网络、云端工具和持久目录要求。支持重复安装 dsh 插件，并保留唯一命名的配置备份。
- 增加隔离安装器测试，覆盖不同 home、CPU、仓库地址、缺少依赖和已有部署。新增 `VERSION` 与离线查询命令 `./install.sh --version`。

兼容范围：本地安装需要 macOS 桌面登录；Bot 云电脑需要 Linux x86_64 或 ARM64，并使用持久目录 `/workspace/agent-mesh/`。Reason Network 仍固定为 **v0.10.7**，其版本与 agent-mesh 版本独立。

已有用户：重新运行安装器前先检查准备条件和已有部署。Bot 云电脑上的仓库与已保存的 routine 提示词需要分别更新；修改 Mac 文件不会自动更新它们。本次发布本身不会迁移正在运行的本地部署。
