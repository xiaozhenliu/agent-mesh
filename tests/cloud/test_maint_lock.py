"""The cloud maintenance lock: rn-engine.py hold/held, and the update chain old script -> checkout -> new script."""
import os, shutil, signal, subprocess, sys, tempfile, time, unittest
from pathlib import Path

from tests.cloud.bot_support import BotBox, MAINT_STOP
from tests.cloud.support import HERE

ENGINE = str(HERE / "rn-engine.py")
ENGINE_STEP = 'bash "$A/box/rn-cloud-setup.sh" --upgrade-engine </dev/null'
PROBE = ('{ python3 -I "$A/box/rn-engine.py" held --exclusive "$A/.maint.lock" && echo "probe: held $RN_MAINT_MODE"'
         ' && if env -u RN_MAINT_FD -u RN_MAINT_MODE python3 -I "$A/box/rn-engine.py" hold --shared --wait 0'
         ' "$A/.maint.lock" -- true >/dev/null; then echo "probe: not exclusive"; else echo "probe: others wait";'
         ' fi; } </dev/null')
OLD_UPDATER = '''#!/usr/bin/env bash
set -uo pipefail
A="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TAG="$1" COMMIT="$2" REQ="$3"
echo "agent-mesh-result request $REQ"
cd "$A" || exit 1
git fetch -q --no-tags origin "refs/tags/$TAG:refs/tags/$TAG" </dev/null || { echo "STOP: fetch"; exit 1; }
git checkout -q --detach "$COMMIT" </dev/null || { echo "STOP: checkout"; exit 1; }
exec bash "$A/box/rn-cloud-update.sh" --after-checkout "$TAG" "$COMMIT" "$REQ"
'''
CHILD_ENV = ("import os, sys; "
             "print(os.environ.get('RN_MAINT_FD'), os.environ.get('RN_MAINT_MODE'), os.get_inheritable(9))")


