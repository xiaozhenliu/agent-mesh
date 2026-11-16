# Security policy

**English** | [简体中文](SECURITY.zh-CN.md)

## Supported versions

Only the latest release receives security fixes. The current version is in [VERSION](VERSION); on `[LOCAL]`, `./install.sh --version` also prints it. Fixes ship in a new release and are not backported.

## Reporting a vulnerability

Report privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability**, or go to <https://github.com/xiaozhenliu/agent-mesh/security/advisories/new>. Only you and the maintainers can see the report.

Do not open a public issue, discussion or pull request for a security problem, and do not post details in public chats.

**Do not include real credentials** in a report: the signing key `outbound.key`, the Webhook URL and key, team invite links or codes. Remove them from logs before attaching. If a credential has already been exposed, follow [If you suspect a credential leak](#if-you-suspect-a-credential-leak) first, then report.

## What to include

- The agent-mesh and Reason Network versions (`./install.sh --version` prints both).
- The affected component and where it runs: `[LOCAL]`, `[BOT-CLOUD]` or `[GROK-UI]`.
- Steps to reproduce, preferably offline, for example as a test case based on `tests/relay/` or `tests/cloud/`.
- Impact: what an attacker needs, and what they can achieve.
- Any mitigation you already know of.

## Scope

### In scope

- The `[LOCAL]` relay `agent_relay.py`, including signature checks, freshness and replay protection, proxy address restrictions, rate limits, and storage permissions of the spool and forwarded copies.
- The `[BOT-CLOUD]` scripts `box/rn-cloud-setup.sh`, `box/rn-cloud.sh`, `box/rn-fetch.py`, `box/rn-engine.py`, `box/rn-cloud-update.sh`, `box/rn-cloud-add-bot.sh` and `box/rn-cloud-uninstall.sh`.
- The install, self-check and uninstall scripts `install.sh`, `check.sh`, `uninstall.sh`, `scripts/`, and the dsh plugin in `dsh/`.
- The message formats in the [protocol reference](PROTOCOL.md#message-formats): RN-OUT, RN-FWD, RN-REFWD and RN-REFWD-ACK.
- Prompts and templates that the installer renders or copies: `prompts/` and `templates/`. Examples: a prompt that leads Grok Bot to perform an action it should first ask the user about, or that fails to tell it to treat incoming message text as external data.

### Report upstream

- The Reason Network engine, iroh relays and the team join mechanism: [Reason Network](https://github.com/reason-machines/reason-network).
- Grok Bot, routines, the webhook service and CopyToBox: xAI.
- Permission and approval behavior of Claude Code, Codex, pi and DeepSeek Harness: their respective developers.

If you are unsure where a problem belongs, report it privately here anyway and we will help forward it.

### Known limitations, not treated as vulnerabilities

The [Security and privacy](README.md#security-and-privacy) section already documents these:

- Grok Bot is the trust root: if content it reads in a web page or document steers it (prompt injection), it can send validly signed messages. Signatures only stop impersonation by sessions without the key.
- Any program running as the same macOS user can read the keys and webhook key in `~/.config/agent-relay/`.
- A local agent configured to approve automatically (for example Claude Code's `--dangerously-skip-permissions` or Codex's `--yolo`) acts on incoming text as if it were a command, including when it is woken unattended.
- Message bodies are stored in plain text in both engines' databases, the relay's copies and the cloud inbox; see [Message bodies are stored in plain text](README.md#message-bodies-are-stored-in-plain-text) in the README for retention and cleanup.
- Traffic may pass through iroh public relays, which this project has not independently audited.
- Each wake-up consumes Grok Bot usage; the relay only rate-limits it.
- Any team member can message and wake Grok Bot; the relay does not restrict senders within the team. Who joins the team is the boundary: `./check.sh` reports any member other than your local computer and `cloud`.

With several Grok Bots on one Mac ([Several Grok Bots](README.md#several-grok-bots)), these also apply:

- The Bots share one trust domain. Each Bot's routine can read everything on the cloud computer, including the signing key, and could act as another Bot; the shared cloud account, engines, databases and signing key do not isolate the Bots from each other. Faults in the engine, a database or the shared scripts affect all Bots at once.
- The engine counts one outbox for the whole cloud. Another Bot's queued messages can leave a delivered reply at `replying`, later shown as unknown; such a reply is not sent again automatically.
- `./install.sh --rotate-key` replaces the key of every Bot and restarts every relay; if a restart fails, the new key is not rolled back. Bodies signed with the old key, and the window until the cloud has the new key, remain as with one Bot.
- `rn-cloud.sh purge` is refused while the cloud has more than one Bot.
- A Bot's address name cannot be changed: add a Bot with the new name and remove the old one. A removed name cannot be used again.
- The cloud maintenance lock protects single commands only, not the model's work between commands or its direct reads and writes of thread notes. During a cloud update other commands wait up to 600 seconds for it, then fail without changing anything; the routine tells the user, and the next wake-up handles the mail.
- At the first update from v0.8.2 or v0.8.3 to v0.9.0, the old cloud checkout and old commands already running do not respect the maintenance lock. This affects only single-Bot installations, which is all those versions support.
- Management commands of an older and this version started at the same time are not fully kept apart. Older management tools cannot manage several Bots.
- If waiting for an added Bot's cloud registration times out, the wake request fails, the model does not run step 0 of its routine prompt, or Grok usage runs out, the Bot stays pending and its relay does not start; `./install.sh --relay-name <name> --wake-bot` tries again. It is never recorded as added.
- A registered relay does not prove that the matching Grok routine works. If the routine of the Bot that carries the cloud update request is broken, `./update.sh` waits until it times out; `./update.sh --manual` is the way out.
- Any team member can wake any of the Bots, as with one Bot.

A way around the documented protections, such as bypassing signatures, freshness, replay protection, address restrictions or rate limits, is still in scope.

## How reports are handled

This project is maintained by one person on a best-effort basis, with no fixed response time.

1. Acknowledge the report and decide whether it is in scope.
2. Reproduce it in a private security advisory, working with the reporter.
3. Ship the fix in a new release. If the fix touches `[BOT-CLOUD]` or the routine prompt, the release notes say which side users need to update.
4. Publish the advisory after the release, crediting the reporter if they wish.

## If you suspect a credential leak

Run these on `[LOCAL]` unless marked otherwise.

**Signing key `outbound.key`**: generate a new key and restart the relay (with several Bots, the one key is shared and every relay restarts):

```bash
./install.sh --rotate-key --lang en
```

Then follow the steps it prints: in `[GROK-UI]`, have Grok Bot use CopyToBox to copy the new key from `[LOCAL]` `~/.config/agent-relay/outbound.key` to `[BOT-CLOUD]` `/workspace/agent-mesh/box/outbound.key` and run `chmod 600` on it, as in step 3 of the [cloud setup prompt](prompts/cloud-setup.md). Until the cloud has the new key, signature checks fail in both directions. After the copy, run `./check.sh` and send a test message. Messages forwarded before the rotation that the cloud has not fetched no longer verify, and `fetch` reports them as `mismatch`; they may have been forged with the leaked key, so ask the senders to resend and clean up the old messages as described in [Message bodies are stored in plain text](README.md#message-bodies-are-stored-in-plain-text).

**Webhook URL or key**: in `[GROK-UI]`, regenerate the routine's key, or delete and recreate the routine if the interface does not offer that. Then run `./install.sh --webhook --lang en` on `[LOCAL]` and enter the new values; with several Bots, add `--relay-name <name>` for the Bot whose routine it is.

**Invite link or code**: run `reason-network team` (`./check.sh` checks it too) and confirm that only your own local computer and `cloud` are listed. Remove any unknown person or machine with `reason-network remove <person>/<machine>`, then replace the signing key as above.

**Grok Bot may have been steered into unexpected actions**: pause the routine in `[GROK-UI]` first. To review each incoming message, set `[inbound] default = "hold"` in `~/.reason/network/config.toml` and release messages with `reason-network approve`. Clean up message bodies you no longer need as described in [Message bodies are stored in plain text](README.md#message-bodies-are-stored-in-plain-text).
