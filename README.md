# agent-mesh

**English** | [简体中文](README.zh-CN.md)

Connect a cloud Grok Bot with active Claude Code, Codex, pi, and DeepSeek Harness (dsh) sessions running on your local computer, in one private [Reason Network](https://github.com/reason-machines/reason-network) team. The two sides can hold multi-turn conversations: either side can start one, and whoever replies wakes the other.

| | |
|---|---|
| **Version** | agent-mesh **v0.11.0** · [Changelog](CHANGELOG.md) · [Version file](VERSION). Query it offline with `./install.sh --version`; the Reason Network dependency has a separate version. |
| **Platform** | Local: macOS (Apple Silicon or Intel) with a one-click installer · relay messaging verified on Debian Linux, but local installation on Linux is not supported yet (see [Prerequisites](#prerequisites)) · Bot's cloud computer: Linux x86_64 or ARM64 · Python 3.9+ on both sides |
| **Network** | No public IP, no dedicated server, no port forwarding required |
| **Not required** | Reason account, Docker, or Node |
| **License** | MIT |

> [!TIP]
> To let an agent install it, give Claude Code or Codex this repository and say: **“Read [AGENT-INSTALL.md](AGENT-INSTALL.md) and install it.”**

**Contents:** [Architecture overview](#architecture-overview) · [What problem it solves](#what-problem-it-solves) · [Execution location labels](#execution-location-labels) · [Terms](#terms) · [How it works](#how-it-works) · [Prerequisites](#prerequisites) · [Install](#install) · [Several Grok Bots](#several-grok-bots) · [Verify](#verify) · [How agents receive messages](#how-agents-receive-messages) · [Security and privacy](#security-and-privacy) · [Troubleshooting](#troubleshooting) · [Uninstall](#uninstall) · [Upgrade](#upgrade) · [Documentation and language](#documentation-and-language) · [Appendix: message formats](#appendix-message-formats)

## Architecture overview

```mermaid
flowchart LR
    subgraph LOCAL["💻 Local Computer (Your workspace · No public IP needed)"]
        direction TB
        AGENT["🤖 Local sessions<br/>(Claude Code / Codex / dsh / pi)"]
        RELAY["📮 Relay<br/>Always-on service: receives at the Bot address, proxies Grok Bot's replies"]
        AGENT <-->|"Local session messaging"| RELAY
    end

    subgraph CLOUD["☁️ Cloud"]
        direction TB
        DOORBELL["🔔 Routine (webhook)<br/>A doorbell wakes Grok Bot; it carries only id, length, checksum and a preview, not the full body"]
        BOT["🧠 Grok Bot · Bot's cloud computer<br/>Sleeps when idle; fetches and verifies during the turn it is woken for"]
        DOORBELL -->|"Wake up"| BOT
    end

    RELAY -->|"① Full message body as cloud mail (Reason Network)"| BOT
    RELAY -->|"② Webhook POST doorbell"| DOORBELL
    BOT -->|"③ Signed replies (proxied to local session)"| RELAY
```

## What problem it solves

- **Reaches the live session**: Calling `claude -p "..."` from a remote script runs a one-off task, but it **spawns a new, blank process every time** and cannot touch the terminal session you already have open, with its project background, conversation history and pending edits. agent-mesh addresses that live session directly, so the context stays.
- **No busy waiting**: Complex tasks need back-and-forth: questions, revisions, picking the thread up later. Without a wake-up mechanism the cloud has to hang or poll; with agent-mesh each side sleeps right after sending, and a reply rings the "doorbell" to wake the other. Neither side busy-polls (the local side runs only a lightweight relay that long-polls for mail), which saves Grok Bot usage and puts your existing local agent subscriptions to use. By default the relay waits at least 20 seconds between wake-ups and wakes at most 20 times per hour; whether a local session wakes depends on its [delivery mode](#how-agents-receive-messages).
- **Code stays where it is**: Your code, documents, dependencies and session history stay on the local computer; you do not need to pack a project and upload it to a cloud computer to work with the cloud.
- **Several sessions at once**: Open several local sessions for different roles (e.g. refactoring code, auditing docs, inspecting logs); Grok Bot can talk to all of them, and local sessions can cross-check a disagreement among themselves before reporting back.
- **No public exposure**: Built on Reason Network. Your local computer opens no inbound port and needs no self-hosted or rented server; as long as it can reach the iroh relays, it works inside home or corporate networks. Messages may pass through iroh public relays, and a VPN or proxy must allow `iroh.link`; see [Network and usage](#network-and-usage) and [Troubleshooting](#troubleshooting).
- **No extra privileges**: The channel carries text only and **grants no extra execution privileges**; what runs locally is still governed by the agent's own approval settings. Two caveats: for a session that auto-approves (Claude Code's `--dangerously-skip-permissions`, Codex's `--yolo` and similar modes), incoming text acts as a command; and Grok Bot in the cloud is the channel's trust root, so if content it reads steers it, it can still send validly signed messages. See [Security and privacy](#security-and-privacy).
- **When you don't need it**: If the whole task can be done on the Bot's cloud computer (running scripts, research, writing standalone files), install the CLIs you need there; you do not need this project.

## Execution location labels

This guide, the runbook, and the prompts use these labels. They are instructions, not text to enter in a terminal.

| Label | Location | What runs or happens there |
|---|---|---|
| `[LOCAL]` | User's local computer (default machine name `mac`) | Local coding agents, the Reason Network engine, and relay; `install.sh`, `check.sh`, `uninstall.sh` |
| `[BOT-CLOUD]` | The Bot's cloud computer: the Linux computer where Grok Bot runs commands, named `cloud` | Grok Bot runs `/workspace/agent-mesh/box/rn-cloud*.sh` and stores cloud state and message bodies |
| `[GROK-UI]` | Grok Bot interface opened by the user | Create or pause routines, paste the routine prompt, send the cloud setup prompt, and approve CopyToBox; these are not terminal commands |

Each computer has its own repository clone; files do not sync automatically. On `[LOCAL]`, `~` is the user's home directory on their local computer; `/workspace/agent-mesh/` on `[BOT-CLOUD]` is a directory on the Bot's cloud computer. Unless labeled otherwise, terminal commands and `~/…` paths below belong to `[LOCAL]`.

## Terms

This guide uses these words as follows.

**Machines and roles**

| Term | Meaning |
|---|---|
| Local computer `[LOCAL]` | Your Mac, also called this computer. It stays on and runs the engine, the relay and the local sessions. |
| Bot's cloud computer `[BOT-CLOUD]` | The Linux computer where Grok Bot runs commands, named `cloud`; the cloud computer for short. It sleeps: nothing stays running there, and the engine starts only during the turn it is woken for. "The cloud" means that side as a whole: the clone in `/workspace/agent-mesh/`, the cloud scripts, the engine and their state. |
| Grok Bot | Your Bot in Grok, the Bot for short, and the trust root of this channel. In agent-mesh each Bot has its own Bot address, relay service, routine and cloud sessions; another routine of the same Grok Bot, added under a new Bot address name, is another Bot ([Several Grok Bots](#several-grok-bots)). |
| Grok Bot interface `[GROK-UI]` | Where you work with Grok Bot: create routines, paste the routine prompt, send prompts in the chat, approve CopyToBox. |
| Local session | A coding agent session on the local computer that is wired into the team (Claude Code, Codex, pi, dsh). |

**Names**

| Term | Meaning |
|---|---|
| Team name `<team-name>` | The name of your private one-person team, also your person name in it; it cannot change once the team exists. |
| Bot address `<team-name>/<name>` | The address local sessions write to Grok Bot at. The relay on the local computer receives at this address, not the Bot's cloud computer. |
| Bot address name `<name>` | The name in the Bot address, chosen with `--relay-name` at installation and changed later with `agent-mesh rename` (`bot` was the default before v0.8.0). The cloud derives two sessions from it: `<name>-cloud` sends, `<name>-inbox` receives full bodies. |
| Grok name | The Bot's name as Grok shows it, set with `--grok-name` and used only for display: `<team-name>/<name> → Grok: <Grok name>`. |

**Parts**

| Term | Meaning |
|---|---|
| Engine | Reason Network (pinned v0.12.17), one copy on each computer; it handles the team, the sessions and message delivery. |
| Relay | The always-on service on the local computer, one per Bot. It receives at the Bot address, forwards bodies to the cloud, rings the doorbell to wake Grok Bot, and proxies Grok Bot's signed replies to local sessions. Not to be confused with the network relays (iroh or Reason relay servers) the engine connects through. |
| Routine | A webhook-triggered task created in the Grok Bot interface, one per Bot. One POST to its webhook wakes Grok Bot to run the routine prompt. |
| Routine prompt, routine stub | The text saved in the routine. By default it is the routine stub: a fixed short English text that loads the rules from the cloud on every run, so a change of the rules needs no new paste ([Routine stub](UPGRADE.md#routine-stub)). With `--full-prompt` the routine holds the complete prompt instead. |
| Rules | The steps the routine follows on every turn, rendered on `[BOT-CLOUD]` from templates in the cloud's clone. |
| Cloud setup prompt | The prompt sent to Grok Bot with the team invite at installation. With it the Bot clones the release on its cloud computer, installs the engine and joins the team. |
| Signing key | `outbound.key`, one copy on the local computer and one on the Bot's cloud computer; it signs the messages between the two. |
| Deployment record | What the local computer records about the deployed version and confirmation state of each part; `./update.sh` compares it with the new release. |

**Messages**

| Term | Meaning |
|---|---|
| Doorbell | One POST from the relay to the routine's webhook, carrying only the message id, length, checksum and an optional preview, never the full body. |
| Wake, turn | A doorbell runs the routine once and wakes Grok Bot; that run is a turn. For how new messages wake local sessions, see delivery mode. |
| Usage | Every wake-up of Grok Bot consumes its usage. By default the relay wakes it at most 20 times per hour. |
| Cloud mail | Bodies that reach the cloud through `<name>-inbox`. `fetch` retrieves and checks each by id and stores it under `inbox/<id>/` (an added Bot: `bots/<name>/inbox/`). |
| Pending, stored, handled | How the relay tracks each forwarded message. The cloud tells the relay with signed receipts which messages it stored and which it handled; a message stays pending until it is handled. |
| Re-forward | When the cloud cannot get a body, it sends a signed re-forward request, and the relay sends its copy again; copies are kept for 7 days, and each message is re-forwarded at most twice. |
| Thread notes | The cloud's `threads/*.md` (an added Bot: `bots/<name>/threads/`), the conversation summaries the routine appends each turn; Grok Bot relies on them for context. |
| Delivery mode | How a local session receives new messages: push, turn or pull ([How agents receive messages](#how-agents-receive-messages)). |

## How it works

There are three roles:

| Role | Availability | What it does |
|---|---|---|
| `[LOCAL]` Local computer | Kept awake | Runs the engine and relay. The relay listens at the Bot address `<team-name>/<name>`, representing Grok Bot to local sessions. The address belongs to the relay on the local computer, not to the Bot's cloud computer. `<name>` is your [Bot address name](#grok-bot-and-team-name), normally the Bot's own name (`bot` was the default before v0.8.0). |
| `[BOT-CLOUD]` Bot's cloud computer | May sleep | Starts the engine on demand during a turn. It sends as `<team-name>/<name>-cloud`, receives full bodies as `<team-name>/<name>-inbox`, and uses the machine name `cloud`. No always-on service is installed there. |
| `[GROK-UI]` Routine | Woken by webhook | A webhook task created in the Grok Bot interface; when woken, the Bot executes its routine prompt on `[BOT-CLOUD]`. A POST from the `[LOCAL]` relay (the doorbell) wakes it. |

```mermaid
sequenceDiagram
    autonumber
    participant Local as [LOCAL] Local session
    participant Relay as [LOCAL] Relay
    participant Webhook as [GROK-UI] Routine (webhook)
    participant Cloud as [BOT-CLOUD] Grok Bot · Bot's cloud computer

    Note over Local,Cloud: Scenario 1: a local session replies to Grok Bot (body and doorbell split)
    Local->>Relay: Local session replies to the Bot address
    Relay->>Cloud: First, deliver the body as cloud mail (RN-FWD)
    Relay->>Webhook: Then POST doorbell (id, length, sha256, preview, no full body, rung even if forwarding failed)
    Webhook->>Cloud: Wake up Grok Bot
    Cloud->>Cloud: rn-cloud.sh fetch retrieves and verifies body

    Note over Local,Cloud: Scenario 2: Grok Bot sends to a local session (signed proxy)
    Cloud->>Relay: Send signed envelope (RN-OUT)
    Relay->>Local: Proxied as the Bot address to the target local session
```

Five design choices make this work:

1. **The always-on machine handles replies.** The local computer sends every cloud-originated message on Grok Bot's behalf, so replies return to the local computer rather than a sleeping cloud computer.
2. **The webhook is a doorbell.** In the original deployment, Grok Bot retained roughly the first 4,000 characters of a webhook body. Full text therefore travels over Reason Network to the cloud's `<name>-inbox`; the webhook carries an id, length, checksum, and optional preview. The routine fetches and verifies the body. A 10,000-character message can use one wake-up, within the engine's byte limit.
3. **Both ends recover lost mail.** The relay keeps forwarded bodies for seven days. If `fetch` cannot retrieve one, the cloud requests a signed re-forward, at most twice per message; this also recovers a failed initial forward. The cloud also tells the relay with signed receipts which messages it stored and which were handled. A message stays pending on the relay until it is handled; the relay rings again for one that stays unconfirmed, a limited number of times, and then alerts Grok Bot instead of retrying forever. Timings, settings and known limitations are under [Recovery and receipts](PROTOCOL.md#recovery-and-receipts).
4. **Each message goes to one turn, and an interrupted one is not redone silently.** Wake-ups can overlap, and a retried webhook can ring twice for the same id. `fetch` claims each body it hands out, so an overlapping turn skips it. If a turn stops after it started acting and before it recorded completion, later turns report `uncertain`: the Bot checks its thread notes or asks the user rather than acting again. `fetch` also hands out verified bodies whose doorbell was lost. This is not exactly-once; see [Claims and interrupted turns](PROTOCOL.md#claims-and-interrupted-turns).
5. **Relay protocol messages are signed.** A team session can claim another sender name. Proxy envelopes, forwarded bodies, and re-forward requests therefore use HMAC signatures. Envelopes and requests have a 15-minute freshness window and replay protection. The signing key has one copy on each machine.

## Prerequisites

### `[LOCAL]` Local computer

- **Computer and system:** not tied to one OS by design; the engine ships macOS / Linux / Windows builds, and the relay is pure Python. Only macOS (Apple Silicon or Intel) has the one-click installer `./install.sh`, run as a regular user signed into the desktop. Do not use `sudo`; a pure SSH/headless login is outside this installation flow. On Debian Linux, the relay's messaging has been verified inside an existing team, including real two-way messages with the cloud; installing the local side on Linux (creating the team, wiring agents, generating and copying the signing key, running the relay as a service) is not supported yet. In principle systemd could replace the macOS login service. Windows is unverified; Task Scheduler could play the same role. Community testing and contributions are welcome.
- **Git and Python 3.9+**, including the `ssl` and `sqlite3` standard-library modules. Check with:

  ```bash
  git --version
  python3 -c 'import sys, ssl, sqlite3; print(sys.version)'
  ```

  If Git or Python is missing, run `xcode-select --install`, finish installation and check again; if Python is still too old, install a suitable Python and put it on PATH. The installer also checks `curl`, `tar`, `shasum`, `openssl`, `launchctl`, and `plutil`; supply missing tools before retrying. No separate Node installation or pip packages are needed. The relay service runs on one fixed Python: a new installation takes `/opt/homebrew/bin/python3`, then `/usr/local/bin/python3`, then `/usr/bin/python3`, and never one inside a virtual environment or a temporary directory. To use another one, add `--python <path>` to `./install.sh` ([Relay Python](UPGRADE.md#upgrade-with-updatesh)).
- **At least one coding agent:** first install and sign in to at least one of Claude Code, Codex, pi, or DeepSeek Harness and verify it works independently. This project does not install these agents or configure model accounts/subscriptions. The integration plan comes from `reason-network setup --dry-run` on the user's computer, not from the author's configuration.
- **`~/.local/bin` in PATH:** in the current Bash/zsh shell, run `export PATH="$HOME/.local/bin:$PATH"`. For persistence, use `~/.zshrc` for zsh, the appropriate `~/.bash_profile` or `~/.bashrc` for Bash, or `fish_add_path "$HOME/.local/bin"` for fish. Do not always edit `~/.zshrc`. The relay service records the detected Python's absolute path; if that Python is later moved or removed, repair Python and rerun the installer to update the service.
- **Existing Reason Network installations:** review them first. This flow uses `~/.local/bin/reason-network`, the default `~/.reason/network` state directory, and pinned v0.12.17. An older engine at that path stops the installer, which points to `./update.sh` (it upgrades the engine with a backup and rolls back on failure). A different PATH installation, a newer or unrecognised engine version, or custom `REASON_NETWORK_HOME` stops the installer so the existing deployment can be reviewed. An existing team with the same person name can be reused; a different person cannot be renamed in place. Uninstalling at level `identity` or `all` removes this default engine's state and integrations (level `all` also the engine) and may affect existing uses; it is not a general troubleshooting step.
- **Stay awake:** keep the local computer powered on and awake during collaboration. On macOS, enable the setting that prevents automatic sleep on power, or run `caffeinate`. Closing a laptop lid stops everything.

### `[BOT-CLOUD]` Bot's cloud computer

- Linux x86_64 or ARM64, Bash, Git, Python 3.9+ with `ssl` and `sqlite3`, `curl`, `tar`, `sha256sum`, and a writable persistent `/workspace/agent-mesh/`.
- The Bot prepares missing tools using the cloud system's package manager and explains any needed permissions to the user; do not install them on the local computer instead. The cloud script checks system, CPU, and runtime dependencies before downloads; the cloud setup prompt covers Git and directory checks.

### Grok Bot and team name

- Grok Bot must support webhook-triggered routines and CopyToBox from your Mac.
- Choose a team name, also used as your person name: 1–40 lowercase ASCII letters, digits, or hyphens; no leading or trailing hyphen. Use a pseudonym. Renaming requires recreating the team.
- Team and machine names use single hyphens only: the engine merges repeated hyphens, so `a--b` would become `a-b`, and the installer refuses such names.
- Choose the Bot address name: use the Bot's own name, so your agents can tell your Grok Bots apart. The relay listens as `<team-name>/<name>`, and the cloud uses `<name>-cloud` and `<name>-inbox`. A new installation has no default (before v0.8.0 it was `bot`; existing installations keep their name). Rules: 1–34 lowercase ASCII letters and digits, in groups joined by a single hyphen (no leading, trailing or repeated hyphen), not `terminal` or `terminal-<n>`. Do not use your real name. An unfinished installation cannot change it: queued mail, the routine prompt and the cloud setup all use it. Once the installation is complete, `agent-mesh rename` changes it without anything to do in Grok ([Rename a Bot](#rename-a-bot)).
- Give the Grok name, the Bot's name as Grok shows it, in any language, with `--grok-name` (up to 64 characters, no spaces at the start or end, no line breaks or other control characters). It is stored as given, not used for routing, and shown next to the address as `<team-name>/<name> → Grok: <its name>` by the installer, `./check.sh` and the filled-in reply convention. For a name without Latin letters, pick an address yourself, for example in pinyin (「小助手」 → `xiaozhushou`).

### Network

- Both `[LOCAL]` and `[BOT-CLOUD]` need HTTPS access to GitHub repositories and release downloads, plus iroh relay connectivity.
- The default public HTTPS clone URL needs no GitHub account or SSH key. For a public fork, pass `--repo-url https://…` on each installer command that generates a cloud setup prompt, and the same option to `./update.sh`; it must allow credential-free cloud reads and carry the release tag `v<VERSION>`, because the cloud installs only that tag. The local clone can live anywhere; the cloud clone must remain at `/workspace/agent-mesh/` because prompts use that fixed path.
- Check connectivity to `*.iroh.link`, especially with a VPN or proxy. This probe should return HTTP 200:

  ```bash
  curl -sS -o /dev/null -m 10 -w 'code=%{http_code}\n' https://aps1-1.relay.n0.iroh.link/
  ```

  If it fails, see [Troubleshooting](#troubleshooting). The probe checks one endpoint, not worldwide availability.
- Reason Network 0.12 uses Reason's own network relays by default (`relay-sfo1`, `relay-iad1`, `relay-fra1` and `relay-sin1` under `compute.reasonmachines.com`) instead of n0's `*.iroh.link`. agent-mesh 0.7.0 pins 0.12.17, so allow `*.compute.reasonmachines.com`; an engine still on 0.10.7 uses n0's network relays until `./update.sh` upgrades it, so allow both domains. `./check.sh` probes the network relay the installed engine actually uses and names it.

## Install

**`[LOCAL]` Run as the user or local installing agent.** Replace the team-name placeholder:

```bash
git clone https://github.com/xiaozhenliu/agent-mesh.git ~/agent-mesh
cd ~/agent-mesh
./install.sh --name <team-name> --relay-name <name> --grok-name '<its name in Grok>' --lang en
```

`--relay-name` is the Bot address name and `--grok-name` the Bot's name in Grok ([Grok Bot and team name](#grok-bot-and-team-name)). A new installation without `--relay-name` stops before changing anything and suggests an address derived from `--grok-name`. Later runs (`--webhook`, `--cloud-prompt`, `update.sh`) take the name from the installation; a different `--relay-name` is refused, and only `agent-mesh rename` changes the name. With several Bots on this Mac, `--relay-name` chooses one of them ([Several Grok Bots](#several-grok-bots)). The installer stops before wiring anything if the name is already used in the team, and checks after starting the relay that the engine registered exactly `<team-name>/<name>`. `./install.sh --grok-name '<its name in Grok>'` records or changes the Grok name of an existing installation; the address stays.

If the destination already exists, check that it is the same repository and review uncommitted changes before proceeding; do not overwrite it or blindly pull. The cloud defaults to the same public distribution repository, whose root must contain `box/rn-cloud-setup.sh`; the installer does not infer its URL from the local origin.

The installer handles the local computer (Mac) side and pauses for user actions:

1. Downloads Reason Network v0.12.17, verifies it against the SHA-256 pinned in the installer, creates your private team, and disables auto-update and LAN broadcasts.
2. Shows `reason-network setup --dry-run` and **waits for confirmation** before wiring local agents.
3. `[LOCAL]` generates the signing key and prints the routine prompt, a fixed short text (the routine stub) that loads its rules from `[BOT-CLOUD]` on every run ([Routine stub](UPGRADE.md#routine-stub)). **In `[GROK-UI]`, the user creates a webhook-triggered routine and pastes that prompt.** Paste its Webhook URL and key into the `[LOCAL]` terminal. The installer writes local configuration and installs the relay as a Mac login service.
4. `[LOCAL]` prints a team invite and cloud setup prompt. **In `[GROK-UI]`, the user sends both to Grok Bot.** On `[BOT-CLOUD]`, the Bot clones the release after checking its commit and the cloud scripts' checksums, installs the engine and joins the team, then uses CopyToBox to copy the key from `[LOCAL]` to `[BOT-CLOUD]`. The user approves the copy in `[GROK-UI]`.

Reopen Claude Code and Codex sessions that were already running. Codex asks once on first launch; choose “Trust all and continue”.

### Prompt language

Use `--lang en` or `--lang zh-CN` on each installer invocation to select generated prompts. English is the default; `AGENT_MESH_LANG` sets a shell-level default, and `--lang` overrides it. Terminal diagnostics, configuration keys, and protocol fields remain English. Agents speak to users in their preferred language.

### Optional

- **dsh automatic wake-ups:** run `dsh/mac-install.sh <team-name>`; see the [bridge guide](dsh/README.md).
- **Reply convention:** append the filled-in copy in `~/.local/lib/agent-mesh/templates/` (English `AGENTS.reason-network.md` or Chinese `AGENTS.reason-network.zh-CN.md`; the installer writes them with your Bot address and Grok name) to your working repository's `AGENTS.md`. The [English template](templates/AGENTS.reason-network.md) and [Chinese template](templates/AGENTS.reason-network.zh-CN.md) here have placeholders instead. After changing the Grok name, `python3 scripts/reply_convention.py refresh <repository>/AGENTS.md` updates a copy; it replaces only the lines between the `agent-mesh:bot-address` markers. For Claude Code, also add `@AGENTS.md` to `CLAUDE.md`. The template asks for a conclusion followed by four concise status fields.

## Several Grok Bots

Since v0.9.0 one Mac can serve several Grok Bots in the same team. They share the team, the engine, the cloud computer, the signing key and the relay code. Each Bot has its own address `<team-name>/<name>`, its own relay service, webhook and routine, and its own `<name>-cloud` and `<name>-inbox` sessions on `[BOT-CLOUD]`.

| | First Bot | Each added Bot `<name>` |
|---|---|---|
| Relay settings | `~/.config/agent-relay/env` | `~/.config/agent-relay-bots/<name>/env`, with `OUTBOUND_KEY_FILE` pointing to the shared `~/.config/agent-relay/outbound.key` |
| Login service | `com.local.agent-relay` | `com.local.agent-relay.<name>` |
| Relay state | `~/Library/Application Support/agent-relay/` | `~/Library/Application Support/agent-relay-bots/<name>/` |
| Relay log | `~/Library/Logs/agent-relay.log` | `~/Library/Logs/agent-relay-<name>.log` |
| Mail and thread notes on `[BOT-CLOUD]` | `/workspace/agent-mesh/inbox/`, `threads/` | `/workspace/agent-mesh/bots/<name>/inbox/`, `threads/` |

An existing installation does not move: the first Bot keeps its paths, its login service file and its routine prompt. An added Bot keeps the paths of the name it was added with, also after a rename.

### Add a Bot

The installation must be complete and up to date first: every Bot has its webhook, its relay and its routine prompt confirmed, the cloud update of this version is confirmed (the installer says `run ./update.sh first` otherwise), no update is unfinished and no Bot is being uninstalled. Then run on `[LOCAL]`:

```bash
./install.sh --add-bot --lang en
```

1. It asks what the Bot is called in Grok and proposes an address name derived from it; for a name without Latin letters or digits it proposes the first free `bot-2`, `bot-3`, …. Press Enter to accept it or type another. An agent can give both at once with `--grok-name '<its name in Grok>' --relay-name <name>`; then the installer asks nothing but the plan. The team comes from the installation, so `--name` is not needed (if given, it must be the same team). It does not wire agents again, download the engine or generate a key.
2. It checks that none of `<name>`, `<name>-cloud` and `<name>-inbox` is a session the team still lists (on any machine, online or offline) or was ever used on this Mac, and that the name is no current or earlier name of another Bot; a name of an uninstalled Bot or of an earlier setup can be taken back after you check the evidence ([Which names a Bot can take](#which-names-a-bot-can-take)). Then it shows the plan `Add Bot <team-name>/<name> (called “…” in Grok); <the other Bots> stay as they are.` for you to confirm. `--add-bot` never accepts `--yes`; outside a terminal it prints the plan and changes nothing.
3. `[GROK-UI]`: create a webhook-triggered routine for the new Bot and paste the routine prompt the installer prints, then paste its Webhook URL and key into the `[LOCAL]` terminal. This Bot's routine stub names the Bot, the team and the team hash in its first command; the rules it loads put `RN_BOT=<name>` in front of every `rn-cloud.sh` command and keep its thread notes under `bots/<name>/threads/`. With `--full-prompt` the complete prompt has an extra step 0 instead ([step 0](prompts/cloud-add-bot.md)).
4. The rest is automatic. The installer wakes the new Bot through its webhook; the stub's first command runs `box/rn-cloud-add-bot.sh` before it prints the rules, which checks the shared cloud computer and registers the Bot's two sessions there, without CopyToBox or a new invite. Only once both sessions appear in the team does the installer start the Bot's relay; then it sends the Bot the one-line request `agent-mesh add-bot <name> <req>` and waits for the reply that confirms the registration and the stub. Each wait lasts up to 10 minutes.

If a wait runs out, the Bot stays pending, neither failed nor done, and its relay is not started before the sessions appear. `./update.sh` later looks for the reply by itself. `./install.sh --relay-name <name> --wake-bot` wakes the Bot again and continues where it stopped, and `./install.sh --add-bot --relay-name <name>` resumes the addition. While one addition is unfinished, no other Bot can be added; finish it or remove it with `--level bot`.

### Daily use with several Bots

- `./check.sh`, `./update.sh` and `./uninstall.sh --plan` cover every Bot without new options. `./check.sh` shows one section per Bot, headed `Bot address: <team-name>/<name> → Grok: <its name>`.
- `--webhook`, `--cloud-prompt`, `--purge-copies`, `--grok-name` alone and `--wake-bot` act on one Bot. With one Bot they work as before; with several, choose one with `--relay-name <name>`, or the installer lists the Bots and stops. For an added Bot, `--cloud-prompt` wakes it instead of printing a cloud setup prompt.
- `--restart-relay` and `--rotate-key` always cover every Bot whose relay is set up, because the relay code and the signing key are shared; they refuse `--relay-name`. If one relay does not come back, every relay returns to its previous code, plist and running state. A new key is not rolled back: `--rotate-key` says which relays run with the new key and which did not start, and the new key still has to be copied to `[BOT-CLOUD]` once.
- The filled-in reply convention lists every Bot address with its name in Grok.

### Names and removal

`agent-mesh rename` changes a Bot's address and keeps its routine, mail and paths ([Rename a Bot](#rename-a-bot)). `./uninstall.sh --level bot --relay-name <name>` removes one Bot: the engine keeps its old sessions, where mail sent to its addresses waits unread, and the cloud keeps a tombstone for each of its names. Such a name can be used again only by taking it back ([Which names a Bot can take](#which-names-a-bot-can-take)). The [uninstall guide](UNINSTALL.md#remove-one-bot) has the details. The limits of sharing one cloud computer are in the [security policy](SECURITY.md#known-limitations-not-treated-as-vulnerabilities).

## Rename a Bot

Once the installation is complete and up to date (the Bot's webhook is set and its relay runs, and no update or Bot addition is unfinished), run on `[LOCAL]`:

```bash
agent-mesh rename <new-name>
```

With several Bots, choose one with `--relay-name <current-name>` (`./install.sh --rename <new-name>` in the checkout does the same). Nothing is done in Grok: the Bot keeps its routine, its mail and thread notes on `[BOT-CLOUD]`, and its history. An added Bot keeps the paths and login service of the name it was added with.

1. It checks the new name ([below](#which-names-a-bot-can-take)), then shows the plan `Rename Bot <old> to <new>. …` for you to confirm. Outside a terminal, `--yes` confirms it in the command, except when the name is taken back from an uninstalled Bot or an earlier setup, or when unread messages for it wait on this Mac: that needs a terminal.
2. It sends the Bot the one-line request `agent-mesh rename …` and waits up to 10 minutes for the reply (one routine run). The cloud registers `<new>-cloud` and `<new>-inbox`; until the first message reaches `<new>-inbox`, the Bot keeps answering as the old name.
3. It restarts the relay under the new name, checks that the engine registered exactly `<team-name>/<new>`, and updates the deployment record and the reply convention: `Bot <old> is now <new>.` The Bot's replies come from the new address, so other agents switch by themselves.

Messages to the old address keep reaching the Bot for 30 days: the relay picks them up about once a minute and forwards them like any other message, and replies or receipts still on the way under the old name are accepted. `./check.sh` shows until when. After 30 days, mail to the old address waits unread in the engine; `./check.sh` says so and shows how to use the old name again.

If the wait runs out, nothing has changed on this Mac: run `agent-mesh rename` again to keep waiting, or `agent-mesh rename --cancel` to stop. The cloud may already hold the new name for this Bot, so after a cancel or a refusal the name stays this Bot's: no other Bot can take it, and `agent-mesh rename <new-name>` continues the same change later (a name being taken back goes through the same checks again). While a rename is unfinished, `agent-mesh update` and other installer runs stop and say so. Once the relay runs under the new name, the rename can only be finished, not cancelled; rename the Bot back afterwards if you want the old name.

### Which names a Bot can take

- A name never used in the team.
- A name the Bot itself used before: renaming back.
- Never a current or earlier name of another Bot of this installation, even after its 30 days.
- A name of a Bot uninstalled from this Mac, or a name left by an earlier setup whose three sessions are still in the team, can be taken back. The installer shows the evidence for you to check: the sessions belong to your team, `<name>` is on this Mac and the other two on the Bot's cloud computer, for a name left by an earlier setup all three offline for 24 hours, this Mac's engine holds the key of `<name>`, and no process or login service uses it. Unread messages queued on this Mac for that name are listed with their senders; after the change the Bot receives them as new messages.
- Messages already in the cloud inbox of a name taken back are set aside, so the Bot does not handle old mail again; the result says how many. `agent-mesh rename --accept-history` gives them to the Bot (one routine run); after several names taken back, it gives every batch still set aside.

`./install.sh --add-bot` checks names the same way, except that a new Bot has no earlier names of its own. When it takes back a name left by an earlier setup, the unread messages queued on this Mac for that name are set aside with the earlier ones instead of arriving as new; `agent-mesh rename --accept-history` gives them to the Bot.

## Verify

Locations: self-checks, test sends, and relay log inspection run on `[LOCAL]`; Grok Bot uses `/workspace/agent-mesh/box/rn-cloud.sh` on `[BOT-CLOUD]` for `who` and `fetch`.

1. **Self-check.** This is read-only; each `FAIL` includes a suggested fix.

   ```bash
   ./check.sh
   ```

2. **Test message.** Send a message to the Bot address (replace `<name>` with your Bot address name, which `./check.sh` prints as `Bot address:`), then wait for the reply in another terminal:

   ```bash
   reason-network send <team-name>/<name> "test 1"
   reason-network listen --as terminal --wait 600   # in another terminal
   ```

   Look for `forwarded` and `POST … code=200` in `~/Library/Logs/agent-relay.log` (for an added Bot, `~/Library/Logs/agent-relay-<name>.log`).

3. **Wake a session.** Ask Grok Bot to run `who` and send a sentence to a specific Claude Code session. The session should wake even if idle; its reply should wake Grok Bot again.
4. **Long message.** Send a 10,000-character message. Expect one wake-up and a `complete` fetch result with matching length and SHA-256. The signed message must fit within the relay's 60 KiB forwarding limit; UTF-8 byte size depends on the text.

## How agents receive messages

The last column of `reason-network who` shows the delivery mode:

| Mode | Behavior | Agents |
|---|---|---|
| **push** | Wakes an idle session. | Claude Code receives an injected message; Codex gets it through `codex queue`, which queues it in the open session; pi polls through an extension every five seconds. |
| **turn** | Normally waits for a tool call or the end of a turn. | dsh with the bridge reports `turn`, but starts a turn within roughly five seconds when idle. |
| **pull** | Does not wake automatically. | dsh without the bridge must check its inbox. |

Closed sessions disappear from the roster. Messages remain in the inbox while the recipient is offline. After reopening, you have to tell the agent to check its inbox before it reads them. Keep a session open for prompt delivery.

## Security and privacy

### Grok Bot is the trust root

- There are two copies of the signing key, and one of them is on `[BOT-CLOUD]`. Signatures stop sessions without the key from impersonating the Bot; they do not stop a manipulated Bot. If content Grok Bot reads in a web page or document steers it (prompt injection), it can still produce validly signed RN-OUT envelopes and message any of your local sessions as the Bot address `<team-name>/<name>`. Local agents should therefore treat the Bot's messages as outside input, not as instructions from you.
- Agents running as the same macOS user may still read the keys in `~/.config/agent-relay/`. A project folder is not an access-control boundary.

### Local agent permissions

- This bridge sends text to a local agent; it does not itself give Grok Bot direct file access or bypass the agent's permissions. The receiving session decides whether and how to act. The Mac opens no inbound port. Grok Bot's local always-approve and local-network switches are not required.
- That holds only if the agent does not approve automatically. Even without auto-approval, actions the session's sandbox and permission settings already allow (such as reading files or editing in the working directory) run without asking you; only actions that need human approval wait for you. The following modes let more actions run without asking, so incoming text acts as a command; start such sessions only in a sandbox folder or isolated environment:
  - Claude Code: `--dangerously-skip-permissions`, or `--permission-mode bypassPermissions`.
  - Codex: `--yolo` (that is, `--dangerously-bypass-approvals-and-sandbox`: no prompts and no sandbox); `--sandbox danger-full-access` (no sandbox); `--ask-for-approval never` (never asks: what the sandbox allows runs, the rest fails); and `--approve-for-me`, which hands approvals to automatic review.
- **Unattended wake-ups**: push sessions wake even when you are away. Reason Network v0.10.7 wakes Codex with `codex queue --thread <id> --message`: the message is queued in that already open session and runs as a new turn with the approval and sandbox settings the session started with; no flag loosens them. Tested with Codex 0.166.0 (`--ask-for-approval on-request --sandbox read-only`, where writing a file needs human approval): when the woken turn needed to write a file, the approval prompt appeared in that session's terminal and waited; nothing ran without confirmation, and after about a minute it had not approved itself. Without auto-approval, actions that need human approval therefore wait at the prompt until you return, while actions the sandbox and permission settings already allow still run; with auto-approval, nobody stops them. Claude Code receives the message directly in the open session; this project has not separately tested its approvals while unattended.
- To hold incoming messages, set `[inbound] default = "hold"` in `~/.reason/network/config.toml`, then use `reason-network approve` to release them.

### Message bodies are stored in plain text

Message bodies remain in plain text in the following places, so **do not send keys, passwords, tokens or other secrets through this channel**:

| Location | Content | Kept for |
|---|---|---|
| `[LOCAL]` `~/.reason/network/network.db`, `[BOT-CLOUD]` `/workspace/agent-mesh/box/state/network.db` | Messages each engine received, kept after they are read | Not deleted by the engine |
| `[LOCAL]` `~/.local/lib/agent-mesh/engine-backup-*/state`, `[BOT-CLOUD]` `/workspace/agent-mesh/box/engine-backup-*/state` | A copy of the engine state, including received messages, taken before an engine upgrade | Until you delete it; `./uninstall.sh --level identity` removes both |
| `[LOCAL]` `~/Library/Application Support/agent-relay/fwd-copies/` | The relay's copies of forwarded bodies, kept for re-forwards | After `FWD_COPY_DAYS` (default 7 days) they are no longer re-forwarded, but they are deleted only when later mail passes through the relay (checked at most hourly); an idle relay keeps them |
| `[BOT-CLOUD]` `/workspace/agent-mesh/inbox/<id>/full.txt` (an added Bot: `/workspace/agent-mesh/bots/<name>/inbox/<id>/full.txt`) | Bodies retrieved by `fetch` | Not deleted automatically |
| `[BOT-CLOUD]` `/workspace/agent-mesh/threads/*.md` (an added Bot: `/workspace/agent-mesh/bots/<name>/threads/*.md`) | Thread notes: the conversation summaries the routine appends each turn | Not deleted automatically |
| `[LOCAL]` `~/Library/Application Support/agent-relay/state.json` | Doorbells waiting for the webhook, each with its body preview | Removed once the webhook delivers them; while the webhook keeps failing they stay, and only the oldest are dropped past `MAX_SPOOL`. Changing `DOORBELL_PREVIEW_CHARS` affects later doorbells only; fix the webhook so the backlog goes out |
| The routine's webhook | A preview of the first 200 characters in each doorbell | Up to Grok Bot; `DOORBELL_PREVIEW_CHARS=0` turns previews off |

The relay log records message ids, lengths and checksums, not bodies. `FWD_COPY_DAYS` and `DOORBELL_PREVIEW_CHARS` are set in `~/.config/agent-relay/env`; restart the relay with `launchctl kickstart -k "gui/$(id -u)/com.local.agent-relay"` for a change to take effect. An added Bot keeps its own settings, relay state and copies in the paths listed under [Several Grok Bots](#several-grok-bots), and its service is `com.local.agent-relay.<name>`. To clean up:

- On `[LOCAL]`, run `./install.sh --purge-copies` to delete all copies the relay kept (with several Bots, add `--relay-name <name>`; it deletes that Bot's copies). Re-forward requests for those messages then get `no-copy`, and the sender has to send again.
- For `[BOT-CLOUD]`, ask Grok Bot in `[GROK-UI]` to run `/workspace/agent-mesh/box/rn-cloud.sh purge <days>`. It stops the cloud engine, then deletes received messages (the cloud `network.db`), `inbox/<id>/`, misdirected mail logs in `inbox/_stray/`, and `threads/*.md` older than that many days; `0` deletes all of them. The thread notes give the routine its context, so the Bot no longer remembers earlier conversations afterwards. The cloud clone must first be updated to a version that has this command. It is refused while the cloud computer serves more than one Bot, because it cannot tell their mail apart.
- The engine manages the local `network.db`; this project offers no partial cleanup for it. `./uninstall.sh --level identity` and `--level all` remove all of `~/.reason/network`.

### Team members

- The team is the trust boundary. The relay forwards a message from any team member to the Bot and wakes it, without checking who sent it; the forwarded envelope keeps the original sender, and the hourly wake-up limit still applies. This lets you add another computer or Bot without extra settings, so keep the team limited to your own machines.
- After setup, and periodically afterwards, run `reason-network team` and confirm that only your local computer and `cloud` are listed. `./check.sh` checks this too and reports FAIL for any other member. Remove an unknown person or machine with `reason-network remove <person>/<machine>`, then replace the signing key as described below.
- Team members can see session names, the last working-directory component, repository names, and branches. Avoid starting agents directly in your home directory. Use invite links in the terminal, not a browser.

### Replacing keys

- **Signing key**: on `[LOCAL]`, run `./install.sh --rotate-key --lang en`. It generates a new `outbound.key`, restarts the relay (with several Bots, every relay, since they share the key), and prints the CopyToBox steps: in `[GROK-UI]`, have Grok Bot use CopyToBox to copy the new key from `[LOCAL]` `~/.config/agent-relay/outbound.key` to `[BOT-CLOUD]` `/workspace/agent-mesh/box/outbound.key`, approve the copy, and run `chmod 600` on the cloud copy. Until the cloud has the new key, signature checks fail in both directions. Messages forwarded before the rotation that the cloud has not fetched yet keep their old-key signature: after the rotation `fetch` reports them as `mismatch` (bad signature) and requests no re-forward; each is reported again whenever a doorbell asks for it, and once by the scans no doorbell asked for. If the old key did not leak, have the Bot fetch pending mail before rotating; otherwise ask the senders to resend, and clean up the old messages with `rn-cloud.sh purge`.
- **Webhook URL or key**: regenerate the routine's key in `[GROK-UI]`; if the interface cannot, delete and recreate the routine. Then run `./install.sh --webhook --lang en` on `[LOCAL]` and enter the new values.
- The complete leak response is in the [security policy](SECURITY.md#if-you-suspect-a-credential-leak).

### Network and usage

- The pinned open-source engine runs locally in account-free mode, and in that mode it sends no data to Reason. Messages are end-to-end encrypted between the engines and a network relay only forwards them, as upstream describes it; this project has not independently audited the network relays.
- Network relays: version 0.10.7 uses n0's public iroh relays by default, 0.12 uses Reason's. A team can pick its own network relay with `reason-network relay <URL>` (`reason-network relay default` goes back to the engine's default). Every machine in the team, including `[BOT-CLOUD]`, must run the same command (there: `REASON_NETWORK_HOME=/workspace/agent-mesh/box/state /workspace/agent-mesh/box/bin/reason-network relay <URL>`; the engine daemon stops and starts again with the next command); a machine that was offline must run it when it comes back. With an explicit network relay, short invite codes do not work; use the long invite form, which `./install.sh --cloud-prompt` prints anyway.
- **Forgotten sessions (Reason Network 0.12 only):** an ordinary agent session that has been offline for more than a day is forgotten, and its unread messages are deleted with it. The engine starts doing this once its daemon has been running for six hours. The relay's mailbox on `[LOCAL]` and the cloud's mailboxes on `[BOT-CLOUD]` (sessions started with `listen --as`) are exempt. Version 0.10.7 never forgets sessions, so the rule applies only after an upgrade. Read a session's messages before leaving it offline for long.
- Each Grok Bot wake-up consumes usage. The relay defaults to at most 20 wake-ups per hour.
- Report security problems privately as described in the [security policy](SECURITY.md), not in a public issue.

## Troubleshooting

- **Cannot reach the network relay:** `./check.sh` and `install.sh` name the network relay they probed. Retry the probe with the VPN/proxy disabled. If that resolves it, add a direct/bypass rule covering `iroh.link` and `reasonmachines.com` with all subdomains, including in TUN, virtual-NIC, enhanced, or global modes. Exclude both domains from HTTPS decryption/MITM too. Check the certificate issuer (use the probed network relay's address in place of `aps1-1`):

  ```bash
  curl -sv -o /dev/null https://aps1-1.relay.n0.iroh.link/ 2>&1 | grep issuer
  ```

  Expect a public certificate authority (currently Let's Encrypt) rather than your proxy's CA. If it still fails without the proxy, investigate DNS, firewall rules, or the network. The engine can use several network relays; allowing only the probe host is insufficient. `check.sh` reports SKIP `relay reachability unknown` when it cannot tell which network relay a 0.12 engine uses (it has not logged one yet); allow both domains and run it again later.
- **Cloud join reports `couldn't reach the machine that made the invite`:** usually the Mac's VPN or proxy is intercepting `*.iroh.link` again. Fix it as in the previous item, run `reason-network stop` on the Mac, then have Grok Bot retry with the same invite while valid.
- **Session missing from `who`:** reopen sessions started before `setup`. Codex also needs to complete a turn before appearing.
- **`fetch` reports `missing`:** inspect `refwd`. `ack=resent` means the relay re-forwarded it; retry after 60 seconds. `no-copy`, `limit`, `failed`, or `skipped` means recovery did not succeed; ask the sender to resend. No `refwd` field means the Mac was not connected.
- **Relay log shows `webhook POST failed: 30x`:** `WEBHOOK_URL` is not the final address; the relay does not follow redirects. Copy the webhook URL again from the routine in `[GROK-UI]`, then run `./install.sh --webhook --lang en` on `[LOCAL]`.
- **Message `refused`:** the engine's loop guard refuses more than 12 messages between a pair of agents within ten minutes. Body forwarding uses the exempt terminal identity.
- **The relay names are not derived from one name, or the relay registered under another name:** `install.sh`, `update.sh` or `check.sh` says so. The three names in `~/.config/agent-relay/env` (`LISTEN_NAME`, `CLOUD_NAME`, `INBOX_NAME`) must be `<name>`, `<name>-cloud` and `<name>-inbox`; the installer writes them, do not edit them. A registration such as `<team-name>/<name>-2` means another session on this computer or in the team already had the name. The engine keeps that pick for the relay even after the other session is gone, so the check keeps failing; remove the installation including its identity ([level `identity`](UNINSTALL.md#remove-the-identity-or-everything)) and install again with another `--relay-name`.
- **`rn-cloud.sh` says `box/relay is damaged`, or replies never arrive after `box/relay` was deleted:** `/workspace/agent-mesh/box/relay` holds the Bot address name on `[BOT-CLOUD]`; without it the cloud uses `bot`. On `[LOCAL]`, run `./install.sh --cloud-prompt --lang en` and give Grok Bot the printed prompt, which contains the full invite link and your name; `rn-cloud-setup.sh` then sees that the cloud is already in the team, skips joining, and writes the file again (a damaged file is kept as `box/relay.damaged-<time>`). It writes a name other than `bot` only if the cloud engine shows that it already registered `<name>-inbox` or `<name>-cloud`; otherwise only an uninstall at level `identity` or `all` and a new install help. Check with `cat /workspace/agent-mesh/box/relay` and one real message.
- **`rn-cloud-setup.sh` stops at `join` on a cloud that already joined:** give it the full invite link (the one containing `tincan1…`, as `./install.sh --cloud-prompt` prints it). A short invite code cannot show that the cloud is in the same team.
- **`check.sh` reports the engine as older, the daemon as running a replaced binary, or an unfinished engine upgrade:** run `./update.sh` in a terminal. It upgrades an older engine, offers to restart a daemon that started before the binary was replaced, and rolls an unfinished switch back first; the [upgrade guide](UPGRADE.md#reason-network-engine) explains each step and the backup.
- **`check.sh` reports `relay not registered on the running engine`:** right after a daemon or relay restart it reports SKIP for up to 11 minutes while the relay registers again. A FAIL after that means the relay has not listened on the running daemon: see `tail -n 30 ~/Library/Logs/agent-relay.log`, then run `./install.sh --restart-relay`, which fails and puts the previous relay back if the new one does not register.
- **`install.sh --cloud-prompt` or `./update.sh` says the release `v<VERSION>` could not be fetched, or that a file differs from the release:** the cloud installs only a published release tag, and the cloud scripts in this checkout (`box/rn-cloud.sh`, `box/rn-fetch.py`, `box/rn-cloud-setup.sh`, `box/rn-engine.py`, `box/rn-cloud-add-bot.sh`, `box/rn-routine.py`, `box/rn-names.py`, `box/rn-cloud-update.sh` and the rules templates `prompts/routine-body*.md`) must match it. Check the network, and that `--repo-url` names the repository the cloud installs from; a checkout ahead of the latest release has to wait for it, or go back to the released files. Nothing was changed.
- **Grok Bot's command block prints a `STOP:` line instead of `READY <tag>` or its `agent-mesh-result` lines:** the block changed nothing, or put the previous commit back (it says `restored …`). After `the engine step failed`, the clone stays at the checked release and the engine step's own lines above say what happened to the engine. Send Grok Bot's full output with the line to whoever maintains your setup, and do not run the cloud scripts while it says `could not restore`. A local change in `/workspace/agent-mesh` has to be looked at first; never reset it blindly. If it says a different tag of the same name is already there, have Grok Bot delete that tag with `git -C /workspace/agent-mesh tag -d <tag>` and send the same block again; the block still checks the commit and the checksums.
- **`rn-cloud.sh` stops with `box/bin/engine/reason-network is not a Reason Network build pinned by agent-mesh`:** the engine's bytes are not a pinned build, and it is not run. Pause the routine in `[GROK-UI]`, have Grok Bot run `bash /workspace/agent-mesh/box/rn-cloud-setup.sh --upgrade-engine` directly in chat, which replaces the engine with the pinned version without running the old one, then resume the routine. If it says `the engine has not been moved to box/bin/engine yet`, run `./update.sh` on `[LOCAL]`; its cloud update moves the engine.
- **`rn-cloud.sh` says the launcher had been replaced, or `--upgrade-engine` prints `engine: note: other engine daemons run from box/bin`:** another engine daemon, started from `box/bin/reason-network` before this version, updated itself. It cannot replace the engine at `box/bin/engine`, and `rn-cloud.sh` puts the launcher back each time. Stop each daemon that is not needed with `REASON_NETWORK_HOME=<its state directory> REASON_NETWORK_NO_UPDATE=1 /workspace/agent-mesh/box/bin/engine/reason-network stop`, using the state the note prints; never point it at `box/state`, which is the cloud's own engine.
- **`<` became `[`:** version 0.10.7's `listen` rewrites some tags to deter spoofing without changing the character count. Re-forwards use the relay's stored copy.

## Uninstall

Update this folder first (`git pull`), so that `./uninstall.sh` is the uninstaller described here; the script in v0.4.1 and earlier has no levels, removes the relay settings and signing key even without `--all`, and does not handle the cloud.

`./uninstall.sh` runs on `[LOCAL]`. Without options it only lists what is installed and removes nothing; `./uninstall.sh --plan` adds what each level would remove and keep.

| Level | What it removes |
|---|---|
| `--level program` | The relay program and its login service only. The team, the keys and the cloud stay as they are, and `./install.sh --restart-relay` brings the relay back. This is the only level an agent may run for you. |
| `--level bot --relay-name <name>` | Only one Bot, when another stays: its relay service, settings, state, logs and entry in the deployment record, and on `[BOT-CLOUD]` its mail and thread notes. The team, the engine, the signing key and the other Bots stay; another Bot runs the cloud step. Its names can be used again only by taking them back. |
| `--level identity` | Also the team identity, signing key, relay settings and relay state: this computer and the cloud both leave the team, and `~/.reason/network` is removed. |
| `--level all` (or `--all`) | Also the logs, the engine binary, and on `[BOT-CLOUD]` `inbox/`, `threads/` and `/workspace/agent-mesh`. |

Levels `program`, `identity` and `all` cover every Bot on this Mac. Levels `bot`, `identity` and `all` run only in your own terminal and include a step in Grok Bot; `identity` and `all` disconnect every agent on this computer from Reason Network. Read the [uninstall guide](UNINSTALL.md) first: the full list of what each level removes and keeps, what to check beforehand, the steps on both computers, and how to confirm the result.

## Upgrade

The local repository, the clone on the Bot's cloud computer and the saved routine prompt are separate: changing files on the Mac does not update the other two. A reply convention already appended to a project's `AGENTS.md` is not updated either; copy the template again. The [changelog](CHANGELOG.md) says which parts each version changes.

In the repository on `[LOCAL]`, run:

```bash
./update.sh --lang <en|zh-CN>
```

It moves the checkout to the newest release, compares each part with what this computer deployed and asks once before changing anything. It updates the relay, the dsh plugin and the local engine itself; when the cloud changed it sends Grok Bot an update request from this Mac and checks Grok Bot's reply; when the routine stub changed it shows the new one for you to paste and then asks the Bot to confirm it. A change of the rules alone needs no paste, because the stub loads them from the cloud. `./update.sh --manual` does the cloud step by hand. After the first run, `agent-mesh update` and `agent-mesh check` run the scripts of this checkout from any folder. `./update.sh --plan` only shows the comparison and the steps and changes nothing in the deployment. With several Bots one run covers all of them: one engine switch for every relay, one cloud update, and the new prompt for each Bot whose prompt changed ([Several Bots](UPGRADE.md#several-bots)). The [upgrade guide](UPGRADE.md) has the details, the manual upgrade order and the rollback order. agent-mesh 0.7.0 also upgrades Reason Network to 0.12.17 on both computers, with a backup and a rollback on failure: see [Reason Network engine](UPGRADE.md#reason-network-engine).

## Documentation and language

| Guide | English | 简体中文 |
|---|---|---|
| User guide | [README](README.md) | [使用指南](README.zh-CN.md) |
| Agent installation | [Runbook](AGENT-INSTALL.md) | [安装手册](AGENT-INSTALL.zh-CN.md) |
| Uninstall | [Guide](UNINSTALL.md) | [卸载指南](UNINSTALL.zh-CN.md) |
| Upgrade | [Guide](UPGRADE.md) | [升级指南](UPGRADE.zh-CN.md) |
| Protocol reference | [Reference](PROTOCOL.md) | [协议参考](PROTOCOL.zh-CN.md) |
| DeepSeek Harness | [Bridge](dsh/README.md) | [插件指南](dsh/README.zh-CN.md) |
| Grok Bot routine | [Prompt](prompts/routine.md) | [提示词](prompts/routine.zh-CN.md) |
| Cloud setup | [Prompt](prompts/cloud-setup.md) | [提示词](prompts/cloud-setup.zh-CN.md) |
| Added Bot, step 0 | [Prompt](prompts/cloud-add-bot.md) | [提示词](prompts/cloud-add-bot.zh-CN.md) |
| Cloud uninstall | [Prompt](prompts/cloud-uninstall.md) | [提示词](prompts/cloud-uninstall.zh-CN.md) |
| Reply convention | [Template](templates/AGENTS.reason-network.md) | [模板](templates/AGENTS.reason-network.zh-CN.md) |
| Maintaining translations | [Contributing](CONTRIBUTING.md) | [维护说明](CONTRIBUTING.zh-CN.md) |
| Security policy | [Security policy](SECURITY.md) | [安全策略](SECURITY.zh-CN.md) |

To choose the language of generated prompts, see [Prompt language](#prompt-language).

## Appendix: message formats

The signed message formats (RN-OUT, RN-FWD, RN-REFWD, RN-REFWD-ACK, RN-GOT), the doorbell JSON and their test vectors are in the [protocol reference](PROTOCOL.md#message-formats).
