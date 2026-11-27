# 文档维护说明

[English](CONTRIBUTING.md) | **简体中文**

## 共用一套用户指南

公开发行目录可独立使用。所有链接使用发行目录内部的相对路径，保证单独检出时也能正常阅读。

英文沿用原文件名（如 `README.md`、`AGENT-INSTALL.md`），简体中文放在旁边的 `.zh-CN.md` 文件。两种语言覆盖相同的安装步骤、限制、恢复行为和协议。改变行为时，在同一次修改中更新两种语言。英文是默认入口，技术行为以实现为准。

## 发布版本

`VERSION` 记录 agent-mesh 项目版本（`major.minor.patch`），可用 `./install.sh --version` 查询。Reason Network v0.12.17 是独立固定的依赖版本：写在 `install.sh` 和 `box/rn-cloud-setup.sh` 的 `VERSION` 里，两个脚本各有一张相同的发布包 SHA-256 表，覆盖全部四个平台。两个脚本要一起改，`tests/cli/test_engine.py` 会核对二者一致。发布时一并更新 `VERSION`、两份 README 中的版本标识、`llms.txt` 和两份更新日志，记录兼容性变化及升级说明。文档检查会核对 README 和更新日志版本。在公开发布提交上创建 `v<VERSION>` 格式的附注 tag，保留已有 tag。

## 翻译规则

