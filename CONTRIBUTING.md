# Maintaining documentation

**English** | [简体中文](CONTRIBUTING.zh-CN.md)

## One set of user guides

This distribution is self-contained. Keep links relative and inside this distribution so a standalone checkout works.

English uses the original filenames (`README.md`, `AGENT-INSTALL.md`, and so on). Simplified Chinese uses `.zh-CN.md` beside each English file. Both languages cover the same installation steps, restrictions, recovery behavior, and protocol. When changing behavior, update both versions in the same change. English is the default entry point; the implementation determines technical behavior.

## Translation rules

- Put a language switch at the top of each document. Keep links in the reader's language where a translation exists. Copyable AGENTS templates put navigation in an HTML comment so it does not become an instruction.
- Translate explanations, prompts, and sample conversational text. Preserve command names, flags, paths, configuration keys, protocol markers, status codes, version numbers, and cryptographic test vectors.
- Team/person identifiers remain ASCII even when message bodies use other languages. `text_len` is Python's Unicode code-point count; hashes use UTF-8 bytes. Do not translate or normalize signed text.
- The two prompt documents each contain exactly one untyped or `text` fenced block. The installer renders that block directly; avoid maintaining separate prompt copies in shell code. Keep `<repo-url>`, `<team-name>`, `<mac-user>`, and `<invite-link>` identical across cloud templates. The invite is printed separately and filled by the receiving agent.
- New languages need paired guides, navigation links, renderer support, installer validation, and tests before being advertised as supported. Unsupported language codes fail explicitly.
- Diagnostic output and protocol fields currently stay English. `--lang` selects prompts, not a translation of the Reason Network engine or CLI diagnostics.

## Check a change offline

Run from the distribution root:

```bash
python3 scripts/check_docs.py
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 test_relay.py
python3 box/test_rn_fetch.py
node dsh/reason-network-bridge.test.mjs
for script in install.sh check.sh uninstall.sh; do bash -n "$script" || exit; done
```

The dsh test can also use the app runtime documented in its [guide](dsh/README.md). The documentation checker validates language pairs, relative links and anchors, prompt placeholders, and protocol vectors. It cannot judge translation quality; review both languages for meaning.

Preview prompts without installing, reading credentials, creating an invite, or contacting the network:

```bash
python3 scripts/render_prompt.py routine --lang en
python3 scripts/render_prompt.py routine --lang zh-CN
python3 scripts/render_prompt.py cloud-setup --lang zh-CN --team-name demo --mac-user example --repo-url https://example.invalid/agent-mesh.git
```
