#!/usr/bin/env python3
"""Offline checks for rn-fetch.py against a fake engine database (same messages schema as Reason Network
v0.10.5 src/store.rs) and the relay's own RN-FWD encoder (../agent_relay.py), plus the RN-REFWD re-forward flow
against a fake reason-network binary. Run: python3 test_rn_fetch.py"""
import importlib.util, json, os, sqlite3, subprocess, sys, tempfile, time, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KEY = "0123456789abcdef" * 4                      # fake test key


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


os.environ.setdefault("AGENT_RELAY_STATE", tempfile.mkdtemp())
rr = load("agent_relay", HERE.parent / "agent_relay.py")
SCHEMA = """CREATE TABLE messages (id TEXT PRIMARY KEY, sender TEXT NOT NULL, sender_agent TEXT, sender_repo TEXT,
recipient TEXT NOT NULL, via TEXT, reply_to TEXT, body TEXT NOT NULL, wake INTEGER NOT NULL, sent_at INTEGER NOT NULL,
read_at INTEGER, held INTEGER NOT NULL DEFAULT 0);"""


class T(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "state").mkdir()
        (self.d / "key").write_text(KEY + "\n")
        self.db = sqlite3.connect(self.d / "state" / "network.db")
        self.db.executescript("PRAGMA journal_mode=WAL;" + SCHEMA)
        self.n = 0
        self.env = dict(os.environ, REASON_NETWORK_HOME=str(self.d / "state"), RN_PERSON="demo",
                        RN_INBOX_NAME="bot-inbox", RN_KEY_FILE=str(self.d / "key"), RN_INBOX_DIR=str(self.d / "inbox"))

    def put(self, body, to="demo/bot-inbox", read=False):
        self.n += 1
        body = body.strip()                                     # what the engine stores (src/daemon/mail.rs:83)
        self.db.execute("INSERT INTO messages (id, sender, recipient, body, wake, sent_at, read_at) VALUES (?,?,?,?,1,?,?)",
                        (f"01RN{self.n:022d}", "demo/terminal", to, body, 1000 + self.n, 5 if read else None))
        self.db.commit()

    def fwd(self, mid, text, key=KEY, frm="demo/other"):
        return rr.fwd_body(key, mid, frm, "codex", "01JTHREAD", text)

    def run_fetch(self, *args):
        p = subprocess.run([sys.executable, str(HERE / "rn-fetch.py"), *args], env=self.env, capture_output=True,
                           text=True, timeout=60)
        return p.returncode, [json.loads(l) for l in p.stdout.splitlines() if l.strip()]

    def test_exact_text_complete(self):
        texts = {"a": "  leading\n\ntrailing  \n\n", "b": "<reason-message> &lt; <b> 中文 😀 \r\n\t\x01",
                 "c": "x" * 9990 + "\nEND-10000", "d": "", "e": "has\n---\nseparator\n--- RN-FWD END\nin it"}
        for k, t in texts.items():
            self.put(self.fwd(k, t), read=(k == "a"))           # read rows still count
        rc, res = self.run_fetch("scan", *texts)
        self.assertEqual(rc, 0, res)
        for r in res:
            self.assertEqual(r["status"], "complete", r)
            got = open(r["full"], encoding="utf-8", newline="").read()
            self.assertEqual(got, texts[r["id"]])
            meta = json.loads((Path(r["dir"]) / "meta.json").read_text())
            self.assertEqual((meta["parts"], meta["text_len"], meta["via"]), (1, len(texts[r["id"]]), "rn-fwd"))

    def test_missing_and_wait_timeout(self):
        rc, res = self.run_fetch("wait", "--timeout", "3", "nope")
        self.assertEqual((rc, res[0]["status"]), (4, "missing"))

    def test_wait_picks_up_late_arrival(self):
        p = subprocess.Popen([sys.executable, str(HERE / "rn-fetch.py"), "wait", "--timeout", "20", "late"],
                             env=self.env, stdout=subprocess.PIPE, text=True)
        import time; time.sleep(3)
        self.put(self.fwd("late", "arrived later"))
        out, _ = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(out)["status"], "complete")

    def test_bad_signature_tamper_cutoff(self):
        self.put(self.fwd("forged", "hi", key="f" * 64))
        self.put(self.fwd("tampered", "original").replace("original", "changed!"))
        self.put(self.fwd("cut", "y" * 500)[:-20])
        self.put("plain text to the inbox")
        rc, res = self.run_fetch("scan", "forged", "tampered", "cut")
        self.assertEqual(rc, 5)
        by = {r["id"]: r for r in res}
        self.assertIn("bad signature", by["forged"]["reason"])
        self.assertEqual(by["tampered"]["status"], "mismatch")
        self.assertIn("trailer", by["cut"]["reason"])
        self.assertFalse((self.d / "inbox" / "forged").exists())

    def test_valid_copy_beats_forged_one_and_resend_is_idempotent(self):
        self.put(self.fwd("m1", "forged", key="f" * 64))
        self.put(self.fwd("m1", "real"))
        self.put(self.fwd("m1", "real"))
        rc, res = self.run_fetch("scan", "m1")
        self.assertEqual((rc, res[0]["status"]), (0, "complete"))
        self.assertEqual(Path(res[0]["full"]).read_text(), "real")
        rc, res = self.run_fetch("scan", "m1")
        self.assertTrue(res[0]["already"])

    def test_other_recipients_ignored(self):
        self.put(self.fwd("m2", "for someone else"), to="demo/bot-cloud")
        rc, res = self.run_fetch("scan", "m2")
        self.assertEqual(res[0]["status"], "missing")

    def test_no_ids_lists_only_new(self):
        self.put(self.fwd("n1", "one"))
        self.put(self.fwd("n2", "two"))
        rc, res = self.run_fetch("scan")
        self.assertEqual(sorted(r["id"] for r in res), ["n1", "n2"])
        rc, res = self.run_fetch("scan")
        self.assertEqual(res, [])

    def row(self, rid, body, sent_ms):
        self.db.execute("INSERT INTO messages (id, sender, recipient, body, wake, sent_at) VALUES (?,?,?,?,1,?)",
                        (rid, "demo/terminal", "demo/bot-inbox", body.strip(), sent_ms))
        self.db.commit()

    def purge_fixture(self):
        import time
        now = int(time.time() * 1000)
        self.put(self.fwd("old1", "old secret"))                  # sent_at ~1970: older than any cutoff
        self.put(self.fwd("old2", "also old"))
        self.row("01RNRECENT", self.fwd("new1", "recent"), now)
        self.put(self.fwd("dup", "sent long ago"))                # original before the cutoff ...
        self.row("01RNDUPAGAIN", self.fwd("dup", "sent long ago"), now)   # ... its re-forward after it
        self.row("01RNFUTURE", self.fwd("fut", "clock skew"), now + 10 * 86400 * 1000)
        rc, res = self.run_fetch("scan")
        self.assertEqual(sorted(r["id"] for r in res), ["dup", "fut", "new1", "old1", "old2"])
        inbox, threads = self.d / "inbox", self.d / "threads"
        (inbox / "_refwd").mkdir()
        (inbox / "_refwd" / "old1.json").write_text("[1]")
        (inbox / "_stray").mkdir()
        (inbox / "_stray" / "bot-cloud.log").write_text("stray text")
        threads.mkdir()
        for name in ("demo_old.md", "demo_new.md"):
            (threads / name).write_text("notes")
        os.utime(threads / "demo_old.md", (1, 1))
        os.utime(inbox / "_stray" / "bot-cloud.log", (1, 1))
        self.env["RN_THREADS_DIR"] = str(threads)
        self.db.close()
        return inbox, threads

    def rows_left(self):
        db = sqlite3.connect(self.d / "state" / "network.db")
        try:
            return sorted(r[0] for r in db.execute("SELECT id FROM messages"))
        finally:
            db.close()

    def test_purge_removes_old_plaintext_together_and_keeps_recent(self):
        inbox, threads = self.purge_fixture()
        rc, res = self.run_fetch("purge", "7")
        self.assertEqual(rc, 0)
        self.assertEqual(res, [{"purged": {"messages": 2, "inbox": 2, "stray": 1, "threads": 1, "mismatch": 0},
                                "older_than_days": 7}])
        # both copies of "dup" stay: its newest copy is recent, and its folder stays with them
        self.assertEqual(self.rows_left(), ["01RN0000000000000000000003", "01RNDUPAGAIN", "01RNFUTURE", "01RNRECENT"])
        self.assertEqual(sorted(p.name for p in inbox.iterdir()),
                         ["_claims_v1", "_lock", "_refwd", "_stray", "dup", "fut", "new1"])
        self.assertTrue((inbox / "_refwd" / "old1.json").exists())   # the per-id re-forward cap survives
        self.assertEqual([p.name for p in threads.iterdir()], ["demo_new.md"])
        rc, res = self.run_fetch("scan")                              # nothing purged comes back as new
        self.assertEqual((rc, res), (0, []))
        rc, res = self.run_fetch("purge", "0")                        # 0 = everything, future timestamps too
        self.assertEqual(res[0]["purged"], {"messages": 4, "inbox": 3, "stray": 0, "threads": 1, "mismatch": 0})
        self.assertEqual(self.rows_left(), [])
        self.assertEqual(sorted(p.name for p in inbox.iterdir()), ["_claims_v1", "_lock", "_refwd", "_stray"])
        for bad in (["purge"], ["purge", "-1"], ["purge", "7d"], ["purge", "²"]):
            p = subprocess.run([sys.executable, str(HERE / "rn-fetch.py"), *bad], env=self.env,
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(p.returncode, 2, bad)

    def test_purge_refuses_symlinks_and_a_running_fetch_before_deleting(self):
        import fcntl
        inbox, threads = self.purge_fixture()
        before = self.rows_left()
        elsewhere = self.d / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "keep.md").write_text("not ours")
        (elsewhere / "keep.log").write_text("not ours")
        os.utime(elsewhere / "keep.md", (1, 1))
        os.utime(elsewhere / "keep.log", (1, 1))
        for link_name in ("threads", "stray"):
            link = threads if link_name == "threads" else inbox / "_stray"
            saved = link.with_name(link.name + ".real")
            link.rename(saved)
            link.symlink_to(elsewhere, target_is_directory=True)
            p = subprocess.run([sys.executable, str(HERE / "rn-fetch.py"), "purge", "0"], env=self.env,
                               capture_output=True, text=True, timeout=30)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("symlinked", p.stderr)
            link.unlink()
            saved.rename(link)
        self.assertEqual(sorted(p.name for p in elsewhere.iterdir()), ["keep.log", "keep.md"])
        self.assertEqual(self.rows_left(), before)
        with open(inbox / "_lock", "a") as held:                      # a fetch holds the inbox lock
            fcntl.flock(held, fcntl.LOCK_EX)
            self.env["RN_PURGE_LOCK_WAIT"] = "1"
            p = subprocess.run([sys.executable, str(HERE / "rn-fetch.py"), "purge", "0"], env=self.env,
                               capture_output=True, text=True, timeout=30)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("still running", p.stderr)
        self.assertEqual(self.rows_left(), before)

    def test_purge_keeps_the_folder_of_a_re_forward_that_lands_during_the_purge(self):
        import time
        inbox, _ = self.purge_fixture()
        # A copy of old1 lands right after purge commits its deletes, before it removes folders.
        driver = (
            "import importlib.util, sqlite3, sys, time\n"
            "spec = importlib.util.spec_from_file_location('rnf', sys.argv[1]); m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\n"
            "orig = m.live_ids\n"
            "def late(db_path):\n"
            "    db = sqlite3.connect(db_path)\n"
            "    db.execute('INSERT INTO messages (id, sender, recipient, body, wake, sent_at) VALUES (?,?,?,?,1,?)',"
            " ('01RNLATE', 'demo/terminal', 'demo/bot-inbox', sys.argv[2], int(time.time() * 1000)))\n"
            "    db.commit(); db.close()\n"
            "    return orig(db_path)\n"
            "m.live_ids = late\n"
            "print(m.purge(7, m.Path(sys.argv[3]))['purged'])\n")
        p = subprocess.run([sys.executable, "-c", driver, str(HERE / "rn-fetch.py"),
                            self.fwd("old1", "old secret").strip(), str(self.d / "threads")],
                           env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("'inbox': 1", p.stdout)                         # old2 went; old1 stayed with its new row
        self.assertIn("01RNLATE", self.rows_left())
        self.assertTrue((inbox / "old1").is_dir())
        self.assertFalse((inbox / "old2").exists())
        rc, res = self.run_fetch("scan")                              # old1 already fetched: not new
        self.assertEqual((rc, res), (0, []))

    def test_vector_matches_relay(self):
        fetch = load("rn_fetch", HERE / "rn-fetch.py")
        body = rr.fwd_body(KEY, "01JTESTMSG0000000000000000", "demo/claude-session", "claude-code", "", "你好\n x ",
                           ts=1700000000)
        f, text, err = fetch.parse(body.strip(), KEY)
        self.assertIsNone(err)
        self.assertEqual(text, "你好\n x ")
        self.assertEqual(f["sig"], "a48eb3bbb089a3d6b31fed070d76063bb3b3ddabfac08b7651f4093ce358c576")


FAKE_RN = r'''#!/usr/bin/env python3
# fake reason-network for test_rn_fetch: records every send; on RN-REFWD acts like the Mac relay
import json, os, sqlite3, sys, time, importlib.util
args, body = sys.argv[1:], sys.stdin.read()
log = os.environ["FAKE_LOG"]
with open(log, "a") as fh:
    fh.write(json.dumps({"args": args, "body": body}) + "\n")
mode = os.environ.get("FAKE_MODE", "resend")
if args[:1] == ["send"] and body.startswith("RN-REFWD v1"):
    spec = importlib.util.spec_from_file_location("rr", os.environ["FAKE_RELAY"])
    rr = importlib.util.module_from_spec(spec); spec.loader.exec_module(rr)
    key = open(os.environ["RN_KEY_FILE"]).read().strip()
    f = rr.parse_refwd(body)
    assert rr.hmac.compare_digest(f["sig"], rr.sign_refwd(key, f["id"], f["ts"], f["nonce"]))
    db = sqlite3.connect(os.path.join(os.environ["REASON_NETWORK_HOME"], "network.db"))
    n = int(time.time() * 1000)
    def put(b, k):
        db.execute("INSERT INTO messages (id, sender, recipient, body, wake, sent_at) VALUES (?,?,?,?,1,?)",
                   (f"01FAKE{n}{k}", "demo/terminal", "demo/bot-inbox", b.strip(), n))
    if mode == "resend":
        put(rr.fwd_body(key, f["id"], "demo/other", "codex", "", "re-sent text " + f["id"]), "a")
    if mode != "silent":
        put(rr.refwd_ack_body(key, f["id"], "resent" if mode == "resend" else "no-copy", 1), "b")
    db.commit()
print("→ demo/bot: queued")
'''


class Refwd(T):
    def setUp(self):
        super().setUp()
        self.db.execute("CREATE TABLE outbox (id TEXT)")
        self.db.commit()
        rn = self.d / "fake-rn"
        rn.write_text(FAKE_RN); rn.chmod(0o755)
        self.log = self.d / "rn.log"
        self.env.update(RN_BIN=str(rn), FAKE_LOG=str(self.log), FAKE_RELAY=str(HERE.parent / "agent_relay.py"),
                        RN_CLOUD_NAME="bot-cloud", RN_RELAY_NAME="bot")

    def sends(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_missing_triggers_one_signed_refwd_and_completes(self):
        self.put(self.fwd("ok1", "already here"))
        rc, res = self.run_fetch("fetch", "--timeout", "2", "--refwd-timeout", "10", "ok1", "lost1")
        by = {r["id"]: r for r in res}
        self.assertEqual(rc, 0, res)
        self.assertEqual(by["lost1"]["status"], "complete")
        self.assertEqual(open(by["lost1"]["full"]).read(), "re-sent text lost1")
        self.assertEqual(by["lost1"]["refwd"], {"attempt": 1, "ack": "resent"})
        self.assertNotIn("refwd", by["ok1"])
        (s,) = self.sends()
        self.assertEqual(s["args"], ["send", "demo/bot", "--from", "demo/bot-cloud", "-"])
        self.assertTrue(s["body"].startswith("RN-REFWD v1\nid: lost1\n"))

    def test_box_cap_two_per_id_no_loop(self):
        self.env["FAKE_MODE"] = "silent"
        for k in range(3):
            rc, (r,) = self.run_fetch("fetch", "--timeout", "1", "--refwd-timeout", "2", "gone")
            self.assertEqual((rc, r["status"]), (4, "missing"))
        self.assertEqual(len(self.sends()), 2)
        self.assertIn("box limit", r["refwd"]["skipped"])

    def test_no_copy_ack_stops_waiting_early(self):
        self.env["FAKE_MODE"] = "nocopy"
        t0 = __import__("time").time()
        rc, (r,) = self.run_fetch("fetch", "--timeout", "1", "--refwd-timeout", "30", "nc")
        self.assertLess(__import__("time").time() - t0, 15)
        self.assertEqual((rc, r["status"], r["refwd"]["ack"]), (4, "missing", "no-copy"))
        self.assertIn("no-copy", r["reason"])

    def test_refwd_off_and_wait_unchanged(self):
        self.env["RN_REFWD"] = "0"
        rc, (r,) = self.run_fetch("fetch", "--timeout", "1", "x")
        self.assertEqual((rc, r["status"]), (4, "missing"))
        rc, (r,) = self.run_fetch("wait", "--timeout", "1", "y")
        self.assertEqual(self.sends(), [])

    def test_acks_never_show_as_mismatch_and_forged_acks_ignored(self):
        self.put(rr.refwd_ack_body(KEY, "z", "resent", 1))
        self.put(rr.refwd_ack_body("f" * 64, "z", "no-copy", 1))
        rc, res = self.run_fetch("scan")
        self.assertEqual((rc, res), (0, []))
        fetch = load("rn_fetch2", HERE / "rn-fetch.py")
        self.assertIsNone(fetch.parse_ack(rr.refwd_ack_body("f" * 64, "z", "x", 1), KEY))
        self.assertEqual(fetch.parse_ack(rr.refwd_ack_body(KEY, "z", "resent", 2), KEY)["attempt"], "2")

    def test_refwd_signature_matches_relay(self):
        fetch = load("rn_fetch3", HERE / "rn-fetch.py")
        body = fetch.refwd_body(KEY, "01JTESTMSG0000000000000000", ts=1700000000, nonce="00" * 12)
        f = rr.parse_refwd(body)
        self.assertEqual(f["sig"], rr.sign_refwd(KEY, f["id"], f["ts"], f["nonce"]))


GOT_VECTOR_SIG = "26666d313c6c3dae158d5f8b3a6228f2be554c6ec58401662647f51aca361518"


class Receipts(unittest.TestCase):
    """RN-GOT receipts: caps kept from RN-FWD, one signed receipt after fetch (only when the Mac is connected),
    done receipts, caps in RN-REFWD, and RN_GOT=0."""
    put, fwd, run_fetch, row = T.put, T.fwd, T.run_fetch, T.row

    def setUp(self):
        T.setUp(self)
        self.addCleanup(self.db.close)
        self.db.execute("CREATE TABLE outbox (id TEXT)")
        self.db.commit()
        rn = self.d / "fake-rn"
        rn.write_text(FAKE_RN)
        rn.chmod(0o755)
        self.log = self.d / "rn.log"
        self.env.update(RN_BIN=str(rn), FAKE_LOG=str(self.log), FAKE_RELAY=str(HERE.parent / "agent_relay.py"),
                        RN_CLOUD_NAME="bot-cloud", RN_RELAY_NAME="bot", RN_REFWD="0",
                        RN_THREADS_DIR=str(self.d / "threads"))
        self.fetch_mod = load("rn_fetch_got", HERE / "rn-fetch.py")

    def legacy(self, mid, text):
        body = self.fwd(mid, text)
        self.assertIn("\ncaps: got1\n", body)
        return body.replace("\ncaps: got1\n", "\n", 1)

    def receipts(self):
        """Every RN-GOT sent, checked against the relay's parser and signature."""
        out = []
        for s in (json.loads(l) for l in self.log.read_text().splitlines()) if self.log.exists() else []:
            if s["body"].startswith("RN-GOT v1"):
                f = rr.parse_got(s["body"])
                self.assertTrue(f, s["body"])
                self.assertEqual(f["sig"], rr.sign_got(KEY, f["stored"], f["done"], f["ts"], f["nonce"]))
                self.assertLessEqual(len(f["stored_ids"]) + len(f["done_ids"]), 100)
                out.append((s["args"], f["stored_ids"], f["done_ids"]))
        return out

    def meta(self, mid):
        return json.loads((self.d / "inbox" / mid / "meta.json").read_text())

    def test_signature_matches_relay(self):
        self.assertEqual(self.fetch_mod.sign_got(KEY, "01JTESTMSG0000000000000000", "01JTESTMSG0000000000000001",
                                                 "1700000000", "0123456789abcdef01234567"), GOT_VECTOR_SIG)
        f = rr.parse_got(self.fetch_mod.got_body(KEY, ["a", "b"], ["c"]))
        self.assertEqual((f["stored_ids"], f["done_ids"]), (["a", "b"], ["c"]))
        self.assertEqual(f["sig"], rr.sign_got(KEY, f["stored"], f["done"], f["ts"], f["nonce"]))

    def test_caps_kept_in_meta_and_added_by_a_later_copy(self):
        self.put(self.legacy("a", "same text"))
        self.put(self.fwd("b", "new relay"))
        rc, res = self.run_fetch("scan", "a", "b")
        self.assertEqual(rc, 0, res)
        self.assertEqual((self.meta("a")["caps"], self.meta("b")["caps"]), ([], ["got1"]))
        self.put(self.fwd("a", "same text"))
        self.run_fetch("scan", "a")
        self.assertEqual(self.meta("a")["caps"], ["got1"])

    def test_fetch_sends_one_receipt_stored_or_done(self):
        self.env["RN_GOT"] = "0"
        self.put(self.fwd("h", "handled before"))
        self.run_fetch("fetch", "--timeout", "1", "h")
        self.assertEqual(subprocess.run([sys.executable, str(HERE / "rn-fetch.py"), "done", "h"], env=self.env,
                                        capture_output=True, timeout=60).returncode, 0)
        self.assertEqual(self.receipts(), [])
        del self.env["RN_GOT"]
        self.put(self.fwd("c", "complete"))
        self.put(self.legacy("L", "old relay copy"))
        self.row("01RCORPHAN000000000000000", self.fwd("o", "orphan"), int(time.time() * 1000))
        rc, res = self.run_fetch("fetch", "--timeout", "1", "--got", "c", "h", "L")
        by = {r["id"]: r for r in res}
        self.assertEqual(rc, 0, res)
        self.assertEqual({i: (r["status"], r.get("got")) for i, r in by.items()},
                         {"c": ("complete", "stored"), "h": ("handled", "done"), "L": ("complete", None),
                          "o": ("complete", "stored")})
        self.assertTrue(by["o"].get("orphan"))
        ((args, stored, done),) = self.receipts()
        self.assertEqual(args, ["send", "demo/bot", "-"])
        self.assertEqual((sorted(stored), done), (["c", "o"], ["h"]))
        rc, res = self.run_fetch("fetch", "--timeout", "1", "--got", "c")
        self.assertEqual([(r["status"], r.get("got")) for r in res], [("claimed", "stored")])
        self.assertEqual(self.receipts()[-1][1:], (["c"], []))

    def test_uncertain_and_reclaimed_ids_are_reported_as_stored(self):
        for mid in ("W", "R", "C"):
            self.put(self.fwd(mid, f"text {mid}"))
        rc, _ = self.run_fetch("fetch", "--timeout", "1", "W", "R", "C")
        self.assertEqual(rc, 0)
        for name, mid in (("working", "W"), ("replying", "R")):
            p = subprocess.run([sys.executable, str(HERE / "rn-fetch.py"), name, mid], env=self.env,
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr)
        t = time.time() - 4000
        for mid, names in (("W", ("claimed", "working")), ("R", ("claimed", "replying")), ("C", ("claimed",))):
            for name in names:
                os.utime(self.d / "inbox" / mid / name, (t, t))
        self.assertEqual(self.receipts(), [])
        rc, res = self.run_fetch("fetch", "--timeout", "1", "--got", "W", "R", "C")
        by = {r["id"]: r for r in res}
        self.assertEqual(rc, 0, res)
        self.assertEqual({i: (r["status"], r.get("claim"), r.get("got")) for i, r in by.items()},
                         {"W": ("uncertain", None, "stored"), "R": ("uncertain", None, "stored"),
                          "C": ("complete", "reclaimed", "stored")})
        ((args, stored, done),) = self.receipts()
        self.assertEqual((sorted(stored), done), (["C", "R", "W"], []))

    def test_receipts_split_above_100_ids(self):
        ids = [f"m{k:03d}" for k in range(150)]
        for i in ids:
            self.put(self.fwd(i, "x"))
        rc, res = self.run_fetch("fetch", "--timeout", "1", "--got")
        self.assertEqual((rc, len(res)), (0, 150))
        got = self.receipts()
        self.assertEqual([len(s) for _, s, _ in got], [100, 50])
        self.assertEqual(sorted(i for _, s, _ in got for i in s), ids)

    def test_no_receipt_without_the_flag_or_with_receipts_off(self):
        self.put(self.fwd("a", "x"))
        rc, res = self.run_fetch("fetch", "--timeout", "1", "a")
        self.assertEqual((rc, res[0]["status"], "got" in res[0]), (0, "complete", False))
        self.env["RN_GOT"] = "0"
        self.put(self.fwd("b", "x"))
        rc, res = self.run_fetch("fetch", "--timeout", "1", "--got", "b")
        self.assertEqual((rc, "got" in res[0]), (0, False))
        self.assertEqual(self.run_fetch("done", "a")[0], 0)
        self.assertEqual(self.receipts(), [])

    def test_done_sends_a_done_receipt_for_caps_ids_and_alert_batches(self):
        self.put(self.fwd("a", "x"))
        self.put(self.legacy("L", "x"))
        self.run_fetch("fetch", "--timeout", "1", "a", "L")
        rc, _ = self.run_fetch("done", "a", "L", "nofolder", "relay-alerts-9")
        self.assertEqual(rc, 0)
        self.assertEqual(self.receipts(), [(["send", "demo/bot", "-"], [], ["a", "relay-alerts-9"])])
        rc, _ = self.run_fetch("done", "a", "--via", "send")
        self.assertEqual((rc, len(self.receipts())), (0, 1))

    def test_done_fails_only_when_an_alert_batch_receipt_was_not_sent(self):
        self.put(self.fwd("a", "x"))
        self.run_fetch("fetch", "--timeout", "1", "a")
        self.env["RN_BIN"] = str(self.d / "no-such-engine")
        p = subprocess.run([sys.executable, str(HERE / "rn-fetch.py"), "done", "a"], env=self.env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("receipt (RN-GOT) not sent", p.stderr)
        self.assertTrue((self.d / "inbox" / "a" / "done").exists())
        p = subprocess.run([sys.executable, str(HERE / "rn-fetch.py"), "done", "relay-alerts-9"], env=self.env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 1)
        self.assertIn("sends it again later", p.stderr)

    def test_refwd_request_declares_caps_unless_off(self):
        self.env.update(RN_REFWD="1", FAKE_MODE="silent")
        for off, want in ((False, True), (True, False)):
            if off:
                self.env["RN_GOT"] = "0"
            self.log.unlink(missing_ok=True)
            self.run_fetch("fetch", "--timeout", "1", "--refwd-timeout", "1", f"lost{off}")
            (s,) = [json.loads(l) for l in self.log.read_text().splitlines()]
            self.assertEqual(s["body"].endswith("\ncaps: got1"), want, s["body"])
            f = rr.parse_refwd(s["body"])
            self.assertEqual(f["sig"], rr.sign_refwd(KEY, f["id"], f["ts"], f["nonce"]))
            self.assertEqual(rr.has_caps(f.get("caps")), want)


def fetch_after_barrier(barrier, env, mid, queue):
    """One of two real processes that start `fetch` at the same moment (module level so spawn can import it)."""
    barrier.wait()
    p = subprocess.run([sys.executable, str(HERE / "rn-fetch.py"), "fetch", "--timeout", "5", mid], env=env,
                       capture_output=True, text=True, timeout=60)
    queue.put((p.returncode, p.stdout))


class Claims(unittest.TestCase):
    """Idempotent consumption (claim markers, one-time migration) and the orphan scan."""
    put, fwd, run_fetch, row = T.put, T.fwd, T.run_fetch, T.row

    def setUp(self):
        T.setUp(self)
        self.addCleanup(self.db.close)
        self.env.update(RN_REFWD="0", RN_THREADS_DIR=str(self.d / "threads"))  # no engine: never ask the relay
        self.inbox = self.d / "inbox"

    def recent(self, mid, text="hello", age=0, key=KEY):
        """An RN-FWD row sent `age` seconds ago (put() rows date from 1970, outside the orphan window)."""
        self.n += 1
        self.row(f"01RC{self.n:022d}", self.fwd(mid, text, key=key), int((time.time() - age) * 1000))

    def fetch(self, *ids, timeout="1"):
        rc, res = self.run_fetch("fetch", "--timeout", timeout, *ids)
        by = {r["id"]: r for r in res}
        self.assertEqual(len(by), len(res), res)                      # never two results for one id
        return rc, by

    def mark(self, name, *ids):
        return subprocess.run([sys.executable, str(HERE / "rn-fetch.py"), name, *ids], env=self.env,
                              capture_output=True, text=True, timeout=60)

    def age(self, mid, *names, by=4000):
        t = time.time() - by
        for name in names:
            os.utime(self.inbox / mid / name, (t, t))

    def old_scan(self, *ids):
        """What the previous version left behind: inbox folders written by scan(), no markers, no migration."""
        driver = ("import importlib.util, sys\n"
                  "spec = importlib.util.spec_from_file_location('rnf', sys.argv[1]); m = importlib.util.module_from_spec(spec)\n"
                  "spec.loader.exec_module(m)\n"
                  "m.scan(sys.argv[3:], open(sys.argv[2]).read().strip(), 'demo', 'bot-inbox')\n")
        subprocess.run([sys.executable, "-c", driver, str(HERE / "rn-fetch.py"), str(self.d / "key"), *ids],
                       env=self.env, check=True, capture_output=True, timeout=60)

    def crash_before_settle(self, *args):
        """Runs main() but dies after store() and before the claim is written."""
        driver = ("import importlib.util, sys\n"
                  "spec = importlib.util.spec_from_file_location('rnf', sys.argv[1]); m = importlib.util.module_from_spec(spec)\n"
                  "spec.loader.exec_module(m)\n"
                  "def boom(res): raise SystemExit(9)\n"
                  "m.settle = boom\n"
                  "sys.exit(m.main(sys.argv[2:]))\n")
        p = subprocess.run([sys.executable, "-c", driver, str(HERE / "rn-fetch.py"), *args], env=self.env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual((p.returncode, p.stdout), (9, ""))

    def test_claim_arrivals_in_different_polls(self):
        self.put(self.fwd("A", "first"))
        p = subprocess.Popen([sys.executable, str(HERE / "rn-fetch.py"), "fetch", "--timeout", "20", "A", "B"],
                             env=self.env, stdout=subprocess.PIPE, text=True)
        time.sleep(3)                                                  # polls at 0, 2, 4 s: B lands before the third
        self.put(self.fwd("B", "second"))
        out, _ = p.communicate(timeout=60)
        by = {r["id"]: r for r in map(json.loads, out.splitlines())}
        self.assertEqual(p.returncode, 0, out)
        for mid in "AB":
            self.assertEqual((by[mid]["status"], by[mid]["claim"]), ("complete", "new"))
            self.assertTrue((self.inbox / mid / "claimed").exists())

    def test_claim_one_missing_until_timeout(self):
        self.put(self.fwd("A", "here"))
        rc, by = self.fetch("A", "B", timeout="2")
        self.assertEqual(rc, 4)
        self.assertEqual((by["A"]["status"], by["A"]["claim"], by["B"]["status"]), ("complete", "new", "missing"))
        claim = json.loads((self.inbox / "A" / "claimed").read_text())
        self.assertEqual(claim["id"], "A")
        self.assertTrue(claim["token"])

    def test_second_run_sees_the_claim(self):
        self.put(self.fwd("A", "here"))
        self.assertEqual(self.fetch("A")[1]["A"]["status"], "complete")
        rc, by = self.fetch("A")
        self.assertEqual((rc, by["A"]["status"]), (0, "claimed"))
        self.assertNotIn("full", by["A"])
        self.assertIn("claimed_at", by["A"])

    def test_two_concurrent_processes_claim_once(self):
        import multiprocessing
        self.put(self.fwd("A", "here"))
        ctx = multiprocessing.get_context("spawn")
        barrier, queue = ctx.Barrier(2), ctx.Queue()
        procs = [ctx.Process(target=fetch_after_barrier, args=(barrier, self.env, "A", queue)) for _ in range(2)]
        for p in procs:
            p.start()
        got = [queue.get(timeout=90) for _ in procs]
        for p in procs:
            p.join(30)
        self.assertEqual([rc for rc, _ in got], [0, 0])
        self.assertEqual(sorted(json.loads(o)["status"] for _, o in got), ["claimed", "complete"])

    def test_expired_claim_is_reclaimed(self):
        self.put(self.fwd("A", "here"))
        self.fetch("A")
        self.age("A", "claimed")
        rc, by = self.fetch("A")
        self.assertEqual((rc, by["A"]["status"], by["A"]["claim"]), (0, "complete", "reclaimed"))
        self.assertIn("full", by["A"])
        self.assertEqual(self.fetch("A")[1]["A"]["status"], "claimed")    # the new claim holds again

    def test_empty_claimed_file_still_counts(self):
        self.put(self.fwd("A", "here"))
        (self.inbox / "A").mkdir(parents=True)
        (self.inbox / "A" / "claimed").write_text("")
        self.assertEqual(self.fetch("A")[1]["A"]["status"], "claimed")
        self.age("A", "claimed")
        self.assertEqual(self.fetch("A")[1]["A"]["claim"], "reclaimed")

    def test_done_makes_it_handled(self):
        self.put(self.fwd("A", "here"))
        self.fetch("A")
        p = self.mark("done", "A")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads((self.inbox / "A" / "done").read_text())["id"], "A")
        rc, by = self.fetch("A")
        self.assertEqual((rc, by["A"]["status"]), (0, "handled"))
        self.assertNotIn("full", by["A"])

    def test_unfinished_reply_becomes_uncertain_after_the_lease(self):
        self.put(self.fwd("A", "here"))
        self.fetch("A")
        self.assertEqual(self.mark("replying", "A").returncode, 0)
        self.assertEqual(self.fetch("A")[1]["A"]["status"], "claimed")
        self.age("A", "claimed", "replying")
        rc, by = self.fetch("A")
        self.assertEqual((rc, by["A"]["status"]), (0, "uncertain"))
        self.assertEqual(Path(by["A"]["full"]).read_text(), "here")
        self.assertIn("replying_at", by["A"])

    def test_unfinished_work_is_uncertain_not_reclaimed(self):
        self.recent("A", "work")
        self.fetch("A")
        self.assertEqual(self.mark("working", "A").returncode, 0)    # then the turn dies: no replying, no done
        rc, by = self.fetch("A")
        self.assertEqual(by["A"]["status"], "claimed")
        self.assertNotIn("A", self.fetch("X")[1])                     # held within the lease: no orphan either
        self.age("A", "claimed", "working")
        rc, by = self.fetch("X")                                       # orphan scan
        self.assertEqual((by["A"]["status"], by["A"]["orphan"]), ("uncertain", True))
        self.assertIn("working_at", by["A"])
        rc, by = self.fetch("A")                                       # explicit doorbell
        self.assertEqual(by["A"]["status"], "uncertain")
        self.assertNotIn("claim", by["A"])

    def test_crash_after_claim_before_working_is_reclaimed(self):
        self.recent("A", "work")
        self.fetch("A")
        self.age("A", "claimed")
        rc, by = self.fetch("X")
        self.assertEqual((by["A"]["status"], by["A"]["claim"], by["A"]["orphan"]), ("complete", "reclaimed", True))

    def test_legacy_claim_then_crash_is_reclaimed(self):
        self.put(self.fwd("A", "old"))
        self.old_scan("A")
        rc, by = self.fetch("A")                                       # first run: migration marks A legacy
        self.assertEqual((by["A"]["status"], by["A"]["claim"]), ("complete", "legacy"))
        self.age("A", "claimed")                                       # the turn died before working
        rc, by = self.fetch("A")
        self.assertEqual((by["A"]["status"], by["A"]["claim"]), ("complete", "reclaimed"))

    def test_interrupted_migration_resumes_without_relabelling_claims(self):
        self.recent("P", "p")
        self.recent("Q", "q")
        self.old_scan("P", "Q")
        driver = ("import importlib.util, sys\n"
                  "spec = importlib.util.spec_from_file_location('rnf', sys.argv[1]); m = importlib.util.module_from_spec(spec)\n"
                  "spec.loader.exec_module(m)\n"
                  "real, calls = m.put_marker, []\n"
                  "def flaky(p, c):\n"
                  "    calls.append(p)\n"
                  "    if len(calls) == 2: raise SystemExit(9)\n"
                  "    real(p, c)\n"
                  "m.put_marker = flaky\n"
                  "m.migrate()\n")
        p = subprocess.run([sys.executable, "-c", driver, str(HERE / "rn-fetch.py")], env=self.env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 9, p.stderr)
        self.assertFalse((self.inbox / "_claims_v1").exists())
        self.assertEqual(sorted(d.name for d in self.inbox.iterdir() if (d / "legacy").exists()), ["P"])
        (self.inbox / "N").mkdir()                                     # stored and claimed by the new version
        (self.inbox / "N" / "claimed").write_text(json.dumps({"id": "N", "token": "t", "ts": 1}))
        self.fetch("X")
        self.assertTrue((self.inbox / "_claims_v1").exists())
        self.assertTrue((self.inbox / "P" / "legacy").exists())
        self.assertTrue((self.inbox / "Q" / "legacy").exists())
        self.assertFalse((self.inbox / "N" / "legacy").exists())

    def test_crash_after_store_before_claim_is_handed_out_next_time(self):
        self.recent("A", "a")
        self.crash_before_settle("fetch", "--timeout", "1", "A")
        self.assertTrue((self.inbox / "A" / "full.txt").exists())
        rc, by = self.fetch("X")                                       # as an orphan
        self.assertEqual((by["A"]["status"], by["A"]["claim"], by["A"]["orphan"]), ("complete", "new", True))
        self.recent("B", "b")
        self.crash_before_settle("fetch", "--timeout", "1", "B")
        rc, by = self.fetch("B")                                       # by its doorbell
        self.assertEqual((by["B"]["status"], by["B"]["claim"]), ("complete", "new"))

    def test_scan_then_orphan_scan_hands_it_out_once(self):
        self.fetch("X")                                                # already upgraded
        self.recent("B", "b")
        rc, res = self.run_fetch("scan")                               # diagnostic: stores, claims nothing
        self.assertEqual([r["id"] for r in res], ["B"])
        self.assertFalse((self.inbox / "B" / "claimed").exists())
        rc, by = self.fetch("X")
        self.assertEqual((by["B"]["status"], by["B"]["orphan"]), ("complete", True))
        self.assertNotIn("B", self.fetch("X")[1])

    def test_migration_marks_old_folders_legacy_once(self):
        self.recent("A", "a")
        self.recent("B", "b")
        self.old_scan()
        rc, by = self.fetch("X")
        self.assertEqual(sorted(by), ["X"])                            # old folders are not orphans
        for mid in "AB":
            self.assertTrue((self.inbox / mid / "legacy").exists())
            self.assertFalse((self.inbox / mid / "done").exists())
        self.assertTrue((self.inbox / "_claims_v1").exists())
        self.recent("C", "c")
        self.old_scan("C")                                             # unmarked folder after the migration
        rc, by = self.fetch("X")
        self.assertFalse((self.inbox / "C" / "legacy").exists())
        self.assertEqual((by["C"]["status"], by["C"]["claim"], by["C"]["orphan"]), ("complete", "new", True))

    def test_old_scan_then_upgrade_doorbell_is_legacy_not_handled(self):
        self.recent("B", "b")
        self.old_scan("B")
        rc, by = self.fetch("B")
        self.assertEqual((rc, by["B"]["status"], by["B"]["claim"]), (0, "complete", "legacy"))

    def test_old_stored_unhandled_without_doorbell_stays_until_its_doorbell(self):
        self.recent("B", "b")
        self.old_scan("B")
        self.assertNotIn("B", self.fetch("X")[1])                      # known limit: not an orphan
        rc, by = self.fetch("B")
        self.assertEqual((by["B"]["status"], by["B"]["claim"]), ("complete", "legacy"))

    def test_orphan_reported_once_then_claimed_then_handled(self):
        self.recent("B", "lost doorbell")
        rc, by = self.fetch("X")
        self.assertEqual(rc, 4)                                        # X is missing; the orphan does not change that
        self.assertEqual((by["B"]["status"], by["B"]["orphan"]), ("complete", True))
        self.assertEqual(Path(by["B"]["full"]).read_text(), "lost doorbell")
        self.assertNotIn("B", self.fetch("X")[1])
        self.assertEqual(self.fetch("B")[1]["B"]["status"], "claimed")
        self.assertEqual(self.mark("done", "B").returncode, 0)
        self.age("B", "claimed", "done")
        self.assertNotIn("B", self.fetch("X")[1])
        self.assertEqual(self.fetch("B")[1]["B"]["status"], "handled")

    def test_orphan_window(self):
        self.recent("OLD", "too old", age=8 * 86400)
        self.recent("NEW", "inside", age=6 * 86400)
        rc, by = self.fetch("X")
        self.assertEqual(sorted(by), ["NEW", "X"])
        self.env["RN_ORPHAN_DAYS"] = "9"
        self.assertIn("OLD", self.fetch("X")[1])
        self.env["RN_ORPHANS"] = "0"
        self.recent("OFF", "not scanned")
        self.assertEqual(sorted(self.fetch("X")[1]), ["X"])

    def test_bad_rows_reported_once_by_the_orphan_scan(self):
        self.recent("M", "forged", key="f" * 64)
        self.n += 1
        self.row("01RCPLAIN", "plain text to the inbox", int(time.time() * 1000))
        rc, by = self.fetch("X")
        self.assertEqual(rc, 5)
        self.assertIn("bad signature", by["M"]["reason"])
        self.assertEqual((by["rn:01RCPLAIN"]["status"], by["rn:01RCPLAIN"]["orphan"]), ("mismatch", True))
        self.assertEqual(len(list((self.inbox / "_mismatch").iterdir())), 2)
        rc, by = self.fetch("X")
        self.assertEqual((rc, sorted(by)), (4, ["X"]))
        for _ in range(2):                                             # asked for by id: every time
            rc, by = self.fetch("M")
            self.assertEqual((rc, by["M"]["status"]), (5, "mismatch"))
            self.assertNotIn("orphan", by["M"])

    def test_bad_rows_reported_once_by_fetch_and_wait_without_ids(self):
        self.recent("M", "forged", key="f" * 64)
        self.recent("M", "forged again", key="f" * 64)                # two bad rows, one id
        self.row("01RCPLAIN", "plain text to the inbox", int(time.time() * 1000))
        rc, res = self.run_fetch("fetch", "--timeout", "1")
        self.assertEqual(rc, 5)
        self.assertEqual(sorted(r["id"] for r in res), ["M", "rn:01RCPLAIN"])
        for cmd in ("fetch", "wait"):
            rc, res = self.run_fetch(cmd, "--timeout", "1")
            self.assertEqual((rc, res), (0, []), cmd)
        rc, by = self.fetch("X")                                       # nor through the orphan scan
        self.assertEqual((rc, sorted(by)), (4, ["X"]))
        rc, res = self.run_fetch("scan")                               # the diagnostic scan still shows them
        self.assertEqual((rc, sorted(r["id"] for r in res)), (5, ["M", "rn:01RCPLAIN"]))
        for _ in range(2):                                             # asked for by id: every time
            rc, by = self.fetch("M")
            self.assertEqual((rc, by["M"]["status"]), (5, "mismatch"))

    def test_bad_row_reported_by_the_orphan_scan_stays_quiet_without_ids(self):
        self.recent("N", "forged", key="f" * 64)
        rc, by = self.fetch("X")
        self.assertEqual((rc, by["N"]["status"]), (5, "mismatch"))
        for cmd in ("fetch", "wait"):
            rc, res = self.run_fetch(cmd, "--timeout", "1")
            self.assertEqual((rc, res), (0, []), cmd)
        rc, by = self.fetch("N")
        self.assertEqual((rc, by["N"]["status"]), (5, "mismatch"))

    def test_doorbell_ids_are_not_also_orphans(self):
        self.recent("A", "a")
        self.recent("B", "b")                                          # no doorbell: an orphan
        rc, res = self.run_fetch("fetch", "--timeout", "1", "A")
        self.assertEqual([r["id"] for r in res], ["A", "B"])          # A once, from its doorbell
        self.assertNotIn("orphan", res[0])
        self.assertEqual((res[0]["claim"], res[1]["orphan"]), ("new", True))

    def test_id_collision(self):
        self.put(self.fwd("A", "a"))
        (self.inbox / "A").mkdir(parents=True)
        (self.inbox / "A" / "claimed").write_text(json.dumps({"id": "other", "token": "t", "ts": 1}))
        rc, by = self.fetch("A")
        self.assertEqual((rc, by["A"]["status"]), (5, "mismatch"))
        self.assertIn("id collision", by["A"]["reason"])
        p = self.mark("done", "A")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("id collision", p.stderr)
        self.assertFalse((self.inbox / "A" / "done").exists())

    def test_purge_removes_markers_and_remembered_bad_rows(self):
        self.recent("A", "a")
        self.recent("M", "forged", key="f" * 64)
        self.fetch("X")
        self.assertEqual(self.mark("done", "A").returncode, 0)
        rc, res = self.run_fetch("purge", "0")
        self.assertEqual(res[0]["purged"], {"messages": 2, "inbox": 1, "stray": 0, "threads": 0, "mismatch": 1})
        self.assertEqual(sorted(p.name for p in self.inbox.iterdir()), ["_claims_v1", "_lock", "_mismatch"])
        self.assertEqual(list((self.inbox / "_mismatch").iterdir()), [])
        rc, by = self.fetch("X")
        self.assertEqual((rc, sorted(by)), (4, ["X"]))

    def test_exit_codes_ignore_handled_claimed_uncertain(self):
        for mid in "HCU":
            self.put(self.fwd(mid, mid))
        self.fetch("H", "C", "U")
        self.mark("done", "H")
        self.mark("replying", "U")
        self.age("U", "claimed", "replying")
        rc, by = self.fetch("H", "C", "U")
        self.assertEqual(rc, 0)
        self.assertEqual([by[m]["status"] for m in "HCU"], ["handled", "claimed", "uncertain"])
        rc, by = self.fetch("H", "C", "U", "MISS")
        self.assertEqual((rc, by["MISS"]["status"]), (4, "missing"))


FAKE_ENGINE = r'''#!/usr/bin/env python3
# fake reason-network for rn-cloud.sh: always connected; records calls; FAKE_SEND picks what `send` does
import json, os, sqlite3, subprocess, sys, time
args = sys.argv[1:]
body = sys.stdin.read() if args[:1] == ["send"] else ""
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(json.dumps({"args": args, "body": body}) + "\n")
if args[:1] == ["doctor"]:
    print("peers: 0 connected" if os.environ.get("FAKE_OFFLINE") else "peers: 1 connected")
elif args[:1] == ["listen"]:
    print("No messages.")
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
    print("-> demo/bot: queued")
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
        engine = self.box / "bin" / "reason-network"
        engine.write_text(FAKE_ENGINE.replace("#!/usr/bin/env python3", "#!" + sys.executable, 1))
        engine.chmod(0o755)
        db = sqlite3.connect(self.box / "state" / "network.db")
        db.executescript(SCHEMA + "CREATE TABLE outbox (id TEXT);")
        db.close()
        tools = self.d / "tools"
        tools.mkdir()
        for name, script in (("uname", 'echo Linux'), ("sleep", "exit 0")):    # sleep: skip the 60 s waits
            (tools / name).write_text("#!/bin/sh\n" + script + "\n")
            (tools / name).chmod(0o755)
        (tools / "python3").symlink_to(sys.executable)
        self.log = self.d / "rn.log"
        self.env = dict(os.environ, PATH=f"{tools}{os.pathsep}{os.environ['PATH']}", RN_CLOUD_BASE=str(self.box),
                        RN_INBOX_DIR=str(self.inbox), FAKE_LOG=str(self.log))
        for k in ("RN_PERSON", "RN_KEY_FILE", "REASON_NETWORK_HOME", "RN_DONE_LOCK_WAIT", "RN_CLAIM_LEASE"):
            self.env.pop(k, None)

    def cloud(self, *args, body=""):
        return subprocess.run(["bash", str(HERE / "rn-cloud.sh"), *args], env=self.env, input=body,
                              capture_output=True, text=True, timeout=60)

    def engine_sends(self):
        calls = [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []
        return [c for c in calls if c["args"][:1] == ["send"]]

    def put_fwd(self, mid, text):
        db = sqlite3.connect(self.box / "state" / "network.db")
        db.execute("INSERT INTO messages (id, sender, recipient, body, wake, sent_at) VALUES (?,?,?,?,1,?)",
                   (f"01CL{mid}", "demo/terminal", "demo/bot-inbox", rr.fwd_body(KEY, mid, "demo/other", "codex", "",
                                                                           text).strip(), int(time.time() * 1000)))
        db.commit()
        db.close()

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
