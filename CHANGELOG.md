# Changelog

**English** | [简体中文](CHANGELOG.zh-CN.md)

## Unreleased

- Treat every HTTP 2xx webhook response as delivered, including 202 and 204, so accepted batches are removed from the spool instead of being sent again. Failed requests remain queued for retry.

- Added an English and Chinese [security policy](SECURITY.md): private vulnerability reporting, scope and upstream ownership, how reports are handled, and steps after a possible leak of the signing key, webhook key or an invite link. The README's security section and documentation table link to it.

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