class Hold(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        self.lock = self.d / ".maint.lock"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("RN_MAINT_")}
        self.procs = []
        self.addCleanup(self.stop_procs)

    def stop_procs(self):
        for p in self.procs:
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except OSError:
                    pass
                p.wait()

    def hold(self, mode, *cmd, wait=None, env=None, **kw):
        args = [sys.executable, ENGINE, "hold", f"--{mode}"] + (["--wait", str(wait)] if wait is not None else [])
        return subprocess.run(args + [str(self.lock), "--", *cmd], env=env or self.env, capture_output=True,
                              text=True, timeout=60, **kw)

    def start(self, mode, code, ready):
        p = subprocess.Popen([sys.executable, ENGINE, "hold", f"--{mode}", str(self.lock), "--", sys.executable,
                              "-c", code], env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        self.procs.append(p)
        end = time.monotonic() + 20
        while not ready.exists():
            if p.poll() is not None or time.monotonic() > end:
                raise AssertionError("the holder did not start")
            time.sleep(0.05)
        return p

    def sleeper(self, mode, seconds=30):
        ready = self.d / f"ready-{len(self.procs)}"
        return self.start(mode, f"import pathlib, time; pathlib.Path({str(ready)!r}).touch(); time.sleep({seconds})",
                          ready)

    def test_the_child_gets_fd_9_the_mode_stdin_and_its_exit_code(self):
        r = self.hold("shared", sys.executable, "-c", CHILD_ENV)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "9 shared True"), r.stderr)
        r = self.hold("exclusive", sys.executable, "-c", "import sys; sys.exit(7)")
        self.assertEqual(r.returncode, 7)
        r = self.hold("shared", "cat", input="line one\nline two\n")
        self.assertEqual(r.stdout, "line one\nline two\n")
        r = self.hold("exclusive", sys.executable, ENGINE, "held", "--exclusive", str(self.lock))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.lock.stat().st_mode & 0o777, 0o600)

    def test_usage_errors_and_missing_commands(self):
        for args in (["--shared"], ["--shared", str(self.lock)], ["--wait", "1", str(self.lock), "--", "true"],
                     ["--shared", "--exclusive", str(self.lock), "--", "true"], ["--shared", str(self.lock), "--"],
                     ["--shared", "--wait", "-1", str(self.lock), "--", "true"]):
            with self.subTest(args=args):
                r = subprocess.run([sys.executable, ENGINE, "hold", *args], env=self.env, capture_output=True,
                                   text=True, timeout=60)
                self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        r = self.hold("shared", str(self.d / "missing"))
        self.assertEqual(r.returncode, 127)
        self.assertIn("cannot run", r.stderr)
        r = subprocess.run([sys.executable, ENGINE, "held", "--shared", str(self.lock)], env=self.env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertIn("RN_MAINT_FD is not 9", r.stderr)

    def test_exclusive_waits_for_shared_and_times_out_without_running_anything(self):
        self.sleeper("shared")
        marker = self.d / "ran"
        start = time.monotonic()
        r = self.hold("exclusive", "touch", str(marker), wait=0.5)
        self.assertGreaterEqual(time.monotonic() - start, 0.4)
        self.assertEqual((r.returncode, r.stdout.strip()), (1, MAINT_STOP))
        self.assertFalse(marker.exists())
        r = self.hold("shared", "touch", str(marker), wait=0)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_a_waiting_command_runs_once_the_holder_is_done(self):
        self.sleeper("exclusive", seconds=1)
        start = time.monotonic()
        r = self.hold("shared", "true", wait=20)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertGreaterEqual(time.monotonic() - start, 0.5)

    def test_a_shared_holder_never_satisfies_an_exclusive_command(self):
        r = self.hold("shared", sys.executable, ENGINE, "hold", "--exclusive", "--wait", "0", str(self.lock), "--",
                      "true")
        self.assertEqual(r.returncode, 1)
        self.assertIn("needs the exclusive maintenance lock, but it runs inside a shared one", r.stderr)
        r = self.hold("shared", sys.executable, ENGINE, "held", "--exclusive", str(self.lock))
        self.assertEqual(r.returncode, 1)

    def test_shared_inside_exclusive_stays_exclusive(self):
        r = self.hold("exclusive", sys.executable, ENGINE, "hold", "--shared", str(self.lock), "--", sys.executable,
                      "-c", CHILD_ENV)
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "9 exclusive True"), r.stderr)
        r = self.hold("exclusive", sys.executable, ENGINE, "hold", "--shared", str(self.lock), "--", sys.executable,
                      ENGINE, "held", "--exclusive", str(self.lock))
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.hold("exclusive", sys.executable, ENGINE, "hold", "--shared", str(self.lock), "--", "env", "-u",
                      "RN_MAINT_FD", "-u", "RN_MAINT_MODE", sys.executable, ENGINE, "hold", "--shared", "--wait", "0",
                      str(self.lock), "--", "true")
        self.assertEqual((r.returncode, r.stdout.strip()), (1, MAINT_STOP))

    def test_a_forged_or_replaced_lock_is_not_held(self):
        r = subprocess.run([sys.executable, ENGINE, "held", "--shared", str(self.lock)],
                           env=dict(self.env, RN_MAINT_FD="9", RN_MAINT_MODE="shared"), capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 1)
        swap = (f"import os, subprocess, sys; os.replace({str(self.lock)!r}, {str(self.lock)!r} + '.old'); "
                f"open({str(self.lock)!r}, 'w').close(); "
                f"sys.exit(subprocess.run([sys.executable, {ENGINE!r}, 'held', '--exclusive', {str(self.lock)!r}],"
                f" pass_fds=(9,)).returncode)")
        r = self.hold("exclusive", sys.executable, "-c", swap)
        self.assertEqual(r.returncode, 1)
        self.assertIn("fd 9 is not", r.stderr)
        r = self.hold("shared", sys.executable, ENGINE, "held", "--shared", str(self.lock),
                      env=dict(self.env, RN_MAINT_FD="9", RN_MAINT_MODE="bogus"))
        self.assertEqual(r.returncode, 1)
        self.assertIn("neither shared nor exclusive", r.stderr)

    def test_the_lock_stays_with_the_child_when_hold_is_killed(self):
        ready, pidfile = self.d / "ready", self.d / "child.pid"
        p = self.start("exclusive", f"import os, pathlib, time; pathlib.Path({str(pidfile)!r}).write_text("
                                    f"str(os.getpid())); pathlib.Path({str(ready)!r}).touch(); time.sleep(30)", ready)
        os.kill(p.pid, signal.SIGKILL)
        p.wait()
        child = int(pidfile.read_text())
        try:
            r = self.hold("shared", "true", wait=0.3)
            self.assertEqual((r.returncode, r.stdout.strip()), (1, MAINT_STOP))
        finally:
            os.kill(child, signal.SIGKILL)
        end = time.monotonic() + 10
        while True:
            r = self.hold("exclusive", "true", wait=0.2)
            if r.returncode == 0 or time.monotonic() > end:
                break
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_signals_reach_the_child_and_its_exit_code_comes_back(self):
        ready = self.d / "ready"
        code = (f"import pathlib, signal, sys, time; signal.signal(signal.SIGTERM, lambda *a: sys.exit(5)); "
                f"pathlib.Path({str(ready)!r}).touch(); time.sleep(30)")
        p = self.start("shared", code, ready)
        os.kill(p.pid, signal.SIGTERM)
        self.assertEqual(p.wait(timeout=20), 5)
        ready.unlink()
        code = (f"import pathlib, signal, time; signal.signal(signal.SIGINT, signal.SIG_DFL); "
                f"signal.signal(signal.SIGTERM, signal.SIG_DFL); pathlib.Path({str(ready)!r}).touch(); time.sleep(30)")
        p = self.start("shared", code, ready)
        os.kill(p.pid, signal.SIGINT)
        self.assertEqual(p.wait(timeout=20), 128 + signal.SIGINT)
        ready.unlink()
        p = self.start("shared", code, ready)
        os.kill(p.pid, signal.SIGTERM)
        self.assertEqual(p.wait(timeout=20), 128 + signal.SIGTERM)


