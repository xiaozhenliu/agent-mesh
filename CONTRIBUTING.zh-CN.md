# 文档维护说明

[English](CONTRIBUTING.md) | **简体中文**

## 共用一套用户指南

公开发行目录可独立使用。所有链接使用发行目录内部的相对路径，保证单独检出时也能正常阅读。

英文沿用原文件名（如 `README.md`、`AGENT-INSTALL.md`），简体中文放在旁边的 `.zh-CN.md` 文件。两种语言覆盖相同的安装步骤、限制、恢复行为和协议。改变行为时，在同一次修改中更新两种语言。英文是默认入口，技术行为以实现为准。

## 翻译规则

- 每份文档顶部放语言切换。有对应翻译时，链接保持读者所选语言。可复制的 AGENTS 模板把导航放进 HTML 注释，避免成为执行指令。
- 翻译解释、提示词和对话示例；保留命令名、参数、路径、配置项、协议标记、状态码、版本号和密码学测试向量。
- 消息正文支持多种语言，但团队/person 标识符仍用 ASCII。`text_len` 按 Python 的 Unicode 码点数计数，哈希按 UTF-8 字节计算。已签名文本不能翻译或归一化。
- 两份提示词文档各自只放一个未标语言或标为 `text` 的围栏代码块。安装器直接读取该块，shell 里不再保存另一份提示词。云端模板的 `<repo-url>`、`<team-name>`、`<mac-user>`、`<invite-link>` 在各语言中保持一致。邀请单独打印，由接收的 agent 填入。
- 增加语言时，先补齐成对指南、导航、渲染器支持、安装器校验和测试，再声明支持。未知语言代码明确报错。
- 诊断输出和协议字段暂时保持英文。`--lang` 选择提示词语言，不翻译 Reason Network 引擎或 CLI 诊断。

## 离线检查

在公开发行目录根部运行：

```bash
python3 scripts/check_docs.py
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 test_relay.py
python3 box/test_rn_fetch.py
node dsh/reason-network-bridge.test.mjs
for script in install.sh check.sh uninstall.sh; do bash -n "$script" || exit; done
```

dsh 测试也可以用[插件指南](dsh/README.zh-CN.md)中的 App 自带运行时。文档检查覆盖语言配对、相对链接和锚点、提示词占位符和协议向量；不能代替人工核对翻译含义。

只预览提示词，不安装、不读取凭证、不创建邀请、不联网：

```bash
python3 scripts/render_prompt.py routine --lang en
python3 scripts/render_prompt.py routine --lang zh-CN
python3 scripts/render_prompt.py cloud-setup --lang zh-CN --team-name demo --mac-user example --repo-url https://example.invalid/agent-mesh.git
```
