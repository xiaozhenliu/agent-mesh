import json, os, sqlite3, subprocess, sys, tempfile, time, unittest
from unittest import mock
from pathlib import Path

from tests.cloud.support import (
    HERE,
    KEY,
    load,
    rr,
    SCHEMA,
)


SESSIONS = """CREATE TABLE sessions (name TEXT PRIMARY KEY, key TEXT NOT NULL, agent TEXT, cwd TEXT, repo TEXT,
branch TEXT, doing TEXT, tier TEXT, last_seen INTEGER NOT NULL);"""

FAKE_ENGINE = r'''#!/usr/bin/env python3
# fake reason-network for rn-cloud.sh: always connected; records calls; FAKE_SEND picks what `send` does
import json, os, sqlite3, subprocess, sys, time
args = sys.argv[1:]
body = sys.stdin.read() if args[:1] == ["send"] else ""
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(json.dumps({"args": args, "body": body}) + "\n")
if args[:1] == ["doctor"]:
    print("peers: 0 connected" if os.environ.get("FAKE_OFFLINE") else "peers: 1 connected")
    if os.environ.get("FAKE_DOCTOR_TAIL"):
        sys.stdout.flush()
        time.sleep(0.3)
        for i in range(20000):
            print("diagnostic line", i)
    sys.exit(int(os.environ.get("FAKE_DOCTOR_RC", "0")))
elif args[:1] == ["listen"]:
    who = args[args.index("--as") + 1]
    name = json.loads(os.environ.get("FAKE_REGISTER", "{}")).get(who, who)
    if name != "-":
        db = sqlite3.connect(os.path.join(os.environ["REASON_NETWORK_HOME"], "network.db"))
        db.execute("INSERT OR IGNORE INTO sessions (name, key, last_seen) VALUES (?, ?, ?)",
                   (name, "cli:" + who, int(time.time() * 1000)))
        db.commit()
        db.close()
    print(os.environ.get("FAKE_LISTEN_OUT") or f"No messages. You are demo/{name}.")
elif args[:1] == ["send"]:
    mode = os.environ.get("FAKE_SEND", "ok")
    if mode == "queued":                       # never leaves the box's outbox
        db = sqlite3.connect(os.path.join(os.environ["REASON_NETWORK_HOME"], "network.db"))
        db.execute("INSERT INTO outbox VALUES ('stuck')"); db.commit()
    elif mode == "lock":                       # someone else holds the inbox lock right after the handoff
        ready = os.environ["FAKE_LOG"] + ".ready"
        holder = ("import fcntl, sys, time; fh = open(sys.argv[1], 'a'); fcntl.flock(fh, fcntl.LOCK_EX); "
                  "open(sys.argv[2], 'w').close(); time.sleep(4)")
        subprocess.Popen([sys.executable, "-c", holder, os.path.join(os.environ["RN_INBOX_DIR"], "_lock"), ready],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
        while not os.path.exists(ready):
            time.sleep(0.05)
    print(f"→ {args[1]}: queued" + (": held for approval" if mode == "held" else ""))
'''


