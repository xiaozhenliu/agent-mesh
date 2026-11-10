#!/usr/bin/env python3
"""Offline checks for agent_relay.py: envelope verification (RN-OUT v1), replay/staleness/address guards, outbound
send records (duplicate, failed and unknown sends), wake limit, spool overflow, forwarding (RN-FWD v1) and doorbells,
re-forward (RN-REFWD v1), per-message isolation, crash-safe ordering and startup recovery from a throwaway
network.db. No network beyond loopback test servers, no real webhook, no reason-network binary.
Run: python3 test_relay.py"""
import hashlib, http.server, io, json, os, random, re, sqlite3, subprocess, sys, tempfile, threading, time, unittest
from unittest import mock
from pathlib import Path

tmp = tempfile.mkdtemp()
os.environ["AGENT_RELAY_STATE"] = tmp
sys.path.insert(0, str(Path(__file__).resolve().parent))
import agent_relay as rr  # noqa: E402

REAL_WEBHOOK_OPEN = rr.webhook_open

KEY = "0123456789abcdef" * 4          # obviously fake test key (64 hex chars, like `openssl rand -hex 32`)
CFG = {"RN_BIN": "/nonexistent", "PERSON": "demo", "LISTEN_NAME": "bot", "CLOUD_NAME": "bot-cloud",
       "INBOX_NAME": "bot-inbox", "CLOUD_MACHINE": "cloud", "DOORBELL_PREVIEW_CHARS": 200,
       "WEBHOOK_URL": "http://127.0.0.1:9/", "WEBHOOK_KEY": "x", "OUTBOUND_KEY": KEY, "MAX_WAKES_PER_HOUR": 20,
       "MIN_SECONDS_BETWEEN_WAKES": 20, "MAX_BATCH": 10, "MAX_PROXY_PER_HOUR": 60,
       "MAX_SPOOL": 50, "WEBHOOK_MAX_BODY_CHARS": 3500,
       "RN_DB": str(Path(tempfile.mkdtemp()) / "missing-network.db")}   # never the real engine database
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
    returncode, stdout, stderr = 0, "→ demo/claude-session: queued\nmessage id 01JX\n", ""


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

    def alerts(self):
        return [a["text"] for a in self.r.s.alerts]

    def test_vector(self):
        sig = rr.sign(KEY, "demo/claude-session", "01JTESTREPLYTO0000000000", "1700000000",
                      "0123456789abcdef01234567", "hello\n第二行")
        self.assertEqual(sig, VECTOR_SIG)

    def test_good_signature_is_forwarded_once(self):
        e = envelope("demo/claude-session", "r1", "hi")
        self.r.handle([self.msg(e)])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0][:4], ("send", "demo/claude-session", "--from", "demo/bot"))
        self.r.handle([self.msg(e, i="m2")])                     # same nonce again: already sent
        self.assertEqual(len(self.sent), 1)
        self.assertEqual((self.notices(), self.alerts(), len(self.r.s.proxies)), ([], [], 1))

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
        self.assertTrue(any("stale" in a for a in self.alerts()))

    def test_group_and_self_refused(self):
        for i, to in enumerate(["*", "demo/*", "repo:a/b", "demo/bot", "demo/bot-cloud"]):
            self.r.handle([self.msg(envelope(to, "", "hi", nonce=f"n{i}"), i=f"g{i}")])
        self.assertEqual(self.sent, [])
        self.assertEqual(len([a for a in self.alerts() if "group or self address" in a]), 5)

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
        rr.webhook_open = lambda req, timeout=None: (self.posts.append(req.data.decode()), FakeResp())[1]

    def m(self, text, i="m1", frm="demo/other", agent="codex", thread="01JTHREAD000000000000000000"):
        return {"id": i, "from": frm, "agent": agent, "thread": thread, "text": text}

    def drain(self):
        while self.r.s.spool:
            self.r.s.wakes = []
            self.r.flush()
        return [json.loads(b) for b in self.posts]

    def test_all_success_statuses_acknowledge_only_the_sent_batch(self):
        for status in (200, 201, 202, 204, 299):
            with self.subTest(status=status):
                self.setUp()
                self.r.c["MAX_BATCH"] = 1
                self.r.s.spool = [self.m("overflow", i="relay-spool-overflow", frm="relay"), self.m("next", i="m2")]
                self.r.s.overflow_dropped = 3
                response = FakeResp()
                response.status = status
                rr.webhook_open = lambda req, timeout=None: (self.posts.append(req.data), response)[1]
                self.assertEqual(self.r.flush(), 5)
                self.assertEqual([m["id"] for m in self.r.s.spool], ["m2"])
                self.assertEqual(self.r.s.overflow_dropped, 0)
                self.assertEqual(len(self.r.s.wakes), 1)
                self.assertEqual([m["id"] for m in rr.State().spool], ["m2"])
                self.r.s.wakes = []
                self.assertEqual(self.r.flush(), 600)
                self.assertEqual(self.r.s.spool, [])
                self.r.flush()
                self.assertEqual(len(self.posts), 2)

    def test_failed_requests_keep_the_batch_for_retry(self):
        for failure in (199, 300, 400, 500, rr.urllib.error.HTTPError(
                CFG["WEBHOOK_URL"], 503, "unavailable", None, io.BytesIO()),
                rr.urllib.error.URLError("offline")):
            with self.subTest(failure=failure):
                self.setUp()
                self.r.s.spool = [self.m("pending")]
                def post(req, timeout=None):
                    if isinstance(failure, Exception):
                        raise failure
                    response = FakeResp()
                    response.status = failure
                    return response
                rr.webhook_open = post
                self.assertEqual(self.r.flush(), 5)
                self.assertEqual([m["id"] for m in self.r.s.spool], ["m1"])
                self.assertEqual([m["id"] for m in rr.State().spool], ["m1"])
                self.assertEqual(len(self.r.s.wakes), 1)
                if isinstance(failure, rr.urllib.error.HTTPError):
                    failure.close()

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

    def test_webhook_goes_through_the_no_redirect_opener_only(self):
        src = (Path(__file__).resolve().parent / "agent_relay.py").read_text(encoding="utf-8")
        self.assertNotIn("urlopen(", src)
        self.assertNotIn("install_opener", src)

    def servers(self, code):
        """A answers every POST with `code` and a Location pointing at B; B counts whatever reaches it."""
        hits = {"a": [], "b": []}
        holder = {}
        class A(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                hits["a"].append(("POST", self.headers))
                self.send_response(code)
                self.send_header("Location", holder["b"])
                self.send_header("Content-Length", "0")
                self.end_headers()
            def log_message(self, *a):
                pass
        class B(http.server.BaseHTTPRequestHandler):
            def hit(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                hits["b"].append((self.command, self.headers))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
            do_GET = do_POST = do_HEAD = hit
            def log_message(self, *a):
                pass
        out = []
        for handler in (A, B):
            srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
            self.addCleanup(srv.server_close)
            self.addCleanup(srv.shutdown)
            out.append(srv)
        holder["b"] = f"http://127.0.0.1:{out[1].server_port}/b-landing-xyz"
        return f"http://127.0.0.1:{out[0].server_port}/hook", holder["b"], hits

    def test_webhook_redirects_are_not_followed(self):
        loopback = {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
        with mock.patch.dict(os.environ, loopback):
            url, landing, hits = self.servers(302)
            req = rr.urllib.request.Request(url, data=b"{}", method="POST",
                                            headers={"Authorization": "Bearer control-key"})
            try:
                rr.urllib.request.build_opener().open(req, timeout=10).close()
            except rr.urllib.error.HTTPError as e:
                e.close()
            self.assertEqual(len(hits["a"]), 1)
            self.assertEqual(len(hits["b"]), 1)
            self.assertEqual(hits["b"][0][1].get("Authorization"), "Bearer control-key")
            for code in (301, 302, 303, 307, 308):
                for alerts in (False, True):
                    with self.subTest(code=code, alerts=alerts):
                        self.setUp()
                        url, landing, hits = self.servers(code)
                        self.r.c.update(WEBHOOK_URL=url, WEBHOOK_KEY="test-webhook-key-123")
                        if alerts:
                            self.r.s.alert("k", "an alert")
                        else:
                            self.r.s.spool = [self.m("pending")]
                        with mock.patch.object(rr, "webhook_open", REAL_WEBHOOK_OPEN), \
                                self.assertLogs("agent-relay", "INFO") as lg:
                            self.assertEqual(self.r.flush(), 5)
                        self.assertIsNot(rr.webhook_open, REAL_WEBHOOK_OPEN)
                        self.assertEqual(len(hits["a"]), 1)
                        self.assertEqual(hits["a"][0][1].get("X-Automation-Key"), "test-webhook-key-123")
                        self.assertEqual(hits["a"][0][1].get("Authorization"), "Bearer test-webhook-key-123")
                        self.assertEqual(hits["b"], [])
                        self.assertEqual(len(self.r.s.wakes), 1)
                        if alerts:
                            self.assertEqual([(a["key"], a["posted"]) for a in rr.State().alerts], [("k", None)])
                        else:
                            self.assertEqual([m["id"] for m in rr.State().spool], ["m1"])
                        log = "\n".join(lg.output)
                        self.assertIn(f"code={code}", log)
                        self.assertIn(f"webhook POST failed: {code}", log)
                        self.assertIn("redirects are not followed", log)
                        self.assertNotIn("b-landing-xyz", log)
                        self.assertNotIn(landing.split("/b-")[0], log)
                        self.assertNotIn("test-webhook-key-123", log)

    def test_fwd_vector(self):
        body = rr.fwd_body(KEY, "01JTESTMSG0000000000000000", "demo/claude-session", "claude-code", "", "你好\n x ",
                           ts=1700000000)
        self.assertEqual(body.split("\nsig: ")[1].split("\n")[0], FWD_VECTOR_SIG)


class Crash(BaseException):
    """Stands in for the process dying: not an Exception, so nothing in the relay catches it."""


# The engine's messages table (the columns the relay reads), as in box/test_rn_fetch.py.
SCHEMA = """CREATE TABLE messages (id TEXT PRIMARY KEY, sender TEXT NOT NULL, sender_agent TEXT, sender_repo TEXT,
recipient TEXT NOT NULL, via TEXT, reply_to TEXT, body TEXT NOT NULL, wake INTEGER NOT NULL, sent_at INTEGER NOT NULL,
read_at INTEGER, held INTEGER NOT NULL DEFAULT 0);"""

# State exactly as released in v0.1.1 (before seen_t and recv_floor), to check that a downgrade still works.
PREV_STATE = r'''
class State:
    def __init__(self, max_spool=1000):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(STATE_DIR, 0o700)
        self.path = STATE_DIR / "state.json"
        try:
            d = json.loads(self.path.read_text())
        except Exception:
            d = {}
        self.seen = d.get("seen", [])            # message ids already handled (dedupe)
        self.nonces = d.get("nonces", {})        # envelope nonce -> ts (replay guard)
        self.wakes = d.get("wakes", [])          # timestamps of webhook POSTs
        self.proxies = d.get("proxies", [])      # timestamps of proxied sends
        self.spool = d.get("spool", [])          # items waiting to be POSTed
        self.overflow_dropped = d.get("overflow_dropped", 0)   # dropped since the overflow notice last went out
        self.refwds = d.get("refwds", {})        # message id -> [timestamps of honoured RN-REFWD]
        self.max_spool = max_spool

    def trim_spool(self):
        """Never drop silently: past MAX_SPOOL the oldest items go, get logged, and one relay notice says so."""
        cap = max(10, self.max_spool)
        if len(self.spool) <= cap:
            return
        notice = next((i for i in self.spool if i.get("id") == "relay-spool-overflow"), None)
        rest = [i for i in self.spool if i is not notice]
        drop = len(rest) - (cap - 1)
        dropped, rest = rest[:drop], rest[drop:]
        self.overflow_dropped += len(dropped)
        uniq = list(dict.fromkeys(str(i.get("id")) for i in dropped))
        ids = ", ".join(uniq[:20]) + (" ..." if len(uniq) > 20 else "")
        logging.getLogger("agent-relay").warning(
            "spool over %d: dropped %d oldest item(s) (%d since last delivery): %s",
            cap, len(dropped), self.overflow_dropped, ids)
        notice = notice_item("relay-spool-overflow",
                             f"relay spool was full ({cap} items, webhook down or rate-limited for long): "
                             f"dropped {self.overflow_dropped} oldest item(s) so far. Last dropped ids: {ids}. "
                             f"Ask the senders to resend if needed; see the relay log on the Mac.")
        self.spool = [notice] + rest   # the notice goes out first, in the next webhook batch

    def save(self):
        now = time.time()
        self.seen = self.seen[-2000:]
        self.nonces = {n: t for n, t in self.nonces.items() if now - t < 3600}
        self.wakes = [t for t in self.wakes if now - t < 3600]
        self.proxies = [t for t in self.proxies if now - t < 3600]
        self.refwds = {k: v for k, v in self.refwds.items() if v and now - max(v) < 8 * 86400}
        self.trim_spool()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.__dict__ | {"path": None, "max_spool": None}, ensure_ascii=False))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
'''


def prev_state_class():
    ns = {"STATE_DIR": rr.STATE_DIR, "json": json, "os": os, "time": time, "logging": rr.logging,
          "notice_item": rr.notice_item}
    exec(PREV_STATE, ns)
    return ns["State"]


class Hardening(Doorbell):
    def setUp(self):
        super().setUp()
        self.db_path = str(Path(tempfile.mkdtemp()) / "network.db")
        self.cfg = dict(CFG, RN_DB=self.db_path)
        self.r.c["RN_DB"] = self.db_path
        self.r.s.recv_floor = time.time() - 3600              # upgraded an hour ago
        self.r.s.save()

    def restart(self):
        """A new relay process: everything it knows comes from disk."""
        r = rr.Relay(dict(self.cfg))
        r.rn = lambda *a, **k: (self.calls.append((a, k)), self.result)[1]
        return r

    def put(self, *msgs, ago=60, held=0, sent_at=None):
        db = sqlite3.connect(self.db_path)
        db.executescript(SCHEMA if not db.execute("SELECT 1 FROM sqlite_master WHERE name='messages'").fetchone()
                         else "")
        for n, m in enumerate(msgs):
            db.execute("INSERT INTO messages (id, sender, sender_agent, recipient, reply_to, body, wake, sent_at, "
                       "read_at, held) VALUES (?,?,?,?,?,?,1,?,?,?)",
                       (m["id"], m["from"], m["agent"], "demo/bot", m["thread"], m["text"],
                        sent_at if sent_at is not None else int((time.time() - ago) * 1000) + n,
                        None if held else 1, held))
        db.commit(); db.close()

    def doorbells(self, r=None):
        return [i["id"] for i in (r or self.r).s.spool if i.get("doorbell")]

    def notices(self, r=None):
        return [i["text"] for i in (r or self.r).s.spool if not i.get("doorbell")]

    def forwarded(self):
        return [k["stdin"].split("\n")[1] for a, k in self.calls if (k.get("stdin") or "").startswith("RN-FWD v1")]

    def cloud_env(self, i="o1", to="demo/claude-a"):
        return {"id": i, "from": "demo/bot-cloud", "agent": None, "thread": None, "text": envelope(to, "", "hi")}

    def test_proxy_timeout_keeps_rest_of_batch(self):
        def rn(*a, **k):
            if "--from" in a:                                      # the proxied send hangs
                raise subprocess.TimeoutExpired(a, 60)
            self.calls.append((a, k))
            return RunOK()
        self.r.rn = rn
        self.r.handle([self.cloud_env(), self.m("after the timeout", i="m2")])
        self.assertEqual(self.r.s.seen, ["o1", "m2"])
        self.assertEqual(self.doorbells(), ["m2"])
        self.assertEqual(self.forwarded(), ["id: m2"])
        self.assertEqual(len([a for a in self.r.s.alerts if "o1" in a["text"] and "not confirmed" in a["text"]]), 1)

    def test_unexpected_error_is_isolated_and_reported(self):
        def broken(m): raise RuntimeError("boom")
        self.r.proxy = broken
        with self.assertLogs("agent-relay", "ERROR"):
            self.r.handle([self.cloud_env(), self.m("still forwarded", i="m2")])
        self.assertEqual(self.doorbells(), ["m2"])
        self.assertTrue(any("o1" in n and "RuntimeError" in n for n in self.notices()))
        self.assertEqual(self.restart().s.seen, ["o1", "m2"])  # saved

    def test_doorbell_saved_before_forward(self):
        def dies(*a, **k): raise Crash()
        self.r.rn = dies
        with self.assertRaises(Crash):
            self.r.handle([self.m("text", i="m1")])
        restored = self.restart()                              # what a restarted relay would ring
        self.assertEqual(self.doorbells(restored), ["m1"])
        self.assertIn("m1", restored.s.seen)
        self.assertTrue((Path(tmp) / "fwd-copies" / "m1.json").exists())

    def test_reconcile_recovers_rest_of_batch(self):
        a, b = self.m("first", i="A"), self.m("second", i="B")
        self.put(a, b)                                         # listen marked both read; the rows stay
        def rn(*args, **k):
            if (k.get("stdin") or "").startswith("RN-FWD v1\nid: A\n"):
                raise Crash()                                  # dies while forwarding A
            self.calls.append((args, k))
            return self.result
        self.r.rn = rn
        with self.assertRaises(Crash):
            self.r.handle([a, b])
        r2 = self.restart()
        r2.reconcile()                                         # no new input after the restart
        self.assertEqual(self.doorbells(r2), ["A", "B"])       # A once (saved before its forward), B recovered
        self.assertEqual(self.forwarded(), ["id: B"])
        self.assertTrue((Path(tmp) / "fwd-copies" / "B.json").exists())
        self.assertEqual(r2.s.seen, ["A", "B"])
        self.assertEqual(self.restart().s.seen, ["A", "B"])

    def test_reconcile_before_first_save(self):
        a = self.m("lost between listen and handle", i="A")
        self.put(a)                                            # listen returned A, then the relay died
        r2 = self.restart()
        r2.reconcile()
        self.assertEqual(self.doorbells(r2), ["A"])
        self.assertEqual(self.forwarded(), ["id: A"])
        item = r2.s.spool[-1]
        self.assertEqual((item["from"], item["agent"], item["thread"], item["text_sha256"]),
                         ("demo/other", "codex", "01JTHREAD000000000000000000", sha(a["text"])))
        r2.reconcile()                                         # a second start finds nothing new
        self.assertEqual(self.doorbells(r2), ["A"])

    def test_reconcile_skips_pre_upgrade_and_seen(self):
        clean_state()
        (Path(tmp) / "state.json").write_text(json.dumps({"seen": ["S"], "spool": []}))   # an earlier version
        t0 = time.time()
        r2 = self.restart()
        t1 = time.time()
        on_disk = json.loads((Path(tmp) / "state.json").read_text())
        self.assertTrue(t0 <= on_disk["recv_floor"] <= t1)      # set and saved on the first start
        self.assertEqual(on_disk["seen_t"].keys(), {"S"})
        self.put(self.m("before the upgrade", i="P"), ago=12 * 3600)
        self.put(self.m("already handled", i="S"), ago=0)
        r2.reconcile()
        self.assertEqual(r2.s.spool, [])
        self.assertEqual(self.calls, [])

    def test_state_readable_by_previous_version(self):
        self.r.handle([self.m("x", i="m1")])
        on_disk = json.loads((Path(tmp) / "state.json").read_text())
        self.assertEqual(on_disk["seen"], ["m1"])
        self.assertIn("m1", on_disk["seen_t"])
        old = prev_state_class()()                             # downgrade: the previous relay starts
        old.seen.append("m2")
        old.save()
        on_disk = json.loads((Path(tmp) / "state.json").read_text())
        self.assertEqual(on_disk["seen"], ["m1", "m2"])
        self.assertNotIn("seen_t", on_disk)
        self.assertNotIn("recv_floor", on_disk)
        t0 = time.time()
        new = rr.State()                                       # upgrade again: treated as a first upgrade
        self.assertEqual(new.seen, ["m1", "m2"])
        self.assertEqual(set(new.seen_t), {"m1", "m2"})
        self.assertGreaterEqual(new.recv_floor, t0)
        self.assertIn("recv_floor", json.loads((Path(tmp) / "state.json").read_text()))

    def test_reconcile_leaves_held_messages_until_approved(self):
        self.put(self.m("waiting for approval", i="H"), held=1)    # [inbound] default = "hold"
        r2 = self.restart()
        r2.reconcile()
        self.assertEqual((r2.s.spool, self.calls), ([], []))
        self.assertNotIn("H", r2.s.seen)
        self.assertNotIn("H", self.restart().s.seen)
        db = sqlite3.connect(self.db_path)
        db.execute("UPDATE messages SET held = 0 WHERE id = 'H'"); db.commit(); db.close()   # approved
        r2.reconcile()
        self.assertEqual(self.doorbells(r2), ["H"])
        self.assertEqual(self.forwarded(), ["id: H"])

    def test_listen_failure_recovers_the_marked_batch(self):
        for failure in ("timeout", "rc"):
            with self.subTest(failure):
                self.setUp()
                self.put()                                     # empty messages table at startup
                listens = []
                def rn(*a, **k):
                    if a[0] != "listen":
                        self.calls.append((a, k))
                        return self.result
                    listens.append(a)
                    if len(listens) > 1:
                        raise Crash()                          # no new mail after the failed listen
                    self.put(self.m("a", i="A"), self.m("b", i="B"))   # marked read, then listen fails
                    if failure == "timeout":
                        raise subprocess.TimeoutExpired(a, 1)
                    return RunOut(1, "Error: daemon went away")
                self.r.rn = rn
                with mock.patch.object(rr.time, "sleep"), self.assertRaises(Crash):
                    self.r.run()
                self.assertEqual(self.forwarded(), ["id: A", "id: B"])
                rung = [i["id"] for b in self.drain() for i in b["messages"] if b["doorbell"]]
                self.assertEqual(rung, ["A", "B"])
                self.r.reconcile()                             # idempotent
                self.assertEqual(self.forwarded(), ["id: A", "id: B"])
                self.assertEqual(self.r.s.spool, [])

    def test_reconcile_reads_only_the_window_in_pages(self):
        old = int((time.time() - 30 * 86400) * 1000)
        self.put(*[self.m("expired", i=f"x{k:04d}") for k in range(500)], sent_at=old)
        now = int(time.time() * 1000)
        self.put(*[self.m("in window", i=f"w{k}") for k in range(3)], sent_at=now - 5000)   # same sent_at
        self.put(*[self.m("in window", i=f"w{k}") for k in range(3, 5)], ago=1)
        fetched, real = [], sqlite3.connect
        class Conn:
            def __init__(self, *a, **k): self.c = real(*a, **k)
            def close(self): self.c.close()
            def execute(self, sql, *args):
                cur = self.c.execute(sql, *args)
                if not sql.startswith("SELECT id,"):          # count only the message rows read
                    return cur
                class Cur:
                    def fetchall(_): rows = cur.fetchall(); fetched.append(len(rows)); return rows
                    def fetchmany(_, n=1): rows = cur.fetchmany(n); fetched.append(len(rows)); return rows
                    def __iter__(_): raise AssertionError("rows must be fetched in counted pages")
                return Cur()
        with mock.patch.object(rr, "RECONCILE_PAGE", 2, create=True), mock.patch.object(rr.sqlite3, "connect", Conn):
            self.restart().reconcile()
        self.assertEqual(self.forwarded(), [f"id: w{k}" for k in range(5)])
        self.assertEqual(sum(fetched), 5)                      # no expired row is ever read
        self.assertLessEqual(max(fetched), 2)

    def test_reconcile_ends_while_new_mail_keeps_arriving(self):
        self.put(*[self.m("before", i=f"b{k}") for k in range(3)])
        inserted = []
        def rn(*a, **k):
            if len(inserted) >= 50:
                raise Crash()                                  # the old unbounded loop: stop instead of hanging
            self.calls.append((a, k))
            n = len(inserted)
            inserted.append(n)                                 # each forward lands a new message after the bound
            self.put(self.m("new", i=f"n{n}"), sent_at=int((time.time() + 60) * 1000))
            return self.result
        r2 = self.restart()
        r2.rn = rn
        with mock.patch.object(rr, "RECONCILE_PAGE", 2, create=True), \
                self.assertLogs("agent-relay", "INFO") as lg:
            r2.reconcile()
        self.assertEqual(self.forwarded(), ["id: b0", "id: b1", "id: b2"])
        self.assertEqual(len(inserted), 3)
        self.assertNotIn("n0", r2.s.seen)                      # left unread for the next listen
        (line,) = [l for l in lg.output if "network.db: handled" in l]
        self.assertIn("handled 3 message(s)", line)

    def test_reconcile_ends_when_new_mail_shares_a_frozen_sent_at(self):
        stuck = int((time.time() - 60) * 1000)                # a sender whose clock does not move
        self.put(*[self.m("before", i=f"b{k}") for k in range(3)], sent_at=stuck)
        inserted, arriving = [], [True]
        def rn(*a, **k):
            if len(inserted) >= 50:
                raise Crash()                                  # unbounded loop: stop instead of hanging
            self.calls.append((a, k))
            if arriving[0]:
                inserted.append(len(inserted))                 # same sent_at, larger id: sorts after b2
                self.put(self.m("new", i=f"z{len(inserted):03d}"), sent_at=stuck)
            return self.result
        r2 = self.restart()
        r2.rn = rn
        with mock.patch.object(rr, "RECONCILE_PAGE", 2, create=True):
            r2.reconcile()
            self.assertEqual(self.forwarded(), ["id: b0", "id: b1", "id: b2"])
            self.assertEqual(len(inserted), 3)
            self.assertFalse(any(i.startswith("z") for i in r2.s.seen))
            arriving[0] = False                                # the next recovery takes the rest, once each
            r2.reconcile()
            r2.reconcile()
        self.assertEqual(self.forwarded(), ["id: b0", "id: b1", "id: b2", "id: z001", "id: z002", "id: z003"])

    def test_reconcile_proxies_an_envelope_cut_off_before_its_send_record(self):
        a = self.m("first", i="A")
        o = {"id": "O", "from": "demo/bot-cloud", "agent": None, "thread": None,
             "text": envelope("demo/claude-a", "", "hi", nonce="77" * 12)}
        self.put(a, o)
        def rn(*args, **k):
            if (k.get("stdin") or "").startswith("RN-FWD v1\nid: A\n"):
                raise Crash()
            self.calls.append((args, k))
            return RunOut(0, "→ demo/claude-a: queued")
        self.r.rn = rn
        with self.assertRaises(Crash):
            self.r.handle([a, o])
        self.assertNotIn("77" * 12, self.restart().s.sends)
        r2 = self.restart()
        r2.rn = rn
        r2.reconcile()
        r2.reconcile()
        self.restart().reconcile()
        self.assertEqual(len([c for c in self.calls if "--from" in c[0]]), 1)
        self.assertEqual(r2.s.sends["77" * 12]["state"], "sent")

    def test_reconcile_db_unavailable(self):
        self.r.c["RN_DB"] = str(Path(self.db_path).with_name("nope.db"))
        listens = []
        def rn(*a, **k):
            if a[0] == "listen":
                listens.append(a)
                raise Crash()                                  # stop the loop at its first listen
            return self.result
        self.r.rn = rn
        with self.assertLogs("agent-relay", "WARNING") as lg, self.assertRaises(Crash):
            self.r.run()
        self.assertEqual(len(listens), 1)                      # the relay went on to listen
        self.assertTrue(any("could not check" in l for l in lg.output))
        (b,) = [json.loads(p) for p in self.posts]
        self.assertFalse(b["doorbell"])
        (n,) = b["messages"]
        self.assertIn("could not check network.db", n["text"])


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
            self.r.handle([{"id": "bad", "from": "demo/bot-cloud", "agent": None, "thread": None,
                            "text": "RN-GOT v1\nstored: A"}])
        self.assertEqual(len(lg.output), 7)
        self.assertEqual(json.dumps([self.r.s.pending, self.r.s.alerts, self.r.s.spool, self.r.s.got_last]), before)
        self.got(stored=["A"], nonce="ab" * 12)
        self.assertEqual(self.p("A")["stage"], "stored")
        with self.assertLogs("agent-relay", "WARNING"):
            self.got(done=["A"], nonce="ab" * 12)
        self.assertIn("A", self.r.s.pending)
        self.assertEqual([i for i in self.r.s.spool if not i.get("doorbell")], [])
        for frm in ("demo/bot-cloud", "demo/bot-inbox", "demo/terminal-3", "demo/terminal-12"):
            self.inbound(f"X{frm[-1]}")
            self.got(done=[f"X{frm[-1]}"], frm=frm)
            self.assertNotIn(f"X{frm[-1]}", self.r.s.pending)

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
                raise Crash()
            return RunOK()
        self.hook = stop
        with self.assertRaises(Crash):
            self.r.run()
        self.assertTrue(5 <= waits[0] <= 301, waits)

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


class OutboundIdempotency(unittest.TestCase):
    """RN-OUT send records: a nonce already sent is dropped, an unknown result is never resent, a failure may be."""
    N = "0123456789abcdef01234567"

    def setUp(self):
        clean_state()
        self.now = time.time()
        clock = mock.patch.object(rr.time, "time", lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.cfg = dict(CFG)
        self.calls, self.posts, self.result, self.k = [], [], FakeRun(), 0
        rr.webhook_open = self.post
        self.r = self.relay()

    def relay(self):
        r = rr.Relay(dict(self.cfg))
        r.rn = self.rn
        return r

    def rn(self, *a, **k):
        self.calls.append((a, k))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result

    def post(self, req, timeout=None):
        self.posts.append(json.loads(req.data.decode()))
        return FakeResp()

    def out(self, nonce=N, to="demo/claude-a", reply_to="A", body="answer", ts=None, key=KEY):
        self.k += 1
        ts = int(self.now) if ts is None else ts
        self.r.handle([{"id": f"o{self.k}", "from": "demo/bot-cloud", "agent": None, "thread": None,
                        "text": envelope(to, reply_to, body, ts=ts, nonce=nonce, key=key)}])

    def proxied(self):
        return [a for a, k in self.calls if "--from" in a]

    def keys(self, r=None):
        return [a["key"] for a in (r or self.r).s.alerts]

    def notices(self):
        return [i["text"] for i in self.r.s.spool if not i.get("doorbell")]

    def state(self, nonce=N, r=None):
        rec = (r or self.r).s.sends.get(nonce)
        return rec and rec["state"]

    def confirm(self):
        """Delivers what is due and confirms every alert batch with a done receipt, as the routine would."""
        for _ in range(40):
            if not self.r.ready_alerts():
                break
            self.r.s.wakes = []
            self.r.flush()
            item = self.posts[-1]["messages"][0]
            if not item["id"].startswith("relay-alerts-"):
                continue
            self.r.handle([{"id": f"got{len(self.posts)}", "from": "demo/terminal", "agent": None, "thread": None,
                            "text": rr.got_body(KEY, [], [item["id"]], ts=int(self.now))}])

    def test_same_nonce_twice_is_sent_once_silently(self):
        self.out()
        self.out()
        self.assertEqual(len(self.proxied()), 1)
        self.assertEqual((self.state(), self.notices(), self.keys(), len(self.r.s.proxies)), ("sent", [], [], 1))
        disk = json.loads((Path(tmp) / "state.json").read_text())
        self.assertEqual((disk["sends"][self.N]["state"], disk["sends"][self.N]["to"]), ("sent", "demo/claude-a"))
        self.assertIn(self.N, disk["nonces"])

    def test_clear_refusal_is_failed_and_may_be_resent(self):
        words = ("refused", "not on this machine", "no session", "nobody matches", "ambiguous")
        for rc in (0, 1):
            for word in words:
                with self.subTest(rc=rc, word=word):
                    self.setUp()
                    self.result = RunOut(rc, f"→ demo/claude-a: {word.upper()} (test)")
                    self.out()
                    self.assertEqual((self.state(), self.keys()), ("failed", [f"failed:{self.N}"]))
                    self.result = FakeRun()
                    self.out()
                    self.assertEqual((len(self.proxied()), self.state()), (2, "sent"))

    def test_rn_missing_is_failed_and_may_be_resent(self):
        self.result = FileNotFoundError("rn")
        self.out()
        self.assertEqual((self.state(), self.keys()), ("failed", [f"failed:{self.N}"]))
        self.result = FakeRun()
        self.out()
        self.assertEqual((len(self.proxied()), self.state()), (2, "sent"))

    def test_unknown_results_are_reported_once_and_never_resent(self):
        for name, result in (("held", RunOut(0, "→ demo/claude-a: held for approval")),
                             ("no arrow", RunOut(0, "queued")),
                             ("error", RunOut(0, "→ demo/claude-a: queued (error: x)")),
                             ("rc", RunOut(2, "daemon went away")),
                             ("timeout", subprocess.TimeoutExpired(("rn",), 60))):
            with self.subTest(name):
                self.setUp()
                self.result = result
                self.out()
                self.assertEqual((self.state(), self.keys()), ("unknown", [f"unknown:{self.N}"]))
                self.assertIn("not confirmed", self.r.s.alerts[0]["text"])
                self.result = FakeRun()
                self.out()
                self.out()
                self.assertEqual(len(self.proxied()), 1)
                self.assertEqual((self.keys(), self.notices()), ([f"unknown:{self.N}"], []))

    def test_send_record_is_saved_before_the_engine_runs(self):
        self.result = Crash()
        with self.assertRaises(Crash):
            self.out(to="demo/claude-b")
        self.result = FakeRun()
        r2 = self.r = self.relay()
        self.assertEqual((self.state(r=r2), r2.s.sends[self.N]["noticed"]), ("unknown", True))
        self.assertEqual(self.keys(r2), [f"unknown:{self.N}"])
        self.assertIn("o1 to demo/claude-b was being sent when the relay stopped", r2.s.alerts[0]["text"])
        self.out(to="demo/claude-b")
        self.assertEqual((len(self.proxied()), self.keys(r2)), (1, [f"unknown:{self.N}"]))

    def test_crash_after_the_engine_accepted_and_before_the_result_was_saved(self):
        real_save, seen = rr.State.save, []
        def save(state):
            if len(seen) == 1:
                disk = json.loads((Path(tmp) / "state.json").read_text())
                seen.append((len(self.proxied()), disk["sends"][self.N]["state"]))
                raise Crash()
            seen.append(None)
            real_save(state)
        with mock.patch.object(rr.State, "save", save), self.assertRaises(Crash):
            self.out(to="demo/claude-b")
        self.assertEqual(seen, [None, (1, "sending")])
        r2 = self.r = self.relay()
        self.assertEqual((self.state(r=r2), r2.s.sends[self.N]["noticed"]), ("unknown", True))
        self.assertEqual(self.keys(r2), [f"unknown:{self.N}"])
        self.assertEqual([a["key"] for a in rr.State().alerts], [f"unknown:{self.N}"])
        self.out(to="demo/claude-b")
        self.assertEqual((len(self.proxied()), self.state(r=r2), self.keys(r2)), (1, "unknown", [f"unknown:{self.N}"]))

    def done(self, batch):
        self.r.handle([{"id": f"got-{batch}-{self.k}", "from": "demo/terminal", "agent": None, "thread": None,
                        "text": rr.got_body(KEY, [], [batch], ts=int(self.now))}])

    def test_unknown_after_a_stale_refusal_replaces_it(self):
        self.out(ts=int(self.now) - 1000)
        self.assertEqual(self.keys(), [f"reject:{self.N}"])
        self.result = subprocess.TimeoutExpired(("rn",), 60)
        self.out()
        self.assertEqual((self.keys(), len(self.proxied()), self.state()), ([f"unknown:{self.N}"], 1, "unknown"))
        self.assertTrue(self.r.s.alerts[0]["text"].startswith(rr.SUPERSEDED))
        self.assertEqual(rr.State().alerts[0]["text"], self.r.s.alerts[0]["text"])

    def test_unknown_after_an_engine_refusal_replaces_it(self):
        self.result = RunOut(0, "→ demo/claude-a: refused: these two agents have exchanged a lot of messages")
        self.out()
        self.assertEqual(self.keys(), [f"failed:{self.N}"])
        self.result = RunOut(0, "→ demo/claude-a: queued: held for approval")
        self.out()
        self.assertEqual((self.keys(), len(self.proxied()), self.state()), ([f"unknown:{self.N}"], 2, "unknown"))
        self.assertTrue(self.r.s.alerts[0]["text"].startswith(rr.SUPERSEDED))
        self.result = FakeRun()
        self.out()
        self.assertEqual((self.keys(), len(self.proxied())), ([f"unknown:{self.N}"], 2))

    def test_replacement_note_reaches_the_posted_alert_batch(self):
        self.cfg["WEBHOOK_MAX_BODY_CHARS"] = 600
        self.r = self.relay()
        to = "demo/" + "c" * 40 + "@" + "m" * 40
        self.out(to=to, ts=int(self.now) - 1000)
        self.result = subprocess.TimeoutExpired(("rn",), 60)
        self.out(to=to)
        self.assertEqual(self.r.c["WEBHOOK_MAX_BODY_CHARS"], 600)
        self.r.s.wakes = []
        self.r.flush()
        (b,) = self.posts
        self.assertLessEqual(len(json.dumps(b, ensure_ascii=False)), 600)
        self.assertTrue(b["messages"][0]["text"].startswith(rr.SUPERSEDED))
        self.assertNotIn("refused: stale", b["messages"][0]["text"])

    def test_unknown_after_a_crash_during_the_resend_replaces_the_failure(self):
        self.result = RunOut(1, "Error: no session called 'demo/claude-a'")
        self.out()
        self.assertEqual(self.keys(), [f"failed:{self.N}"])
        self.result = Crash()
        with self.assertRaises(Crash):
            self.out()
        self.assertEqual(json.loads((Path(tmp) / "state.json").read_text())["sends"][self.N]["state"], "sending")
        self.result = FakeRun()
        self.r = self.relay()
        self.assertEqual(self.keys(), [f"unknown:{self.N}"])
        self.assertTrue(self.r.s.alerts[0]["text"].startswith(rr.SUPERSEDED))
        self.assertIn("was being sent when the relay stopped", self.r.s.alerts[0]["text"])
        self.assertEqual([a["key"] for a in rr.State().alerts], [f"unknown:{self.N}"])

    def test_replaced_alert_batches_confirm_only_what_is_left(self):
        self.result = RunOut(1, "Error: no session called 'demo/claude-a'")
        self.out()
        self.r.s.wakes = []
        self.r.flush()
        first = self.posts[-1]["messages"][0]["id"]
        self.assertEqual(self.r.s.alerts[0]["batch"], first)
        self.result = subprocess.TimeoutExpired(("rn",), 60)
        self.out()
        self.assertEqual(self.keys(), [f"unknown:{self.N}"])
        self.now += 30
        self.r.s.wakes = []
        self.r.flush()
        second = self.posts[-1]["messages"][0]["id"]
        self.assertNotEqual(first, second)
        self.assertEqual(self.r.s.alerts[0]["batch"], second)
        self.done(first)
        self.assertEqual(self.keys(), [f"unknown:{self.N}"])
        self.done(second)
        self.assertEqual(self.r.s.alerts, [])

    def test_unknown_without_an_earlier_report_has_no_replacement_note(self):
        self.result = subprocess.TimeoutExpired(("rn",), 60)
        self.out()
        self.assertEqual(self.keys(), [f"unknown:{self.N}"])
        self.assertNotIn("Replaces", self.r.s.alerts[0]["text"])
        self.result = Crash()
        with self.assertRaises(Crash):
            self.out(nonce="ab" * 12)
        self.r = self.relay()
        self.assertEqual(self.keys(), [f"unknown:{self.N}", f"unknown:{'ab' * 12}"])
        self.assertFalse(any("Replaces" in a["text"] for a in self.r.s.alerts))

    def test_recipient_and_reply_to_must_be_plain(self):
        bad = [(to, "A") for to in ("--help", "-x", "demo/-x", "-demo/x", "demo/Codex", "demo/a b", "demo/x/y",
                                    "demo/x@", "demo/x@-m", "demo/x@m@n", "demo/*@mac", "*@mac", "repo:x@mac",
                                    "codex", "/x", "demo/", "demo/xé", "ｄemo/x")]
        bad += [("demo/codex", "--help"), ("demo/codex", "a b"), ("demo/codex", "-m1"), ("demo/codex", "a" * 121)]
        for k, (to, reply_to) in enumerate(bad):
            with self.subTest(to=to, reply_to=reply_to):
                nonce = f"bad{k:02d}" + "0" * 19
                self.out(nonce=nonce, to=to, reply_to=reply_to)
                self.assertEqual(self.proxied(), [])
                self.assertNotIn(nonce, self.r.s.sends)
                self.assertEqual(self.keys()[-1], f"reject:{nonce}")
        self.assertEqual(len(self.keys()), len(bad))
        texts = [a["text"] for a in self.r.s.alerts]
        self.assertTrue(all("\n" not in t for t in texts))
        plain = [t for t in texts if "not a plain address/id" in t]
        self.assertEqual(len(plain), len(bad) - 1)
        self.assertTrue(all("Do not resend; tell the user." in t[:rr.ALERT_ITEM_CHARS] for t in plain))
        self.assertIn("repo:x@mac", next(t for t in texts if "group or self address" in t))

    def test_plain_recipients_and_reply_tos_are_sent(self):
        good = [(to, "A") for to in ("demo/codex", "demo/terminal-3", "demo/claude@mac", "other/codex", "a--b/x",
                                     "demo/codex@" + "a" * 39 + "-", "demo/x-@m-", "0/1@2")]
        good += [("demo/codex", "01JTESTREPLYTO0000000000"), ("demo/codex", "m1"), ("demo/codex", ""),
                 ("demo/codex", "a" * 120), ("demo/codex", "relay-alerts-1.x_y")]
        for k, (to, reply_to) in enumerate(good):
            with self.subTest(to=to, reply_to=reply_to):
                self.out(nonce=f"good{k:02d}" + "0" * 18, to=to, reply_to=reply_to)
                args = self.proxied()[-1]
                self.assertEqual(args[1], to)
                self.assertEqual("--reply-to" in args, bool(reply_to))
        self.assertEqual((len(self.proxied()), self.keys()), (len(good), []))

    def test_malformed_recipient_refusal_leaves_the_nonce_free(self):
        self.out(to="--help")
        self.assertEqual((self.proxied(), self.r.s.sends, self.keys()), ([], {}, [f"reject:{self.N}"]))
        self.out(to="demo/codex")
        self.assertEqual((len(self.proxied()), self.state(), self.keys()), (1, "sent", []))

    def test_startup_alert_goes_out_without_new_mail_and_ends_when_confirmed(self):
        self.result = Crash()
        with self.assertRaises(Crash):
            self.out()
        self.r = self.relay()
        self.assertEqual((self.r.s.spool, self.keys()), ([], [f"unknown:{self.N}"]))
        self.assertEqual(self.r.flush(), 600)
        (b,) = self.posts
        self.assertFalse(b["doorbell"])
        self.assertIn("demo/claude-a", b["messages"][0]["text"])
        self.assertEqual(self.r.s.alerts[0]["batch"], b["messages"][0]["id"])
        self.r = self.relay()
        self.assertEqual(self.keys(), [f"unknown:{self.N}"])
        self.r.handle([{"id": "got", "from": "demo/terminal", "agent": None, "thread": None,
                        "text": rr.got_body(KEY, [], [b["messages"][0]["id"]], ts=int(self.now))}])
        self.assertEqual(self.r.s.alerts, [])
        self.r = self.relay()
        self.assertEqual((self.r.s.alerts, self.state()), ([], "unknown"))

    def test_alerts_survive_spool_overflow(self):
        self.cfg["MAX_SPOOL"] = 10
        self.r = self.relay()
        self.out(ts=int(self.now) - 1000)
        self.assertEqual(self.keys(), [f"reject:{self.N}"])
        self.r.s.wakes = [self.now] * 20
        self.r.handle([{"id": f"in{j}", "from": "demo/other", "agent": None, "thread": None, "text": "x"}
                       for j in range(15)])
        self.r.flush()
        self.assertTrue(self.r.s.overflow_dropped)
        self.assertEqual(self.posts, [])
        self.r = self.relay()
        self.assertEqual(self.keys(), [f"reject:{self.N}"])
        self.r.s.wakes = []
        self.confirm()
        self.assertTrue(any("refused: stale" in m["text"] for b in self.posts for m in b["messages"]
                            if m["id"].startswith("relay-alerts-")))
        self.assertEqual(self.r.s.alerts, [])

    def test_later_success_withdraws_reject_and_failed_alerts(self):
        self.out(ts=int(self.now) - 1000)
        self.r.s.wakes = []
        self.r.flush()
        self.assertIsNotNone(self.r.s.alerts[0]["posted"])
        self.out()
        self.assertEqual((self.state(), self.keys()), ("sent", []))
        self.result = RunOut(1, "Error: no session called 'demo/claude-a'")
        self.out(nonce="ab" * 12)
        self.assertEqual(self.keys(), [f"failed:{'ab' * 12}"])
        self.result = FakeRun()
        self.out(nonce="ab" * 12)
        self.assertEqual((self.state("ab" * 12), self.keys()), ("sent", []))
        self.out(nonce="ab" * 12, ts=int(self.now) - 1000)
        self.assertEqual((len(self.proxied()), self.keys(), self.notices()), (3, [], []))

    def test_legacy_nonces_are_unknown_after_the_upgrade(self):
        clean_state()
        (Path(tmp) / "state.json").write_text(json.dumps({"seen": [], "spool": [], "recv_floor": self.now - 10,
                                                          "nonces": {self.N: self.now - 100}}))
        self.r = self.relay()
        self.assertEqual(self.r.s.sends[self.N], {"t": self.now - 100, "state": "unknown", "id": "", "to": "",
                                                  "noticed": False})
        self.r.s.save()
        self.now += 3700
        self.r = self.relay()
        self.r.s.save()
        self.assertNotIn(self.N, self.r.s.nonces)
        self.assertEqual((self.state(), self.r.s.sends[self.N]["noticed"]), ("unknown", False))
        self.out()
        self.assertEqual((self.proxied(), self.keys()), ([], [f"dup-unknown:{self.N}"]))
        self.assertIn("unknown recipient", self.r.s.alerts[0]["text"])
        self.r = self.relay()
        self.out()
        self.assertEqual((self.proxied(), self.keys(), self.r.s.sends[self.N]["noticed"]),
                         ([], [f"dup-unknown:{self.N}"], True))

    def test_old_relay_failure_left_its_nonce(self):
        clean_state()
        (Path(tmp) / "state.json").write_text(json.dumps({"seen": [], "spool": [], "recv_floor": self.now - 10,
                                                          "nonces": {self.N: self.now - 30}}))
        self.r = self.relay()
        self.out()
        self.out()
        self.assertEqual((self.proxied(), self.keys(), self.notices()), ([], [f"dup-unknown:{self.N}"], []))

    def test_unrecorded_old_nonce_also_counts_as_unknown(self):
        self.r.s.nonces[self.N] = self.now - 30
        self.out()
        self.assertEqual((self.proxied(), self.state(), self.keys()), ([], "unknown", [f"dup-unknown:{self.N}"]))

    def test_sending_past_the_keep_time_is_alerted_then_dropped(self):
        clean_state()
        (Path(tmp) / "state.json").write_text(json.dumps({"recv_floor": self.now - 10, "sends": {self.N: {
            "t": self.now - 90000, "state": "sending", "id": "o7", "to": "demo/claude-a", "noticed": False}}}))
        self.r = self.relay()
        self.assertEqual(self.keys(), [f"unknown:{self.N}"])
        self.assertNotIn(self.N, self.r.s.sends)
        disk = json.loads((Path(tmp) / "state.json").read_text())
        self.assertEqual(([a["key"] for a in disk["alerts"]], disk["sends"]), ([f"unknown:{self.N}"], {}))

    def test_bad_signature_is_a_plain_notice_and_changes_nothing(self):
        self.out()
        before = json.dumps([self.r.s.sends, self.r.s.alerts, self.r.s.nonces])
        self.out(key="f" * 64)
        self.out(key="f" * 64, nonce="cd" * 12)
        self.assertEqual(json.dumps([self.r.s.sends, self.r.s.alerts, self.r.s.nonces]), before)
        self.assertEqual(len([n for n in self.notices() if "bad signature" in n]), 2)
        self.assertEqual(len(self.proxied()), 1)

    def test_stale_original_then_fresh_resend(self):
        self.out(ts=int(self.now) - 1000)
        self.assertEqual((self.proxied(), self.r.s.sends, self.keys()), ([], {}, [f"reject:{self.N}"]))
        self.out()
        self.assertEqual((len(self.proxied()), self.state()), (1, "sent"))

    def test_fresh_resend_then_stale_original_is_dropped(self):
        self.out()
        self.out(ts=int(self.now) - 1000)
        self.assertEqual((len(self.proxied()), self.keys(), self.notices()), (1, [], []))

    def test_limit_or_group_refusal_allows_a_resend(self):
        self.r.s.proxies = [self.now - 10] * CFG["MAX_PROXY_PER_HOUR"]
        self.out()
        self.assertEqual((self.proxied(), self.r.s.sends, self.keys()), ([], {}, [f"reject:{self.N}"]))
        self.r.s.proxies = []
        self.out()
        self.assertEqual((len(self.proxied()), self.state(), self.keys()), (1, "sent", []))
        self.out(nonce="ef" * 12, to="demo/*")
        self.assertEqual((self.r.s.sends.get("ef" * 12), self.keys()), (None, [f"reject:{'ef' * 12}"]))
        self.out(nonce="ef" * 12)
        self.assertEqual((len(self.proxied()), self.state("ef" * 12), self.keys()), (2, "sent", []))

    def test_resend_after_the_keep_time_is_sent_again(self):
        self.out()
        self.now += 86400 - 10
        self.out()
        self.assertEqual(len(self.proxied()), 1)
        self.now += 20
        self.out()
        self.assertEqual((len(self.proxied()), self.state()), (2, "sent"))
        self.cfg["NONCE_KEEP_SECONDS"] = 600
        self.r = self.relay()
        self.now += 700
        self.out()
        self.assertEqual(len(self.proxied()), 3)

    def test_state_without_sends_is_readable_and_kept_by_the_previous_version(self):
        clean_state()
        (Path(tmp) / "state.json").write_text(json.dumps({"seen": ["S"], "spool": [], "recv_floor": self.now}))
        self.r = self.relay()
        self.assertEqual(self.r.s.sends, {})
        self.out()
        old = prev_state_class()()
        self.assertIn(self.N, old.nonces)
        old.save()
        disk = json.loads((Path(tmp) / "state.json").read_text())
        self.assertNotIn("sends", disk)
        self.assertIn(self.N, disk["nonces"])

    def test_status_words_in_addresses_do_not_change_the_result(self):
        for to in ("demo/ambiguous-session", "demo/held-out", "demo/error-bot", "demo/refused-x", "demo/no-session",
                   "demo/not-on-this-machine", "demo/nobody-matches", "demo/ambiguous@cloud"):
            with self.subTest(to=to):
                self.setUp()
                self.result = RunOut(0, f"→ {to}: queued into the Codex session: it runs after the current turn\n"
                                        f"message id 01JX\n")
                self.out(to=to)
                self.out(to=to)
                self.assertEqual((len(self.proxied()), self.state(), self.keys()), (1, "sent", []))
        for inbox in ("ambiguous-inbox", "held-inbox", "error-inbox", "refused-inbox"):
            with self.subTest(inbox=inbox):
                self.setUp()
                self.cfg["INBOX_NAME"] = inbox
                self.r = self.relay()
                self.result = RunOut(0, f"→ demo/{inbox}: queued: it gets it the next time it listens")
                self.r.handle([{"id": "m1", "from": "demo/other", "agent": None, "thread": None, "text": "hi"}])
                self.assertNotIn("forward_failed", self.r.s.spool[-1])

    def test_status_is_read_from_the_engine_words_only(self):
        for out, rc, want in (("→ demo/ambiguous-x: refused: these two agents ...", 0, "failed"),
                              ("Error: no session called 'demo/held-x'", 1, "failed"),
                              ("Error: nobody matches \"ambiguous\"", 1, "failed"),
                              ("Error: daemon cannot reach 'demo/refused-x'", 1, "unknown"),
                              ("could not deliver to demo/ambiguous-x", 1, "unknown"),
                              ("→ demo/x: queued: held for approval", 0, "unknown"),
                              ("→ demo/x: queued\nmessage id 01JX", 0, "sent")):
            with self.subTest(out=out):
                self.assertEqual(rr.send_outcome(rc, out), want)

    def test_short_names_equal_to_status_words_keep_the_status(self):
        for name in ("refused", "ambiguous", "nobody", "session", "held", "error"):
            for to in (f"demo/{name}", name):
                for out, rc, want in ((f"→ {to}: refused: these two agents have exchanged a lot of messages", 0, "failed"),
                                      (f"Error: no session called '{to}'", 1, "failed"),
                                      (f"Error: nobody matches \"{to}\"", 1, "failed"),
                                      (f"→ {to}: queued: held for approval", 0, "unknown"),
                                      (f"→ {to}: queued: error while waking the session", 0, "unknown"),
                                      (f"→ {to}: queued into the Codex session: it runs after the current turn\n"
                                       f"message id 01JX", 0, "sent"),
                                      (f"→ {to}: delivered into the session\nmessage id 01JX", 0, "sent")):
                    with self.subTest(to=to, out=out):
                        self.assertEqual(rr.send_outcome(rc, out), want)
        for name in ("refused", "ambiguous", "held", "error"):
            with self.subTest(name=name):
                self.setUp()
                to = f"demo/{name}"
                self.result = RunOut(0, f"→ {to}: refused: these two agents have exchanged a lot of messages\n")
                self.out(to=to)
                self.assertEqual((len(self.proxied()), self.state(), self.keys()), (1, "failed", [f"failed:{self.N}"]))
                self.result = RunOut(0, f"→ {to}: queued\nmessage id 01JX\n")
                self.out(to=to)
                self.out(to=to)
                self.assertEqual((len(self.proxied()), self.state(), self.keys()), (2, "sent", []))

    def test_non_positive_keep_time_falls_back_to_the_default(self):
        env = Path(tempfile.mkdtemp()) / "env"
        env.write_text("RN_BIN=/nonexistent\nPERSON=demo\nWEBHOOK_URL=https://example.invalid/\nWEBHOOK_KEY=x\n"
                       f"OUTBOUND_KEY={KEY}\nNONCE_KEEP_SECONDS=0\n")
        env.chmod(0o600)
        with mock.patch.object(rr, "ENV_FILE", env), self.assertLogs("agent-relay", "WARNING") as lg:
            cfg = rr.load_env()
        self.assertEqual(cfg["NONCE_KEEP_SECONDS"], 86400)
        self.assertIn("NONCE_KEEP_SECONDS=0", lg.output[0])
        self.cfg = cfg
        self.r = self.relay()
        self.out()
        self.out()
        self.assertEqual((len(self.proxied()), self.state()), (1, "sent"))

    def test_cloud_classifies_send_output_with_the_same_code(self):
        import importlib.util, inspect
        spec = importlib.util.spec_from_file_location("rn_fetch_parity", Path(rr.__file__).parent / "box/rn-fetch.py")
        fetch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fetch)
        for name in ("send_status", "send_outcome"):
            self.assertEqual(inspect.getsource(getattr(fetch, name)), inspect.getsource(getattr(rr, name)), name)
        for name in ("SEND_REFUSED", "SEND_UNSURE"):
            self.assertEqual((getattr(fetch, name).pattern, getattr(fetch, name).flags),
                             (getattr(rr, name).pattern, getattr(rr, name).flags), name)
        self.assertEqual(fetch.SEND_ACCEPTED, rr.SEND_ACCEPTED)
        outs = ["→ demo/x: queued", "→ demo/x: Refused by policy", "→ demo/x: held", "", "queued",
                "→ demo/x: queued: held for approval", "→ demo/x: queued (error: x)", "daemon went away",
                "→ demo/error: queued", "→ demo/held-x: queued\nmessage id 01JX", "Error: nobody matches \"x\"",
                "Error: no session called 'demo/held-x'", "Error: daemon cannot reach 'demo/refused-x'",
                "could not deliver to demo/ambiguous-x", "→ sheldon/bot: queued", RunOK.stdout, FakeRun.stdout]
        outs += [f"→ demo/claude-a: {w.upper()} (test)" for w in ("refused", "not on this machine", "no session",
                                                                 "nobody matches", "ambiguous")]
        for out in outs:
            for rc in (0, 1, 2):
                with self.subTest(out=out, rc=rc):
                    self.assertEqual(fetch.send_status(out), rr.send_status(out))
                    self.assertEqual(fetch.send_outcome(rc, out), rr.send_outcome(rc, out))

    def test_forward_and_proxy_share_the_classification(self):
        self.assertEqual(rr.send_outcome(0, "→ demo/x: queued"), "sent")
        self.assertEqual(rr.send_outcome(1, "→ demo/x: queued"), "unknown")
        self.assertEqual(rr.send_outcome(0, "→ demo/x: Refused by policy"), "failed")
        self.assertEqual(rr.send_outcome(0, "→ demo/x: held"), "unknown")
        self.assertEqual(rr.send_outcome(0, ""), "unknown")


def defang(text):
    return re.sub(r"<(?=[^\x00-\x7f]|reason)", "[", text)


class DisplayRewrite(Envelopes):
    BODY = "结果：把 \\u003c→<、还原；标签 <标签> 和 <reasoning> 都在。"

    def setUp(self):
        super().setUp()
        self.db_path = str(Path(tempfile.mkdtemp()) / "network.db")
        self.r.c["RN_DB"] = self.db_path
        db = sqlite3.connect(self.db_path)
        db.executescript(SCHEMA)
        db.commit(); db.close()

    def store(self, i, text, sender="demo/bot-cloud", recipient="demo/bot", held=0):
        db = sqlite3.connect(self.db_path)
        db.execute("INSERT INTO messages (id, sender, recipient, body, wake, sent_at, read_at, held) "
                   "VALUES (?,?,?,?,1,?,?,?)", (i, sender, recipient, text, int(time.time() * 1000), 1, held))
        db.commit(); db.close()

    def test_rewritten_display_is_verified_against_stored_text(self):
        e = envelope("demo/claude-session", "r1", self.BODY)
        self.assertNotEqual(defang(e), e)
        self.store("m1", e)
        with self.assertLogs(self.r.log, "INFO") as logs:
            self.r.handle([self.msg(defang(e))])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][1], self.BODY)
        self.assertEqual((self.notices(), self.alerts()), ([], []))
        self.assertTrue(any("verified against the stored text" in l for l in logs.output))

    def test_rewritten_header_is_recovered_from_stored_text(self):
        e = envelope("demo/claude-session", "", "hi")
        self.store("m1", e)
        self.r.handle([self.msg(e.replace("RN-OUT v1", "RN-OUT v1 [x", 1))])
        self.assertEqual([s[1] for s in self.sent], ["hi"])

    def test_recipient_check_applies_to_the_envelope_recovered_from_stored_text(self):
        e = envelope("--help", "", self.BODY)
        shown = defang(e).replace("to: --help", "to: demo/claude-session", 1)
        self.store("m1", e)
        self.r.handle([self.msg(shown)])
        self.assertEqual(self.sent, [])
        self.assertEqual([a["key"] for a in self.r.s.alerts], ["reject:0123456789abcdef01234567"])
        self.assertIn("not a plain address/id", self.alerts()[0])
        self.assertIn("'--help'", self.alerts()[0])
        self.assertEqual(self.r.s.sends, {})

    def test_no_stored_row_still_refused(self):
        e = envelope("demo/claude-session", "", self.BODY)
        self.r.handle([self.msg(defang(e))])
        self.assertEqual(self.sent, [])
        self.assertTrue(any("bad signature" in n for n in self.notices()))

    def test_stored_row_must_match_sender_recipient_and_not_be_held(self):
        e = envelope("demo/claude-session", "", self.BODY)
        self.store("a", e, sender="demo/bot-cloud-2")
        self.store("b", e, recipient="demo/other")
        self.store("c", e, held=1)
        for i in ("a", "b", "c"):
            self.r.handle([self.msg(defang(e), i=i)])
        self.assertEqual(self.sent, [])
        self.assertEqual(len([n for n in self.notices() if "bad signature" in n]), 3)

    def test_tampered_stored_text_refused(self):
        e = envelope("demo/claude-session", "", self.BODY)
        self.store("m1", e + " (edited)")
        self.r.handle([self.msg(defang(e) + " (edited)")])
        self.assertEqual(self.sent, [])
        self.assertTrue(any("bad signature" in n for n in self.notices()))

    def test_wrong_key_refused_even_when_stored(self):
        e = envelope("demo/claude-session", "", self.BODY, key="f" * 64)
        self.store("m1", e)
        self.r.handle([self.msg(defang(e))])
        self.assertEqual(self.sent, [])

    def test_unreadable_database_refuses_without_crashing(self):
        self.r.c["RN_DB"] = str(Path(self.db_path).with_name("nope.db"))
        e = envelope("demo/claude-session", "", self.BODY)
        with self.assertLogs(self.r.log, "WARNING") as logs:
            self.r.handle([self.msg(defang(e))])
        self.assertEqual(self.sent, [])
        self.assertTrue(any("could not read" in l for l in logs.output))

    def test_stored_text_keeps_replay_and_staleness_checks(self):
        old = envelope("demo/claude-session", "", self.BODY, ts=int(time.time()) - 1000, nonce="n-old")
        self.store("s1", old)
        self.r.handle([self.msg(defang(old), i="s1")])
        self.assertEqual(self.sent, [])
        self.assertTrue(any("stale" in a for a in self.alerts()))
        e = envelope("demo/claude-session", "", self.BODY, nonce="n-dup")
        self.store("d1", e); self.store("d2", e)
        self.r.handle([self.msg(defang(e), i="d1"), self.msg(defang(e), i="d2")])
        self.assertEqual(len(self.sent), 1)


class CustomNames(unittest.TestCase):
    """A relay installed with --relay-name alice: every address follows LISTEN_NAME, CLOUD_NAME and INBOX_NAME."""
    NAMES = {"LISTEN_NAME": "alice", "CLOUD_NAME": "alice-cloud", "INBOX_NAME": "alice-inbox"}
    ME, CLOUD, INBOX, FWD = "demo/alice", "demo/alice-cloud", "demo/alice-inbox", "demo/alice-inbox@cloud"

    def setUp(self):
        clean_state()
        self.r = rr.Relay(dict(CFG, **self.NAMES))
        self.calls, self.posts = [], []
        self.r.rn = lambda *a, **k: (self.calls.append((a, k)), RunOK())[1]
        rr.webhook_open = lambda req, timeout=None: (self.posts.append(json.loads(req.data.decode())), FakeResp())[1]

    def msg(self, text, i="m1", frm="demo/other"):
        return {"id": i, "from": frm, "agent": None, "thread": None, "text": text}

    def notices(self):
        return [i["text"] for i in self.r.s.spool if i["from"] == "relay"]

    def drain(self):
        while self.r.s.spool:
            self.r.s.wakes = []
            self.r.flush()
        return self.posts

    def test_listens_as_the_configured_name(self):
        listens = []
        def rn(*a, **k):
            if a[0] == "listen":
                listens.append(a)
                raise Crash()
            return RunOK()
        self.r.rn = rn
        with mock.patch.object(rr.time, "sleep"), self.assertRaises(Crash):
            self.r.run()
        self.assertEqual(listens[0][:3], ("listen", "--as", self.ME.split("/")[1]))

    def test_inbound_mail_is_forwarded_to_the_inbox_and_rings_with_these_names(self):
        self.r.handle([self.msg("hi")])
        (args, _), = self.calls
        self.assertEqual(args, ("send", self.FWD, "-"))
        (bell,) = self.drain()
        self.assertEqual((bell["to"], bell["reply_via"], bell["inbox"]), (self.ME, self.CLOUD, self.INBOX))

    def test_only_the_configured_cloud_session_is_proxied(self):
        self.r.handle([self.msg(envelope("demo/claude-session", "r1", "hi"), frm=self.CLOUD)])
        (args, _), = self.calls
        self.assertEqual(args[:4], ("send", "demo/claude-session", "--from", self.ME))
        self.calls.clear()
        self.r.handle([self.msg(envelope("demo/claude-session", "r2", "hi", nonce="n2"), i="m2", frm="demo/bot-cloud")])
        self.assertEqual([a[1] for a, _ in self.calls], [self.FWD])
        self.assertEqual(self.r.s.spool[-1]["from"], "demo/bot-cloud")

    def test_notice_names_the_configured_cloud_session(self):
        self.r.handle([self.msg("just text", frm=self.CLOUD)])
        self.assertTrue(any(n.startswith(f"{self.CLOUD.split('/')[1]} sent m1 without a valid RN-OUT")
                            for n in self.notices()), self.notices())

    def test_receipts_come_from_the_configured_inbox_or_a_terminal_only(self):
        for i, (frm, allowed) in enumerate(((self.INBOX, True), (self.CLOUD, True), ("demo/terminal-3", True),
                                            ("demo/bot-inbox", False))):
            with self.assertLogs(self.r.log, "INFO") as logs:
                self.r.log.info("start")
                self.r.got({"id": f"g{i}", "from": frm, "text": rr.got_body(KEY, [], ["m1"])})
            refused = any("sender not allowed" in line for line in logs.output)
            self.assertEqual(refused, not allowed, (frm, logs.output))


class UnrelatedNames(CustomNames):
    """The relay itself does not require derived names; only the installer insists on them."""
    NAMES = {"LISTEN_NAME": "x", "CLOUD_NAME": "y", "INBOX_NAME": "z"}
    ME, CLOUD, INBOX, FWD = "demo/x", "demo/y", "demo/z", "demo/z@cloud"


if __name__ == "__main__":
    unittest.main(verbosity=2)
