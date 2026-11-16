"""rn-cloud-add-bot.sh: refusals change nothing, an interrupted registration continues, --check answers the Mac."""
import json, os, unittest
from pathlib import Path

from tests.cloud.bot_support import BotBox, MAINT_STOP, TEAM_HASH, pin_file

REQ = "0123456789abcdef"
ADDRESS = "Bot address: demo/beta (this cloud uses beta-cloud and beta-inbox)"


class AddBot(BotBox, unittest.TestCase):
    def setUp(self):
        super().setUp()
        (self.box / "relay").write_text("alpha\n")

    def register(self, name="beta", team="demo", team_hash=TEAM_HASH, **env):
        return self.add_bot(name, team, team_hash, env=env)

    def check(self, name="beta", req=REQ, **env):
        return self.add_bot("--check", name, req, env=env)

    def snapshot(self):
        bots = self.root / "bots"
        files = sorted(str(p.relative_to(self.root)) for p in bots.rglob("*")) if bots.exists() else None
        return files, self.sessions(), self.listened()

    def assert_stop(self, p, why):
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertRegex(p.stdout, r"(?m)^STOP: ")
        self.assertIn(why, p.stdout)

    def assert_done(self, p):
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout.strip().splitlines()[-1], ADDRESS)
        d = self.root / "bots" / "beta"
        self.assertTrue((d / ".registered").is_file())
        self.assertEqual((d / ".claim").read_text().splitlines()[0], f"team {TEAM_HASH}")
        self.assertEqual({(n, k) for n, k in self.sessions() if n.startswith("beta-")},
                         {("beta-cloud", "cli:beta-cloud"), ("beta-inbox", "cli:beta-inbox")})
        for p in (d, d / "inbox", d / "threads", self.root / "bots"):
            self.assertEqual(p.stat().st_mode & 0o777, 0o700, p)
        self.assertEqual(list((self.root / "bots").glob(".tmp-*")), [])

    def test_a_new_bot_is_claimed_registered_and_answers_the_check(self):
        p = self.register()
        self.assert_done(p)
        self.assertEqual(self.listened(), ["beta-cloud", "beta-inbox"])
        self.assertEqual({c["fd9"] for c in self.calls()}, {False})
        self.assertEqual({c["mode"] for c in self.calls()}, {"exclusive"})
        p = self.check()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout.strip(), f"agent-mesh-result add-bot {REQ} ok beta {TEAM_HASH}")
        before = self.listened()
        p = self.register()
        self.assert_done(p)
        self.assertEqual(self.listened(), before)

    def test_a_cloud_without_box_relay_has_bot_as_its_first_bot(self):
        (self.box / "relay").unlink()
        self.assert_stop(self.register("bot"), "bot is the first Bot on this cloud")
        self.assert_done(self.register())
        (self.root / "bots" / "beta" / ".registered").unlink()
        (self.box / "relay.removed").write_text("bot\n")
        self.assertEqual(self.register().returncode, 0)

    def test_refusals_change_nothing(self):
        def person_other():
            (self.box / "person").write_text("other\n")

        def key_empty():
            (self.box / "outbound.key").write_text("")

        def unpinned():
            with open(self.engine, "a") as f:
                f.write("\n")

        def old_version():
            pin_file(self.builds, self.engine, "0.12.16")

        def no_engine():
            self.engine.unlink()

        def historical_session():
            self.add_session("beta-inbox")

        def taken_session():
            self.add_session("beta-cloud", "cli:someone-else")

        def tombstoned():
            self.tombstone("beta")

        def foreign_dir():
            (self.root / "bots" / "beta").mkdir(parents=True)

        def foreign_file():
            (self.root / "bots").mkdir()
            (self.root / "bots" / "beta").write_text("x")

        def other_team_claim():
            self.claim("beta", team_hash="f" * 16)

        def damaged_relay():
            (self.box / "relay").write_text("Not A Name\n")

        def symlinked_bots():
            (self.d / "elsewhere").mkdir()
            (self.root / "bots").symlink_to(self.d / "elsewhere")

        def no_person():
            (self.box / "person").unlink()

        cases = [
            ("person", person_other, {}, {}, "box/person is other, not demo"),
            ("no person", no_person, {}, {}, "cloud setup is incomplete (no box/person)"),
            ("key", key_empty, {}, {}, "box/outbound.key is missing or empty"),
            ("binary", unpinned, {}, {}, "is not a Reason Network build pinned by agent-mesh"),
            ("version", old_version, {}, {}, "this cloud runs Reason Network 0.12.16, but this release needs 0.12.17"),
            ("no engine", no_engine, {}, {}, "no engine at box/bin/engine/reason-network"),
            ("hash", None, {"team_hash": "0" * 16}, {}, "this cloud is in another team"),
            ("machine", None, {}, {"FAKE_TEAM": json.dumps({"team_id": "team-0001", "person": "demo",
                                                            "machine": "mac"})},
             "this computer is demo/mac in the team, not demo/cloud"),
            ("not in a team", None, {}, {"FAKE_TEAM": "none"}, "did not show this cloud in a team"),
            ("historical session", historical_session, {}, {}, "the session demo/beta-inbox already exists"),
            ("taken session", taken_session, {}, {}, "the session demo/beta-cloud already exists"),
            ("tombstone", tombstoned, {}, {}, "was removed from this cloud and its name cannot be used again"),
            ("dir without claim", foreign_dir, {}, {}, "exists but was not created by rn-cloud-add-bot.sh"),
            ("file", foreign_file, {}, {}, "bots/beta is not a folder"),
            ("other team claim", other_team_claim, {}, {}, "bots/beta was claimed for another team"),
            ("first bot", None, {"name": "alpha"}, {}, "alpha is the first Bot on this cloud"),
            ("damaged relay", damaged_relay, {}, {}, "box/relay is damaged"),
            ("symlink", symlinked_bots, {}, {}, "is a symlink; not touching it"),
            ("invalid name", None, {"name": "Beta"}, {}, "invalid Bot address name 'Beta'"),
            ("terminal", None, {"name": "terminal-3"}, {}, "invalid Bot address name"),
            ("long name", None, {"name": "b" * 35}, {}, "invalid Bot address name"),
            ("team name", None, {"team": "De mo"}, {}, "invalid team name"),
            ("hash format", None, {"team_hash": "ABC"}, {}, "the team hash must be 16 lowercase hex"),
        ]
        for label, prepare, args, env, why in cases:
            with self.subTest(label):
                self.setUp()
                if prepare:
                    prepare()
                before = self.snapshot()
                p = self.register(**args, **env)
                self.assert_stop(p, why)
                self.assertEqual(self.snapshot(), before)
                self.assertFalse((self.root / "bots" / "beta" / ".registered").exists())

    def test_usage_errors(self):
        for args in ((), ("beta",), ("beta", "demo"), ("--check", "beta"), ("beta", "demo", TEAM_HASH, "x")):
            with self.subTest(args=args):
                p = self.add_bot(*args)
                self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
                self.assertIn("usage: rn-cloud-add-bot.sh", p.stderr)
        self.assertEqual(self.snapshot(), (None, [], []))

    def test_an_old_engine_helper_without_the_lock_changes_nothing(self):
        (self.box / "rn-engine.py").unlink()
        (self.box / "rn-engine.py").write_text("import sys\nprint('usage: old', file=sys.stderr)\nsys.exit(2)\n")
        p = self.register()
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.snapshot(), (None, [], []))

    def test_a_cloud_without_the_distribution_stops(self):
        (self.box / "rn-cloud.sh").unlink()
        p = self.register()
        self.assert_stop(p, "no agent-mesh clone at")
        self.assertEqual(self.snapshot(), (None, [], []))

    def test_a_leftover_temporary_claim_is_cleaned_up(self):
        tmp = self.root / "bots" / ".tmp-abc123"
        tmp.mkdir(parents=True)
        (tmp / ".claim").write_text(f"team {TEAM_HASH}\n")
        self.assert_done(self.register())
        self.assertFalse(tmp.exists())

    def test_a_claim_without_its_folders_continues(self):
        d = self.root / "bots" / "beta"
        d.mkdir(parents=True)
        (d / ".claim").write_text(f"team {TEAM_HASH}\ntime x\n")
        p = self.register()
        self.assertIn("add-bot: continuing the unfinished registration of beta", p.stdout)
        self.assert_done(p)

    def test_an_interruption_after_the_first_session_continues(self):
        p = self.register(FAKE_DIE_ON="beta-inbox")
        self.assert_stop(p, "registering beta-cloud and beta-inbox did not finish")
        d = self.root / "bots" / "beta"
        self.assertTrue((d / ".claim").is_file())
        self.assertFalse((d / ".registered").exists())
        self.assertEqual([n for n, _ in self.sessions()], ["beta-cloud"])
        self.assert_stop(self.check(), "is not registered on this cloud yet")
        p = self.register()
        self.assertIn("continuing the unfinished registration", p.stdout)
        self.assert_done(p)

    def test_an_interruption_before_the_registered_mark_continues(self):
        self.claim("beta")
        self.add_session("beta-cloud")
        self.add_session("beta-inbox")
        p = self.register()
        self.assertIn("continuing the unfinished registration", p.stdout)
        self.assert_done(p)

    def test_a_session_registered_under_another_name_stops_the_registration(self):
        p = self.register(FAKE_REGISTER=json.dumps({"beta-inbox": "beta-inbox-2"}))
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("the engine registered beta-inbox-2 instead of demo/beta-inbox", p.stderr)
        self.assertFalse((self.root / "bots" / "beta" / ".registered").exists())

    def test_a_finished_bot_whose_sessions_vanished_stops(self):
        d = self.claim("beta")
        (d / ".registered").write_text("x\n")
        self.assert_stop(self.register(), "the session table does not show both beta-cloud and beta-inbox")

    def test_check_refusals(self):
        self.assert_stop(self.check(req="xyz"), "the request id must be 8 to 32")
        self.assert_stop(self.check(), "is not registered on this cloud yet")
        self.claim("beta", registered=True)
        self.assertEqual(self.check().returncode, 0)
        p = self.check(FAKE_TEAM=json.dumps({"team_id": "team-0002", "person": "demo", "machine": "cloud"}))
        self.assert_stop(p, "bots/beta was claimed for another team than the one this cloud is in")
        p = self.check(FAKE_TEAM=json.dumps({"team_id": "team-0001", "person": "demo", "machine": "mac"}))
        self.assert_stop(p, "this computer is demo/mac in the team, not demo/cloud")
        self.tombstone("beta")
        self.assert_stop(self.check(), "the Bot beta was removed from this cloud")

    def test_check_without_both_sessions_stops(self):
        d = self.claim("beta")
        (d / ".registered").write_text("x\n")
        self.add_session("beta-cloud")
        self.assert_stop(self.check(), "does not show both beta-cloud and beta-inbox")

    def test_registration_waits_for_running_commands_and_check_does_not(self):
        self.claim("gamma", registered=True)
        self.holder("shared")
        p = self.register(RN_MAINT_WAIT="0.3")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(p.stdout.strip(), MAINT_STOP)
        self.assertFalse((self.root / "bots" / "beta").exists())
        p = self.check("gamma", RN_MAINT_WAIT="0.3")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_check_waits_for_an_exclusive_holder(self):
        self.claim("beta", registered=True)
        self.holder("exclusive")
        p = self.check(RN_MAINT_WAIT="0.3")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(p.stdout.strip(), MAINT_STOP)


if __name__ == "__main__":
    unittest.main()
