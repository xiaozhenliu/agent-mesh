import os
import shlex
import subprocess
import sys
import unittest

from render_prompt import ROOT, render_prompt


class Prompts(unittest.TestCase):
    def test_both_languages_preserve_recovery_and_permission_rules(self):
        for lang in ("en", "zh-CN"):
            with self.subTest(lang=lang):
                text = render_prompt("routine", lang)
                for token in ("complete", "missing", "mismatch", "ack=resent", "no-copy", "limit",
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
