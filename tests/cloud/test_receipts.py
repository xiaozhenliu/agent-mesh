import json, os, subprocess, sys, time, unittest

from tests.cloud.support import (
    HERE,
    KEY,
    load,
    rr,
    FetchFixture,
    FAKE_RN,
)


GOT_VECTOR_SIG = "26666d313c6c3dae158d5f8b3a6228f2be554c6ec58401662647f51aca361518"


class Receipts(FetchFixture, unittest.TestCase):
    """RN-GOT receipts: caps kept from RN-FWD, one signed receipt after fetch (only when the Mac is connected),
    done receipts, caps in RN-REFWD, and RN_GOT=0."""

    def setUp(self):
        super().setUp()
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
