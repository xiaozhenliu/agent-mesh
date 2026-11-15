import json, sqlite3, tempfile, time, unittest
from unittest import mock
from pathlib import Path

from tests.relay.support import (
    tmp,
    rr,
    REAL_WEBHOOK_OPEN,
    KEY,
    CFG,
    LIMIT,
    FWD_VECTOR_SIG,
    clean_state,
    FakeResp,
    envelope,
    RunOK,
    RunOut,
    Crash,
    SCHEMA,
    prev_state_class,
    refwd_req,
)


GOT_VECTOR_SIG = "26666d313c6c3dae158d5f8b3a6228f2be554c6ec58401662647f51aca361518"


class FakeCloud:
    """The cloud's claim rules (rn-fetch.py settle()) for one id: a claim holds for `lease` seconds."""
    def __init__(self, clock, lease=3600):
        self.clock, self.lease, self.claims, self.done = clock, lease, {}, set()

    def ring(self, ids, then):
        """then: "done" (claims, reports stored, handles, reports done), "stored" (claims and reports stored),
        "crash" (claims, dies before any receipt). Returns the receipts as (stored, done) pairs."""
        out, now = [], self.clock()
        for i in ids:
            if i in self.done:
                out.append(((), (i,)))
            elif i in self.claims and now - self.claims[i] < self.lease:
                out.append(((i,), ()))
            else:
                self.claims[i] = now
                if then == "crash":
                    continue
                out.append(((i,), ()))
                if then == "done":
                    self.done.add(i)
                    out.append(((), (i,)))
        return out


