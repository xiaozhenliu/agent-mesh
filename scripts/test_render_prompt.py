import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from render_prompt import ROOT, render_prompt

ROUTINE_V082 = {"en": "3f1e26b18b221e700aed15a18ec33be184c96b7c10d9251a7538ab36b297b6b0",
                "zh-CN": "3556ac4a966e2e911da0bae194625b38692d30a9697a5a64065da28866d47d06"}
ROUTINE_V083 = {"en": "0dc906079839d237305b20abe2c3269d234b1aa9947d2ea307ec3fcf2289a4ff",
                "zh-CN": "a8e571baff8c934d3db8caa9eb235b7e8efbac062423b4450428900cdd3310a2"}
FIRST_UPDATE_PREFIX = "This is my update request from my Mac (sent by ./update.sh)."


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
        sums = "".join(f"{c * 64}  box/{n}\n" for c, n in zip("abcd", ("rn-cloud.sh", "rn-fetch.py",
                                                                     "rn-cloud-setup.sh", "rn-engine.py")))
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


if __name__ == "__main__":
    unittest.main()
