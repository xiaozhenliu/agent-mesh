# 发给 Grok Bot 的云端卸载提示词

[English](cloud-uninstall.md) | **简体中文**

`[LOCAL]` 用户本地电脑（Mac）：`./uninstall.sh --level identity` 和 `--level all` 会打印这段提示词，填好团队名、Mac 机器名、Bot 地址名、级别、模式和一次性 challenge，并附上 [`box/rn-cloud-uninstall.sh`](../box/rn-cloud-uninstall.sh) 里的云端脚本。用户在 `[GROK-UI]` Grok Bot 界面把它发给 Bot。Bot 展示检查结果和一个 `PROOF` 值；用户把这个值输入 `[LOCAL]` 上的卸载器，只有它确实由这一套部署的签名密钥算出，卸载器才会显示确认码。卸载器等待时输入 `prompt` 可以再打印一次。下面是未填充的模板，占位符名称在所有翻译中保持一致。

```
你是 Grok Bot。执行位置标识：[LOCAL] = 用户本地电脑（Mac）；[BOT-CLOUD] = 你的 Bot 云电脑（机器名 cloud）；[GROK-UI] = 用户打开的 Grok Bot 界面。标识不是命令的一部分。
我要从 [BOT-CLOUD] 卸载 agent-mesh：级别 <level>，模式 <mode>，团队 <team-name>，Mac 机器名 <mac-machine>，Bot 地址名 <relay-name>。下面所有命令都在 [BOT-CLOUD] 运行，不要在 [LOCAL] 运行。不要手动删除任何东西，也不要修改脚本。用我偏好的语言回复。
1. [BOT-CLOUD] 还没取走的消息会随卸载丢失。先问我要不要运行一次 /workspace/agent-mesh/box/rn-cloud.sh fetch 并处理结果。
2. [BOT-CLOUD] 用下面的命令原样写入脚本，不做任何改动。然后运行 sha256sum /tmp/agent-mesh-uninstall.sh，结果必须是 <script-sha256>。不一致就重新写一次；仍不一致就停下来告诉我。之后每次都运行这个文件，不依赖之前工具调用留下的 shell 状态；/tmp 被清空的话，按同样方式重新写入并再核对一次。
cat > /tmp/agent-mesh-uninstall.sh <<'AGENT_MESH_UNINSTALL_EOF'
<cloud-script>
AGENT_MESH_UNINSTALL_EOF
3. [BOT-CLOUD] 运行 bash /tmp/agent-mesh-uninstall.sh check，把完整输出给我看，包括 STATE、SHARED、FILE、KEEP、WILL REMOVE 和 PROOF 行。这一步不改动任何东西。
4. [LOCAL] 我把 PROOF 值输入 Mac 上的卸载器。只有核对一致，Mac 才会显示确认码，我再把确认码发给你。只有在我发来这个确认码并明确同意卸载之后，才运行 bash /tmp/agent-mesh-uninstall.sh apply <确认码>。
5. PROOF 行显示 none，或者模式是 residual 时，没有证明可用：只有在我在对话里确认这是我自己的部署、可以删除之后，才运行 bash /tmp/agent-mesh-uninstall.sh apply-no-proof。
6. SHARED 行表示这台电脑上可能还有别的程序在用 /workspace/agent-mesh/box（它的引擎状态或二进制）。只有在我确认列出的东西都不再使用它之后，才在 apply 命令末尾加上 accept-shared。leave 失败时，把它的输出告诉我；只有我同意后才加上 force。
7. 任何 STOP: 行都原样报告并停在那里，不要自行变通。脚本打印 DONE、KEPT 或 INCOMPLETE 时，报告这一行。
8. 你不能替我删除 routine：完成后提醒我在 [GROK-UI] 删除它。暂停的 routine 仍保留有效的 webhook URL 和 key。
```