class Cloud(unittest.TestCase):
    """rn-cloud.sh send/working/done against a fake engine, with RN_CLOUD_BASE and a fake uname (never the network)."""

    def setUp(self):
        import shutil
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        self.box, self.inbox = self.d / "box", self.d / "inbox"
        (self.box / "state").mkdir(parents=True)
        (self.box / "bin").mkdir()
        (self.box / "person").write_text("demo\n")
        (self.box / "outbound.key").write_text(KEY + "\n")
        (self.box / "rn-fetch.py").symlink_to(HERE / "rn-fetch.py")
        (self.box / "rn-engine.py").symlink_to(HERE / "rn-engine.py")
        (self.box / "bin" / "engine").mkdir()
        engine = self.box / "bin" / "engine" / "reason-network"
        engine.write_text(FAKE_ENGINE.replace("#!/usr/bin/env python3", "#!" + sys.executable, 1))
        engine.chmod(0o755)
        self.builds = self.d / "builds.json"
        self.pin(engine)
        db = sqlite3.connect(self.box / "state" / "network.db")
        db.executescript(SCHEMA + SESSIONS + "CREATE TABLE outbox (id TEXT);")
        db.close()
        tools = self.d / "tools"
        tools.mkdir()
        for name, script in (("uname", 'echo Linux'), ("sleep", "exit 0")):    # sleep: skip the 60 s waits
            (tools / name).write_text("#!/bin/sh\n" + script + "\n")
            (tools / name).chmod(0o755)
        (tools / "python3").symlink_to(sys.executable)
        self.log = self.d / "rn.log"
        self.env = dict(os.environ, PATH=f"{tools}{os.pathsep}{os.environ['PATH']}", RN_CLOUD_BASE=str(self.box),
                        RN_INBOX_DIR=str(self.inbox), FAKE_LOG=str(self.log), RN_PINNED_BUILDS_FILE=str(self.builds))
        for k in ("RN_PERSON", "RN_KEY_FILE", "REASON_NETWORK_HOME", "RN_DONE_LOCK_WAIT", "RN_CLAIM_LEASE",
                  "RN_RELAY_NAME", "RN_CLOUD_NAME", "RN_INBOX_NAME", "AGENT_RELAY_NAME", "RN_BOX", "RN_BIN",
                  "REASON_NETWORK_NO_UPDATE"):
            self.env.pop(k, None)

    def pin(self, *binaries):
        import hashlib
        target = {"x86_64": "x86_64-unknown-linux-musl"}.get(os.uname().machine, "aarch64-unknown-linux-musl")
        builds = {f"0.0.{i}": {target: hashlib.sha256(Path(b).read_bytes()).hexdigest()}
                  for i, b in enumerate(binaries)}
        self.builds.write_text(json.dumps(builds))

    def cloud(self, *args, body=""):
        return subprocess.run(["bash", str(HERE / "rn-cloud.sh"), *args], env=self.env, input=body,
                              capture_output=True, text=True, timeout=60)

    def engine_sends(self):
        calls = [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []
        return [c for c in calls if c["args"][:1] == ["send"]]

    def engine_calls(self):
        return [json.loads(l)["args"] for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def listened(self):
        return [a[a.index("--as") + 1] for a in self.engine_calls() if a[:1] == ["listen"]]

    def put_fwd(self, mid, text, to="demo/bot-inbox"):
        db = sqlite3.connect(self.box / "state" / "network.db")
        db.execute("INSERT INTO messages (id, sender, recipient, body, wake, sent_at) VALUES (?,?,?,?,1,?)",
                   (f"01CL{mid}", "demo/terminal", to, rr.fwd_body(KEY, mid, "demo/other", "codex", "",
                                                                           text).strip(), int(time.time() * 1000)))
        db.commit()
        db.close()

    def test_an_unpinned_engine_is_never_run(self):
        engine = self.box / "bin" / "engine" / "reason-network"
        with open(engine, "a") as f:
            f.write("\n")
        for args in (("who",), ("fetch",), ("done", "A"), ("init-inbox",)):
            with self.subTest(args=args):
                p = self.cloud(*args)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn("is not a Reason Network build pinned by agent-mesh", p.stderr)
        self.assertEqual(self.engine_calls(), [])
        engine.unlink()
        engine.symlink_to(self.d / "elsewhere")
        p = self.cloud("who")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(self.engine_calls(), [])

    def test_rn_fetch_run_alone_checks_the_engine_in_rn_box(self):
        engine = self.box / "bin" / "engine" / "reason-network"
        keep = {k: v for k, v in self.env.items() if k != "RN_BIN"}
        rf = load("rn_fetch_alone", HERE / "rn-fetch.py")
        with mock.patch.dict(os.environ, dict(keep, RN_BOX=str(self.box)), clear=True):
            self.assertEqual(rf.rn_bin(), str(engine))
            with open(engine, "a") as f:
                f.write("\n")
            with self.assertRaises(SystemExit) as stopped:
                rf.rn_bin()
            self.assertIn("is not a Reason Network build pinned by agent-mesh", str(stopped.exception))
        with mock.patch.dict(os.environ, dict(keep, RN_BOX=str(self.box), RN_BIN="/given"), clear=True):
            self.assertEqual(rf.rn_bin(), "/given")

    def test_a_replaced_launcher_is_put_back_before_the_engine_runs(self):
        launcher = self.box / "bin" / "reason-network"
        launcher.write_text("#!/bin/sh\necho stray\n")
        p = self.cloud("who")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("the launcher is back", p.stderr)
        self.assertTrue(launcher.read_text().startswith("#!/bin/sh\nhere=$(dirname \"$0\")"))
        self.assertTrue(self.engine_calls())

    def test_planted_modules_next_to_the_scripts_or_in_the_cwd_are_not_imported(self):
        marker = self.d / "planted"
        for name in ("rn-fetch.py", "rn-engine.py"):
            (self.box / name).unlink()
            (self.box / name).write_bytes((HERE / name).read_bytes())
        for d in (self.box, self.d):
            for name in ("json.py", "shutil.py", "hashlib.py", "sqlite3.py"):
                (d / name).write_text(f"open({str(marker)!r}, 'a').write({name!r})\n")
        p = subprocess.run(["bash", str(HERE / "rn-cloud.sh"), "who"], env=self.env, cwd=self.d,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse(marker.exists(), marker.read_text() if marker.exists() else "")

    def test_the_engine_gets_no_update_from_every_command(self):
        self.env["REASON_NETWORK_NO_UPDATE"] = "0"
        engine = self.box / "bin" / "engine" / "reason-network"
        text = engine.read_text()
        engine.write_text(text.replace("import ", "import os as _os\nopen(_os.environ['FAKE_LOG'] + '.env', 'a')"
                                       ".write(_os.environ.get('REASON_NETWORK_NO_UPDATE', '-') + '\\n')\nimport ", 1))
        self.pin(engine)
        p = self.cloud("who")
        self.assertEqual(p.returncode, 0, p.stderr)
        seen = Path(str(self.log) + ".env").read_text().split()
        self.assertTrue(seen)
        self.assertEqual(set(seen), {"1"})

    def test_usage_lists_working_done_and_settings(self):
        p = self.cloud()
        self.assertEqual(p.returncode, 2)
        for word in ("rn-cloud.sh working <id>", "rn-cloud.sh done <id>...", "RN_CLAIM_LEASE", "RN_ORPHANS",
                     "RN_ORPHAN_DAYS", "RN_DONE_LOCK_WAIT", "RN_GOT", "RN-GOT"):
            self.assertIn(word, p.stdout)

    def test_send_records_replying_then_done(self):
        p = self.cloud("send", "demo/other", "A", body="reply text")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("handed to the Mac", p.stdout)
        self.assertEqual(len(self.engine_sends()), 1)
        self.assertTrue((self.inbox / "A" / "replying").exists())
        self.assertEqual(json.loads((self.inbox / "A" / "done").read_text())["via"], "send")
        p = self.cloud("send", "demo/other", body="no reply_to: no markers")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(sorted(x.name for x in self.inbox.iterdir()), ["A", "_claims_v1", "_lock"])

    def test_send_does_not_reach_the_engine_if_replying_cannot_be_written(self):
        import fcntl
        self.inbox.mkdir()
        self.env["RN_DONE_LOCK_WAIT"] = "1"
        with open(self.inbox / "_lock", "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            p = self.cloud("send", "demo/other", "A", body="reply text")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("nothing was sent", p.stderr)
        self.assertEqual(self.engine_sends(), [])
        self.assertFalse((self.inbox / "A").exists())

    def test_send_handed_over_but_done_not_recorded_exits_0_then_uncertain(self):
        self.put_fwd("A", "question")
        self.env.update(FAKE_SEND="lock", RN_DONE_LOCK_WAIT="1")
        p = self.cloud("send", "demo/other", "A", body="reply text")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("handed to the Mac", p.stdout)
        self.assertIn("sent, but could not record A as handled", p.stderr)
        self.assertFalse((self.inbox / "A" / "done").exists())
        t = time.time() - 4000
        os.utime(self.inbox / "A" / "replying", (t, t))
        self.env.pop("FAKE_SEND")
        self.env["RN_DONE_LOCK_WAIT"] = "300"
        p = self.cloud("fetch", "A")                                   # waits for the holder, then reports
        self.assertEqual(p.returncode, 0, p.stderr)
        r = json.loads(p.stdout.splitlines()[0])
        self.assertEqual((r["id"], r["status"]), ("A", "uncertain"))

    def test_status_words_in_the_team_name_do_not_fail_a_send(self):
        (self.box / "person").write_text("sheldon\n")
        p = self.cloud("send", "sheldon/other", "A", body="reply text")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("→ sheldon/bot: queued", p.stdout)
        self.assertIn("handed to the Mac", p.stdout)
        self.assertTrue((self.inbox / "A" / "done").exists())
        self.assertEqual([s["args"][1] for s in self.engine_sends()], ["sheldon/bot"])

    def test_held_still_fails_the_send(self):
        (self.box / "person").write_text("sheldon\n")
        self.env["FAKE_SEND"] = "held"
        p = self.cloud("send", "sheldon/other", body="reply text")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("did not accept", p.stderr)
        self.assertNotIn("handed to the Mac", p.stdout)

    def test_send_fails_when_the_output_cannot_be_classified(self):
        (self.box / "rn-fetch.py").unlink()
        p = self.cloud("send", "demo/other", body="reply text")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("could not classify the engine output", p.stderr)
        self.assertNotIn("handed to the Mac", p.stdout)
        self.assertEqual(len(self.engine_sends()), 1)

    def test_send_still_queued_after_60s_is_not_done(self):
        self.env["FAKE_SEND"] = "queued"
        p = self.cloud("send", "demo/other", "A", body="reply text")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("still queued", p.stderr)
        self.assertIn("Do not resend", p.stderr)
        self.assertTrue((self.inbox / "A" / "replying").exists())
        self.assertFalse((self.inbox / "A" / "done").exists())

    def envelopes(self):
        return [rr.parse_envelope(c["body"]) for c in self.engine_sends() if c["body"].startswith("RN-OUT v1")]

    def test_derived_nonce_vector(self):
        p = self.cloud("send", "demo/claude-session", "01JTESTREPLYTO0000000000", body="hello\n第二行\n")
        self.assertEqual(p.returncode, 0, p.stderr)
        (e,) = self.envelopes()
        self.assertEqual((e["nonce"], e["body"]), ("21dfab75a1eabc427d7655db", "hello\n第二行"))

    def test_same_reply_file_twice_repeats_the_nonce_and_the_relay_sends_it_once(self):
        body = "the same reply\n回复正文\n\n"
        self.assertEqual(self.cloud("send", "demo/other", "A", body=body).returncode, 0)
        (first,) = self.envelopes()
        while int(time.time()) <= int(first["ts"]):
            time.sleep(0.05)
        self.assertEqual(self.cloud("send", "demo/other", "A", body=body).returncode, 0)
        e1, e2 = self.envelopes()
        want = rr.hmac.new(KEY.encode(), "idem1\ndemo/other\nA\nthe same reply\n回复正文".encode(),
                           rr.hashlib.sha256).hexdigest()[:24]
        self.assertEqual((e1["nonce"], e2["nonce"]), (want, want))
        self.assertNotEqual(e1["ts"], e2["ts"])
        for e in (e1, e2):
            self.assertEqual(e["sig"], rr.sign(KEY, e["to"], e["reply_to"], e["ts"], e["nonce"], e["body"]))
        (rr.STATE_DIR / "state.json").unlink(missing_ok=True)
        relay = rr.Relay({"RN_BIN": "/nonexistent", "PERSON": "demo", "LISTEN_NAME": "bot", "CLOUD_NAME": "bot-cloud",
                          "MAX_SPOOL": 50, "MAX_PROXY_PER_HOUR": 60, "OUTBOUND_KEY": KEY,
                          "WEBHOOK_MAX_BODY_CHARS": 3500})
        sent = []
        class Ok:
            returncode, stdout, stderr = 0, "→ demo/other: queued", ""
        relay.rn = lambda *a, **k: (sent.append(a), Ok())[1]
        relay.handle([{"id": f"o{k}", "from": "demo/bot-cloud", "agent": None, "thread": None,
                       "text": c["body"]} for k, c in enumerate(self.engine_sends()) if c["body"].startswith("RN-OUT")])
        self.assertEqual(len(sent), 1)
        self.assertEqual((relay.s.spool, relay.s.alerts), ([], []))

    def test_without_reply_to_every_send_gets_a_new_nonce(self):
        for _ in range(2):
            self.assertEqual(self.cloud("send", "demo/other", body="hello again").returncode, 0)
        e1, e2 = self.envelopes()
        self.assertNotEqual(e1["nonce"], e2["nonce"])
        self.assertTrue(all(len(e["nonce"]) == 24 for e in (e1, e2)))

    def test_working_fails_while_the_lock_is_busy(self):
        import fcntl
        self.inbox.mkdir()
        self.env["RN_DONE_LOCK_WAIT"] = "1"
        with open(self.inbox / "_lock", "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            p = self.cloud("working", "A")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("do not process it", p.stderr)
        self.assertFalse((self.inbox / "A").exists())
        p = self.cloud("working", "A")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue((self.inbox / "A" / "working").exists())

    def test_done_records_ids_and_skips_relay_notice_batches(self):
        p = self.cloud("done", "A", "B", "relay-alerts-123")
        self.assertEqual(p.returncode, 0, p.stderr)
        for mid in "AB":
            self.assertTrue((self.inbox / mid / "done").exists())
        self.assertFalse((self.inbox / "relay-alerts-123").exists())
        (s,) = self.engine_sends()
        self.assertEqual(s["args"], ["send", "demo/bot", "-"])
        f = rr.parse_got(s["body"])
        self.assertEqual((f["stored_ids"], f["done_ids"]), ([], ["relay-alerts-123"]))
        self.assertNotEqual(self.cloud("done").returncode, 0)
        self.assertNotEqual(self.cloud("working").returncode, 0)

    def test_connection_check_reads_all_doctor_output(self):
        self.env["FAKE_DOCTOR_TAIL"] = "1"
        p = self.cloud("who")
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_connection_check_fails_when_doctor_fails(self):
        self.env["FAKE_DOCTOR_RC"] = "7"
        p = self.cloud("who")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("the Mac is not connected", p.stderr)

    def test_fetch_sends_a_receipt_only_while_the_mac_is_connected(self):
        self.put_fwd("A", "question")
        p = self.cloud("fetch", "A")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout.splitlines()[0])["got"], "stored")
        (s,) = [c for c in self.engine_sends() if c["body"].startswith("RN-GOT v1")]
        self.assertEqual((s["args"], rr.parse_got(s["body"])["stored_ids"]), (["send", "demo/bot", "-"], ["A"]))
        self.put_fwd("B", "another")
        self.env["FAKE_OFFLINE"] = "1"
        p = self.cloud("fetch", "B")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("got", json.loads(p.stdout.splitlines()[0]))
        self.assertEqual(len([c for c in self.engine_sends() if c["body"].startswith("RN-GOT v1")]), 1)

    def test_without_box_relay_the_names_are_the_defaults(self):
        p = self.cloud("who")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.listened(), ["bot-inbox"])
        self.assertEqual(self.cloud("send", "demo/other", body="hi").returncode, 0)
        (s,) = self.engine_sends()
        self.assertEqual(s["args"], ["send", "demo/bot", "--from", "demo/bot-cloud", "-"])

    def test_box_relay_names_every_session(self):
        (self.box / "relay").write_text("alice\n")
        for cmd in ("who", "init-inbox"):
            self.log.unlink(missing_ok=True)
            p = self.cloud(cmd)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(self.listened(), ["alice-inbox"])
        self.assertIn("demo/alice-inbox registered", p.stdout)
        self.log.unlink()
        self.put_fwd("A", "question", to="demo/alice-inbox")
        p = self.cloud("fetch", "A")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.listened(), ["alice-inbox", "alice-cloud", "alice-inbox"])
        (s,) = self.engine_sends()
        self.assertEqual(s["args"], ["send", "demo/alice", "-"])
        self.log.unlink()
        p = self.cloud("send", "demo/other", "A", body="reply")
        self.assertEqual(p.returncode, 0, p.stderr)
        (s,) = self.engine_sends()
        self.assertEqual(s["args"], ["send", "demo/alice", "--from", "demo/alice-cloud", "-"])

    def test_agent_relay_name_changes_only_the_send_target(self):
        (self.box / "relay").write_text("alice\n")
        self.env["AGENT_RELAY_NAME"] = "x@m"
        p = self.cloud("send", "demo/other", body="reply")
        self.assertEqual(p.returncode, 0, p.stderr)
        (s,) = self.engine_sends()
        self.assertEqual(s["args"], ["send", "demo/x@m", "--from", "demo/alice-cloud", "-"])
        self.assertEqual(self.listened(), ["alice-cloud", "alice-inbox"])

    def test_a_damaged_box_relay_stops_before_any_engine_call(self):
        for text in ("", "\n", "Bot\n", "a--b\n", "terminal\n", "a" * 35 + "\n"):
            with self.subTest(text=text):
                (self.box / "relay").write_text(text)
                for cmd in (("who",), ("fetch", "A"), ("done", "A")):
                    p = self.cloud(*cmd)
                    self.assertNotEqual(p.returncode, 0)
                    self.assertIn("relay is damaged", p.stderr)
                self.assertNotEqual(self.cloud("send", "demo/other", body="x").returncode, 0)
                self.assertEqual(self.engine_calls(), [])
                self.assertFalse(self.inbox.exists())

    def test_invalid_session_name_overrides_stop_before_any_engine_call(self):
        for k, v in (("RN_INBOX_NAME", "Inbox"), ("RN_CLOUD_NAME", "a--cloud"), ("RN_CLOUD_NAME", "c" * 41)):
            with self.subTest(k=k, v=v):
                env = dict(self.env)
                self.env[k] = v
                p = self.cloud("who")
                self.env = env
                self.assertNotEqual(p.returncode, 0)
                self.assertIn("invalid session name", p.stderr)
                self.assertEqual(self.engine_calls(), [])
        self.env.update(RN_INBOX_NAME="lab-inbox", RN_CLOUD_NAME="lab-cloud")
        self.assertEqual(self.cloud("send", "demo/other", body="x").returncode, 0)
        self.assertEqual(self.listened(), ["lab-cloud", "lab-inbox"])

    def test_registration_must_show_exactly_the_expected_name(self):
        (self.box / "relay").write_text("alice\n")
        cases = (({"alice-inbox": "alice-inbox-2"}, "", "who", "registered alice-inbox-2 instead of demo/alice-inbox"),
                 ({"alice-inbox": "alice-inbox-2"}, "from demo/terminal: an unread message body", "who",
                  "registered alice-inbox-2 instead of demo/alice-inbox"),
                 ({"alice-inbox": "-"}, "", "who", "registered nothing instead of demo/alice-inbox"),
                 ({"alice-cloud": "alice-cloud-2"}, "", "fetch", "registered alice-cloud-2 instead of demo/alice-cloud"))
        for register, listen_out, cmd, want in cases:
            with self.subTest(register=register, listen_out=listen_out):
                db = sqlite3.connect(self.box / "state" / "network.db")
                db.execute("DELETE FROM sessions")
                db.commit()
                db.close()
                self.env.update(FAKE_REGISTER=json.dumps(register), FAKE_LISTEN_OUT=listen_out)
                p = self.cloud(cmd, "A") if cmd == "fetch" else self.cloud(cmd)
                self.assertNotEqual(p.returncode, 0)
                self.assertIn(want, p.stderr)
                self.assertIn("another session may use this name", p.stderr)
                self.assertNotIn(listen_out or "unused", p.stdout + p.stderr)

    def test_an_earlier_registration_in_the_same_engine_run_counts(self):
        (self.box / "relay").write_text("alice\n")
        db = sqlite3.connect(self.box / "state" / "network.db")
        db.executemany("INSERT INTO sessions (name, key, last_seen) VALUES (?, ?, 1)",
                       [("alice-inbox", "cli:alice-inbox"), ("alice-cloud", "cli:alice-cloud")])
        db.commit()
        db.close()
        self.env["FAKE_REGISTER"] = json.dumps({"alice-inbox": "-", "alice-cloud": "-"})
        self.assertEqual(self.cloud("who").returncode, 0)
        self.put_fwd("A", "question", to="demo/alice-inbox")
        p = self.cloud("fetch", "A")
        self.assertEqual(p.returncode, 0, p.stderr)
        p = self.cloud("send", "demo/other", "A", body="reply")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.listened(), ["alice-inbox", "alice-inbox", "alice-cloud", "alice-inbox",
                                           "alice-cloud", "alice-inbox"])

    def test_fetch_then_send_registers_each_session_again_in_one_engine_run(self):
        self.put_fwd("A", "question")
        p = self.cloud("fetch", "A")
        self.assertEqual(p.returncode, 0, p.stderr)
        p = self.cloud("send", "demo/other", "A", body="reply")
        self.assertEqual(p.returncode, 0, p.stderr)
        p = self.cloud("send", "demo/other", body="again")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(self.engine_sends()), 3)

    def test_registration_check_fails_when_the_session_table_cannot_be_read(self):
        db = sqlite3.connect(self.box / "state" / "network.db")
        db.execute("DROP TABLE sessions")
        db.commit()
        db.close()
        self.env["FAKE_REGISTER"] = json.dumps({"bot-inbox": "-"})
        p = self.cloud("who")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("registered nothing", p.stderr)
