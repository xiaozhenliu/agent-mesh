import os, sqlite3, subprocess, sys, tempfile, unittest
from unittest import mock
from pathlib import Path

from tests.cloud.support import (
    HERE,
    KEY,
    load,
)


class SendStatus(unittest.TestCase):
    """Receipts and re-forward requests are judged on the engine's status words, never on the recipient's name."""

    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "state").mkdir()
        db = sqlite3.connect(self.d / "state" / "network.db")
        db.executescript("CREATE TABLE outbox (id TEXT);")
        db.close()
        self.mod = load("rn_fetch_status", HERE / "rn-fetch.py")
        self.mod.HOME, self.mod.BASE = self.d / "state", self.d / "inbox"
        env = mock.patch.dict(os.environ, {"RN_RELAY_NAME": "error", "RN_BIN": str(self.d / "rn"),
                                           "RN_CLOUD_NAME": "bot-cloud", "RN_REFWD_MAX": "1000",
                                           "RN_BOX": str(self.d / "box")})
        env.start()
        self.addCleanup(env.stop)

    def run_with(self, rc, out):
        calls = []
        def run(args, **kw):
            calls.append(args)
            return subprocess.CompletedProcess(args, rc, out, "")
        with mock.patch.object(self.mod.subprocess, "run", run):
            errors = self.mod.send_got([], ["a"], KEY, "demo")
            attempt, why = self.mod.request_refwd("m", KEY, "demo")
        self.assertEqual([c[2] for c in calls], ["demo/error", "demo/error"])
        return errors, why

    def test_recipient_names_do_not_make_a_send_fail(self):
        for out in ("→ demo/error: queued", "→ demo/held-x: queued\nmessage id 01JX",
                    "→ demo/error: queued: not connected to cloud right now; it goes out as soon as it is"):
            with self.subTest(out=out), mock.patch("sys.stderr"):
                self.assertEqual(self.run_with(0, out), ([], None))

    def test_refusals_holds_errors_and_failures_still_fail(self):
        for rc, out in ((0, "→ demo/x: refused: these two agents have exchanged a lot of messages"),
                        (0, "→ demo/x: queued: held for approval"), (1, 'Error: nobody matches "x"'),
                        (0, "→ demo/x: queued (error: x)"), (2, "→ demo/x: queued"), (0, "queued")):
            with self.subTest(rc=rc, out=out), mock.patch("sys.stderr"):
                errors, why = self.run_with(rc, out)
                self.assertEqual(len(errors), 1)
                self.assertTrue(why)

    def test_send_status_needs_no_credentials_lock_or_engine(self):
        log = self.d / "engine.log"
        engine = self.d / "rn"
        engine.write_text("#!/bin/sh\necho called >> " + str(log) + "\n")
        engine.chmod(0o755)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("RN_", "REASON_NETWORK"))}
        env.update(RN_BIN=str(engine), RN_INBOX_DIR=str(self.d / "inbox"), RN_KEY_FILE=str(self.d / "no-key"),
                   RN_BOX=str(self.d / "no-box"), REASON_NETWORK_HOME=str(self.d / "no-home"))
        for out, want in (("→ sheldon/bot: queued\nmessage id 01JX\n", "queued\nmessage id 01JX"),
                          ("→ sheldon/bot: queued: held for approval", "queued: held for approval"),
                          ("Error: no session called 'held/x'", "Error: no session called")):
            p = subprocess.run([sys.executable, str(HERE / "rn-fetch.py"), "send-status"], input=out, env=env,
                               capture_output=True, text=True, timeout=60)
            self.assertEqual((p.returncode, p.stdout, p.stderr), (0, want + "\n", ""))
            self.assertEqual(p.stdout.strip(), self.mod.send_status(out))
        self.assertFalse((self.d / "inbox").exists())
        self.assertFalse(log.exists())
