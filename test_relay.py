#!/usr/bin/env python3
"""Offline checks for agent_relay.py: envelope verification (RN-OUT v1), replay/staleness/address guards, wake
limit, spool overflow, forwarding (RN-FWD v1) and doorbells, re-forward (RN-REFWD v1). No network, no webhook,
no reason-network binary. Run: python3 test_relay.py"""
import hashlib, json, os, random, sys, tempfile, time, unittest
from pathlib import Path

tmp = tempfile.mkdtemp()
os.environ["AGENT_RELAY_STATE"] = tmp
sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_relay as rr  # noqa: E402

KEY = "0123456789abcdef" * 4          # obviously fake test key (64 hex chars, like `openssl rand -hex 32`)
CFG = {"RN_BIN": "/nonexistent", "PERSON": "demo", "LISTEN_NAME": "bot", "CLOUD_NAME": "bot-cloud",
       "INBOX_NAME": "bot-inbox", "CLOUD_MACHINE": "cloud", "DOORBELL_PREVIEW_CHARS": 200,
       "WEBHOOK_URL": "http://127.0.0.1:9/", "WEBHOOK_KEY": "x", "OUTBOUND_KEY": KEY, "MAX_WAKES_PER_HOUR": 20,
       "MIN_SECONDS_BETWEEN_WAKES": 20, "MAX_BATCH": 10, "MAX_PROXY_PER_HOUR": 60,
       "MAX_SPOOL": 50, "WEBHOOK_MAX_BODY_CHARS": 3500}
LIMIT = CFG["WEBHOOK_MAX_BODY_CHARS"]
VECTOR_SIG = "652fbb03f1fd8626fd4f5db689b5983bdd57c797d5ba1085c340aaf699003088"
FWD_VECTOR_SIG = "a48eb3bbb089a3d6b31fed070d76063bb3b3ddabfac08b7651f4093ce358c576"


def clean_state():
    import shutil
    for f in Path(tmp).glob("*"):
        shutil.rmtree(f) if f.is_dir() else f.unlink()


class FakeResp:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): return False


def sha(t):
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def samples():
    rnd = random.Random(7)
    zh = "".join(rnd.choice("的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动") for _ in range(9000))
    ascii_ = "".join(rnd.choice("abcdefghijklmnopqrstuvwxyz0123456789 .,") for _ in range(20000))
    nasty = "".join(rnd.choice(['"', "\\", "\n", "\t", "\r", "\x01", "\u2028", "a", "中", "😀", "</x>"]) for _ in range(8000))
    lines = "\n".join(f"line {i}: \"quoted\" 路径 C:\\tmp\\{i}" for i in range(600))
    return {"zh": zh, "ascii": ascii_, "nasty": nasty, "lines": lines}


def envelope(to, reply_to, body, ts=None, nonce="0123456789abcdef01234567", key=KEY):
    ts = str(int(time.time())) if ts is None else str(ts)
    sig = rr.sign(key, to, reply_to, ts, nonce, body)
    return f"RN-OUT v1\nto: {to}\nreply_to: {reply_to}\nts: {ts}\nnonce: {nonce}\nsig: {sig}\n---\n{body}"


class FakeRun:
    returncode, stdout, stderr = 0, "sent", ""


