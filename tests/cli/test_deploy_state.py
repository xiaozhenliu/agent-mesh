"""deploy_state.py record handling: strict writes, old transaction and request formats, the first Bot's name."""
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from render_prompt import ROOT
from tests.cli.install_support import BASE_PYTHON, fake_python, run_locked

ENV = "RN_BIN={home}/.local/bin/reason-network\nPERSON=demo\nOUTBOUND_KEY_FILE={home}/.config/agent-relay/outbound.key\n"


class DeployState(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name) / "home"
        self.home.mkdir()
        self.path = self.home / ".local/lib/agent-mesh/deployed.json"

    def state(self, *args, stdin=""):
        env = dict(os.environ, HOME=str(self.home), AGENT_MESH_LANG="en")
        return subprocess.run([sys.executable, str(ROOT / "scripts/deploy_state.py"), *args], env=env, input=stdin,
                              capture_output=True, text=True)

    def write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(data if isinstance(data, str) else json.dumps(data))

    def legacy_env(self):
        conf = self.home / ".config/agent-relay"
        conf.mkdir(parents=True, exist_ok=True)
        (conf / "env").write_text(ENV.format(home=self.home))

    def added_env(self, name):
        conf = self.home / ".config/agent-relay-bots" / name
        conf.mkdir(parents=True, exist_ok=True)
        (conf / "env").write_text(ENV.format(home=self.home)
                                  + f"LISTEN_NAME={name}\nCLOUD_NAME={name}-cloud\nINBOX_NAME={name}-inbox\n")

    def txn(self, **extra):
        value = {"stage": "relay_stopped", "need": ["prompt"], "lang": "en", "commit": "a" * 40, "target": {}}
        value.update(extra)
        return value

    def test_every_writer_stops_on_an_unreadable_record_and_leaves_it(self):
        writers = (("mark", "cloud", "pending"), ("set-grok-name", "Main"), ("txn-start", "--lang", "en", "--need",
                   "prompt", "--was", "1", "--commit", "a" * 40), ("txn-set", "stage", "relay_stopped"),
                   ("txn-clear",), ("set-engine", "0.12.17"),
                   ("request-new", "--tag", "v1.0.0", "--commit", "a" * 40, "--mode", "line"),
                   ("request-check", "--tag", "v1.0.0", "--commit", "a" * 40))
        for text in ("{damaged", json.dumps({"schema": 9, "txn": self.txn(relay_was_loaded=True)})):
            for args in writers:
                with self.subTest(text=text, args=args[0]):
                    self.write(text)
                    r = self.state(*args)
                    self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
                    self.assertIn("deployed.json", r.stdout + r.stderr)
                    self.assertEqual(self.path.read_text(), text)
                    self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["deployed.json"])

    def test_status_stays_lenient_and_reports_the_damage(self):
        self.write("{damaged")
        r = self.state("status", "--lang", "en", "--python", sys.executable)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("unreadable", r.stderr)
        self.assertIn("cloud unknown", r.stdout)

    def test_an_old_transaction_is_one_participant_the_first_bot(self):
        self.legacy_env()
        for was, want in ((True, "1"), (False, "0")):
            self.write({"schema": 1, "relay_name": "bot", "txn": self.txn(relay_was_loaded=was)})
            self.assertEqual(self.state("txn-get", "relay_was_loaded").stdout, want + "\n")
            self.assertEqual(self.state("txn-get", "stage").stdout, "relay_stopped\n")
        os.environ["HOME"], old = str(self.home), os.environ["HOME"]
        try:
            import deploy_state
            txn = self.txn(relay_was_loaded=True)
            self.assertTrue(deploy_state.valid_txn(txn))
            self.assertEqual(deploy_state.txn_participants(txn, {"relay_name": "bot"}), [
                {"name": "bot", "label": "com.local.agent-relay",
                 "plist": f"{self.home}/Library/LaunchAgents/com.local.agent-relay.plist", "was_loaded": True}])
            new = self.txn(participants=[
                {"name": "bot", "label": "com.local.agent-relay", "plist": "/p1", "was_loaded": False},
                {"name": "luna", "label": "com.local.agent-relay.luna", "plist": "/p2", "was_loaded": True}])
            self.assertTrue(deploy_state.valid_txn(new))
            self.assertEqual([p["name"] for p in deploy_state.txn_participants(new)], ["bot", "luna"])
            self.assertFalse(deploy_state.valid_txn(self.txn(participants=[{"name": "bot"}])))
            self.assertFalse(deploy_state.valid_txn(self.txn()))
            data = {"bots": {"luna": {"stage": "relay"}, "bad": 3}, "removed_bots": ["old", 4],
                    "removing": {"name": "luna", "kind": "added", "step": "service"}, "legacy_removed": True}
            self.assertEqual(deploy_state.bot_entry(data, "luna"), {"stage": "relay"})
            self.assertIsNone(deploy_state.bot_entry(data, "bad"))
            self.assertEqual(deploy_state.removed_bots(data), ["old"])
            self.assertEqual(deploy_state.removing_of(data)["name"], "luna")
            self.assertTrue(deploy_state.legacy_removed(data))
            self.assertIsNone(deploy_state.removing_of({"removing": {"name": "x", "kind": "other"}}))
        finally:
            os.environ["HOME"] = old
        self.write({"schema": 1, "relay_name": "bot", "txn": self.txn(participants=[
            {"name": "bot", "label": "com.local.agent-relay", "plist": "/p1", "was_loaded": False},
            {"name": "luna", "label": "com.local.agent-relay.luna", "plist": "/p2", "was_loaded": True}])})
        self.assertEqual(self.state("txn-get", "relay_was_loaded").stdout, "1\n")

    def test_participants_of_the_update_and_of_the_engine_transaction_in_old_and_new_formats(self):
        self.legacy_env()
        self.added_env("luna")
        plist = f"{self.home}/Library/LaunchAgents/com.local.agent-relay.plist"
        self.write({"schema": 1, "relay_name": "bot", "bots": {"luna": {"stage": "relay", "added_ms": 1}}})
        members = [{"name": "bot", "label": "com.local.agent-relay", "plist": "/p 1", "was_loaded": True},
                   {"name": "luna", "label": "com.local.agent-relay.luna", "plist": "/p2", "was_loaded": False}]
        start = ("txn-start", "--lang", "en", "--need", "prompt", "--commit", "a" * 40)
        self.assertNotEqual(self.state(*start).returncode, 0)
        self.assertNotEqual(self.state(*start, "--participants", '[{"name": "bot"}]').returncode, 0)
        result = self.state(*start, "--participants", json.dumps(members))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.path.read_text())["txn"]["participants"], members)
        self.assertEqual(self.state("txn-participants").stdout,
                         "bot\tcom.local.agent-relay\t/p 1\t1\nluna\tcom.local.agent-relay.luna\t/p2\t0\n")
        self.assertEqual(self.state("txn-clear").returncode, 0)
        self.assertEqual(self.state(*start, "--was", "0").returncode, 0)
        self.assertEqual(self.state("txn-participants").stdout, f"bot\tcom.local.agent-relay\t{plist}\t0\n")
        etxn = self.home / ".local/lib/agent-mesh/engine-upgrade.json"
        base = {"stage": "replaced", "bin": "/b", "home": "/h", "backup": "/k"}
        for extra, want in (({"participants": json.dumps(members)}, "bot\tcom.local.agent-relay\t/p 1\t1\n"
                                                                     "luna\tcom.local.agent-relay.luna\t/p2\t0\n"),
                            ({"relay_was_loaded": "0"}, f"bot\tcom.local.agent-relay\t{plist}\t0\n"),
                            ({"relay_was_loaded": "1"}, f"bot\tcom.local.agent-relay\t{plist}\t1\n"),
                            ({}, f"bot\tcom.local.agent-relay\t{plist}\t1\n")):
            etxn.write_text(json.dumps(dict(base, extra=extra)))
            self.assertEqual(self.state("etxn-participants").stdout, want, extra)
        status = lambda: next(line for line in self.state("status", "--lang", "en", "--python", sys.executable)
                              .stdout.splitlines() if line.startswith("etxn "))
        self.assertEqual(status(), "etxn replaced")
        etxn.write_text(json.dumps(dict(base, phase="committed")))
        self.assertEqual(status(), "etxn committed")

    def test_saved_participants_are_limited_to_bots_that_still_run_a_relay(self):
        self.legacy_env()
        self.added_env("luna")
        members = [{"name": "bot", "label": "com.local.agent-relay", "plist": "/p1", "was_loaded": True},
                   {"name": "luna", "label": "com.local.agent-relay.luna", "plist": "/p2", "was_loaded": True}]
        etxn = self.home / ".local/lib/agent-mesh/engine-upgrade.json"
        etxn.parent.mkdir(parents=True, exist_ok=True)
        etxn.write_text(json.dumps({"stage": "replaced", "phase": "switched",
                                    "extra": {"participants": json.dumps(members)}}))
        both = "bot\tcom.local.agent-relay\t/p1\t1\nluna\tcom.local.agent-relay.luna\t/p2\t1\n"
        only_bot = "bot\tcom.local.agent-relay\t/p1\t1\n"
        only_luna = "luna\tcom.local.agent-relay.luna\t/p2\t1\n"
        luna = {"stage": "relay", "added_ms": 1}
        cases = (({"bots": {"luna": luna}}, both),
                 ({"bots": {"luna": luna}, "removing": {"name": "luna", "kind": "added", "step": "stopped"}},
                  only_bot),
                 ({"bots": {"luna": dict(luna, stage="configured")}}, only_bot),
                 ({"bots": {}, "removed_bots": ["luna"]}, only_bot),
                 ({"bots": {"luna": luna}, "legacy_removed": True}, only_luna),
                 ({"bots": {"luna": luna}, "removing": {"name": "bot", "kind": "legacy", "step": "routine"}},
                  only_luna))
        for extra, want in cases:
            with self.subTest(extra=extra):
                self.legacy_env()
                if extra.get("legacy_removed"):
                    (self.home / ".config/agent-relay/env").unlink()
                self.write(dict({"schema": 1, "relay_name": "bot", "txn": self.txn(participants=members)}, **extra))
                self.assertEqual(self.state("txn-participants").stdout, want)
                self.assertEqual(self.state("etxn-participants").stdout, want)

    def test_every_cloud_runtime_file_changes_a_recorded_cloud_digest(self):
        import deploy_state
        from render_prompt import CLOUD_FILES
        runtime = sorted([f"box/{p.name}" for p in (ROOT / "box").glob("rn-*") if p.name != "rn-cloud-uninstall.sh"]
                         + [f"prompts/{p.name}" for p in (ROOT / "prompts").glob("routine-body*.md")])
        self.assertEqual(sorted(set(CLOUD_FILES) | {deploy_state.UPDATE_SCRIPT}), runtime)
        repo = self.home.parent / "repo"
        shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns("__pycache__", ".git", "tests"))

        def digests():
            env = dict(os.environ, HOME=str(self.home), AGENT_MESH_LANG="en")
            out = []
            for field in deploy_state.CLOUD_PARTS:
                r = subprocess.run([sys.executable, str(repo / "scripts/deploy_state.py"), "digest", field], env=env,
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                out.append(r.stdout)
            return out

        before = digests()
        for name in runtime:
            with self.subTest(name):
                path = repo / name
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                try:
                    self.assertNotEqual(digests(), before)
                finally:
                    path.write_bytes(original)

    def test_the_address_of_an_added_bot(self):
        self.legacy_env()
        self.added_env("luna")
        self.write({"schema": 1, "relay_name": "bot", "bots": {"luna": {"stage": "relay", "added_ms": 1}}})
        self.assertEqual(self.state("bot-address").stdout, "demo/bot\n")
        self.assertEqual(self.state("bot-address", "--relay-name", "luna").stdout, "demo/luna\n")
        self.assertEqual(self.state("bot-address", "--relay-name", "bot").stdout, "demo/bot\n")

    def test_a_request_without_an_address_goes_to_the_first_bot(self):
        self.legacy_env()
        os.environ["HOME"], old = str(self.home), os.environ["HOME"]
        try:
            import deploy_state
            self.assertEqual(deploy_state.request_address({"req": "0" * 16}), "demo/bot")
            self.assertEqual(deploy_state.request_address({"address": "demo/luna"}), "demo/luna")
            self.assertEqual(deploy_state.request_address({"address": "demo/Bad Name"}), "demo/bot")
        finally:
            os.environ["HOME"] = old
        self.write({"schema": 1, "relay_name": "bot"})
        r = self.state("request-new", "--tag", "v1.0.0", "--commit", "a" * 40, "--mode", "line")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("address", json.loads(self.path.read_text())["request"])
        r = self.state("request-new", "--tag", "v1.0.0", "--commit", "a" * 40, "--mode", "line",
                       "--address", "demo/luna")
        self.assertEqual(json.loads(self.path.read_text())["request"]["address"], "demo/luna")

    def test_the_first_bot_name_does_not_fall_back_to_bot_once_it_is_gone(self):
        self.assertEqual(self.state("show-prompt").returncode, 0)
        self.added_env("luna")
        r = self.state("show-prompt")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no first Bot", r.stderr)
        r = self.state("show-prompt", "--relay-name", "luna")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("bots.luna.team_hash", r.stderr)
        r = self.state("status", "--lang", "en", "--python", sys.executable)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("name unknown -\n", r.stdout)
        self.write({"schema": 1, "relay_name": "bot", "legacy_removed": True, "prompt": {"hash": "0" * 64,
                                                                                         "state": "confirmed"}})
        r = self.state("status", "--lang", "en", "--python", sys.executable)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("prompt unknown\n", r.stdout)
        self.assertNotEqual(self.state("bot-address").returncode, 0)
        self.legacy_env()
        self.write({"schema": 1, "relay_name": "bot"})
        self.assertEqual(self.state("bot-address").stdout, "demo/bot\n")


    def roster(self):
        r = subprocess.run([sys.executable, str(ROOT / "scripts/bots.py"), "list"],
                           env=dict(os.environ, HOME=str(self.home), AGENT_MESH_LANG="en"), capture_output=True,
                           text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        return [line.split("\t")[:2] for line in r.stdout.splitlines()]

    def test_removing_marks_one_bot_and_done_drops_it_for_good(self):
        self.legacy_env()
        self.added_env("luna")
        self.write({"schema": 1, "relay_name": "alpha", "grok_name": "Main", "prompt": {"state": "confirmed"},
                    "bots": {"luna": {"stage": "relay", "added_ms": 1, "cloud": {"state": "confirmed"}}}})
        (self.home / ".config/agent-relay/env").write_text(ENV.format(home=self.home) + "LISTEN_NAME=alpha\n"
                                                           "CLOUD_NAME=alpha-cloud\nINBOX_NAME=alpha-inbox\n")
        r = self.state("removing-set", "--relay-name", "nova", "--kind", "added", "--step", "started")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nova is not a Bot on this Mac", r.stderr)
        r = self.state("removing-set", "--relay-name", "luna", "--kind", "added", "--step", "started")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = self.state("removing-set", "--relay-name", "alpha", "--kind", "legacy", "--step", "started")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("Bot luna is being uninstalled", r.stderr)
        self.assertEqual(self.state("removing-set", "--relay-name", "luna", "--kind", "added", "--step",
                                    "stopped").returncode, 0)
        self.assertEqual(json.loads(self.path.read_text())["removing"],
                         {"name": "luna", "kind": "added", "step": "stopped"})
        self.assertIn("removing stopped", self.state("status", "--lang", "en", "--python", sys.executable).stdout)
        self.assertEqual(self.state("removing-clear", "--relay-name", "luna").returncode, 0)
        self.assertNotIn("removing", json.loads(self.path.read_text()))
        self.assertNotEqual(self.state("removing-done", "--relay-name", "luna").returncode, 0)
        self.state("removing-set", "--relay-name", "luna", "--kind", "added", "--step", "cloud")
        self.assertEqual(self.state("removing-done", "--relay-name", "luna").returncode, 0)
        data = json.loads(self.path.read_text())
        self.assertEqual((data["bots"], data["removed_bots"], "removing" in data), ({}, ["luna"], False))
        self.assertEqual(self.roster(), [["alpha", "legacy"]])
        self.added_env("vega")
        data["bots"]["vega"] = {"stage": "relay", "added_ms": 2, "cloud": {"state": "confirmed"}}
        self.write(data)
        self.state("removing-set", "--relay-name", "alpha", "--kind", "legacy", "--step", "cloud")
        self.assertEqual(self.state("removing-done", "--relay-name", "alpha").returncode, 0)
        data = json.loads(self.path.read_text())
        self.assertEqual((data["legacy_removed"], data["removed_bots"]), (True, ["luna", "alpha"]))
        for key in ("relay_name", "grok_name", "prompt", "removing"):
            self.assertNotIn(key, data)
        self.assertEqual(self.roster(), [["alpha", "legacy"], ["vega", "added"]])
        (self.home / ".config/agent-relay/env").unlink()
        self.assertEqual(self.roster(), [["vega", "added"]])
        shutil.rmtree(self.home / ".config/agent-relay-bots/luna")
        self.path.unlink()
        self.assertEqual(self.roster(), [["vega", "added"]])
        r = self.state("status", "--lang", "en", "--python", sys.executable)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("name unknown -\n", r.stdout)
        self.assertNotEqual(self.state("bot-address").returncode, 0)

    def test_rename_records_move_forward_only_and_restore_from_their_backup(self):
        import time
        self.added_env("luna")
        env_path = self.home / ".config/agent-relay-bots/luna/env"
        now = int(time.time() * 1000)
        day = 86400 * 1000
        self.write({"schema": 1, "relay_name": "bot",
                    "bots": {"luna": {"stage": "relay", "added_ms": 1, "cloud": {"state": "confirmed"}}},
                    "former": [{"name": "old", "slot": "luna", "since_ms": 1, "until_ms": now + day},
                               {"name": "old", "slot": "luna", "since_ms": 1, "until_ms": now + 2 * day},
                               {"name": "gone", "slot": "luna", "since_ms": 1, "until_ms": now - day},
                               {"name": "else", "slot": "-", "since_ms": 1, "until_ms": now + day}]})
        env = dict(os.environ, HOME=str(self.home), AGENT_MESH_LANG="en")

        def locked(*args):
            return run_locked(env, sys.executable, ROOT / "scripts/deploy_state.py", *args)

        self.assertEqual(self.state("former-env", "--slot", "luna").stdout, f"old:{(now + 2 * day) // 1000}\n")
        self.assertEqual(self.state("former-env", "--slot", "-").stdout, f"else:{(now + day) // 1000}\n")
        self.assertIn("only runs inside", self.state("rename-start", "--slot", "luna", "--old", "luna", "--new",
                                                     "nova").stderr)
        r = locked("rename-start", "--slot", "luna", "--old", "moon", "--new", "nova")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.state("rename-get").returncode, 3)
        r = locked("rename-start", "--slot", "luna", "--old", "luna", "--new", "nova")
        self.assertEqual(r.returncode, 0, r.stderr)
        req = r.stdout.strip()
        self.assertRegex(req, r"^[0-9a-f]{16}$")
        self.assertNotEqual(locked("rename-start", "--slot", "luna", "--old", "luna", "--new", "nova").returncode, 0)
        self.assertIn("stage requested\nslot luna\nold luna\nnew nova\nreq " + req, self.state("rename-get").stdout)
        self.assertNotEqual(locked("rename-switch").returncode, 0)
        data = json.loads(self.path.read_text())
        data["rename"]["stage"] = "replied"
        self.write(data)
        before = env_path.read_bytes()
        self.assertEqual(locked("rename-switch").stdout, "switching\n")
        self.assertEqual(locked("rename-switch").stdout, "switching\n")
        self.assertEqual(self.state("former-env", "--slot", "luna").stdout.strip().split(",")[0].split(":")[0], "luna")
        env_path.write_text("damaged\n")
        r = locked("rename-restore")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(env_path.read_bytes(), before)
        self.assertEqual(env_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(locked("rename-launched").returncode, 0)
        self.assertNotEqual(locked("rename-restore").returncode, 0)
        self.assertNotEqual(locked("rename-clear").returncode, 0)
        self.assertNotEqual(locked("rename-commit").returncode, 0)
        env_path.write_text(before.decode().replace("=luna", "=nova"))
        self.assertEqual(locked("rename-commit").returncode, 0)
        self.assertEqual(locked("rename-commit").returncode, 0)
        r = locked("rename-finish")
        self.assertEqual((r.returncode, r.stdout), (0, ""), r.stderr)
        data = json.loads(self.path.read_text())
        self.assertNotIn("rename", data)
        self.assertEqual(data["bots"]["luna"]["name"], "nova")
        self.assertEqual(sorted((f["name"], f["slot"]) for f in data["former"]),
                         [("else", "-"), ("gone", "luna"), ("luna", "luna"), ("old", "luna"), ("old", "luna")])
        self.assertFalse((self.home / ".local/lib/agent-mesh/rename-backup").exists())
        lines = self.state("former").stdout.splitlines()
        self.assertIn("former luna gone expired", "\n".join(lines))
        self.assertTrue(all(line.endswith(" nova") for line in lines if " luna " in line[:13]))
        data["rename"] = {"slot": "luna"}
        self.write(data)
        self.assertEqual(self.state("rename-get").returncode, 1)
        self.assertIn("rename damaged", self.state("status", "--lang", "en", "--python", sys.executable).stdout)

    def test_removing_needs_the_lock_when_a_caller_passes_one(self):
        self.added_env("luna")
        self.write({"schema": 1, "bots": {"luna": {"stage": "relay", "added_ms": 1}}})
        lock = self.home / ".local/lib/agent-mesh.lock"
        other = self.home / "other.lock"
        for path in (lock, other):
            path.write_text("")
        env = dict(os.environ, HOME=str(self.home), AGENT_MESH_LANG="en", AGENT_MESH_LOCK_FD="9")
        cmd = [sys.executable, str(ROOT / "scripts/deploy_state.py"), "removing-set", "--relay-name", "luna",
               "--kind", "added", "--step", "started"]
        for path, code in ((other, 5), (lock, 0)):
            fd = os.open(path, os.O_RDWR)
            try:
                os.dup2(fd, 9)
                r = subprocess.run(cmd, env=env, capture_output=True, text=True, pass_fds=(9,))
            finally:
                os.close(9)
                os.close(fd)
            self.assertEqual(r.returncode, code, r.stderr)
        self.assertEqual(json.loads(self.path.read_text())["removing"]["name"], "luna")

    def test_add_reply_check_reads_the_reply_and_writes_nothing(self):
        import sqlite3
        self.legacy_env()
        self.added_env("luna")
        record = {"schema": 1, "bots": {"luna": {"stage": "relay", "added_ms": 1, "team_hash": "1" * 16,
                                                 "cloud": {"state": "pending", "req": "2" * 16, "sent_ms": 5}}}}
        self.write(record)
        before = self.path.read_bytes()
        self.assertEqual(self.state("add-reply", "--relay-name", "luna", "--check").returncode, 4)
        db_path = self.home / ".reason/network/network.db"
        db_path.parent.mkdir(parents=True)
        db = sqlite3.connect(db_path)
        db.execute("CREATE TABLE messages (id TEXT, sender TEXT, recipient TEXT, body TEXT, sent_at INTEGER)")
        db.commit()
        self.assertEqual(self.state("add-reply", "--relay-name", "luna", "--check").returncode, 3)
        db.execute("INSERT INTO messages VALUES ('a', 'demo/luna', 'demo/terminal', ?, 10)",
                   (f"agent-mesh-result add-bot {'2' * 16} ok luna {'0' * 16}",))
        db.commit()
        r = self.state("add-reply", "--relay-name", "luna", "--check")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("agent-mesh-result add-bot", r.stdout)
        db.execute("INSERT INTO messages VALUES ('b', 'demo/luna', 'demo/terminal', ?, 11)",
                   (f"agent-mesh-result add-bot {'2' * 16} ok luna {'1' * 16}",))
        db.commit()
        db.close()
        self.assertEqual(self.state("add-reply", "--relay-name", "luna", "--check").returncode, 0)
        self.assertEqual(self.path.read_bytes(), before)
        self.write("{damaged")
        self.assertEqual(self.state("add-reply", "--relay-name", "luna", "--check").returncode, 4)
        self.write(record)
        self.assertEqual(self.state("add-reply", "--relay-name", "luna").returncode, 0)
        self.assertEqual(json.loads(self.path.read_text())["bots"]["luna"]["cloud"]["state"], "confirmed")


class RelayPython(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.path = self.home / ".local/lib/agent-mesh/deployed.json"
        self.env = dict(os.environ, HOME=str(self.home), AGENT_MESH_LANG="en", AGENT_MESH_PYTHON_CANDIDATES="")
        self.env.pop("AGENT_MESH_LOCK_FD", None)
        conf = self.home / ".config/agent-relay"
        conf.mkdir(parents=True)
        (conf / "env").write_text(ENV.format(home=self.home))

    def state(self, *args, candidates=None):
        env = self.env if candidates is None else dict(self.env, AGENT_MESH_PYTHON_CANDIDATES=candidates)
        return subprocess.run([sys.executable, str(ROOT / "scripts/deploy_state.py"), *args], env=env,
                              capture_output=True, text=True)

    def locked(self, *args):
        return run_locked(self.env, sys.executable, ROOT / "scripts/deploy_state.py", *args)

    def lines(self, *args, **kwargs):
        return self.state("relay-python", *args, **kwargs).stdout.splitlines()

    def record(self):
        return json.loads(self.path.read_text()) if self.path.exists() else None

    def write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(dict(data, schema=1)))

    def plist(self, python, label="com.local.agent-relay"):
        path = self.home / "Library/LaunchAgents" / f"{label}.plist"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": [str(python), "/relay.py"]}))
        return path

    def added(self, name):
        conf = self.home / ".config/agent-relay-bots" / name
        conf.mkdir(parents=True)
        (conf / "env").write_text(ENV.format(home=self.home)
                                  + f"LISTEN_NAME={name}\nCLOUD_NAME={name}-cloud\nINBOX_NAME={name}-inbox\n")
        data = self.record() or {}
        data.setdefault("bots", {})[name] = {"stage": "relay"}
        self.write(data)

    def test_the_record_comes_first_then_the_first_plist_then_the_pick(self):
        missing = str(self.base / "gone/python3")
        self.assertEqual(self.lines(candidates=f"{missing}:{BASE_PYTHON}"), [f"python {BASE_PYTHON}", "source picked"])
        in_plist = fake_python(self.base / "one/python3")
        self.plist(in_plist)
        self.assertEqual(self.lines(candidates=BASE_PYTHON), [f"python {in_plist}", "source plist", "note temp"])
        recorded = fake_python(self.base / "two/python3")
        self.write({"relay_python": str(recorded)})
        self.assertEqual(self.lines(candidates=BASE_PYTHON),
                         [f"python {recorded}", "source recorded", "note temp", "note mixed"])
        recorded.unlink()
        self.assertEqual(self.lines(candidates=BASE_PYTHON), [f"python {in_plist}", "source plist", "note temp"])
        in_plist.unlink()
        self.assertEqual(self.lines(candidates=BASE_PYTHON),
                         [f"python {BASE_PYTHON}", "source picked", f"stale {recorded}", "note mixed"])
        r = self.state("relay-python")
        self.assertEqual((r.returncode, r.stdout.splitlines()), (3, ["source none", f"stale {recorded}"]))
        self.assertEqual(self.record(), {"schema": 1, "relay_python": str(recorded)})

    def test_an_interpreter_that_fails_the_install_check_is_not_used(self):
        broken = self.base / "broken/python3"
        broken.parent.mkdir()
        broken.write_text("#!/bin/sh\nexit 1\n")
        broken.chmod(0o755)
        plain = self.base / "plain/python3"
        plain.parent.mkdir()
        plain.write_text("")
        self.plist(broken)
        self.assertEqual(self.lines(candidates=f"{broken}:{plain}:python3:{BASE_PYTHON}"),
                         [f"python {BASE_PYTHON}", "source picked", f"stale {broken}", "note mixed"])
        for bad in (broken, plain, "python3", str(self.base / "gone")):
            r = self.state("relay-python", "--python", str(bad))
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertIn("cannot run the relay", r.stderr, bad)
        self.assertEqual(self.lines("--python", BASE_PYTHON), [f"python {BASE_PYTHON}", "source given"])

    def test_virtual_environments_and_temporary_directories_are_never_picked(self):
        import deploy_state
        venv = self.base / "venv"
        fake_python(venv / "bin/python3")
        (venv / "pyvenv.cfg").write_text("home = /usr/bin\n")
        temp = fake_python(self.base / "tmp-only/python3")
        self.assertEqual(self.lines("--python", str(venv / "bin/python3")),
                         [f"python {venv / 'bin/python3'}", "source given", "note venv", "note temp"])
        self.assertEqual(self.lines("--python", str(temp)), [f"python {temp}", "source given", "note temp"])
        r = self.state("relay-python", candidates=f"{venv / 'bin/python3'}:{temp}")
        self.assertEqual((r.returncode, r.stdout), (3, "source none\n"))
        self.assertEqual(self.lines(candidates=f"{venv / 'bin/python3'}:{temp}:{BASE_PYTHON}"),
                         [f"python {BASE_PYTHON}", "source picked"])
        self.assertTrue(deploy_state.python_in_venv(str(venv / "bin/python3")))
        self.assertFalse(deploy_state.python_in_venv(BASE_PYTHON))
        if sys.prefix != sys.base_prefix:
            self.assertTrue(deploy_state.python_in_venv(sys.executable))
        old = os.environ.get("TMPDIR")
        os.environ["TMPDIR"] = "/custom/scratch/"
        try:
            for path, want in (("/tmp/v/python3", True), ("/private/tmp/python3", True),
                               ("/var/folders/ab/c/T/python3", True), ("/private/var/folders/x/python3", True),
                               ("/custom/scratch/v/python3", True), ("/custom/scratchy/python3", False),
                               ("/opt/homebrew/bin/python3", False), ("/usr/local/bin/python3", False),
                               ("/usr/bin/python3", False), ("/tmpx/python3", False)):
                self.assertEqual(deploy_state.python_in_temp(path), want, path)
        finally:
            if old is None:
                del os.environ["TMPDIR"]
            else:
                os.environ["TMPDIR"] = old
        self.assertEqual(deploy_state.PYTHON_PICKS,
                         ("/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"))

    def test_the_plist_string_is_kept_as_written_and_only_a_lock_holder_records_it(self):
        link = self.base / "links/python three"
        link.parent.mkdir()
        link.symlink_to(BASE_PYTHON)
        self.plist(link)
        want = [f"python {link}", "source plist", "note temp"]
        self.assertEqual(self.lines(candidates=BASE_PYTHON), want)
        self.assertIsNone(self.record())
        for args in (("--record",), ("--set", BASE_PYTHON)):
            r = self.state("relay-python", *args)
            self.assertNotEqual(r.returncode, 0, args)
            self.assertIn("only runs inside ./install.sh or ./update.sh", r.stderr, args)
            self.assertIsNone(self.record(), args)
        r = self.locked("relay-python", "--record")
        self.assertEqual((r.returncode, r.stdout.splitlines()), (0, want), r.stderr)
        self.assertEqual(self.record(), {"schema": 1, "relay_python": str(link)})
        self.assertEqual(self.lines(), [f"python {link}", "source recorded", "note temp"])
        before = self.path.read_bytes()
        self.assertEqual(self.locked("relay-python", "--record").returncode, 0)
        self.assertEqual(self.path.read_bytes(), before)
        r = self.locked("relay-python", "--set", str(self.base / "gone"))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.path.read_bytes(), before)
        r = self.locked("relay-python", "--set", BASE_PYTHON)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.record()["relay_python"], BASE_PYTHON)

    def test_a_picked_interpreter_is_not_recorded_by_the_query(self):
        r = self.locked("relay-python", "--record")
        self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
        self.env["AGENT_MESH_PYTHON_CANDIDATES"] = BASE_PYTHON
        r = self.locked("relay-python", "--record")
        self.assertEqual((r.returncode, r.stdout.splitlines()), (0, [f"python {BASE_PYTHON}", "source picked"]))
        self.assertIsNone(self.record())

    def test_bots_whose_plists_name_different_interpreters_are_reported_and_the_first_bot_wins(self):
        first = fake_python(self.base / "a/python3")
        second = fake_python(self.base / "b/python3")
        self.added("luna")
        self.plist(first)
        self.plist(second, "com.local.agent-relay.luna")
        self.assertEqual(self.lines(), [f"python {first}", "source plist", "note temp", "note mixed"])
        r = self.state("status", "--lang", "en")
        self.assertIn("relay changed", r.stdout.splitlines())
        self.plist(first, "com.local.agent-relay.luna")
        self.assertEqual(self.lines(), [f"python {first}", "source plist", "note temp"])

    def test_a_path_with_outer_whitespace_is_never_used(self):
        import deploy_state
        odd = fake_python(self.base / "odd/python3 ")
        self.assertTrue(os.access(odd, os.X_OK))
        self.assertFalse(deploy_state.python_usable(str(odd)))
        self.assertFalse(deploy_state.python_usable(f" {BASE_PYTHON}"))
        self.assertTrue(deploy_state.python_usable(BASE_PYTHON))
        r = self.state("relay-python", "--python", str(odd))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("cannot run the relay", r.stderr)
        inner = fake_python(self.base / "in ner/python3")
        self.assertEqual(self.lines("--python", str(inner))[:2], [f"python {inner}", "source given"])
        self.assertEqual(self.lines(candidates=f"{odd}:{BASE_PYTHON}")[:2], [f"python {BASE_PYTHON}", "source picked"])

    def test_the_daemon_must_have_started_in_or_after_the_second_the_settings_were_written(self):
        import deploy_state
        from unittest import mock
        cases = (
            ({"state": "pending", "written_s": 1000}, (None, None), True),
            ({"state": "pending"}, (None, None), True),
            ({"state": "pending", "written_s": 1000}, (7, None), False),
            ({"state": "pending", "written_s": 1000}, (7, 999), False),
            ({"state": "pending", "written_s": 1000}, (7, 999.99), False),
            ({"state": "pending", "written_s": 1000}, (7, 1000), True),
            ({"state": "pending", "written_s": 1000}, (7, 1000.99), True),
            ({"state": "pending", "written_s": 1000}, (7, 1001), True),
            ({"state": "pending"}, (7, 2000), False),
            ({"state": "pending", "written_s": True}, (7, 2000), False),
            ({"state": "pending", "written_s": "1000"}, (7, 2000), False),
            ({"state": "pending", "written_s": 1000.0}, (7, 2000), False),
        )
        for record, start, want in cases:
            with self.subTest(record=record, start=start):
                with mock.patch.object(deploy_state, "daemon_start", return_value=start):
                    self.assertIs(deploy_state.config_loaded(record), want)

    def test_status_uses_the_pinned_interpreter_whatever_runs_it(self):
        import deploy_state
        from write_launch_agent import launch_agent_bytes
        pinned = fake_python(self.base / "pinned/python3")
        lib = self.home / ".local/lib/agent-mesh"
        lib.mkdir(parents=True)
        shutil.copy(ROOT / "agent_relay.py", lib / "agent_relay.py")
        plist = self.home / "Library/LaunchAgents/com.local.agent-relay.plist"
        plist.parent.mkdir(parents=True)
        plist.write_bytes(launch_agent_bytes(self.home, str(pinned), str(lib / "agent_relay.py")))
        before = plist.read_bytes()
        other = fake_python(self.base / "other/python3")
        for runner in (sys.executable, str(other)):
            r = subprocess.run([runner, str(ROOT / "scripts/deploy_state.py"), "status", "--lang", "en"],
                               env=self.env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("relay unchanged", r.stdout.splitlines(), runner)
        r = self.state("status", "--lang", "en", "--python", str(other))
        self.assertIn("relay changed", r.stdout.splitlines())
        self.assertEqual(plist.read_bytes(), before)
        self.assertIsNone(self.record())
        self.assertEqual(deploy_state.CONFIG_KEYS, ("auto_update", "lan"))


if __name__ == "__main__":
    unittest.main()
