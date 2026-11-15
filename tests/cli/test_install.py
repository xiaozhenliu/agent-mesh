import hashlib
import plistlib
import unittest

from render_prompt import ROOT
from write_launch_agent import write_launch_agent
from tests.cli.install_support import (
    InstallHarness,
)


class Install(InstallHarness, unittest.TestCase):
    def test_cloud_prompt_uses_public_https_and_actual_home_without_git(self):
        self.engine()
        for lang in ("en", "zh-CN"):
            result = self.run_script("install.sh", "--cloud-prompt", "--lang", lang)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("URL=https://github.com/xiaozhenliu/agent-mesh.git\n", result.stdout)
            self.assertIn(str(self.home / ".config/agent-relay/outbound.key"), result.stdout)
            self.assertNotIn("<mac-home>", result.stdout)
        self.assertNotIn("unexpected git", self.calls())
        self.assertNotIn("curl", self.calls())
        self.assertNotIn("bootstrap", self.calls())

    def test_cloud_prompt_is_pinned_to_the_published_release(self):
        self.engine()
        tag = "v" + (ROOT / "VERSION").read_text().strip()
        cases = (("release-fail", "could not be fetched"),
                 ("release/box/rn-fetch.py", "box/rn-fetch.py in this checkout differs from the release"))
        for path, why in cases:
            with self.subTest(why):
                (self.git / path).parent.mkdir(parents=True, exist_ok=True)
                (self.git / path).write_text("other\n")
                result = self.run_script("install.sh", "--cloud-prompt")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(why, result.stderr)
                self.assertIn("./install.sh --cloud-prompt", result.stderr)
                self.assertEqual(result.stdout.count("AGENT_MESH_SETUP"), 0)
                self.assertIsNone(self.record())
                (self.git / path).unlink()
        result = self.run_script("install.sh", "--cloud-prompt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"git fetch https://github.com/xiaozhenliu/agent-mesh.git refs/tags/{tag}:refs/tags/{tag}",
                      self.calls())
        self.assertIn(f"TAG='{tag}'; COMMIT='{'a' * 40}'", result.stdout)
        for name in ("box/rn-cloud.sh", "box/rn-fetch.py", "box/rn-cloud-setup.sh", "box/rn-engine.py"):
            digest = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            self.assertIn(f"{digest}  {name}", result.stdout)
        for placeholder in ("<release-tag>", "<release-commit>", "<cloud-checksums>", "<repo-url>"):
            self.assertNotIn(placeholder, result.stdout)

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
        self.assertIn("URL=https://example.invalid/fork.git\n", result.stdout)

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
        self.assertIn("never downgrades or replaces a newer or unrecognised engine", result.stderr)
        self.assertEqual(engine.read_bytes(), before)
        self.env["MOCK_VERSION"] = "0.10.7"
        result = self.run_script("install.sh", "--name", "demo")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("this release requires 0.12.17. Run ./update.sh", result.stderr)
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
            result = self.run_script("install.sh", "--name", "demo", "--relay-name", "nova")
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("could not download Reason Network v0.12.17; nothing was installed", result.stderr)
            self.assertIn(f"reason-network-{target}.tar.gz", self.calls())
            self.assertNotIn(".sha256", self.calls())

    def test_unsupported_cloud_os_and_cpu_stop_before_writes(self):
        for system, arch, error in (("Darwin", "arm64", "requires Linux"), ("Linux", "riscv64", "unsupported cloud CPU")):
            self.env.update(MOCK_OS=system, MOCK_ARCH=arch)
            result = self.run_script("box/rn-cloud-setup.sh", "fake-invite", "demo")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(error, result.stderr)
            self.assertNotIn("curl", self.calls())

    def test_names_the_engine_would_change_are_refused_before_downloads(self):
        for args in (("--name", "a--b"), ("--name", "demo", "--machine", "m--1"), ("--name", "x" * 41),
                     ("--name", "demo-")):
            result = self.run_script("install.sh", *args)
            self.assertNotEqual(result.returncode, 0, args)
            self.assertIn("none repeated", result.stderr, args)
        self.assertIn("could not download", self.run_script("install.sh", "--name", "x" * 40, "--relay-name", "nova").stderr)
        self.tool("sha256sum", "exit 0")
        self.env.update(MOCK_OS="Linux", MOCK_ARCH="x86_64")
        (self.base / "calls").unlink()
        for person in ("a--b", "x" * 41, "-a"):
            result = self.run_script("box/rn-cloud-setup.sh", "fake-invite", person)
            self.assertNotEqual(result.returncode, 0, person)
            self.assertIn("invalid team/person name", result.stderr, person)
        self.assertNotIn("curl", self.calls())

    def test_launch_agent_keeps_selected_python_and_escapes_paths(self):
        destination = self.base / "agent.plist"
        python = str(self.home / "Python & Tools/bin/python3")
        relay = str(self.home / ".local/lib/agent-mesh/agent_relay.py")
        write_launch_agent(self.home, python, relay, destination)
        with destination.open("rb") as stream:
            data = plistlib.load(stream)
        self.assertEqual(data["ProgramArguments"], [python, relay])
        self.assertEqual(data["EnvironmentVariables"]["REASON_NETWORK_HOME"], str(self.home / ".reason/network"))
