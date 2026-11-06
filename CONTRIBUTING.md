# Maintaining documentation

**English** | [简体中文](CONTRIBUTING.zh-CN.md)

## One set of user guides

This distribution is self-contained. Keep links relative and inside this distribution so a standalone checkout works.

English uses the original filenames (`README.md`, `AGENT-INSTALL.md`, and so on). Simplified Chinese uses `.zh-CN.md` beside each English file. Both languages cover the same installation steps, restrictions, recovery behavior, and protocol. When changing behavior, update both versions in the same change. English is the default entry point; the implementation determines technical behavior.

## Release versions

`VERSION` is the agent-mesh project version (`major.minor.patch`); the installer reports it with `./install.sh --version`. Reason Network v0.10.7 is a separate pinned dependency. For a release, update `VERSION`, both README version labels, `llms.txt`, and both changelogs together. Record compatibility changes and upgrade instructions. The documentation checker verifies the README and changelog versions. Use `v<VERSION>` for an annotated tag on the public release commit, and preserve existing tags.

## Translation rules

- Keep execution location labels identical in every language: `[LOCAL]` (user's local computer/Mac), `[BOT-CLOUD]` (Bot's cloud computer), and `[GROK-UI]` (Grok Bot interface operated by the user). Identify the responsible party and location for each operation; a sequence on one computer can share a scope label. Standalone copyable prompts must define these labels inside the code block. Cross-computer copies must name the source and destination. Labels are explanatory, not shell commands or protocol fields.
- Put a language switch at the top of each document. Keep links in the reader's language where a translation exists. Copyable AGENTS templates put navigation in an HTML comment so it does not become an instruction.
- Translate explanations, prompts, and sample conversational text. Preserve command names, flags, paths, configuration keys, protocol markers, status codes, version numbers, and cryptographic test vectors.
- Team/person identifiers remain ASCII even when message bodies use other languages. `text_len` is Python's Unicode code-point count; hashes use UTF-8 bytes. Do not translate or normalize signed text.
- The two prompt documents each contain exactly one untyped or `text` fenced block. The installer renders that block directly; avoid maintaining separate prompt copies in shell code. Keep `<repo-url>`, `<team-name>`, `<mac-home>`, and `<invite-link>` identical across cloud templates. The invite is printed separately and filled by the receiving agent.
- New languages need paired guides, navigation links, renderer support, installer validation, and tests before being advertised as supported. Unsupported language codes fail explicitly.
- Diagnostic output and protocol fields currently stay English. `--lang` selects prompts, not a translation of the Reason Network engine or CLI diagnostics.

## Check a change offline

These offline checks can run on a development computer or CI, without connecting to `[BOT-CLOUD]` or using `[GROK-UI]`. This repository's CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs the same checks on pushes to `main` and on pull requests, on Linux and macOS. Run from the distribution root:

```bash
python3 scripts/check_docs.py
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 test_relay.py
python3 box/test_rn_fetch.py
node dsh/reason-network-bridge.test.mjs
for script in install.sh check.sh uninstall.sh update.sh dsh/mac-install.sh dsh/mac-uninstall.sh box/rn-cloud-setup.sh box/rn-cloud.sh; do bash -n "$script" || exit; done
```

The dsh test can also use the app runtime documented in its [guide](dsh/README.md). The installer tests use disposable homes and fake system/network/service commands; they do not install software or contact real services. The documentation checker validates language pairs, relative links and anchors, prompt placeholders, and protocol vectors. It cannot judge translation quality; review both languages for meaning.

Preview prompts without installing, reading credentials, creating an invite, or contacting the network:

```bash
python3 scripts/render_prompt.py routine --lang en
python3 scripts/render_prompt.py routine --lang zh-CN
python3 scripts/render_prompt.py cloud-setup --lang zh-CN --team-name demo --mac-home /Users/example --repo-url https://example.invalid/agent-mesh.git
```
