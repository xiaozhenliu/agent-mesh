"""The management lock (scripts/mesh_lock.py, scripts/lock.sh) with disposable homes; never touch real services."""
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from render_prompt import ROOT
from tests.cli.install_support import InstallHarness
from tests.cli.test_engine import fake_engine

DRIVER = r'''set -euo pipefail
. "$AGENT_MESH_ROOT/scripts/msg.sh"
. "$AGENT_MESH_ROOT/scripts/lock.sh"
mesh_lock_take python3 || exit 1
echo "taken $$"
if [ $# -gt 0 ]; then "$@"; fi
'''

HOLDER = r'''import fcntl, os, sys, time
path, write, delete = sys.argv[1], sys.argv[2] == "1", sys.argv[3] == "1"
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
fcntl.flock(fd, fcntl.LOCK_EX)
if write:
    os.ftruncate(fd, 0)
    os.write(fd, b"pid 4242\n")
print("held", flush=True)
sys.stdin.readline()
if delete:
    os.unlink(path)
'''


class LockHome:
    def make_home(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.lhome = self.base / "home"
        self.lhome.mkdir()
        self.driver = self.base / "driver.sh"
        self.driver.write_text(DRIVER)
        self.lockfile = self.lhome / ".local/lib/agent-mesh.lock"
        self.lenv = dict(os.environ, HOME=str(self.lhome), AGENT_MESH_ROOT=str(ROOT), AGENT_MESH_LANG="en")
        self.lenv.pop("AGENT_MESH_LOCK_FD", None)
        self.lenv.pop("AGENT_MESH_LOCK_WAIT", None)

    def drive(self, *args, env=None, prefix=(), timeout=30):
        return subprocess.run(["/bin/bash", *prefix, str(self.driver), *args], env=env or self.lenv,
                              capture_output=True, text=True, timeout=timeout)

    def hold(self, write=True, delete=False):
        self.lockfile.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen([sys.executable, "-c", HOLDER, str(self.lockfile), "1" if write else "0",
                                 "1" if delete else "0"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (proc.kill(), proc.wait(), proc.stdin.close(), proc.stdout.close()))
        self.assertEqual(proc.stdout.readline().strip(), "held")
        return proc

    def release(self, proc):
        proc.stdin.write("\n")
        proc.stdin.flush()
        proc.wait(timeout=10)


class MeshLock(LockHome, unittest.TestCase):
    def setUp(self):
        self.make_home()

    def test_takes_the_lock_and_records_the_shell(self):
        r = self.drive()
        self.assertEqual(r.returncode, 0, r.stderr)
        pid = r.stdout.split()[1]
        self.assertEqual(self.lockfile.read_text(), f"pid {pid}\n")
        r = self.drive()
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_only_one_of_two_commands_started_together_goes_on(self):
        for _ in range(5):
            procs = [subprocess.Popen(["/bin/bash", str(self.driver), sys.executable, "-c",
                                       "import time; time.sleep(1.5)"], env=self.lenv, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True) for _ in range(2)]
            results = [(p.wait(timeout=30), *p.communicate()) for p in procs]
            won = [r for r in results if r[0] == 0]
            lost = [r for r in results if r[0] != 0]
            self.assertEqual((len(won), len(lost)), (1, 1), results)
            self.assertIn("taken", won[0][1])
            self.assertIn("another management command", lost[0][2])
            self.assertNotIn("taken", lost[0][1])

    def test_a_lock_file_without_a_pid_yet_is_still_held(self):
        holder = self.hold(write=False)
        self.assertEqual(self.lockfile.read_text(), "")
        r = self.drive()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("another management command", r.stderr)
        self.assertIn("(? in ", r.stderr)
        self.assertEqual(self.lockfile.read_text(), "")
        self.release(holder)
        self.assertEqual(self.drive().returncode, 0)

    def test_the_recorded_pid_is_only_shown(self):
        holder = self.hold()
        r = self.drive()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("pid 4242", r.stderr)
        self.release(holder)
        self.lockfile.write_text("pid 1\n")
        r = self.drive()
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_a_child_with_the_variable_inherits_and_one_without_it_is_refused(self):
        child = self.base / "child.sh"
        child.write_text(DRIVER)
        r = self.drive("/bin/bash", str(child))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("taken"), 2)
        r = self.drive("env", "-u", "AGENT_MESH_LOCK_FD", "/bin/bash", str(child))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(r.stdout.count("taken"), 1)
        self.assertIn("another management command", r.stderr)

    def test_a_forged_lock_variable_is_refused(self):
        other = self.base / "other.lock"
        cases = (("fd 9 on another file", f'exec 9>>"{other}"; AGENT_MESH_LOCK_FD=9 exec /bin/bash "$0"'),
                 ("fd 9 not open", 'exec 9>&-; AGENT_MESH_LOCK_FD=9 exec /bin/bash "$0"'),
                 ("another fd number", 'AGENT_MESH_LOCK_FD=7 exec /bin/bash "$0"'),
                 ("the lock file, but another command holds the lock",
                  f'exec 9>>"{self.lockfile}"; AGENT_MESH_LOCK_FD=9 exec /bin/bash "$0"'))
        for why, wrapper in cases:
            with self.subTest(why):
                holder = self.hold() if why.startswith("the lock file") else None
                r = subprocess.run(["/bin/bash", "-c", wrapper, str(self.driver)], env=self.lenv,
                                   capture_output=True, text=True, timeout=30)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertNotIn("taken", r.stdout)
                self.assertIn("does not hold the management lock", r.stderr)
                if holder:
                    self.release(holder)

    def test_a_lock_file_deleted_while_waiting_is_opened_again(self):
        holder = self.hold(delete=True)
        proc = subprocess.Popen(["/bin/bash", str(self.driver), sys.executable, "-c", "import time; time.sleep(1)"],
                                env=dict(self.lenv, AGENT_MESH_LOCK_WAIT="20"), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        time.sleep(1.5)
        self.assertIsNone(proc.poll())
        self.release(holder)
        out, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0, out + err)
        self.assertIn("taken", out)
        self.assertTrue(self.lockfile.exists())
        self.assertEqual(self.lockfile.read_text(), f"pid {out.split()[1]}\n")

    def test_waiting_ends_when_the_holder_leaves(self):
        holder = self.hold()
        proc = subprocess.Popen(["/bin/bash", str(self.driver)], env=dict(self.lenv, AGENT_MESH_LOCK_WAIT="20"),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(0.5)
        self.release(holder)
        out, err = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0, out + err)

    def test_a_running_older_update_blocks_and_a_stale_lock_is_removed(self):
        legacy = self.lhome / ".local/lib/agent-mesh/update.lock"
        legacy.mkdir(parents=True)
        update = subprocess.Popen(["/bin/sh", "-c", "sleep 30; :", "update.sh"])
        other = subprocess.Popen(["/bin/sh", "-c", "sleep 30; :", "something"])
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        try:
            (legacy / "pid").write_text(f"{update.pid}\n")
            r = self.drive()
            self.assertEqual(r.returncode, 1, r.stdout)
            self.assertIn("another ./update.sh is running", r.stderr)
            self.assertTrue(legacy.is_dir())
            r = self.drive()
            self.assertEqual(r.returncode, 1)
            for pid in (other.pid, dead.pid, "garbage"):
                (legacy / "pid").write_text(f"{pid}\n")
                r = self.drive()
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("removing the lock of an earlier ./update.sh", r.stdout)
                self.assertFalse(legacy.exists())
                legacy.mkdir()
            (legacy / "pid").unlink(missing_ok=True)
            self.assertEqual(self.drive().returncode, 0)
            self.assertFalse(legacy.exists())
        finally:
            for proc in (update, other):
                proc.kill()
                proc.wait()

    def test_mesh_lock_reports_its_exit_codes(self):
        script = str(ROOT / "scripts/mesh_lock.py")
        self.lockfile.parent.mkdir(parents=True)
        code = ("import os, subprocess, sys\n"
                "fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_APPEND)\n"
                "os.dup2(fd, 9)\n"
                "if sys.argv[3] == 'swap':\n"
                "    os.unlink(sys.argv[1]); open(sys.argv[1], 'w').close()\n"
                "sys.exit(subprocess.run([sys.executable, sys.argv[2], sys.argv[4], '--fd', '9', '--path', sys.argv[1]],"
                " pass_fds=(9,)).returncode)\n")
        run = lambda mode, cmd: subprocess.run([sys.executable, "-c", code, str(self.lockfile), script, mode, cmd],
                                               env=self.lenv, capture_output=True, text=True)
        self.assertEqual(run("same", "acquire").returncode, 0)
        self.assertEqual(run("same", "inherit").returncode, 0)
        self.assertEqual(run("swap", "acquire").returncode, 5)
        self.assertEqual(run("swap", "inherit").returncode, 3)
        holder = self.hold()
        self.assertEqual(run("same", "acquire").returncode, 3)
        self.assertEqual(run("same", "inherit").returncode, 3)
        self.release(holder)


class EngineDaemon(LockHome, unittest.TestCase):
    def setUp(self):
        self.make_home()
        self.ctl = self.base / "rnctl"
        self.ctl.mkdir()
        self.rn = self.base / "bin/reason-network"
        self.rn.parent.mkdir()
        self.rn.write_text(fake_engine("0.12.17"))
        self.rn.chmod(0o755)
        self.lenv.update(FAKE_RN_CTL=str(self.ctl), REASON_NETWORK_HOME=str(self.lhome / ".reason/network"),
                         RN_TEST_BIN=str(self.rn))
        self.addCleanup(self.stop_daemons)

    def stop_daemons(self):
        path = self.ctl / "daemons"
        for line in path.read_text().splitlines() if path.exists() else []:
            try:
                os.kill(int(line.split()[0]), 9)
            except OSError:
                pass

    def daemons(self):
        return [line.split()[2] for line in (self.ctl / "daemons").read_text().splitlines()]

    def test_an_engine_started_with_the_lock_closed_does_not_keep_it(self):
        r = self.drive("/bin/bash", "-c", '"$RN_TEST_BIN" team 9>&-')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.daemons(), ["-"])
        self.assertEqual(self.drive().returncode, 0)

    def test_the_fake_engine_shows_a_daemon_that_kept_the_lock(self):
        r = self.drive("/bin/bash", "-c", '"$RN_TEST_BIN" team')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.daemons(), ["fd9"])
        r = self.drive()
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("another management command", r.stderr)


