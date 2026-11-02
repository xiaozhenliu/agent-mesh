# agent-mesh

**English** | [简体中文](README.zh-CN.md)

Connect your local Claude Code, Codex, pi, and DeepSeek Harness sessions with a cloud Grok Bot in one private [Reason Network](https://github.com/reason-machines/reason-network) team. Either side can start a conversation, and replies wake the other side. macOS, Reason Network v0.10.7, Python 3, MIT. No Reason account, separately managed server, public IP, Docker, or separate Node installation is required.

To let an agent install it, give Claude Code or Codex this repository and say: **“Read [AGENT-INSTALL.md](AGENT-INSTALL.md) and install it.”**

## Documentation and language

| Guide | English | 简体中文 |
|---|---|---|
| User guide | [README](README.md) | [使用指南](README.zh-CN.md) |
| Agent installation | [Runbook](AGENT-INSTALL.md) | [安装手册](AGENT-INSTALL.zh-CN.md) |
| DeepSeek Harness | [Bridge](dsh/README.md) | [插件指南](dsh/README.zh-CN.md) |
| Grok Bot routine | [Prompt](prompts/routine.md) | [提示词](prompts/routine.zh-CN.md) |
| Cloud setup | [Prompt](prompts/cloud-setup.md) | [提示词](prompts/cloud-setup.zh-CN.md) |
| Reply convention | [Template](templates/AGENTS.reason-network.md) | [模板](templates/AGENTS.reason-network.zh-CN.md) |
| Maintaining translations | [Contributing](CONTRIBUTING.md) | [维护说明](CONTRIBUTING.zh-CN.md) |

Use `--lang en` or `--lang zh-CN` on each installer invocation to select generated prompts. English is the default; `AGENT_MESH_LANG` sets a shell-level default, and `--lang` overrides it. Terminal diagnostics, configuration keys, and protocol fields remain English. Agents speak to users in their preferred language. Language support does not change operating-system or network requirements.

## When to use it

- If Grok Bot can finish the work on its cloud computer—running scripts, doing research, or writing files—install the relevant CLIs there. You do not need this project.
- Use agent-mesh when Grok Bot needs to collaborate with **already-open sessions on your own computer**: edit a local repository, use your subscriptions, or continue a long conversation. A command such as `claude -p` starts a new process; it does not reach the session already doing the work.

## How it works

There are three roles:

- **Mac, kept awake:** runs the engine and relay. The relay listens as `<team-name>/bot`, representing Grok Bot to local agents.
- **Grok Bot's cloud computer, which may sleep:** starts the engine on demand during a turn. It sends as `<team-name>/bot-cloud`, receives full bodies as `<team-name>/bot-inbox`, and uses the machine name `cloud`. No always-on service is installed there.
- **Routine:** a webhook-triggered task in Grok Bot. A POST from the relay wakes it.

```text
Local agent → Mac relay → full body over Reason Network (RN-FWD) → cloud bot-inbox
                    └── webhook: id, length, sha256 → routine wakes → rn-cloud.sh fetch

Grok Bot → signed RN-OUT → Mac relay sends as <team-name>/bot → local session
                                ↑ replies return here, even when the cloud sleeps
```

Four design choices make this work:

1. **The always-on machine handles replies.** The Mac sends every cloud-originated message on Grok Bot's behalf, so replies return to the Mac rather than a sleeping cloud computer.
2. **The webhook is a doorbell.** In the original deployment, Grok Bot retained roughly the first 4,000 characters of a wake-up body. Full text therefore travels over Reason Network; the webhook carries an id, length, checksum, and optional preview. The routine fetches and verifies the body. A 10,000-character message can use one wake-up, within the engine's byte limit.
3. **The receiver requests redelivery.** The relay keeps forwarded bodies for seven days. If `fetch` cannot retrieve one, it requests a signed re-forward, at most twice per message. This also recovers a failed initial forward.
4. **Relay protocol messages are signed.** A team session can claim another sender name. Proxy envelopes, forwarded bodies, and re-forward requests therefore use HMAC signatures. Envelopes and requests have a 15-minute freshness window and replay protection. The signing key has one copy on each machine.

## Prerequisites

- macOS with Python 3 available; install Xcode Command Line Tools if needed. Ensure `~/.local/bin` is in `PATH`.
- Grok Bot must support webhook-triggered routines and CopyToBox from your Mac.
- Keep the Mac powered on and awake. Enable the setting that prevents automatic sleep on power, or run `caffeinate`. Closing the lid stops everything.
- Choose a team name, also used as your person name: 1–40 lowercase ASCII letters, digits, or hyphens; no leading or trailing hyphen. Use a pseudonym. Renaming requires recreating the team.
- Check connectivity to `*.iroh.link`, especially with a VPN or proxy. This probe should return HTTP 200:

  ```bash
  curl -sS -o /dev/null -m 10 -w 'code=%{http_code}\n' https://aps1-1.relay.n0.iroh.link/
  ```

  If it fails, see [Troubleshooting](#troubleshooting). The probe checks one endpoint, not worldwide availability.

## Install

Replace the repository URL and team-name placeholders:

```bash
git clone <repository-url> ~/agent-mesh
cd ~/agent-mesh
./install.sh --name <team-name> --lang en
```

These commands assume a clone of the public distribution. Use the same public repository URL for the cloud clone; its root must contain `box/rn-cloud-setup.sh`.

The installer handles the Mac side and pauses for user actions:

1. Downloads Reason Network v0.10.7, verifies SHA-256, creates your private team, and disables auto-update and LAN broadcasts.
2. Shows `reason-network setup --dry-run` and **waits for confirmation** before wiring local agents.
3. Generates the signing key and prints a routine prompt. **Create a webhook-triggered routine in Grok Bot and paste that prompt.** Paste its Webhook URL and key into your own terminal. The installer writes the configuration and installs the relay as a login service.
4. Prints a team invite and cloud setup prompt. **Send both to Grok Bot.** It clones the repository on its cloud computer, joins the team, and requests approval to copy the signing key with CopyToBox.

Reopen Claude Code and Codex sessions that were already running. Codex asks once on first launch; choose “Trust all and continue”.

Optional:

- dsh automatic wake-ups: run `dsh/mac-install.sh <team-name>`; see the [bridge guide](dsh/README.md).
- Reply convention: append the [English template](templates/AGENTS.reason-network.md) or [Chinese template](templates/AGENTS.reason-network.zh-CN.md) to your working repository's `AGENTS.md`. For Claude Code, also add `@AGENTS.md` to `CLAUDE.md`. The template asks for a conclusion followed by four concise status fields.

## Verify

- Run `./check.sh`. This is read-only; each `FAIL` includes a suggested fix.
- Send `reason-network send <team-name>/bot "test 1"`, then run `reason-network listen --as terminal --wait 600` in another terminal for the reply. Look for `forwarded` and `POST … code=200` in `~/Library/Logs/agent-relay.log`.
- Ask Grok Bot to run `who` and send a sentence to a specific Claude Code session. The session should wake even if idle; its reply should wake Grok Bot again.
- Send a 10,000-character message. Expect one wake-up and a `complete` fetch result with matching length and SHA-256. The signed message must fit within the relay's 60 KiB forwarding limit; UTF-8 byte size depends on the text.

## How agents receive messages

The last column of `reason-network who` shows the delivery mode:

- **push:** wakes an idle session. Claude Code receives an injected message; Codex uses `codex exec resume`; pi polls through an extension every five seconds.
- **turn:** normally waits for a tool call or the end of a turn. dsh with the bridge reports `turn`, but starts a turn within roughly five seconds when idle.
- **pull:** does not wake automatically. dsh without the bridge must check its inbox.

Closed sessions disappear from the roster. Messages remain in the inbox while the recipient is offline. After reopening, you have to tell the agent to check its inbox before it reads them. Keep a session open for prompt delivery.

## Security and privacy

- This bridge sends text to a local agent; it does not itself give Grok Bot direct file access or bypass the agent's permissions. The Mac opens no inbound port. Grok Bot's local always-approve and local-network switches are not required.
- An agent configured to approve automatically may act on incoming text without asking you. Use an appropriately restricted workspace and permissions for these sessions.
- Signatures prevent impersonation by sessions without the key. Agents running as the same macOS user may still read the keys in `~/.config/agent-relay/`. A project folder is not an access-control boundary.
- To hold incoming messages, set `[inbound] default = "hold"` in `~/.reason/network/config.toml`, then use `reason-network approve` to release them.
- Team members can see session names, the last working-directory component, repository names, and branches. Avoid starting agents directly in your home directory. Use invite links in the terminal, not a browser.
- The pinned open-source engine runs locally in account-free mode, and in that mode it sends no data to Reason. Traffic may pass through iroh public relays; upstream describes the content as encrypted. This project has not independently audited those relays. `reason-network relay <URL>` selects a self-hosted relay.
- Each Grok Bot wake-up consumes usage. The relay defaults to at most 20 wake-ups per hour.

## Troubleshooting

- **Cannot reach the iroh relay:** retry the probe with the VPN/proxy disabled. If that resolves it, add a direct/bypass rule covering `iroh.link` and all subdomains, including in TUN, virtual-NIC, enhanced, or global modes. Exclude the domain from HTTPS decryption/MITM too. Check the certificate issuer:

  ```bash
  curl -sv -o /dev/null https://aps1-1.relay.n0.iroh.link/ 2>&1 | grep issuer
  ```

  Expect a public certificate authority (currently Let's Encrypt) rather than your proxy's CA. If it still fails without the proxy, investigate DNS, firewall rules, or the network. The engine can use several relays; allowing only the probe host is insufficient.
- **Cloud join reports `couldn't reach the machine that made the invite`:** usually the Mac's VPN or proxy is intercepting `*.iroh.link` again. Fix it as in the previous item, run `reason-network stop` on the Mac, then have Grok Bot retry with the same invite while valid.
- **Session missing from `who`:** reopen sessions started before `setup`. Codex also needs to complete a turn before appearing.
- **`fetch` reports `missing`:** inspect `refwd`. `ack=resent` means the relay resent it; retry after 60 seconds. `no-copy`, `limit`, `failed`, or `skipped` means recovery did not succeed; ask the sender to resend. No `refwd` field means the Mac was not connected.
- **Message `refused`:** the engine's loop guard refuses more than 12 messages between a pair of agents within ten minutes. Body forwarding uses the exempt terminal identity.
- **`<` became `[`:** version 0.10.7's `listen` rewrites some tags to deter spoofing without changing the character count. Re-forwards use the relay's stored copy.

## Uninstall

- `./uninstall.sh`: removes the relay service, state, message copies, logs, and `~/.config/agent-relay`.
- `./uninstall.sh --all`: also runs `leave --force` and `setup --remove`, then removes `~/.reason/network` and the engine binary.
- Cloud side: pause or delete the routine, then remove `/workspace/agent-mesh/`.

## Appendix: message formats

The key is the 64-character hex **string** in `outbound.key`, stripped of surrounding whitespace. Use its UTF-8 bytes directly as the HMAC-SHA256 key; do not hex-decode it. Signatures are lowercase hex. The engine trims message whitespace, so strip trailing body whitespace before signing. Avoid the literal `<reason-message` in a body. Protocol fields and test-vector text are identical in every language.

**RN-OUT v1** (`bot-cloud` asks `bot` to send a message)：
```
RN-OUT v1
to: <recipient address>
reply_to: <message id being replied to, or empty>
ts: <Unix seconds>
nonce: <random hex; 12 random bytes recommended>
sig: <hex>
---
<body>
```
Signature: `sig = HMAC(key, "v1\n" + to + "\n" + reply_to + "\n" + ts + "\n" + nonce + "\n" + body)`. Split headers and body at the first `\n---\n`. Test vector: key = `0123456789abcdef` repeated four times, to=`demo/claude-session`, reply_to=`01JTESTREPLYTO0000000000`, ts=`1700000000`, nonce=`0123456789abcdef01234567`, body = `hello` + newline + `第二行`. Expected sig=`652fbb03f1fd8626fd4f5db689b5983bdd57c797d5ba1085c340aaf699003088`.

**RN-FWD v1** (relay forwards a body to `bot-inbox`)：
```
RN-FWD v1
id: <message id>
from: <sender address>
agent: <sender agent type, e.g. claude-code>
thread: <thread id, or empty>
text_len: <body character count>
text_sha256: <SHA-256 of body>
ts: <Unix seconds>
sig: <hex>
---
<original body>
--- RN-FWD END
```
Signature: `sig = HMAC(key, "fwd1\n" + id + "\n" + from + "\n" + agent + "\n" + thread + "\n" + text_len + "\n" + text_sha256 + "\n" + ts)`. text_len counts Unicode code points (Python len), not bytes or grapheme clusters; SHA-256 uses UTF-8 bytes. Test vector: same key, id=`01JTESTMSG0000000000000000`, from=`demo/claude-session`, agent=`claude-code`, empty thread, text = `你好` + newline + ` x `, ts=`1700000000`. Expected sig=`a48eb3bbb089a3d6b31fed070d76063bb3b3ddabfac08b7651f4093ce358c576`.

**RN-REFWD v1** (`bot-cloud` requests redelivery from `bot`; no body)：
```
RN-REFWD v1
id: <message id to resend>
ts: <Unix seconds>
nonce: <random hex>
sig: <hex>
```
Signature: `sig = HMAC(key, "refwd1\n" + id + "\n" + ts + "\n" + nonce)`. The first line identifies a re-forward request. Test vector: same key, id=`01JTESTMSG0000000000000000`, ts=`1700000000`, nonce=`0123456789abcdef01234567`. Expected sig=`eb9e0a80d3126e9f8f026e584927e864871d390c39b5dec8cc02a7a6e2922668`.

**RN-REFWD-ACK v1** (relay acknowledgement to `bot-inbox`)：
```
RN-REFWD-ACK v1
id: <message id>
status: <resent | no-copy | limit | failed>
attempt: <re-forward attempt number>
reason: <reason, or empty>
ts: <Unix seconds>
sig: <hex>
```
Signature: `sig = HMAC(key, "refwdack1\n" + id + "\n" + status + "\n" + attempt + "\n" + ts)`. Test vector: same key and id as above, status=`resent`, attempt=`1`, ts=`1700000000`. Expected sig=`faa3cd29c693eb142c3f3bbb38fa0be8c46bcea4869f90ce9842aa41aa83f6ea`.

**Doorbell** (JSON POSTed to the routine; abbreviated values)：
```
{
  "source": "reason-network",
  "relay_version": 3,
  "doorbell": true,
  "to": "<team-name>/bot",
  "reply_via": "<team-name>/bot-cloud",
  "inbox": "<team-name>/bot-inbox",
  "messages": [
    {"id": …, "from": …, "agent": …, "thread": …, "text_len": …, "text_sha256": …, "preview": …}
  ]
}
```
An item may also contain `refwd` (re-forward attempt number) and `forward_failed` (the reason the initial forward failed). Relay system notices use `doorbell:false` and carry `text` directly in each item.
