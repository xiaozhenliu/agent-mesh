"""In-place rename on the cloud: the switch waits for the first message to the new inbox, earlier names keep being
read, names of other Bots and other keys are refused, and an address taken back holds back its earlier messages."""
import hashlib, hmac, json, os, shlex, shutil, sqlite3, subprocess, sys, tempfile, time, unittest
from pathlib import Path

from tests.cloud.bot_support import BotBox, TEAM_HASH
from tests.cloud.support import HERE, KEY, load, rr
from tests.cloud.test_routine import CloudClone, R, RA, RB, RC, RE

REQ = "0123456789abcdef"
PLACEHOLDERS = ('TEAM="<team-name>"; MAC="<mac-machine>"; RELAY_NAME="<relay-name>"; LEVEL="<level>"; '
                'MODE="<mode>"; CHALLENGE="<challenge>"')


def until(days):
    return int(time.time()) + days * 86400


def run_apply(box, root, env, *args):
    return subprocess.run([sys.executable, "-I", str(box / "rn-engine.py"), "hold", "--exclusive",
                           str(root / ".maint.lock"), "--", sys.executable, "-I", str(box / "rn-names.py"), "apply",
                           *args], env=env, capture_output=True, text=True, timeout=120)


class FirstBot(CloudClone):
    def setUp(self):
        super().setUp()
        self.env["FETCH_TIMEOUT"] = "2"

    def request(self, text, inbox="bot-inbox"):
        mid = self.message(text, inbox=inbox)
        self.fetch(mid)
        p = self.cloud("request", mid)
        sent = self.sent()
        return p, sent[-1][2].rstrip("\n").split("\n") if sent else []

    def apply(self, *args):
        return run_apply(self.box, self.root, self.env, *args)

    def sessions(self):
        db = sqlite3.connect(self.box / "state" / "network.db")
        try:
            return {tuple(r) for r in db.execute("SELECT name, key FROM sessions")}
        finally:
            db.close()

    def add_session(self, name, key=None):
        db = sqlite3.connect(self.box / "state" / "network.db")
        db.execute("INSERT INTO sessions (name, key, last_seen) VALUES (?, ?, 1)", (name, key or "cli:" + name))
        db.commit()
        db.close()

    def pending(self):
        p = self.box / "rename.pending"
        return json.loads(p.read_text()) if p.exists() else None

    def former(self):
        p = self.box / "former"
        return {l.split()[0]: int(l.split()[1]) for l in p.read_text().splitlines()} if p.exists() else {}

    def relay(self):
        p = self.box / "relay"
        return p.read_text() if p.exists() else None

    def test_the_switch_waits_for_the_first_message_to_the_new_inbox(self):
        p, reply = self.request(f"agent-mesh rename - bot nova {R}")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(f"agent-mesh-result rename {R} pending - bot nova held 0", reply)
        self.assertIn("the Bot keeps answering as bot until the first message reaches demo/nova-inbox", reply)
        self.assertEqual(reply[-1], "agent-mesh-result DONE rename")
        pend = self.pending()
        self.assertEqual((pend["old"], pend["new"], pend["req"], pend["before"]), ("bot", "nova", R, []))
        self.assertIsNone(self.relay())
        self.assertLessEqual({("nova-cloud", "cli:nova-cloud"), ("nova-inbox", "cli:nova-inbox")},
                             self.sessions())
        self.assertFalse((self.box / "exclude.json").exists())
        old = self.message("still the old address")
        self.assertEqual(self.fetch(old)[old]["status"], "complete")
        self.assertIsNotNone(self.pending())
        new = self.message("the first message to nova", inbox="nova-inbox")
        self.assertEqual(self.fetch(new)[new]["status"], "complete")
        self.assertEqual(self.relay(), "nova\n")
        self.assertIsNone(self.pending())
        self.assertEqual(list(self.former()), ["bot"])
        self.assertAlmostEqual(self.former()["bot"], until(30), delta=300)
        late = self.message("a late message to bot")
        self.assertEqual(self.fetch(late)[late]["status"], "complete")
        self.assertTrue((self.root / "inbox" / late / "full.txt").exists())

    def test_an_expired_earlier_name_is_no_longer_read(self):
        self.request(f"agent-mesh rename - bot nova {R}")
        self.fetch(self.message("to nova", inbox="nova-inbox"))
        (self.box / "former").write_text(f"bot {int(time.time()) - 60}\n")
        late = self.message("too late for bot")
        probe = self.message("probe", inbox="nova-inbox")
        got = self.fetch(probe)
        self.assertEqual(got[probe]["status"], "complete")
        self.assertNotIn(late, got)

    def test_renaming_back_to_an_earlier_name_waits_for_a_new_message(self):
        self.request(f"agent-mesh rename - bot nova {R}")
        self.fetch(self.message("to nova", inbox="nova-inbox"))
        self.assertEqual(self.relay(), "nova\n")
        p, reply = self.request(f"agent-mesh rename - nova bot {RA}", inbox="nova-inbox")
        self.assertIn(f"agent-mesh-result rename {RA} pending - nova bot held 0", reply, p.stdout)
        self.assertEqual(len(self.pending()["before"]), 1)
        self.assertFalse((self.box / "exclude.json").exists())
        x = self.message("nova again", inbox="nova-inbox")
        self.fetch(x)
        self.assertEqual(self.relay(), "nova\n")
        y = self.message("back at bot")
        self.assertEqual(self.fetch(y)[y]["status"], "complete")
        self.assertEqual(self.relay(), "bot\n")
        self.assertEqual(list(self.former()), ["nova"])

    def test_names_of_other_bots_and_other_keys_are_refused(self):
        beta = self.root / "bots" / "beta"
        (beta / "inbox").mkdir(parents=True)
        (beta / ".claim").write_text(f"team {TEAM_HASH}\n")
        (beta / ".registered").write_text("x\n")
        (beta / "former").write_text(f"gamma {until(10)}\n")
        self.add_session("delta-cloud", "cli:someone-else")
        cases = [
            (f"agent-mesh rename - bot beta {R}", "STOP: beta is an address name of the Bot in bots/beta"),
            (f"agent-mesh rename - bot gamma {RA}", "STOP: gamma is an address name of the Bot in bots/beta"),
            (f"agent-mesh rename - bot delta {RB}", "STOP: the session demo/delta-cloud on this cloud is used by "
                                                   "another key"),
            (f"agent-mesh rename - nova carol {RC}", "STOP: the Bot uses the address name bot on this cloud, not "
                                                     "nova"),
            (f"agent-mesh rename beta beta carol {RE}", "STOP: this request is for the Bot slot beta, but it "
                                                       "reached -"),
        ]
        for text, why in cases:
            with self.subTest(text):
                p, reply = self.request(text)
                self.assertIn(why, "\n".join(reply), p.stdout)
                self.assertNotIn("agent-mesh-result DONE rename", reply)
                self.assertIsNone(self.pending())
                self.assertEqual({n for n, _ in self.sessions() if not n.startswith("bot-")}, {"delta-cloud"})
        self.assertEqual(self.former(), {})

    def test_an_address_taken_back_holds_back_its_earlier_messages(self):
        self.add_session("gamma-cloud")
        self.add_session("gamma-inbox")
        old = self.message("left from an earlier Bot", inbox="gamma-inbox")
        p, reply = self.request(f"agent-mesh rename - bot gamma {R}")
        self.assertIn("STOP: gamma is an earlier address name on this cloud; the Mac must confirm taking it back",
                      "\n".join(reply))
        self.assertIsNone(self.pending())
        p, reply = self.request(f"agent-mesh rename - bot gamma {RA}\nreclaim=1")
        self.assertIn(f"agent-mesh-result rename {RA} pending - bot gamma held 1", reply, p.stdout)
        ex = json.loads((self.box / "exclude.json").read_text())
        self.assertEqual(list(ex["claims"]), [RA])
        self.assertEqual(ex["claims"][RA]["inbox"], "gamma-inbox")
        self.assertEqual(len(ex["claims"][RA]["rows"]), 1)
        probe = self.message("probe")
        got = self.fetch(probe)
        self.assertNotIn(old, got)
        self.assertIsNotNone(self.pending())
        p, reply = self.request(f"agent-mesh history - accept {RA} {RB}")
        self.assertIn(f"agent-mesh-result history {RB} accepted {RA} 1", reply, p.stdout)
        ex = json.loads((self.box / "exclude.json").read_text())
        self.assertEqual((list(ex["claims"]), list(ex["accepted"])), ([], [RA]))
        probe = self.message("probe again")
        got = self.fetch(probe)
        self.assertEqual(got[old]["status"], "complete")
        self.assertTrue(got[old]["orphan"])
        self.assertIsNotNone(self.pending())
        new = self.message("first new message", inbox="gamma-inbox")
        self.fetch(new)
        self.assertEqual(self.relay(), "gamma\n")

    def test_a_held_message_sent_again_as_a_new_row_neither_switches_nor_reaches_the_bot(self):
        self.add_session("gamma-cloud")
        self.add_session("gamma-inbox")
        old = self.message("left from an earlier Bot", inbox="gamma-inbox")
        p = self.apply("rename", "-", "bot", "gamma", R, "reclaim")
        self.assertEqual(p.stdout.splitlines()[-1], f"agent-mesh-result rename {R} pending - bot gamma held 1", p.stdout)
        self.assertEqual(self.message("left from an earlier Bot", inbox="gamma-inbox", mid=old), old)
        probe = self.message("probe")
        got = self.fetch(probe)
        self.assertEqual(got[probe]["status"], "complete")
        self.assertNotIn(old, got)
        self.assertNotEqual(self.relay(), "gamma\n")
        self.assertIsNotNone(self.pending())
        new = self.message("first new message", inbox="gamma-inbox")
        got = self.fetch(new)
        self.assertEqual(got[new]["status"], "complete")
        self.assertNotIn(old, got)
        self.assertEqual((self.relay(), self.pending(), list(self.former())), ("gamma\n", None, ["bot"]))
        probe = self.message("probe again")
        got = self.fetch(probe)
        self.assertEqual(got[probe]["status"], "complete")
        self.assertNotIn(old, got)
        self.assertEqual(self.message("the same id to another inbox", mid=old), old)
        got = self.fetch(old)[old]
        self.assertEqual(got["status"], "complete")
        self.assertEqual(Path(got["full"]).read_text(), "the same id to another inbox")

    def test_a_removed_name_is_taken_back_only_with_reclaim(self):
        (self.box / "bots-removed").mkdir()
        (self.box / "bots-removed" / "zeta").write_text("t\n")
        p, reply = self.request(f"agent-mesh rename - bot zeta {R}")
        self.assertIn("STOP: zeta is an earlier address name on this cloud", "\n".join(reply))
        self.assertTrue((self.box / "bots-removed" / "zeta").exists())
        p, reply = self.request(f"agent-mesh rename - bot zeta {RA}\nreclaim=1")
        self.assertIn(f"agent-mesh-result rename {RA} pending - bot zeta held 0", reply, p.stdout)
        self.assertFalse((self.box / "bots-removed" / "zeta").exists())
        self.assertEqual(json.loads((self.box / "exclude.json").read_text())["claims"][RA]["rows"], [])

    def test_consecutive_renames(self):
        self.request(f"agent-mesh rename - bot nova {R}")
        p, reply = self.request(f"agent-mesh rename - bot carol {RA}")
        self.assertIn(f"agent-mesh-result rename {RA} pending - bot carol held 0", reply, p.stdout)
        self.assertEqual((self.pending()["old"], self.pending()["new"]), ("bot", "carol"))
        self.assertEqual(list(self.former()), ["nova"])
        p, reply = self.request(f"agent-mesh rename - nova dave {RB}")
        self.assertIn("STOP: this cloud is switching the Bot from bot to carol, and this request starts from nova",
                      "\n".join(reply))
        p, reply = self.request(f"agent-mesh rename - carol erin {RC}")
        self.assertIn(f"agent-mesh-result rename {RC} pending - carol erin held 0", reply, p.stdout)
        self.assertEqual(self.relay(), "carol\n")
        self.assertEqual(set(self.former()), {"nova", "bot"})
        self.assertEqual((self.pending()["old"], self.pending()["new"]), ("carol", "erin"))

    def test_running_the_same_rename_again_keeps_the_rows_it_saw(self):
        p = self.apply("rename", "-", "bot", "nova", R)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        db = sqlite3.connect(self.box / "state" / "network.db")
        db.execute("INSERT INTO messages (id, sender, recipient, body, wake, sent_at) VALUES (?,?,?,?,1,?)",
                   ("01RNSTRAY", "demo/mac", "demo/nova-inbox", "not signed", int(time.time() * 1000)))
        db.commit()
        db.close()
        p = self.apply("rename", "-", "bot", "nova", R)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.pending()["before"], [])
        p = self.apply("rename", "-", "bot", "nova", RA)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual((self.pending()["req"], self.pending()["before"]), (RA, []))
        p = self.apply("rename", "-", "bot", "carol", RB)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(list(self.former()), ["nova"])
        p = self.apply("rename", "-", "bot", "nova", RC)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.pending()["before"], ["01RNSTRAY"])

    def test_a_taken_back_address_sent_again_with_a_new_request_id_keeps_its_claim(self):
        self.add_session("gamma-cloud")
        self.add_session("gamma-inbox")
        self.message("left over", inbox="gamma-inbox")
        p = self.apply("rename", "-", "bot", "gamma", R, "reclaim")
        self.assertEqual(p.stdout.splitlines()[-1], f"agent-mesh-result rename {R} pending - bot gamma held 1", p.stdout)
        p = self.apply("rename", "-", "bot", "gamma", RA)
        self.assertEqual(p.stdout.splitlines()[-1], f"agent-mesh-result rename {RA} pending - bot gamma held 1", p.stdout)
        self.assertEqual(list(json.loads((self.box / "exclude.json").read_text())["claims"]), [RA])

    def test_apply_needs_the_exclusive_lock_and_no_overrides(self):
        p = subprocess.run([sys.executable, "-I", str(self.box / "rn-names.py"), "apply", "rename", "-", "bot",
                            "nova", R], env=self.env, capture_output=True, text=True, timeout=60)
        self.assertIn("STOP: the maintenance lock is not held exclusively", p.stdout)
        p = run_apply(self.box, self.root, dict(self.env, RN_INBOX_NAME="x-inbox"), "rename", "-", "bot", "nova", R)
        self.assertIn("STOP: this cloud selects the first Bot with name overrides", p.stdout)
        self.assertIsNone(self.pending())

    def test_a_damaged_exclude_file_stops_fetch(self):
        (self.box / "exclude.json").write_text("{")
        mid = self.message("hello")
        p = self.cloud("fetch", mid)
        self.assertIn("exclude.json cannot be read", p.stdout + p.stderr)
        self.assertFalse((self.root / "inbox" / mid).exists())

    def test_an_exclude_file_with_the_wrong_structure_stops_fetch_and_requests(self):
        mid = self.message("held history")
        good = {"claims": {R: {"inbox": "bot-inbox", "rows": [["01RN0000000000000000000001", mid]], "ts": 1}},
                "accepted": {}}
        (self.box / "exclude.json").write_text(json.dumps(good))
        p = self.cloud("fetch", mid)
        self.assertNotIn('"status": "complete"', p.stdout)
        bad = [{}, None, [], {"claims": [], "accepted": {}}, {"claims": None, "accepted": {}}, {"claims": {}},
               {"claims": {R: {"inbox": "bot-inbox", "rows": "x"}}, "accepted": {}},
               {"claims": {R: {"inbox": "bot-inbox", "rows": [["01RN0000000000000000000001"]]}}, "accepted": {}},
               {"claims": {R: {"rows": []}}, "accepted": {}}, ["claims"], "text"]
        for data in bad:
            with self.subTest(data=data):
                (self.box / "exclude.json").write_text(json.dumps(data))
                p = self.cloud("fetch", mid)
                self.assertIn("exclude.json is damaged; nothing was fetched", p.stdout + p.stderr)
                self.assertNotIn('"status": "complete"', p.stdout)
                self.assertFalse((self.root / "inbox" / mid).exists())
                p = self.apply("history", "-", R, RB)
                self.assertIn("exclude.json is damaged", p.stdout)

    def test_a_rename_cut_off_after_registering_continues_with_the_same_request(self):
        script = self.box / "rn-names.py"
        original = script.read_text()
        self.assertEqual(original.count("    register_name(new)\n"), 1)
        script.write_text(original.replace("    register_name(new)\n", "    register_name(new)\n    os._exit(91)\n"))
        p = self.apply("rename", "-", "bot", "nova", R)
        self.assertEqual(p.returncode, 91, p.stdout + p.stderr)
        self.assertLessEqual({("nova-cloud", "cli:nova-cloud"), ("nova-inbox", "cli:nova-inbox")}, self.sessions())
        self.assertIs(self.pending()["registered"], False)
        script.write_text(original)
        p = self.apply("rename", "-", "bot", "nova", R)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(f"agent-mesh-result rename {R} pending - bot nova held 0", p.stdout)
        self.assertNotIn("registered", self.pending())
        self.fetch(self.message("to nova", inbox="nova-inbox"))
        self.assertEqual(self.relay(), "nova\n")

    def cut_off_reclaim(self, name, needle, after):
        self.add_session(f"{name}-cloud")
        self.add_session(f"{name}-inbox")
        old = self.message(f"left over in {name}", inbox=f"{name}-inbox")
        script = self.box / "rn-names.py"
        original = script.read_text()
        self.assertEqual(original.count(needle), 1)
        crash = "    os._exit(91)\n"
        script.write_text(original.replace(needle, needle + crash if after else crash + needle))
        p = self.apply("rename", "-", "bot", name, R, "reclaim")
        self.assertEqual(p.returncode, 91, p.stdout + p.stderr)
        p = self.cloud("fetch", old)
        self.assertNotIn('"status": "complete"', p.stdout)
        script.write_text(original)
        p = self.apply("rename", "-", "bot", name, R, "reclaim")
        self.assertEqual(p.stdout.splitlines()[-1], f"agent-mesh-result rename {R} pending - bot {name} held 1",
                         p.stdout + p.stderr)
        got = self.fetch(self.message("probe", inbox=f"{name}-inbox"))
        self.assertNotIn(old, got)
        self.assertEqual(self.relay(), f"{name}\n")

    def test_a_reclaim_cut_off_after_its_claim_keeps_the_history_held_back(self):
        self.cut_off_reclaim("nova", '        write_claim(exclude, req, f"{new}-inbox", lambda: inbox_rows(person, f"{new}-inbox"))\n',
                             after=True)

    def test_a_reclaim_cut_off_after_its_rename_record_keeps_the_history_held_back(self):
        (self.box / "bots-removed").mkdir()
        (self.box / "bots-removed" / "zeta").write_text("t\n")
        self.cut_off_reclaim("zeta", "    register_name(new)\n", after=False)
        self.assertFalse((self.box / "bots-removed" / "zeta").exists())

    def test_history_accepted_after_its_name_expired_still_reaches_the_bot(self):
        self.add_session("gamma-cloud")
        self.add_session("gamma-inbox")
        old = self.message("held gamma history", inbox="gamma-inbox")
        p = self.apply("rename", "-", "bot", "gamma", R, "reclaim")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.fetch(self.message("switch to gamma", inbox="gamma-inbox"))
        p = self.apply("rename", "-", "gamma", "delta", RA)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.fetch(self.message("switch to delta", inbox="delta-inbox"))
        self.assertEqual(self.relay(), "delta\n")
        gone = int(time.time()) - 60
        (self.box / "former").write_text("".join(f"{n} {gone if n == 'gamma' else u}\n"
                                                 for n, u in self.former().items()))
        stray = self.message("to gamma after it expired", inbox="gamma-inbox")
        p = self.apply("history", "-", R, RB)
        self.assertEqual(p.stdout.splitlines()[-1], f"agent-mesh-result history {RB} accepted {R} 1", p.stdout)
        got = self.fetch(self.message("probe", inbox="delta-inbox"))
        self.assertEqual(got[old]["status"], "complete")
        self.assertTrue(got[old]["orphan"])
        self.assertNotIn(stray, got)

    def test_a_rename_refused_while_registering_continues_with_a_new_request(self):
        script = self.box / "rn-names.py"
        original = script.read_text()
        script.write_text(original.replace("    register_name(new)\n",
                                           "    register_name(new)\n    raise Stop('simulated')\n"))
        p = self.apply("rename", "-", "bot", "nova", R)
        self.assertIn("STOP: simulated", p.stdout)
        self.assertEqual((self.pending()["req"], self.pending()["registered"]), (R, False))
        script.write_text(original)
        p = self.apply("rename", "-", "bot", "nova", RA)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.pending()["req"], RA)
        self.assertNotIn("registered", self.pending())

    def test_an_added_bot_stub_takes_back_a_removed_name_only_with_reclaim(self):
        (self.box / "bots-removed").mkdir()
        (self.box / "bots-removed" / "helper").write_text("2026-11-15T00:00:00Z\n")
        env = {"RN_BOT": "helper"}
        p = self.cloud("instructions", "1", "demo", TEAM_HASH, env=env)
        self.assertIn("STOP: the Bot helper was removed from this cloud", p.stdout)
        self.assertFalse((self.root / "bots" / "helper").exists())
        p = self.cloud("instructions", "1", "demo", TEAM_HASH, "reclaim", env=env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("agent-mesh-instructions OK 1", p.stdout)
        self.assertFalse((self.box / "bots-removed" / "helper").exists())
        self.assertTrue((self.root / "bots" / "helper" / ".registered").exists())


class ParseRequests(unittest.TestCase):
    def setUp(self):
        self.m = load("rn_routine_parse", HERE / "rn-routine.py")

    def test_rename_and_history_requests(self):
        p = self.m.parse_request(f"agent-mesh rename - bot nova {R}\nreclaim=1")
        self.assertEqual((p.verb, p.args, p.req, p.extras), ("rename", ["-", "bot", "nova"], R, {"reclaim": "1"}))
        p = self.m.parse_request(f"agent-mesh history beta accept add {R}")
        self.assertEqual((p.verb, p.args, p.req), ("history", ["beta", "add"], R))
        p = self.m.parse_request(f"agent-mesh update v0.11.0 {'a' * 40} {R}")
        self.assertEqual(p.verb, "update")
        for text in (f"agent-mesh rename - bot terminal-2 {R}", f"agent-mesh rename - bot {'b' * 35} {R}",
                     f"agent-mesh rename terminal bot nova {R}", f"agent-mesh rename - Bot nova {R}",
                     f"agent-mesh rename - bot nova {R}\nreclaim=2", f"agent-mesh rename - bot nova {R}\nlang=en",
                     f"agent-mesh rename - bot nova {R}\nreclaim=1\nreclaim=1", f"agent-mesh rename bot nova {R}",
                     f"agent-mesh history - accept other {R}", f"agent-mesh history - accept add {R}\nreclaim=1"):
            with self.subTest(text):
                self.assertIsNone(self.m.parse_request(text))


class PruneExclusion(unittest.TestCase):
    def test_purge_drops_rows_that_are_gone(self):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        db = d / "network.db"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE messages (id TEXT)")
        con.execute("INSERT INTO messages VALUES ('01A')")
        con.commit()
        con.close()
        p = d / "exclude.json"
        p.write_text(json.dumps({"claims": {R: {"inbox": "g-inbox", "rows": [["01A", "m1"], ["01B", "m2"]], "ts": 1}},
                                 "accepted": {RA: {"inbox": "g-inbox", "rows": [["01C", None]], "ts": 1}}}))
        fm = load("rn_fetch_prune", HERE / "rn-fetch.py")
        old = os.environ.get("RN_EXCLUDE_FILE")
        os.environ["RN_EXCLUDE_FILE"] = str(p)
        try:
            fm.prune_exclusion(str(db))
        finally:
            if old is None:
                os.environ.pop("RN_EXCLUDE_FILE")
            else:
                os.environ["RN_EXCLUDE_FILE"] = old
        data = json.loads(p.read_text())
        self.assertEqual(data["claims"][R]["rows"], [["01A", "m1"]])
        self.assertEqual(data["accepted"], {})


class AddedBot(BotBox, unittest.TestCase):
    def setUp(self):
        super().setUp()
        (self.box / "relay").write_text("alpha\n")
        (self.root / ".git").mkdir()
        self.beta = self.claim("beta", registered=True)
        self.n = 0

    def apply(self, *args, slot="beta"):
        env = dict(self.env, RN_BOT=slot) if slot else self.env
        return run_apply(self.box, self.root, env, *args)

    def names(self, slot="beta"):
        p = subprocess.run([sys.executable, "-I", str(self.box / "rn-names.py"), "names", slot], env=self.env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p.stdout.splitlines()

    def put(self, text, inbox, read=False):
        self.n += 1
        body = rr.fwd_body(KEY, f"m{self.n:04d}abc", "demo/mac", "codex", "01JTHREAD", text)
        now = int(time.time() * 1000)
        db = sqlite3.connect(self.box / "state" / "network.db")
        db.execute("INSERT INTO messages (id, sender, recipient, body, wake, sent_at, read_at) VALUES (?,?,?,?,1,?,?)",
                   (f"01RN{self.n:022d}", "demo/mac", f"demo/{inbox}", body.strip(), now, now if read else None))
        db.commit()
        db.close()
        return f"01RN{self.n:022d}", f"m{self.n:04d}abc"

    def assert_stop(self, p, why):
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(why, p.stdout)

    def fetched(self, slot, *ids):
        p = self.cloud("fetch", *ids, env={"RN_BOT": slot, "RN_GOT": "0", "REFWD": "0", "FETCH_TIMEOUT": "0"})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return {r["id"]: r for r in (json.loads(line) for line in p.stdout.splitlines() if line.startswith("{"))}

    def uninstall(self, relay):
        text = (HERE / "rn-cloud-uninstall.sh").read_text()
        self.assertEqual(text.count(PLACEHOLDERS), 1)
        text = text.replace(PLACEHOLDERS, f'TEAM="demo"; MAC="mac"; RELAY_NAME="{relay}"; LEVEL="bot"; MODE="normal"; '
                                          f'CHALLENGE="{REQ}"')
        text = text.replace("\nW=/workspace\n", f"\nW={shlex.quote(str(self.root.parent))}\n")
        script = self.d / "uninstall-bot.sh"
        script.write_text(text)
        code = hmac.new(KEY.encode(), f"agent-mesh-uninstall-v1\nack\n{REQ}".encode(), hashlib.sha256).hexdigest()[:16]
        return subprocess.run(["bash", str(script), "apply", code], env=dict(self.env, HOME=str(self.d)),
                              capture_output=True, text=True, timeout=60)

    def test_an_added_bot_switches_and_keeps_its_slot(self):
        p = self.apply("rename", "beta", "beta", "zed", REQ)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout.splitlines()[-1], f"agent-mesh-result rename {REQ} pending beta beta zed held 0")
        self.assertLessEqual({("zed-cloud", "cli:zed-cloud"), ("zed-inbox", "cli:zed-inbox")}, set(self.sessions()))
        self.assertEqual(self.names()[:2], ["beta", "zed-inbox"])
        self.put("hello", "zed-inbox")
        self.assertEqual(self.names(), ["zed", "beta-inbox", str(self.beta / "exclude.json")])
        self.assertEqual((self.beta / "name").read_text(), "zed\n")
        self.assertFalse((self.beta / "rename.pending").exists())
        self.assert_stop(self.add_bot("zed", "demo", TEAM_HASH), "zed is an address name of the Bot in bots/beta")
        self.assert_stop(self.apply("rename", "-", "alpha", "beta", "1" * 16, slot=None),
                         "beta is an address name of the Bot in bots/beta")
        self.assert_stop(self.apply("rename", "-", "alpha", "zed", "2" * 16, slot=None),
                         "zed is an address name of the Bot in bots/beta")
        p = self.apply("rename", "beta", "zed", "beta", "3" * 16)
        self.assertEqual(p.stdout.splitlines()[-1], f"agent-mesh-result rename {'3' * 16} pending beta zed beta held 0",
                         p.stdout)

    def test_the_first_bot_cannot_take_an_added_bots_pending_name(self):
        self.apply("rename", "beta", "beta", "zed", REQ)
        self.assert_stop(self.apply("rename", "-", "alpha", "zed", "1" * 16, slot=None),
                         "zed is an address name of the Bot in bots/beta")

    def test_add_bot_takes_back_a_name_removed_by_an_older_version_only_with_reclaim(self):
        self.tombstone("gamma")
        self.add_session("gamma-cloud")
        self.add_session("gamma-inbox")
        old = self.put("left over", "gamma-inbox", read=True)
        self.put("queued on the Mac while the name was unused", "gamma-inbox")
        self.assert_stop(self.add_bot("gamma", "demo", TEAM_HASH),
                         "its name can be taken back only when the Mac confirms it")
        self.assertFalse((self.root / "bots" / "gamma").exists())
        p = self.add_bot("gamma", "demo", TEAM_HASH, "reclaim")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout.strip().splitlines()[-1],
                         "Bot address: demo/gamma (this cloud uses gamma-cloud and gamma-inbox)")
        self.assertFalse((self.box / "bots-removed" / "gamma").exists())
        d = self.root / "bots" / "gamma"
        self.assertTrue((d / ".reclaim").exists())
        self.assertEqual(json.loads((d / "exclude.json").read_text())["claims"]["add"]["rows"], [list(old)])
        p = self.add_bot("--check", "gamma", REQ)
        self.assertEqual(p.stdout.strip().splitlines(),
                         ["agent-mesh-result held add 1", f"agent-mesh-result add-bot {REQ} ok gamma {TEAM_HASH}"])

    def test_add_bot_takes_back_an_earlier_session_with_reclaim(self):
        self.add_session("gamma-inbox")
        self.assert_stop(self.add_bot("gamma", "demo", TEAM_HASH), "the session demo/gamma-inbox already exists")
        p = self.add_bot("gamma", "demo", TEAM_HASH, "reclaim")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(json.loads((self.root / "bots/gamma/exclude.json").read_text())["claims"]["add"]["rows"], [])
        self.assertEqual(self.add_bot("--check", "gamma", REQ).stdout.strip(),
                         f"agent-mesh-result add-bot {REQ} ok gamma {TEAM_HASH}")

    def test_add_bot_holds_every_message_of_a_name_left_by_an_earlier_setup(self):
        self.add_session("gamma-cloud")
        self.add_session("gamma-inbox")
        read = self.put("read by an earlier setup", "gamma-inbox", read=True)
        unread = self.put("never read", "gamma-inbox")
        p = self.add_bot("gamma", "demo", TEAM_HASH, "reclaim")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(json.loads((self.root / "bots/gamma/exclude.json").read_text())["claims"]["add"]["rows"],
                         [list(read), list(unread)])
        self.assertEqual(self.add_bot("--check", "gamma", REQ).stdout.strip().splitlines()[0],
                         "agent-mesh-result held add 2")

    def test_a_removed_bot_taken_back_holds_what_it_had_and_gets_later_mail(self):
        p = self.apply("rename", "beta", "beta", "zed", REQ)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        switch = self.put("first message to the new address", "zed-inbox")
        self.fetched("beta", switch[1])
        self.assertEqual((self.beta / "name").read_text(), "zed\n")
        old = self.put("late message to the earlier address", "beta-inbox")
        self.assertEqual(self.fetched("beta", old[1])[old[1]]["status"], "complete")
        p = self.cloud("done", old[1], "--via", "send", env={"RN_BOT": "beta"})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        p = self.uninstall("beta")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse(self.beta.exists())
        tomb = (self.box / "bots-removed" / "beta").read_text()
        self.assertEqual(tomb.count("\n"), 1)
        self.assertRegex(json.loads(tomb)["removed"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(json.loads(tomb)["rows"], [list(old)])
        self.assertEqual(json.loads((self.box / "bots-removed" / "zed").read_text())["rows"], [list(switch)])
        later = self.put("queued on the Mac while the name was unused", "beta-inbox")
        p = self.add_bot("beta", "demo", TEAM_HASH, "reclaim")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(json.loads((self.beta / "exclude.json").read_text())["claims"]["add"]["rows"], [list(old)])
        self.assertEqual(self.add_bot("--check", "beta", REQ).stdout.strip().splitlines()[0],
                         "agent-mesh-result held add 1")
        got = self.fetched("beta")
        self.assertNotIn(old[1], got)
        self.assertEqual(got[later[1]]["status"], "complete")
        self.assertEqual(got[later[1]]["claim"], "new")

    def test_a_damaged_tombstone_stops_taking_the_name_back(self):
        (self.box / "bots-removed").mkdir()
        self.add_session("gamma-cloud")
        self.add_session("gamma-inbox")
        good = '{"removed": "2026-11-15T00:00:00Z", "rows": [["01E1", null]]}\n'
        for text in ("", "\n", "t\n", "2026-11-15T00:00:00Z", "2026-11-15 00:00:00\n", "2026-11-15T00:00:00Z\n\n",
                     good[:30], good[:-1], good + good, '{"removed": "x", "rows": []}\n',
                     '{"removed": "2026-11-15T00:00:00Z", "rows": [["01E1"]]}\n',
                     '{"removed": "2026-11-15T00:00:00Z", "rows": [], "more": 1}\n'):
            (self.box / "bots-removed" / "gamma").write_text(text)
            self.assert_stop(self.add_bot("gamma", "demo", TEAM_HASH, "reclaim"), "box/bots-removed/gamma is damaged")
            self.assertFalse((self.root / "bots" / "gamma").exists(), repr(text))
            self.assertEqual((self.box / "bots-removed" / "gamma").read_text(), text)

    def test_an_unfinished_uninstall_is_not_taken_back(self):
        self.claim("gamma", registered=True)
        self.tombstone("gamma")
        self.assert_stop(self.add_bot("gamma", "demo", TEAM_HASH, "reclaim"),
                         "removing the Bot gamma from this cloud did not finish")
        self.assertTrue((self.box / "bots-removed" / "gamma").exists())

    def test_an_interrupted_reclaim_continues(self):
        d = self.claim("gamma")
        (d / ".reclaim").write_text("t\n")
        self.tombstone("gamma")
        p = self.add_bot("gamma", "demo", TEAM_HASH, "reclaim")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue((d / ".registered").exists())
        self.assertFalse((self.box / "bots-removed" / "gamma").exists())


if __name__ == "__main__":
    unittest.main()
