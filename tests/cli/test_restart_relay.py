import hashlib
import signal
import subprocess
import time
import unittest

from tests.cli.install_support import (
    InstallHarness,
)
from tests.cli.test_install_add import LEGACY_LABEL, AddSupport, m


class RestartRelay(InstallHarness, unittest.TestCase):
    def test_restart_relay_needs_the_relay_pid_from_launchd(self):
        repo = self.installed()
        relay, _ = self.relay_files()
        old_relay = relay.read_bytes()
        with open(repo / "agent_relay.py", "a") as stream:
            stream.write("\n")
        (self.state / "nopid").write_text("")
        result = self.run_script("install.sh", "--restart-relay", root=repo)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("the relay service has no running process", result.stdout + result.stderr)
        self.assertIn("previous relay restored", result.stdout)
        self.assertEqual(relay.read_bytes(), old_relay)
        (self.state / "nopid").unlink()
        result = self.run_script("install.sh", "--restart-relay", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_restart_relay_rolls_back_each_failed_start(self):
        repo = self.installed()
        relay, plist = self.relay_files()
        old_relay, old_plist = relay.read_bytes(), plist.read_bytes()
        old_hash = hashlib.sha256(old_relay).hexdigest()
        with open(repo / "agent_relay.py", "a") as stream:
            stream.write("\n")
        for flag in ("no-up", "fail-bootstrap", "die"):
            (self.state / flag).write_text("")
            result = self.run_script("install.sh", "--restart-relay", root=repo)
            self.assertNotEqual(result.returncode, 0, flag)
            self.assertIn("previous relay restored", result.stdout, flag + result.stdout)
            self.assertEqual(relay.read_bytes(), old_relay, flag)
            self.assertEqual(plist.read_bytes(), old_plist, flag)
            self.assertTrue(self.loaded(), flag)
            self.assertEqual(self.started()[-1], old_hash, flag)
            self.assertFalse((relay.parent / "rollback").exists(), flag)
            lines = self.service_changes()
            self.assertEqual(lines[-2].split()[1], "bootout", flag)
        result = self.run_script("install.sh", "--restart-relay", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(relay.read_bytes(), (repo / "agent_relay.py").read_bytes())
        self.assertEqual(self.started()[-1], self.sha(repo / "agent_relay.py"))

    def test_restart_relay_rolls_back_when_the_new_relay_registers_under_another_name(self):
        repo = self.installed()
        relay, plist = self.relay_files()
        old_relay, old_plist = relay.read_bytes(), plist.read_bytes()
        with open(repo / "agent_relay.py", "a") as stream:
            stream.write("\n")
        self.env.update(MOCK_REGISTER="bot-2", AGENT_MESH_REGISTER_WAIT="1")
        result = self.run_script("install.sh", "--restart-relay", root=repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("the new relay registered as demo/bot-2 instead of demo/bot", result.stdout)
        self.assertIn("previous relay restored", result.stdout)
        self.assertNotIn("relay up (log:", result.stdout)
        self.assertEqual((relay.read_bytes(), plist.read_bytes()), (old_relay, old_plist))
        self.assertTrue(self.loaded())
        self.assertEqual(self.started()[-1], hashlib.sha256(old_relay).hexdigest())
        self.assertFalse((relay.parent / "rollback").exists())

    def test_restart_relay_without_a_backup_changes_nothing(self):
        repo = self.installed()
        relay, plist = self.relay_files()
        old = relay.read_bytes()
        with open(repo / "agent_relay.py", "a") as stream:
            stream.write("\n")
        relay.parent.chmod(0o555)
        try:
            result = self.run_script("install.sh", "--restart-relay", root=repo)
        finally:
            relay.parent.chmod(0o755)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not save a copy of the current relay; nothing changed", result.stdout)
        self.assertEqual(relay.read_bytes(), old)
        self.assertEqual(self.service_changes(), [])

    def test_restart_relay_finishes_or_refuses_a_leftover_switch(self):
        repo = self.installed()
        relay, plist = self.relay_files()
        good = relay.read_bytes()
        rollback = relay.parent / "rollback"

        def leftover(tamper=False):
            rollback.mkdir()
            (rollback / "loaded").write_text("1\n")
            (rollback / "agent_relay.py").write_bytes(good)
            (rollback / "agent.plist").write_bytes(plist.read_bytes())
            sums = "".join(f"{self.sha(rollback / name)}  {name}\n" for name in ("loaded", "agent_relay.py", "agent.plist"))
            (rollback / "SHA256SUMS").write_text(sums)
            if tamper:
                (rollback / "agent_relay.py").write_bytes(b"tampered")
            relay.write_bytes(b"half-switched")
            (self.state / "loaded").unlink(missing_ok=True)

        leftover(tamper=True)
        (relay.parent / "rollback.tmp").mkdir()
        result = self.run_script("install.sh", "--restart-relay", root=repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed verification", result.stdout)
        self.assertEqual(relay.read_bytes(), b"half-switched")
        self.assertTrue(rollback.exists())
        for path in rollback.iterdir():
            path.unlink()
        rollback.rmdir()
        leftover()
        result = self.run_script("install.sh", "--restart-relay", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("did not finish; restoring the previous relay first", result.stdout)
        self.assertEqual(self.started()[-2], hashlib.sha256(good).hexdigest())
        self.assertEqual(relay.read_bytes(), (repo / "agent_relay.py").read_bytes())
        self.assertFalse(rollback.exists())
        self.assertFalse((relay.parent / "rollback.tmp").exists())

    def test_restart_relay_interrupted_mid_switch_restores_the_previous_relay(self):
        repo = self.installed()
        relay, plist = self.relay_files()
        old_relay, old_plist = relay.read_bytes(), plist.read_bytes()
        with open(repo / "agent_relay.py", "a") as stream:
            stream.write("\n")
        (self.state / "hang").write_text("")
        proc = subprocess.Popen(["/bin/bash", str(repo / "install.sh"), "--restart-relay"], env=self.env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.time() + 30
        while not (self.state / "hanging").exists() and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue((self.state / "hanging").exists())
        self.assertFalse(self.loaded())
        proc.send_signal(signal.SIGTERM)
        (self.state / "go").write_text("")
        out, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 143, out + err)
        self.assertIn("interrupted; restoring the previous relay", out)
        self.assertEqual((relay.read_bytes(), plist.read_bytes()), (old_relay, old_plist))
        self.assertTrue(self.loaded())
        self.assertEqual(self.started()[-1], hashlib.sha256(old_relay).hexdigest())
        self.assertFalse((relay.parent / "rollback").exists())

    def test_update_finishes_a_switch_killed_after_the_files_moved(self):
        repo = self.installed()
        relay, plist = self.relay_files()
        rollback = relay.parent / "rollback"
        rollback.mkdir()
        (rollback / "loaded").write_text("1\n")
        (rollback / "agent_relay.py").write_bytes(relay.read_bytes())
        (rollback / "agent.plist").write_bytes(plist.read_bytes())
        (rollback / "SHA256SUMS").write_text(
            "".join(f"{self.sha(rollback / name)}  {name}\n" for name in ("loaded", "agent_relay.py", "agent.plist")))
        (self.state / "loaded").unlink()
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("already up to date", result.stdout)
        self.assertTrue(self.loaded())
        self.assertFalse(rollback.exists())


class SeveralBots(AddSupport, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.repo_dir = self.two_ready()
        self.added(self.repo_dir)
        self.key = self.home / ".config/agent-relay/outbound.key"
        self.labels = [LEGACY_LABEL, f"{LEGACY_LABEL}.beta"]

    def plists(self):
        return [self.legacy_plist().read_bytes(), self.plist("beta").read_bytes()]

    def test_rotate_key_restarts_every_relay_with_the_new_key(self):
        old, before = self.key.read_text(), len(self.multi_started())
        result = self.run_script("install.sh", "--rotate-key", "--yes", root=self.repo_dir, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual(self.key.read_text(), old)
        self.assertEqual([label for label, _ in self.multi_started()[before:]], self.labels)
        self.assertIn(m("install.rotate_key_kept", self.key), result.stdout)
        self.assertIn(m("install.rotate_bot_new", "demo/alpha"), result.stdout)
        self.assertIn(m("install.rotate_bot_new", "demo/beta → Grok: Beta"), result.stdout)
        result = self.run_script("install.sh", "--rotate-key", "--yes", "--relay-name", "beta", root=self.repo_dir)
        self.assertEqual(result.returncode, 1)
        self.assertIn(m("install.all_bots_no_name"), result.stderr)

    def test_a_failed_restart_restores_every_relay_and_says_the_key_is_new(self):
        plists = self.plists()
        for label in self.labels:
            old = self.key.read_text()
            (self.state / f"fail-bootstrap.{label}").write_text("")
            result = self.run_script("install.sh", "--rotate-key", "--yes", root=self.repo_dir, timeout=60)
            self.assertEqual(result.returncode, 1, label + result.stdout + result.stderr)
            self.assertIn(m("install.rotate_restart_failed_all"), result.stderr, label)
            self.assertIn(m("install.rotate_key_kept", self.key), result.stdout, label)
            self.assertIn("previous relay restored", result.stdout, label)
            self.assertNotEqual(self.key.read_text(), old, label)
            self.assertEqual(self.plists(), plists, label)
            self.assertEqual(sorted(self.multi_loaded()), self.labels, label)
            self.assertFalse((self.home / ".local/lib/agent-mesh/rollback").exists(), label)

    def test_a_failed_restart_of_the_last_relay_restores_the_code_of_all(self):
        relay = self.home / ".local/lib/agent-mesh/agent_relay.py"
        old_relay, plists = relay.read_bytes(), self.plists()
        old_sha = hashlib.sha256(old_relay).hexdigest()
        with open(self.repo_dir / "agent_relay.py", "a") as stream:
            stream.write("\n")
        new_sha = self.sha(self.repo_dir / "agent_relay.py")
        for flag in ("no-up", "die", "noreg"):
            (self.state / f"{flag}.{LEGACY_LABEL}.beta").write_text("")
            before = len(self.multi_started())
            result = self.run_script("install.sh", "--restart-relay", root=self.repo_dir, timeout=60)
            self.assertNotEqual(result.returncode, 0, flag)
            self.assertIn("previous relay restored", result.stdout, flag + result.stdout + result.stderr)
            self.assertEqual(relay.read_bytes(), old_relay, flag)
            self.assertEqual(self.plists(), plists, flag)
            started = self.multi_started()[before:]
            self.assertEqual(started[:2], [[LEGACY_LABEL, new_sha], [f"{LEGACY_LABEL}.beta", new_sha]], flag)
            self.assertEqual(started[2:], [[LEGACY_LABEL, old_sha], [f"{LEGACY_LABEL}.beta", old_sha]], flag)
            self.assertEqual(sorted(self.multi_loaded()), self.labels, flag)
        result = self.run_script("install.sh", "--restart-relay", root=self.repo_dir, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(relay.read_bytes(), (self.repo_dir / "agent_relay.py").read_bytes())
