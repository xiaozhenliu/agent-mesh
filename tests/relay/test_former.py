import json, sqlite3, subprocess, tempfile, time, unittest
from pathlib import Path
from unittest import mock

from tests.relay.support import (
    rr,
    REAL_WEBHOOK_OPEN,
    KEY,
    CFG,
    clean_state,
    FakeResp,
    envelope,
    refwd_req,
    parse_fwd,
    RunOK,
    RunOut,
    Crash,
    SCHEMA,
)


NAMES = {"LISTEN_NAME": "beta", "CLOUD_NAME": "beta-cloud", "INBOX_NAME": "beta-inbox"}
ME, FWD = "demo/beta", "demo/beta-inbox@cloud"
IDLE = "No messages. You are {}.\n"


def block(i, frm, text):
    return f'<reason-message id="{i}" from="{frm}" agent="codex">\n{text}\n</reason-message>\n'


def fields(body):
    return dict(l.split(": ", 1) for l in body.split("\n")[1:] if ": " in l)


class FormerFixture:
    def setUp(self):
        self.addCleanup(setattr, rr, "webhook_open", REAL_WEBHOOK_OPEN)
        clean_state()
        self.now = float(int(time.time()))
        clock = mock.patch.object(rr.time, "time", lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.db_path = str(Path(tempfile.mkdtemp()) / "network.db")
        db = sqlite3.connect(self.db_path)
        db.executescript(SCHEMA)
        db.commit(); db.close()
        self.cfg = dict(CFG, **NAMES, RN_DB=self.db_path,
                        FORMER_NAMES=f"alpha:{int(self.now) + 3600}, gone:{int(self.now) - 1}")
        self.calls, self.posts = [], []
        rr.webhook_open = lambda req, timeout=None: (self.posts.append(json.loads(req.data.decode())), FakeResp())[1]
        self.r = self.relay()
        self.r.s.recv_floor = self.now - 3600
        self.r.s.save()

    def relay(self):
        r = rr.Relay(dict(self.cfg))
        r.rn = self.rn
        return r

    def rn(self, *a, **k):
        self.calls.append((a, k.get("stdin")))
        return RunOK()

    def msg(self, text, i="m1", frm="demo/other"):
        return {"id": i, "from": frm, "agent": None, "thread": None, "text": text}

    def proxied(self):
        return [(a[1], a[3]) for a, _ in self.calls if a[0] == "send" and "--from" in a]

    def forwarded(self):
        return [parse_fwd(s)[0]["id"] for _, s in self.calls if (s or "").startswith("RN-FWD v1")]

    def acks(self):
        return [fields(s)["status"] for _, s in self.calls if (s or "").startswith(rr.REFWD_ACK_HEAD)]

    def notices(self):
        return [i["text"] for i in self.r.s.spool if not i.get("doorbell")]

    def store(self, i, text, sender, recipient, ago=60):
        db = sqlite3.connect(self.db_path)
        db.execute("INSERT INTO messages (id, sender, recipient, body, wake, sent_at, read_at) VALUES (?,?,?,?,1,?,1)",
                   (i, sender, recipient, text, int((self.now - ago) * 1000)))
        db.commit(); db.close()


class TransitionIdentity(FormerFixture, unittest.TestCase):
    def test_an_old_identity_reply_is_proxied_once_from_the_new_address(self):
        e = envelope("demo/carol", "r1", "hi")
        self.r.handle([self.msg(e, i="o1", frm="demo/alpha-cloud"), self.msg(e, i="o2", frm="demo/alpha-cloud"),
                       self.msg(e, i="o3", frm="demo/beta-cloud")])
        ((args, body),) = [c for c in self.calls if "--from" in c[0]]
        self.assertEqual(args, ("send", "demo/carol", "--from", ME, "--reply-to", "r1", "-"))
        self.assertEqual(body, "hi")
        self.assertEqual((self.forwarded(), self.notices(), self.r.s.alerts), ([], [], []))

    def test_the_stored_text_is_looked_up_under_the_old_identity(self):
        shown = {}
        for i, sender, recipient in (("s1", "demo/alpha-cloud", "demo/alpha"), ("s2", "demo/alpha-cloud", ME),
                                     ("s3", "demo/beta-cloud", "demo/alpha")):
            e = envelope("demo/carol", "", f"body of {i}", nonce=f"n-{i}")
            self.store(i, e, sender, recipient)
            shown[i] = e.replace("RN-OUT v1", "RN-OUT v1 [x", 1)
        self.r.handle([self.msg(t, i=i, frm="demo/alpha-cloud") for i, t in shown.items()])
        self.assertEqual([b for a, b in self.calls if "--from" in a], ["body of s1"])
        self.assertEqual(self.proxied(), [("demo/carol", ME)])
        self.assertEqual(self.notices(), [f"alpha-cloud sent {i} without a valid RN-OUT envelope; not forwarded"
                                          for i in ("s2", "s3")])

    def test_old_receipts_and_re_forward_requests_are_honoured(self):
        self.r.handle([self.msg("first", i="m1"), self.msg("second", i="m2")])
        self.assertEqual(set(self.r.s.pending), {"m1", "m2"})
        self.r.handle([self.msg(rr.got_body(KEY, [], ["m1"]), i="g1", frm="demo/alpha-inbox"),
                       self.msg(rr.got_body(KEY, ["m2"], []), i="g2", frm="demo/alpha-cloud")])
        self.assertEqual(list(self.r.s.pending), ["m2"])
        self.assertEqual(self.r.s.pending["m2"]["stage"], "stored")
        self.calls.clear()
        self.r.handle([self.msg(refwd_req("m1"), i="q1", frm="demo/alpha-cloud"),
                       self.msg(refwd_req("m2"), i="q2", frm="demo/alpha-inbox")])
        self.assertEqual(self.forwarded(), ["m1", "m2"])
        self.assertEqual([a[1] for a, _ in self.calls], [FWD] * 4)
        self.assertEqual(self.acks(), ["resent", "resent"])

    def test_new_mail_and_an_old_reply_in_one_batch(self):
        self.r.handle([self.msg("hello", i="n1", frm="demo/carol"),
                       self.msg(envelope("demo/carol", "n0", "answer"), i="o1", frm="demo/alpha-cloud")])
        self.assertEqual(self.forwarded(), ["n1"])
        self.assertEqual(self.proxied(), [("demo/carol", ME)])
        (bell,) = [i for i in self.r.s.spool if i.get("doorbell")]
        self.assertEqual((bell["id"], bell["from"]), ("n1", "demo/carol"))

    def test_bad_signatures_from_the_old_identity_are_refused(self):
        self.r.handle([self.msg("first", i="m1")])
        self.calls.clear()
        bad = "f" * 64
        with self.assertLogs(self.r.log, "WARNING") as logs:
            self.r.handle([self.msg(envelope("demo/carol", "", "hi", key=bad), i="o1", frm="demo/alpha-cloud"),
                           self.msg(rr.got_body(bad, [], ["m1"]), i="g1", frm="demo/alpha-inbox"),
                           self.msg(refwd_req("m1", key=bad), i="q1", frm="demo/alpha-cloud")])
        self.assertEqual(self.calls, [])
        self.assertIn("m1", self.r.s.pending)
        self.assertEqual(self.notices(), ["outbound o1 refused: bad signature"])
        self.assertTrue(any("receipt g1 ignored (sig=False" in l for l in logs.output), logs.output)
        self.assertTrue(any("refwd request q1 for m1 ignored (sig=False" in l for l in logs.output), logs.output)

    def test_the_old_address_counts_as_self(self):
        self.r.handle([self.msg("echo", i="e1", frm="demo/alpha")])
        self.assertEqual((self.calls, self.r.s.spool), ([], []))
        for i, to in enumerate(("demo/alpha", "demo/alpha-cloud", ME, "demo/beta-cloud")):
            self.r.handle([self.msg(envelope(to, "", "hi", nonce=f"n{i}"), i=f"o{i}", frm="demo/alpha-cloud")])
        self.assertEqual(self.calls, [])
        self.assertEqual(len([a for a in self.r.s.alerts if "group or self address" in a["text"]]), 4)

    def test_expired_and_unlisted_names_get_no_special_treatment(self):
        for k, name in enumerate(("gone", "gamma")):
            self.r.handle([self.msg(envelope("demo/carol", "", "hi", nonce=f"n{k}"), i=f"o{k}",
                                    frm=f"demo/{name}-cloud"),
                           self.msg("hello", i=f"h{k}", frm=f"demo/{name}")])
        self.assertEqual(self.proxied(), [])
        self.assertEqual(self.forwarded(), ["o0", "h0", "o1", "h1"])
        self.assertEqual([i["from"] for i in self.r.s.spool], ["demo/gone-cloud", "demo/gone", "demo/gamma-cloud",
                                                               "demo/gamma"])
        self.calls.clear()
        with self.assertLogs(self.r.log, "WARNING") as logs:
            self.r.handle([self.msg(rr.got_body(KEY, [], ["o0"]), i="g1", frm="demo/gone-inbox"),
                           self.msg(refwd_req("o0"), i="q1", frm="demo/gone-cloud"),
                           self.msg(rr.got_body(KEY, [], ["o0"]), i="g2", frm="demo/gamma-cloud"),
                           self.msg(refwd_req("o0"), i="q2", frm="demo/gamma-inbox")])
        self.assertEqual(self.calls, [])
        self.assertEqual(sum("sender not allowed" in l for l in logs.output), 4)
        self.assertIn("o0", self.r.s.pending)

    def test_a_former_name_expires_while_the_relay_runs(self):
        self.r.handle([self.msg("first", i="m1")])
        self.calls.clear()
        self.now += 3600
        with self.assertLogs(self.r.log, "INFO") as logs:
            self.r.handle([self.msg(envelope("demo/carol", "", "late"), i="o1", frm="demo/alpha-cloud"),
                           self.msg(rr.got_body(KEY, [], ["m1"]), i="g1", frm="demo/alpha-inbox")])
        self.assertEqual(self.proxied(), [])
        self.assertEqual(self.forwarded(), ["o1"])
        self.assertIn("m1", self.r.s.pending)
        self.assertEqual(sum("former name alpha expired" in l for l in logs.output), 1)
        self.assertTrue(any("receipt g1 from demo/alpha-inbox ignored: sender not allowed" in l for l in logs.output))


class FormerReconcile(FormerFixture, unittest.TestCase):
    def test_reconcile_picks_up_unhandled_rows_to_a_former_name(self):
        self.store("A", "hello", "demo/carol", "demo/alpha")
        self.store("B", "to the current name", "demo/dave", ME)
        self.store("G", "to the expired name", "demo/carol", "demo/gone")
        self.store("O", envelope("demo/carol", "c0", "answer"), "demo/alpha-cloud", "demo/alpha")
        self.store("U", "to an unlisted name", "demo/carol", "demo/gamma")
        self.r.reconcile()
        self.assertEqual(self.forwarded(), ["A", "B"])
        self.assertEqual(self.proxied(), [("demo/carol", ME)])
        self.r.reconcile()
        self.assertEqual((self.forwarded(), len(self.proxied())), (["A", "B"], 1))
        self.now += 3600
        self.store("A2", "after the former name expired", "demo/carol", "demo/alpha")
        self.r.reconcile()
        self.assertEqual(self.forwarded(), ["A", "B"])


class FormerPolling(FormerFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.reconciles, self.listens = [], []
        self.restart(f"alpha:{int(self.now) + 200},gone:{int(self.now) - 1}")

    def restart(self, former):
        self.cfg["FORMER_NAMES"] = former
        self.r = self.relay()
        self.r.reconcile = lambda: self.reconciles.append(self.now)
        self.r.check_pending = lambda: None
        self.r.flush = lambda: 600
        self.r.next_wait = lambda _: 300

    def run_until(self, mains, former=()):
        former, count = list(former), [0]
        def rn(*a, **k):
            if a[0] != "listen":
                return self.rn(*a, **k)
            name, w = a[2], int(a[a.index("--wait") + 1])
            self.listens.append((self.now, name, w, k.get("timeout")))
            if name == "beta":
                count[0] += 1
                if count[0] > mains:
                    raise Crash()
                self.now += w
                return RunOut(0, IDLE.format(ME))
            out = former.pop(0) if former else RunOut(0, IDLE.format(f"demo/{name}"))
            if isinstance(out, BaseException):
                raise out
            return out
        self.r.rn = rn
        with mock.patch.object(rr.time, "sleep"), self.assertRaises(Crash):
            self.r.run()

    def test_unexpired_names_are_polled_about_every_interval_and_the_main_wait_is_capped(self):
        t0 = self.now
        with self.assertLogs(self.r.log, "INFO") as logs:
            self.run_until(7)
        self.assertEqual([(t - t0, n, w) for t, n, w, _ in self.listens],
                         [(0, "beta", 0), (0, "alpha", 0), (0, "beta", 60), (60, "alpha", 0), (60, "beta", 60),
                          (120, "alpha", 0), (120, "beta", 60), (180, "alpha", 0), (180, "beta", 60),
                          (240, "beta", 300), (540, "beta", 300), (840, "beta", 300)])
        self.assertEqual({k for _, n, _, k in self.listens if n == "alpha"}, {rr.FORMER_LISTEN_TIMEOUT})
        self.assertEqual(json.loads(rr.REGISTERED.read_text())["name"], ME)
        up = [l for l in logs.output if "agent-relay up" in l]
        self.assertEqual(len(up), 1)
        self.assertIn("former names, checked every 60 s: alpha until ", up[0])
        self.assertNotIn("gone", up[0])
        self.assertEqual(sum("former name alpha expired" in l for l in logs.output), 1)
        self.assertEqual(sum("former name gone expired" in l for l in logs.output), 1)

    def test_the_poll_follows_the_configured_interval(self):
        self.cfg["FORMER_POLL_SECONDS"] = 25
        self.restart(f"alpha:{int(self.now) + 3600}")
        t0 = self.now
        self.run_until(3)
        self.assertEqual([(t - t0, n, w) for t, n, w, _ in self.listens],
                         [(0, "beta", 0), (0, "alpha", 0), (0, "beta", 25), (25, "alpha", 0), (25, "beta", 25),
                          (50, "alpha", 0), (50, "beta", 25)])

    def test_mail_picked_up_under_a_former_name_is_handled_like_any_other(self):
        reply = envelope("demo/carol", "c0", "answer")
        first = block("x1", "demo/carol", "hello") + block("o1", "demo/alpha-cloud", reply) + "\n" + IDLE.format(
            "demo/alpha")
        again = block("x1", "demo/carol", "hello") + "\n" + IDLE.format("demo/alpha")
        self.run_until(2, former=[RunOut(0, first), RunOut(0, again)])
        self.assertEqual(self.forwarded(), ["x1"])
        self.assertEqual([a[1] for a, s in self.calls if (s or "").startswith("RN-FWD v1")], [FWD])
        self.assertEqual(self.proxied(), [("demo/carol", ME)])
        (bell,) = [i for i in self.r.s.spool if i.get("doorbell")]
        self.assertEqual((bell["id"], bell["from"], bell["agent"]), ("x1", "demo/carol", "codex"))
        self.assertIn("x1", self.r.s.pending)

    def test_a_failing_poll_is_logged_once_and_never_stops_the_relay(self):
        outs = [RunOut(1, "Error: network daemon connection closed"), subprocess.TimeoutExpired("listen", 30),
                RunOut(0, block("x1", "demo/carol", "hi") + "\n" + IDLE.format("demo/alpha"))]
        with self.assertLogs(self.r.log, "INFO") as logs:
            self.run_until(3, former=outs)
        self.assertEqual([n for _, n, _, _ in self.listens], ["beta", "alpha"] * 3 + ["beta"])
        failed = [l for l in logs.output if "could not pick up the mail of the former name alpha" in l]
        self.assertEqual(len(failed), 1)
        self.assertIn("daemon connection closed", failed[0])
        self.assertEqual(sum("former name alpha works again" in l for l in logs.output), 1)
        self.assertEqual(len(self.reconciles), 2)
        self.assertEqual(self.forwarded(), ["x1"])
        self.assertEqual(self.notices(), [])

    def test_a_former_name_taken_by_another_computer_is_logged_once_and_its_mail_still_handled(self):
        outs = [RunOut(0, block("x1", "demo/carol", "hi") + "\n" + IDLE.format("demo/alpha-2")),
                RunOut(0, IDLE.format("demo/alpha-2")), RunOut(0, IDLE.format("demo/alpha"))]
        with self.assertLogs(self.r.log, "INFO") as logs:
            self.run_until(3, former=outs)
        taken = [l for l in logs.output if "answered the former name alpha as demo/alpha-2" in l]
        self.assertEqual(len(taken), 1)
        self.assertIn("WARNING", taken[0])
        self.assertEqual(sum("the former name alpha is this relay's again" in l for l in logs.output), 1)
        self.assertEqual(self.forwarded(), ["x1"])

    def test_expired_names_are_never_polled_and_the_wait_is_not_capped(self):
        self.restart(f"gone:{int(self.now) - 1}")
        self.run_until(2)
        self.assertEqual([(n, w) for _, n, w, _ in self.listens], [("beta", 0), ("beta", 300), ("beta", 300)])


class FormerConfig(unittest.TestCase):
    def setUp(self):
        clean_state()

    def test_entries_are_parsed_and_malformed_ones_listed(self):
        long = "n" * 35
        found, bad = rr.former_names(f" alpha:100, beta-2 : 200 ,,bad, x:, :5, Upper:1, terminal:1, terminal-3:1, "
                                     f"a_b:1, {long}:1, y:1.5, z:-1, w:{'9' * 12}, alpha:300, alpha:50, terminals:7")
        self.assertEqual(found, [("alpha", 300), ("beta-2", 200), ("terminals", 7)])
        self.assertEqual(bad, ["bad", "x:", ":5", "Upper:1", "terminal:1", "terminal-3:1", "a_b:1", f"{long}:1",
                               "y:1.5", "z:-1", f"w:{'9' * 12}"])
        self.assertEqual(rr.former_names(""), ([], []))
        self.assertEqual(rr.former_names(None), ([], []))
        self.assertEqual(rr.former_names("n" * 34 + ":1"), ([("n" * 34, 1)], []))

    def test_malformed_entries_are_logged_once_and_the_relay_starts(self):
        now = int(time.time())
        cfg = dict(CFG, **NAMES, FORMER_NAMES=f"alpha:{now + 600},bad,Upper:1,beta:{now + 600},beta-cloud:{now + 600}")
        with self.assertLogs("agent-relay", "WARNING") as logs:
            r = rr.Relay(cfg)
        self.assertEqual(len(logs.output), 3, logs.output)
        self.assertTrue(any("FORMER_NAMES entry 'bad' ignored" in l for l in logs.output))
        self.assertTrue(any("FORMER_NAMES entry 'Upper:1' ignored" in l for l in logs.output))
        self.assertTrue(any("FORMER_NAMES entry beta-cloud ignored: it overlaps" in l for l in logs.output))
        self.assertEqual(r.groups(), [(ME, "demo/beta-cloud", "demo/beta-inbox"),
                                      ("demo/alpha", "demo/alpha-cloud", "demo/alpha-inbox")])
        r.flush = lambda: 600
        def rn(*a, **k):
            raise Crash()
        r.rn = rn
        with mock.patch.object(rr.time, "sleep"), self.assertRaises(Crash), \
                self.assertLogs("agent-relay", "INFO") as logs:
            r.run()
        self.assertTrue(any("agent-relay up" in l and "alpha until" in l for l in logs.output))
        self.assertFalse(any("FORMER_NAMES" in l for l in logs.output))

    def test_load_env_keeps_former_names_and_the_poll_interval(self):
        env = Path(tempfile.mkdtemp()) / "env"
        base = ("RN_BIN=/nonexistent\nPERSON=demo\nWEBHOOK_URL=https://example.invalid/\nWEBHOOK_KEY=x\n"
                f"OUTBOUND_KEY={KEY}\n")
        env.write_text(base + "FORMER_NAMES=alpha:2000000000,oops\n")
        env.chmod(0o600)
        with mock.patch.object(rr, "ENV_FILE", env):
            cfg = rr.load_env()
        self.assertEqual((cfg["FORMER_NAMES"], cfg["FORMER_POLL_SECONDS"]), ("alpha:2000000000,oops", 60))
        env.write_text(base + "FORMER_POLL_SECONDS=0\n")
        with mock.patch.object(rr, "ENV_FILE", env), self.assertLogs("agent-relay", "WARNING") as lg:
            cfg = rr.load_env()
        self.assertEqual(cfg["FORMER_POLL_SECONDS"], 60)
        self.assertIn("FORMER_POLL_SECONDS=0", lg.output[0])
        self.assertNotIn("FORMER_NAMES", cfg)
