# agent-mesh: runbook for the installing agent

**English** | [简体中文](AGENT-INSTALL.zh-CN.md)

You are a coding agent (Claude Code, Codex or similar) running on the user's Mac. The user wants their local agent sessions and their cloud Grok Bot in one private Reason Network team, with wake-ups in both directions. This file is your instruction set; [README.md](README.md) is the English reference and [README.zh-CN.md](README.zh-CN.md) is the Chinese reference.

Speak to the user in their language. Keep status updates short. Run commands from the public distribution root. Select `--lang en` or `--lang zh-CN` from the user's preference and pass it on every installer invocation; examples below use English. Language controls printed prompts; diagnostics remain English.

## Ground rules

- Steps are marked **you** or **user**. Never do a user step yourself; never ask the user to do an agent step.
- **Never read, print or paste** `~/.config/agent-relay/env`, `~/.config/agent-relay/outbound.key`, or a Webhook key. You may check that files exist.
- `install.sh` detects that it is not running in a terminal and stops wherever the user has to act, printing what to do. Relay that text to the user verbatim.
- Run `./check.sh` when a stage says so and paste its output to the user as is. A `FAIL` line names the fix; do not improvise.
- Do not install anything else, do not change shell config beyond the PATH line in stage 0, do not edit other agents' config files (`reason-network setup` does that, after the user approves its plan).
- If the machine is not macOS, stop: this project is tested on macOS only.

## Stage 0: prerequisites (you)

1. `uname -s` must print `Darwin`; `python3 --version` must succeed (otherwise: `xcode-select --install`).
2. `curl -sS -o /dev/null -m 10 -w '%{http_code}\n' https://aps1-1.relay.n0.iroh.link/` must print `200`. If it fails, retry with the VPN/proxy fully off. If that resolves it, add a direct/bypass rule for `iroh.link` and all subdomains (including TUN, virtual-NIC, enhanced, or global modes), and exclude the domain from HTTPS decryption/MITM. `curl -sv -o /dev/null https://aps1-1.relay.n0.iroh.link/ 2>&1 | grep issuer` should show a public CA (currently Let's Encrypt), not the proxy's own CA. The engine may use multiple relays, so cover the whole domain. If it still fails without the proxy, investigate DNS, firewall rules, and the network. Continue only after the probe returns 200.
3. If `$HOME/.local/bin` is not in PATH, append `export PATH="$HOME/.local/bin:$PATH"` to `~/.zshrc` and tell the user. Your own shell will not re-read that file: prefix every later command in this runbook with `export PATH="$HOME/.local/bin:$PATH";` (the scripts themselves use absolute paths).
4. Ask the user, in one message:
   - Which of Claude Code, Codex, pi, DeepSeek Harness they use. Anything else is out of scope; say so.
   - A **team name**, which is also their name inside the team: 1-40 ASCII lowercase letters, digits, or `-`, with no leading/trailing hyphen; not their real name; cannot be changed later.
   - Whether their Grok Bot can create a webhook-triggered routine and use CopyToBox (both required).
   - That the Mac will stay awake (System Settings: prevent sleep on power; a closed lid stops everything).

## Stage 1: Mac side (you)

1. `cd` into the public distribution and run `./install.sh --name <team-name> --lang en`.
   It installs Reason Network, creates the team, prints the `reason-network setup --dry-run` plan and exits with code 3 because nothing can be confirmed without a terminal.
2. Show the plan to the user and ask "OK to apply?". On yes, run `./install.sh --name <team-name> --yes --lang en`.
   It applies setup, generates the signing key, prints the routine prompt and exits 0 at the webhook step.
3. Give the user, verbatim, the text the script printed between the dashed lines, with this instruction: "In Grok Bot, create a routine triggered by a webhook and paste this as its prompt. The panel then shows a Webhook URL and a key. Open your own terminal, run `./install.sh --webhook --lang en` in this folder and paste both there. Tell me when it says `relay up`." Use the selected language in that command too.
4. Wait for the user. Then run `./check.sh`: everything under relay must be PASS. `cloud machine` may still be SKIP.

## Stage 2: cloud side (user and Grok Bot)

1. Run `./install.sh --cloud-prompt --lang en`. It prints the team invite (valid 24 hours while this Mac is online) and the instructions for Grok Bot, with the team name, the Mac username and this repository's URL filled in. Verify that the URL points to the public distribution, whose root contains `box/rn-cloud-setup.sh`; if the URL is missing or that repository's root lacks the script, replace it before handing the prompt to the user.
2. Give the user the whole printed block and say: "Send this to Grok Bot as one message. It will clone this repository on its cloud machine, join the team, and ask you once to approve copying the signing key with CopyToBox. Tell me when it replies that the cloud is in."
3. Wait for the user. Then run `./check.sh`: `cloud machine joined` must be PASS.

## Stage 3: sessions and smoke test

1. Tell the user: any Claude Code or Codex session that was open before stage 1 must be closed and reopened; Codex asks once, choose "Trust all and continue".
2. (you) `reason-network send <team-name>/bot "test 1"`. Expected within 30 seconds: `~/Library/Logs/agent-relay.log` gains a `forwarded` line and a `POST … code=200` line. Grok Bot wakes and may reply into the user's session or to `reason-network listen --as terminal --wait 120`.
3. (user) Ask Grok Bot to run `who` and send one sentence to a specific session, for example `<team-name>/<folder>-claude`. Expected: the sentence appears inside that session even if it was idle; when the session replies, Grok Bot is woken again.
4. Report both results as they happened. Do not claim success for a test you did not observe.

## Optional

- DeepSeek Harness auto-wake: `dsh/mac-install.sh <team-name>`, then the user quits and reopens the app. See the [bridge guide](dsh/README.md).
- Reply convention: append the [English template](templates/AGENTS.reason-network.md) or [Chinese template](templates/AGENTS.reason-network.zh-CN.md) to the `AGENTS.md` of repositories the user works in; for Claude Code also add `@AGENTS.md` to that repository's `CLAUDE.md`.

## If something fails

- Cloud side reports `couldn't reach the machine that made the invite`: stage 0 step 2 regressed (the Mac's VPN/proxy is intercepting `*.iroh.link` again). Fix it as in stage 0 step 2, run `reason-network stop` on the Mac, and have Grok Bot retry with the same invite.
- Relay log shows `webhook POST failed`: check the Webhook URL and key by asking the user to run `./install.sh --webhook --lang en` again (substitute their chosen language). Do not read the env file.
- `install.sh` stops because the Mac is already in a team under another name: names cannot change. Only on the user's explicit decision run `./uninstall.sh --all -y`, then start again from stage 1.
- Anything else: show the user the exact error and the matching README section.

## Removal

`./uninstall.sh` removes the relay; `./uninstall.sh --all` also removes Reason Network and leaves the team. Both keep this folder. Ask the user before running either.
