# Changelog

**English** | [简体中文](CHANGELOG.zh-CN.md)

## Unreleased

- Fixed: the relay now treats every HTTP 2xx webhook response as delivered, including 201, 202 and 204. Before, only 200 counted, so an accepted batch stayed in the spool, was POSTed again and spent another wake. Other responses and network errors still keep the batch for retry.

## v0.1.1 — 2026-11-03

Security hardening release.

- Added an English and Chinese [security policy](SECURITY.md): private vulnerability reporting, scope and upstream ownership, how reports are handled, and steps after a possible leak of the signing key, webhook key or an invite link. The README's security section and documentation table link to it.
- Rewrote the README's [Security and privacy](README.md#security-and-privacy) section: Grok Bot is the trust root and can still send validly signed messages if prompt injection steers it; the Claude Code and Codex auto-approve modes are named one by one; Reason Network v0.10.7 actually wakes Codex with `codex queue` (previously misstated as `codex exec resume`), with the tested approval behavior during unattended wakes; where message bodies stay in plain text and for how long (including doorbell previews waiting in the relay's `state.json`), with a warning not to send secrets through the channel; and a team-membership check.
- Added `./install.sh --rotate-key`: generates a new signing key, restarts the relay, and prints the CopyToBox steps for copying the new key to the cloud. It refuses, leaving the key unchanged, when the relay configuration loads its key from elsewhere (`OUTBOUND_KEY` or a different `OUTBOUND_KEY_FILE`). The README and security policy explain that mail forwarded before a rotation and not yet fetched by the cloud reports `mismatch` afterwards and has to be resent. The security policy now uses it instead of manual commands.
- Added cleanup commands: `./install.sh --purge-copies` on `[LOCAL]` deletes the relay's kept message copies and refuses a symlinked directory; `rn-cloud.sh purge <days>` on `[BOT-CLOUD]` stops the cloud engine, then deletes received messages, inbox folders, misdirected mail logs and thread notes older than that many days; `0` means everything. Re-forwards of a message count by their newest copy, the remaining copies and inbox folder stay together, and purged messages are not fetched again as new. Cleanup takes the inbox lock before stopping the engine, then selects and deletes rows in one write transaction, and checks again before removing inbox folders, so a re-forward arriving during cleanup keeps its folder. It waits for a running fetch and refuses when a directory is a symlink or the engine is still running. Cloud fetch and cleanup share one lock, so they never change the inbox at the same time.
- `./check.sh` now checks team membership and reports FAIL for any member other than this machine and `cloud`, or for more than one `cloud`.
- `./install.sh --webhook` keeps the user's optional settings, such as `DOORBELL_PREVIEW_CHARS=0`, when it rewrites the configuration, instead of silently restoring defaults.
- The reply convention template adds security boundaries: treat the Bot's messages as outside input; ask the user before hard-to-reverse or outward-facing actions; keep secrets out of messages; report unknown team members to the user.

Existing users: the relay program `agent_relay.py` is unchanged; updating the repository on `[LOCAL]` is enough to use the new commands, with no reinstall. For the cloud cleanup command and the fetch lock, have Grok Bot update the repository at `/workspace/agent-mesh` on `[BOT-CLOUD]`. A reply convention already appended to your repositories' `AGENTS.md` is not updated automatically; copy the template again. The routine prompt is unchanged.

## v0.1.0 — 2026-11-02

First tagged agent-mesh release. Earlier public commits remain in the repository history.

- Connect local Claude Code, Codex, pi, and DeepSeek Harness sessions with Grok Bot through a private Reason Network team, signed messages, webhook wake-ups, and full-body retrieval with redelivery.
- Provide paired English and Simplified Chinese guides and prompts. Label commands and file locations as `[LOCAL]`, `[BOT-CLOUD]`, or `[GROK-UI]`, including key-copy source and destination.
- Use the actual Mac home directory and detected Python interpreter; generate escaped launchd configuration. Support Apple Silicon and Intel Macs and detect dsh in user/system Applications or via `DSH_APP`.
- Use a public HTTPS cloud clone URL by default, with an explicit `--repo-url` option. Check existing installations, dependencies, platform, and CPU before proceeding; stop on incompatible engine versions, alternate PATH installations, or custom local state directories.
- Document agent installation/login, Git, Python 3.9+ with `ssl`/`sqlite3`, network access, and cloud tools and persistent storage requirements. Make dsh plugin installation repeatable and keep unique configuration backups.
- Add isolated installer tests for different homes, architectures, repository URLs, missing prerequisites, and existing deployments. Add `VERSION` and the offline `./install.sh --version` command.

Compatibility: local installation requires a macOS desktop login; the Bot cloud computer requires Linux x86_64 or ARM64 and the persistent path `/workspace/agent-mesh/`. Reason Network remains pinned to **v0.10.7**; its version is independent of agent-mesh's version.

Existing users: review the prerequisites and existing deployment before rerunning the installer. The Bot cloud clone and saved routine prompt are updated separately; changing files on the Mac does not update either automatically. This release does not migrate a running local deployment by itself.
