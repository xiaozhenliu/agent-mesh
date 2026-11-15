import shutil
import unittest

from tests.cli.install_support import (
    InstallHarness,
)


class Dsh(InstallHarness, unittest.TestCase):
    def test_dsh_user_app_location_and_reinstall(self):
        self.engine()
        app = self.home / "Applications/DeepSeek Harness.app/Contents/MacOS/DeepSeek Harness"
        self.tool("app", "echo 'ℹ pass 7'", app)
        patch = self.home / ".dsh/cordis.patch.yml"
        patch.parent.mkdir(parents=True)
        patch.write_text("# === reason-network MCP BEGIN ===\nold\n# === reason-network MCP END ===\n")
        for attempt in range(3):
            if attempt == 2:
                custom = self.home / "My Apps/DeepSeek Harness.app"
                custom.parent.mkdir()
                shutil.move(str(app.parents[2]), custom)
                self.env["DSH_APP"] = str(custom)
            result = self.run_script("dsh/mac-install.sh", "demo")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(patch.read_text().count("id: reason-network-bridge"), 1)
            self.assertIn("Alex''s & Team", patch.read_text())

    def test_dsh_uninstall_puts_back_only_reason_networks_block(self):
        patch, own, plugin = self.dsh_installed()
        saved = self.home / ".dsh/backup-reason-network-bridge/reason-network-block.yml"
        self.assertEqual(saved.read_text(), own)
        result = self.run_script("dsh/mac-uninstall.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(patch.read_text(), "- before: 1\n" + own + "- after: 2\n- added later: 3\n")
        self.assertFalse(plugin.exists())
        self.assertIn("own MCP block is back", result.stdout)
        again = self.run_script("dsh/mac-uninstall.sh")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("nothing to change", again.stdout)

    def test_dsh_scripts_speak_chinese_when_asked(self):
        patch, own, plugin = self.dsh_installed()
        self.env["AGENT_MESH_LANG"] = "zh-CN"
        result = self.run_script("dsh/mac-install.sh", "--plugin-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("== 1. 用应用自带的运行时跑单元测试", result.stdout)
        self.assertIn("没有改动，bridge 设置保持不变。", result.stdout)
        self.assertEqual(self.run_script("dsh/mac-install.sh", "nobody").stdout, "团队名必须与这台 Mac 当前的部署一致：demo\n")
        result = self.run_script("dsh/mac-uninstall.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("== 已移除 bridge；备份：", result.stdout)
        self.assertIn("已放回 Reason Network 自己的 MCP 区块", result.stdout)
        self.assertIn("中没有 bridge；无需改动", self.run_script("dsh/mac-uninstall.sh").stdout)
        self.assertFalse(plugin.exists())

    def test_dsh_uninstall_falls_back_to_backups_then_to_setup_hint(self):
        patch, own, _ = self.dsh_installed()
        backups = self.home / ".dsh/backup-reason-network-bridge"
        (backups / "reason-network-block.yml").unlink()
        newer = own.replace("old", "newer")
        (backups / "cordis.patch.yml.29991231235959.zzzzzz").write_text(newer)
        result = self.run_script("dsh/mac-uninstall.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(own, patch.read_text())
        self.assertNotIn("newer", patch.read_text())
        self.run_script("dsh/mac-install.sh", "demo")
        for path in backups.iterdir():
            path.unlink()
        result = self.run_script("dsh/mac-uninstall.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("reason-network", patch.read_text())
        self.assertIn("reason-network setup", result.stdout)

    def test_dsh_uninstall_refuses_ambiguous_markers_and_changes_nothing(self):
        patch, _, plugin = self.dsh_installed()
        good = patch.read_text()
        self.assertEqual(self.run_script("dsh/mac-uninstall.sh", "--bogus").returncode, 2)
        begin = "# === reason-network-bridge BEGIN (replaces reason-network's MCP block) ===\n"
        end = "# === reason-network-bridge END ===\n"
        for broken in (good.replace(end, ""), good.replace(begin, ""),
                       begin + "- keep: 0\n" + good, good.replace(begin, end + begin)):
            patch.write_text(broken)
            result = self.run_script("dsh/mac-uninstall.sh")
            self.assertNotEqual(result.returncode, 0, broken)
            self.assertIn("edit the file by hand", result.stderr)
            self.assertEqual(patch.read_text(), broken)
            self.assertTrue(plugin.exists())

    def test_dsh_uninstall_keeps_lines_after_a_block_saved_without_newline(self):
        self.engine()
        app = self.home / "Applications/DeepSeek Harness.app/Contents/MacOS/DeepSeek Harness"
        self.tool("app", "echo 'ℹ pass 7'", app)
        patch = self.home / ".dsh/cordis.patch.yml"
        patch.parent.mkdir(parents=True)
        patch.write_text("- before: 1\n# === reason-network MCP BEGIN ===\nold\n# === reason-network MCP END ===")
        self.assertEqual(self.run_script("dsh/mac-install.sh", "demo").returncode, 0)
        patch.write_text(patch.read_text() + "- unrelated: 2\n")
        self.assertEqual(self.run_script("dsh/mac-uninstall.sh").returncode, 0)
        self.assertEqual(patch.read_text(), "- before: 1\n# === reason-network MCP BEGIN ===\nold\n"
                                             "# === reason-network MCP END ===\n- unrelated: 2\n")

    def test_legacy_uninstall_all_yes_is_refused_and_changes_nothing(self):
        patch, _, plugin = self.dsh_installed()
        before = patch.read_text()
        result = self.run_script("uninstall.sh", "--all", "-y")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(patch.read_text(), before)
        self.assertTrue(plugin.exists())
        self.assertTrue((self.home / ".reason/network/config.toml").exists())
