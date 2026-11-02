# Cloud setup prompt for Grok Bot

**English** | [简体中文](cloud-setup.zh-CN.md)

The last installer step prints this prompt with the team name, Mac username, and repository URL filled in. Send it to Grok Bot together with the invite. Reprint it with `./install.sh --cloud-prompt --lang en`. The template below has unfilled placeholders; keep their names unchanged in translations.

```
Join my Reason Network team from your cloud computer, using the machine name cloud. Keep all files under the persistent directory /workspace/agent-mesh/ and do not leave any process running in the background. Reply to me in my preferred language.
1. Run git clone <repo-url> /workspace/agent-mesh. If the directory exists, run git -C /workspace/agent-mesh pull instead.
2. Run /workspace/agent-mesh/box/rn-cloud-setup.sh '<invite-link>' <team-name>. The script downloads the same reason-network version to box/bin, joins with box/state as the engine directory, and disables auto_update, lan, and tray. Use the invite supplied above this prompt.
3. Use CopyToBox to copy /Users/<mac-user>/.config/agent-relay/outbound.key from my Mac to /workspace/agent-mesh/box/outbound.key, then chmod 600 the destination. Use an absolute source path. Keep the key contents out of chat.
4. Run /workspace/agent-mesh/box/rn-cloud.sh who. You should see my local sessions. After checking, tell me that the cloud has joined.
```
