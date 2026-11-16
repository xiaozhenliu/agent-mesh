"""rn-cloud-uninstall.sh level bot, the new paths at identity level, and the runtime paths git and RUNTIME ignore."""
import ast, os, re, shlex, shutil, signal, subprocess, sys, tempfile, time, unittest
from pathlib import Path

from tests.cli.test_uninstall import Base, FAKE_RN, GIT, KEY, ack
from tests.cloud.support import HERE

PLACEHOLDERS = ('TEAM="<team-name>"; MAC="<mac-machine>"; RELAY_NAME="<relay-name>"; LEVEL="<level>"; '
                'MODE="<mode>"; CHALLENGE="<challenge>"')
CHALLENGE = "0123456789abcdef"


class CloudBots(Base, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.w = self.base / "work space"
        self.a = self.w / "agent-mesh"
        self.b = self.a / "box"
        self.b.mkdir(parents=True)
        for name in ("rn-cloud.sh", "rn-cloud-setup.sh"):
            shutil.copy(HERE / name, self.b / name)
        (self.b / "person").write_text("demo\n")
        (self.b / "relay").write_text("alpha\n")
        (self.b / "outbound.key").write_text(KEY + "\n")
        (self.b / "state").mkdir()
        (self.b / "state/machine.key").write_text("m")
        self.tool("reason-network", FAKE_RN, self.b / "bin/reason-network", shell="")
        (self.a / "inbox/01A").mkdir(parents=True)
        (self.a / "inbox/01A/full.txt").write_text("body")
        (self.a / "threads").mkdir()
        (self.a / "threads/t.md").write_text("note")
        self.beta = self.claim("beta")
        self.git = self.base / "git"
        self.git.mkdir()
        self.env.update(MOCK_RN=str(self.base / "rn"), MOCK_GIT=str(self.git))
        self.tool("uname", 'if [ "$1" = -s ]; then echo Linux; else echo x86_64; fi')
        self.tool("git", GIT)
        self.rn_state(self.base / "rn", **{"in": True, "team": "demo", "me": ["demo", "cloud"],
                                           "rows": [["demo", "mac", "direct"]], "fail": {}})
        self.procs = []
        self.addCleanup(self.stop_procs)

    def stop_procs(self):
        for p in self.procs:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()

    def claim(self, name):
        d = self.a / "bots" / name
        (d / "inbox/01B").mkdir(parents=True)
        (d / "inbox/01B/full.txt").write_text("body")
        (d / "threads").mkdir()
        (d / ".claim").write_text("team " + "a" * 16 + "\n")
        (d / ".registered").write_text("x\n")
        return d

    def script(self, relay, level="bot", mode="normal"):
        text = (HERE / "rn-cloud-uninstall.sh").read_text()
        self.assertEqual(text.count(PLACEHOLDERS), 1)
        text = text.replace(PLACEHOLDERS, f'TEAM="demo"; MAC="mac"; RELAY_NAME="{relay}"; LEVEL="{level}"; '
                                          f'MODE="{mode}"; CHALLENGE="{CHALLENGE}"')
        text = text.replace("\nW=/workspace\n", f"\nW={shlex.quote(str(self.w))}\n")
        fd, name = tempfile.mkstemp(suffix=".sh", dir=self.base)
        os.close(fd)
        Path(name).write_text(text)
        return Path(name)

    def run_cloud(self, script, *args, env=None):
        r = subprocess.run(["/bin/bash", str(script), *args], env=dict(self.env, **(env or {})), capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.stderr, "", r.stderr)
        return r

    def apply(self, script, *extra, env=None):
        return self.run_cloud(script, "apply", ack(KEY, CHALLENGE), *extra, env=env)

    def tree(self):
        return sorted(str(p.relative_to(self.a)) for p in self.a.rglob("*") if p.name != ".maint.lock")

    def rn_calls(self):
        return [c for c in self.calls().splitlines() if c.startswith("rn ")]

    def assert_identity_kept(self):
        for name in ("state/machine.key", "bin/reason-network", "person", "outbound.key"):
            self.assertTrue((self.b / name).exists(), name)
        self.assertNotIn("rn leave", " ".join(self.rn_calls()))
        self.assertNotIn("rn stop", " ".join(self.rn_calls()))

    def test_level_bot_removes_the_first_bot_and_keeps_everything_else(self):
        s = self.script("alpha")
        r = self.run_cloud(s, "check")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("STATE: alpha is the first Bot on this cloud", r.stdout)
        self.assertIn("WILL WRITE: box/bots-removed/alpha", r.stdout)
        self.assertIn("box/relay becomes box/relay.removed", r.stdout)
        self.assertIn("WILL KEEP: box/state box/bin box/person box/outbound.key", r.stdout)
        self.assertIn(f"PROOF: {ack(KEY, CHALLENGE, 'proof')}", r.stdout)
        self.assertNotIn("SHARED", r.stdout)
        self.assertNotIn("WILL REMOVE: box/state", r.stdout)
        self.rn_state(self.base / "rn", rows=[["demo", "mac", "offline"]])
        r = self.apply(s)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("DONE: level bot; demo/alpha is removed from this cloud and cannot be added again", r.stdout)
        self.assertFalse((self.a / "inbox").exists())
        self.assertFalse((self.a / "threads").exists())
        self.assertFalse((self.b / "relay").exists())
        self.assertEqual((self.b / "relay.removed").read_text(), "alpha\n")
        self.assertTrue((self.b / "bots-removed/alpha").is_file())
        self.assertEqual((self.b / "bots-removed").stat().st_mode & 0o777, 0o700)
        self.assertTrue((self.beta / "inbox/01B/full.txt").exists())
        self.assert_identity_kept()
        r = self.apply(s)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("STATE: alpha was already removed from this cloud", r.stdout)

    def test_level_bot_removes_the_default_first_bot_named_bot(self):
        (self.b / "relay").unlink()
        r = self.apply(self.script("bot"))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual((self.b / "relay.removed").read_text(), "bot\n")
        self.assertFalse((self.a / "inbox").exists())

    def test_level_bot_removes_an_added_bot(self):
        s = self.script("beta")
        r = self.run_cloud(s, "check")
        self.assertIn("STATE: beta is a Bot added with rn-cloud-add-bot.sh", r.stdout)
        self.assertIn("WILL REMOVE: bots/beta/", r.stdout)
        r = self.apply(s)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse(self.beta.exists())
        self.assertTrue((self.b / "bots-removed/beta").is_file())
        self.assertEqual((self.b / "relay").read_text(), "alpha\n")
        self.assertTrue((self.a / "inbox/01A/full.txt").exists())
        self.assertFalse((self.b / "relay.removed").exists())
        self.assert_identity_kept()

    def test_level_bot_of_a_never_claimed_name_only_writes_the_tombstone(self):
        s = self.script("gamma")
        before = self.tree()
        r = self.run_cloud(s, "check")
        self.assertIn("STATE: this cloud never claimed gamma (no bots/gamma)", r.stdout)
        self.assertEqual(self.tree(), before)
        r = self.apply(s)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(sorted(set(self.tree()) - set(before)), ["box/bots-removed", "box/bots-removed/gamma"])

    def test_level_bot_resumes_after_an_interruption(self):
        (self.b / "bots-removed").mkdir()
        (self.b / "bots-removed/beta").write_text("t\n")
        r = self.apply(self.script("beta"))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("already removed", r.stdout)
        self.assertFalse(self.beta.exists())
        (self.b / "bots-removed/alpha").write_text("t\n")
        (self.b / "relay").rename(self.b / "relay.removed")
        r = self.apply(self.script("alpha"))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertNotIn("already removed", r.stdout)
        self.assertFalse((self.a / "inbox").exists())
        self.assertEqual((self.b / "relay.removed").read_text(), "alpha\n")

    def test_level_bot_stops_without_changing_anything(self):
        def foreign_dir():
            shutil.rmtree(self.beta)
            (self.a / "bots/beta").mkdir()

        def both():
            self.claim("alpha")

        def no_person():
            for name in ("person", "outbound.key", "relay"):
                (self.b / name).unlink()
            shutil.rmtree(self.b / "state")
            shutil.rmtree(self.b / "bin")

        def other_team():
            self.rn_state(self.base / "rn", team="other")

        def symlinked():
            target = self.base / "elsewhere"
            target.mkdir()
            (self.b / "bots-removed").symlink_to(target)

        cases = [
            ("dir without claim", "beta", "normal", foreign_dir, "bots/beta was not created by rn-cloud-add-bot.sh"),
            ("both names", "alpha", "normal", both, "both box/relay and bots/alpha name alpha"),
            ("residual", "beta", "residual", None, "level bot needs mode normal"),
            ("terminal", "terminal-1", "normal", None, "invalid Bot address name terminal-1"),
            ("long", "b" * 35, "normal", None, "invalid Bot address name"),
            ("no person", "beta", "normal", no_person, "box/person is missing; there is no deployment here"),
            ("other team", "beta", "normal", other_team, "this cloud is in the team other, not demo"),
            ("symlink", "beta", "normal", symlinked, "is a symlink; not touching it"),
        ]
        for label, relay, mode, prepare, why in cases:
            with self.subTest(label):
                self.setUp()
                if prepare:
                    prepare()
                before = self.tree()
                r = self.apply(self.script(relay, mode=mode))
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn(why, r.stdout)
                self.assertEqual(self.tree(), before)
        self.setUp()
        before = self.tree()
        r = self.run_cloud(self.script("beta"), "apply", "0" * 16)
        self.assertIn("the code does not match", r.stdout)
        self.assertEqual(self.tree(), before)

    def test_identity_removes_the_added_bots_and_their_marks(self):
        (self.b / "relay").unlink()
        (self.b / "relay.removed").write_text("alpha\n")
        (self.b / "bots-removed").mkdir()
        (self.b / "bots-removed/alpha").write_text("t\n")
        (self.a / ".maint.lock").write_text("")
        s = self.script("beta", level="identity")
        r = self.run_cloud(s, "check")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("STATE: box/relay is missing; Bots added with rn-cloud-add-bot.sh: beta", r.stdout)
        self.assertIn("WILL REMOVE: bots/ box/bots-removed/ box/relay.removed .maint.lock", r.stdout)
        r = self.apply(s)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("DONE: level identity", r.stdout)
        for name in ("bots", "box/bots-removed", "box/relay.removed", ".maint.lock", "box/person", "box/state"):
            self.assertFalse((self.a / name).exists(), name)
        self.assertIn("rn leave", self.calls())

    def test_identity_accepts_a_removed_name_and_refuses_a_foreign_one(self):
        (self.b / "relay").unlink()
        r = self.run_cloud(self.script("gamma", level="identity"), "check")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("this cloud has no Bot named gamma; it belongs to another deployment", r.stdout)
        (self.b / "relay.removed").write_text("gamma\n")
        r = self.run_cloud(self.script("gamma", level="identity"), "check")
        self.assertEqual(r.returncode, 0, r.stdout)
        (self.b / "relay.removed").unlink()
        (self.b / "bots-removed").mkdir()
        (self.b / "bots-removed/gamma").write_text("t\n")
        r = self.run_cloud(self.script("gamma", level="identity"), "check")
        self.assertEqual(r.returncode, 0, r.stdout)
        shutil.rmtree(self.beta)
        (self.b / "relay.removed").write_text("alpha\n")
        r = self.run_cloud(self.script("delta", level="identity"), "check")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("STATE: the first Bot was removed from this cloud (box/relay.removed)", r.stdout)

    def test_uninstall_waits_for_running_cloud_commands(self):
        ready = self.base / "ready"
        p = subprocess.Popen([sys.executable, str(HERE / "rn-engine.py"), "hold", "--shared", str(self.a / ".maint.lock"),
                              "--", sys.executable, "-c",
                              f"import pathlib, time; pathlib.Path({str(ready)!r}).touch(); time.sleep(30)"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        self.procs.append(p)
        end = time.monotonic() + 20
        while not ready.exists() and time.monotonic() < end:
            time.sleep(0.05)
        before = self.tree()
        for s in (self.script("beta"), self.script("alpha", level="identity")):
            r = self.apply(s, env={"RN_MAINT_WAIT": "0.3"})
            self.assertEqual((r.returncode, r.stdout.strip()),
                             (1, "STOP: other cloud commands are still running; nothing was changed"))
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.rn_calls(), [])


@unittest.skipUnless(shutil.which("git"), "needs git")
class RuntimePaths(unittest.TestCase):
    RUNTIME_FILES = ("box/relay.removed", "box/bots-removed/beta", ".maint.lock", "bots/beta/.claim",
                     "bots/beta/.registered", "bots/beta/inbox/01A/full.txt", "bots/beta/threads/t.md",
                     "box/relay", "box/person", "inbox/01A/full.txt")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "agent-mesh"
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(HOME=self.temp.name, GIT_CONFIG_NOSYSTEM="1", LC_ALL="C")
        self.repo.mkdir()
        shutil.copy(HERE.parent / ".gitignore", self.repo / ".gitignore")
        self.git("init", "-q")
        self.git("add", ".gitignore")
        self.git("-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q", "-m", "init")
        source = (HERE / "rn-cloud-uninstall.sh").read_text()
        block = re.search(r'RUNTIME = re\.compile\((.*?)\)\n', source, re.S).group(1)
        self.runtime = re.compile("".join(ast.literal_eval(part) for part in re.findall(r'r"[^"]*"', block)))

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], env=self.env, capture_output=True, text=True,
                              check=True, timeout=60).stdout

    def put(self, rel):
        path = self.repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")

    def test_runtime_paths_are_ignored_by_git_and_by_the_uninstaller(self):
        for rel in self.RUNTIME_FILES:
            self.put(rel)
        self.assertEqual(self.git("status", "--porcelain"), "")
        ignored = self.git("ls-files", "--others", "--ignored", "--exclude-standard").split()
        self.assertEqual(sorted(ignored), sorted(self.RUNTIME_FILES))
        for rel in ignored:
            self.assertTrue(self.runtime.search(rel), rel)

    def test_a_bots_folder_elsewhere_is_user_content(self):
        for rel in ("notes/bots/todo.md", "box/bots/x", "box/relay.removed.bak"):
            self.put(rel)
            self.assertFalse(self.runtime.search(rel), rel)
        status = self.git("status", "--porcelain", "-uall")
        for rel in ("notes/bots/todo.md", "box/bots/x", "box/relay.removed.bak"):
            self.assertIn("?? " + rel, status)


if __name__ == "__main__":
    unittest.main()
