# reason-network-bridge (dsh plugin)

**English** | [简体中文](README.zh-CN.md)

This host plugin lets DeepSeek Harness (dsh) sessions receive Reason Network messages automatically, like Claude Code, Codex, and pi. It has no package dependencies; it resolves the required dsh modules from the installed app at runtime.

Reason Network's built-in setup supplies a shared MCP address and requires sessions to check their inbox. This plugin adds a separate address for each session and automatic wake-ups.

## Behavior

- Mounts a Reason Network MCP for each top-level session, skipping subagents, with `REASON_NETWORK_SESSION=<dsh-session-id>`. Each gets an address based on the folder name plus `-dsh`, visible in `reason-network who`.
- When idle: runs `reason-network hook dsh poll` immediately on becoming idle and every five seconds thereafter; a new message starts a turn with `followup`.
- When busy: runs `hook dsh post-tool-use` near the end of a turn and injects interrupt-marked messages with `steer`.
- Archived sessions stop receiving, unmount their MCP, and release the address. Messages remain unread for the next session using the address in that folder. Unarchiving restores reception.
- Delivers messages only when the sender's person name exactly matches an entry in `allowPeople`. In this setup, that name equals the team name. Without the allowlist, the plugin refuses to load. Rejected messages are logged with id, sender, and the first 200 body characters.
- Logs to `~/.dsh/logs/reason-network-bridge.log`, mode 600, rotating after 1 MB.

## Install (macOS desktop)

First install the Mac side using the [main guide](../README.md), then run from the distribution root:

```bash
dsh/mac-install.sh <team-name>
```

The script runs tests with the app's runtime, backs up `~/.dsh/cordis.patch.yml` to `~/.dsh/backup-reason-network-bridge/`, copies the plugin to `~/.dsh/plugins/`, and replaces the Reason Network MCP block with the configuration below. If a step fails after backup, it restores the patch. **Quit dsh completely (⌘Q) and reopen it**; the desktop app does not hot-reload plugins.

```yaml
- insert:
    - id: reason-network-bridge
      name: '/Users/<mac-user>/.dsh/plugins/reason-network-bridge.mjs'
      config:
        serverName: reason-network
        command: '/Users/<mac-user>/.local/bin/reason-network'
        allowPeople:
          - '<team-name>'
```

With `serverName: reason-network` and `command` present, engine setup and upgrades recognize the existing integration and do not reinsert the original block. It is expected for `doctor` to show dsh as skipped. Optional settings: `pollMs` (default 5000), `hookTimeoutMs`, `toolCallTimeoutMs` (default 660000), `mountTimeoutMs`, and `logFile`.

## Verify

- Run `reason-network who`. Each dsh session should have its own line resembling `<folder>-dsh  dsh … turn`. Although it reports `turn`, the plugin handles idle messages within roughly five seconds.
- Send `reason-network send <team-name>/<folder>-dsh "Self-test: please confirm receipt"`. It should appear as a user message with a trailing `From <team-name>… Reply if useful with …` line, and dsh should reply.
- Check the log for `attached session=…`, delivery via `followup …` or `steer …`, and allowlist rejections via `dropped …`.

## Known limitations

- Multiple sessions in one folder receive suffixes such as `<folder>-dsh-2`; messages to the unsuffixed address reach only the first.
- Messages are delivered near the end of a turn or while idle, not halfway through a tool call.
- Restart the app completely after changing the plugin file.
- Incoming messages trigger agent work. Keep `allowPeople` limited to your own identity. dsh's permissions and sandbox settings still apply.

## Roll back

Copy the latest backup from `~/.dsh/backup-reason-network-bridge/` to `~/.dsh/cordis.patch.yml`, then quit and reopen the app. To restore the engine's original integration, run `reason-network setup`.

## Files

- `reason-network-bridge.mjs`: plugin.
- `reason-network-bridge.test.mjs`: offline tests run before installation. Manually: `ELECTRON_RUN_AS_NODE=1 "/Applications/DeepSeek Harness.app/Contents/MacOS/DeepSeek Harness" dsh/reason-network-bridge.test.mjs`.
- `mac-install.sh`: installer.
