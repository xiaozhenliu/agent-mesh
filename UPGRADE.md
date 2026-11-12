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
| Engine | `~/.local/bin/reason-network` against the version this release pins, and whether the running daemon started after the binary was last replaced | an older engine is upgraded after a question in a terminal, also with `--yes` ([Reason Network engine](#reason-network-engine)); a daemon still running a replaced binary is restarted after a question; a newer or unrecognised engine stops the update |
| dsh plugin | the installed plugin file, if the bridge is installed | the file is replaced; `~/.dsh/cordis.patch.yml` and your bridge settings stay as they are; quit and reopen dsh |
| Cloud scripts | `box/rn-cloud.sh` and `box/rn-fetch.py` against what you last confirmed | guided steps, below |
| Cloud setup | `box/rn-cloud-setup.sh` against what you last confirmed | asks whether the cloud side is set up for this version; the changelog describes any migration |
| Cloud engine | the Reason Network version on `[BOT-CLOUD]` this Mac last confirmed, against the pinned version | asks whether to do the cloud engine step in this run; it runs inside the guided steps, below |
| Routine prompt | the prompt rendered for the chosen language against what you last confirmed | guided steps, below |

What the Mac last confirmed is kept in `~/.local/lib/agent-mesh/deployed.json`. When only the relay or the dsh plugin changed, the update can run unattended: in a terminal it asks for one confirmation, which `--yes` skips; outside a terminal it asks nothing. When the cloud scripts or the routine prompt changed, it needs a terminal and walks you through the order that keeps old parts from being woken: it stops the relay, asks you to pause the routine in `[GROK-UI]`, prints a message asking Grok Bot to pull this commit on `[BOT-CLOUD]` together with the commit and checksums its reply must show, asks you to paste Grok Bot's reply and compares it, prints the new routine prompt to paste, asks you to resume the routine, and finally starts the relay. The cloud step needs a committed checkout that matches its upstream, so that the cloud pulls exactly the same files.

- `./update.sh --plan` only shows the comparison and the steps; it changes nothing in the deployment. Without `--no-pull` it may still fast-forward the checkout first.
- Exit code 3 means answers in a terminal are needed; nothing was changed.
- If you answer no, press Ctrl-C or a step fails, the relay that was running before is started again, and you are reminded if the routine is still paused. Run `./update.sh` again to continue from that step; finished steps are not repeated unless the version moved on in between.
- Updating an installation from before Bot address names: it keeps the name `bot`, and the routine prompt stays the same. The cloud setup script changed, so `update.sh` asks once whether the cloud side is set up for this version; no cloud migration is needed, answer yes. An installation with another Bot address name cannot go back to a version without Bot address names.
- First update from v0.3.0 or earlier: run `git pull` yourself once, because older checkouts have no `update.sh`. Without a record, the cloud scripts and the prompt count as unknown, so the guided steps run once.
- Cloud step: paste Grok Bot's whole reply, then a line with only a dot (`.`). The cloud is recorded as updated only when the reply shows exactly this checkout's commit and every checksum the message lists, and in the cloud engine step also the `engine:` line with the pinned version; a plain yes, an empty answer, another commit or a different checksum leaves it unrecorded, the routine stays paused, and the next `./update.sh` asks for the reply again. If the cloud clone is on a detached HEAD, the message asks Grok Bot to check out the branch this checkout tracks before pulling, and to stop rather than reset, clean or stash anything.
- The script cannot see the routine or the cloud computer: it relies on your answers and on the commit and checksums Grok Bot reports.

## Reason Network engine

agent-mesh 0.7.0 moves the pinned engine from Reason Network 0.10.7 to 0.12.17 (0.10.7 is affected by the upstream security advisory GHSA-84r9-56w3-xqp9). `./update.sh` upgrades the engine on both computers. It works from any earlier agent-mesh version in one run: it installs the new relay on the old engine first, so you do not need to install 0.6.0 first.

On `[LOCAL]` it needs a terminal and always asks before the switch; `--yes` does not skip that question. The order:

1. It installs this checkout's relay on the engine you have and checks that the relay registers under its name. If that fails, the previous relay is put back and the engine is not touched.
2. It lists other processes that use the engine, such as open Claude Code or Codex sessions. They do not block the upgrade and keep working with the restarted daemon; restart those sessions after the update so they run the new version.
3. It downloads 0.12.17 and checks it against the SHA-256 written in `install.sh` (the same table is in `box/rn-cloud-setup.sh`), so a changed download is refused. If the download or the check fails, the engine is not changed; the relay from step 1 stays installed and running.
4. It stops the relay, copies the binary and `~/.reason/network` to `~/.local/lib/agent-mesh/engine-backup-<UTC time>/` (mode 700), replaces the binary, restarts the daemon, and checks that the running daemon started after the replacement, reports 0.12.17, still lists the team's machines and answers `who`.
5. It starts the relay and checks that the relay registered on the new daemon.

If step 4 or 5 fails, or you press Ctrl-C, only the old binary is put back, the daemon is restarted on it and the relay starts again. The state directory is not restored: the newer engine only adds tables and columns, 0.10.7 opens the upgraded database, and mail that arrived after the switch stays. A daemon that does not exit after `reason-network stop` is ended with SIGTERM, then SIGKILL. Going back to 0.10.7 does not bring back what 0.12 already dropped or what 0.10.7 does not know: ordinary sessions forgotten while 0.12 ran, with their unread mail (the relay's and the cloud's mailboxes are not forgotten); and newer team signing attestations and `[folders]` settings, which 0.10.7 drops the next time it saves the team state. CLI sessions created under 0.10.7 after a rollback are not mailboxes until 0.12's `listen` marks them again after the next upgrade. If the old engine cannot be confirmed running, the script prints the manual steps and keeps the record `~/.local/lib/agent-mesh/engine-upgrade.json`; `./check.sh` reports FAIL, and the next `./update.sh` rolls the switch back before anything else. Restoring the state directory from the backup is only the last manual step and loses the mail received after the switch.

The backup holds a copy of this computer's engine identity and received messages. Delete it once `./check.sh` passes; `./uninstall.sh --level identity` also removes it, with the deployment record.

`./update.sh` never downgrades: a newer or unrecognised engine stops it, and `./install.sh` stops on an older engine and points to `./update.sh`. If the binary is already 0.12.17 but the running daemon started before the binary was replaced, `./update.sh` offers to restart the daemon.

On `[BOT-CLOUD]`, the cloud engine step is part of the guided cloud update, while the routine is paused. The message for Grok Bot adds `bash /workspace/agent-mesh/box/rn-cloud-setup.sh --upgrade-engine`, which checks the cloud engine and upgrades it the same way: pinned checksum, backup in `box/engine-backup-<UTC time>/`, switch, check of the running daemon, and on failure only the old binary back. An engine that is already 0.12.17 is only checked; its daemon is started or restarted when needed. Grok Bot's reply must also show the checksums of `box/rn-cloud-setup.sh` and `box/rn-engine.py` and the line `engine: reason-network 0.12.17 running (daemon pid …)`; `./update.sh` compares them and records the cloud engine only when they match. If you answer no to the cloud engine step, the summary says it is not done and the next `./update.sh` asks again. A cloud engine newer than 0.12.17 is left as it is, and the step fails.

While one side runs 0.10.7 and the other 0.12.17, in either order, messages, receipts and re-forwards keep working: agent-mesh 0.6.0 and later read both engines' output. Upgrade the Mac and the cloud in the same run when you can. After the upgrade, Reason Network 0.12's rules apply: its default relays are under `compute.reasonmachines.com` ([Network](README.md#network)), and ordinary sessions offline for more than a day are forgotten together with their unread messages ([Network and usage](README.md#network-and-usage)).

Check the result with `./check.sh`. It reports the engine version against the pinned one, whether the running daemon runs the installed binary, an unfinished engine upgrade, whether the relay registered on the running daemon, and the cloud engine version this Mac last confirmed. For up to 11 minutes after a daemon or relay restart, the registration line is SKIP while the relay registers again.

Known limitations:

- The daemon's start time and the binary's change time are compared in whole seconds, so a daemon the old binary started in the same second as the replacement counts as fresh. On Linux a daemon still running a replaced binary is also caught because its program shows as deleted; macOS has no such check.
- The relay records its registration after `listen` returns. If the daemon restarts in between, `./check.sh` can report a stale registration until the next `listen`, a few seconds later.
- Relay startup work before the first registration (catching up on missed mail, re-sends, the webhook) can use up the 45-second registration wait; the restart then counts as failed and is rolled back. `AGENT_MESH_REGISTER_WAIT=<seconds>` raises the wait.
- If a rollback succeeds but the relay does not start again, the upgrade record is already removed, so the next `./update.sh` does not retry; run `./install.sh --restart-relay` as the message says.
- `./check.sh` does not compare the cloud scripts' checksums; it reports only the cloud engine version this Mac last confirmed. `./update.sh` compares the checksums when it guides the cloud step.

## Manual upgrade

When the relay, the cloud scripts and the routine prompt all change, update them together in this order, so that no doorbell rings while some parts are old:

1. On `[LOCAL]`, stop the relay: `launchctl bootout "gui/$(id -u)/com.local.agent-relay"`. Messages that reach the Mac from now on stay unread in the local engine and ring no doorbell.
2. In `[GROK-UI]`, pause the routine and wait until no run is active.
3. Have Grok Bot update `/workspace/agent-mesh` on `[BOT-CLOUD]` (ask it directly in chat, not through the routine) and confirm that the `rn-cloud.sh` usage lists `done`, `working` and `RN_GOT`.
4. In `[GROK-UI]`, replace the saved routine prompt and resume the routine. The relay is still stopped, so no doorbell rings yet.
5. On `[LOCAL]`, update the repository and run `./install.sh --restart-relay`, or rerun the full install: `./install.sh --name <team-name> --lang <en|zh-CN>`. Neither upgrades an older Reason Network: `--restart-relay` keeps the engine you have, and the full install stops on an older engine. Upgrade the engines with `./update.sh` ([Reason Network engine](#reason-network-engine)), or by hand on the cloud with `bash /workspace/agent-mesh/box/rn-cloud-setup.sh --upgrade-engine` while the routine is paused. An already configured webhook is not asked for again; the installer copies the new `agent_relay.py` and starts the relay service, which first catches up from `network.db` and then picks up the messages that arrived while it was stopped. If the new relay does not come up, the installer puts the previous one back. The full install also shows the agent wiring plan again and asks for confirmation.

While the relay is stopped the Mac gets no pushes, and messages wait in the local engine. Keep the stop short: a reply the cloud signed more than 15 minutes before the relay forwards it is refused as stale and is not delivered. If step 3 or 4 fails, start the old relay again with `launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.local.agent-relay.plist` to return to the state before the upgrade.

To roll back receipts and re-rings later, go in this order so that an old relay never receives a receipt:

1. On `[LOCAL]`, set `GOT_TIMEOUT_SECONDS=0` in `~/.config/agent-relay/env`, restart the relay with `launchctl kickstart -k "gui/$(id -u)/com.local.agent-relay"`, keep the routine running and wait until `~/Library/Logs/agent-relay.log` shows `pending: 0` and `alerts: 0` (while the webhook delivers, at most about `GOT_DONE_SECONDS × (GOT_MAX_RERINGS + 1)`).
2. In `[GROK-UI]`, pause the routine and wait until no run is active.
3. Have Grok Bot roll `/workspace/agent-mesh` back on `[BOT-CLOUD]` and check that the cloud engine's outbox is empty, then wait a few minutes so the relay still handles any receipt on its way.
4. Restore the previous routine prompt and resume the routine.
5. Roll back the relay on `[LOCAL]` last.
