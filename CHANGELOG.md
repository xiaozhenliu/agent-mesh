# Changelog

**English** | [简体中文](CHANGELOG.zh-CN.md)

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## v0.5.1 — 2026-11-11

Fixes the registration check added in v0.5.0, which stopped cloud replies after a fetch.

### Fixed

- Cloud scripts and installer: the registration check no longer fails when a name registers a second time while the same engine keeps running. The engine records the time only at a name's first registration in each run, so the check that v0.5.0 added refused every later one. On the cloud, `rn-cloud.sh send` failed after a `fetch`, and `./install.sh --webhook` failed on an existing installation. The check now accepts the session the engine has for the name when it carries exactly that name; it still refuses a numbered name or a missing session (#27).

### Upgrade notes

Only the cloud script and the installer changed; the relay, the routine prompt and the cloud setup script are unchanged. A cloud on v0.5.0 cannot send replies after a `fetch`, so update it soon: run `git pull`, then `./update.sh --lang <en|zh-CN>` in a terminal, and follow its guided steps to have Grok Bot pull the new commit.

## v0.5.0 — 2026-11-09

Relay and cloud-script fixes, a choosable Bot address name and a tiered uninstaller.

### Fixed

- Relay: the webhook POST no longer follows HTTP redirects. A 3xx answer counts as a failed POST and is retried like any other failure; the log asks for the final address. `WEBHOOK_URL` must be the final address: if the relay log shows `webhook POST failed: 30x`, copy the webhook URL again from the routine and run `./install.sh --webhook`.
- Relay: RN-OUT recipient addresses are checked against an allow-list. `to` must be `<person>/<session>[@<machine>]`, each part lowercase letters, digits and hyphens starting with a letter or digit, and a non-empty `reply_to` must be a plain message id. Anything else is refused before sending, with an alert; the nonce stays free, so a corrected envelope can be sent.
- Relay: when a nonce that was refused or failed is tried again and the result is unknown, the earlier refusal or failure alert is withdrawn and the unknown alert says it replaces it, so the Bot no longer gets "may resend" and "do not resend" for the same message (#15).
- Relay: a `done` receipt also withdraws a doorbell still queued for a message whose pending entry an RN-OUT reply had already ended, so the Bot is not woken once more (#17).
- Cloud scripts: send results are judged on the engine's status words only. A recipient or team name containing words such as `held` or `error` (for example a team called `sheldon`) no longer makes a queued receipt, re-forward request or `rn-cloud.sh send` count as failed. For receipts and re-forward requests, an `rn send` that exits 0 without a `→` line now counts as failed, as the relay already required (#18).
- Relay: when `reason-network listen` shows a signed RN-OUT envelope in a rewritten form (it rewrites text that looks like its own message tags, such as `<reason-message …>`), the relay no longer refuses it as a bad signature. If the displayed envelope does not parse or verify, the relay verifies the body the engine stored for that message id instead: read-only, only a message from the cloud session to this relay, not held, exactly one row. Freshness, nonce, rate and address checks still apply, and an altered message is still refused (#11).
- Installer: two more pipelines that could stop early under `set -o pipefail` no longer report a false failure (the team lookup and the signing-key check during `--rotate-key`), as for the cloud connection check in v0.4.1 (#24).
- Installer: team and machine names with repeated hyphens (such as `a--b`) are refused before anything is downloaded. The engine merged the hyphens, so the team ended up with another name than the one the installer and the prompts used (#25).
- `box/rn-cloud-setup.sh` can run again on a cloud that already joined the team: when the full invite link shows the same team and the cloud is already `<team-name>/cloud`, it skips `join` instead of stopping there. A short invite code cannot show this; the engine then decides as before, and the error names the cause.

### Added

- `box/rn-fetch.py send-status` prints only the status words of an `rn send` output read on stdin; `rn-cloud.sh send` uses it.
- `install.sh --relay-name <name>` chooses the Bot address name: the relay listens as `<team-name>/<name>`, the cloud uses `<name>-cloud` and `<name>-inbox`. Default `bot`; 1–34 lowercase letters and digits joined by single hyphens, not `terminal`. The name cannot be changed, also not for an unfinished installation. The installer refuses a name already used in the team, writes the relay settings atomically, and checks that the engine registered exactly that address; `check.sh` reports the name and the registration; `update.sh` and the deployment record refuse a changed name. The routine and cloud setup prompts take the name; with `bot` the routine prompt is byte-for-byte the same as in v0.4.1. On the cloud, `rn-cloud-setup.sh` records the name in `box/relay`, and `rn-cloud.sh` and `rn-fetch.py` read it (`bot` without the file; an empty or damaged file stops them) and check after each registration that the engine used exactly that name.
- Regression test for a relay stopped after the engine accepted an RN-OUT and before the result was saved: after the restart the send is reported once as unknown and not sent again (#14).
- `box/rn-cloud-uninstall.sh` and the prompt `prompts/cloud-uninstall.md`: the uninstaller fills in the cloud script and embeds it, with its SHA-256 checksum, in the prompt for Grok Bot. On `[BOT-CLOUD]` the script checks the team, the cloud's machine name, `box/person` and `box/relay`, and that the Mac is online; `check` changes nothing and prints a `PROOF` value made with the deployment's signing key; `apply <code>` runs only with the code the Mac shows for a matching `PROOF`, leaves the team and removes the cloud's identity, key and names. It looks for other programs that use `box/` first (`SHARED:` lines, `accept-shared` only with the user's consent) and, at level `all`, lists the user's own files before removing `inbox/`, `threads/` and the clone; it keeps the clone when Git cannot list everything or a local branch, tag or `HEAD` has commits that no remote branch has. References to `agent-mesh/box` anywhere under `/workspace`, also relative ones, and files too large to check count as shared. A cloud identity whose engine is missing ends with `INCOMPLETE` instead of `DONE`.

### Changed

- `./uninstall.sh` is now a tiered uninstaller for both computers. Without options it only lists what is installed, including the team, the Bot address names, the queue, the dsh bridge and deployments it did not install; `--plan` shows what each level removes and keeps. `--level program` removes only the relay program and keeps the identity, settings, signing key, queue and logs, so `./install.sh --restart-relay` brings it back without touching the cloud. `--level identity` also leaves the team on both computers and removes keys and identity; `--level all` (`--all`) also removes logs, the engine, the dsh backups and the cloud's data. The deeper levels run only in a terminal, ask for the team name (`delete <team-name>` for `all`), stop before changing anything when names disagree or a path is unsafe, never touch other relays or login services (`--plan` shows the `I accept` gate; processes are shown without arguments), never stop a `com.local.agent-relay` service loaded from another home, and run the cloud step before this Mac leaves the team. A symlinked dsh temporary file stops them before dsh is touched, and `dsh/mac-uninstall.sh` no longer writes through one. `--residual` cleans up the cloud after an older `--all`; `--cloud-prompt` repeats the cloud step.
- `./uninstall.sh` without options no longer removes anything, and `-y` works only with `--level program`: `./uninstall.sh -y` and `./uninstall.sh --all -y` now stop with a usage error.

### Upgrade notes

The relay and the cloud scripts changed; the routine prompt is unchanged. Run `git pull`, then `./update.sh --lang <en|zh-CN>` in a terminal, and follow its guided steps. Until the cloud scripts are updated, the old cloud classification stays in effect, including for team names that contain `held`. Check that `WEBHOOK_URL` is the final address before updating the relay.

The uninstaller changed: scripts or notes that run `./uninstall.sh -y` or `./uninstall.sh --all -y` need `--level program --yes` or a run of `--level identity` / `--level all` in a terminal; see the README's Uninstall section. Run `git pull` before uninstalling an older installation.

Existing installations keep the Bot address name `bot` and need no migration. The cloud setup script changed, so `update.sh` asks once whether the cloud side is set up for this version: answer yes. The next `./install.sh --webhook` adds the three name lines to the relay settings without changing their values. An installation with another Bot address name cannot go back to an earlier version: older cloud scripts ignore `box/relay` and send to `<team-name>/bot`. Renaming is not supported in any case; a different name needs a full uninstall and a new install.

## v0.4.1 — 2026-11-08

The cloud script no longer reports the Mac offline when it is connected.

### Fixed

- `box/rn-cloud.sh`: the connection check used `doctor | grep -q` under `set -o pipefail`. When `grep` stopped at the first match, `doctor` could be killed by SIGPIPE and the check failed for about 90 seconds although the Mac was connected, so `send`, `who` and `init-inbox` stopped with "the Mac is not connected" and `fetch` skipped re-forward requests. The check now reads all of the `doctor` output; a failing `doctor` still counts as not connected.

### Changed

- README: the uninstall section now gives an ordered plan for both computers and the routine, says what is shared and must be checked first, and how to confirm that everything is gone.

### Upgrade notes

The relay and the routine prompt are unchanged; only the cloud script changed. Run `git pull`, then `./update.sh --lang <en|zh-CN>` in a terminal, and follow its guided steps to have Grok Bot pull the new commit.

## v0.4.0 — 2026-11-06

`./update.sh` updates an existing installation without reinstalling.

### Added

- `./update.sh` compares the relay, the dsh plugin, the cloud scripts and the routine prompt with what is deployed. It updates the relay and the dsh plugin itself; when the cloud scripts or the prompt change, it guides you in a terminal through stopping the relay, pausing the routine, having Grok Bot pull the exact local commit, pasting the new prompt, resuming the routine and starting the relay. `--plan` shows the comparison only; interrupted updates resume where they stopped. What was confirmed is recorded in `~/.local/lib/agent-mesh/deployed.json`.
- `./install.sh --restart-relay` installs this checkout's relay and restarts it; if the new relay does not come up, the previous one is restored.
- `dsh/mac-install.sh --plugin-only` replaces only the installed plugin file and keeps `~/.dsh/cordis.patch.yml`.

### Changed

- Every relay (re)start by `install.sh` now backs up the installed relay first, waits for the new one to report that it is up, and restores the previous files if it does not.
- `install.sh` records the cloud scripts and the routine prompt it prints as pending; `./update.sh` asks whether they were applied. `./uninstall.sh` removes the record with the relay.
- `dsh/mac-install.sh` also restores the previous plugin, not only the patch, when a step fails.

### Upgrade notes

The relay, the cloud scripts and the routine prompt are unchanged. `update.sh` is new, so run `git pull` in the local repository once, then `./update.sh --lang <en|zh-CN>` in a terminal. With no record yet, it treats the cloud scripts and the prompt as unknown and runs the guided steps once, and asks whether the cloud side is set up for this version (no cloud setup migration is needed since v0.1.0, so a working installation can answer yes). From then on, `./update.sh` covers every upgrade.

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

Reliable delivery release: per-message isolation in the relay, idempotent cloud fetch, receipts with re-rings, and reply deduplication.

### Added

- Cloud receipts and relay re-rings (#7): after each fetch the cloud sends the relay a signed receipt (new format RN-GOT v1); the relay tracks every forwarded message, rings again when it is not stored or handled in time, and alerts the user after repeated failures. Alerts must be confirmed by the routine and survive spool overflow.
- A reply resent by the cloud is delivered only once (#8): a reply with `reply_to` gets a nonce derived from its content, and the relay records the result of every nonce; a repeat of a sent reply is dropped, and one with an unknown result is not resent.
- Cloud orphan scan: messages whose doorbell was lost and that nobody handled are handed out on the next fetch.
- New commands `rn-cloud.sh working` and `rn-cloud.sh done`.
- Relay settings `GOT_TIMEOUT_SECONDS`, `GOT_DONE_SECONDS`, `GOT_MAX_RERINGS` and `NONCE_KEEP_SECONDS`; cloud settings `RN_CLAIM_LEASE`, `RN_ORPHAN_DAYS`, `RN_ORPHANS` and `RN_GOT`.
- CI: offline checks run on Linux and macOS for every push and pull request.

### Changed

- The cloud hands each message to one routine turn, with new statuses `handled`, `claimed` and `uncertain`; an interrupted message is left to the user instead of being redone.
- The relay handles messages one by one, so one failure no longer affects the rest of its batch.
- At startup the relay recovers messages the local engine marked read but that were never handled.
- A re-forward request from a new cloud no longer rings an extra doorbell.
- Proxied-send problems such as refused or unknown results are reported as relay alerts instead of spool notices.
- The routine prompt follows the behavior above.

### Fixed

- The relay treats every HTTP 2xx webhook response as delivered; before, only 200 counted, which caused repeated wakes.
- A proxied send that times out no longer ends the whole batch and is reported as "not confirmed" (#1, #5).

### Upgrade notes

The relay, the cloud scripts and the routine prompt must be updated together, in the [order given in the README](README.md#upgrade).

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
