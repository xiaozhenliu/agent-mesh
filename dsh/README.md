# reason-network-bridge（dsh 插件）

这个插件让 DeepSeek Harness（dsh）的会话像 Claude Code、Codex、pi 一样，能即时收到 Reason Network 的消息。它是一个 dsh 主机插件，没有任何包依赖，需要的 dsh 模块在运行时直接从 App 安装包里解析。

为什么需要它：Reason Network 自带的 `setup` 只给 dsh 接了一个 MCP，所有会话共用同一个地址，而且消息只有会话自己去查收件箱才看得到。这个插件补上了两样东西：每个会话一个独立地址，以及自动唤醒。

## 做什么
- 给每个顶层 dsh 会话（子 agent 跳过）单独挂一个 Reason Network MCP，环境变量 `REASON_NETWORK_SESSION=<dsh 会话 id>`。这样每个会话都有自己的地址，格式是文件夹名加 `-dsh`，可以用 `reason-network who` 查看。
- 会话空闲时：刚变空闲的那一刻以及之后每 5 秒，跑一次 `reason-network hook dsh poll`；有新消息就用 `followup` 开一轮。
- 会话忙时：在一轮快结束的时候跑 `hook dsh post-tool-use`，只取那些标记为需要打断的消息，用 `steer` 插进当前这一轮。
- 会话被归档后就停止收信，它的 MCP 被卸掉，地址也让出来。期间的消息留在 Reason Network 里保持未读，同一个文件夹下一个新会话接到这个地址时会一并收到。取消归档后恢复收信。
- 只投递发件人团队名在 `allowPeople` 名单里的消息，而且要完全相等才算。没配 `allowPeople` 的话插件会拒绝加载。不在名单里的消息不会注入会话，只在日志里记下 id、发件人和正文前 200 字。
- 日志写在 `~/.dsh/logs/reason-network-bridge.log`，权限 600，超过 1 MB 自动轮转。

## 安装（macOS 桌面版）
先按主 README 装好 Mac 侧（`./install.sh`），然后运行：

```
dsh/mac-install.sh <team-name>
```

脚本会依次做四件事：用 App 自带的运行时跑一遍单测；把 `~/.dsh/cordis.patch.yml` 备份到 `~/.dsh/backup-reason-network-bridge/`；把插件拷到 `~/.dsh/plugins/`；最后把 patch 里 Reason Network 的 MCP 块换成下面这一块。任何一步失败都会自动把 patch 恢复原样。装完后要**完全退出 dsh（⌘Q）再重新打开**，因为桌面版不会热加载。

```yaml
- insert:
    - id: reason-network-bridge
      name: '/Users/<用户>/.dsh/plugins/reason-network-bridge.mjs'
      config:
        serverName: reason-network
        command: '/Users/<用户>/.local/bin/reason-network'
        allowPeople:
          - '<team-name>'
```

这一块里写了 `serverName: reason-network` 和 `command`，所以 Reason Network 的 `setup` 和 `upgrade` 会认为 dsh 的 MCP 已经接好，不会再把原来的块插回去；`doctor` 把 dsh 列为 skipped 也是预期行为。可选配置项有 `pollMs`（默认 5000）、`hookTimeoutMs`、`toolCallTimeoutMs`（默认 660000）、`mountTimeoutMs`、`logFile`。

## 验证
- 运行 `reason-network who`，每个 dsh 会话应各占一行，形如 `<目录>-dsh  dsh … turn`。显示 `turn` 是因为引擎不认识 dsh 这种 agent，实际上会话空闲时 5 秒内就会处理消息。
- 在终端发 `reason-network send <team-name>/<目录>-dsh "自测：请回复收到"`。这封信会作为一条 user 消息出现在 dsh 会话里，末尾多一行 `From <team-name>… Reply if useful with …`，然后 dsh 会自己回复。
- 看日志：每个会话挂载时记一条 `attached session=…`；收到消息时记 `followup …` 或 `steer …`；被名单过滤掉的记成 `dropped …`。

## 已知限制
- 同一个文件夹开多个会话时，后开的会叫 `<目录>-dsh-2`，发给 `<目录>-dsh` 的信只会进第一个会话。
- 工具调用进行到一半时不会投递，要等这一轮快结束或者会话空闲。
- 改了插件文件之后，要完全退出 App 再打开才生效。
- 外部消息会驱动 dsh 跑一轮，所以 `allowPeople` 里只放你自己的团队名。dsh 自己的权限和沙箱设置照常生效。

## 回滚
把 `~/.dsh/backup-reason-network-bridge/` 里最新的那份备份拷回 `~/.dsh/cordis.patch.yml`，然后完全退出并重开 App。如果想恢复 Reason Network 官方的接法，再跑一次 `reason-network setup`。

## 文件
- `reason-network-bridge.mjs`：插件本体。
- `reason-network-bridge.test.mjs`：单测。`mac-install.sh` 安装前会先跑它；也可以手动跑：`ELECTRON_RUN_AS_NODE=1 "/Applications/DeepSeek Harness.app/Contents/MacOS/DeepSeek Harness" dsh/reason-network-bridge.test.mjs`。
- `mac-install.sh`：安装脚本。
