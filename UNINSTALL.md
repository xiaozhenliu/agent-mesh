# Uninstall

**English** | [简体中文](UNINSTALL.zh-CN.md)

This is the uninstall part of the [agent-mesh guide](README.md). The labels `[LOCAL]`, `[BOT-CLOUD]` and `[GROK-UI]` are explained under [Execution location labels](README.md#execution-location-labels).

Update this folder first (`git pull`), so that `./uninstall.sh` is the uninstaller described here; the script in v0.4.1 and earlier has no levels, removes the relay settings and signing key even without `--all`, and does not handle the cloud.

`./uninstall.sh` runs on `[LOCAL]`. Without options it only lists what is installed and removes nothing: the version, the team name and this machine, the cloud member, the Bot address names, the relay and its queue, the dsh bridge, the other team members, and any deployment it did not install. `./uninstall.sh --plan` adds what each level would remove and keep; `--plan --level <level>` shows one level. Neither asks anything or changes anything. In a terminal, `./uninstall.sh` then offers to choose a level.

| Level | Removes | Keeps |
|---|---|---|
| `--level program` | The relay service and plist, the relay program, its temporary files and `rollback/`. | Everything else: the team identity on both computers, `~/.config/agent-relay` (webhook settings and signing key), the queue, logs and Reason Network. |
| `--level identity` | Also the deployment record, `~/.config/agent-relay`, the relay state (queue, replay protection, message copies) and the dsh bridge block; this Mac leaves the team, runs `setup --remove` and removes `~/.reason/network`. The Bot leaves the team and removes the cloud's identity, key and names (`box/state`, `box/bin`, `box/person`, `box/relay`, `box/outbound.key`) and the engine backups `box/engine-backup-*`. On this Mac the deployment record directory goes with its engine backups. | Relay logs, the engine binary, and the cloud's `inbox/`, `threads/` and `/workspace/agent-mesh`. |
| `--level all` (or `--all`) | Also the relay logs, `~/.local/bin/reason-network`, the dsh bridge backups and log; on `[BOT-CLOUD]` also `inbox/`, `threads/` and `/workspace/agent-mesh`. | Only the items listed below. |

No level touches this code folder, PATH lines in your shell startup files, reply conventions appended to your projects, an external inbox folder on `[BOT-CLOUD]`, or what the Bot keeps in its long-term memory. Level `all` lists them for you. `dsh/mac-uninstall.sh` alone removes only the dsh bridge; see the [bridge guide](dsh/README.md#uninstall).

## Before you uninstall

1. **Does anything else on `[LOCAL]` use Reason Network?** Levels `identity` and `all` leave the team, disconnect every agent on this computer from Reason Network and remove its state. If this computer uses Reason Network for anything else, use only `--level program`.
2. **Deployments it did not install.** A second relay, another login service that runs a `*_relay.py`, or an older layout installed by hand is listed under "foreign" and never changed. Levels `identity` and `all` stop for each one until you type `I accept <id>`, because removing the shared identity breaks it; `--plan` shows this as a `STOP unless you type I accept <id>` line. Processes are shown by PID and program name only, never with their arguments.
3. **Other users of `/workspace/agent-mesh/box` on `[BOT-CLOUD]`.** The cloud script searches `/workspace` itself and prints a `SHARED:` line for each file or link that refers to the cloud's engine state or binary. The Bot removes nothing while such lines remain, unless you confirm that nothing listed still uses them.
4. **Messages not yet delivered.** The uninstaller shows the relay queue. Level `program` keeps it for the reinstall (entries still `sending` become `unknown` and are not sent again); the deeper levels ask you to type `yes` to lose it. On `[BOT-CLOUD]`, the prompt asks the Bot to offer one more `rn-cloud.sh fetch` first.
5. **Something stops the run.** It lists every reason before changing anything. Every level stops for a running `./update.sh`, a login service changed by hand, or a loaded `com.local.agent-relay` service that does not come from this home's plist (it is never stopped); levels `identity` and `all` also stop when the team or Bot address names differ between the settings, the team list and the deployment record, or when a path is a symlink or belongs to another user. Nothing is removed until they are resolved.

## Remove the relay only

For a reinstall or a version change; the team, the key and the cloud stay as they are.

1. `[GROK-UI]`: pause the routine.
2. `[LOCAL]`: run `./uninstall.sh --level program` and type `y`. With `--yes` it does not ask; this is the only level an agent may run for you.
3. `[LOCAL]`: to reinstall, `git pull` and run `./install.sh --restart-relay`. It installs the relay of this folder again with the same settings, signing key and Bot address name, so nothing needs copying to `[BOT-CLOUD]`. Then resume the routine. Do not run the full install again.

## Remove the identity or everything

Levels `identity` and `all` run only in your own terminal; anywhere else they show the plan and exit with status 3. Keep Grok Bot open: one step happens there.

1. **`[LOCAL]`: run `./uninstall.sh --level identity` or `./uninstall.sh --level all`.** It shows the plan and asks you to type the team name (`identity`) or `delete <team-name>` (`all`).
2. **It stops the relay** and counts the queue again.
3. **Cloud step.** First pause the routine in `[GROK-UI]` and make sure no run is in progress. Then send the printed prompt to Grok Bot as one message. It contains the cloud script and its SHA-256 checksum; the Bot writes the script on `[BOT-CLOUD]`, checks the checksum, runs `check` (which changes nothing) and reports a `PROOF:` value.
   - Type the `PROOF` value into the uninstaller. It matches only if the cloud holds this Mac's signing key, so a cloud of another deployment is never removed by mistake. The uninstaller then shows a confirmation code; send it to the Bot and say you agree. The Bot runs `apply <code>`: it leaves the team while this Mac is still online to hear it, then removes the cloud files. When it reports `DONE` (or `KEPT`, see below), type `done`; when it reports `INCOMPLETE`, type `incomplete`, and the run stays not complete until the cloud is out of the team.
   - Type `prompt` to show the prompt again. If the Bot printed `PROOF: none` (the cloud has no signing key left), type `none`; the Bot then runs `apply-no-proof` only after you confirm in the chat that this is your own deployment.
   - If `leave` fails on `[BOT-CLOUD]`, the Bot shows the output and adds `force` only with your consent. At level `all`, the Bot first lists your own files in the clone (`FILE:` lines). It keeps the clone, and prints `KEPT:`, when Git cannot list everything or when a local branch, tag or `HEAD` has commits that no remote branch has. If the cloud's engine is missing but its identity remains, the Bot removes the files and reports `INCOMPLETE`: the cloud could not leave the team, and the uninstaller then offers to remove it from this Mac.
   - If the cloud cannot finish (`skip`) and is still in the team, the uninstaller offers to remove it from this Mac with `reason-network remove <team-name>/cloud`. It never removes other members.
4. **The dsh bridge**, if present, is removed with `dsh/mac-uninstall.sh --no-restore`. If that fails, the run stops before Reason Network is touched.
5. **This Mac leaves the team**, runs `setup --remove` and stops the engine. If every other member is offline, nobody hears the `leave`; it asks you to type `force`, and they need to remove this Mac on their side. Then the files of the level are removed.
6. **`[GROK-UI]`: delete the routine** and type `deleted`. A paused routine keeps its webhook URL and key valid; deleting it revokes them. Typing `later` leaves the run marked as not complete.
7. Reopen your agent sessions.

The result lists every item as `done`, `absent`, `skipped` or `failed`, and what is still open; the exit status is 0 only when the level is complete. The run can be repeated: finished steps show `absent` or `done`. `./uninstall.sh --cloud-prompt --level identity|all` repeats only the cloud step.

**After an older `./uninstall.sh --all`.** Older versions removed the Mac side only, so the cloud may still be in the team with valid keys. Run `./uninstall.sh --residual --level identity` (or `--level all`). It asks for the team name and gives a prompt for the cloud step without a proof; the Bot runs `apply-no-proof` after you confirm, and stops if other people are in that team.

## Check that it is gone

- `[LOCAL]`: `launchctl print gui/$(id -u)/com.local.agent-relay` fails (no such service). After level `identity`, `~/.local/lib/agent-mesh`, `~/.config/agent-relay`, `~/Library/Application Support/agent-relay` and `~/.reason/network` do not exist; after level `all`, `~/.local/bin/reason-network` and the `agent-relay` logs in `~/Library/Logs` do not exist either. `./uninstall.sh` shows the rest.
- `[BOT-CLOUD]`: after level `identity`, `box/state`, `box/bin`, `box/person`, `box/relay` and `box/outbound.key` are gone under `/workspace/agent-mesh`; after level `all`, `/workspace/agent-mesh` does not exist, unless the Bot reported `KEPT`.
- `[GROK-UI]`: the routine is no longer listed.
