import json, unittest
from unittest import mock

from tests.relay.support import (
    rr,
    REAL_WEBHOOK_OPEN,
    KEY,
    CFG,
    clean_state,
    FakeResp,
    envelope,
    RunOK,
    Crash,
)


class CustomNamesFixture:
    NAMES = {"LISTEN_NAME": "alice", "CLOUD_NAME": "alice-cloud", "INBOX_NAME": "alice-inbox"}

    ME, CLOUD, INBOX, FWD = "demo/alice", "demo/alice-cloud", "demo/alice-inbox", "demo/alice-inbox@cloud"

    def setUp(self):
        self.addCleanup(setattr, rr, "webhook_open", REAL_WEBHOOK_OPEN)
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


class CustomNamesCases:
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
                                            ("demo/terminal@cloud", True), ("demo/terminal@laptop", False),
                                            ("demo/bot-inbox", False))):
            with self.assertLogs(self.r.log, "INFO") as logs:
                self.r.log.info("start")
                self.r.got({"id": f"g{i}", "from": frm, "text": rr.got_body(KEY, [], ["m1"])})
            refused = any("sender not allowed" in line for line in logs.output)
            self.assertEqual(refused, not allowed, (frm, logs.output))

    def test_terminal_receipts_follow_the_configured_cloud_machine(self):
        r = rr.Relay(dict(CFG, **self.NAMES, CLOUD_MACHINE="box.1"))
        for frm, allowed in (("demo/terminal@box.1", True), ("demo/terminal@boxx1", False),
                             ("demo/terminal@cloud", False), ("demo/terminal", True), ("demo/terminal-2", True)):
            self.assertEqual(bool(r.terminal.fullmatch(frm)), allowed, frm)


class CustomNames(CustomNamesFixture, CustomNamesCases, unittest.TestCase):
    """A relay installed with --relay-name alice: every address follows LISTEN_NAME, CLOUD_NAME and INBOX_NAME."""


class UnrelatedNames(CustomNamesFixture, CustomNamesCases, unittest.TestCase):
    """The relay itself does not require derived names; only the installer insists on them."""
    NAMES = {"LISTEN_NAME": "x", "CLOUD_NAME": "y", "INBOX_NAME": "z"}
    ME, CLOUD, INBOX, FWD = "demo/x", "demo/y", "demo/z", "demo/z@cloud"
