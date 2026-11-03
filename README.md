# agent-mesh

**English** | [简体中文](README.zh-CN.md)

Connect a cloud Grok Bot with active Claude Code, Codex, pi, and DeepSeek Harness (dsh) sessions running on your local computer, in one private [Reason Network](https://github.com/reason-machines/reason-network) team. The two sides can hold multi-turn conversations: either side can start one, and whoever replies wakes the other.

| | |
|---|---|
| **Version** | agent-mesh **v0.1.0** · [Changelog](CHANGELOG.md) · [Version file](VERSION). Query it offline with `./install.sh --version`; the Reason Network dependency has a separate version. |
| **Platform** | Local: not tied to one OS by design (the engine ships macOS / Linux / Windows builds; the relay is pure Python), but only macOS currently has a one-click installer and has been verified · Python 3.9+ |
| **Network** | No public IP, no dedicated server, no port forwarding required |
| **Not required** | Reason account, Docker, or Node |
| **License** | MIT |

> [!TIP]
> To let an agent install it, give Claude Code or Codex this repository and say: **“Read [AGENT-INSTALL.md](AGENT-INSTALL.md) and install it.”**

**Contents:** [Architecture overview](#architecture-overview) · [What problem it solves](#what-problem-it-solves) · [Key highlights](#key-highlights) · [Execution location labels](#execution-location-labels) · [How it works](#how-it-works) · [Prerequisites](#prerequisites) · [Install](#install) · [Verify](#verify) · [How agents receive messages](#how-agents-receive-messages) · [Security and privacy](#security-and-privacy) · [Troubleshooting](#troubleshooting) · [Uninstall](#uninstall) · [Documentation and language](#documentation-and-language) · [Appendix: message formats](#appendix-message-formats)

## Architecture overview

```mermaid
flowchart LR
    subgraph LOCAL["💻 Local Computer (Your workspace · No public IP needed)"]
        direction TB
        AGENT["🤖 Local Coding Agent<br/>(Claude Code / Codex / dsh / pi)"]
        RELAY["📮 Local Relay<br/>Lightweight daemon for proxying and inbox"]
        AGENT <-->|"Local session messaging"| RELAY
    end

    subgraph CLOUD["☁️ Grok Bot (Cloud Environment)"]
        direction TB
        DOORBELL["🔔 Webhook Routine (Doorbell)<br/>Carries only id, length, checksum, and a preview, not the full body, to wake the bot"]
        BOT["🧠 Grok Bot (Cloud Box)<br/>Sleeps when idle; wakes to fetch and verify"]
        DOORBELL -->|"Wake up"| BOT
    end

    RELAY -->|"① Full message body (Reason Network)"| BOT
    RELAY -->|"② Webhook POST doorbell"| DOORBELL
    BOT -->|"③ Signed replies (proxied to local session)"| RELAY
```

## What problem it solves

- **Reaching the right person (retaining live conversation context)**: Calling `claude -p "..."` from a remote script executes a one-off command, but it **spawns a brand new, blank process every time**. If you already have an active terminal session with extensive project background, conversation history, and pending edits, `claude -p` cannot touch it. agent-mesh addresses your specific live session directly without losing context.
- **No busy waiting (two-way doorbell wake-up)**: Complex tasks require back-and-forth dialogue (clarifying details, making revisions, and resuming discussions). Without wake-up mechanisms, the cloud must either hang in a busy loop or poll periodically; with agent-mesh, each turn sleeps immediately after sending. A reply rings the "doorbell" to wake the other side (by default the relay waits at least 20 seconds between wake-ups and wakes at most 20 times per hour; whether a local session wakes depends on its [delivery mode](#how-agents-receive-messages)), saving compute and quota on both ends.
- **Zero public exposure (no server, no public IP)**: Built on Reason Network; messages may pass through iroh public relays. Upstream describes message content as encrypted; this project has not independently audited it. Your local computer opens no inbound port and, as long as it can reach the iroh relays, works inside home or corporate networks without a self-hosted server or relay. With a VPN or proxy, allow `iroh.link`; see [Troubleshooting](#troubleshooting).
- **Guarded permissions (communication without privilege escalation)**: The channel transmits text messages only and **grants no extra execution privileges**. Your local agent still asks you to confirm sensitive actions, provided it does not auto-approve: for sessions in modes such as `--full-auto`, incoming text acts as a command; see [Security and privacy](#security-and-privacy).
- **When you don't need it**: If the entire task can be finished inside Grok Bot's cloud computer (running scripts, conducting research, writing standalone files), install the needed CLIs directly on the cloud box; you do not need this project.

## Key highlights

- 📦 **No need to migrate local code and environments**: Your code, documents, dependencies, and session history stay where they are. You do not need to pack and upload projects to a cloud machine to get cloud collaboration.
- ⚡ **Save tokens and avoid idle burn**: The cloud bot sleeps immediately after dispatching; your local agent works and rings the doorbell when finished. Neither side busy-polls (the local side runs only a lightweight relay that long-polls for mail), saving Grok quotas while utilizing your existing local agent subscriptions.
- 🤝 **Multi-session team collaboration**: Not limited to 1-on-1. Open multiple terminal sessions locally for different roles (e.g. refactoring code, auditing docs, inspecting logs); the cloud bot can communicate with them simultaneously, and local sessions can even cross-check discrepancies before reporting back.
- 🔒 **No self-hosted relay, clear permission boundary**: Built on Reason Network with no server to host or rent (messages may pass through iroh public relays); the channel carries task conversation only and grants no extra execution rights, so what runs locally is still governed by the agent's own approval settings.

## Execution location labels

This guide, the runbook, and the prompts use these labels. They are instructions, not text to enter in a terminal.

| Label | Location | What runs or happens there |
|---|---|---|
| `[LOCAL]` | User's local computer (default machine name `mac`) | Local coding agents, the Reason Network engine, and relay; `install.sh`, `check.sh`, `uninstall.sh` |
| `[BOT-CLOUD]` | Grok Bot's cloud computer, named `cloud` | Grok Bot runs `/workspace/agent-mesh/box/rn-cloud*.sh` and stores cloud state and message bodies |
| `[GROK-UI]` | Grok Bot interface opened by the user | Create or pause routines, paste prompts, and approve CopyToBox; these are not terminal commands |

Each computer has its own repository clone; files do not sync automatically. On `[LOCAL]`, `~` is the user's home directory on their local computer; `/workspace/agent-mesh/` on `[BOT-CLOUD]` is a directory on the Bot's cloud computer. Unless labeled otherwise, terminal commands and `~/…` paths below belong to `[LOCAL]`.

## How it works

There are three roles:

| Role | Availability | What it does |
|---|---|---|
| `[LOCAL]` User's local computer | Kept awake | Runs the engine and relay. The relay listens as `<team-name>/bot`, representing Grok Bot to local agents. The `bot` address belongs to the relay on the local computer, not to the Bot's cloud computer. |
| `[BOT-CLOUD]` Bot's cloud computer | May sleep | Starts the engine on demand during a turn. It sends as `<team-name>/bot-cloud`, receives full bodies as `<team-name>/bot-inbox`, and uses the machine name `cloud`. No always-on service is installed there. |
| `[GROK-UI]` Routine | Woken by webhook | A webhook task created in the Grok Bot interface; when woken, the Bot executes its prompt on `[BOT-CLOUD]`. A POST from the `[LOCAL]` relay wakes it. |

```mermaid
sequenceDiagram
    autonumber
    participant Local as [LOCAL] Local Agent
    participant Relay as [LOCAL] Relay
    participant Webhook as [GROK-UI] Webhook Doorbell
    participant Cloud as [BOT-CLOUD] Grok Bot Cloud Box

    Note over Local,Cloud: Scenario 1: Local Agent replies to Cloud (Body & Doorbell split)
    Local->>Relay: Local Agent replies
    Relay->>Cloud: First, deliver body to bot-inbox (RN-FWD)
    Relay->>Webhook: Then POST doorbell (id, length, sha256, preview, no full body, rung even if forwarding failed)
    Webhook->>Cloud: Wake up Grok Bot
    Cloud->>Cloud: rn-cloud.sh fetch retrieves and verifies body

    Note over Local,Cloud: Scenario 2: Cloud Grok Bot sends to Local (Signed proxy)
    Cloud->>Relay: Send signed envelope (RN-OUT)
    Relay->>Local: Proxied as <team-name>/bot to target local session
```

Four design choices make this work:

1. **The always-on machine handles replies.** The local computer sends every cloud-originated message on Grok Bot's behalf, so replies return to the local computer rather than a sleeping cloud computer.
2. **The webhook is a doorbell.** In the original deployment, Grok Bot retained roughly the first 4,000 characters of a wake-up body. Full text therefore travels over Reason Network; the webhook carries an id, length, checksum, and optional preview. The routine fetches and verifies the body. A 10,000-character message can use one wake-up, within the engine's byte limit.
3. **The receiver requests redelivery.** The relay keeps forwarded bodies for seven days. If `fetch` cannot retrieve one, it requests a signed re-forward, at most twice per message. This also recovers a failed initial forward.
4. **Relay protocol messages are signed.** A team session can claim another sender name. Proxy envelopes, forwarded bodies, and re-forward requests therefore use HMAC signatures. Envelopes and requests have a 15-minute freshness window and replay protection. The signing key has one copy on each machine.

## Prerequisites

### `[LOCAL]` Local computer

- **Computer and system:** not tied to one OS by design; the engine ships macOS / Linux / Windows builds, and the relay is pure Python. Currently only macOS (Apple Silicon or Intel) has the one-click installer `./install.sh` and has been verified, running as a regular user signed into the desktop. Do not use `sudo`; a pure SSH/headless login is outside this installation flow. Linux and Windows have no installer support yet and are unverified; in principle systemd or Task Scheduler could replace the macOS login service. Community testing and contributions are welcome.
- **Git and Python 3.9+**, including the `ssl` and `sqlite3` standard-library modules. Check with:

  ```bash
  git --version
  python3 -c 'import sys, ssl, sqlite3; print(sys.version)'
  ```

  If Git or Python is missing, run `xcode-select --install`, finish installation and check again; if Python is still too old, install a suitable Python and put it on PATH. The installer also checks `curl`, `tar`, `shasum`, `openssl`, `launchctl`, and `plutil`; supply missing tools before retrying. No separate Node installation or pip packages are needed.
- **At least one coding agent:** first install and sign in to at least one of Claude Code, Codex, pi, or DeepSeek Harness and verify it works independently. This project does not install these agents or configure model accounts/subscriptions. The integration plan comes from `reason-network setup --dry-run` on the user's computer, not from the author's configuration.
- **`~/.local/bin` in PATH:** in the current Bash/zsh shell, run `export PATH="$HOME/.local/bin:$PATH"`. For persistence, use `~/.zshrc` for zsh, the appropriate `~/.bash_profile` or `~/.bashrc` for Bash, or `fish_add_path "$HOME/.local/bin"` for fish. Do not always edit `~/.zshrc`. The relay service records the detected Python's absolute path; if that Python is later moved or removed, repair Python and rerun the installer to update the service.
- **Existing Reason Network installations:** review them first. This flow uses `~/.local/bin/reason-network`, the default `~/.reason/network` state directory, and pinned v0.10.7. A different PATH installation, engine version, or custom `REASON_NETWORK_HOME` stops the installer so the existing deployment can be reviewed. An existing team with the same person name can be reused; a different person cannot be renamed in place. Uninstalling with `--all` removes this default engine and its integrations and may affect existing uses; it is not a general troubleshooting step.
- **Stay awake:** keep the local computer powered on and awake during collaboration. On macOS, enable the setting that prevents automatic sleep on power, or run `caffeinate`. Closing a laptop lid stops everything.

### `[BOT-CLOUD]` Bot's cloud computer

- Linux x86_64 or ARM64, Bash, Git, Python 3.9+ with `ssl` and `sqlite3`, `curl`, `tar`, `sha256sum`, and a writable persistent `/workspace/agent-mesh/`.
- The Bot prepares missing tools using the cloud system's package manager and explains any needed permissions to the user; do not install them on the local computer instead. The cloud script checks system, CPU, and runtime dependencies before downloads; the cloud prompt covers Git and directory checks.

### Grok Bot and team name

- Grok Bot must support webhook-triggered routines and CopyToBox from your Mac.
- Choose a team name, also used as your person name: 1–40 lowercase ASCII letters, digits, or hyphens; no leading or trailing hyphen. Use a pseudonym. Renaming requires recreating the team.

### Network

- Both `[LOCAL]` and `[BOT-CLOUD]` need HTTPS access to GitHub repositories and release downloads, plus iroh relay connectivity.
- The default public HTTPS clone URL needs no GitHub account or SSH key. For a public fork, pass `--repo-url https://…` on each installer command that generates a cloud prompt; it must allow credential-free cloud reads. The local clone can live anywhere; the cloud clone must remain at `/workspace/agent-mesh/` because prompts use that fixed path.
- Check connectivity to `*.iroh.link`, especially with a VPN or proxy. This probe should return HTTP 200:

  ```bash
  curl -sS -o /dev/null -m 10 -w 'code=%{http_code}\n' https://aps1-1.relay.n0.iroh.link/
  ```

  If it fails, see [Troubleshooting](#troubleshooting). The probe checks one endpoint, not worldwide availability.

## Install

**`[LOCAL]` Run as the user or local installing agent.** Replace the team-name placeholder:

```bash
git clone https://github.com/xiaozhenliu/agent-mesh.git ~/agent-mesh
cd ~/agent-mesh
./install.sh --name <team-name> --lang en
```

If the destination already exists, check that it is the same repository and review uncommitted changes before proceeding; do not overwrite it or blindly pull. These commands clone the public distribution. The cloud defaults to the same public repository, whose root must contain `box/rn-cloud-setup.sh`; the installer does not infer its URL from the local origin.

The installer handles the local computer (Mac) side and pauses for user actions:

1. Downloads Reason Network v0.10.7, verifies SHA-256, creates your private team, and disables auto-update and LAN broadcasts.
2. Shows `reason-network setup --dry-run` and **waits for confirmation** before wiring local agents.
3. `[LOCAL]` generates the signing key and prints a routine prompt. **In `[GROK-UI]`, the user creates a webhook-triggered routine and pastes that prompt.** Paste its Webhook URL and key into the `[LOCAL]` terminal. The installer writes local configuration and installs the relay as a Mac login service.
4. `[LOCAL]` prints a team invite and cloud setup prompt. **In `[GROK-UI]`, the user sends both to Grok Bot.** On `[BOT-CLOUD]`, the Bot clones the repository and joins the team, then uses CopyToBox to copy the key from `[LOCAL]` to `[BOT-CLOUD]`. The user approves the copy in `[GROK-UI]`.

Reopen Claude Code and Codex sessions that were already running. Codex asks once on first launch; choose “Trust all and continue”.

### Prompt language

Use `--lang en` or `--lang zh-CN` on each installer invocation to select generated prompts. English is the default; `AGENT_MESH_LANG` sets a shell-level default, and `--lang` overrides it. Terminal diagnostics, configuration keys, and protocol fields remain English. Agents speak to users in their preferred language. Language support does not change operating-system or network requirements.

### Optional

- **dsh automatic wake-ups:** run `dsh/mac-install.sh <team-name>`; see the [bridge guide](dsh/README.md).
- **Reply convention:** append the [English template](templates/AGENTS.reason-network.md) or [Chinese template](templates/AGENTS.reason-network.zh-CN.md) to your working repository's `AGENTS.md`. For Claude Code, also add `@AGENTS.md` to `CLAUDE.md`. The template asks for a conclusion followed by four concise status fields.

## Verify

Locations: self-checks, test sends, and relay log inspection run on `[LOCAL]`; Grok Bot uses `/workspace/agent-mesh/box/rn-cloud.sh` on `[BOT-CLOUD]` for `who` and `fetch`.

1. **Self-check.** This is read-only; each `FAIL` includes a suggested fix.

   ```bash
   ./check.sh
   ```

2. **Test message.** Send a message, then wait for the reply in another terminal:

   ```bash
   reason-network send <team-name>/bot "test 1"
   reason-network listen --as terminal --wait 600   # in another terminal
   ```

   Look for `forwarded` and `POST … code=200` in `~/Library/Logs/agent-relay.log`.

3. **Wake a session.** Ask Grok Bot to run `who` and send a sentence to a specific Claude Code session. The session should wake even if idle; its reply should wake Grok Bot again.
4. **Long message.** Send a 10,000-character message. Expect one wake-up and a `complete` fetch result with matching length and SHA-256. The signed message must fit within the relay's 60 KiB forwarding limit; UTF-8 byte size depends on the text.

## How agents receive messages

The last column of `reason-network who` shows the delivery mode:

| Mode | Behavior | Agents |
|---|---|---|
| **push** | Wakes an idle session. | Claude Code receives an injected message; Codex uses `codex exec resume`; pi polls through an extension every five seconds. |
| **turn** | Normally waits for a tool call or the end of a turn. | dsh with the bridge reports `turn`, but starts a turn within roughly five seconds when idle. |
| **pull** | Does not wake automatically. | dsh without the bridge must check its inbox. |

Closed sessions disappear from the roster. Messages remain in the inbox while the recipient is offline. After reopening, you have to tell the agent to check its inbox before it reads them. Keep a session open for prompt delivery.

## Security and privacy

- This bridge sends text to a local agent; it does not itself give Grok Bot direct file access or bypass the agent's permissions. The Mac opens no inbound port. Grok Bot's local always-approve and local-network switches are not required.
- An agent configured to approve automatically may act on incoming text without asking you. Use an appropriately restricted workspace and permissions for these sessions.
- Signatures prevent impersonation by sessions without the key. Agents running as the same macOS user may still read the keys in `~/.config/agent-relay/`. A project folder is not an access-control boundary.
- To hold incoming messages, set `[inbound] default = "hold"` in `~/.reason/network/config.toml`, then use `reason-network approve` to release them.
- Team members can see session names, the last working-directory component, repository names, and branches. Avoid starting agents directly in your home directory. Use invite links in the terminal, not a browser.
- The pinned open-source engine runs locally in account-free mode, and in that mode it sends no data to Reason. Traffic may pass through iroh public relays; upstream describes the content as encrypted. This project has not independently audited those relays. `reason-network relay <URL>` selects a self-hosted relay.
- Each Grok Bot wake-up consumes usage. The relay defaults to at most 20 wake-ups per hour.
- Report security problems privately as described in the [security policy](SECURITY.md), not in a public issue. It also explains what to do if the signing key, webhook key or an invite link may have leaked.

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

All `uninstall.sh` commands run on `[LOCAL]`:

| Command | Removes |
|---|---|
| `./uninstall.sh` | The relay service, state, message copies, logs, and `~/.config/agent-relay`. |
| `./uninstall.sh --all` | Everything above; also runs `leave --force` and `setup --remove`, then removes `~/.reason/network` and the engine binary. |

Then, in `[GROK-UI]`, pause or delete the routine, and on `[BOT-CLOUD]`, remove `/workspace/agent-mesh/`.

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
| Security policy | [Security policy](SECURITY.md) | [安全策略](SECURITY.zh-CN.md) |

To choose the language of generated prompts, see [Prompt language](#prompt-language).

## Appendix: message formats

### Signing rules

- The key is the 64-character hex **string** in `outbound.key`, stripped of surrounding whitespace. Use its UTF-8 bytes directly as the HMAC-SHA256 key; do not hex-decode it.
- Signatures are lowercase hex.
- The engine trims message whitespace, so strip trailing body whitespace before signing.
- Avoid the literal `<reason-message` in a body.
- Protocol fields and test-vector text are identical in every language.
- All test vectors below use the same key: `0123456789abcdef` repeated four times.

### RN-OUT v1

`bot-cloud` asks `bot` to send a message:

```text
RN-OUT v1
to: <recipient address>
reply_to: <message id being replied to, or empty>
ts: <Unix seconds>
nonce: <random hex; 12 random bytes recommended>
sig: <hex>
---
<body>
```

Signature: `sig = HMAC(key, "v1\n" + to + "\n" + reply_to + "\n" + ts + "\n" + nonce + "\n" + body)`. Split headers and body at the first `\n---\n`.

Test vector:

- to=`demo/claude-session`, reply_to=`01JTESTREPLYTO0000000000`, ts=`1700000000`, nonce=`0123456789abcdef01234567`
- body = `hello` + newline + `第二行`
- Expected sig=`652fbb03f1fd8626fd4f5db689b5983bdd57c797d5ba1085c340aaf699003088`

### RN-FWD v1

The relay forwards a body to `bot-inbox`:

```text
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

Signature: `sig = HMAC(key, "fwd1\n" + id + "\n" + from + "\n" + agent + "\n" + thread + "\n" + text_len + "\n" + text_sha256 + "\n" + ts)`. text_len counts Unicode code points (Python len), not bytes or grapheme clusters; SHA-256 uses UTF-8 bytes.

Test vector:

- id=`01JTESTMSG0000000000000000`, from=`demo/claude-session`, agent=`claude-code`, empty thread, ts=`1700000000`
- text = `你好` + newline + ` x `
- Expected sig=`a48eb3bbb089a3d6b31fed070d76063bb3b3ddabfac08b7651f4093ce358c576`

### RN-REFWD v1

`bot-cloud` requests redelivery from `bot`; no body:

```text
RN-REFWD v1
id: <message id to resend>
ts: <Unix seconds>
nonce: <random hex>
sig: <hex>
```

Signature: `sig = HMAC(key, "refwd1\n" + id + "\n" + ts + "\n" + nonce)`. The first line identifies a re-forward request.

Test vector:

- id=`01JTESTMSG0000000000000000`, ts=`1700000000`, nonce=`0123456789abcdef01234567`
- Expected sig=`eb9e0a80d3126e9f8f026e584927e864871d390c39b5dec8cc02a7a6e2922668`

### RN-REFWD-ACK v1

Relay acknowledgement to `bot-inbox`:

```text
RN-REFWD-ACK v1
id: <message id>
status: <resent | no-copy | limit | failed>
attempt: <re-forward attempt number>
reason: <reason, or empty>
ts: <Unix seconds>
sig: <hex>
```

Signature: `sig = HMAC(key, "refwdack1\n" + id + "\n" + status + "\n" + attempt + "\n" + ts)`.

Test vector:

- Same id as above, status=`resent`, attempt=`1`, ts=`1700000000`
- Expected sig=`faa3cd29c693eb142c3f3bbb38fa0be8c46bcea4869f90ce9842aa41aa83f6ea`

### Doorbell

JSON POSTed to the routine (abbreviated values):

```text
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
