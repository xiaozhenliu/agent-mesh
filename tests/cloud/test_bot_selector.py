"""rn-cloud.sh picks its Bot with RN_BOT, the low-level overrides or box/relay; purge refuses on a cloud with several Bots."""
import json, os, subprocess, sys, unittest
from pathlib import Path

from tests.cloud.bot_support import BotBox, MAINT_STOP


class Selector(BotBox, unittest.TestCase):
    def init_inbox(self, **env):
        return self.cloud("init-inbox", env=env)

    def assert_refused(self, p, why):
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(why, p.stderr)
        self.assertEqual(self.calls(), [])

    def test_without_rn_bot_the_first_bot_uses_the_shared_folders(self):
        p = self.init_inbox()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.listened(), ["bot-inbox"])
        listen = [c for c in self.calls() if c["args"][:1] == ["listen"]][0]
        self.assertEqual(listen["inbox"], str(self.root / "inbox"))
        self.assertEqual(listen["threads"], str(self.root / "threads"))
        self.log.unlink()
        (self.box / "relay").write_text("alpha\n")
        p = self.init_inbox()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.listened(), ["alpha-inbox"])

    def test_rn_bot_selects_a_registered_bot_and_its_own_folders(self):
        (self.box / "relay").write_text("alpha\n")
        d = self.claim("beta", registered=True)
        p = self.init_inbox(RN_BOT="beta")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("demo/beta-inbox registered", p.stdout)
        self.assertEqual(self.listened(), ["beta-inbox"])
        listen = [c for c in self.calls() if c["args"][:1] == ["listen"]][0]
        self.assertEqual((listen["inbox"], listen["threads"]), (str(d / "inbox"), str(d / "threads")))
        self.log.unlink()
        p = self.init_inbox(RN_BOT="beta", RN_INBOX_DIR="", RN_CLOUD_NAME="")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.listened(), ["beta-inbox"])

    def test_rn_bot_values_that_name_no_usable_bot_are_refused(self):
        (self.box / "relay").write_text("alpha\n")
        self.claim("claimed")
        self.claim("gone", registered=True)
        self.tombstone("gone")
        cases = {
            "": "invalid RN_BOT ''",
            "Beta": "invalid RN_BOT 'Beta'",
            "beta_x": "invalid RN_BOT",
            "-beta": "invalid RN_BOT",
            "terminal": "invalid RN_BOT",
            "terminal-2": "invalid RN_BOT",
            "a" * 35: "invalid RN_BOT",
            "unknown": "the Bot unknown is not registered on this cloud",
            "claimed": "the Bot claimed is not registered on this cloud",
            "gone": "the Bot gone was removed from this cloud",
            "alpha": "the Bot alpha is not registered on this cloud",
        }
        for value, why in cases.items():
            with self.subTest(value=value):
                self.assert_refused(self.init_inbox(RN_BOT=value), why)
                self.assertFalse((self.root / "bots" / value / "inbox" / "_stray").exists())

    def test_rn_bot_cannot_be_combined_with_the_low_level_overrides(self):
        self.claim("beta", registered=True)
        for var in ("AGENT_RELAY_NAME", "RN_CLOUD_NAME", "RN_INBOX_NAME", "RN_INBOX_DIR", "RN_THREADS_DIR"):
            with self.subTest(var=var):
                self.assert_refused(self.init_inbox(RN_BOT="beta", **{var: "x"}),
                                    f"RN_BOT cannot be combined with {var}")

    def test_register_bot_needs_rn_bot_and_a_claim(self):
        self.assert_refused(self.cloud("register-bot"), "register-bot is run by rn-cloud-add-bot.sh with RN_BOT set")
        self.assert_refused(self.cloud("register-bot", env={"RN_BOT": "beta"}),
                            "the Bot beta has not been claimed on this cloud")
        self.claim("beta")
        p = self.cloud("register-bot", "extra", env={"RN_BOT": "beta"})
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("usage: RN_BOT=<name> rn-cloud.sh register-bot", p.stderr)
        p = self.cloud("register-bot", env={"RN_BOT": "beta"})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.listened(), ["beta-cloud", "beta-inbox"])
        self.assertIn("demo/beta-cloud and demo/beta-inbox registered on this box", p.stdout)

    def test_register_bot_does_not_need_the_mac(self):
        self.claim("beta")
        p = self.cloud("register-bot", env={"RN_BOT": "beta", "FAKE_OFFLINE": "1"})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("the Mac is not connected", p.stderr)
        self.assertEqual(self.listened(), ["beta-cloud", "beta-inbox"])

    def test_a_removed_first_bot_is_never_the_default(self):
        (self.box / "relay.removed").write_text("alpha\n")
        self.assert_refused(self.init_inbox(), "the first Bot was removed from this cloud (box/relay.removed)")
        self.claim("beta", registered=True)
        p = self.init_inbox(RN_BOT="beta")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.log.unlink()
        p = self.init_inbox(RN_INBOX_DIR=str(self.d / "elsewhere"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.listened(), ["bot-inbox"])

    def test_a_tombstoned_first_bot_is_refused_without_overrides(self):
        (self.box / "relay").write_text("alpha\n")
        self.tombstone("alpha")
        self.assert_refused(self.init_inbox(), "the Bot alpha was removed from this cloud")

    def test_the_low_level_overrides_keep_working_without_rn_bot(self):
        (self.box / "relay").write_text("alpha\n")
        p = self.init_inbox(RN_INBOX_NAME="custom-inbox", RN_INBOX_DIR=str(self.d / "in"))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.listened(), ["custom-inbox"])
        self.assertEqual(self.calls()[-1]["inbox"], str(self.d / "in"))

    def test_the_engine_never_inherits_the_maintenance_lock(self):
        self.claim("beta", registered=True)
        for env in ({}, {"RN_BOT": "beta"}):
            p = self.init_inbox(**env)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        p = self.cloud("who")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        calls = self.calls()
        self.assertTrue(calls)
        self.assertEqual({c["fd9"] for c in calls}, {False})
        self.assertEqual({c["mode"] for c in calls}, {"shared"})

    def test_commands_wait_for_an_exclusive_holder_and_then_stop(self):
        self.holder("exclusive")
        p = self.init_inbox(RN_MAINT_WAIT="0.3")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertEqual(p.stdout.strip(), MAINT_STOP)
        self.assertEqual(self.calls(), [])

    def test_commands_run_beside_a_shared_holder(self):
        self.holder("shared")
        p = self.init_inbox(RN_MAINT_WAIT="0.3")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_a_forged_inherited_lock_is_refused(self):
        p = self.init_inbox(RN_MAINT_FD="9", RN_MAINT_MODE="shared")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("the maintenance lock this command inherited is not held", p.stderr)
        self.assertEqual(self.calls(), [])


