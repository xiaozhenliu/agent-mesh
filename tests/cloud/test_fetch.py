import json, os, subprocess, sys, time, unittest
from unittest import mock
from pathlib import Path

from tests.cloud.support import (
    HERE,
    KEY,
    load,
    rr,
    FetchFixture,
    FAKE_RN,
)


class FetchCases:
    def test_exact_text_complete(self):
        texts = {"a": "  leading\n\ntrailing  \n\n", "b": "<reason-message> &lt; <b> 中文 😀 \r\n\t\x01",
                 "c": "x" * 9990 + "\nEND-10000", "d": "", "e": "has\n---\nseparator\n--- RN-FWD END\nin it"}
        for k, t in texts.items():
            self.put(self.fwd(k, t), read=(k == "a"))           # read rows still count
        rc, res = self.run_fetch("scan", *texts)
        self.assertEqual(rc, 0, res)
        for r in res:
            self.assertEqual(r["status"], "complete", r)
            got = open(r["full"], encoding="utf-8", newline="").read()
            self.assertEqual(got, texts[r["id"]])
            meta = json.loads((Path(r["dir"]) / "meta.json").read_text())
            self.assertEqual((meta["parts"], meta["text_len"], meta["via"]), (1, len(texts[r["id"]]), "rn-fwd"))

    def test_missing_and_wait_timeout(self):
        rc, res = self.run_fetch("wait", "--timeout", "3", "nope")
        self.assertEqual((rc, res[0]["status"]), (4, "missing"))

    def test_wait_picks_up_late_arrival(self):
        p = subprocess.Popen([sys.executable, str(HERE / "rn-fetch.py"), "wait", "--timeout", "20", "late"],
                             env=self.env, stdout=subprocess.PIPE, text=True)
        import time; time.sleep(3)
        self.put(self.fwd("late", "arrived later"))
        out, _ = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(out)["status"], "complete")

    def test_bad_signature_tamper_cutoff(self):
        self.put(self.fwd("forged", "hi", key="f" * 64))
        self.put(self.fwd("tampered", "original").replace("original", "changed!"))
        self.put(self.fwd("cut", "y" * 500)[:-20])
        self.put("plain text to the inbox")
        rc, res = self.run_fetch("scan", "forged", "tampered", "cut")
        self.assertEqual(rc, 5)
        by = {r["id"]: r for r in res}
        self.assertIn("bad signature", by["forged"]["reason"])
        self.assertEqual(by["tampered"]["status"], "mismatch")
        self.assertIn("trailer", by["cut"]["reason"])
        self.assertFalse((self.d / "inbox" / "forged").exists())

    def test_valid_copy_beats_forged_one_and_resend_is_idempotent(self):
        self.put(self.fwd("m1", "forged", key="f" * 64))
        self.put(self.fwd("m1", "real"))
        self.put(self.fwd("m1", "real"))
        rc, res = self.run_fetch("scan", "m1")
        self.assertEqual((rc, res[0]["status"]), (0, "complete"))
        self.assertEqual(Path(res[0]["full"]).read_text(), "real")
        rc, res = self.run_fetch("scan", "m1")
        self.assertTrue(res[0]["already"])

    def test_other_recipients_ignored(self):
        self.put(self.fwd("m2", "for someone else"), to="demo/bot-cloud")
        rc, res = self.run_fetch("scan", "m2")
        self.assertEqual(res[0]["status"], "missing")

    def test_no_ids_lists_only_new(self):
        self.put(self.fwd("n1", "one"))
        self.put(self.fwd("n2", "two"))
        rc, res = self.run_fetch("scan")
        self.assertEqual(sorted(r["id"] for r in res), ["n1", "n2"])
        rc, res = self.run_fetch("scan")
        self.assertEqual(res, [])

    def test_purge_removes_old_plaintext_together_and_keeps_recent(self):
        inbox, threads = self.purge_fixture()
        rc, res = self.run_fetch("purge", "7")
        self.assertEqual(rc, 0)
        self.assertEqual(res, [{"purged": {"messages": 2, "inbox": 2, "stray": 1, "threads": 1, "mismatch": 0},
                                "older_than_days": 7}])
        # both copies of "dup" stay: its newest copy is recent, and its folder stays with them
        self.assertEqual(self.rows_left(), ["01RN0000000000000000000003", "01RNDUPAGAIN", "01RNFUTURE", "01RNRECENT"])
        self.assertEqual(sorted(p.name for p in inbox.iterdir()),
                         ["_claims_v1", "_lock", "_refwd", "_stray", "dup", "fut", "new1"])
        self.assertTrue((inbox / "_refwd" / "old1.json").exists())   # the per-id re-forward cap survives
        self.assertEqual([p.name for p in threads.iterdir()], ["demo_new.md"])
        rc, res = self.run_fetch("scan")                              # nothing purged comes back as new
        self.assertEqual((rc, res), (0, []))
        rc, res = self.run_fetch("purge", "0")                        # 0 = everything, future timestamps too
        self.assertEqual(res[0]["purged"], {"messages": 4, "inbox": 3, "stray": 0, "threads": 1, "mismatch": 0})
        self.assertEqual(self.rows_left(), [])
        self.assertEqual(sorted(p.name for p in inbox.iterdir()), ["_claims_v1", "_lock", "_refwd", "_stray"])
        for bad in (["purge"], ["purge", "-1"], ["purge", "7d"], ["purge", "²"]):
            p = subprocess.run([sys.executable, str(HERE / "rn-fetch.py"), *bad], env=self.env,
                               capture_output=True, text=True, timeout=30)
            self.assertEqual(p.returncode, 2, bad)

    def test_purge_refuses_symlinks_and_a_running_fetch_before_deleting(self):
        import fcntl
        inbox, threads = self.purge_fixture()
        before = self.rows_left()
        elsewhere = self.d / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "keep.md").write_text("not ours")
        (elsewhere / "keep.log").write_text("not ours")
        os.utime(elsewhere / "keep.md", (1, 1))
        os.utime(elsewhere / "keep.log", (1, 1))
        for link_name in ("threads", "stray"):
            link = threads if link_name == "threads" else inbox / "_stray"
            saved = link.with_name(link.name + ".real")
            link.rename(saved)
            link.symlink_to(elsewhere, target_is_directory=True)
            p = subprocess.run([sys.executable, str(HERE / "rn-fetch.py"), "purge", "0"], env=self.env,
                               capture_output=True, text=True, timeout=30)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("symlinked", p.stderr)
            link.unlink()
            saved.rename(link)
        self.assertEqual(sorted(p.name for p in elsewhere.iterdir()), ["keep.log", "keep.md"])
        self.assertEqual(self.rows_left(), before)
        with open(inbox / "_lock", "a") as held:                      # a fetch holds the inbox lock
            fcntl.flock(held, fcntl.LOCK_EX)
            self.env["RN_PURGE_LOCK_WAIT"] = "1"
            p = subprocess.run([sys.executable, str(HERE / "rn-fetch.py"), "purge", "0"], env=self.env,
                               capture_output=True, text=True, timeout=30)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("still running", p.stderr)
        self.assertEqual(self.rows_left(), before)

    def test_purge_keeps_the_folder_of_a_re_forward_that_lands_during_the_purge(self):
        import time
        inbox, _ = self.purge_fixture()
        # A copy of old1 lands right after purge commits its deletes, before it removes folders.
        driver = (
            "import importlib.util, sqlite3, sys, time\n"
            "spec = importlib.util.spec_from_file_location('rnf', sys.argv[1]); m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\n"
            "orig = m.live_ids\n"
            "def late(db_path):\n"
            "    db = sqlite3.connect(db_path)\n"
            "    db.execute('INSERT INTO messages (id, sender, recipient, body, wake, sent_at) VALUES (?,?,?,?,1,?)',"
            " ('01RNLATE', 'demo/terminal', 'demo/bot-inbox', sys.argv[2], int(time.time() * 1000)))\n"
            "    db.commit(); db.close()\n"
            "    return orig(db_path)\n"
            "m.live_ids = late\n"
            "print(m.purge(7, m.Path(sys.argv[3]))['purged'])\n")
        p = subprocess.run([sys.executable, "-c", driver, str(HERE / "rn-fetch.py"),
                            self.fwd("old1", "old secret").strip(), str(self.d / "threads")],
                           env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("'inbox': 1", p.stdout)                         # old2 went; old1 stayed with its new row
        self.assertIn("01RNLATE", self.rows_left())
        self.assertTrue((inbox / "old1").is_dir())
        self.assertFalse((inbox / "old2").exists())
        rc, res = self.run_fetch("scan")                              # old1 already fetched: not new
        self.assertEqual((rc, res), (0, []))

    def test_vector_matches_relay(self):
        fetch = load("rn_fetch", HERE / "rn-fetch.py")
        body = rr.fwd_body(KEY, "01JTESTMSG0000000000000000", "demo/claude-session", "claude-code", "", "你好\n x ",
                           ts=1700000000)
        f, text, err = fetch.parse(body.strip(), KEY)
        self.assertIsNone(err)
        self.assertEqual(text, "你好\n x ")
        self.assertEqual(f["sig"], "a48eb3bbb089a3d6b31fed070d76063bb3b3ddabfac08b7651f4093ce358c576")


class T(FetchFixture, FetchCases, unittest.TestCase):
    pass


class RefwdFixture(FetchFixture):
    def setUp(self):
        super().setUp()
        self.db.execute("CREATE TABLE outbox (id TEXT)")
        self.db.commit()
        rn = self.d / "fake-rn"
        rn.write_text(FAKE_RN); rn.chmod(0o755)
        self.log = self.d / "rn.log"
        self.env.update(RN_BIN=str(rn), FAKE_LOG=str(self.log), FAKE_RELAY=str(HERE.parent / "agent_relay.py"),
                        RN_CLOUD_NAME="bot-cloud", RN_RELAY_NAME="bot")

    def sends(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []


class Refwd(RefwdFixture, FetchCases, unittest.TestCase):
    def test_missing_triggers_one_signed_refwd_and_completes(self):
        self.put(self.fwd("ok1", "already here"))
        rc, res = self.run_fetch("fetch", "--timeout", "2", "--refwd-timeout", "10", "ok1", "lost1")
        by = {r["id"]: r for r in res}
        self.assertEqual(rc, 0, res)
        self.assertEqual(by["lost1"]["status"], "complete")
        self.assertEqual(open(by["lost1"]["full"]).read(), "re-sent text lost1")
        self.assertEqual(by["lost1"]["refwd"], {"attempt": 1, "ack": "resent"})
        self.assertNotIn("refwd", by["ok1"])
        (s,) = self.sends()
        self.assertEqual(s["args"], ["send", "demo/bot", "--from", "demo/bot-cloud", "-"])
        self.assertTrue(s["body"].startswith("RN-REFWD v1\nid: lost1\n"))

    def test_box_cap_two_per_id_no_loop(self):
        self.env["FAKE_MODE"] = "silent"
        for k in range(3):
            rc, (r,) = self.run_fetch("fetch", "--timeout", "1", "--refwd-timeout", "2", "gone")
            self.assertEqual((rc, r["status"]), (4, "missing"))
        self.assertEqual(len(self.sends()), 2)
        self.assertIn("box limit", r["refwd"]["skipped"])

    def test_no_copy_ack_stops_waiting_early(self):
        self.env["FAKE_MODE"] = "nocopy"
        t0 = __import__("time").time()
        rc, (r,) = self.run_fetch("fetch", "--timeout", "1", "--refwd-timeout", "30", "nc")
        self.assertLess(__import__("time").time() - t0, 15)
        self.assertEqual((rc, r["status"], r["refwd"]["ack"]), (4, "missing", "no-copy"))
        self.assertIn("no-copy", r["reason"])

    def test_refwd_off_and_wait_unchanged(self):
        self.env["RN_REFWD"] = "0"
        rc, (r,) = self.run_fetch("fetch", "--timeout", "1", "x")
        self.assertEqual((rc, r["status"]), (4, "missing"))
        rc, (r,) = self.run_fetch("wait", "--timeout", "1", "y")
        self.assertEqual(self.sends(), [])

    def test_acks_never_show_as_mismatch_and_forged_acks_ignored(self):
        self.put(rr.refwd_ack_body(KEY, "z", "resent", 1))
        self.put(rr.refwd_ack_body("f" * 64, "z", "no-copy", 1))
        rc, res = self.run_fetch("scan")
        self.assertEqual((rc, res), (0, []))
        fetch = load("rn_fetch2", HERE / "rn-fetch.py")
        self.assertIsNone(fetch.parse_ack(rr.refwd_ack_body("f" * 64, "z", "x", 1), KEY))
        self.assertEqual(fetch.parse_ack(rr.refwd_ack_body(KEY, "z", "resent", 2), KEY)["attempt"], "2")

    def test_refwd_signature_matches_relay(self):
        fetch = load("rn_fetch3", HERE / "rn-fetch.py")
        body = fetch.refwd_body(KEY, "01JTESTMSG0000000000000000", ts=1700000000, nonce="00" * 12)
        f = rr.parse_refwd(body)
        self.assertEqual(f["sig"], rr.sign_refwd(KEY, f["id"], f["ts"], f["nonce"]))


class NamesFixture(FetchFixture):
    def setUp(self):
        super().setUp()
        self.addCleanup(self.db.close)
        self.box = self.d / "box"
        self.box.mkdir()
        self.env.pop("RN_INBOX_NAME")
        self.mod = load("rn_fetch_names", HERE / "rn-fetch.py")

    def names(self, **env):
        keep = {k: v for k, v in os.environ.items() if k not in ("RN_RELAY_NAME", "RN_CLOUD_NAME", "RN_INBOX_NAME")}
        with mock.patch.dict(os.environ, dict(keep, RN_BOX=str(self.box), **env), clear=True):
            return self.mod.names()


class Names(NamesFixture, FetchCases, unittest.TestCase):
    """rn-fetch.py takes its session names from RN_*_NAME, else from $RN_BOX/relay, else bot."""

    def test_box_relay_is_read_when_a_name_is_not_given(self):
        self.assertEqual(self.names(), ("bot", "bot-cloud", "bot-inbox"))
        (self.box / "relay").write_text("alice\n")
        self.assertEqual(self.names(), ("alice", "alice-cloud", "alice-inbox"))
        self.assertEqual(self.names(RN_INBOX_NAME="lab-inbox"), ("alice", "alice-cloud", "lab-inbox"))

    def test_all_three_given_skip_the_file_even_when_it_is_damaged(self):
        (self.box / "relay").write_text("Bad--name\n")
        given = dict(RN_RELAY_NAME="x@m", RN_CLOUD_NAME="y", RN_INBOX_NAME="z")
        self.assertEqual(self.names(**given), ("x@m", "y", "z"))
        with self.assertRaises(SystemExit):
            self.names(RN_RELAY_NAME="x@m", RN_CLOUD_NAME="y")

    def test_damaged_or_empty_file_stops(self):
        for text in ("", "\n", " alice\n", "alice \n", "Bot", "a--b", "terminal-3", "a" * 35):
            with self.subTest(text=text):
                (self.box / "relay").write_text(text)
                with self.assertRaises(SystemExit) as e:
                    self.names()
                self.assertIn("is damaged", str(e.exception))

    def test_scan_reads_the_inbox_named_in_box_relay(self):
        (self.box / "relay").write_text("alice\n")
        self.put(self.fwd("a1", "for alice"), to="demo/alice-inbox")
        self.put(self.fwd("b1", "for bot"), to="demo/bot-inbox")
        rc, res = self.run_fetch("scan")
        self.assertEqual(rc, 0, res)
        self.assertEqual([(r["id"], r["status"]) for r in res], [("a1", "complete")])

    def test_damaged_file_stops_scan_and_done_but_not_send_status(self):
        (self.box / "relay").write_text("\n")
        for args in (("scan",), ("wait", "--timeout", "1", "x"), ("fetch", "--timeout", "1", "x"), ("done", "A")):
            with self.subTest(args=args):
                p = subprocess.run([sys.executable, str(HERE / "rn-fetch.py"), *args], env=self.env,
                                   capture_output=True, text=True, timeout=60)
                self.assertNotEqual(p.returncode, 0)
                self.assertIn("is damaged", p.stderr)
        self.assertFalse((self.d / "inbox").exists())
        p = subprocess.run([sys.executable, str(HERE / "rn-fetch.py"), "send-status"], env=self.env,
                           input="→ demo/alice: queued", capture_output=True, text=True, timeout=60)
        self.assertEqual((p.returncode, p.stdout), (0, "queued\n"))

    def test_receipts_and_refwd_go_to_the_names_in_box_relay(self):
        (self.box / "relay").write_text("alice\n")
        calls = []

        def run(args, **kw):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, f"→ {args[2]}: queued", "")
        self.mod.HOME, self.mod.BASE = self.d / "state", self.d / "inbox"
        self.db.execute("CREATE TABLE outbox (id TEXT)")
        self.db.commit()
        keep = {k: v for k, v in os.environ.items() if k not in ("RN_RELAY_NAME", "RN_CLOUD_NAME", "RN_INBOX_NAME")}
        env = dict(keep, RN_BOX=str(self.box), RN_BIN=str(self.d / "rn"), RN_REFWD_MAX="1000")
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(self.mod.subprocess, "run", run):
            self.assertEqual(self.mod.send_got([], ["a"], KEY, "demo"), [])
            self.mod.request_refwd("m", KEY, "demo")
        self.assertEqual([c[1:5] for c in calls], [["send", "demo/alice", "-"],
                                                   ["send", "demo/alice", "--from", "demo/alice-cloud"]])

    def test_a_refwd_request_that_cannot_be_built_is_reported_not_raised(self):
        calls = []

        def urandom(n):
            raise OSError("entropy unavailable")
        self.mod.HOME, self.mod.BASE = self.d / "state", self.d / "inbox"
        keep = {k: v for k, v in os.environ.items() if k not in ("RN_RELAY_NAME", "RN_CLOUD_NAME", "RN_INBOX_NAME")}
        env = dict(keep, RN_BOX=str(self.box), RN_BIN=str(self.d / "rn"), RN_REFWD_MAX="1000")
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(self.mod.subprocess, "run", lambda args, **kw: calls.append(args)), \
                mock.patch.object(self.mod.os, "urandom", urandom):
            self.assertEqual(self.mod.request_refwd("m", KEY, "demo"), (None, "send failed: OSError"))
        self.assertEqual(calls, [])
