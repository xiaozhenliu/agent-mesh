# 发给 Grok Bot 的云端接入提示词

`./install.sh` 的最后一步会打印这段提示词，并把团队名、用户名和仓库地址都填好；你把它连同邀请链接一起发给 Grok Bot 即可。想再打印一次，运行 `./install.sh --cloud-prompt`。下面是未填充的模板。

```
请在你的云电脑上接入我的 Reason Network 团队，机器名用 cloud。所有文件都放在持久目录 /workspace/agent-mesh/ 下，不要留任何常驻进程。
1. 运行 git clone <仓库地址> /workspace/agent-mesh。如果目录已经存在，改为运行 git -C /workspace/agent-mesh pull。
2. 运行 /workspace/agent-mesh/box/rn-cloud-setup.sh '<上面的邀请链接>' <team-name>。这个脚本会把同版本的 reason-network 下载到 box/bin，以 box/state 作为引擎目录加入团队，并关掉 auto_update、lan、tray。
3. 用 CopyToBox 把我电脑上的 /Users/<用户>/.config/agent-relay/outbound.key 拷到 /workspace/agent-mesh/box/outbound.key，然后对它执行 chmod 600。源路径要写绝对路径。密钥内容不要出现在聊天里。
4. 运行 /workspace/agent-mesh/box/rn-cloud.sh who，应该能看到我电脑上的会话。确认后回我一句「cloud 已接入」。
```
