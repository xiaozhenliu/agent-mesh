# agent-mesh: runbook for the installing agent

**English** | [简体中文](AGENT-INSTALL.zh-CN.md)

You are a coding agent (Claude Code, Codex or similar) running on the user's Mac. The user wants their local agent sessions and their cloud Grok Bot in one private Reason Network team, with wake-ups in both directions. This file is your instruction set; [README.md](README.md) is the English reference and [README.zh-CN.md](README.zh-CN.md) is the Chinese reference.

Speak to the user in their language. Keep status updates short. Run commands from the public distribution root. Select `--lang en` or `--lang zh-CN` from the user's preference and pass it on every installer invocation; examples below use English. Language controls printed prompts; diagnostics remain English.

## Ground rules

- `[LOCAL]` = the user's local computer (Mac); `[BOT-CLOUD]` = Grok Bot's cloud computer (machine name `cloud`); `[GROK-UI]` = the Grok Bot interface opened by the user. Labels are not part of commands.
- You are the `[LOCAL]` installing agent. Unlabeled commands in this runbook run on `[LOCAL]`; Grok Bot executes cloud commands on `[BOT-CLOUD]`. Each side has its own repository and state directories; they are not interchangeable.
- Steps identify both location and responsible party. Never do a user step yourself; never ask the user to do an agent step. The user performs `[GROK-UI]` actions.
- **Never read, print or paste** `~/.config/agent-relay/env`, `~/.config/agent-relay/outbound.key`, or a Webhook key. You may check that files exist.
- `install.sh` detects that it is not running in a terminal and stops wherever the user has to act, printing what to do. Relay that text to the user verbatim.
- Run `./check.sh` when a stage says so and paste its output to the user as is. A `FAIL` line names the fix; do not improvise.
- Do not install anything else, do not change shell config beyond the PATH line in stage 0, do not edit other agents' config files (`reason-network setup` does that, after the user approves its plan).
- If the machine is not macOS, stop: `install.sh` supports macOS only.

## Stage 0: prerequisites ([LOCAL] installing agent)

