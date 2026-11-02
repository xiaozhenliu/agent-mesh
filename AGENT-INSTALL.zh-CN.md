# agent-mesh：给安装 agent 的操作手册

[English](AGENT-INSTALL.md) | **简体中文**

你是在用户 Mac 上运行的 coding agent（如 Claude Code、Codex）。用户希望把本地会话和云端 Grok Bot 接入同一个 Reason Network 私有团队，实现双向唤醒。按本手册执行；原理和参考见[中文指南](README.zh-CN.md)或[英文指南](README.md)。

使用用户的语言交流，简短汇报。从公开发行版根目录运行命令。按用户偏好选择 `--lang en` 或 `--lang zh-CN`，每次调用安装器都传入；下例使用中文。语言参数控制生成的提示词，终端诊断仍为英文。

## 基本规则

- 步骤标明 **agent** 或 **用户**。用户步骤绝不自己代做，agent 步骤绝不让用户去做。
- 不读取、打印或粘贴 `~/.config/agent-relay/env`、`~/.config/agent-relay/outbound.key` 或 Webhook key。可以检查文件是否存在。
- `install.sh` 在非交互终端里会在需要用户操作的位置停下并打印操作说明，把说明原样交给用户。
- 每个阶段要求时运行 `./check.sh`，原样展示结果。`FAIL` 行给出修复办法，照做，不要即兴发挥。
- 不安装额外软件，除第 0 阶段的 PATH 外不改 shell 配置。其他 agent 的配置由 `reason-network setup` 在用户确认计划后修改。
- 不是 macOS 就停止：本安装流程只在 macOS 上测试过。

## 第 0 阶段：准备（agent）

1. `uname -s` 应输出 `Darwin`；`python3 --version` 应成功，否则使用 `xcode-select --install`。
2. 运行 `curl -sS -o /dev/null -m 10 -w '%{http_code}\n' https://aps1-1.relay.n0.iroh.link/`，应输出 `200`。失败时先让用户关掉 VPN/代理重试；若恢复，给 `iroh.link` 及所有子域添加直连规则（TUN、虚拟网卡、增强或全局模式也需要），并排除 HTTPS 解密/MITM。用 `curl -sv -o /dev/null https://aps1-1.relay.n0.iroh.link/ 2>&1 | grep issuer` 查看，应是公共 CA（目前是 Let's Encrypt）而非代理自己的 CA。引擎可能使用多台中继，规则应覆盖整个域名。关闭代理后仍失败则排查 DNS、防火墙和网络。返回 200 后再继续。
3. 如果 `$HOME/.local/bin` 不在 PATH，把 `export PATH="$HOME/.local/bin:$PATH"` 追加到 `~/.zshrc` 并告知用户。当前 shell 不会重新读取该文件，因此后续命令都加上 `export PATH="$HOME/.local/bin:$PATH";` 前缀；脚本本身使用绝对路径。
4. 一次向用户确认：使用 Claude Code、Codex、pi、DeepSeek Harness 中的哪些（其他工具不在本手册支持范围）；团队名（同时是 person 名，1–40 个 ASCII 小写字母、数字或 `-`，首尾不能是 `-`，不用真名，建好不能直接改）；Grok Bot 能否创建 webhook routine 并使用 CopyToBox（都必须支持）；Mac 是否会保持唤醒（接电防睡眠，合盖会让整套停止）。

## 第 1 阶段：Mac 侧（agent）

1. 进入公开发行目录，运行 `./install.sh --name <team-name> --lang zh-CN`。它安装引擎、建团队、打印 `reason-network setup --dry-run` 计划；非交互终端不能确认，所以以退出码 3 停下。
2. 展示计划并请求用户确认。确认后运行 `./install.sh --name <team-name> --yes --lang zh-CN`。它应用接入配置、生成签名密钥、打印 routine 提示词，并在 webhook 步骤以退出码 0 停下。
3. 把分隔线之间的提示词原样交给用户，说明：「在 Grok Bot 创建由 webhook 触发的 routine，把提示词贴进去。面板会显示 Webhook URL 和 key。请在你自己的终端进入这个目录，运行 `./install.sh --webhook --lang zh-CN`，把两项粘贴进去；出现 `relay up` 后告诉我。」
4. 等用户完成，再运行 `./check.sh`：relay 项应全部 PASS，cloud machine 可以暂时 SKIP。

## 第 2 阶段：云端（用户和 Grok Bot）

1. 运行 `./install.sh --cloud-prompt --lang zh-CN`。它打印邀请（Mac 在线时有效 24 小时）及填好团队名、Mac 用户名和仓库地址的云端提示词。核对仓库地址指向公开发行版，其根目录应有 `box/rn-cloud-setup.sh`；若地址缺失，或该仓库根目录没有这个脚本，先替换再交给用户。
2. 把整段输出交给用户，说明：「把它作为一条消息发给 Grok Bot。它会在云电脑克隆仓库、加入团队，并请你批准一次 CopyToBox 来复制签名密钥。它确认 cloud 已接入后告诉我。」
3. 等用户完成，再运行 `./check.sh`：`cloud machine joined` 应为 PASS。

## 第 3 阶段：会话与连通验证

1. 提醒用户关闭并重新打开第 1 阶段之前启动的 Claude Code、Codex 会话。Codex 会询问一次，选 Trust all and continue。
2. （agent）运行 `reason-network send <team-name>/bot "test 1"`。预期 30 秒内 `~/Library/Logs/agent-relay.log` 新增 `forwarded` 和 `POST … code=200`。Grok Bot 醒来，可以回复用户会话，也可由 `reason-network listen --as terminal --wait 120` 收取。
3. （用户）请 Grok Bot 运行 `who` 并给具体会话（如 `<team-name>/<folder>-claude`）发一句话。预期空闲会话也收到；会话回复后 Grok Bot 再次被唤醒。
4. 按实际观察汇报两项结果，未观察到的测试不宣称成功。

## 可选

- dsh 自动唤醒：`dsh/mac-install.sh <team-name>`，然后用户完全退出并重开 App。见[插件指南](dsh/README.zh-CN.md)。
- 回复约定：把[中文模板](templates/AGENTS.reason-network.zh-CN.md)或[英文模板](templates/AGENTS.reason-network.md)追加到用户常用仓库的 `AGENTS.md`；Claude Code 还需在该仓库 `CLAUDE.md` 加上 `@AGENTS.md`。

## 出错时

- 云端报 `couldn't reach the machine that made the invite`：第 0 阶段第 2 步又失效了（Mac 的 VPN/代理又在拦截 `*.iroh.link`）。按第 0 阶段第 2 步修好，在 Mac 上运行 `reason-network stop`，请 Grok Bot 在邀请仍有效时用同一链接重试。
- 日志报 `webhook POST failed`：请用户运行 `./install.sh --webhook --lang zh-CN` 重新核对 URL 和 key，不读取 env 文件。
- Mac 已用另一个名字入队：不能直接改名。仅在用户明确决定后运行 `./uninstall.sh --all -y`，再从第 1 阶段重来。
- 其他问题：展示原始错误和指南中对应的排错章节。

## 卸载

`./uninstall.sh` 删除 relay；`./uninstall.sh --all` 还会退出团队并删除 Reason Network。两者都保留当前代码目录。执行前询问用户。
