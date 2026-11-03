# 文档维护说明

[English](CONTRIBUTING.md) | **简体中文**

## 共用一套用户指南

公开发行目录可独立使用。所有链接使用发行目录内部的相对路径，保证单独检出时也能正常阅读。

英文沿用原文件名（如 `README.md`、`AGENT-INSTALL.md`），简体中文放在旁边的 `.zh-CN.md` 文件。两种语言覆盖相同的安装步骤、限制、恢复行为和协议。改变行为时，在同一次修改中更新两种语言。英文是默认入口，技术行为以实现为准。

## 发布版本

`VERSION` 记录 agent-mesh 项目版本（`major.minor.patch`），可用 `./install.sh --version` 查询。Reason Network v0.10.7 是独立固定的依赖版本。发布时一并更新 `VERSION`、两份 README 中的版本标识、`llms.txt` 和两份更新日志，记录兼容性变化及升级说明。文档检查会核对 README 和更新日志版本。在公开发布提交上创建 `v<VERSION>` 格式的附注 tag，保留已有 tag。

## 翻译规则

- 执行位置标识在所有语言中固定为 `[LOCAL]`（用户本地电脑/Mac）、`[BOT-CLOUD]`（Bot 云电脑）和 `[GROK-UI]`（用户操作的 Grok Bot 界面）。每段操作明确负责人和执行位置；同一端的连续命令可以统一标注范围。可单独复制的提示词必须在代码块内部定义这些标识；跨端复制必须写清源端和目标端。标识是说明，不是 shell 命令或协议字段。
- 每份文档顶部放语言切换。有对应翻译时，链接保持读者所选语言。可复制的 AGENTS 模板把导航放进 HTML 注释，避免成为执行指令。
- 翻译解释、提示词和对话示例；保留命令名、参数、路径、配置项、协议标记、状态码、版本号和密码学测试向量。
- 消息正文支持多种语言，但团队/person 标识符仍用 ASCII。`text_len` 按 Python 的 Unicode 码点数计数，哈希按 UTF-8 字节计算。已签名文本不能翻译或归一化。
- 两份提示词文档各自只放一个未标语言或标为 `text` 的围栏代码块。安装器直接读取该块，shell 里不再保存另一份提示词。云端模板的 `<repo-url>`、`<team-name>`、`<mac-home>`、`<invite-link>` 在各语言中保持一致。邀请单独打印，由接收的 agent 填入。
- 增加语言时，先补齐成对指南、导航、渲染器支持、安装器校验和测试，再声明支持。未知语言代码明确报错。
- 诊断输出和协议字段暂时保持英文。`--lang` 选择提示词语言，不翻译 Reason Network 引擎或 CLI 诊断。

## 离线检查

以下离线检查可在开发电脑或 CI 上运行，不需要连接 `[BOT-CLOUD]` 或操作 `[GROK-UI]`。本仓库的 CI（[`.github/workflows/ci.yml`](.github/workflows/ci.yml)）在推送到 `main` 和每个 pull request 时，于 Linux 和 macOS 上运行同一组检查。在公开发行目录根部运行：

```bash
python3 scripts/check_docs.py
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 test_relay.py
python3 box/test_rn_fetch.py
node dsh/reason-network-bridge.test.mjs
for script in install.sh check.sh uninstall.sh dsh/mac-install.sh box/rn-cloud-setup.sh box/rn-cloud.sh; do bash -n "$script" || exit; done
```

dsh 测试也可以用[插件指南](dsh/README.zh-CN.md)中的 App 自带运行时。安装器测试使用临时 home 和模拟的系统、网络、服务命令，不安装软件或接触真实服务。文档检查覆盖语言配对、相对链接和锚点、提示词占位符和协议向量；不能代替人工核对翻译含义。

只预览提示词，不安装、不读取凭证、不创建邀请、不联网：

```bash
python3 scripts/render_prompt.py routine --lang en
python3 scripts/render_prompt.py routine --lang zh-CN
python3 scripts/render_prompt.py cloud-setup --lang zh-CN --team-name demo --mac-home /Users/example --repo-url https://example.invalid/agent-mesh.git
```
