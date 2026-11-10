import hashlib
import os
import shlex
import subprocess
import sys
import unittest

from render_prompt import ROOT, render_prompt

ROUTINE_V041 = {"en": "4f8c6ff1996cc1432a8079302597f67d5a8fef0d5579d10807e31e8ce4fdad96",
                "zh-CN": "65c9e2ac143736c720a937868bad2e9ca43c369e875ab5b4ffcb370bbfd8fb23"}


class Prompts(unittest.TestCase):
    def test_project_version_is_available_without_installing(self):
        result = subprocess.run(["bash", str(ROOT / "install.sh"), "--version"],
                                env=dict(os.environ, AGENT_MESH_LANG="invalid"),
                                capture_output=True, text=True, check=True)
        version = (ROOT / "VERSION").read_text().strip()
        self.assertEqual(result.stdout, f"agent-mesh {version} (Reason Network 0.10.7)\n")
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
            self.assertIn(f"git clone {shlex.quote(repo)} /workspace/agent-mesh", text)
            self.assertIn("/Users/用户/.config/agent-relay/outbound.key", text)
            self.assertIn("rn-cloud-setup.sh '<invite-link>' demo", text)
            self.assertNotIn("<mac-user>", text)
            self.assertNotIn("<repo-url>", text)

    def test_default_relay_name_renders_the_routine_users_already_saved(self):
        for lang, digest in ROUTINE_V041.items():
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

    def test_installer_rejects_invalid_language_before_installing(self):
        for args in (("--lang", "fr"), ("--lang",), ("--lang", "")):
            result = subprocess.run(["bash", str(ROOT / "install.sh"), *args],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("en or zh-CN", result.stderr)
            self.assertNotIn("1/6", result.stdout)

    def test_installer_language_precedence_without_side_effects(self):
        # With no name, a valid language reaches name validation and exits before network/config access.
        for environment, args, expected in (("fr", (), "unsupported language"),
                                             ("fr", ("--lang", "zh-CN"), "need --name"),
                                             ("zh-CN", (), "need --name"),
                                             ("", (), "need --name")):
            result = subprocess.run(["bash", str(ROOT / "install.sh"), *args],
                                    env=dict(os.environ, AGENT_MESH_LANG=environment),
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(expected, result.stderr)


if __name__ == "__main__":
    unittest.main()
