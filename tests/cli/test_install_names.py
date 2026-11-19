import json
import os
import shutil
import sqlite3
import subprocess
import sys
import unittest

from render_prompt import ROOT, render_prompt
from tests.helpers.prompts import ROUTINE_V083
from tests.cli.install_support import (
    BASE_PYTHON,
    InstallHarness,
    EMPTY_DIRECTORY,
    fake_python,
)


class InstallNames(InstallHarness, unittest.TestCase):
    def test_fresh_install_runs_the_relay_on_a_picked_python_not_on_the_callers(self):
        self.fresh()
        venv = self.base / "venv"
        fake_python(venv / "bin/python3")
        (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
        self.env["AGENT_MESH_PYTHON_CANDIDATES"] = f"{venv / 'bin/python3'}:{self.base / 'missing/python3'}"
        result = self.run_script("install.sh", "--name", "demo", "--relay-name", "alice", "--yes")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("no Python for the relay was found (tried /opt/homebrew/bin/python3, /usr/local/bin/python3 "
                      "and /usr/bin/python3; virtual environments and temporary directories are not used); "
                      "choose one with ./install.sh --python <path>", result.stderr)
        self.assertFalse(self.env_file().exists())
        self.assertIsNone(self.record())
        self.assertEqual([line for line in self.rn_calls() if line.split()[1] in ("init", "setup", "stop")], [])
        self.env["AGENT_MESH_PYTHON_CANDIDATES"] += f":{BASE_PYTHON}"
        result = self.run_script("install.sh", "--name", "demo", "--relay-name", "alice", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("relay_python", self.record())
        result = self.webhook()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.plist_python(), self.record()["relay_python"]), (BASE_PYTHON, BASE_PYTHON))
        self.assertNotEqual(self.plist_python(), str(self.bin / "python3"))
        self.assertEqual(self.relay_python(), {"python": BASE_PYTHON, "source": "recorded"})

    def test_fresh_install_keeps_the_python_chosen_for_the_relay(self):
        self.fresh()
        chosen = fake_python(self.base / "chosen tools/python3")
        result = self.run_script("install.sh", "--name", "demo", "--relay-name", "alice", "--yes", "--python",
                                 str(self.base / "missing/python3"))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("install: --python: ", result.stderr)
        self.assertFalse(self.env_file().exists())
        result = self.run_script("install.sh", "--name", "demo", "--relay-name", "alice", "--yes", "--python",
                                 str(chosen))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.record()["relay_python"], str(chosen))
        self.assertFalse(self.relay_files()[1].exists())
        result = self.webhook()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.plist_python(), self.record()["relay_python"]), (str(chosen), str(chosen)))

    def test_relay_name_fresh_install_seeds_the_env_and_records_the_name(self):
        self.fresh()
        result = self.run_script("install.sh", "--name", "demo", "--relay-name", "alice", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        env = self.env_file()
        lines = env.read_text().splitlines()
        for line in ("LISTEN_NAME=alice", "CLOUD_NAME=alice-cloud", "INBOX_NAME=alice-inbox"):
            self.assertEqual(lines.count(line), 1, line)
        self.assertFalse([line for line in lines if line.startswith(("WEBHOOK_URL=", "WEBHOOK_KEY="))])
        self.assertEqual(env.stat().st_mode & 0o777, 0o600)
        self.assertIn(render_prompt("routine", "en"), result.stdout)
        self.assertNotIn("<relay-name>", result.stdout)
        full = self.state_cmd("--prompt-mode", "full", "show-prompt")
        self.assertEqual(full.returncode, 0, full.stderr)
        self.assertIn("/alice-cloud", full.stdout)
        self.assertNotIn("<relay-name>", full.stdout)
        self.assertIn("rn who --all --json", self.rn_calls())
        data = self.record()
        self.assertEqual(data["relay_name"], "alice")
        self.assertEqual(data["prompt"]["hash"], self.state_cmd("digest", "prompt").stdout.strip())
        self.assertEqual(self.state_cmd("show-prompt").stdout, render_prompt("routine", "en", relay_name="alice"))

        result = self.webhook()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("relay registered as demo/alice", result.stdout)
        lines = env.read_text().splitlines()
        for line in ("LISTEN_NAME=alice", "CLOUD_NAME=alice-cloud", "INBOX_NAME=alice-inbox",
                     "WEBHOOK_URL=https://example.invalid/hook"):
            self.assertEqual(lines.count(line), 1, line)
        for register, said in (("alice-2", "registered as demo/alice-2 instead of demo/alice"),
                               ("-", "registered as nothing (the new relay reported no registration within 2 s) "
                                     "instead of demo/alice")):
            db = self.sessions([])
            con = sqlite3.connect(db)
            con.execute("DELETE FROM sessions")
            con.commit()
            con.close()
            self.env["MOCK_REGISTER"] = register
            result = self.webhook()
            self.assertNotEqual(result.returncode, 0, register)
            self.assertIn(said, result.stderr, register)
        self.env["MOCK_REGISTER"] = ""
        self.sessions([("alice", "cli:alice", 1)])
        result = self.webhook()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("relay registered as demo/alice", result.stdout)
        db.unlink()
        self.env["MOCK_NO_DB"] = "1"
        result = self.webhook()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("registered as nothing (no engine database", result.stderr)

    def test_relay_name_cannot_be_changed_even_before_the_install_finished(self):
        self.fresh()
        self.assertEqual(self.run_script("install.sh", "--name", "demo", "--relay-name", "alice", "--yes").returncode, 0)
        env, record = self.env_file(), self.home / ".local/lib/agent-mesh/deployed.json"
        before = (env.read_bytes(), record.read_bytes())
        (self.base / "calls").unlink()
        for args in (("--name", "demo", "--relay-name", "bob", "--yes"), ("--webhook", "--relay-name", "bob"),
                     ("--cloud-prompt", "--relay-name", "bob")):
            result = self.run_script("install.sh", *args)
            self.assertNotEqual(result.returncode, 0, args)
            self.assertIn("Renaming is not supported", result.stderr, args)
            self.assertEqual((env.read_bytes(), record.read_bytes()), before, args)
            self.assertEqual(self.rn_calls(), [], args)
        result = self.run_script("install.sh", "--cloud-prompt", "--relay-name", "alice")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Bot address: demo/alice (cloud sessions alice-cloud, alice-inbox)", result.stdout)
        self.set_names("bob")
        result = self.run_script("install.sh", "--webhook")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("installed with the Bot address name 'alice', but", result.stderr)
        self.assertIn("now gives 'bob'", result.stderr)
        env.unlink()
        result = self.run_script("install.sh", "--name", "demo", "--yes", "--relay-name", "bob")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("installed with the Bot address name 'alice'", result.stderr)
        self.assertIn("not 'bob'", result.stderr)
        self.assertIn("--relay-name alice or without --relay-name", result.stderr)
        self.assertFalse(env.exists())
        result = self.run_script("install.sh", "--name", "demo", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("LISTEN_NAME=alice", env.read_text())
        self.assertEqual(self.record()["relay_name"], "alice")

    def test_relay_name_on_an_install_without_name_keys_is_bot(self):
        self.configured()
        result = self.run_script("install.sh", "--cloud-prompt", "--relay-name", "bot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rn-cloud-setup.sh '<invite-link>' demo bot.", result.stdout)
        self.assertIn("Bot address: demo/bot (cloud sessions bot-cloud, bot-inbox)", result.stdout)
        self.assertNotIn("<relay-name>", result.stdout)
        result = self.run_script("install.sh", "--cloud-prompt", "--relay-name", "alice")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("uses the Bot address name 'bot'. Renaming is not supported", result.stderr)
        self.assertEqual(self.record()["relay_name"], "bot")

    def test_new_install_without_an_address_name_stops_with_a_suggestion(self):
        self.fresh()
        (self.home / ".reason/network/config.toml").unlink()
        for extra, said in (((), "What is your Grok Bot called?"),
                            (("--grok-name", "Zoë Bot"), "the suggested address is: --relay-name zoe-bot"),
                            (("--grok-name", "小助手"), "has no Latin letters or digits")):
            result = self.run_script("install.sh", "--name", "demo", "--yes", *extra)
            self.assertEqual(result.returncode, 1, extra)
            self.assertIn("a new installation needs the Bot's address name", result.stderr, extra)
            self.assertIn(said, result.stderr, extra)
            self.assertIn("--relay-name <address> --grok-name", result.stderr, extra)
        self.assertNotIn("curl", self.calls())
        self.assertEqual(self.rn_calls(), [])
        self.assertEqual([p.name for p in (self.home / ".local/lib").iterdir()], ["agent-mesh.lock"])
        self.assertFalse(self.env_file().exists())

    def test_existing_installations_keep_their_address_without_relay_name(self):
        self.fresh()
        result = self.run_script("install.sh", "--name", "demo", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("LISTEN_NAME=bot", self.env_file().read_text())
        self.assertEqual(self.record()["relay_name"], "bot")
        self.assertNotIn("grok_name", self.record())
        self.assertIn(render_prompt("routine", "en"), result.stdout)
        self.assertIn("/bot-cloud", self.state_cmd("--prompt-mode", "full", "show-prompt").stdout)
        for relay in (None, "alice"):
            shutil.rmtree(self.home / ".local/lib/agent-mesh")
            shutil.rmtree(self.home / ".config")
            shutil.rmtree(self.home / ".reason")
            self.configured(relay)
            want = relay or "bot"
            for args in (("--cloud-prompt",), ("--name", "demo", "--yes")):
                result = self.run_script("install.sh", *args)
                self.assertEqual(result.returncode, 0, (relay, args, result.stdout + result.stderr))
                self.assertIn(f"Bot address: demo/{want} (cloud sessions", result.stdout, (relay, args))
                self.assertEqual(self.record()["relay_name"], want, (relay, args))

    def test_grok_name_is_stored_as_given_and_shown_next_to_the_address(self):
        self.fresh()
        cfg = self.home / ".reason/network/config.toml"
        cfg.unlink()
        real = self.home / ".local/bin/reason-network"
        real.rename(self.base / "real-rn")
        self.tool("reason-network", f'case "$1" in team) [ -f "{cfg}" ] || exit 1 ;; '
                                    f'init) printf \'person = "demo"\\n\' >> "{cfg}" ;; esac\n'
                                    f'exec "{self.base / "real-rn"}" "$@"', real)
        grok = '小助手 "Bot" it\'s'
        result = self.run_script("install.sh", "--name", "demo", "--relay-name", "xiaozhushou", "--grok-name", grok,
                                 "--yes", "--lang", "zh-CN")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("1/6 网络", result.stdout)
        self.assertIn("3/6 团队", result.stdout)
        self.assertNotIn("1/6 network", result.stdout)
        data = self.record()
        self.assertEqual((data["relay_name"], data["grok_name"]), ("xiaozhushou", grok))
        self.assertNotIn(grok, self.env_file().read_text())
        self.assertEqual(self.state_cmd("show-prompt", "--lang", "zh-CN").stdout,
                         render_prompt("routine", "zh-CN", relay_name="xiaozhushou"))
        templates = self.home / ".local/lib/agent-mesh/templates"
        en = (templates / "AGENTS.reason-network.md").read_text()
        zh = (templates / "AGENTS.reason-network.zh-CN.md").read_text()
        self.assertIn(f"- This team's Grok Bot address: `demo/xiaozhushou` (called “{grok}” in Grok).\n", en)
        self.assertIn(f"- 本团队 Grok Bot 的地址：`demo/xiaozhushou`（在 Grok 里叫「{grok}」）。\n", zh)
        for text, name in ((en, "AGENTS.reason-network.md"), (zh, "AGENTS.reason-network.zh-CN.md")):
            source = (ROOT / "templates" / name).read_text().splitlines()
            lines = text.splitlines()
            self.assertEqual(len(lines), len(source), name)
            self.assertEqual([i for i, (a, b) in enumerate(zip(lines, source)) if a != b],
                             [i for i, line in enumerate(source) if "<relay-name>" in line], name)
        result = self.run_script("install.sh", "--cloud-prompt")
        self.assertIn(f"Bot 地址：demo/xiaozhushou → Grok: {grok}（云端会话 xiaozhushou-cloud", result.stdout)
        result = self.run_script("install.sh", "--cloud-prompt", "--lang", "en")
        self.assertIn(f"Bot address: demo/xiaozhushou → Grok: {grok} (cloud sessions", result.stdout)
        self.assertEqual(self.record()["lang"], "zh-CN")
        self.assertIn(f"PASS  Bot 地址：demo/xiaozhushou → Grok: {grok}\n", self.run_script("check.sh").stdout)
        self.assertIn(f"PASS  Bot address: demo/xiaozhushou → Grok: {grok}\n",
                      self.run_script("check.sh", "--lang", "en").stdout)

    def test_grok_name_on_its_own_only_labels_an_existing_installation(self):
        result = self.run_script("install.sh", "--grok-name", "Nova")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no installation to label yet", result.stderr)
        self.assertIsNone(self.record())
        key = self.configured()
        before = self.env_file().read_bytes()
        calls = self.base / "calls"
        if calls.exists():
            calls.unlink()
        for bad, said in (("", "is empty"), (" Nova", "starts or ends with a space"), ("a\nb", "contains a line break"),
                          ("x" * 65, "is longer than 64")):
            result = self.run_script("install.sh", "--grok-name", bad)
            self.assertNotEqual(result.returncode, 0, bad)
            self.assertIn(f"--grok-name: the Bot's name in Grok {said}", result.stderr, repr(bad))
            self.assertIsNone(self.record(), bad)
        result = self.run_script("install.sh", "--rotate-key", "--grok-name", "Nova")
        self.assertEqual(result.returncode, 2)
        self.assertIsNone(self.record())
        result = self.run_script("install.sh", "--grok-name", "Nova")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Bot address: demo/bot → Grok: Nova", result.stdout)
        self.assertIn("reply_convention.py refresh", result.stdout)
        self.assertEqual(self.record(), {"schema": 1, "relay_name": "bot", "grok_name": "Nova"})
        result = self.run_script("install.sh", "--grok-name", "Nova 2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.record()["grok_name"], "Nova 2")
        self.assertIn("`demo/bot` (called “Nova 2” in Grok)",
                      (self.home / ".local/lib/agent-mesh/templates/AGENTS.reason-network.md").read_text())
        self.assertEqual(self.env_file().read_bytes(), before)
        self.assertEqual(key.read_text(), "a" * 64 + "\n")
        self.assertEqual(self.service_changes(), [])
        self.assertEqual(self.rn_calls(), [])
        path = self.home / ".local/lib/agent-mesh/deployed.json"
        path.write_text("{")
        result = self.run_script("install.sh", "--grok-name", "Nova 3")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("is unreadable", result.stderr)
        self.assertEqual(path.read_text(), "{")

    def test_reply_convention_refresh_replaces_only_the_block(self):
        self.configured(relay="alice")
        self.write_record({"schema": 1, "relay_name": "alice", "grok_name": "Alice"})
        script = ROOT / "scripts/reply_convention.py"

        def refresh(path):
            return subprocess.run([sys.executable, str(script), "refresh", str(path)], env=self.env,
                                  capture_output=True, text=True)

        agents = self.base / "AGENTS.md"
        template = (ROOT / "templates/AGENTS.reason-network.zh-CN.md").read_text()
        before, after = "# My rules\r\n\r\nKeep it short.\r\n\r\n", "\r\n## Other\r\nUser notes stay.\r\n"
        original = before + template.replace("\n", "\r\n") + after
        agents.write_bytes(original.encode())
        agents.chmod(0o640)
        result = refresh(agents)
        self.assertEqual(result.returncode, 0, result.stderr)
        placeholder = [line for line in template.splitlines() if "<relay-name>" in line]
        self.assertEqual(len(placeholder), 1)
        want = original.replace(placeholder[0], "- 本团队 Grok Bot 的地址：`demo/alice`（在 Grok 里叫「Alice」）。")
        self.assertEqual(agents.read_bytes().decode(), want)
        self.assertEqual(agents.stat().st_mode & 0o777, 0o640)
        self.assertIn("already shows", refresh(agents).stdout)
        for content in ("# no block\n", template + template,
                        template.replace("<!-- /agent-mesh:bot-address -->\n", ""),
                        template.replace("lang=zh-CN", "lang=fr")):
            agents.write_text(content)
            result = refresh(agents)
            self.assertEqual(result.returncode, 1, content[:40])
            self.assertIn("nothing was changed", result.stderr)
            self.assertEqual(agents.read_text(), content)
        result = subprocess.run([sys.executable, str(script), "render", "--lang", "en"], env=self.env,
                                capture_output=True, text=True)
        self.assertIn("`demo/alice` (called “Alice” in Grok).", result.stdout)

    def test_update_refreshes_the_filled_in_templates_but_plan_does_not(self):
        repo = self.installed(relay="alice")
        templates = self.home / ".local/lib/agent-mesh/templates"
        shutil.rmtree(templates, ignore_errors=True)
        result = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(templates.exists())
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("reply convention with this Bot's address", result.stdout)
        self.assertIn("`demo/alice`.", (templates / "AGENTS.reason-network.md").read_text())
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertNotIn("reply convention with this Bot's address", result.stdout)
        self.assertIn("already up to date", result.stdout)
        self.assertEqual(self.service_changes(), [])

    def test_grok_name_with_webhook_or_cloud_prompt_needs_an_installation(self):
        for args in (("--webhook",), ("--cloud-prompt",)):
            result = self.run_script("install.sh", *args, "--grok-name", "Nova")
            self.assertNotEqual(result.returncode, 0, args)
            self.assertIsNone(self.record(), args)
        self.engine()
        for args, said in ((("--webhook",), "team or signing key missing"),
                           (("--cloud-prompt",), "no installation to label yet")):
            result = self.run_script("install.sh", *args, "--grok-name", "Nova")
            self.assertNotEqual(result.returncode, 0, args)
            self.assertIn(said, result.stderr, args)
            self.assertIsNone(self.record(), args)
        result = self.run_script("install.sh", "--name", "demo", "--relay-name", "nova", "--grok-name", "Nova",
                                 "--cloud-prompt")
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(self.record())

    def test_webhook_with_grok_name_checks_its_prerequisites_before_recording(self):
        key = self.configured(relay="alice")
        self.write_record({"schema": 1, "relay_name": "alice", "grok_name": "Old"})
        key.unlink()
        result = self.run_script("install.sh", "--webhook", "--grok-name", "New")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("team or signing key missing", result.stderr)
        self.assertEqual(self.record()["grok_name"], "Old")

    def test_full_install_with_grok_name_refuses_an_unreadable_record_before_any_change(self):
        self.fresh()
        path = self.home / ".local/lib/agent-mesh/deployed.json"
        for content in ("{", json.dumps({"schema": 99, "relay_name": "bot"})):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            result = self.run_script("install.sh", "--name", "demo", "--relay-name", "nova", "--grok-name", "Nova",
                                     "--yes")
            self.assertNotEqual(result.returncode, 0, content)
            self.assertIn("deployed.json", result.stderr, content)
            self.assertEqual(path.read_text(), content)
            self.assertFalse(self.env_file().exists(), content)
            self.assertEqual(self.rn_calls(), [], content)
            self.assertNotIn("curl", self.calls(), content)

    def test_grok_names_starting_with_a_hyphen_are_stored(self):
        self.configured()
        for name in ("-Nova", "--help", "-- x"):
            result = self.run_script("install.sh", "--grok-name", name)
            self.assertEqual(result.returncode, 0, (name, result.stdout + result.stderr))
            self.assertEqual(self.record()["grok_name"], name)
            self.assertIn(f"Bot address: demo/bot → Grok: {name}", result.stdout)

    def test_cloud_prompt_with_grok_name_refreshes_the_filled_in_templates(self):
        self.configured(relay="alice")
        self.write_record({"schema": 1, "relay_name": "alice", "grok_name": "Old"})
        result = self.run_script("install.sh", "--cloud-prompt", "--grok-name", "New")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.record()["grok_name"], "New")
        self.assertIn("Bot address: demo/alice → Grok: New (cloud sessions", result.stdout)
        text = (self.home / ".local/lib/agent-mesh/templates/AGENTS.reason-network.md").read_text()
        self.assertIn("`demo/alice` (called “New” in Grok).", text)

    def test_check_reports_an_unreadable_record_instead_of_no_address(self):
        self.engine()
        path = self.home / ".local/lib/agent-mesh/deployed.json"
        path.parent.mkdir(parents=True)
        path.write_text("{")
        out = self.run_script("check.sh").stdout
        self.assertIn("FAIL  Bot address", out)
        self.assertIn("is unreadable", out)
        self.assertNotIn("no address name chosen yet", out)
        self.assertIn("SKIP  relay registration (the Bot address name is not known)", out)

    def test_reply_convention_refresh_keeps_the_file_and_refuses_what_it_cannot_change(self):
        self.configured(relay="alice")
        script = ROOT / "scripts/reply_convention.py"

        def refresh(path):
            return subprocess.run([sys.executable, str(script), "refresh", str(path)], env=self.env,
                                  capture_output=True, text=True)

        template = (ROOT / "templates/AGENTS.reason-network.md").read_text()
        agents = self.base / "AGENTS.md"
        agents.write_text(template)
        link = self.base / "hardlink.md"
        os.link(agents, link)
        inode = agents.stat().st_ino
        result = refresh(agents)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(agents.stat().st_ino, inode)
        self.assertIn("`demo/alice`.", link.read_text())
        agents.write_text(template)
        agents.chmod(0o444)
        result = refresh(agents)
        if os.geteuid() != 0:
            self.assertEqual(result.returncode, 1)
            self.assertEqual(agents.read_text(), template)
        agents.chmod(0o644)
        bad = template.encode() + b"\xff\n"
        agents.write_bytes(bad)
        result = refresh(agents)
        self.assertEqual(result.returncode, 1)
        self.assertIn("is not UTF-8 text; nothing was changed", result.stderr)
        self.assertEqual(agents.read_bytes(), bad)
        agents.write_text(template)
        symlink = self.base / "symlink.md"
        symlink.symlink_to(agents)
        result = refresh(symlink)
        self.assertEqual(result.returncode, 1)
        self.assertIn("is not a regular file; nothing was changed", result.stderr)
        self.assertEqual(agents.read_text(), template)
        result = refresh(self.base)
        self.assertEqual(result.returncode, 1)

    def test_update_still_finishes_when_the_filled_in_templates_cannot_be_written(self):
        repo = self.installed(relay="alice")
        templates = self.home / ".local/lib/agent-mesh/templates"
        shutil.rmtree(templates, ignore_errors=True)
        templates.write_text("not a directory")
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("note: could not refresh the reply convention", result.stdout)
        self.assertIn("already up to date", result.stdout)
        self.assertEqual(templates.read_text(), "not a directory")

    def test_invalid_relay_names_stop_before_any_download(self):
        for name in ("Bot", "a--b", "terminal", "terminal-3", "x" * 35, "a_b"):
            result = self.run_script("install.sh", "--name", "demo", "--relay-name", name)
            self.assertNotEqual(result.returncode, 0, name)
            self.assertIn("--relay-name: the Bot address name", result.stderr, name)
        self.assertNotIn("curl", self.calls())

    def test_relay_name_taken_in_the_team_stops_before_setup(self):
        self.fresh()
        row = {"address": "demo/alice-inbox", "name": "alice-inbox", "person": "demo", "machine": "laptop",
               "agent": "cli", "cwd": "~", "repo": None, "branch": None, "status": "offline", "since": 1,
               "doing": None, "tier": "pull", "unread": 0, "is_me": False}
        listing = self.who / "all.json"
        args = ("install.sh", "--name", "demo", "--relay-name", "alice", "--yes")
        for content, said in ((json.dumps(dict(EMPTY_DIRECTORY, sessions=[row])),
                               "demo/alice-inbox (machine laptop, offline) already exists"),
                              ("not json", "could not check the session names"),
                              ('{"sessions": [{}]}', "could not check the session names")):
            listing.write_text(content)
            result = self.run_script(*args)
            self.assertNotEqual(result.returncode, 0, content)
            self.assertIn(said, result.stderr, content)
            self.assertIn("rn who --all --json", self.rn_calls(), content)
            self.assertFalse([line for line in self.rn_calls() if line.startswith("rn setup")], content)
            self.assertFalse(self.env_file().exists(), content)
        listing.write_text(json.dumps(EMPTY_DIRECTORY))
        (self.who / "fail").write_text("")
        result = self.run_script(*args)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("who --all --json failed", result.stderr)
        (self.who / "fail").unlink()
        self.sessions([("alice-2", "cli:alice", 1000)])
        result = self.run_script(*args)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already registered the relay key for alice as alice-2", result.stderr)
        self.assertFalse([line for line in self.rn_calls() if line.startswith("rn setup")])
        result = self.run_script("install.sh", "--name", "demo", "--relay-name", "bob", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_relay_names_not_derived_from_one_name_stop_without_showing_secrets(self):
        repo = self.installed()
        env = self.env_file()
        env.write_text(env.read_text() + "LISTEN_NAME=x\n")
        for script, args, root in (("install.sh", ("--webhook",), ROOT), ("update.sh", ("--no-pull", "--plan"), repo)):
            result = self.run_script(script, *args, root=root)
            out = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0, script)
            self.assertIn("not derived from one Bot address name: x, bot-cloud, bot-inbox", out, script)
            self.assertNotIn("0123456789", out, script)
            self.assertNotIn("example.invalid", out, script)
        self.assertEqual(self.service_changes(), [])

    def test_env_writes_replace_the_whole_file_or_nothing(self):
        self.fresh()
        real = (self.bin / "mv").resolve()
        self.tool("mv", f'case "$*" in */agent-relay/env) [ ! -f "$MOCK_LAUNCHD/fail-mv" ] || exit 1 ;; esac\n'
                        f'exec "{real}" "$@"')
        conf = self.env_file().parent
        args = ("install.sh", "--name", "demo", "--relay-name", "alice", "--yes")
        (self.state / "fail-mv").write_text("")
        result = self.run_script(*args)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not write", result.stderr)
        self.assertFalse(self.env_file().exists())
        self.assertEqual(list(conf.glob(".env.*")), [])
        (conf / ".env.Ab12Cd").write_text("leftover")
        (self.state / "fail-mv").unlink()
        result = self.run_script(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("LISTEN_NAME=alice", self.env_file().read_text())
        self.assertEqual(list(conf.glob(".env.*")), [])
        before = self.env_file().read_bytes()
        (self.state / "fail-mv").write_text("")
        result = self.webhook()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not write", result.stderr)
        self.assertEqual(self.env_file().read_bytes(), before)
        self.assertEqual(list(conf.glob(".env.*")), [])
        (self.state / "fail-mv").unlink()
        result = self.webhook()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("WEBHOOK_URL=https://example.invalid/hook", self.env_file().read_text())

    def test_update_prints_the_prompt_that_deploy_state_hashes(self):
        repo = self.installed(relay="alice")
        data = self.record()
        self.assertEqual(data["relay_name"], "alice")
        result = self.run_tty(repo, "--no-pull", "--full-prompt", answers=["y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        shown = self.state_cmd("--prompt-mode", "full", "show-prompt", root=repo).stdout
        self.assertIn("demo/alice-cloud", shown)
        self.assertIn("this one (en):\n" + "-" * 64 + "\n" + shown + "\n" + "-" * 64 + "\n", result.stdout)
        self.assertEqual(self.record()["prompt"]["hash"],
                         self.state_cmd("--prompt-mode", "full", "digest", "prompt", root=repo).stdout.strip())

    def test_update_accepts_records_from_before_the_name_was_recorded(self):
        repo = self.installed()
        old = self.record()
        old.pop("relay_name")
        old["prompt"] = {"hash": ROUTINE_V083["en"], "state": "confirmed"}
        self.write_record(old)
        result = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"routine prompt\s+changed")
        self.assertRegex(result.stdout, r"address name\s+unknown\s+bot")
        status = self.state_cmd("status", "--lang", "en", "--python", sys.executable, root=repo).stdout.splitlines()
        self.assertIn("name unknown bot", status)
        self.set_names("alice")
        result = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"address name\s+unknown\s+alice")
        self.mark(repo, "cloud", "confirmed")
        self.assertEqual(self.record()["relay_name"], "alice")
        self.assertRegex(self.run_script("update.sh", "--no-pull", "--plan", root=repo).stdout,
                         r"address name\s+ok\s+alice")
        self.set_names("bob")
        before = self.record()
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("now gives 'bob'. Renaming is not supported", result.stderr)
        self.assertIn("nothing was changed", result.stderr)
        result = self.run_script("install.sh", "--webhook", root=repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("now gives 'bob'", result.stderr)
        result = self.state_cmd("mark", "prompt", "pending", root=repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("changed from alice", result.stderr)
        self.assertEqual(self.record(), before)
        self.assertEqual(self.service_changes(), [])