class ScriptLocks(BotBox, unittest.TestCase):
    def setup_upgrade(self, env=None, wrap=()):
        return subprocess.run([*wrap, "bash", str(self.box / "rn-cloud-setup.sh"), "--upgrade-engine"],
                              env=dict(self.env, **(env or {})), capture_output=True, text=True, timeout=60)

    def test_the_engine_upgrade_waits_for_running_commands(self):
        self.holder("shared")
        p = self.setup_upgrade({"RN_MAINT_WAIT": "0.3"})
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(p.stdout.strip(), MAINT_STOP)
        self.assertEqual(self.calls(), [])

    def test_the_engine_upgrade_refuses_a_shared_lock_it_inherits(self):
        p = self.setup_upgrade(wrap=(sys.executable, ENGINE, "hold", "--shared", str(self.lock), "--"))
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("needs the exclusive maintenance lock", p.stderr)
        self.assertEqual(self.calls(), [])


@unittest.skipUnless(shutil.which("git") and shutil.which("sha256sum"), "needs git and sha256sum")
class UpdateChain(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(os.path.realpath(self.temp.name))
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "RN_", "REASON_NETWORK_"))}
        self.env.update(HOME=str(self.base), GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x", LC_ALL="C")
        self.src = self.base / "src"
        self.bare = self.base / "remote.git"
        self.clone = self.base / "workspace" / "agent-mesh"
        self.procs = []
        self.addCleanup(self.stop_procs)

    def stop_procs(self):
        for p in self.procs:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()

    def git(self, *args):
        return subprocess.run(["git", *args], env=self.env, capture_output=True, text=True, check=True,
                              timeout=60).stdout.strip()

    def build(self, gitignore):
        self.git("init", "-q", "-b", "main", str(self.src))
        box = self.src / "box"
        box.mkdir()
        for name in ("rn-cloud.sh", "rn-fetch.py", "rn-cloud-setup.sh", "rn-engine.py", "rn-cloud-add-bot.sh"):
            (box / name).write_bytes((HERE / name).read_bytes())
        (box / "rn-cloud-update.sh").write_text(OLD_UPDATER)
        (self.src / "VERSION").write_text("0.0.1\n")
        self.git("-C", str(self.src), "add", "-A")
        self.git("-C", str(self.src), "commit", "-q", "-m", "old")
        self.old = self.git("-C", str(self.src), "rev-parse", "HEAD")
        text = (HERE / "rn-cloud-update.sh").read_text()
        self.assertEqual(text.count(ENGINE_STEP), 1)
        (box / "rn-cloud-update.sh").write_text(text.replace(ENGINE_STEP, PROBE))
        (self.src / "VERSION").write_text("0.0.2\n")
        if gitignore:
            (self.src / ".gitignore").write_bytes((HERE.parent / ".gitignore").read_bytes())
        self.git("-C", str(self.src), "add", "-A")
        self.git("-C", str(self.src), "commit", "-q", "-m", "release")
        self.git("-C", str(self.src), "tag", "-a", "-m", "release", "v0.0.2")
        self.commit = self.git("-C", str(self.src), "rev-parse", "HEAD")
        self.git("clone", "-q", "--bare", str(self.src), str(self.bare))
        self.git("clone", "-q", self.bare.as_uri(), str(self.clone))
        self.git("-C", str(self.clone), "checkout", "-q", "--detach", self.old)

    def update(self, **env):
        return subprocess.run(["bash", str(self.clone / "box/rn-cloud-update.sh"), "v0.0.2", self.commit,
                               "0123456789abcdef"], cwd=self.base, env=dict(self.env, **env), capture_output=True,
                              text=True, timeout=120)

    def assert_locked_run(self, r):
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines.count("agent-mesh-result request 0123456789abcdef"), 1, r.stdout)
        self.assertIn("probe: held exclusive", lines)
        self.assertIn("probe: others wait", lines)
        self.assertNotIn("probe: not exclusive", lines)
        self.assertEqual(lines[-1], "agent-mesh-result DONE v0.0.2")
        self.assertEqual(self.git("-C", str(self.clone), "rev-parse", "HEAD"), self.commit)
        self.assertEqual(self.git("-C", str(self.clone), "status", "--porcelain"), "")

    def exclude(self):
        path = self.clone / ".git/info/exclude"
        return path.read_text() if path.exists() else ""

    def test_old_script_then_new_script_after_the_checkout_holds_the_lock(self):
        self.build(gitignore=False)
        self.assert_locked_run(self.update())
        self.assertIn("/.maint.lock\n", self.exclude())
        self.assert_locked_run(self.update())
        self.assertEqual(self.exclude().count("/.maint.lock\n"), 1)

    def test_a_checkout_that_ignores_the_lock_file_needs_no_exclude_line(self):
        self.build(gitignore=True)
        self.git("-C", str(self.clone), "checkout", "-q", "--detach", self.commit)
        self.assert_locked_run(self.update())
        self.assertNotIn("/.maint.lock", self.exclude())

    def test_the_update_waits_for_running_commands(self):
        self.build(gitignore=True)
        self.git("-C", str(self.clone), "checkout", "-q", "--detach", self.commit)
        ready = self.base / "ready"
        lock = self.clone / ".maint.lock"
        p = subprocess.Popen([sys.executable, str(self.clone / "box/rn-engine.py"), "hold", "--shared", str(lock),
                              "--", sys.executable, "-c",
                              f"import pathlib, time; pathlib.Path({str(ready)!r}).touch(); time.sleep(30)"],
                             env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        self.procs.append(p)
        end = time.monotonic() + 20
        while not ready.exists() and time.monotonic() < end:
            time.sleep(0.05)
        r = self.update(RN_MAINT_WAIT="0.3")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "agent-mesh-result request 0123456789abcdef\n" + MAINT_STOP)
        self.assertNotIn("probe:", r.stdout)


if __name__ == "__main__":
    unittest.main()
