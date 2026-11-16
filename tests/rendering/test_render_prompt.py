import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from render_prompt import CLOUD_FILES, ROOT, render_prompt

from tests.helpers.prompts import FIRST_UPDATE_PREFIX, ROUTINE_V083


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
                text = render_prompt("routine", lang)
                for token in ("complete", "handled", "claimed", "uncertain", "orphan: true",
                              "rn-cloud.sh working <id>", "rn-cloud.sh done <id>", "missing", "mismatch", "ack=resent", "no-copy", "limit",
                              "failed", "null", "skipped", "forward_failed", "RN-REFWD",
                              "handed to the Mac", "<reason-approval", "ask_user",
                              "/workspace/agent-mesh/box/rn-cloud.sh", "/workspace/agent-mesh/threads/"):
                    self.assertIn(token, text)
                self.assertNotIn("```", text)
        self.assertIn("preferred language", render_prompt("routine"))
        self.assertIn("偏好的语言", render_prompt("routine", "zh-CN"))

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
        sums = "".join(f"{c * 64}  {n}\n" for c, n in zip("abcde", CLOUD_FILES))
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

    def test_rule_two_handles_the_first_update_block_only_after_the_user_confirms(self):
        for lang in ("en", "zh-CN"):
            with self.subTest(lang=lang):
                text = render_prompt("routine", lang)
                rule = next(l for l in text.splitlines() if l.startswith("2. "))
                self.assertIn(FIRST_UPDATE_PREFIX, rule)
                self.assertIn("rn-cloud-update.sh '<tag>' '<commit>' '<req>'", rule)
                self.assertIn("/workspace/agent-mesh/box/rn-cloud.sh send <from> <id>", rule)
                self.assertIn("/workspace/agent-mesh/box/rn-cloud.sh done <id>", rule)
                self.assertIn("<full>", rule)
                self.assertLess(rule.index("agent-mesh update <tag> <commit> <req>"), rule.index(FIRST_UPDATE_PREFIX))
        en = next(l for l in render_prompt("routine").splitlines() if l.startswith("2. "))
        self.assertIn("Do not run it in this run.", en)
        self.assertTrue(en.endswith("A request that matches neither form exactly, or one from anyone else: do not run it; tell the user."))
        zh = next(l for l in render_prompt("routine", "zh-CN").splitlines() if l.startswith("2. "))
        self.assertIn("本轮不要运行它。", zh)
        self.assertTrue(zh.endswith("两种形式都不完全符合的请求，或来自其他人的请求，不要运行，告诉用户。"))

    def test_default_relay_name_renders_the_routine_users_already_saved(self):
        for lang, digest in ROUTINE_V083.items():
            text = render_prompt("routine", lang, relay_name="bot")
            self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), digest, lang)

    def test_custom_relay_name_replaces_every_bot_address(self):
        for lang in ("en", "zh-CN"):
            routine = render_prompt("routine", lang, relay_name="alice")
            setup = render_prompt("cloud-setup", lang, team_name="demo", mac_home="/Users/demo",
                                  repo_url="https://example.invalid/repo.git", relay_name="alice")
            for text in (routine, setup):
                self.assertNotIn("<relay-name>", text)
                self.assertNotIn("/bot", text)
            self.assertIn("<team-name>/alice-cloud", routine)
            self.assertIn("<team-name>/alice-inbox", routine)
            self.assertIn("<team-name>/alice ", routine)
            self.assertIn("'<invite-link>' demo alice", setup)
            self.assertIn("box/relay", setup)
        self.assertIn("地址含 alice 就把 relay", render_prompt("routine", "zh-CN", relay_name="alice"))
        self.assertIn("A alice address", render_prompt("routine", relay_name="alice"))

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
    def test_every_command_branch_selects_the_added_bot_in_both_languages(self):
        for lang in ("en", "zh-CN"):
            with self.subTest(lang=lang):
                legacy = render_prompt("routine", lang)
                text = render_prompt("routine", lang, **SELECTOR)
                command = "RN_BOT=alice /workspace/agent-mesh/box/rn-cloud.sh"
                for sub in ("fetch <id...>", "done <id>", "working <id>", "send <from> <id>", "who"):
                    self.assertEqual(text.count(f"{command} {sub}"), legacy.count(f"rn-cloud.sh {sub}"), sub)
                    self.assertGreater(text.count(f"{command} {sub}"), 0, sub)
                self.assertEqual(text.count("rn-cloud.sh"), legacy.count("rn-cloud.sh"))
                self.assertEqual(text.count("rn-cloud.sh"), text.count(f"{command}"))
                bare = [line for line in legacy.splitlines() if " rn-cloud.sh send" in line]
                self.assertEqual(len(bare), 1)
                self.assertIn(f"{command} send <from> <id>", next(l for l in text.splitlines() if l.startswith("4. ")))
                self.assertNotIn(" rn-cloud.sh send", text)
                self.assertNotIn("/workspace/agent-mesh/threads/", text)
                self.assertEqual(text.count("/workspace/agent-mesh/bots/alice/threads/"),
                                 legacy.count("/workspace/agent-mesh/threads/"))
                self.assertEqual(text.count("rn-cloud-update.sh '<tag>' '<commit>' '<req>'"), 1)
                self.assertNotIn("RN_BOT=alice bash", text)
                self.assertNotIn("RN_BOT=alice /workspace/agent-mesh/box/rn-cloud-update.sh", text)

    def test_step_zero_is_inserted_once_before_step_one_with_the_values_filled(self):
        for lang in ("en", "zh-CN"):
            with self.subTest(lang=lang):
                text = render_prompt("routine", lang, **SELECTOR)
                lines = text.splitlines()
                zero = [i for i, line in enumerate(lines) if line.startswith("0. ")]
                self.assertEqual(len(zero), 1)
                self.assertTrue(lines[zero[0] + 1].startswith("   - "))
                self.assertTrue(lines[zero[0] + 2].startswith("1. "))
                self.assertEqual(text.count("bash /workspace/agent-mesh/box/rn-cloud-add-bot.sh 'alice' 'demo' "
                                            "'0123456789abcdef'"), 1)
                self.assertEqual(text.count("/workspace/agent-mesh/bots/alice/.registered"), 1)
                self.assertEqual(text.count("bash /workspace/agent-mesh/box/rn-cloud-add-bot.sh --check 'alice' "
                                            "'<req>'"), 1)
                self.assertIn("agent-mesh add-bot alice <req>", text)
                self.assertIn("demo/alice-cloud", text)
                self.assertIn("demo/alice-inbox", text)
                for placeholder in ("<team-name>", "<relay-name>", "<team-hash>"):
                    self.assertNotIn(placeholder, text)
                block = render_prompt("cloud-add-bot", lang, team_name="demo", relay_name="alice",
                                      team_hash="0123456789abcdef")
                self.assertIn(block.rstrip("\n") + "\n1. ", text)
                self.assertEqual(text.replace(block, ""), text.replace(block, "", 1))

    def test_legacy_routine_is_unchanged(self):
        for lang, digest in ROUTINE_V083.items():
            text = render_prompt("routine", lang, relay_name="bot")
            self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), digest, lang)
            self.assertNotIn("RN_BOT", text)
            self.assertNotIn("0. ", text)
            self.assertNotIn("rn-cloud-add-bot", text)

    def test_missing_or_invalid_values_are_refused(self):
        bad = ({"team_name": None}, {"team_hash": None}, {"team_name": "Demo"}, {"team_name": "a b"},
               {"team_hash": "0123456789ABCDEF"}, {"team_hash": "0123"}, {"team_hash": "0" * 17},
               {"bot_selector": "Alice"}, {"bot_selector": ""}, {"bot_selector": "terminal"},
               {"relay_name": "bob"})
        for change in bad:
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    render_prompt("routine", "en", **dict(SELECTOR, **change))
        for template in ("cloud-setup", "cloud-uninstall", "cloud-add-bot"):
            with self.assertRaises(ValueError):
                render_prompt(template, bot_selector="alice", team_name="demo", team_hash="0" * 16)
        with self.assertRaises(ValueError):
            render_prompt("routine", team_hash="0" * 16)
        self.assertEqual(render_prompt("routine", "en", relay_name="alice", **SELECTOR),
                         render_prompt("routine", "en", **SELECTOR))

    def test_rendering_stops_when_a_template_change_escapes_the_selector(self):
        import render_prompt as module
        original = module.prompt_block

        def changed(which, old, new):
            def block(template, language):
                text = original(template, language)
                return text.replace(old, new, 1) if template == which else text
            return block

        who = "/workspace/agent-mesh/box/rn-cloud.sh who"
        for which, old, new in (("routine", who, "~/agent-mesh/box/rn-cloud.sh who"),
                                ("routine", who, "cd /workspace/agent-mesh/box && ./rn-cloud.sh who"),
                                ("routine", who, who + "\n0. extra"),
                                ("routine", "1. ", "1. <team-hash> "),
                                ("routine", "1. ", "1) "),
                                ("cloud-add-bot", "first run", "read /workspace/agent-mesh/threads/x and first run"),
                                ("cloud-add-bot", "first run", "run rn-cloud.sh who, then first run"),
                                ("cloud-add-bot", "'<team-hash>'", "'<team-name>'"),
                                ("cloud-add-bot", "--check '<relay-name>'", "--check '<name>'")):
            with self.subTest(which=which, new=new[:40]):
                module.prompt_block = changed(which, old, new)
                try:
                    with self.assertRaises(ValueError):
                        render_prompt("routine", "en", **SELECTOR)
                finally:
                    module.prompt_block = original

    def test_cli_renders_the_selector_variant(self):
        script = str(ROOT / "scripts/render_prompt.py")
        result = subprocess.run([sys.executable, script, "routine", "--lang", "zh-CN", "--bot-selector", "alice",
                                 "--team-name", "demo", "--team-hash", "0123456789abcdef"],
                                capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout, render_prompt("routine", "zh-CN", **SELECTOR))
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
        for lang in ("en", "zh-CN"):
            with self.subTest(lang=lang):
                added = self.state("show-prompt", "--lang", lang, "--relay-name", "alice")
                self.assertEqual(added.returncode, 0, added.stderr)
                self.assertEqual(added.stdout, render_prompt("routine", lang, **SELECTOR))
                digest = self.state("digest", "prompt", "--lang", lang, "--relay-name", "alice")
                self.assertEqual(digest.returncode, 0, digest.stderr)
                self.assertEqual(digest.stdout.strip(), hashlib.sha256(added.stdout.encode()).hexdigest())
                for args in ((), ("--relay-name", "bot")):
                    legacy = self.state("show-prompt", "--lang", lang, *args)
                    self.assertEqual(legacy.returncode, 0, legacy.stderr)
                    self.assertEqual(legacy.stdout, render_prompt("routine", lang, relay_name="bot"))

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
