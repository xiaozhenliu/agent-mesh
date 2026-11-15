import re, sqlite3, tempfile, time, unittest
from pathlib import Path

from tests.relay.support import (
    rr,
    KEY,
    CFG,
    clean_state,
    envelope,
    FakeRun,
    SCHEMA,
)


VECTOR_SIG = "652fbb03f1fd8626fd4f5db689b5983bdd57c797d5ba1085c340aaf699003088"


class EnvelopesFixture:
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


class EnvelopesCases:
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


class Envelopes(EnvelopesFixture, EnvelopesCases, unittest.TestCase):
    pass


def defang(text):
    return re.sub(r"<(?=[^\x00-\x7f]|reason)", "[", text)


class DisplayRewriteFixture(EnvelopesFixture):
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


class DisplayRewrite(DisplayRewriteFixture, EnvelopesCases, unittest.TestCase):
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
