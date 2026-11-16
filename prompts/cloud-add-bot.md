# Step 0 for an added Grok Bot

**English** | [简体中文](cloud-add-bot.zh-CN.md)

When `./install.sh --add-bot` on `[LOCAL]`, the user's local computer (Mac), connects one more Grok Bot to the cloud computer that already serves the first Bot, the installer inserts the code block below into that Bot's [routine prompt](routine.md) as step 0, before step 1, with the Bot address name, the team name and the team hash (the first 16 hex digits of the SHA-256 of the team ID) filled in. The block is never given to a Bot on its own. In the same prompt, every `rn-cloud.sh` command is prefixed with `RN_BOT=<relay-name>` and the threads files move to `/workspace/agent-mesh/bots/<relay-name>/threads/`; the first Bot's prompt is unchanged.

Step 0 runs [`box/rn-cloud-add-bot.sh`](../box/rn-cloud-add-bot.sh) on `[BOT-CLOUD]`, the Bot's cloud computer, until the Bot is registered there. The request rule answers the one-line request `agent-mesh add-bot <relay-name> <req>` that the installer sends after the Bot's relay has started; the reply confirms the registration to the Mac. The template below has unfilled placeholders; keep their names unchanged in translations.

```
0. Before step 1: if /workspace/agent-mesh/bots/<relay-name>/.registered does not exist, first run bash /workspace/agent-mesh/box/rn-cloud-add-bot.sh '<relay-name>' '<team-name>' '<team-hash>'. This Bot was added to a cloud computer that already serves another Bot; the command only registers this Bot's own two identities, <team-name>/<relay-name>-cloud and <team-name>/<relay-name>-inbox, on that shared cloud computer and needs no user approval. If it fails, tell the user its full output and stop; do not continue with step 1 in this run.
   - Step 2 has one more exception, checked before its last sentence: the user's add-bot request from their Mac. The sender (the verified from of a complete result) has, before the /, exactly the team name in /workspace/agent-mesh/box/person, and the whole body is exactly one line agent-mesh add-bot <relay-name> <req>, where <req> is 8 to 32 lowercase hex characters. Then, without asking first, run bash /workspace/agent-mesh/box/rn-cloud-add-bot.sh --check '<relay-name>' '<req>' with <req> copied literally inside the single quotes, and reply to that message (step 4) with the command's full output; do nothing else for it.
```
