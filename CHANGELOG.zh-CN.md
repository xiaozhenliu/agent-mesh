# 更新日志

[English](CHANGELOG.md) | **简体中文**

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