- 执行位置标识在所有语言中固定为 `[LOCAL]`（用户本地电脑/Mac）、`[BOT-CLOUD]`（Bot 云电脑）和 `[GROK-UI]`（用户操作的 Grok Bot 界面）。每段操作明确负责人和执行位置；同一端的连续命令可以统一标注范围。可单独复制的提示词必须在代码块内部定义这些标识；跨端复制必须写清源端和目标端。标识是说明，不是 shell 命令或协议字段。
- 术语以 README 的[术语](README.zh-CN.md#术语)一节为准：中英文一一对应，正文不另起叫法。新增概念时先补这张表，两种语言一起改。
- 每份文档顶部放语言切换。有对应翻译时，链接保持读者所选语言。可复制的 AGENTS 模板把导航放进 HTML 注释，避免成为执行指令。每份模板有一个生成块，位于 `<!-- agent-mesh:bot-address lang=<语言> -->` 和 `<!-- /agent-mesh:bot-address -->` 之间；保留这两个标记和它们之间的那一行，安装器会把这一行换成本团队的 Bot 地址。
- 翻译解释、提示词和对话示例；保留命令名、参数、路径、配置项、协议标记、状态码、版本号和密码学测试向量。
- 消息正文支持多种语言，但团队/person 标识符仍用 ASCII。`text_len` 按 Python 的 Unicode 码点数计数，哈希按 UTF-8 字节计算。已签名文本不能翻译或归一化。
- 各份提示词文档（`routine`、`routine-body`、`routine-full`、`cloud-setup`、`cloud-add-bot`、`cloud-uninstall`）各自只放一个未标语言或标为 `text` 的围栏代码块。安装器和卸载器直接读取该块，shell 里不再保存另一份提示词。云端模板的 `<repo-url>`、`<team-name>`、`<relay-name>`、`<mac-home>`、`<invite-link>` 在各语言中保持一致；routine 桩（`routine`）是一段固定的英文块，两种语言相同，没有占位符；规则模板（`routine-body`）只用 `<team-name>`、`<relay-name>`、`<rn-cloud>` 和 `<threads-dir>`，由 `box/rn-routine.py` 渲染，云端和 `--full-prompt` 用的是同一个渲染器；`routine-full` 放 `--full-prompt` 接在规则后面的几步，只用 `<rn-cloud>`。邀请单独打印，由接收的 agent 填入。云端卸载模板使用 `<level>`、`<mode>`、`<team-name>`、`<mac-machine>`、`<relay-name>`、`<script-sha256>` 和 `<cloud-script>`；`<cloud-script>` 替换为填好的 `box/rn-cloud-uninstall.sh`，云端脚本只保存这一份。添加 Bot 的模板使用 `<relay-name>`、`<team-name>` 和 `<team-hash>`；添加的 Bot 的桩给每条 `rn-cloud.sh` 命令加上 `RN_BOT=<name>` 前缀，并把团队名和团队哈希传给 `instructions`；它的 `--full-prompt` 完整提示词把这个块作为第 0 步，也加同样的前缀；由添加的 Bot 执行云端卸载提示词时，也加同样的前缀。两者都由 `scripts/render_prompt.py --bot-selector` 渲染，`scripts/check_docs.py` 核对前缀覆盖每条 `rn-cloud.sh` 命令。
- 增加语言时，先补齐成对指南、导航、渲染器支持、安装器校验和测试，再声明支持。未知语言代码明确报错。
- `--lang`（没有时依次取记录中的语言、`AGENT_MESH_LANG`）同时选择提示词和 `install.sh`、`update.sh`、`check.sh`、`uninstall.sh` 及其辅助脚本的终端文字。这些文字放在 `scripts/messages/<语言>/*.tsv`，每行 `key<TAB>text`，由 `scripts/msg.sh`（`t`/`tq`）和 `scripts/messages.py`（`t`）读取。文字是可信的格式串：转换只允许 `%s` 和 `%%`，转义只允许 `\n`、`\t`、`\\`；值只作为 `%s` 的参数传入，不拼进格式串。各语言的 key 相同，每个 key 的 `%s` 个数相同；英文输出保持原样。被机器读取的输出保持英文、不翻译：脚本读取的状态词、哈希、`agent-mesh-result`、`engine:`、`STOP:`、`cloud-setup:`、`check.sh` 的 `PASS`/`FAIL`/`WARN`/`RESULT` 前缀、确认口令、发给 Grok Bot 的命令块和消息、日志、`[LOCAL]`/`[GROK-UI]`/`[BOT-CLOUD]` 标识，以及所有云端脚本的输出。Reason Network 引擎和 CLI 诊断不翻译。
- 需要在 `[BOT-CLOUD]` 上做迁移的版本，把迁移放进 `box/rn-cloud-setup.sh --upgrade-engine` 或 `box/rn-cloud-update.sh` 的 `--after-checkout` 部分：每次云端更新都会从新 checkout 运行这两处。

## 离线检查

以下离线检查可在开发电脑或 CI 上运行，不需要连接 `[BOT-CLOUD]` 或操作 `[GROK-UI]`。本仓库的 CI（[`.github/workflows/ci.yml`](.github/workflows/ci.yml)）在推送到 `main` 和每个 pull request 时，于 Linux 和 macOS 上运行同一组检查；只改用户文档（根目录的 Markdown 文件和 `dsh/README*.md`、`llms.txt`、`LICENSE`）时跳过它，由 [`.github/workflows/docs.yml`](.github/workflows/docs.yml) 在每次推送和 pull request 时运行文档检查。在公开发行目录根部运行：

```bash
python3 scripts/check_docs.py
python3 -m unittest discover -s tests/cli -t . -p 'test_*.py'
python3 -m unittest discover -s tests/rendering -t . -p 'test_*.py'
python3 -m unittest discover -s tests/relay -t . -p 'test_*.py'
python3 -m unittest discover -s tests/cloud -t . -p 'test_*.py'
node dsh/reason-network-bridge.test.mjs
bash scripts/check_shell.sh
```

`scripts/check_shell.sh` 找出所有 `*.sh` 文件，逐个运行 `bash -n`，再用 ShellCheck 检查（跟进它们 source 的文件）；`scripts/msg.sh` 按 bash 单独检查。它需要 `shellcheck`；没有安装时，`bash scripts/check_shell.sh --syntax-only` 只运行 `bash -n`，完整检查仍由 CI 的 shellcheck 作业执行。macOS 上的 CI 作业只做语法检查。

`tests/` 下的每个测试包单独一个进程运行，不支持一次发现整个 `tests/` 目录。单个模块用 `python3 -m unittest tests.<包>.<模块>`，例如 `python3 -m unittest tests.cli.test_engine`。

CI 在每种运行环境上把 CLI 测试分成四部分并行运行：`python3 -m tests.parts cli <部分>`，`<部分>` 为 `update`、`engine`、`install` 或 `other`。`tests/parts.py` 列出前三部分的模块，`other` 运行其余全部 CLI 模块，新增模块无须改动它。四部分合起来与上面 `tests/cli` 那条命令运行的测试相同，在开发电脑上也可以同时运行。

旧版 CLI 测试跑完后可能留下假引擎守护进程，每个都是一个 `python3` 进程，命令为 `<临时目录>/workspace/box/bin/engine/reason-network daemon --service`。用 `ps -axo pid,command | grep '[w]orkspace/box/bin/engine/reason-network daemon'` 列出，再用 `kill <pid>` 停掉。真实引擎是二进制文件，不由 `python3` 运行，不会匹配这个模式。现在的 CLI 测试会在假守护进程比测试活得久时报失败。

dsh 测试也可以用[插件指南](dsh/README.zh-CN.md)中的 App 自带运行时。安装器和卸载器测试使用临时 home 和模拟的系统、网络、服务、引擎命令，不安装软件、不删除临时目录以外的东西，也不接触真实服务。文档检查覆盖语言配对、相对链接和锚点、提示词占位符、AGENTS 模板里的生成块、协议向量和消息目录（各语言 key 与 `%s` 个数一致、只用允许的转换和转义、每个 key 都被使用、被使用的 key 都存在）；不能代替人工核对翻译含义。

只预览提示词，不安装、不读取凭证、不创建邀请、不联网：

```bash
python3 scripts/render_prompt.py routine --lang en
python3 scripts/render_prompt.py routine --lang zh-CN
python3 scripts/render_prompt.py cloud-setup --lang zh-CN --team-name demo --mac-home /Users/example --repo-url https://example.invalid/agent-mesh.git
python3 scripts/render_prompt.py cloud-uninstall --lang zh-CN --team-name demo --mac-machine mac --relay-name bot --level identity --mode normal --challenge 0123456789abcdef
```
