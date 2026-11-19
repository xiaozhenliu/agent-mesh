# Step 0 for an added Grok Bot

**English** | [简体中文](cloud-add-bot.zh-CN.md)

When `./install.sh --add-bot` on `[LOCAL]`, the user's local computer (Mac), connects one more Grok Bot to the cloud computer that already serves the first Bot, the Bot gets the short [routine prompt](routine.md) (the stub). Step 1 of the stub runs `rn-cloud.sh instructions` with the team name and the team hash (the first 16 hex digits of the SHA-256 of the team ID), and that command registers the Bot on the cloud computer by itself the first time.

Only the complete prompt of an added Bot, printed by `./update.sh --full-prompt`, contains the code block below: the installer inserts it as step 0, before R1 of the [rules](routine-body.md), with the Bot address name, the team name and the team hash filled in. The block is never given to a Bot on its own. In that prompt every `rn-cloud.sh` command is prefixed with `RN_BOT=<relay-name>` and the threads files move to `/workspace/agent-mesh/bots/<relay-name>/threads/`; the first Bot's prompt is unchanged.

Step 0 runs [`box/rn-cloud-add-bot.sh`](../box/rn-cloud-add-bot.sh) on `[BOT-CLOUD]`, the Bot's cloud computer, until the Bot is registered there. The one-line request `agent-mesh add-bot <relay-name> <req>` that the installer sends after the Bot's relay has started is a maintenance request (R2); its reply confirms the registration to the Mac. The template below has unfilled placeholders; keep their names unchanged in translations.

```
0. Before R1: if /workspace/agent-mesh/bots/<relay-name>/.registered does not exist, first run bash /workspace/agent-mesh/box/rn-cloud-add-bot.sh '<relay-name>' '<team-name>' '<team-hash>'. This Bot was added to a cloud computer that already serves another Bot; the command only registers this Bot's own two identities, <team-name>/<relay-name>-cloud and <team-name>/<relay-name>-inbox, on that shared cloud computer and needs no user approval. If it fails, tell the user its full output and stop; do not continue with R1 in this run.
```