class Entrypoints(InstallHarness, LockHome, unittest.TestCase):
    def setUp(self):
        InstallHarness.setUp(self)

    def hold_home(self):
        self.lockfile = self.home / ".local/lib/agent-mesh.lock"
        return self.hold()

    def test_every_writing_entry_waits_for_the_lock_and_preflight_does_not(self):
        repo = self.installed()
        before = self.record()
        holder = self.hold_home()
        for script, args in (("install.sh", ("--restart-relay",)), ("install.sh", ("--purge-copies",)),
                             ("install.sh", ("--grok-name", "Main")), ("install.sh", ("--rotate-key", "--yes")),
                             ("update.sh", ("--no-pull", "--yes")), ("uninstall.sh", ("--level", "program", "--yes"))):
            with self.subTest(script=script, args=args):
                result = self.run_script(script, *args, root=repo, timeout=60)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("another management command", result.stderr)
                self.assertEqual(self.service_changes(), [])
                self.assertEqual(self.record(), before)
        result = self.run_script("install.sh", "--preflight", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pid 4242", (self.home / ".local/lib/agent-mesh.lock").read_text())
        self.release(holder)
        result = self.run_script("install.sh", "--restart-relay", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_a_forged_lock_variable_does_not_reach_the_relay(self):
        repo = self.installed()
        other = self.base / "other.lock"
        result = subprocess.run(["/bin/bash", "-c", f'exec 9>>"{other}"; AGENT_MESH_LOCK_FD=9 exec /bin/bash "$0" '
                                 '--restart-relay', str(repo / "install.sh")], env=self.env, capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("does not hold the management lock", result.stderr)
        self.assertEqual(self.service_changes(), [])

    def test_a_damaged_record_stops_every_writing_entry(self):
        repo = self.installed()
        path = self.home / ".local/lib/agent-mesh/deployed.json"
        path.write_text("{damaged")
        for args in (("--restart-relay",), ("--purge-copies",), ("--grok-name", "Main"), ("--webhook",)):
            with self.subTest(args=args):
                result = self.run_script("install.sh", *args, root=repo)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("deployed.json", result.stderr)
                self.assertEqual(self.service_changes(), [])
                self.assertEqual(path.read_text(), "{damaged")
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.service_changes(), [])
        self.assertEqual(path.read_text(), "{damaged")


if __name__ == "__main__":
    unittest.main()
