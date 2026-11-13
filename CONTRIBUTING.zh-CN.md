# 文档维护说明

[English](CONTRIBUTING.md) | **简体中文**

## 共用一套用户指南

公开发行目录可独立使用。所有链接使用发行目录内部的相对路径，保证单独检出时也能正常阅读。

英文沿用原文件名（如 `README.md`、`AGENT-INSTALL.md`），简体中文放在旁边的 `.zh-CN.md` 文件。两种语言覆盖相同的安装步骤、限制、恢复行为和协议。改变行为时，在同一次修改中更新两种语言。英文是默认入口，技术行为以实现为准。

## 发布版本

`VERSION` 记录 agent-mesh 项目版本（`major.minor.patch`），可用 `./install.sh --version` 查询。Reason Network v0.12.17 是独立固定的依赖版本：写在 `install.sh` 和 `box/rn-cloud-setup.sh` 的 `VERSION` 里，两个脚本各有一张相同的发布包 SHA-256 表，覆盖全部四个平台。两个脚本要一起改，`scripts/test_engine.py` 会核对二者一致。发布时一并更新 `VERSION`、两份 README 中的版本标识、`llms.txt` 和两份更新日志，记录兼容性变化及升级说明。文档检查会核对 README 和更新日志版本。在公开发布提交上创建 `v<VERSION>` 格式的附注 tag，保留已有 tag。

## 翻译规则

- 执行位置标识在所有语言中固定为 `[LOCAL]`（用户本地电脑/Mac）、`[BOT-CLOUD]`（Bot 云电脑）和 `[GROK-UI]`（用户操作的 Grok Bot 界面）。每段操作明确负责人和执行位置；同一端的连续命令可以统一标注范围。可单独复制的提示词必须在代码块内部定义这些标识；跨端复制必须写清源端和目标端。标识是说明，不是 shell 命令或协议字段。
- 每份文档顶部放语言切换。有对应翻译时，链接保持读者所选语言。可复制的 AGENTS 模板把导航放进 HTML 注释，避免成为执行指令。每份模板有一个生成块，位于 `<!-- agent-mesh:bot-address lang=<语言> -->` 和 `<!-- /agent-mesh:bot-address -->` 之间；保留这两个标记和它们之间的那一行，安装器会把这一行换成本团队的 Bot 地址。
- 翻译解释、提示词和对话示例；保留命令名、参数、路径、配置项、协议标记、状态码、版本号和密码学测试向量。
- 消息正文支持多种语言，但团队/person 标识符仍用 ASCII。`text_len` 按 Python 的 Unicode 码点数计数，哈希按 UTF-8 字节计算。已签名文本不能翻译或归一化。
- 各份提示词文档（`routine`、`cloud-setup`、`cloud-uninstall`）各自只放一个未标语言或标为 `text` 的围栏代码块。安装器和卸载器直接读取该块，shell 里不再保存另一份提示词。云端模板的 `<repo-url>`、`<team-name>`、`<relay-name>`、`<mac-home>`、`<invite-link>` 在各语言中保持一致；routine 模板只用 `<team-name>` 和 `<relay-name>`，名字为 `bot` 时渲染结果必须与以前逐字节相同（测试保留旧摘要）。邀请单独打印，由接收的 agent 填入。云端卸载模板使用 `<level>`、`<mode>`、`<team-name>`、`<mac-machine>`、`<relay-name>`、`<script-sha256>` 和 `<cloud-script>`；`<cloud-script>` 替换为填好的 `box/rn-cloud-uninstall.sh`，云端脚本只保存这一份。
- 增加语言时，先补齐成对指南、导航、渲染器支持、安装器校验和测试，再声明支持。未知语言代码明确报错。
- `--lang`（没有时依次取记录中的语言、`AGENT_MESH_LANG`）同时选择提示词和 `install.sh`、`update.sh`、`check.sh`、`uninstall.sh` 及其辅助脚本的终端文字。这些文字放在 `scripts/messages/<语言>/*.tsv`，每行 `key<TAB>text`，由 `scripts/msg.sh`（`t`/`tq`）和 `scripts/messages.py`（`t`）读取。文字是可信的格式串：转换只允许 `%s` 和 `%%`，转义只允许 `\n`、`\t`、`\\`；值只作为 `%s` 的参数传入，不拼进格式串。各语言的 key 相同，每个 key 的 `%s` 个数相同；英文输出保持原样。被机器读取的输出保持英文、不翻译：脚本读取的状态词、哈希、`agent-mesh-result`、`engine:`、`STOP:`、`cloud-setup:`、`check.sh` 的 `PASS`/`FAIL`/`WARN`/`RESULT` 前缀、确认口令、发给 Grok Bot 的命令块和消息、日志、`[LOCAL]`/`[GROK-UI]`/`[BOT-CLOUD]` 标识，以及所有云端脚本的输出。Reason Network 引擎和 CLI 诊断不翻译。
- 需要在 `[BOT-CLOUD]` 上做迁移的版本，把迁移放进 `box/rn-cloud-setup.sh --upgrade-engine` 或 `box/rn-cloud-update.sh` 的 `--after-checkout` 部分：每次云端更新都会从新 checkout 运行这两处。

## 离线检查

以下离线检查可在开发电脑或 CI 上运行，不需要连接 `[BOT-CLOUD]` 或操作 `[GROK-UI]`。本仓库的 CI（[`.github/workflows/ci.yml`](.github/workflows/ci.yml)）在推送到 `main` 和每个 pull request 时，于 Linux 和 macOS 上运行同一组检查。在公开发行目录根部运行：

```bash
python3 scripts/check_docs.py
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 test_relay.py
python3 box/test_rn_fetch.py
node dsh/reason-network-bridge.test.mjs
for script in install.sh check.sh uninstall.sh update.sh dsh/mac-install.sh dsh/mac-uninstall.sh box/rn-cloud-setup.sh box/rn-cloud.sh box/rn-cloud-update.sh box/rn-cloud-uninstall.sh; do bash -n "$script" || exit; done
```

dsh 测试也可以用[插件指南](dsh/README.zh-CN.md)中的 App 自带运行时。安装器和卸载器测试使用临时 home 和模拟的系统、网络、服务、引擎命令，不安装软件、不删除临时目录以外的东西，也不接触真实服务。文档检查覆盖语言配对、相对链接和锚点、提示词占位符、AGENTS 模板里的生成块、协议向量和消息目录（各语言 key 与 `%s` 个数一致、只用允许的转换和转义、每个 key 都被使用、被使用的 key 都存在）；不能代替人工核对翻译含义。

只预览提示词，不安装、不读取凭证、不创建邀请、不联网：

```bash
python3 scripts/render_prompt.py routine --lang en
python3 scripts/render_prompt.py routine --lang zh-CN
python3 scripts/render_prompt.py cloud-setup --lang zh-CN --team-name demo --mac-home /Users/example --repo-url https://example.invalid/agent-mesh.git
python3 scripts/render_prompt.py cloud-uninstall --lang zh-CN --team-name demo --mac-machine mac --relay-name bot --level identity --mode normal --challenge 0123456789abcdef
```
