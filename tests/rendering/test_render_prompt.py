import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from render_prompt import CLOUD_FILES, ROOT, STUB_VERSION, render_prompt

from tests.helpers.stub_runner import full_command, stub_commands


class Prompts(unittest.TestCase):
    def test_project_version_is_available_without_installing(self):
        result = subprocess.run(["bash", str(ROOT / "install.sh"), "--version"],
                                env=dict(os.environ, AGENT_MESH_LANG="invalid"),
                                capture_output=True, text=True, check=True)
        version = (ROOT / "VERSION").read_text().strip()
        self.assertEqual(result.stdout, f"agent-mesh {version} (Reason Network 0.12.17)\n")
        self.assertNotIn("1/6", result.stdout)

    def test_both_languages_preserve_recovery_and_permission_rules(self):
        for lang in ("en", "zh-CN"):
            with self.subTest(lang=lang):
                text = render_prompt("routine", lang, team_name="demo", full=True)
                for token in ("complete", "handled", "claimed", "uncertain", "orphan: true",
                              "rn-cloud.sh working <id>", "rn-cloud.sh done <id>", "missing", "mismatch", "ack=resent", "no-copy", "limit",
                              "failed", "null", "skipped", "forward_failed", "RN-REFWD",
                              "handed to the Mac", "<reason-approval", "ask_user", "rn-cloud.sh request <id>",
                              "agent-mesh-request ORDINARY", "/workspace/agent-mesh/box/rn-cloud.sh",
                              "/workspace/agent-mesh/threads/"):
                    self.assertIn(token, text)
                self.assertNotIn("```", text)
                self.assertEqual([line[:4] for line in text.split("\n") if re.match(r"[RF][0-9]\. ", line)],
                                 ["R1. ", "R2. ", "R3. ", "R4. ", "R5. ", "R6. ", "R7. ", "F1. ", "F2. ", "F3. "])
        self.assertIn("preferred language", render_prompt("routine", team_name="demo", full=True))
        self.assertIn("偏好的语言", render_prompt("routine", "zh-CN", team_name="demo", full=True))

    def test_the_stub_is_one_fixed_text_for_every_language_and_bot_name(self):
        stub = render_prompt("routine")
        self.assertTrue(stub.startswith("agent-mesh routine stub v1. "))
        for lang in ("en", "zh-CN"):
            for name in (None, "bot", "alice"):
                self.assertEqual(render_prompt("routine", lang, relay_name=name), stub, (lang, name))
        step1, step3, version = stub_commands(stub)
        self.assertEqual((step1, version), ("bash /workspace/agent-mesh/box/rn-cloud.sh instructions 1", 1))
        self.assertIn("--stdin 1 \"$d\" \"$i\"", step3)
        for token in ("agent-mesh-instructions OK 1", "agent-mesh-bootstrap RELOAD", "agent-mesh-bootstrap SKIP"):
            self.assertIn(token, stub)
        self.assertNotIn("```", stub)
        self.assertIsNone(re.search(r"<[a-z]+(?:-[a-z]+)+>", stub))
        self.assertEqual(STUB_VERSION, version)

    def test_cloud_values_are_literal_and_repository_is_shell_quoted(self):
        repo = "https://example.invalid/team/a b.git?x=1&y=2|<team-name>"
        for lang in ("en", "zh-CN"):
            text = render_prompt("cloud-setup", lang, team_name="demo", mac_user="用户",
                                 repo_url=repo)
            self.assertIn(f"URL={shlex.quote(repo)}\n", text)
            self.assertIn("/Users/用户/.config/agent-relay/outbound.key", text)
            self.assertIn("rn-cloud-setup.sh '<invite-link>' demo", text)
            self.assertNotIn("<mac-user>", text)
            self.assertNotIn("<repo-url>", text)

    def test_release_values_fill_the_setup_block_and_bad_ones_are_refused(self):
        sums = "".join(f"{c * 64}  {n}\n" for c, n in zip("abcdef01", CLOUD_FILES))
        blocks = set()
        for lang in ("en", "zh-CN"):
            text = render_prompt("cloud-setup", lang, team_name="demo", mac_home="/Users/demo",
                                 repo_url="https://example.invalid/repo.git", release_tag="v1.2.3",
                                 release_commit="f" * 40, cloud_checksums=sums)
            self.assertIn(f"TAG='v1.2.3'; COMMIT='{'f' * 40}'", text)
            self.assertEqual(text.count(sums), 2)
            for placeholder in ("<release-tag>", "<release-commit>", "<cloud-checksums>"):
                self.assertNotIn(placeholder, text)
            start = text.index("bash -s <<'AGENT_MESH_SETUP'")
            blocks.add(text[start:text.index("\nAGENT_MESH_SETUP\n", start)])
        self.assertEqual(len(blocks), 1)
        unfilled = render_prompt("cloud-setup", team_name="demo")
        self.assertIn("TAG='<release-tag>'; COMMIT='<release-commit>'", unfilled)
        bad = ({"release_tag": "v1.2"}, {"release_tag": "1.2.3"}, {"release_tag": "v1.2.3'; x"},
               {"release_commit": "F" * 40}, {"release_commit": "f" * 39},
               {"cloud_checksums": sums.rsplit("\n", 2)[0]}, {"cloud_checksums": sums + sums},
               {"cloud_checksums": sums.replace("box/rn-engine.py", "box/x y")},
               {"cloud_checksums": sums.replace("box/", "/tmp/", 1)})
        for kwargs in bad:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    render_prompt("cloud-setup", team_name="demo", **kwargs)
        result = subprocess.run([sys.executable, str(ROOT / "scripts/render_prompt.py"), "cloud-setup",
                                 "--release-tag", "v1.2.3", "--release-commit", "f" * 40, "--cloud-checksums", sums],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"TAG='v1.2.3'; COMMIT='{'f' * 40}'", result.stdout)

    def test_rule_two_passes_maintenance_requests_to_the_request_command(self):
        for lang in ("en", "zh-CN"):
            with self.subTest(lang=lang):
                text = render_prompt("routine", lang, team_name="demo", full=True)
                rule = next(l for l in text.split("\n") if l.startswith("R2. "))
                self.assertIn('"agent-mesh "', rule)
                self.assertIn("/workspace/agent-mesh/box/rn-cloud.sh request <id>", rule)
                self.assertIn("agent-mesh-request ORDINARY", rule)
                self.assertNotIn("rn-cloud-update.sh", rule)
                fixed = full_command(text)
                self.assertIn("--stdin 0 - \"$i\"", fixed)
                self.assertEqual(fixed, full_command(render_prompt("routine", "en", team_name="demo", full=True)))
                f1 = next(l for l in text.split("\n") if l.startswith("F1. "))
                self.assertIn("agent-mesh-request DONE <id>", f1)

    def test_custom_relay_name_replaces_every_bot_address(self):
        for lang in ("en", "zh-CN"):
            routine = render_prompt("routine", lang, team_name="demo", relay_name="alice", full=True)
            setup = render_prompt("cloud-setup", lang, team_name="demo", mac_home="/Users/demo",
                                  repo_url="https://example.invalid/repo.git", relay_name="alice")
            for text in (routine, setup):
                self.assertNotIn("<relay-name>", text)
                self.assertNotIn("/bot", text)
            self.assertIn("demo/alice-cloud", routine)
            self.assertIn("demo/alice-inbox", routine)
            self.assertIn("demo/alice ", routine)
            self.assertIn("'<invite-link>' demo alice", setup)
            self.assertIn("box/relay", setup)
        self.assertIn("地址含 alice 就把 relay", render_prompt("routine", "zh-CN", team_name="demo", relay_name="alice",
                                                          full=True))
        self.assertIn("A alice address", render_prompt("routine", team_name="demo", relay_name="alice", full=True))
        self.assertIn("demo/bot-cloud", render_prompt("routine", team_name="demo", full=True))

    def test_the_complete_prompt_needs_the_team_name(self):
        for kwargs in ({}, {"relay_name": "alice"}, {"team_name": "Demo"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    render_prompt("routine", full=True, **kwargs)
        for template in ("cloud-setup", "cloud-uninstall", "cloud-add-bot"):
            with self.assertRaises(ValueError):
                render_prompt(template, team_name="demo", full=True)
        script = str(ROOT / "scripts/render_prompt.py")
        result = subprocess.run([sys.executable, script, "routine", "--full", "--lang", "zh-CN", "--team-name", "demo"],
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, render_prompt("routine", "zh-CN", team_name="demo", full=True))
        result = subprocess.run([sys.executable, script, "routine", "--full"], capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (1, ""))

    def test_invalid_relay_names_are_refused(self):
        for bad in ("Bot", "a;rm", "a--b", "terminal", "", "x" * 35, "a b"):
            for template in ("routine", "cloud-setup"):
                with self.assertRaises(ValueError, msg=bad):
                    render_prompt(template, relay_name=bad)
            result = subprocess.run([sys.executable, str(ROOT / "scripts/render_prompt.py"), "cloud-setup",
                                     "--relay-name", bad], capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stdout), (1, ""), bad)
        result = subprocess.run([sys.executable, str(ROOT / "scripts/render_prompt.py"), "routine", "--lang", "zh-CN",
                                 "--relay-name", "alice"], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, render_prompt("routine", "zh-CN", relay_name="alice"))

    def test_actual_mac_home_does_not_assume_users_directory(self):
        home = "/Volumes/People/Alex & Team"
        for lang in ("en", "zh-CN"):
            text = render_prompt("cloud-setup", lang, team_name="demo", mac_home=home,
                                 repo_url="https://example.invalid/repo.git")
            self.assertIn(home + "/.config/agent-relay/outbound.key", text)
            self.assertNotIn("<mac-home>", text)
            self.assertNotIn("/Users/", text)

    def test_cli_from_another_directory(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts/render_prompt.py"),
                                 "routine", "--lang", "zh-CN"], cwd=ROOT.parent,
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, render_prompt("routine", "zh-CN"))

    def test_unknown_language_and_template_fail(self):
        for template, lang in (("routine", "fr"), ("../routine", "en")):
            with self.assertRaises(ValueError):
                render_prompt(template, lang)

    def empty_home(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        return Path(home.name)

    def test_installer_rejects_invalid_language_before_installing(self):
        home = self.empty_home()
        for args in (("--lang", "fr"), ("--lang",), ("--lang", "")):
            result = subprocess.run(["bash", str(ROOT / "install.sh"), *args],
                                    env=dict(os.environ, HOME=str(home)), capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("en or zh-CN", result.stderr)
            self.assertNotIn("1/6", result.stdout)

    def test_installer_language_precedence_without_side_effects(self):
        home = self.empty_home()
        for environment, args, expected in (("fr", (), "unsupported language"),
                                             ("en", ("--lang", "fr"), "不支持的语言"),
                                             ("fr", ("--lang", "zh-CN"), "需要 --name"),
                                             ("zh-CN", (), "需要 --name"),
                                             ("zh-CN", ("--lang", "en"), "need --name"),
                                             ("", (), "need --name")):
            result = subprocess.run(["bash", str(ROOT / "install.sh"), *args],
                                    env=dict(os.environ, HOME=str(home), AGENT_MESH_LANG=environment),
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(expected, result.stderr)
        record = home / ".local/lib/agent-mesh/deployed.json"
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({"schema": 1, "lang": "zh-CN"}))
        for environment, args, expected in (("en", (), "需要 --name"), ("fr", (), "需要 --name"),
                                             ("zh-CN", ("--lang", "en"), "need --name")):
            result = subprocess.run(["bash", str(ROOT / "install.sh"), *args],
                                    env=dict(os.environ, HOME=str(home), AGENT_MESH_LANG=environment),
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(expected, result.stderr)


SELECTOR = {"team_name": "demo", "bot_selector": "alice", "team_hash": "0123456789abcdef"}
ADDED_ENV = ("RN_BIN={home}/.local/bin/reason-network\nPERSON={person}\nOUTBOUND_KEY_FILE={home}/.config/agent-relay/"
             "outbound.key\nLISTEN_NAME={name}\nCLOUD_NAME={name}-cloud\nINBOX_NAME={name}-inbox\n")


class AddedBotPrompts(unittest.TestCase):
    def test_added_stub_selects_the_bot_in_both_commands(self):
        plain = render_prompt("routine")
        for lang in ("en", "zh-CN"):
            with self.subTest(lang=lang):
                text = render_prompt("routine", lang, **SELECTOR)
                step1, step3, version = stub_commands(text)
                self.assertEqual(step1, "RN_BOT=alice bash /workspace/agent-mesh/box/rn-cloud.sh instructions 1 "
                                        "'demo' '0123456789abcdef'")
                plain3 = stub_commands(plain)[1]
                self.assertEqual(step3, "RN_BOT=alice " + plain3.replace("inbox/", "bots/alice/inbox/"))
                self.assertEqual(step3.count("bots/alice/inbox/"), 3)
                self.assertIsNone(re.search(r"(?<!alice/)inbox/", step3))
                changed = [(x, y) for x, y in zip(plain.split("\n"), text.split("\n")) if x != y]
                self.assertEqual(len(changed), 2)
                self.assertEqual(len(plain.split("\n")), len(text.split("\n")))
                self.assertNotIn("rn-cloud-add-bot", text)

    def test_every_command_in_the_complete_prompt_selects_the_added_bot(self):
        for lang in ("en", "zh-CN"):
            with self.subTest(lang=lang):
                legacy = render_prompt("routine", lang, team_name="demo", relay_name="alice", full=True)
                text = render_prompt("routine", lang, full=True, **SELECTOR)
                command = "RN_BOT=alice /workspace/agent-mesh/box/rn-cloud.sh"
                for sub in ("fetch <id...>", "done <id>", "working <id>", "send <from> <id>", "who", "request <id>"):
                    self.assertEqual(text.count(f"{command} {sub}"), legacy.count(f"rn-cloud.sh {sub}"), sub)
                    self.assertGreater(text.count(f"{command} {sub}"), 0, sub)
                self.assertEqual(text.count("rn-cloud.sh"), legacy.count("rn-cloud.sh"))
                self.assertEqual(text.count("rn-cloud.sh"), text.count(command))
                self.assertNotIn("/workspace/agent-mesh/threads/", text)
                self.assertEqual(text.count("/workspace/agent-mesh/bots/alice/threads/"),
                                 legacy.count("/workspace/agent-mesh/threads/"))
                self.assertEqual(full_command(text),
                                 "RN_BOT=alice " + full_command(legacy).replace("inbox/", "bots/alice/inbox/"))

    def test_step_zero_is_inserted_once_before_rule_one_with_the_values_filled(self):
        for lang in ("en", "zh-CN"):
            with self.subTest(lang=lang):
                text = render_prompt("routine", lang, full=True, **SELECTOR)
                lines = text.split("\n")
                zero = [i for i, line in enumerate(lines) if line.startswith("0. ")]
                self.assertEqual(len(zero), 1)
                self.assertTrue(lines[zero[0] + 1].startswith("R1. "))
                self.assertEqual(text.count("bash /workspace/agent-mesh/box/rn-cloud-add-bot.sh 'alice' 'demo' "
                                            "'0123456789abcdef'"), 1)
                self.assertEqual(text.count("/workspace/agent-mesh/bots/alice/.registered"), 1)
                self.assertIn("demo/alice-cloud", text)
                self.assertIn("demo/alice-inbox", text)
                for placeholder in ("<team-name>", "<relay-name>", "<team-hash>", "<rn-cloud>", "<threads-dir>"):
                    self.assertNotIn(placeholder, text)
                block = render_prompt("cloud-add-bot", lang, team_name="demo", relay_name="alice",
                                      team_hash="0123456789abcdef")
                self.assertIn(block.rstrip("\n") + "\nR1. ", text)
                self.assertEqual(text.count(block.rstrip("\n")), 1)

    def test_first_bot_prompts_have_no_selector(self):
        for lang in ("en", "zh-CN"):
            for text in (render_prompt("routine", lang, relay_name="bot"),
                         render_prompt("routine", lang, team_name="demo", relay_name="bot", full=True)):
                self.assertNotIn("RN_BOT", text)
                self.assertFalse(any(line.startswith("0. ") for line in text.split("\n")))
                self.assertNotIn("rn-cloud-add-bot", text)
                self.assertNotIn("/bots/", text)

    def test_missing_or_invalid_values_are_refused(self):
        bad = ({"team_name": None}, {"team_hash": None}, {"team_name": "Demo"}, {"team_name": "a b"},
               {"team_hash": "0123456789ABCDEF"}, {"team_hash": "0123"}, {"team_hash": "0" * 17},
               {"bot_selector": "Alice"}, {"bot_selector": ""}, {"bot_selector": "terminal"},
               {"relay_name": "bob"})
        for full in (False, True):
            for change in bad:
                with self.subTest(change=change, full=full):
                    with self.assertRaises(ValueError):
                        render_prompt("routine", "en", full=full, **dict(SELECTOR, **change))
            self.assertEqual(render_prompt("routine", "en", relay_name="alice", full=full, **SELECTOR),
                             render_prompt("routine", "en", full=full, **SELECTOR))
        for template in ("cloud-setup", "cloud-uninstall", "cloud-add-bot"):
            with self.assertRaises(ValueError):
                render_prompt(template, bot_selector="alice", team_name="demo", team_hash="0" * 16)
        with self.assertRaises(ValueError):
            render_prompt("routine", team_hash="0" * 16)

    def escape(self, which, old, new, **kwargs):
        import render_prompt as module
        routine = module.routine_module()
        originals = (module.prompt_block, routine.template)
        source = originals[1](ROOT, "en") if which == "routine-body" else originals[0](which, "en")
        self.assertIn(old, source)

        def block(template, language):
            text = originals[0](template, language)
            return text.replace(old, new, 1) if template == which else text

        def body(root, lang, name="routine-body"):
            text = originals[1](root, lang, name)
            return text.replace(old, new, 1) if name == which else text

        render_prompt("routine", "en", **kwargs)
        module.prompt_block, routine.template = block, body
        try:
            with self.assertRaises(ValueError):
                render_prompt("routine", "en", **kwargs)
        finally:
            module.prompt_block, routine.template = originals

    def test_rendering_stops_when_a_template_change_escapes_the_selector(self):
        step1 = "/workspace/agent-mesh/box/rn-cloud.sh instructions 1\n"
        for old, new in ((step1, step1.replace("1\n", "1 x\n")),
                         ("1. Load", "1. Load\n   bash " + step1.rstrip("\n")),
                         ("inbox/*/claimed", "*/claimed")):
            with self.subTest(which="routine", new=new[:40]):
                self.escape("routine", old, new, **SELECTOR)
        full = dict(SELECTOR, full=True)
        for which, old, new in (("routine-body", "<rn-cloud> who", "/workspace/agent-mesh/box/rn-cloud.sh who"),
                                ("routine-body", "<threads-dir>/", "/workspace/agent-mesh/threads/"),
                                ("routine-body", "R5. ", "R5. <team-hash> "),
                                ("routine-body", "R5. ", "R1. "),
                                ("routine-body", "R5. ", "0. "),
                                ("routine-full", '[ -f "inbox/$i/full.txt" ] && ', ""),
                                ("routine-full", "<rn-cloud> request", "/workspace/agent-mesh/box/rn-cloud.sh request"),
                                ("routine-full", "F1. ", "F1. <mac-home> "),
                                ("cloud-add-bot", "first run", "read /workspace/agent-mesh/threads/x and first run"),
                                ("cloud-add-bot", "first run", "run rn-cloud.sh who, then first run"),
                                ("cloud-add-bot", "'<team-hash>'", "'<team-name>'"),
                                ("cloud-add-bot", "0. ", "0. x\n0. ")):
            with self.subTest(which=which, new=new[:40]):
                self.escape(which, old, new, **full)
        self.escape("routine-body", "R5. ", "R5. <mac-home> ", team_name="demo", full=True)

    def test_cli_renders_the_selector_variant(self):
        script = str(ROOT / "scripts/render_prompt.py")
        args = ["--bot-selector", "alice", "--team-name", "demo", "--team-hash", "0123456789abcdef"]
        result = subprocess.run([sys.executable, script, "routine", "--lang", "zh-CN", *args],
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, render_prompt("routine", "zh-CN", **SELECTOR))
        result = subprocess.run([sys.executable, script, "routine", "--full", *args],
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, render_prompt("routine", "en", full=True, **SELECTOR))
        result = subprocess.run([sys.executable, script, "routine", "--bot-selector", "alice", "--team-name", "demo"],
                                capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertIn("team hash", result.stderr)

    def test_uninstall_prompt_accepts_level_bot_with_mode_normal(self):
        for lang in ("en", "zh-CN"):
            text = render_prompt("cloud-uninstall", lang, team_name="demo", mac_machine="mac", relay_name="alice",
                                 level="bot", mode="normal", challenge="0" * 16)
            self.assertIn('RELAY_NAME="alice"; LEVEL="bot"; MODE="normal"', text)
            self.assertNotIn("<level>", text)
        with self.assertRaises(ValueError):
            render_prompt("cloud-uninstall", team_name="demo", mac_machine="mac", relay_name="alice", level="bot",
                          mode="residual", challenge="0" * 16)
        with self.assertRaises(ValueError):
            render_prompt("cloud-uninstall", level="program")
        result = subprocess.run([sys.executable, str(ROOT / "scripts/render_prompt.py"), "cloud-uninstall",
                                 "--level", "bot", "--mode", "normal", "--relay-name", "alice"],
                                capture_output=True, text=True, check=True)
        self.assertIn('LEVEL="bot"', result.stdout)


    def test_uninstall_prompt_uses_the_selector_of_the_bot_that_runs_it(self):
        values = dict(team_name="demo", mac_machine="mac", relay_name="bot", level="bot", mode="normal",
                      challenge="0" * 16)
        for lang in ("en", "zh-CN"):
            with self.subTest(lang=lang):
                plain = render_prompt("cloud-uninstall", lang, **values)
                selected = render_prompt("cloud-uninstall", lang, bot_selector="alice", **values)
                self.assertNotIn("RN_BOT", plain)
                self.assertIn("RN_BOT=alice /workspace/agent-mesh/box/rn-cloud.sh fetch", selected)
                self.assertEqual(selected.replace("RN_BOT=alice ", ""), plain)
                self.assertNotIn("RN_BOT", selected.split("AGENT_MESH_UNINSTALL_EOF")[1])
                self.assertIn('RELAY_NAME="bot"; LEVEL="bot"', selected)
        for change in ({"bot_selector": "Alice"}, {"bot_selector": ""}, {"bot_selector": "terminal"},
                       {"bot_selector": "alice", "team_hash": "0" * 16}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    render_prompt("cloud-uninstall", "en", **dict(values, **change))
        result = subprocess.run([sys.executable, str(ROOT / "scripts/render_prompt.py"), "cloud-uninstall",
                                 "--lang", "zh-CN", "--team-name", "demo", "--mac-machine", "mac", "--relay-name", "bot",
                                 "--level", "bot", "--mode", "normal", "--challenge", "0" * 16,
                                 "--bot-selector", "alice"], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, render_prompt("cloud-uninstall", "zh-CN", bot_selector="alice", **values))

    def test_uninstall_selector_stops_when_a_template_change_escapes_it(self):
        import render_prompt as module
        original = module.prompt_block
        fetch = "/workspace/agent-mesh/box/rn-cloud.sh fetch"
        for new in ("~/agent-mesh/box/rn-cloud.sh fetch", "cd /workspace/agent-mesh/box && ./rn-cloud.sh fetch",
                    "fetch"):
            with self.subTest(new=new):
                module.prompt_block = lambda template, language: original(template, language).replace(fetch, new)
                try:
                    with self.assertRaises(ValueError):
                        render_prompt("cloud-uninstall", "en", bot_selector="alice")
                finally:
                    module.prompt_block = original


class DeployStatePrompt(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name) / "home"
        self.home.mkdir()

    def state(self, *args):
        env = dict(os.environ, HOME=str(self.home), AGENT_MESH_LANG="en")
        return subprocess.run([sys.executable, str(ROOT / "scripts/deploy_state.py"), *args], env=env,
                              capture_output=True, text=True)

    def env(self, conf, name, person="demo"):
        conf.mkdir(parents=True, exist_ok=True)
        (conf / "env").write_text(ADDED_ENV.format(home=self.home, person=person, name=name))

    def record(self, bots):
        path = self.home / ".local/lib/agent-mesh/deployed.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema": 1, "relay_name": "bot", "bots": bots}))

    def test_added_bot_gets_the_selector_prompt_and_the_first_bot_keeps_its_own(self):
        self.env(self.home / ".config/agent-relay", "bot")
        self.env(self.home / ".config/agent-relay-bots/alice", "alice")
        self.record({"alice": {"team_hash": "0123456789abcdef", "stage": "configured", "added_ms": 1}})
        digests = set()
        for lang in ("en", "zh-CN"):
            with self.subTest(lang=lang):
                added = self.state("show-prompt", "--lang", lang, "--relay-name", "alice")
                self.assertEqual(added.returncode, 0, added.stderr)
                self.assertEqual(added.stdout, render_prompt("routine", lang, **SELECTOR))
                digest = self.state("digest", "prompt", "--lang", lang, "--relay-name", "alice")
                self.assertEqual(digest.returncode, 0, digest.stderr)
                self.assertEqual(digest.stdout.strip(),
                                 hashlib.sha256(f"{added.stdout}\0lang={lang}".encode()).hexdigest())
                digests.add(digest.stdout)
                full = self.state("--prompt-mode", "full", "show-prompt", "--lang", lang, "--relay-name", "alice")
                self.assertEqual(full.returncode, 0, full.stderr)
                self.assertEqual(full.stdout, render_prompt("routine", lang, full=True, **SELECTOR))
                digest = self.state("--prompt-mode", "full", "digest", "prompt", "--lang", lang, "--relay-name", "alice")
                self.assertEqual(digest.stdout.strip(), hashlib.sha256(full.stdout.encode()).hexdigest())
                for args in ((), ("--relay-name", "bot")):
                    legacy = self.state("show-prompt", "--lang", lang, *args)
                    self.assertEqual(legacy.returncode, 0, legacy.stderr)
                    self.assertEqual(legacy.stdout, render_prompt("routine", lang))
                    legacy = self.state("--prompt-mode", "full", "show-prompt", "--lang", lang, *args)
                    self.assertEqual(legacy.returncode, 0, legacy.stderr)
                    self.assertEqual(legacy.stdout, render_prompt("routine", lang, team_name="demo", relay_name="bot",
                                                                  full=True))
        self.assertEqual(len(digests), 2)

    def test_added_bot_without_team_hash_or_team_name_is_an_error(self):
        self.env(self.home / ".config/agent-relay", "bot")
        self.env(self.home / ".config/agent-relay-bots/alice", "alice")
        for bots, expected in (({"alice": {"stage": "configured"}}, "team hash"),
                               ({"alice": {"team_hash": "XYZ"}}, "team hash"),
                               ({}, "team hash")):
            with self.subTest(bots=bots):
                self.record(bots)
                result = self.state("show-prompt", "--relay-name", "alice")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertIn(expected, result.stderr)
                self.assertIn("alice", result.stderr)
        self.record({"alice": {"team_hash": "0123456789abcdef"}})
        self.env(self.home / ".config/agent-relay-bots/alice", "alice", person="")
        result = self.state("show-prompt", "--relay-name", "alice")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PERSON", result.stderr)
        self.env(self.home / ".config/agent-relay", "bot", person="")
        self.assertEqual(self.state("show-prompt").stdout, render_prompt("routine"))
        result = self.state("--prompt-mode", "full", "show-prompt")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("PERSON", result.stderr)
