# Upgrade

**English** | [简体中文](UPGRADE.zh-CN.md)

This is the upgrade part of the [agent-mesh guide](README.md). The labels `[LOCAL]`, `[BOT-CLOUD]` and `[GROK-UI]` are explained under [Execution location labels](README.md#execution-location-labels).

The local repository, the clone on the Bot cloud computer and the saved routine prompt are separate: changing files on the Mac does not update the other two. A reply convention already appended to a project's `AGENTS.md` is not updated either; copy the template again. The [changelog](CHANGELOG.md) says which parts each version changes.

## Upgrade with update.sh

In the repository on `[LOCAL]`, run:

```bash
./update.sh --lang <en|zh-CN>
```

It first runs `git pull --ff-only` (skip it with `--no-pull`), then compares each part with what this Mac deployed:

| Part | Compared | When it differs |
|---|---|---|
| Relay | the installed `agent_relay.py` and its login service file against this checkout | installed and restarted after one confirmation (none with `--yes` or outside a terminal); if the new relay does not come up, the previous one is put back |
| dsh plugin | the installed plugin file, if the bridge is installed | the file is replaced; `~/.dsh/cordis.patch.yml` and your bridge settings stay as they are; quit and reopen dsh |
| Cloud scripts | `box/rn-cloud.sh` and `box/rn-fetch.py` against what you last confirmed | guided steps, below |
| Cloud setup | `box/rn-cloud-setup.sh` against what you last confirmed | asks whether the cloud side is set up for this version; the changelog describes any migration |
| Routine prompt | the prompt rendered for the chosen language against what you last confirmed | guided steps, below |

What the Mac last confirmed is kept in `~/.local/lib/agent-mesh/deployed.json`. When only the relay or the dsh plugin changed, the update can run unattended: in a terminal it asks for one confirmation, which `--yes` skips; outside a terminal it asks nothing. When the cloud scripts or the routine prompt changed, it needs a terminal and walks you through the order that keeps old parts from being woken: it stops the relay, asks you to pause the routine in `[GROK-UI]`, prints a message asking Grok Bot to pull this commit on `[BOT-CLOUD]` together with the commit and checksums its reply must show, asks you to paste Grok Bot's reply and compares it, prints the new routine prompt to paste, asks you to resume the routine, and finally starts the relay. The cloud step needs a committed checkout that matches its upstream, so that the cloud pulls exactly the same files.

- `./update.sh --plan` only shows the comparison and the steps; it changes nothing in the deployment. Without `--no-pull` it may still fast-forward the checkout first.
- Exit code 3 means answers in a terminal are needed; nothing was changed.
- If you answer no, press Ctrl-C or a step fails, the relay that was running before is started again, and you are reminded if the routine is still paused. Run `./update.sh` again to continue from that step; finished steps are not repeated unless the version moved on in between.
- Updating an installation from before Bot address names: it keeps the name `bot`, and the routine prompt stays the same. The cloud setup script changed, so `update.sh` asks once whether the cloud side is set up for this version; no cloud migration is needed, answer yes. An installation with another Bot address name cannot go back to a version without Bot address names.
- First update from v0.3.0 or earlier: run `git pull` yourself once, because older checkouts have no `update.sh`. Without a record, the cloud scripts and the prompt count as unknown, so the guided steps run once.
- Cloud step: paste Grok Bot's whole reply, then a line with only a dot (`.`). The cloud is recorded as updated only when the reply shows exactly this checkout's commit and both checksums; a plain yes, an empty answer, another commit or a different checksum leaves it unrecorded, the routine stays paused, and the next `./update.sh` asks for the reply again. If the cloud clone is on a detached HEAD, the message asks Grok Bot to check out the branch this checkout tracks before pulling, and to stop rather than reset, clean or stash anything.
- The script cannot see the routine or the cloud computer: it relies on your answers and on the commit and checksums Grok Bot reports.

## Manual upgrade

When the relay, the cloud scripts and the routine prompt all change, update them together in this order, so that no doorbell rings while some parts are old:

1. On `[LOCAL]`, stop the relay: `launchctl bootout "gui/$(id -u)/com.local.agent-relay"`. Messages that reach the Mac from now on stay unread in the local engine and ring no doorbell.
2. In `[GROK-UI]`, pause the routine and wait until no run is active.
3. Have Grok Bot update `/workspace/agent-mesh` on `[BOT-CLOUD]` (ask it directly in chat, not through the routine) and confirm that the `rn-cloud.sh` usage lists `done`, `working` and `RN_GOT`.
4. In `[GROK-UI]`, replace the saved routine prompt and resume the routine. The relay is still stopped, so no doorbell rings yet.
5. On `[LOCAL]`, update the repository and run `./install.sh --restart-relay`, or rerun the full install: `./install.sh --name <team-name> --lang <en|zh-CN>`. An already configured webhook is not asked for again; the installer copies the new `agent_relay.py` and starts the relay service, which first catches up from `network.db` and then picks up the messages that arrived while it was stopped. If the new relay does not come up, the installer puts the previous one back. The full install also shows the agent wiring plan again and asks for confirmation.

While the relay is stopped the Mac gets no pushes, and messages wait in the local engine. Keep the stop short: a reply the cloud signed more than 15 minutes before the relay forwards it is refused as stale and is not delivered. If step 3 or 4 fails, start the old relay again with `launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.local.agent-relay.plist` to return to the state before the upgrade.

To roll back receipts and re-rings later, go in this order so that an old relay never receives a receipt:

1. On `[LOCAL]`, set `GOT_TIMEOUT_SECONDS=0` in `~/.config/agent-relay/env`, restart the relay with `launchctl kickstart -k "gui/$(id -u)/com.local.agent-relay"`, keep the routine running and wait until `~/Library/Logs/agent-relay.log` shows `pending: 0` and `alerts: 0` (while the webhook delivers, at most about `GOT_DONE_SECONDS × (GOT_MAX_RERINGS + 1)`).
2. In `[GROK-UI]`, pause the routine and wait until no run is active.
3. Have Grok Bot roll `/workspace/agent-mesh` back on `[BOT-CLOUD]` and check that the cloud engine's outbox is empty, then wait a few minutes so the relay still handles any receipt on its way.
4. Restore the previous routine prompt and resume the routine.
5. Roll back the relay on `[LOCAL]` last.