1. Check both `[LOCAL]` and `[BOT-CLOUD]` dependencies in the guide's Prerequisites first. On `[LOCAL]`, `uname -s` must print `Darwin` and the CPU must be arm64 or x86_64; use a regular user signed into the desktop, without sudo. Run `git --version` and `python3 -c 'import sys, ssl, sqlite3; print(sys.version)'`; Git must work and Python must be 3.9+. If Git/Python is missing, run `xcode-select --install`, wait for the user to finish, then check again; starting installation does not mean it is ready. The user's agents must already be installed, signed in, and working independently.
2. `curl -sS -o /dev/null -m 10 -w '%{http_code}\n' https://aps1-1.relay.n0.iroh.link/` must print `200`. If it fails, retry with the VPN/proxy fully off. If that resolves it, add a direct/bypass rule for `iroh.link` and all subdomains (including TUN, virtual-NIC, enhanced, or global modes), and exclude the domain from HTTPS decryption/MITM. `curl -sv -o /dev/null https://aps1-1.relay.n0.iroh.link/ 2>&1 | grep issuer` should show a public CA (currently Let's Encrypt), not the proxy's own CA. The engine may use multiple relays, so cover the whole domain; cover `reasonmachines.com` and its subdomains the same way, because Reason Network 0.12 uses Reason's relays there by default. If it still fails without the proxy, investigate DNS, firewall rules, and the network. Continue only after the probe returns 200.
3. If `$HOME/.local/bin` is not in PATH, prefix later Bash/zsh commands with `export PATH="$HOME/.local/bin:$PATH";`. Before persisting it, identify the user's actual shell: use `~/.zshrc` for zsh, the startup file actually read by Bash, or `fish_add_path "$HOME/.local/bin"` for fish; do not always edit zsh configuration. Check the path, version, and default state directory of any existing Reason Network installation. Report incompatibility before proceeding; do not overwrite another installation or immediately uninstall it. If the repository directory exists, review its origin and local changes first; the local directory can be chosen freely.
4. Ask the user, in one message:
   - Which of Claude Code, Codex, pi, DeepSeek Harness they use. Anything else is out of scope; say so.
   - A **team name**, which is also their name inside the team: 1-40 ASCII lowercase letters, digits, or `-`, with no leading/trailing or repeated hyphen; not their real name; cannot be changed later.
   - A **Bot address name**, default `bot`: lowercase letters and digits joined by single `-`, at most 34 characters, not `terminal`; cannot be changed later.
   - Whether their Grok Bot can create a webhook-triggered routine and use CopyToBox (both required).
   - That the Mac will stay awake (System Settings: prevent sleep on power; a closed lid stops everything).

## Stage 1: local installation ([LOCAL] installing agent)

1. `cd` into the public distribution and run `./install.sh --name <team-name> --relay-name <relay-name> --lang en`.
   It installs Reason Network, creates the team, prints the `reason-network setup --dry-run` plan and exits with code 3 because nothing can be confirmed without a terminal.
2. Show the plan to the user and ask "OK to apply?". On yes, run `./install.sh --name <team-name> --relay-name <relay-name> --yes --lang en`.
   It applies setup, generates the signing key, prints the routine prompt and exits 0 at the webhook step.
3. Give the user, verbatim, the text the script printed between the dashed lines, with this instruction: "In `[GROK-UI]`, create a routine triggered by a webhook and paste this as its prompt. The panel then shows a Webhook URL and a key. Open a terminal on `[LOCAL]`, your local computer, run `./install.sh --webhook --lang en` in this repository folder and paste both there. Tell me when it says `relay up`." Use the selected language in that command too. The Bot will execute the routine prompt on `[BOT-CLOUD]`; the local installing agent must not run its commands.
4. Wait for the user. Then run `./check.sh`: everything under relay must be PASS. `cloud machine` may still be SKIP.

## Stage 2: connect the Bot's cloud computer (cross-computer handoff)

1. **`[LOCAL]` Installing agent:** run `./install.sh --cloud-prompt --lang en`. It prints the team invite (valid 24 hours while this Mac is online) and the instructions for Grok Bot, with the team name, the Mac home directory and this repository's URL filled in. Verify that the URL points to the public distribution, whose root contains `box/rn-cloud-setup.sh`; if the URL is missing or that repository's root lacks the script, replace it before handing the prompt to the user. If it is an SSH URL and `[BOT-CLOUD]` has no GitHub credentials, use the same public repository's HTTPS URL; do not copy the user's SSH private key to the cloud.
2. **`[GROK-UI]` User → `[BOT-CLOUD]` Grok Bot:** give the user the whole printed block and say: "Send this to the Bot as one message in the Grok Bot interface. On its cloud computer it will clone the repository and join the team, then ask you to approve CopyToBox: source `[LOCAL]` `<mac-home>/.config/agent-relay/outbound.key`, destination `[BOT-CLOUD]` `/workspace/agent-mesh/box/outbound.key`. Tell me when it replies that the cloud is in."
3. **`[LOCAL]` Installing agent:** wait for the user, then run `./check.sh`: `cloud machine joined` must be PASS. This verifies cloud team membership, not successful messaging in both directions.

## Stage 3: sessions and smoke test

1. **`[LOCAL]` User:** close and reopen any Claude Code or Codex session that was open before stage 1; Codex asks once, choose "Trust all and continue".
2. **`[LOCAL]` Installing agent:** run `reason-network send <team-name>/<relay-name> "test 1"`. Here `<relay-name>` is the relay address on the Mac (`bot` by default; the installer prints it as `Bot address:`). Expected within 30 seconds: the local `~/Library/Logs/agent-relay.log` gains a `forwarded` line and a `POST … code=200` line. Grok Bot wakes, retrieves the body and replies on `[BOT-CLOUD]`; the reply may reach the user's local session or be collected with `reason-network listen --as terminal --wait 120` on `[LOCAL]`.
3. **`[GROK-UI]` User → `[BOT-CLOUD]` Grok Bot:** ask the Bot to run `/workspace/agent-mesh/box/rn-cloud.sh who` on its cloud computer and send one sentence to a specific `[LOCAL]` session, for example `<team-name>/<folder>-claude`. Expected: the sentence appears inside the local session even if it was idle; when the local session replies, Grok Bot is woken again.
4. Report both results as they happened. Do not claim success for a test you did not observe.

## Optional

All operations below run on `[LOCAL]` and modify local app or repository files.

- DeepSeek Harness auto-wake: `dsh/mac-install.sh <team-name>`, then the user quits and reopens the app. See the [bridge guide](dsh/README.md).
- Reply convention: append the [English template](templates/AGENTS.reason-network.md) or [Chinese template](templates/AGENTS.reason-network.zh-CN.md) to the `AGENTS.md` of repositories the user works in; for Claude Code also add `@AGENTS.md` to that repository's `CLAUDE.md`.

## If something fails

Repair commands below run on `[LOCAL]`; invite retries and `fetch` run on `[BOT-CLOUD]`, performed by Grok Bot.

- Cloud side reports `couldn't reach the machine that made the invite`: stage 0 step 2 regressed (the Mac's VPN/proxy is intercepting `*.iroh.link` again). Fix it as in stage 0 step 2, run `reason-network stop` on the Mac, and have Grok Bot retry with the same invite.
- Relay log shows `webhook POST failed`: check the Webhook URL and key by asking the user to run `./install.sh --webhook --lang en` again (substitute their chosen language). Do not read the env file.
- `install.sh` stops because the Mac is already in a team under another name: names cannot change. Only on the user's explicit decision, ask the user to run `./uninstall.sh --level identity` in their own terminal (it needs typed confirmation and a step with Grok Bot), then start again from stage 1.
- Anything else: show the user the exact error and the matching README section.

## Update

`[LOCAL]`: for an existing installation, run `./update.sh --plan --lang <en|zh-CN>` (the language the user chose) and show the user its output. When only the relay or the dsh plugin differ, you may run `./update.sh --yes --lang <en|zh-CN>`. Exit code 3 means the update needs the user's answers about the routine and the Bot's cloud computer: ask the user to run `./update.sh --lang <en|zh-CN>` in their own terminal. Do not edit `~/.local/lib/agent-mesh/deployed.json`. First update from v0.3.0 or earlier: run `git pull` once, because older checkouts have no `update.sh`.

## Removal

`[LOCAL]`: run `git pull` first, then `./uninstall.sh --plan` and show the user its output; it lists what is installed and what each level removes, and changes nothing. With the user's consent you may run `./uninstall.sh --level program --yes`, which removes only the relay program; `./install.sh --restart-relay` brings it back. Levels `identity` (also leave the team on both computers and remove keys and identity) and `all` (also logs, the engine and the cloud's data) need typed confirmation and a step in Grok Bot: ask the user to run `./uninstall.sh --level identity` or `--level all` in their own terminal and follow the [uninstall guide](UNINSTALL.md). Without a terminal they exit with code 3. All levels keep this folder. Never remove files by hand instead, and never touch a deployment the uninstaller lists as foreign.
