"""deploy_state.py record handling: strict writes, old transaction and request formats, the first Bot's name."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from render_prompt import ROOT

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
        runtime = sorted(f"box/{p.name}" for p in (ROOT / "box").glob("rn-*") if p.name != "rn-cloud-uninstall.sh")
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

if __name__ == "__main__":
    unittest.main()
