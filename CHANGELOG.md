# Changelog

**English** | [简体中文](CHANGELOG.zh-CN.md)

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
