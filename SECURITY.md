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
- Steps to reproduce, preferably offline, for example as a test case based on `test_relay.py` or `box/test_rn_fetch.py`.
- Impact: what an attacker needs, and what they can achieve.
- Any mitigation you already know of.

## Scope

### In scope

- The `[LOCAL]` relay `agent_relay.py`, including signature checks, freshness and replay protection, proxy address restrictions, rate limits, and storage permissions of the spool and forwarded copies.
- The `[BOT-CLOUD]` scripts `box/rn-cloud-setup.sh`, `box/rn-cloud.sh` and `box/rn-fetch.py`.
- The install, self-check and uninstall scripts `install.sh`, `check.sh`, `uninstall.sh`, `scripts/`, and the dsh plugin in `dsh/`.
- The message formats in the [README appendix](README.md#appendix-message-formats): RN-OUT, RN-FWD, RN-REFWD and RN-REFWD-ACK.
- Prompts and templates that the installer renders or copies: `prompts/` and `templates/`. Examples: a prompt that leads Grok Bot to perform an action it should first ask the user about, or that fails to tell it to treat incoming message text as external data.

### Report upstream

- The Reason Network engine, iroh relays and the team join mechanism: [Reason Network](https://github.com/reason-machines/reason-network).
- Grok Bot, routines, the webhook service and CopyToBox: xAI.
- Permission and approval behavior of Claude Code, Codex, pi and DeepSeek Harness: their respective developers.

If you are unsure where a problem belongs, report it privately here anyway and we will help forward it.

### Known limitations, not treated as vulnerabilities

The [Security and privacy](README.md#security-and-privacy) section already documents these:

- Any program running as the same macOS user can read the keys and webhook key in `~/.config/agent-relay/`.
- A local agent configured to approve automatically acts on incoming text as if it were a command.
- Traffic may pass through iroh public relays, which this project has not independently audited.
- Each wake-up consumes Grok Bot usage; the relay only rate-limits it.

A way around the documented protections, such as bypassing signatures, freshness, replay protection, address restrictions or rate limits, is still in scope.

## How reports are handled

This project is maintained by one person on a best-effort basis, with no fixed response time.

1. Acknowledge the report and decide whether it is in scope.
2. Reproduce it in a private security advisory, working with the reporter.
3. Ship the fix in a new release. If the fix touches `[BOT-CLOUD]` or the routine prompt, the release notes say which side users need to update.
4. Publish the advisory after the release, crediting the reporter if they wish.

## If you suspect a credential leak

Run these on `[LOCAL]` unless marked otherwise.

**Signing key `outbound.key`**: generate a new key and restart the relay:

```bash
( umask 077; openssl rand -hex 32 > ~/.config/agent-relay/outbound.key )
launchctl kickstart -k "gui/$(id -u)/com.local.agent-relay"
./check.sh
```

Then, in `[GROK-UI]`, have Grok Bot use CopyToBox to copy the new key from `[LOCAL]` `~/.config/agent-relay/outbound.key` to `[BOT-CLOUD]` `/workspace/agent-mesh/box/outbound.key` and run `chmod 600` on it, as in step 3 of the [cloud setup prompt](prompts/cloud-setup.md). Until the cloud has the new key, signature checks fail in both directions.

**Webhook URL or key**: in `[GROK-UI]`, regenerate the routine's key, or delete and recreate the routine if the interface does not offer that. Then run `./install.sh --webhook --lang en` on `[LOCAL]` and enter the new values.

**Invite link or code**: run `reason-network team` and confirm that only your own local computer and `cloud` are listed. Remove any unknown person or machine with `reason-network remove`, then replace the signing key as above.

**Grok Bot may have been steered into unexpected actions**: pause the routine in `[GROK-UI]` first. To review each incoming message, set `[inbound] default = "hold"` in `~/.reason/network/config.toml` and release messages with `reason-network approve`.
