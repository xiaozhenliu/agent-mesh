# Changelog

**English** | [简体中文](CHANGELOG.zh-CN.md)

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## v0.9.1 — 2026-11-17

Upgrading takes less effort, and this version includes security hardening (#38). Update with `./update.sh`; the cloud is not updated in this version and the routine prompt does not need pasting again.

### Added

- `./install.sh --python <path>` chooses the Python the relay runs on.

### Changed

- The relay's Python is fixed: running `./update.sh` from another terminal or inside a virtual environment no longer changes it; `./check.sh` shows it.
- When the checkout has uncommitted changes, `./update.sh` lists the files and can continue with the checkout as it is.
- The upgrade guide has a table to look up by the version you come from.

### Fixed

- Security hardening; `./update.sh` may restart the engine and the relay once for it.

## v0.9.0 — 2026-11-16

One Mac can connect several Grok Bots (#32). Update with `./update.sh`; the cloud scripts change, so it sends one cloud update request. An existing installation becomes the first Bot and keeps its paths and routine prompt.

### Added

- `./install.sh --add-bot` adds a Grok Bot. It asks only the Bot's name in Grok; the new Bot registers itself on the cloud.
- `./uninstall.sh --level bot --relay-name <name>` removes one Bot and keeps the others. A removed name cannot be added again.
- `./check.sh` shows one section per Bot.

### Changed

- `./update.sh` covers every Bot and no longer asks you to pause routines; `--manual` still does.
- With several Bots, per-Bot options need `--relay-name <name>`. `--restart-relay` and `--rotate-key` cover every Bot.
- An engine upgrade switches the engine and every relay together, and rolls all of them back if one fails.
- `rn-cloud.sh purge` is refused while the cloud has more than one Bot.

## v0.8.3 — 2026-11-13

The first cloud update from v0.8.1 or earlier no longer stops at a refusal. Update with `./update.sh`; the routine prompt changes in this version, so paste the new prompt into Grok once when `update.sh` asks.

### Fixed

- The routine prompt has a rule for the first-update command block that `./update.sh` sends when the cloud was last confirmed with v0.8.1 or earlier: Grok Bot does not run it in the routine run, tells you the message id and a sentence to say, and runs it and replies after you say it in a Grok conversation. The v0.8.2 prompt refused the block, and `./update.sh` said Grok Bot would ask, so the update waited until you found out what to do (#37).
- `./update.sh` says what to do in Grok when it sends the first-update block and when it stops waiting for its reply.
- `llms.txt` states the current version.

## v0.8.2 — 2026-11-13

Updating takes one command: `./update.sh` asks once, updates the Mac side and has Grok Bot update the cloud by itself, without pausing the routine or copying replies. Update with `./update.sh` in a terminal; the routine prompt changes in this version, so paste the new prompt into Grok once when `update.sh` asks.

### Added

- `box/rn-cloud-update.sh <tag> <commit> <request>` updates the cloud to the checked release in one command, or prints `STOP: <reason>` and changes nothing.
- The routine prompt runs a one-line `agent-mesh update <tag> <commit> <request>` message from the owner without asking and replies with its output; other update-like messages are not run.
- Terminal text follows `--lang en|zh-CN`, then the language recorded at install, then `AGENT_MESH_LANG`, then English; `check.sh` gains `--lang`.

### Changed

- `./update.sh` asks a single "Continue? [Y/n]" after showing the plan (`--yes` skips it).
- When the cloud needs an update, `./update.sh` sends the update message to Grok Bot and waits up to 15 minutes (`AGENT_MESH_REPLY_WAIT`) for its reply; if none arrives, rerun `./update.sh`.
- The first update from a version without the new script sends a command block instead; `./update.sh --manual` keeps the guided flow of v0.8.1.

## v0.8.1 — 2026-11-13

Security hardening for the cloud side: the cloud installs and updates only a checked published release, and its engine can no longer be replaced by another engine daemon updating itself. Update with `./update.sh` in a terminal; it runs the cloud scripts and the cloud engine step in one guided cloud step, and the routine prompt does not change.

### Security

- An engine command run by hand on the cloud can no longer replace the pinned engine, and the cloud scripts stop when the engine is not a pinned build.
- The cloud clone is pinned to the release tag `v<VERSION>` and checked before its scripts run.

### Changed

- `install.sh --cloud-prompt` and `./update.sh` first check online that the release tag matches this checkout's cloud scripts; `./update.sh` gains `--repo-url` (default: the public repository) and no longer requires a committed checkout.
- `rn-cloud-setup.sh --upgrade-engine` also replaces an engine that is not a pinned build; Grok Bot's reply must show its `engine: pinned build` and `engine: launcher ok` lines.
- `./update.sh` records the cloud step only from the `agent-mesh-result` lines of Grok Bot's reply.

### Fixed

- The engine settings `auto_update` and `lan` are written where they take effect.

## v0.8.0 — 2026-11-12

The Bot address is the Bot's own name: a new installation asks for it instead of using `bot`, and the Bot's name in Grok is shown next to the address (#31). Existing installations keep their address. Update with `./update.sh`; it only writes the filled-in reply convention copies, and the routine prompt is unchanged.

### Added

- `install.sh --grok-name <name>` records the Bot's name as Grok shows it, in any language; given alone, it changes only that name. Run it once after updating to show the name (#31).
- The installer and `./check.sh` show `<team-name>/<address> → Grok: <name>` (#31).
- The reply convention templates carry this team's Bot address and Grok name; `python3 scripts/reply_convention.py refresh <repository>/AGENTS.md` updates a repository's copy. Replace copies appended before v0.8.0 with one from `~/.local/lib/agent-mesh/templates/` (#31).

### Changed

- A new installation needs `--relay-name`; without it, `install.sh` stops and suggests an address derived from `--grok-name`, and AGENT-INSTALL asks for the Bot's name in Grok instead of offering `bot` (#31).
- `./check.sh` and `install.sh` read the recorded address when the relay env is missing, instead of assuming `bot` (#31).

### Fixed

- `./update.sh --help` printed only part of its description.

## v0.7.0 — 2026-11-12

Pins Reason Network 0.12.17 and upgrades installed engines on the Mac and the cloud, with a backup and a rollback (#20); 0.10.7 is affected by the upstream security advisory GHSA-84r9-56w3-xqp9. First allow `*.compute.reasonmachines.com` alongside `*.iroh.link`, then run `./update.sh` in a terminal; its guided cloud step has Grok Bot run `box/rn-cloud-setup.sh --upgrade-engine` while the routine is paused. The routine prompt is unchanged.

### Added

- `./update.sh` upgrades an older engine on the Mac, puts the old one back on failure or Ctrl-C and never downgrades ([UPGRADE.md](UPGRADE.md#reason-network-engine)). Afterwards restart open Claude Code and Codex sessions, and delete the engine backups once `./check.sh` passes.
- `box/rn-cloud-setup.sh --upgrade-engine` does the same on the cloud (#20).
- `check.sh` reports the engine version, an unfinished engine upgrade and the relay's registration (#20, #30).

### Changed

- Reason Network 0.12 forgets ordinary sessions offline for more than a day, with their unread messages; the relay's and the cloud's mailboxes are exempt.
- A full install stops on an older engine and points to `./update.sh` (#20); `./uninstall.sh --level identity` also removes the engine backups.

### Fixed

- `box/rn-cloud-setup.sh` no longer unpacks the pinned engine over an existing one, even a newer one.
- `./install.sh --restart-relay` could accept a relay that never listened (#30).

## v0.6.0 — 2026-11-11

Prepares for Reason Network 0.12: the relay, the self-check and the cloud scripts read what both engine versions produce; the pinned engine stays 0.10.7 (#20). Update with `./update.sh`; the cloud scripts and the routine prompt are unchanged, and when it asks whether the cloud side is set up for this version, answer yes.

### Added

- README: Reason Network 0.12's default relays (`*.compute.reasonmachines.com`), how a team sets or resets its relay, and 0.12 forgetting ordinary sessions offline for more than a day (#20).

### Changed

- `check.sh` and `install.sh` probe the relay the installed engine actually uses; when it cannot be told, `check.sh` reports SKIP with what to allow (#20).
- The relay also accepts cloud receipts from `<team-name>/terminal@<CLOUD_MACHINE>`, the form newer engines give a terminal sender (#20).
- `./install.sh --restart-relay` restores the previous relay and fails when the new one registers under another name (#20).
- The DeepSeek Harness plugin shows a message from `terminal@<machine>` as typed in a terminal (#20).

### Fixed

- `check.sh`, the cloud setup and uninstall scripts and `./uninstall.sh` read team lists that mark a person `(verified)`, as Reason Network 0.12 does (#20).

## v0.5.2 — 2026-11-11

Makes the guided cloud update and the self-check report what actually happened (#26, #28). Update with `./update.sh`; the relay, the cloud scripts and the routine prompt are unchanged, so a confirmed v0.5.1 with no unfinished update needs nothing on the cloud.

### Changed

- The README is shorter: the uninstall steps, the upgrade steps and the message formats moved, unchanged, to [UNINSTALL.md](UNINSTALL.md), [UPGRADE.md](UPGRADE.md) and [PROTOCOL.md](PROTOCOL.md). No behavior changes.

### Fixed

- `update.sh` records the cloud as updated only when you paste Grok Bot's reply and it shows this checkout's commit and cloud script checksums; a plain yes no longer counts. The message for Grok Bot also covers a cloud clone on a detached HEAD (#28).
- `check.sh` judges the webhook by the latest POST in the relay log, so a failure that a later retry delivered no longer sends you to `./install.sh --webhook` (#26).

## v0.5.1 — 2026-11-11

Fixes the registration check added in v0.5.0, which stopped cloud replies after a fetch.

### Fixed

- Cloud scripts and installer: the registration check no longer fails when a name registers a second time while the same engine keeps running. The engine records the time only at a name's first registration in each run, so the check that v0.5.0 added refused every later one. On the cloud, `rn-cloud.sh send` failed after a `fetch`, and `./install.sh --webhook` failed on an existing installation. The check now accepts the session the engine has for the name when it carries exactly that name; it still refuses a numbered name or a missing session (#27).

### Upgrade notes

Only the cloud script and the installer changed; the relay, the routine prompt and the cloud setup script are unchanged. A cloud on v0.5.0 cannot send replies after a `fetch`, so update it soon: run `git pull`, then `./update.sh --lang <en|zh-CN>` in a terminal, and follow its guided steps to have Grok Bot pull the new commit.

## v0.5.0 — 2026-11-09

Relay and cloud-script fixes, a choosable Bot address name and a tiered uninstaller. Run `git pull`, then `./update.sh --lang <en|zh-CN>` in a terminal, and answer yes when asked whether the cloud side is set up; the routine prompt is unchanged.

### Added

- `install.sh --relay-name <name>` chooses the Bot address name (default `bot`, kept by existing installations). It cannot be changed, and an installation with another name cannot go back to an earlier version.
- The cloud uninstall prompt removes the cloud's identity only with a code shown on the Mac.

### Changed

- `./uninstall.sh` is a tiered uninstaller for both computers: without options it only lists what is installed, `--plan` shows each level, and `--level program`, `--level identity` and `--level all` (`--all`) remove progressively more. `--residual` cleans up the cloud after an older `--all`.
- `-y` works only with `--level program`: replace `./uninstall.sh -y` or `./uninstall.sh --all -y` with `--level program --yes`.

### Fixed

- Relay: the webhook POST no longer follows redirects, so `WEBHOOK_URL` must be the final address; if the relay log shows `webhook POST failed: 30x`, copy the URL from the routine again and run `./install.sh --webhook`.
- Relay: recipient addresses are checked against an allow-list, conflicting resend alerts and extra wakes are gone (#15, #17), and envelopes that `reason-network listen` shows rewritten are no longer refused (#11).
- Cloud scripts: a team name containing words such as `held` (for example `sheldon`) no longer fails sends (#18).
- Installer: no false failures under `set -o pipefail` (#24), and names with repeated hyphens (`a--b`) are refused (#25).

## v0.4.1 — 2026-11-08

The cloud script no longer reports the Mac offline when it is connected.

### Fixed

- `box/rn-cloud.sh`: the connection check used `doctor | grep -q` under `set -o pipefail`. When `grep` stopped at the first match, `doctor` could be killed by SIGPIPE and the check failed for about 90 seconds although the Mac was connected, so `send`, `who` and `init-inbox` stopped with "the Mac is not connected" and `fetch` skipped re-forward requests. The check now reads all of the `doctor` output; a failing `doctor` still counts as not connected.

### Changed

- README: the uninstall section now gives an ordered plan for both computers and the routine, says what is shared and must be checked first, and how to confirm that everything is gone.

### Upgrade notes

The relay and the routine prompt are unchanged; only the cloud script changed. Run `git pull`, then `./update.sh --lang <en|zh-CN>` in a terminal, and follow its guided steps to have Grok Bot pull the new commit.

## v0.4.0 — 2026-11-06

`./update.sh` updates an existing installation without reinstalling. Run `git pull` once, then `./update.sh --lang <en|zh-CN>` in a terminal; the first run goes through the guided cloud steps once, and a working installation can answer yes when asked whether the cloud side is set up. The relay, the cloud scripts and the routine prompt are unchanged.

### Added

- `./update.sh` compares the relay, the dsh plugin, the cloud scripts and the routine prompt with what is deployed, updates the relay and the dsh plugin itself, and guides you through the rest. `--plan` shows the comparison only; an interrupted update resumes where it stopped.
- `./install.sh --restart-relay` installs this checkout's relay and restarts it, restoring the previous one if the new one does not come up.
- `dsh/mac-install.sh --plugin-only` replaces only the installed plugin file.

### Changed

- `install.sh` restores the previous relay when a new one does not come up, and `dsh/mac-install.sh` restores the previous plugin when a step fails.
- `install.sh` records the cloud scripts and the routine prompt it prints as pending; `./update.sh` asks whether they were applied.

## v0.3.0 — 2026-11-05

The dsh bridge can now be removed cleanly and says when a DeepSeek Harness update breaks it.

### Added

- `dsh/mac-uninstall.sh` removes the dsh bridge by swapping only its block in `~/.dsh/cordis.patch.yml` back to Reason Network's own block, saved by `dsh/mac-install.sh`; older installations fall back to the earliest backup. Other changes to the patch are kept.
- The dsh bridge checks on load that DeepSeek Harness provides what it needs; when something is missing it logs what and how to recover, and skips incompatible sessions instead of failing on them.

### Changed

- `./uninstall.sh --all` removes the dsh bridge block before `setup --remove`, so dsh no longer keeps pointing at a removed engine; if that fails, it stops before removing Reason Network.

### Upgrade notes

The relay, cloud scripts and routine prompt are unchanged. If you use the dsh bridge, update the local repository, run `dsh/mac-install.sh <team-name>` again, then quit and reopen DeepSeek Harness; this installs the checked plugin and saves Reason Network's own block for the uninstaller. Without it, `dsh/mac-uninstall.sh` falls back to the earliest backup. Nothing else needs updating.

## v0.2.1 — 2026-11-03

Maintenance release with no behavior changes.

### Changed

- CI's shellcheck job now blocks a failing build, and all shell scripts pass it.
- `./check.sh` words its PATH check as "PATH includes ~/.local/bin" or "PATH lacks ~/.local/bin".

### Upgrade notes

Existing deployments need no update.

## v0.2.0 — 2026-11-03

Reliable delivery: per-message isolation in the relay, idempotent cloud fetch, receipts with re-rings and reply deduplication. The relay, the cloud scripts and the routine prompt must be updated together, in the [order given in the README](README.md#upgrade).

### Added

- Cloud receipts and relay re-rings: the relay rings again when a message is not stored or handled in time, and alerts the user after repeated failures (#7).
- A reply resent by the cloud is delivered only once (#8).
- Messages whose doorbell was lost are handed out on the next fetch.
- Commands `rn-cloud.sh working` and `rn-cloud.sh done`; relay settings `GOT_TIMEOUT_SECONDS`, `GOT_DONE_SECONDS`, `GOT_MAX_RERINGS` and `NONCE_KEEP_SECONDS`; cloud settings `RN_CLAIM_LEASE`, `RN_ORPHAN_DAYS`, `RN_ORPHANS` and `RN_GOT`.

### Changed

- The cloud hands each message to one routine turn and leaves an interrupted one to the user.
- The relay handles messages one by one, and at startup recovers messages marked read but never handled.
- A re-forward request from a new cloud no longer rings an extra doorbell, and proxied-send problems are reported as relay alerts.

### Fixed

- Every HTTP 2xx webhook response counts as delivered, which ends repeated wakes.
- A proxied send that times out no longer ends the whole batch (#1, #5).

## v0.1.1 — 2026-11-03

Security hardening release.

### Added

- English and Chinese [security policy](SECURITY.md).
- `./install.sh --rotate-key` to rotate the signing key.
- Cleanup commands: `./install.sh --purge-copies` locally and `rn-cloud.sh purge <days>` on the cloud.
- `./check.sh` checks team membership and reports FAIL for unexpected members.
- Security boundaries in the reply convention template.

### Changed

- Rewrote the README's [Security and privacy](README.md#security-and-privacy) section: trust root, local agent auto-approve modes, where bodies stay in plain text, team-membership checks.
- Cloud fetch and cleanup share one lock.
- `./install.sh --webhook` keeps the user's optional settings when it rewrites the configuration.

### Fixed

- The documented Codex wake method is corrected to `codex queue`.

### Upgrade notes

The relay program is unchanged. The cloud clone and reply conventions already copied into projects must be updated separately.

## v0.1.0 — 2026-11-02

First tagged release.

### Added

- Connects local Claude Code, Codex, pi and DeepSeek Harness sessions with Grok Bot through a private Reason Network team: signed messages, webhook wake-ups, full-body retrieval and redelivery.
- Paired English and Simplified Chinese guides and prompts, with `[LOCAL]`, `[BOT-CLOUD]` and `[GROK-UI]` execution labels.
- Supports Apple Silicon and Intel Macs; the cloud side supports Linux x86_64 and ARM64.
- Pre-install checks for existing deployments, dependencies, platform and CPU.
- Isolated installer tests.
- `VERSION` file and `./install.sh --version`.

Reason Network is pinned to v0.10.7; its version is independent of agent-mesh's.
