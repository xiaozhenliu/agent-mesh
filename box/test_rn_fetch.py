#!/usr/bin/env python3
"""Offline checks for rn-fetch.py against a fake engine database (same messages schema as Reason Network
v0.10.5 src/store.rs) and the relay's own RN-FWD encoder (../agent_relay.py), plus the RN-REFWD re-forward flow
against a fake reason-network binary. Run: python3 test_rn_fetch.py"""
import importlib.util, json, os, sqlite3, subprocess, sys, tempfile, unittest
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
