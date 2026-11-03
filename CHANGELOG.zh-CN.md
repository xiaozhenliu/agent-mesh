# 更新日志

[English](CHANGELOG.md) | **简体中文**

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

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
