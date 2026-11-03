# Changelog

**English** | [简体中文](CHANGELOG.zh-CN.md)

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
