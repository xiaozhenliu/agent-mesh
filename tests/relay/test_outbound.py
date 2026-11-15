import json, subprocess, tempfile, time, unittest
from unittest import mock
from pathlib import Path

from tests.relay.support import (
    tmp,
    rr,
    REAL_WEBHOOK_OPEN,
    KEY,
    CFG,
    clean_state,
    FakeResp,
    envelope,
    FakeRun,
    RunOK,
    RunOut,
    Crash,
    prev_state_class,
)


class OutboundIdempotency(unittest.TestCase):
    """RN-OUT send records: a nonce already sent is dropped, an unknown result is never resent, a failure may be."""
    N = "0123456789abcdef01234567"

    def setUp(self):
        self.addCleanup(setattr, rr, "webhook_open", REAL_WEBHOOK_OPEN)
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