class Envelopes(unittest.TestCase):
    def setUp(self):
        clean_state()
        self.r = rr.Relay(dict(CFG))
        self.sent = []
        self.r.rn = lambda *a, **k: (self.sent.append((a, k.get("stdin"))), FakeRun())[1]

    def msg(self, text, i="m1", frm="demo/bot-cloud"):
        return {"id": i, "from": frm, "agent": None, "thread": None, "text": text}

    def notices(self):
        return [i["text"] for i in self.r.s.spool if i["from"] == "relay"]

    def test_vector(self):
        sig = rr.sign(KEY, "demo/claude-session", "01JTESTREPLYTO0000000000", "1700000000",
                      "0123456789abcdef01234567", "hello\n第二行")
        self.assertEqual(sig, VECTOR_SIG)

    def test_good_signature_is_forwarded_once(self):
        e = envelope("demo/claude-session", "r1", "hi")
        self.r.handle([self.msg(e)])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0][:4], ("send", "demo/claude-session", "--from", "demo/bot"))
        self.r.handle([self.msg(e, i="m2")])                     # same nonce again = replay
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(any("refused" in n for n in self.notices()))

    def test_bad_signature_refused(self):
        self.r.handle([self.msg(envelope("demo/claude-session", "", "hi", key="f" * 64))])
        self.assertEqual(self.sent, [])
        self.assertTrue(any("bad signature" in n for n in self.notices()))

    def test_tampered_body_refused(self):
        e = envelope("demo/claude-session", "", "hi") + " (edited)"
        self.r.handle([self.msg(e)])
        self.assertEqual(self.sent, [])

    def test_stale_refused(self):
        self.r.handle([self.msg(envelope("demo/claude-session", "", "hi", ts=int(time.time()) - 1000))])
        self.assertEqual(self.sent, [])

    def test_group_and_self_refused(self):
        for i, to in enumerate(["*", "demo/*", "repo:a/b", "demo/bot", "demo/bot-cloud"]):
            self.r.handle([self.msg(envelope(to, "", "hi", nonce=f"n{i}"), i=f"g{i}")])
        self.assertEqual(self.sent, [])

    def test_plain_text_from_cloud_not_forwarded(self):
        self.r.handle([self.msg("just text")])
        self.assertEqual(self.sent, [])
        self.assertTrue(any("without a valid RN-OUT" in n for n in self.notices()))

    def test_other_senders_go_to_inbox_not_proxy(self):
        self.r.handle([self.msg(envelope("demo/x", "", "hi"), frm="demo/bot-cloud-2")])
        # an impostor name is just inbound mail: never proxied to demo/x, only forwarded to the box inbox
        self.assertEqual([a[0][1] for a in self.sent], ["demo/bot-inbox@cloud"])
        self.assertEqual(self.r.s.spool[-1]["from"], "demo/bot-cloud-2")

    def test_wake_limit(self):
        now = time.time()
        self.r.s.wakes = [now - 3000 + i for i in range(20)]
        self.assertFalse(self.r.can_wake()[0])
        self.r.s.wakes = [now - 5]
        self.assertFalse(self.r.can_wake()[0])                    # min gap 20 s
        self.r.s.wakes = [now - 30]
        self.assertTrue(self.r.can_wake()[0])

    def test_spool_overflow_is_reported(self):
        self.r.handle([self.msg("x", i=f"in{i}", frm="demo/other") for i in range(70)])
        spool = self.r.s.spool
        self.assertEqual(len(spool), 50)
        self.assertEqual(spool[0]["id"], "relay-spool-overflow")
        self.assertIn("dropped 21", spool[0]["text"])
        self.assertEqual(spool[-1]["id"], "in69")


class RunOK:
    returncode, stderr = 0, ""
    stdout = "→ demo/bot-inbox: queued: not connected to cloud right now; it goes out as soon as it is\nmessage id 01JX\n"


class RunOut:
    def __init__(self, rc, out): self.returncode, self.stdout, self.stderr = rc, out, ""


def parse_fwd(body):
    """Independent re-implementation of the box-side parser (box/rn-fetch.py) for cross-checking."""
    assert body.endswith(rr.FWD_TRAILER)
    head, _, rest = body.partition("\n---\n")
    text = rest[: -len(rr.FWD_TRAILER)]
    lines = head.split("\n")
    assert lines[0] == "RN-FWD v1"
    f = dict(l.split(": ", 1) if ": " in l else (l.rstrip(":"), "") for l in lines[1:])
    return f, text


