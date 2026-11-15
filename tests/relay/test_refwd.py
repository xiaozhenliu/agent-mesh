import json, os, time, unittest
from pathlib import Path

from tests.relay.support import (
    tmp,
    rr,
    KEY,
    CFG,
    sha,
    samples,
    RunOK,
    RunOut,
    parse_fwd,
    DoorbellFixture,
    refwd_req,
)


def parse_ack(body):
    lines = body.split("\n")
    assert lines[0] == "RN-REFWD-ACK v1", body
    f = dict(l.split(": ", 1) if ": " in l else (l.rstrip(":"), "") for l in lines[1:])
    assert f["sig"] == rr.sign_refwd_ack(KEY, f["id"], f["status"], f["attempt"], f["ts"])
    return f


class Refwd(DoorbellFixture, unittest.TestCase):
    def req(self, mid="m1", frm="demo/bot-cloud", i=None, **kw):
        self.k = getattr(self, "k", 0) + 1
        return {"id": i or f"req{self.k}", "from": frm, "agent": None, "thread": None, "text": refwd_req(mid, **kw)}

    def fwds(self):
        return [parse_fwd(k["stdin"].strip()) for a, k in self.calls if (k.get("stdin") or "").startswith("RN-FWD v1")]

    def acks(self):
        return [parse_ack(k["stdin"]) for a, k in self.calls if (k.get("stdin") or "").startswith("RN-REFWD-ACK v1")]

    def test_copy_kept_private_and_refwd_resends_exact_text_and_rings(self):
        text = samples()["nasty"]
        self.r.handle([self.m(text)])
        copy = Path(tmp) / "fwd-copies" / "m1.json"
        self.assertEqual(oct(copy.stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct(copy.parent.stat().st_mode & 0o777), "0o700")
        self.drain(); self.posts.clear()
        self.r.handle([self.req()])
        (f1, t1), (f2, t2) = self.fwds()
        self.assertEqual((t1, t2), (text, text))
        self.assertEqual((f2["id"], f2["from"], f2["text_sha256"]), ("m1", "demo/other", sha(text)))
        self.assertEqual(self.calls[-1][0], ("send", "demo/bot-inbox@cloud", "-"))
        (a,) = self.acks()
        self.assertEqual((a["id"], a["status"], a["attempt"]), ("m1", "resent", "1"))
        (b,) = self.drain()
        self.assertTrue(b["doorbell"])
        self.assertEqual((b["messages"][0]["id"], b["messages"][0]["refwd"]), ("m1", 1))

    def test_failed_first_forward_is_recovered_by_refwd(self):
        self.result = RunOut(1, "Error: boom")
        self.r.handle([self.m("important")])
        self.assertTrue(self.r.s.spool[-1]["forward_failed"])
        self.result = RunOK()
        self.r.handle([self.req()])
        self.assertEqual(self.fwds()[-1][1], "important")
        self.assertEqual(self.acks()[-1]["status"], "resent")

    def test_inbox_sender_also_allowed(self):
        self.r.handle([self.m("hi")])
        self.r.handle([self.req(frm="demo/bot-inbox")])
        self.assertEqual(len(self.fwds()), 2)

    def test_limit_per_id(self):
        self.r.handle([self.m("hi")])
        self.r.handle([self.req(), self.req(), self.req()])
        self.assertEqual(len(self.fwds()), 3)                    # original + 2
        self.assertEqual([a["status"] for a in self.acks()], ["resent", "resent", "limit"])

    def test_limit_survives_restart(self):
        self.r.handle([self.m("hi")])
        self.r.handle([self.req(), self.req()])
        r2 = rr.Relay(dict(CFG)); r2.rn = self.r.rn
        r2.handle([self.req()])
        self.assertEqual(self.acks()[-1]["status"], "limit")

    def test_hourly_limit(self):
        self.r.c["REFWD_MAX_PER_HOUR"] = 1
        self.r.handle([self.m("a", i="a"), self.m("b", i="b")])
        self.r.handle([self.req("a"), self.req("b")])
        self.assertEqual([a["status"] for a in self.acks()], ["resent", "limit"])

    def test_other_senders_ignored_not_forwarded_no_wake(self):
        self.r.handle([self.m("hi")])
        n = len(self.calls)
        with self.assertLogs("agent-relay", "WARNING") as lg:
            self.r.handle([self.req(frm="demo/other"), self.req(frm="demo/bot"), self.req(frm="evil/bot-cloud")])
        self.assertEqual(len(self.calls), n)                     # no re-forward, no ack, not forwarded as mail
        self.assertEqual(len(lg.output), 3)
        self.assertTrue(all("sender not allowed" in l for l in lg.output))
        self.drain()
        self.assertEqual(len(self.posts), 1)                     # only the original doorbell

    def test_bad_sig_stale_replay_malformed_ignored(self):
        self.r.handle([self.m("hi")])
        n = len(self.calls)
        nonce = "ab" * 12
        with self.assertLogs("agent-relay", "WARNING"):
            self.r.handle([self.req(key="f" * 64), self.req(ts=int(time.time()) - 3600),
                           {"id": "rq-bad", "from": "demo/bot-cloud", "agent": None, "thread": None,
                            "text": "RN-REFWD v1\nid: m1"}])
        self.assertEqual(len(self.calls), n)
        self.r.handle([self.req(nonce=nonce)])
        self.assertEqual(len(self.calls), n + 2)                  # re-forward + ack
        with self.assertLogs("agent-relay", "WARNING"):
            self.r.handle([self.req(nonce=nonce)])                # replayed nonce
        self.assertEqual(len(self.calls), n + 2)

    def test_no_copy_and_expired_copy(self):
        self.r.handle([self.req("never-seen")])
        self.assertEqual([a["status"] for a in self.acks()], ["no-copy"])
        self.r.handle([self.m("old", i="old")])
        p = Path(tmp) / "fwd-copies" / "old.json"
        d = json.loads(p.read_text()); d["saved"] -= 8 * 86400; p.write_text(json.dumps(d))
        self.r.handle([self.req("old")])
        self.assertEqual(self.acks()[-1]["status"], "no-copy")

    def test_prune_after_days(self):
        self.r.handle([self.m("x", i="p1"), self.m("y", i="p2")])
        p1 = Path(tmp) / "fwd-copies" / "p1.json"
        os.utime(p1, (time.time() - 8 * 86400,) * 2)
        self.assertEqual(self.r.copies.prune(), 1)
        self.assertFalse(p1.exists())
        self.assertTrue((Path(tmp) / "fwd-copies" / "p2.json").exists())

    def test_failed_refwd_is_acked_failed(self):
        self.r.handle([self.m("hi")])
        self.result = RunOut(1, "Error: boom")
        self.r.handle([self.req()])
        self.assertEqual(self.acks()[-1]["status"], "failed")

    def test_plain_mail_mentioning_refwd_is_normal(self):
        self.r.handle([self.m("please see RN-REFWD v1 docs")])
        self.assertEqual(len(self.fwds()), 1)

    def test_refwd_vector(self):
        want = rr.hmac.new(KEY.encode(), b"refwd1\n01JTESTMSG0000000000000000\n1700000000\n" + b"00" * 12,
                           rr.hashlib.sha256).hexdigest()
        self.assertEqual(rr.sign_refwd(KEY, "01JTESTMSG0000000000000000", "1700000000", "00" * 12), want)
