# 更新日志

[English](CHANGELOG.md) | **简体中文**

## 未发布

- 修复：relay 现在把所有 HTTP 2xx 的 webhook 响应都视为已送达，包括 201、202 和 204。此前只认 200，已被接受的批次会留在 spool 中再次 POST，多消耗一次唤醒。其他响应和网络错误仍保留批次等待重试。

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