class Doorbell(unittest.TestCase):
    def setUp(self):
        clean_state()
        self.r = rr.Relay(dict(CFG))
        self.calls, self.posts, self.result = [], [], RunOK()
        self.r.rn = lambda *a, **k: (self.calls.append((a, k)), self.result)[1]
        rr.urllib.request.urlopen = lambda req, timeout=None: (self.posts.append(req.data.decode()), FakeResp())[1]

    def m(self, text, i="m1", frm="demo/other", agent="codex", thread="01JTHREAD000000000000000000"):
        return {"id": i, "from": frm, "agent": agent, "thread": thread, "text": text}

    def drain(self):
        while self.r.s.spool:
            self.r.s.wakes = []
            self.r.flush()
        return [json.loads(b) for b in self.posts]

    def test_full_text_goes_over_rn_signed_and_exact(self):
        for name, text in {**samples(), "ws": "  leading\n\ntrailing  \n\n", "tags": "<reason-message> &lt; <b>"}.items():
            with self.subTest(name):
                self.setUp()
                self.r.handle([self.m(text, i=name)])
                (args, kw), = self.calls
                self.assertEqual(args, ("send", "demo/bot-inbox@cloud", "-"))      # no --from: sent as terminal
                body = kw["stdin"]
                f, got = parse_fwd(body.strip())                                     # the engine trims the body
                self.assertEqual(got, text)
                self.assertEqual((f["id"], f["from"], f["agent"], f["thread"]), (name, "demo/other", "codex",
                                                                                   "01JTHREAD000000000000000000"))
                self.assertEqual((int(f["text_len"]), f["text_sha256"]), (len(text), sha(text)))
                self.assertEqual(f["sig"], rr.sign_fwd(KEY, f["id"], f["from"], f["agent"], f["thread"],
                                                       f["text_len"], f["text_sha256"], f["ts"]))

    def test_agent_env_is_stripped(self):
        os.environ["CLAUDECODE"] = "1"
        try:
            self.r.handle([self.m("hi")])
        finally:
            del os.environ["CLAUDECODE"]
        self.assertNotIn("CLAUDECODE", self.calls[0][1]["env"])
        self.assertIn("PATH", self.calls[0][1]["env"])

    def test_doorbell_post_is_small_and_has_no_text(self):
        text = samples()["nasty"]
        self.r.handle([self.m(text)])
        (b,) = self.drain()
        self.assertEqual((b["relay_version"], b["doorbell"], b["inbox"]), (3, True, "demo/bot-inbox"))
        (it,) = b["messages"]
        self.assertNotIn("text", it)
        self.assertEqual((it["id"], it["text_len"], it["text_sha256"]), ("m1", len(text), sha(text)))
        self.assertEqual(it["preview"], text[:200])
        self.assertLessEqual(len(self.posts[0]), 1500)

    def test_many_doorbells_merge_under_limit_in_order(self):
        nasty = samples()["nasty"]
        self.r.handle([self.m(nasty[k * 300:], i=f"d{k}") for k in range(25)])
        bodies = self.drain()
        self.assertEqual([i["id"] for b in bodies for i in b["messages"]], [f"d{k}" for k in range(25)])
        for raw in self.posts:
            self.assertLessEqual(len(raw), LIMIT)

    def test_no_preview_option(self):
        self.r.c["DOORBELL_PREVIEW_CHARS"] = 0
        self.r.handle([self.m("secret-ish")])
        (b,) = self.drain()
        self.assertNotIn("preview", b["messages"][0])

    def test_forward_failure_still_rings_without_text(self):
        for result in (RunOut(1, "Error: no session called 'demo/bot-inbox'"),
                       RunOut(0, "→ demo/bot-inbox: refused: these two agents ...")):
            with self.subTest(result.stdout):
                self.setUp()
                self.result = result
                self.r.handle([self.m("y" * 9000)])
                (b,) = self.drain()
                (it,) = b["messages"]
                self.assertTrue(b["doorbell"])
                self.assertNotIn("text", it)
                self.assertTrue(it["forward_failed"])
                self.assertTrue((Path(tmp) / "fwd-copies" / "m1.json").exists())
                self.assertLessEqual(len(self.posts[0]), LIMIT)

    def test_rn_missing_or_text_too_large_flags_doorbell(self):
        def boom(*a, **k): raise FileNotFoundError("x")
        self.r.rn = boom
        self.r.handle([self.m("hi")])
        self.assertIn("rn send failed", self.r.s.spool[-1]["forward_failed"])
        self.setUp()
        self.r.handle([self.m("中" * 25000)])                      # 75 KB UTF-8 > 60 KB
        self.assertEqual(self.calls, [])
        self.assertIn("too large", self.r.s.spool[-1]["forward_failed"])

    def test_notices_never_share_a_post_with_doorbells(self):
        self.r.handle([self.m("a", i="d1")])
        self.r.notice("relay says hi")
        self.r.handle([self.m("b", i="d2")])
        bodies = self.drain()
        self.assertEqual([[i["id"] for i in b["messages"]] for b in bodies][0], ["d1"])
        self.assertEqual([b["doorbell"] for b in bodies], [True, False, True])
        self.assertEqual(bodies[1]["messages"][0]["text"], "relay says hi")

    def test_rate_limit_keeps_spool(self):
        self.r.handle([self.m("x", i=f"r{k}") for k in range(3)])
        self.r.s.wakes = [time.time() - 1]                        # inside the 20 s gap
        self.r.flush()
        self.assertEqual(self.posts, [])
        self.assertEqual(len(self.r.s.spool), 3)
        self.r.s.save()
        self.assertEqual(len(rr.Relay(dict(CFG)).s.spool), 3)     # survives a restart

    def test_fwd_vector(self):
        body = rr.fwd_body(KEY, "01JTESTMSG0000000000000000", "demo/claude-session", "claude-code", "", "你好\n x ",
                           ts=1700000000)
        self.assertEqual(body.split("\nsig: ")[1].split("\n")[0], FWD_VECTOR_SIG)


def refwd_req(mid, ts=None, nonce=None, key=KEY):
    ts = str(int(time.time())) if ts is None else str(ts)
    nonce = nonce or os.urandom(12).hex()
    return f"RN-REFWD v1\nid: {mid}\nts: {ts}\nnonce: {nonce}\nsig: {rr.sign_refwd(key, mid, ts, nonce)}"


def parse_ack(body):
    lines = body.split("\n")
    assert lines[0] == "RN-REFWD-ACK v1", body
    f = dict(l.split(": ", 1) if ": " in l else (l.rstrip(":"), "") for l in lines[1:])
    assert f["sig"] == rr.sign_refwd_ack(KEY, f["id"], f["status"], f["attempt"], f["ts"])
    return f


class Refwd(Doorbell):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
