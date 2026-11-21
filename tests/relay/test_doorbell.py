import io, json, os, sqlite3, subprocess, tempfile, time, unittest
from unittest import mock
from pathlib import Path

from tests.helpers.paths import ROOT
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
    sha,
    samples,
    envelope,
    RunOK,
    RunOut,
    parse_fwd,
    DoorbellFixture,
    Crash,
    SCHEMA,
    prev_state_class,
)


class DoorbellCases:
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
        src = (ROOT / "agent_relay.py").read_text(encoding="utf-8")
        self.assertNotIn("urlopen(", src)
        self.assertNotIn("install_opener", src)

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


class Doorbell(DoorbellFixture, DoorbellCases, unittest.TestCase):
    pass


class HardeningFixture(DoorbellFixture):
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


class Hardening(HardeningFixture, DoorbellCases, unittest.TestCase):
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
        def broken(m, group): raise RuntimeError("boom")
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