class GotReceipts(unittest.TestCase):
    """RN-GOT receipts, pending messages, re-rings and durable alerts (doc: receipts + relay timeout re-ring)."""
    TO, DONE, MAXR = 900, 7200, 2

    def setUp(self):
        self.addCleanup(setattr, rr, "webhook_open", REAL_WEBHOOK_OPEN)
        clean_state()
        self.now = time.time()
        clock = mock.patch.object(rr.time, "time", lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        real_alert = rr.State.alert
        self.alert_keys = []
        def spy(state, key, text):
            self.alert_keys.append(key)
            return real_alert(state, key, text)
        patch = mock.patch.object(rr.State, "alert", spy)
        patch.start()
        self.addCleanup(patch.stop)
        self.cfg = dict(CFG, GOT_TIMEOUT_SECONDS=self.TO, GOT_DONE_SECONDS=self.DONE, GOT_MAX_RERINGS=self.MAXR)
        self.calls, self.posts, self.code, self.result, self.hook = [], [], 200, RunOK(), None
        rr.webhook_open = self.post
        self.evidence, self.keeps, self.prev, self.k = set(), set(), {}, 0
        self.cloud = FakeCloud(lambda: self.now)
        self.r = self.relay()

    def relay(self):
        r = rr.Relay(dict(self.cfg))
        r.rn = self.rn
        return r

    def restart(self, **cfg):
        self.cfg.update(cfg)
        self.r = self.relay()
        self.inv()

    def rn(self, *a, **k):
        self.calls.append((a, k))
        return self.hook(a, k) if self.hook else self.result

    def post(self, req, timeout=None):
        if isinstance(self.code, Exception):
            raise self.code
        self.posts.append(json.loads(req.data.decode()))
        resp = FakeResp()
        resp.status = self.code
        return resp

    def p(self, mid):
        return self.r.s.pending.get(mid)

    def inv(self):
        """t is None exactly when the spool holds that id's doorbell; at most one doorbell per id; keep never
        goes back to false; a pending id only goes away with proof it was handled, with an alert written for it,
        or as a probe while receipts are off."""
        bells = [str(i["id"]) for i in self.r.s.spool if i.get("doorbell")]
        self.assertEqual(len(bells), len(set(bells)), bells)
        for mid, p in self.r.s.pending.items():
            self.assertEqual(p["t"] is None, mid in bells, (mid, p))
            if mid in self.keeps:
                self.assertTrue(p["keep"], mid)
            if p["keep"]:
                self.keeps.add(mid)
        for mid, p in self.prev.items():
            if mid not in self.r.s.pending:
                probe = p["stage"] == "unacked" and p["rung"] and not p["keep"] and not self.r.probing()
                self.assertTrue(mid in self.evidence or f"abandon:{mid}" in self.alert_keys or probe, (mid, p))
        self.prev = json.loads(json.dumps(self.r.s.pending))

    def inbound(self, *ids, frm="demo/other"):
        self.r.handle([{"id": i, "from": frm, "agent": "codex", "thread": None, "text": f"text of {i}"}
                       for i in ids])
        self.inv()

    def deliver(self):
        """POSTs whatever is due (no rate limit); returns the doorbell ids that went out."""
        start = len(self.posts)
        for _ in range(60):
            if not self.r.s.spool and not self.r.ready_alerts():
                break
            self.r.s.wakes = []
            self.r.flush()
            self.inv()
        return [m["id"] for b in self.posts[start:] if b["doorbell"] for m in b["messages"]]

    def tick(self, seconds):
        self.now += seconds
        self.r.check_pending()
        self.inv()

    def got(self, stored=(), done=(), frm="demo/terminal", key=KEY, ts=None, nonce=None):
        self.k += 1
        if key == KEY:
            self.evidence |= set(done)
        body = rr.got_body(key, list(stored), list(done), ts=int(self.now) if ts is None else ts, nonce=nonce)
        self.r.handle([{"id": f"got{self.k}", "from": frm, "agent": None, "thread": None, "text": body}])
        self.inv()

    def turn(self, ids, then="done"):
        for stored, done in self.cloud.ring(ids, then):
            self.got(stored, done)

    def fwds(self, mid):
        return [k for a, k in self.calls if (k.get("stdin") or "").startswith(f"RN-FWD v1\nid: {mid}\n")]

    def rerings(self, mid):
        return [m.get("rering") for b in self.posts if b["doorbell"] for m in b["messages"]
                if m["id"] == mid and "rering" in m]

    def overflow(self, mid):
        """Fills the spool until mid's doorbell is dropped (webhook down), then lets the backlog go out."""
        self.r.s.max_spool = 10
        while any(i.get("doorbell") and i["id"] == mid for i in self.r.s.spool):
            self.r.notice("filler")
            self.r.s.save()
        self.r.s.max_spool = self.cfg["MAX_SPOOL"]
        self.inv()

    def on_disk(self):
        return json.loads((Path(tmp) / "state.json").read_text())

    def test_vector_and_body(self):
        self.assertEqual(rr.sign_got(KEY, "01JTESTMSG0000000000000000", "01JTESTMSG0000000000000001", "1700000000",
                                     "0123456789abcdef01234567"), GOT_VECTOR_SIG)
        f = rr.parse_got(rr.got_body(KEY, ["a", "b"], [], ts=1, nonce="n"))
        self.assertEqual((f["stored_ids"], f["done_ids"], f["done"]), (["a", "b"], [], ""))
        for bad in ("RN-GOT v1\nstored: \ndone: \nts: 1\nnonce: n\nsig: s", "RN-GOT v1\nstored: a b\ndone: \nts: 1\n"
                    "nonce: n\nsig: s", "RN-GOT v1\ndone: a\nts: 1\nnonce: n\nsig: s",
                    rr.got_body(KEY, [f"i{k}" for k in range(101)], [])):
            self.assertEqual(rr.parse_got(bad), {}, bad)
        self.assertIsNone(rr.parse_got("hello"))

    def test_forged_stale_replayed_or_foreign_receipts_change_nothing(self):
        self.inbound("A")
        self.deliver()
        before = json.dumps([self.r.s.pending, self.r.s.alerts, self.r.s.spool, self.r.s.got_last])
        with self.assertLogs("agent-relay", "WARNING") as lg:
            self.got(done=["A"], key="f" * 64)
            self.got(done=["A"], ts=int(self.now) - 1000)
            self.got(done=["A"], frm="demo/other")
            self.got(done=["A"], frm="demo/terminal-x")
            self.got(done=["A"], frm="demo/terminal-3x")
            self.got(done=["A"], frm="evil/terminal-3")
            self.got(done=["A"], frm="demo/terminal@other")
            self.got(done=["A"], frm="demo/terminal@cloudx")
            self.got(done=["A"], frm="evil/terminal@cloud")
            self.got(done=["A"], frm="demo/terminal-3@cloud")
            self.r.handle([{"id": "bad", "from": "demo/bot-cloud", "agent": None, "thread": None,
                            "text": "RN-GOT v1\nstored: A"}])
        self.assertEqual(len(lg.output), 11)
        self.assertEqual(json.dumps([self.r.s.pending, self.r.s.alerts, self.r.s.spool, self.r.s.got_last]), before)
        self.got(stored=["A"], nonce="ab" * 12)
        self.assertEqual(self.p("A")["stage"], "stored")
        with self.assertLogs("agent-relay", "WARNING"):
            self.got(done=["A"], nonce="ab" * 12)
        self.assertIn("A", self.r.s.pending)
        self.assertEqual([i for i in self.r.s.spool if not i.get("doorbell")], [])
        for i, frm in enumerate(("demo/bot-cloud", "demo/bot-inbox", "demo/terminal-3", "demo/terminal-12",
                                 "demo/terminal@cloud")):
            self.inbound(f"X{i}")
            self.got(done=[f"X{i}"], frm=frm)
            self.assertNotIn(f"X{i}", self.r.s.pending)

    def test_non_hex_signature_is_an_invalid_receipt(self):
        self.inbound("A")
        self.deliver()
        self.r.s.caps_lost = True
        self.r.s.save()
        before = json.dumps([self.r.s.pending, self.r.s.alerts, self.r.s.spool, self.r.s.got_last,
                             self.r.s.caps_lost, self.r.s.got_probe])
        good = rr.got_body(KEY, ["A"], [], ts=int(self.now))
        for k, sig in enumerate(("é" * 64, "é", "A" * 64, "0" * 63, "0" * 65)):
            body = good.rsplit("sig: ", 1)[0] + f"sig: {sig}"
            self.assertEqual(rr.parse_got(body), {}, sig)
            with self.assertLogs("agent-relay", "WARNING") as lg:
                self.r.handle([{"id": f"bad-{k}", "from": "demo/terminal", "agent": None, "thread": None,
                                "text": body}])
            self.assertIn("malformed", lg.output[0])
        self.assertEqual(json.dumps([self.r.s.pending, self.r.s.alerts, self.r.s.spool, self.r.s.got_last,
                                     self.r.s.caps_lost, self.r.s.got_probe]), before)

    def test_pending_lifecycle(self):
        self.inbound("A")
        self.assertEqual(self.p("A"), {"stage": "ringing", "t": None, "rerings": 0, "rung": False, "keep": False,
                                       "from": "demo/other"})
        self.r.s.wakes = [self.now - 1]
        self.r.flush()
        self.tick(self.TO + 100)
        self.assertEqual((self.posts, self.p("A")["stage"], len(self.fwds("A"))), ([], "ringing", 1))
        self.assertEqual(self.deliver(), ["A"])
        self.assertEqual((self.p("A")["stage"], self.p("A")["t"], self.p("A")["rung"]), ("unacked", self.now, True))
        self.inbound("B")
        self.now += 5
        self.overflow("B")
        self.assertEqual((self.p("B")["stage"], self.p("B")["t"], self.p("B")["keep"], self.p("B")["rung"]),
                         ("unacked", self.now, True, False))

    def test_stored_and_done_receipts(self):
        self.inbound("A")
        self.got(stored=["A"])
        self.assertEqual((self.p("A")["stage"], self.p("A")["keep"], self.p("A")["t"]), ("stored", True, None))
        self.assertEqual([i["id"] for i in self.r.s.spool], ["A"])
        self.deliver()
        t = self.p("A")["t"]
        self.now += 100
        self.got(stored=["A"])
        self.assertEqual(self.p("A")["t"], t)
        self.inbound("B")
        self.r.notice("hello")
        self.got(done=["B"])
        self.assertNotIn("B", self.r.s.pending)
        self.assertEqual([i["id"] for i in self.r.s.spool if i.get("doorbell")], [])
        self.assertEqual([i["text"] for i in self.r.s.spool if not i.get("doorbell")], ["hello"])
        self.assertTrue(self.r.s.got_last)

    def test_rate_limited_rering_then_stored_then_crash(self):
        self.inbound("A")
        self.deliver()
        self.tick(self.TO + 1)
        self.assertEqual((self.p("A")["rerings"], self.p("A")["t"], len(self.fwds("A"))), (1, None, 2))
        self.got(stored=["A"])
        self.assertEqual((self.p("A")["stage"], self.p("A")["t"]), ("stored", None))
        self.tick(self.DONE + 100)
        self.assertEqual(self.p("A")["rerings"], 1)
        self.assertEqual(self.deliver(), ["A"])
        self.assertEqual(self.p("A")["t"], self.now)
        self.tick(self.DONE + 1)
        self.assertEqual((self.p("A")["rerings"], len(self.fwds("A"))), (2, 2))
        self.deliver()
        self.tick(self.DONE + 1)
        self.assertNotIn("A", self.r.s.pending)
        self.assertEqual([a["key"] for a in self.r.s.alerts], ["abandon:A"])
        self.assertEqual(self.rerings("A"), [1, 2])

    def test_capabilities_lost(self):
        t0 = self.now
        self.inbound("A", "B")
        self.deliver()
        self.got(stored=["B"])
        self.now = t0 + 10
        self.inbound("C")
        self.overflow("C")
        self.deliver()
        self.now = t0 + 901
        self.r.check_pending()
        self.inv()
        self.assertEqual((self.r.s.got_probe, self.p("A")["rerings"]), ({"id": "A", "posted": None}, 1))
        self.assertEqual(self.p("C")["rerings"], 0)
        self.deliver()
        self.assertEqual(self.r.s.got_probe, {"id": "A", "posted": t0 + 901})
        self.now = t0 + 905
        self.inbound("D")
        self.now = t0 + 901 + self.TO + 1
        self.r.check_pending()
        self.inv()
        self.assertTrue(self.r.s.caps_lost)
        self.assertEqual([a["key"] for a in self.r.s.alerts], ["caps-lost:A"])
        self.assertTrue(all(p["keep"] for p in self.r.s.pending.values()))
        self.tick(1)
        self.assertEqual({m: p["stage"] for m, p in self.r.s.pending.items()},
                         {"A": "stored", "B": "stored", "C": "unacked", "D": "ringing"})
        self.assertEqual(len(self.fwds("C")), 2)
        self.assertEqual(self.p("C")["rerings"], 1)
        self.deliver()
        self.tick(1)
        self.assertEqual({m: p["stage"] for m, p in self.r.s.pending.items()},
                         {"A": "stored", "B": "stored", "C": "stored", "D": "stored"})
        self.now = t0 + self.DONE + 1
        self.r.check_pending()
        self.inv()
        self.assertEqual((self.p("B")["rerings"], self.p("A")["rerings"]), (1, 1))
        self.now = t0 + 901 + self.DONE + 1
        self.r.check_pending()
        self.inv()
        self.assertEqual((self.p("A")["rerings"], len(self.fwds("A")), len(self.fwds("B"))), (2, 2, 1))
        self.assertEqual(sorted(self.r.s.pending), ["A", "B", "C", "D"])
        self.got(done=["A", "B", "C", "D"])
        self.assertEqual(self.r.s.pending, {})
        self.assertEqual(len([k for k in self.alert_keys if k.startswith("caps-lost")]), 1)

    def test_turned_off_within_the_lease_after_a_receipt(self):
        self.inbound("B")
        self.turn(self.deliver(), then="stored")
        self.restart(GOT_TIMEOUT_SECONDS=0)
        self.tick(self.DONE - 10)
        self.assertEqual((self.p("B")["rerings"], self.deliver()), (0, []))
        self.tick(20)
        self.assertEqual(self.p("B")["rerings"], 1)
        self.turn(self.deliver())
        self.assertEqual(self.r.s.pending, {})
        self.inbound("E")
        self.assertEqual(self.r.s.pending, {})

    def test_turned_off_within_the_lease_before_a_receipt(self):
        self.inbound("B")
        t1 = self.now
        self.turn(self.deliver(), then="crash")
        self.restart(GOT_TIMEOUT_SECONDS=0)
        self.assertTrue(self.p("B")["keep"])
        self.tick(1)
        self.assertEqual((self.p("B")["stage"], self.p("B")["t"]), ("stored", t1))
        self.now = t1 + self.DONE + 1
        self.r.check_pending()
        self.turn(self.deliver())
        self.assertEqual(self.r.s.pending, {})

    def test_turned_off_while_the_probe_rering_waits(self):
        self.inbound("B")
        self.deliver()
        self.tick(self.TO + 1)
        self.assertEqual(self.r.s.got_probe, {"id": "B", "posted": None})
        self.restart(GOT_TIMEOUT_SECONDS=0)
        self.assertEqual((self.p("B")["keep"], self.r.s.got_probe), (True, None))
        self.cloud.claims["B"] = self.now - 10
        self.turn(self.deliver())
        self.assertEqual((self.p("B")["stage"], self.p("B")["t"]), ("stored", self.now))
        self.tick(self.DONE + 1)
        self.turn(self.deliver())
        self.assertEqual(self.r.s.pending, {})

    def test_orphan_reclaimed_then_turned_off(self):
        for maxr in (2, 1):
            with self.subTest(maxr=maxr):
                self.setUp()
                self.restart(GOT_MAX_RERINGS=maxr)
                self.inbound("B")
                t0 = self.now
                self.turn(self.deliver(), then="stored")
                self.now = t0 + 7190
                self.turn(["B"], then="stored")
                self.assertEqual(self.p("B")["t"], t0)
                self.restart(GOT_TIMEOUT_SECONDS=0)
                self.now = t0 + self.DONE + 1
                self.r.check_pending()
                self.turn(self.deliver())
                self.assertIn("B", self.r.s.pending)
                self.tick(self.DONE + 1)
                if maxr == 2:
                    self.turn(self.deliver())
                    self.assertEqual(self.r.s.pending, {})
                else:
                    self.assertEqual(self.r.s.pending, {})
                    self.assertEqual([a["key"] for a in self.r.s.alerts], ["abandon:B"])

    def test_turned_off_recovery_rering(self):
        self.inbound("B")
        self.overflow("B")
        self.deliver()
        self.restart(GOT_TIMEOUT_SECONDS=0)
        self.tick(self.TO + 1)
        self.assertEqual(len(self.fwds("B")), 2)
        self.turn(self.deliver(), then="crash")
        self.tick(1)
        self.assertEqual(self.p("B")["stage"], "stored")
        self.tick(self.DONE + 1)
        self.turn(self.deliver())
        self.assertEqual(self.r.s.pending, {})

    def test_dropped_rerings_count_and_end_with_an_alert(self):
        self.inbound("B")
        self.deliver()
        for n in (1, 2):
            self.tick(self.TO + 1)
            self.assertEqual(self.p("B")["rerings"], n)
            self.overflow("B")
            self.restart()
            self.assertEqual((self.p("B")["rerings"], self.p("B")["keep"]), (n, True))
            self.deliver()
        self.tick(self.TO + 1)
        self.assertNotIn("B", self.r.s.pending)
        self.assertEqual([a["key"] for a in self.r.s.alerts], ["abandon:B"])

    def test_alert_survives_overflow_and_restart_until_confirmed(self):
        self.restart(GOT_MAX_RERINGS=0)
        self.inbound("B")
        self.deliver()
        self.tick(self.TO + 1)
        disk = self.on_disk()
        self.assertNotIn("B", disk["pending"])
        self.assertEqual([a["key"] for a in disk["alerts"]], ["abandon:B"])
        self.r.s.wakes = [self.now] * 20
        self.r.s.max_spool = 10
        self.inbound(*[f"F{j}" for j in range(45)])
        self.assertTrue(self.r.s.overflow_dropped)
        self.restart()
        self.assertEqual([a["key"] for a in self.r.s.alerts], ["abandon:B"])
        self.r.s.wakes = []
        self.r.s.spool = []
        self.r.s.pending = {}
        self.prev = {}
        start = len(self.posts)
        self.r.flush()
        (b,) = self.posts[start:]
        (item,) = b["messages"]
        self.assertFalse(b["doorbell"])
        self.assertTrue(item["id"].startswith("relay-alerts-"))
        self.assertIn("Message B (from demo/other)", item["text"])
        self.assertEqual(self.r.s.alerts[0]["batch"], item["id"])
        self.got(stored=[item["id"]])
        self.assertEqual(len(self.r.s.alerts), 1)
        self.got(done=[item["id"]])
        self.assertEqual(self.r.s.alerts, [])
        self.r.s.alert("abandon:X", "x")
        self.got(stored=["X"])
        self.assertEqual(len(self.r.s.alerts), 1)
        self.got(done=["X"])
        self.assertEqual(self.r.s.alerts, [])

    def test_alert_and_spool_batches_alternate_and_end(self):
        for limit in (3500, 600):
            with self.subTest(limit=limit):
                self.setUp()
                self.restart(MAX_SPOOL=10, MAX_BATCH=1, WEBHOOK_MAX_BODY_CHARS=limit)
                for k in range(25):
                    self.r.s.alert(f"k{k}", f"alert number {k} " + "x" * 60)
                for k in range(10):
                    self.r.notice(f"queued {k}")
                self.r.s.save()
                kinds = []
                for _ in range(200):
                    if not self.r.s.spool and not self.r.s.alerts:
                        break
                    self.r.s.wakes = []
                    n = len(self.posts)
                    self.r.flush()
                    self.assertEqual(len(self.posts), n + 1)
                    (item,) = self.posts[-1]["messages"]
                    kinds.append("alerts" if item["id"].startswith("relay-alerts-") else "spool")
                    if kinds[-1] == "alerts":
                        self.got(done=[item["id"]])
                self.assertEqual((self.r.s.spool, self.r.s.alerts), ([], []))
                both = kinds[:2 * min(kinds.count("alerts"), kinds.count("spool"))]
                self.assertTrue(all(a != b for a, b in zip(both, both[1:])), kinds)
                self.assertEqual(kinds.count("spool"), 10)
                self.assertFalse(any(m["id"] == "relay-spool-overflow" for b in self.posts for m in b["messages"]))
                self.assertEqual(len(self.r.s.wakes), 1)
                self.r.s.alert("late", "one more")
                self.r.notice("and a notice")
                self.r.flush()
                self.assertFalse(self.r.can_wake()[0])
                self.assertEqual(self.r.flush(), 20)

    def test_alert_resent_until_confirmed_then_dropped(self):
        for lost in (False, True):
            with self.subTest(caps_lost=lost):
                self.setUp()
                self.r.s.caps_lost = lost
                self.r.s.alert("abandon:Z", "z")
                self.r.s.save()
                self.code = 500
                self.r.flush()
                self.assertEqual((self.r.s.alerts[0]["posted"], self.r.s.alerts[0]["tries"]), (None, 0))
                self.code = 200
                for tries in range(self.MAXR + 1):
                    self.r.s.wakes = []
                    self.r.flush()
                    self.assertEqual((self.r.s.alerts[0]["posted"], self.r.s.alerts[0]["tries"]), (self.now, tries))
                    self.restart()
                    self.assertEqual(self.r.s.alerts[0]["tries"], tries)
                    self.r.s.wakes = []
                    self.r.flush()
                    self.assertEqual(self.r.s.alerts[0]["tries"], tries)
                    self.now += self.TO + 1
                    if tries < self.MAXR:
                        self.r.check_pending()
                with self.assertLogs("agent-relay", "WARNING") as lg:
                    self.r.check_pending()
                self.assertEqual(self.r.s.alerts, [])
                self.assertIn("dropped after 3 unacknowledged deliveries", lg.output[0])
                self.assertEqual(len(self.posts), self.MAXR + 2)

    def test_overflow_summary_keeps_what_was_not_confirmed(self):
        with mock.patch.object(rr, "MAX_ALERTS", 2):
            for k in range(5):
                self.r.s.alert(f"k{k}", f"text {k}")
            (summary,) = [a for a in self.r.s.alerts if a["key"] == "alerts-overflow"]
            self.assertEqual(summary["n"], 3)
            self.r.flush()
            (item,) = self.posts[-1]["messages"]
            self.assertIn("3 more relay alert(s)", item["text"])
            self.assertEqual(summary["sent_n"], 3)
            self.r.s.alert("k5", "five")
            self.r.s.alert("k6", "six")
            self.assertEqual(summary["n"], 5)
            self.got(done=[item["id"]])
            self.assertEqual([(a["key"], a.get("n"), a["posted"]) for a in self.r.s.alerts],
                             [("alerts-overflow", 2, None)])
            self.r.s.wakes = []
            self.r.flush()
            self.assertIn("2 more relay alert(s)", self.posts[-1]["messages"][0]["text"])

    def test_expired_overflow_summary_keeps_what_was_added_after_its_last_post(self):
        with mock.patch.object(rr, "MAX_ALERTS", 2):
            for k in range(5):
                self.r.s.alert(f"k{k}", f"text {k}")
            (summary,) = [a for a in self.r.s.alerts if a["key"] == "alerts-overflow"]
            self.r.s.wakes = []
            self.r.flush()
            self.assertEqual((summary["n"], summary["sent_n"]), (3, 3))
            summary["tries"] = self.MAXR
            self.r.s.alert("k5", "five")
            self.r.s.alert("k6", "six")
            self.assertEqual(summary["n"], 5)
            self.now += self.TO + 1
            with self.assertLogs("agent-relay", "WARNING") as lg:
                self.r.check_pending()
            self.assertIn("2 added since then kept", "\n".join(lg.output))
            self.assertEqual([(a["key"], a["n"], a["sent_n"], a["batch"], a["posted"], a["tries"])
                              for a in self.r.s.alerts if a["key"] == "alerts-overflow"],
                             [("alerts-overflow", 2, 0, None, None, 0)])
            self.restart()
            (summary,) = [a for a in self.r.s.alerts if a["key"] == "alerts-overflow"]
            self.assertEqual(summary["n"], 2)
            self.r.s.alerts = [summary]
            self.r.s.wakes = []
            start = len(self.posts)
            self.r.flush()
            (b,) = self.posts[start:]
            self.assertIn("2 more relay alert(s)", b["messages"][0]["text"])
            self.assertEqual((summary["sent_n"], summary["tries"]), (2, 0))

    def test_alert_batch_fits_and_confirms_only_what_it_carried(self):
        for k in range(30):
            self.r.s.alert(f"k{k}", f"{k:02d}" + "y" * 298)
        self.r.flush()
        raw = json.dumps(self.posts[-1], ensure_ascii=False)
        (item,) = self.posts[-1]["messages"]
        self.assertLessEqual(len(item["text"]), 2000)
        self.assertLessEqual(len(raw), LIMIT)
        self.got(done=[item["id"]])
        gone = {f"k{k}" for k in range(30)} - {a["key"] for a in self.r.s.alerts}
        self.assertTrue(gone)
        for key in gone:
            self.assertIn(f"{int(key[1:]):02d}" + "y" * 298, item["text"])
        self.assertTrue(all(a["posted"] is None for a in self.r.s.alerts))

    def test_alert_capacity(self):
        with self.assertLogs("agent-relay", "WARNING") as lg:
            for k in range(rr.MAX_ALERTS + 5):
                self.r.s.alert(f"k{k}", f"t{k}")
        self.assertEqual(len([l for l in lg.output if "ALERT k" in l]), rr.MAX_ALERTS + 5)
        self.assertEqual(len(self.r.s.alerts), rr.MAX_ALERTS + 1)
        self.assertEqual(self.r.s.alerts[-1]["key"], "alerts-overflow")
        self.assertEqual(self.r.s.alerts[-1]["n"], 5)

    def test_alert_goes_out_with_an_empty_spool_and_is_cut_to_fit(self):
        for limit in (3500, 600):
            with self.subTest(limit=limit):
                self.setUp()
                self.restart(WEBHOOK_MAX_BODY_CHARS=limit)
                self.r.s.alert("big", "\"" * 5000)
                self.assertEqual(self.r.s.spool, [])
                self.assertEqual(self.r.flush(), 600)
                (b,) = self.posts
                self.assertLessEqual(len(json.dumps(b, ensure_ascii=False)), limit)
                self.assertLessEqual(len(b["messages"][0]["text"]), 300)
                self.assertTrue(b["messages"][0]["text"])

    def test_late_receipts_do_not_bring_a_message_back(self):
        self.inbound("B")
        self.deliver()
        self.got(done=["B"])
        self.now += 10
        self.got(stored=["B"])
        self.got(done=["B"])
        self.assertEqual((self.r.s.pending, self.r.s.got_last), ({}, self.now))

    def test_dropped_probe_rering_is_no_capability_loss(self):
        self.inbound("A")
        self.deliver()
        self.tick(self.TO + 1)
        self.assertEqual(self.r.s.got_probe, {"id": "A", "posted": None})
        self.overflow("A")
        self.assertEqual((self.r.s.got_probe, self.p("A")["keep"]), (None, True))
        self.deliver()
        self.tick(self.TO - 10)
        self.assertFalse(self.r.s.caps_lost)
        self.tick(20)
        self.assertEqual((self.p("A")["rerings"], self.r.s.caps_lost, self.r.s.got_probe), (2, False, None))

    def test_one_doorbell_per_id(self):
        self.inbound("A")
        self.r.handle([{"id": "rq", "from": "demo/bot-cloud", "agent": None, "thread": None,
                        "text": refwd_req("A")}])
        self.assertEqual([(i["id"], i.get("refwd")) for i in self.r.s.spool], [("A", 1)])
        self.r.s.pending["A"].update(stage="unacked", t=self.now - self.TO - 1, rung=True)
        self.r.check_pending()
        self.assertEqual([(i["id"], i.get("refwd"), i.get("rering")) for i in self.r.s.spool], [("A", 1, 1)])

    def test_cold_start_without_receipts(self):
        self.inbound("B")
        self.turn(self.deliver(), then="crash")
        self.tick(self.TO + 1)
        self.assertEqual(len(self.fwds("B")), 2)
        self.deliver()
        self.tick(self.TO + 1)
        self.assertTrue(self.r.s.caps_lost)
        self.assertEqual([a["key"] for a in self.r.s.alerts], ["caps-lost:B"])
        self.tick(1)
        self.assertEqual((self.p("B")["stage"], self.p("B")["keep"]), ("stored", True))
        self.tick(self.DONE + 1)
        self.assertEqual(self.p("B")["rerings"], 2)
        self.deliver()
        self.tick(self.DONE + 1)
        self.assertNotIn("B", self.r.s.pending)
        self.assertEqual([a["key"] for a in self.r.s.alerts], ["caps-lost:B", "abandon:B"])

    def test_refwd_with_and_without_caps(self):
        self.inbound("A")
        self.deliver()
        self.now += 100
        self.r.handle([{"id": "rq1", "from": "demo/bot-cloud", "agent": None, "thread": None,
                        "text": refwd_req("A") + "\ncaps: got1"}])
        self.assertEqual((self.r.s.spool, self.p("A")["t"]), ([], self.now))
        self.r.handle([{"id": "rq2", "from": "demo/bot-cloud", "agent": None, "thread": None,
                        "text": refwd_req("A")}])
        self.assertEqual(([i["id"] for i in self.r.s.spool], self.p("A")["t"]), (["A"], None))
        self.inv()
        self.r.s.caps_lost = False
        self.inbound("N")
        self.r.handle([{"id": "rq3", "from": "demo/bot-cloud", "agent": None, "thread": None,
                        "text": refwd_req("gone") + "\ncaps: got1"}])
        self.assertNotIn("gone", self.r.s.pending)

    def test_relay_resends_do_not_use_up_refwd(self):
        self.inbound("A")
        self.deliver()
        for _ in range(2):
            self.tick(self.TO + 1)
            self.deliver()
            self.got(stored=["elsewhere"])
        self.assertEqual(len(self.fwds("A")), 3)
        self.assertEqual(self.r.s.refwds, {})
        for k in range(2):
            self.r.handle([{"id": f"rq{k}", "from": "demo/bot-cloud", "agent": None, "thread": None,
                            "text": refwd_req("A") + "\ncaps: got1"}])
        self.assertEqual(len(self.fwds("A")), 5)

    def test_rn_out_reply_to_is_proof_of_handling(self):
        self.inbound("A", "B", "C")
        self.r.s.alert("abandon:C", "c")
        bad = envelope("demo/x", "A", "hi", key="f" * 64)
        self.r.handle([{"id": "o1", "from": "demo/bot-cloud", "agent": None, "thread": None, "text": bad}])
        self.assertIn("A", self.r.s.pending)
        for i, (mid, ts) in enumerate((("A", None), ("B", int(self.now) - 5000), ("C", None))):
            self.evidence.add(mid)
            self.r.handle([{"id": f"o{i + 2}", "from": "demo/bot-cloud", "agent": None, "thread": None,
                            "text": envelope("demo/x", mid, "hi", ts=ts, nonce=f"n{i}")}])
        self.inv()
        self.assertEqual((self.r.s.pending, [a["key"] for a in self.r.s.alerts]), ({}, ["reject:n1"]))

    def reply(self, mid, k):
        self.evidence.add(mid)
        self.r.handle([{"id": f"out-{k}", "from": "demo/bot-cloud", "agent": None, "thread": None,
                        "text": envelope("demo/x", mid, "hi", nonce=f"reply-{k}")}])
        self.inv()

    def bells(self):
        return [i["id"] for i in self.r.s.spool if i.get("doorbell")]

    def test_done_receipt_withdraws_a_queued_doorbell_after_the_reply_ended_pending(self):
        self.inbound("A", "B")
        self.r.notice("hello")
        self.reply("A", 1)
        self.assertNotIn("A", self.r.s.pending)
        self.assertEqual(self.bells(), ["A", "B"])
        self.r.s.alert("x", "an alert")
        self.r.s.last_batch, self.r.s.wakes = "spool", []
        self.r.flush()
        batch = self.r.s.alerts[0]["batch"]
        self.assertTrue(batch.startswith(rr.ALERT_PREFIX))
        self.got(done=["A", batch])
        self.assertEqual(self.bells(), ["B"])
        self.assertEqual([i["text"] for i in self.r.s.spool if not i.get("doorbell")], ["hello"])
        self.assertEqual(self.r.s.alerts, [])
        self.assertIn("B", self.r.s.pending)
        self.assertEqual([i["id"] for i in rr.State().spool if i.get("doorbell")], ["B"])
        before = json.dumps([self.r.s.pending, self.r.s.alerts, self.r.s.spool])
        self.got(done=["A"])
        self.assertEqual(json.dumps([self.r.s.pending, self.r.s.alerts, self.r.s.spool]), before)

    def test_reply_alone_keeps_the_queued_doorbell(self):
        self.inbound("A")
        self.reply("A", 1)
        self.assertEqual((self.r.s.pending, self.bells()), ({}, ["A"]))
        self.assertEqual(self.deliver(), ["A"])

    def test_only_a_valid_done_receipt_withdraws_the_doorbell(self):
        self.inbound("A")
        self.reply("A", 1)
        self.got(stored=["A"])
        self.assertEqual(self.bells(), ["A"])
        self.got(stored=["Z"], nonce="ab" * 12)
        with self.assertLogs("agent-relay", "WARNING") as lg:
            self.got(done=["A"], key="f" * 64)
            self.got(done=["A"], ts=int(self.now) - 1000)
            self.got(done=["A"], nonce="ab" * 12)
            self.got(done=["A"], frm="demo/other")
        self.assertEqual(len(lg.output), 4)
        self.assertEqual(self.bells(), ["A"])
        self.got(done=["A"])
        self.assertEqual(self.bells(), [])

    def test_receipts_off_then_back(self):
        self.r.s.caps_lost = True
        self.inbound("C")
        self.deliver()
        self.tick(1)
        self.assertNotIn("C", self.r.s.pending)
        self.tick(self.TO + 1)
        self.assertEqual(self.rerings("C"), [])
        self.r.handle([{"id": "rq", "from": "demo/bot-cloud", "agent": None, "thread": None,
                        "text": refwd_req("C")}])
        self.assertEqual([i["id"] for i in self.r.s.spool], ["C"])
        self.got(stored=["nobody"])
        self.assertFalse(self.r.s.caps_lost)
        self.inbound("D")
        self.deliver()
        self.tick(self.TO + 1)
        self.assertEqual(self.p("D")["rerings"], 1)

    def test_rering_is_saved_before_the_resend(self):
        self.inbound("A")
        self.deliver()
        def dies(a, k):
            if (k.get("stdin") or "").startswith("RN-FWD v1"):
                raise Crash()
            return RunOK()
        self.hook = dies
        self.now += self.TO + 1
        with self.assertRaises(Crash):
            self.r.check_pending()
        self.hook = None
        self.restart()
        self.assertEqual(self.p("A")["rerings"], 1)
        self.assertEqual([(i["id"], i.get("rering")) for i in self.r.s.spool], [("A", 1)])
        self.tick(self.TO + 1)
        self.assertEqual((self.p("A")["rerings"], len(self.r.s.spool)), (1, 1))
        (Path(tmp) / "fwd-copies" / "A.json").unlink()
        self.deliver()
        self.got(stored=["elsewhere"])
        self.tick(self.TO + 1)
        self.assertEqual(self.r.s.spool[0]["forward_failed"], "no copy kept")

    def test_stored_rering_does_not_resend(self):
        self.inbound("A")
        self.deliver()
        self.got(stored=["A"])
        self.tick(self.TO + 1)
        self.assertEqual(self.p("A")["rerings"], 0)
        self.tick(self.DONE)
        self.assertEqual((self.p("A")["rerings"], len(self.fwds("A"))), (1, 1))
        self.assertEqual(self.deliver(), ["A"])
        self.assertEqual(self.rerings("A"), [1])

    def test_fwd_carries_caps_outside_the_signature(self):
        body = rr.fwd_body(KEY, "01JTESTMSG0000000000000000", "demo/claude-session", "claude-code", "", "你好\n x ",
                           ts=1700000000)
        head = body.split("\n---\n")[0].split("\n")
        self.assertEqual(head[-2:], [f"sig: {FWD_VECTOR_SIG}", "caps: got1"])

    def test_listen_wait_never_passes_the_next_deadline(self):
        db = Path(tempfile.mkdtemp()) / "network.db"
        con = sqlite3.connect(db)
        con.executescript(SCHEMA)
        con.close()
        self.restart(RN_DB=str(db))
        self.inbound("A")
        self.deliver()
        self.now += self.TO - 300
        waits = []
        def stop(a, k):
            if a[0] == "listen":
                waits.append(int(a[a.index("--wait") + 1]))
                if len(waits) > 1:
                    raise Crash()
                return RunOut(0, "No messages. You are demo/bot.\n")
            return RunOK()
        self.hook = stop
        with self.assertRaises(Crash):
            self.r.run()
        self.assertEqual(waits[0], 0)
        self.assertTrue(5 <= waits[1] <= 301, waits)

    def test_state_without_the_new_fields(self):
        clean_state()
        (Path(tmp) / "state.json").write_text(json.dumps({"seen": ["S"], "spool": [], "recv_floor": self.now}))
        self.restart()
        s = self.r.s
        self.assertEqual((s.pending, s.alerts, s.got_last, s.caps_lost, s.got_probe), ({}, [], None, False, None))
        self.inbound("A")
        old = prev_state_class()()
        old.save()
        self.assertNotIn("pending", self.on_disk())

    def test_turned_off_writes_no_pending(self):
        self.restart(GOT_TIMEOUT_SECONDS=0)
        self.inbound("A")
        self.assertEqual(self.r.s.pending, {})
        self.assertEqual(self.deliver(), ["A"])
