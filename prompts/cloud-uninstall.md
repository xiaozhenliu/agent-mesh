# Cloud uninstall prompt for Grok Bot

**English** | [简体中文](cloud-uninstall.zh-CN.md)

`[LOCAL]`, the user's local computer (Mac): `./uninstall.sh --level identity` and `--level all` print this prompt with the team name, the Mac's machine name, the Bot address name, the level, the mode and a one-time challenge filled in, together with the cloud script from [`box/rn-cloud-uninstall.sh`](../box/rn-cloud-uninstall.sh). The user sends it to the Bot in `[GROK-UI]`, the Grok Bot interface. The Bot shows the check result and a `PROOF` value; the user types the value into the uninstaller on `[LOCAL]`, which prints a confirmation code only if the value was made with this deployment's signing key. Reprint the prompt by typing `prompt` while the uninstaller waits. The template below has unfilled placeholders; keep their names unchanged in translations.

```
You are Grok Bot. Execution location labels: [LOCAL] = the user's local computer (Mac); [BOT-CLOUD] = your Bot cloud computer (machine name cloud); [GROK-UI] = the Grok Bot interface opened by the user. Labels are not part of commands.
I am removing agent-mesh from [BOT-CLOUD]: level <level>, mode <mode>, team <team-name>, Mac machine <mac-machine>, Bot address name <relay-name>. Run every command below on [BOT-CLOUD], never on [LOCAL]. Do not delete anything by hand and do not change the script. Reply to me in my preferred language.
1. [BOT-CLOUD] Messages that were not fetched yet are lost with the removal. Ask me first whether to run /workspace/agent-mesh/box/rn-cloud.sh fetch once and handle the results.
2. [BOT-CLOUD] Write the script with exactly the command below, unchanged. Then run sha256sum /tmp/agent-mesh-uninstall.sh: it must print <script-sha256>. If it differs, write the file again; if it still differs, stop and tell me. Run the script from this file every time and do not rely on shell state from an earlier tool call; if /tmp was cleared, write the file again the same way and check it again.
cat > /tmp/agent-mesh-uninstall.sh <<'AGENT_MESH_UNINSTALL_EOF'
<cloud-script>
AGENT_MESH_UNINSTALL_EOF
3. [BOT-CLOUD] Run bash /tmp/agent-mesh-uninstall.sh check and show me its whole output, including the STATE, SHARED, FILE, KEEP, WILL REMOVE and PROOF lines. It changes nothing.
4. [LOCAL] I type the PROOF value into the uninstaller on my Mac. Only if it matches does my Mac print a confirmation code, which I send you. Run bash /tmp/agent-mesh-uninstall.sh apply <code> only after I send you that code and clearly agree to the removal.
5. If the PROOF line says none, or the mode is residual, there is no proof: run bash /tmp/agent-mesh-uninstall.sh apply-no-proof only after I confirm in chat that this is my own deployment and that it may be removed.
6. SHARED lines mean something else on this computer may still use /workspace/agent-mesh/box (its engine state or binary). Add the word accept-shared to the apply command only after I confirm that nothing listed there still uses it. If leave fails, tell me what it said; add the word force only with my consent.
7. Report every STOP: line exactly as printed and stop there; do not work around it. When the script prints DONE, KEPT or INCOMPLETE, report that line.
8. You cannot delete the routine for me: when you are done, remind me to delete it in [GROK-UI]. A paused routine keeps its webhook URL and key valid.
```