class Purge(BotBox, unittest.TestCase):
    def purge(self, **env):
        return self.cloud("purge", "0", env=env)

    def test_purge_is_refused_while_the_cloud_has_several_bots(self):
        self.claim("beta", registered=True)
        (self.root / "inbox" / "01A").mkdir(parents=True)
        for env in ({}, {"RN_BOT": "beta"}):
            with self.subTest(env=env):
                p = self.purge(**env)
                self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
                self.assertIn("this cloud has 2 Bots", p.stderr)
                self.assertIn("nothing was deleted", p.stderr)
        self.assertEqual(self.calls(), [])
        self.assertTrue((self.root / "inbox" / "01A").exists())
        (self.box / "relay.removed").write_text("bot\n")
        self.claim("gamma", registered=True)
        p = self.purge(RN_BOT="beta")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("this cloud has 2 Bots", p.stderr)

    def test_purge_of_the_only_added_bot_uses_its_folders(self):
        (self.box / "relay.removed").write_text("bot\n")
        d = self.claim("beta", registered=True)
        (d / "threads" / "t.md").write_text("note")
        (self.root / "threads").mkdir()
        (self.root / "threads" / "keep.md").write_text("other")
        p = self.purge(RN_BOT="beta")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        result = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(result["purged"]["threads"], 1, result)
        self.assertFalse((d / "threads" / "t.md").exists())
        self.assertTrue((self.root / "threads" / "keep.md").exists())

    def test_purge_of_the_only_first_bot_still_works(self):
        (self.root / "threads").mkdir()
        (self.root / "threads" / "t.md").write_text("note")
        p = self.purge()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse((self.root / "threads" / "t.md").exists())


if __name__ == "__main__":
    unittest.main()
