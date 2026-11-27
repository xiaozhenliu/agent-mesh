# Maintaining documentation

**English** | [简体中文](CONTRIBUTING.zh-CN.md)

## One set of user guides

This distribution is self-contained. Keep links relative and inside this distribution so a standalone checkout works.

English uses the original filenames (`README.md`, `AGENT-INSTALL.md`, and so on). Simplified Chinese uses `.zh-CN.md` beside each English file. Both languages cover the same installation steps, restrictions, recovery behavior, and protocol. When changing behavior, update both versions in the same change. English is the default entry point; the implementation determines technical behavior.

## Release versions

`VERSION` is the agent-mesh project version (`major.minor.patch`); the installer reports it with `./install.sh --version`. Reason Network v0.12.17 is a separate pinned dependency: `VERSION` in `install.sh` and `box/rn-cloud-setup.sh`, each with the same table of release SHA-256 checksums for all four targets. Change both scripts together; `tests/cli/test_engine.py` checks that they agree. For a release, update `VERSION`, both README version labels, `llms.txt`, and both changelogs together. Record compatibility changes and upgrade instructions. The documentation checker verifies the README and changelog versions. Use `v<VERSION>` for an annotated tag on the public release commit, and preserve existing tags.

## Translation rules

- Keep execution location labels identical in every language: `[LOCAL]` (user's local computer/Mac), `[BOT-CLOUD]` (Bot's cloud computer), and `[GROK-UI]` (Grok Bot interface operated by the user). Identify the responsible party and location for each operation; a sequence on one computer can share a scope label. Standalone copyable prompts must define these labels inside the code block. Cross-computer copies must name the source and destination. Labels are explanatory, not shell commands or protocol fields.
- Terms follow the [Terms](README.md#terms) section of the README: each term has one counterpart in the other language, and the text uses no other names for it. Add a new concept to that table first, in both languages.
- Put a language switch at the top of each document. Keep links in the reader's language where a translation exists. Copyable AGENTS templates put navigation in an HTML comment so it does not become an instruction. Each has one generated block between `<!-- agent-mesh:bot-address lang=<language> -->` and `<!-- /agent-mesh:bot-address -->`; keep both markers and the single line between them, which the installer replaces with this team's Bot address.
- Translate explanations, prompts, and sample conversational text. Preserve command names, flags, paths, configuration keys, protocol markers, status codes, version numbers, and cryptographic test vectors.
- Team/person identifiers remain ASCII even when message bodies use other languages. `text_len` is Python's Unicode code-point count; hashes use UTF-8 bytes. Do not translate or normalize signed text.
- The prompt documents (`routine`, `routine-body`, `routine-full`, `cloud-setup`, `cloud-add-bot`, `cloud-uninstall`) each contain exactly one untyped or `text` fenced block. The installer and uninstaller render that block directly; avoid maintaining separate prompt copies in shell code. Keep `<repo-url>`, `<team-name>`, `<relay-name>`, `<mac-home>`, and `<invite-link>` identical across cloud templates; the routine stub (`routine`) is one fixed English block, the same in both languages, without placeholders; the rules templates (`routine-body`) use exactly `<team-name>`, `<relay-name>`, `<rn-cloud>` and `<threads-dir>` and are rendered by `box/rn-routine.py`, on the cloud and for `--full-prompt` alike; `routine-full` holds the steps that `--full-prompt` appends to the rules and uses only `<rn-cloud>`. The invite is printed separately and filled by the receiving agent. The cloud-uninstall templates use `<level>`, `<mode>`, `<team-name>`, `<mac-machine>`, `<relay-name>`, `<script-sha256>` and `<cloud-script>`; `<cloud-script>` becomes the filled `box/rn-cloud-uninstall.sh`, which is the only copy of the cloud script. The cloud-add-bot templates use `<relay-name>`, `<team-name>` and `<team-hash>`; an added Bot's stub prefixes every `rn-cloud.sh` command with `RN_BOT=<name>` and passes the team name and team hash to `instructions`, its complete prompt from `--full-prompt` has that block as step 0 and the same prefix, and the cloud-uninstall prompt gets the same prefix when an added Bot runs it. `scripts/render_prompt.py --bot-selector` renders both, and `scripts/check_docs.py` checks that the prefix reaches every `rn-cloud.sh` command.
- New languages need paired guides, navigation links, renderer support, installer validation, and tests before being advertised as supported. Unsupported language codes fail explicitly.
- `--lang` (or the recorded language, then `AGENT_MESH_LANG`) selects the prompts and the terminal text of `install.sh`, `update.sh`, `check.sh`, `uninstall.sh` and their helpers. That text lives in `scripts/messages/<lang>/*.tsv`, one `key<TAB>text` per line, read by `scripts/msg.sh` (`t`/`tq`) and `scripts/messages.py` (`t`). The text is a trusted format string: only `%s` and `%%` conversions and the `\n`, `\t` and `\\` escapes; values go in as `%s` arguments, never into the format. Every language has the same keys and the same number of `%s` per key; English output stays as it was. Machine-read output stays English and untranslated: status words read by the scripts, hashes, `agent-mesh-result`, `engine:`, `STOP:`, `cloud-setup:`, the `PASS`/`FAIL`/`WARN`/`RESULT` prefixes of `check.sh`, confirmation tokens, command blocks and messages for Grok Bot, logs, the `[LOCAL]`/`[GROK-UI]`/`[BOT-CLOUD]` labels and all cloud script output. The Reason Network engine and CLI diagnostics are not translated.
- A release that needs a migration on `[BOT-CLOUD]` puts it in `box/rn-cloud-setup.sh --upgrade-engine` or in the `--after-checkout` part of `box/rn-cloud-update.sh`: every cloud update runs both, from the new checkout.

## Check a change offline

These offline checks can run on a development computer or CI, without connecting to `[BOT-CLOUD]` or using `[GROK-UI]`. This repository's CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs the same checks on pushes to `main` and on pull requests, on Linux and macOS; a change that touches only the user documentation (the Markdown files in the root and `dsh/README*.md`, `llms.txt`, `LICENSE`) skips it, and [`.github/workflows/docs.yml`](.github/workflows/docs.yml) runs the documentation check on every push and pull request. Run from the distribution root:

```bash
python3 scripts/check_docs.py
python3 -m unittest discover -s tests/cli -t . -p 'test_*.py'
python3 -m unittest discover -s tests/rendering -t . -p 'test_*.py'
python3 -m unittest discover -s tests/relay -t . -p 'test_*.py'
python3 -m unittest discover -s tests/cloud -t . -p 'test_*.py'
node dsh/reason-network-bridge.test.mjs
bash scripts/check_shell.sh
```

`scripts/check_shell.sh` finds every `*.sh` file, runs `bash -n` on each, then runs ShellCheck on them, following the files they source; `scripts/msg.sh` is checked on its own as bash. It needs `shellcheck`; without it, `bash scripts/check_shell.sh --syntax-only` runs only `bash -n`, and the CI shellcheck job still runs the full check. The macOS CI job runs the syntax check only.

Each package under `tests/` runs in its own process; discovering the whole `tests/` tree at once is refused. To run one module, use `python3 -m unittest tests.<package>.<module>`, for example `python3 -m unittest tests.cli.test_engine`.

CI splits the CLI tests into four parts that run in parallel on each runner: `python3 -m tests.parts cli <part>`, where `<part>` is `update`, `engine`, `install` or `other`. `tests/parts.py` lists the modules of the first three; `other` runs every remaining CLI module, so a new module needs no change there. Together the four parts run the same tests as the `tests/cli` command above; they can also run side by side on a development computer.

Earlier versions of the CLI tests could leave fake engine daemons running after a run. Each one is a `python3` process whose command is `<temporary folder>/workspace/box/bin/engine/reason-network daemon --service`. List them with `ps -axo pid,command | grep '[w]orkspace/box/bin/engine/reason-network daemon'` and stop them with `kill <pid>`. A real engine is a binary, not run by `python3`, so it never matches this pattern. The CLI tests now fail if a fake daemon outlives its test.

The dsh test can also use the app runtime documented in its [guide](dsh/README.md). The installer and uninstaller tests use disposable homes and fake system/network/service/engine commands; they do not install software, remove anything outside their temporary folders, or contact real services. The documentation checker validates language pairs, relative links and anchors, prompt placeholders, the generated block in each AGENTS template, protocol vectors, and the message catalogs (same keys and `%s` counts in every language, allowed conversions and escapes, every key used and every used key present). It cannot judge translation quality; review both languages for meaning.

Preview prompts without installing, reading credentials, creating an invite, or contacting the network:

```bash
python3 scripts/render_prompt.py routine --lang en
python3 scripts/render_prompt.py routine --lang zh-CN
python3 scripts/render_prompt.py cloud-setup --lang zh-CN --team-name demo --mac-home /Users/example --repo-url https://example.invalid/agent-mesh.git
python3 scripts/render_prompt.py cloud-uninstall --lang en --team-name demo --mac-machine mac --relay-name bot --level identity --mode normal --challenge 0123456789abcdef
```
