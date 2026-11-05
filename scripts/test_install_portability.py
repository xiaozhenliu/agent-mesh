"""Installation checks with fake tools and a disposable home; never contact real services."""
import os
import plistlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from render_prompt import ROOT, render_prompt
from write_launch_agent import write_launch_agent


class Installation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "custom homes" / "Alex's & Team"
        self.home.mkdir(parents=True)
        self.bin = self.base / "tools"
        self.bin.mkdir()
        for name in ("dirname", "sed", "head", "awk", "tar", "shasum", "openssl", "mkdir",
                     "chmod", "cp", "date", "grep", "cat", "install", "sleep", "tail", "wc", "mktemp", "rm", "mv"):
            found = shutil.which(name)
            if found:
                (self.bin / name).symlink_to(found)
        (self.bin / "python3").symlink_to(sys.executable)
        self.env = {"PATH": str(self.bin), "HOME": str(self.home),
                    "MOCK_OS": "Darwin", "MOCK_ARCH": "arm64", "MOCK_VERSION": "0.10.7",
                    "CALL_LOG": str(self.base / "calls")}
        self.tool("uname", 'if [ "$1" = -s ]; then echo "$MOCK_OS"; else echo "$MOCK_ARCH"; fi')
        self.tool("id", "echo 501")
        self.tool("launchctl", 'echo "launchctl $*" >> "$CALL_LOG"; exit 0')
        self.tool("plutil", "exit 0")
        self.tool("curl", 'echo "curl $*" >> "$CALL_LOG"; case "$*" in *https://aps1-1*) printf 200 ;; *) exit 73 ;; esac')
        self.tool("git", 'echo "unexpected git call" >> "$CALL_LOG"; exit 99')

    def tool(self, name, body, path=None):
        path = path or self.bin / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
        return path

    def engine(self):
        path = self.home / ".local/bin/reason-network"
        self.tool("reason-network", 'case "$1" in --version) echo "reason-network $MOCK_VERSION" ;; invite) echo "For a cloud agent of yours send it: fake-invite" ;; *) exit 0 ;; esac', path)
        cfg = self.home / ".reason/network/config.toml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text('person = "demo"\n')
        return path

    def run_script(self, script="install.sh", *args):
        return subprocess.run(["/bin/bash", str(ROOT / script), *args], env=self.env,
                              input="", capture_output=True, text=True, timeout=10)

    def calls(self):
        path = self.base / "calls"
        return path.read_text() if path.exists() else ""

    def test_cloud_prompt_uses_public_https_and_actual_home_without_git(self):
        self.engine()
        for lang in ("en", "zh-CN"):
            result = self.run_script("install.sh", "--cloud-prompt", "--lang", lang)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("git clone https://github.com/xiaozhenliu/agent-mesh.git", result.stdout)
            self.assertIn(str(self.home / ".config/agent-relay/outbound.key"), result.stdout)
            self.assertNotIn("<mac-home>", result.stdout)
        self.assertNotIn("unexpected git", self.calls())
        self.assertNotIn("curl", self.calls())
        self.assertNotIn("bootstrap", self.calls())

    def configured(self):
        """An installed engine plus a configured relay: env with a webhook, and the current key."""
        self.engine()
        conf = self.home / ".config/agent-relay"
        conf.mkdir(parents=True)
        (conf / "env").write_text("WEBHOOK_URL=https://example.invalid/hook\nWEBHOOK_KEY=0123456789\n"
                                  "OUTBOUND_KEY_FILE=~/.config/agent-relay/outbound.key\n")
        key = conf / "outbound.key"
        key.write_text("a" * 64 + "\n")
        key.chmod(0o600)
        log = self.home / "Library/Logs/agent-relay.log"
        self.tool("launchctl", f'echo "launchctl $*" >> "$CALL_LOG"; '
                               f'[ "$1" = bootstrap ] && echo "agent-relay up" >> "{log}"; exit 0')
        return key

    def test_rotate_key_replaces_key_restarts_relay_and_explains_cloud_copy(self):
        key = self.configured()
        result = self.run_script("install.sh", "--rotate-key", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        new = key.read_text()
        self.assertRegex(new, r"^[0-9a-f]{64}\n$")
        self.assertNotEqual(new, "a" * 64 + "\n")
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(key.parent.glob("outbound.key.new.*")), [])
        self.assertIn("bootstrap", self.calls())
        self.assertIn("CopyToBox", result.stdout)
        self.assertIn("/workspace/agent-mesh/box/outbound.key", result.stdout)
        self.assertIn("./install.sh --webhook", result.stdout)
        self.assertNotIn(new.strip(), result.stdout + result.stderr)

    def test_rotate_key_without_terminal_or_relay_changes_nothing(self):
        key = self.configured()
        result = self.run_script("install.sh", "--rotate-key")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(key.read_text(), "a" * 64 + "\n")
        self.assertNotIn("bootstrap", self.calls())
        (key.parent / "env").unlink()
        result = self.run_script("install.sh", "--rotate-key", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("relay is not configured", result.stderr)
        self.assertEqual(key.read_text(), "a" * 64 + "\n")

    def test_rotate_key_refuses_when_the_relay_loads_another_key(self):
        key = self.configured()
        env = key.parent / "env"
        base = env.read_text()
        for extra, said in (("OUTBOUND_KEY=" + "b" * 64 + "\n", "sets OUTBOUND_KEY inline"),
                            ("OUTBOUND_KEY_FILE=/elsewhere/outbound.key\n", "does not load the key")):
            env.write_text(base + extra)
            result = self.run_script("install.sh", "--rotate-key", "--yes")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(said, result.stderr)
            self.assertEqual(key.read_text(), "a" * 64 + "\n")
            self.assertNotIn("bootstrap", self.calls())

    def test_purge_copies_removes_kept_bodies_only(self):
        state = self.home / "Library/Application Support/agent-relay"
        result = self.run_script("install.sh", "--purge-copies")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("removed 0 kept message copies", result.stdout)
        copies = state / "fwd-copies"
        copies.mkdir(parents=True)
        for name in ("01A.json", "01B.json", "01C.tmp"):
            (copies / name).write_text("{}")
        (state / "state.json").write_text("{}")
        result = self.run_script("install.sh", "--purge-copies")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("removed 3 kept message copies", result.stdout)
        self.assertEqual(list(copies.iterdir()), [])
        self.assertTrue((state / "state.json").exists())
        self.assertNotIn("curl", self.calls())
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "keep.json").write_text("{}")
        copies.rmdir()
        copies.symlink_to(elsewhere, target_is_directory=True)
        result = self.run_script("install.sh", "--purge-copies")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", result.stderr)
        self.assertTrue((elsewhere / "keep.json").exists())

    def test_explicit_public_fork(self):
        self.engine()
        result = self.run_script("install.sh", "--cloud-prompt", "--repo-url", "https://example.invalid/fork.git")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("git clone https://example.invalid/fork.git", result.stdout)

    def test_ssh_repository_override_is_rejected(self):
        result = self.run_script("install.sh", "--cloud-prompt", "--repo-url", "git@example.invalid:repo.git")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("public HTTPS", result.stderr)

    def test_existing_other_version_is_not_overwritten(self):
        engine = self.engine()
        before = engine.read_bytes()
        self.env["MOCK_VERSION"] = "9.9.9"
        result = self.run_script("install.sh", "--name", "demo")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("has not been overwritten", result.stderr)
        self.assertEqual(engine.read_bytes(), before)
        self.assertNotIn("curl", self.calls())

    def test_other_path_installation_is_not_duplicated(self):
        self.tool("reason-network", "exit 0")
        result = self.run_script("install.sh", "--name", "demo")
        self.assertIn("already exists at", result.stderr)
        self.assertNotIn("curl", self.calls())

    def test_custom_network_home_stops_before_changes(self):
        self.env["REASON_NETWORK_HOME"] = str(self.base / "another-profile")
        result = self.run_script("install.sh", "--name", "demo")
        self.assertIn("custom REASON_NETWORK_HOME", result.stderr)
        self.assertNotIn("curl", self.calls())

    def test_missing_dependency_stops_before_download(self):
        (self.bin / "curl").unlink()
        result = self.run_script("install.sh", "--name", "demo")
        self.assertIn("missing curl", result.stderr)
        self.assertFalse((self.home / ".local").exists())

    def test_missing_gui_login_is_explained_before_changes(self):
        self.tool("launchctl", "exit 1")
        result = self.run_script("install.sh", "--name", "demo")
        self.assertIn("no macOS GUI login session", result.stderr)
        self.assertNotIn("curl", self.calls())

    def test_root_and_unsupported_local_system_are_rejected(self):
        self.env["MOCK_OS"] = "Linux"
        result = self.run_script("install.sh", "--name", "demo")
        self.assertIn("requires macOS", result.stderr)
        self.env["MOCK_OS"] = "Darwin"
        self.tool("id", "echo 0")
        result = self.run_script("install.sh", "--name", "demo")
        self.assertIn("without sudo", result.stderr)
        self.assertNotIn("curl", self.calls())

    def test_incomplete_python_is_explained(self):
        (self.bin / "python3").unlink()
        self.tool("python3", 'case "$*" in *sys.executable*) echo "$0" ;; *) exit 1 ;; esac')
        result = self.run_script("install.sh", "--name", "demo")
        self.assertIn("Python 3.9+ with ssl and sqlite3", result.stderr)
        self.assertNotIn("curl", self.calls())

    def test_both_mac_architectures_select_their_own_release(self):
        for arch, target in (("arm64", "aarch64-apple-darwin"), ("x86_64", "x86_64-apple-darwin")):
            self.env["MOCK_ARCH"] = arch
            result = self.run_script("install.sh", "--name", "demo")
            self.assertEqual(result.returncode, 73, result.stderr)
            self.assertIn(f"reason-network-{target}.tar.gz", self.calls())

    def test_unsupported_cloud_os_and_cpu_stop_before_writes(self):
        for system, arch, error in (("Darwin", "arm64", "requires Linux"), ("Linux", "riscv64", "unsupported cloud CPU")):
            self.env.update(MOCK_OS=system, MOCK_ARCH=arch)
            result = self.run_script("box/rn-cloud-setup.sh", "fake-invite", "demo")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(error, result.stderr)
            self.assertNotIn("curl", self.calls())

    def test_cloud_runtime_on_mac_fails_before_accessing_cloud_paths(self):
        result = self.run_script("box/rn-cloud.sh", "who")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Bot's Linux cloud computer", result.stderr)
        self.assertNotIn("No such file", result.stderr)

    def test_cloud_purge_stops_engine_first_and_checks_days(self):
        box = self.base / "box"
        (box / "state").mkdir(parents=True)
        (box / "person").write_text("demo\n")
        (box / "outbound.key").write_text("k" * 64)
        (box / "rn-fetch.py").symlink_to(ROOT / "box/rn-fetch.py")
        self.tool("reason-network", 'echo "rn $*" >> "$CALL_LOG"', box / "bin/reason-network")
        db = sqlite3.connect(box / "state/network.db")
        db.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, sender TEXT, recipient TEXT, body TEXT, sent_at INTEGER)")
        db.execute("INSERT INTO messages VALUES ('m1', 'demo/terminal', 'demo/bot-inbox', 'old text', 1)")
        db.commit()
        db.close()
        self.env.update(MOCK_OS="Linux", MOCK_ARCH="x86_64", RN_CLOUD_BASE=str(box),
                        RN_INBOX_DIR=str(self.base / "inbox"), RN_THREADS_DIR=str(self.base / "threads"))
        for bad in ((), ("x",), ("-1",)):
            result = self.run_script("box/rn-cloud.sh", "purge", *bad)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("usage: rn-cloud.sh purge <days>", result.stderr)
        self.assertNotIn("rn stop", self.calls())
        result = self.run_script("box/rn-cloud.sh", "purge", "0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"messages": 1', result.stdout)
        self.assertEqual(self.calls().strip(), "rn stop")

    def test_launch_agent_keeps_selected_python_and_escapes_paths(self):
        destination = self.base / "agent.plist"
        python = str(self.home / "Python & Tools/bin/python3")
        relay = str(self.home / ".local/lib/agent-mesh/agent_relay.py")
        write_launch_agent(self.home, python, relay, destination)
        with destination.open("rb") as stream:
            data = plistlib.load(stream)
        self.assertEqual(data["ProgramArguments"], [python, relay])
        self.assertEqual(data["EnvironmentVariables"]["REASON_NETWORK_HOME"], str(self.home / ".reason/network"))

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


    def dsh_installed(self):
        self.engine()
        app = self.home / "Applications/DeepSeek Harness.app/Contents/MacOS/DeepSeek Harness"
        self.tool("app", "echo 'ℹ pass 7'", app)
        patch = self.home / ".dsh/cordis.patch.yml"
        patch.parent.mkdir(parents=True)
        own = "# === reason-network MCP BEGIN ===\nold\n# === reason-network MCP END ===\n"
        patch.write_text("- before: 1\n" + own + "- after: 2\n")
        result = self.run_script("dsh/mac-install.sh", "demo")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        plugin = self.home / ".dsh/plugins/reason-network-bridge.mjs"
        self.assertTrue(plugin.is_file())
        patch.write_text(patch.read_text() + "- added later: 3\n")
        return patch, own, plugin

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

    def test_uninstall_all_stops_before_removing_the_engine_if_dsh_cleanup_fails(self):
        patch, _, plugin = self.dsh_installed()
        patch.write_text(patch.read_text().replace("# === reason-network-bridge END ===\n", ""))
        result = self.run_script("uninstall.sh", "--all", "-y")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Reason Network was left in place", result.stdout)
        self.assertTrue((self.home / ".local/bin/reason-network").exists())
        self.assertTrue((self.home / ".reason/network/config.toml").exists())
        self.assertTrue(plugin.exists())

    def test_uninstall_all_drops_dsh_bridge_without_restoring(self):
        patch, _, plugin = self.dsh_installed()
        result = self.run_script("uninstall.sh", "--all", "-y")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(patch.read_text(), "- before: 1\n- after: 2\n- added later: 3\n")
        self.assertFalse(plugin.exists())
        self.assertIn("dsh bridge block", result.stdout)

if __name__ == "__main__":
    unittest.main()
