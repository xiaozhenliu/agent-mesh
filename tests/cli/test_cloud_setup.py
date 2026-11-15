import json
import shutil
import sqlite3
import unittest

from render_prompt import ROOT
from tests.cli.install_support import (
    InstallHarness,
    TEAM_ID,
    OTHER_TEAM_ID,
    invite,
)


class CloudSetup(InstallHarness, unittest.TestCase):
    def test_cloud_setup_writes_the_relay_name_then_the_team_name(self):
        self.cloud_box()
        result = self.setup(invite(), "demo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.names_files(), {"relay": "bot\n", "person": "demo\n"})
        self.assertIn("Bot address: demo/bot (this cloud uses bot-cloud and bot-inbox)", result.stdout)
        moves = [l for l in self.calls().splitlines() if "rn join" in l]
        self.assertEqual(len(moves), 1)
        shutil.rmtree(self.box / "state")
        (self.mock / "joined").unlink()
        for f in ("relay", "person"):
            (self.box / f).unlink()
        result = self.setup(invite(), "demo", "alice")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.names_files(), {"relay": "alice\n", "person": "demo\n"})
        self.assertIn("Bot address: demo/alice", result.stdout)

    def test_cloud_setup_order_relay_before_person(self):
        self.cloud_box()
        self.tool("mv", 'echo "mv ${3:-$2}" >> "$CALL_LOG"\n' f'exec "{shutil.which("mv")}" "$@"')
        result = self.setup(invite(), "demo", "alice")
        self.assertEqual(result.returncode, 0, result.stderr)
        moves = [l.rsplit("/", 1)[-1] for l in self.calls().splitlines() if l.startswith("mv ")]
        self.assertEqual(moves, ["reason-network", "relay", "person"])
        self.assertEqual([f.name for f in self.box.iterdir() if f.name.startswith(".")], [])

    def test_cloud_setup_rerun_in_the_same_team_skips_join(self):
        self.cloud_box()
        for link in (invite(), invite().rsplit("#", 1)[1], "  " + invite() + "\n"):
            with self.subTest(link=link[:20]):
                self.joined_before()
                (self.base / "calls").unlink(missing_ok=True)
                result = self.setup(link, "demo", "alice")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("already joined this team as demo/cloud; the invite is not used", result.stdout)
                self.assertNotIn("rn join", self.calls())
                self.assertEqual(self.names_files()["relay"], "alice\n")

    def test_cloud_setup_in_a_team_with_the_same_name_but_another_id_stops(self):
        self.cloud_box()
        self.joined_before(team=OTHER_TEAM_ID)
        result = self.setup(invite(), "demo", "alice")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rn join", self.calls())
        self.assertIn("different team", result.stderr)
        self.assertIn("already in team 'demo'", result.stderr)
        self.assertEqual(self.names_files(), {})

    def test_cloud_setup_rerun_reads_verified_names(self):
        self.cloud_box()
        self.joined_before(me="demo (verified)  cloud")
        result = self.setup(invite(), "demo", "alice")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already joined this team as demo/cloud; the invite is not used", result.stdout)
        self.assertNotIn("rn join", self.calls())

    def test_cloud_setup_joined_as_someone_else_stops_without_join(self):
        self.cloud_box()
        for me in ("demo  laptop", "eve   cloud"):
            with self.subTest(me=me):
                self.joined_before(me=me)
                result = self.setup(invite(), "demo")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("review the existing deployment", result.stderr)
                self.assertNotIn("rn join", self.calls())
                self.assertEqual(self.names_files(), {})

    def test_cloud_setup_that_cannot_confirm_the_team_lets_the_engine_decide(self):
        self.cloud_box()
        cases = (("short code", "apple-river-stone-cloud", None),
                 ("bad base64", invite().replace("#tincan1", "#tincan1!!"), None),
                 ("cut off", invite(cut=10), None),
                 ("no id", invite(), json.dumps({"name": "demo"})),
                 ("bad json", invite(), "{"),
                 ("no team rows", invite(), "team-output"))
        for label, link, team in cases:
            with self.subTest(label):
                self.joined_before()
                if team == "team-output":
                    (self.mock / "team").write_text("something else\n")
                elif team is not None:
                    self.cloud_state(team)
                (self.base / "calls").unlink(missing_ok=True)
                result = self.setup(link, "demo", "alice")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("rn join", self.calls())
                self.assertIn("could not confirm", result.stderr)
                self.assertIn("already in team 'demo'", result.stderr)
                self.assertEqual(self.names_files(), {})

    def test_cloud_setup_with_an_unreadable_database_lets_the_engine_decide(self):
        self.cloud_box()
        self.joined_before()
        (self.box / "state" / "network.db").write_bytes(b"not a database" * 100)
        result = self.setup(invite(), "demo")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not confirm", result.stderr)

    def test_cloud_setup_never_renames(self):
        self.cloud_box()
        (self.box / "person").write_text("demo\n")
        (self.box / "relay").write_text("alice\n")
        result = self.setup(invite(), "demo", "bob")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("uses the Bot address name alice; renaming is not supported", result.stderr)
        self.assertNotIn("curl", self.calls())
        self.assertEqual(self.names_files(), {"relay": "alice\n", "person": "demo\n"})

    def test_cloud_setup_on_an_older_setup_needs_proof_for_a_custom_name(self):
        self.cloud_box()
        (self.box / "person").write_text("demo\n")
        for rows in ((), (("alice-inbox", "cli:someone-else"),)):
            with self.subTest(rows=rows):
                self.cloud_state(rows=rows)
                result = self.setup(invite(), "demo", "alice")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("cannot be proven", result.stderr)
                self.assertNotIn("curl", self.calls())
                self.assertNotIn("relay", self.names_files())
        self.joined_before()
        result = self.setup(invite(), "demo", "bot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.names_files()["relay"], "bot\n")
        (self.box / "relay").unlink()
        self.cloud_state(rows=(("alice-inbox", "cli:alice-inbox"),))
        result = self.setup(invite(), "demo", "alice")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.names_files()["relay"], "alice\n")

    def test_cloud_setup_repairs_a_damaged_relay_file_only_with_proof(self):
        self.cloud_box()
        for text in ("", "Bot\n", "a--b\n"):
            with self.subTest(text=text):
                for f in self.box.glob("relay*"):
                    f.unlink()
                (self.box / "person").write_text("demo\n")
                (self.box / "relay").write_text(text)
                shutil.rmtree(self.box / "state", ignore_errors=True)
                self.joined_before()
                result = self.setup(invite(), "demo", "alice")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("cannot be proven", result.stderr)
                self.assertNotIn("uses the Bot address name", result.stderr)
                self.assertIn("box/relay is damaged", result.stderr)
                self.assertEqual((self.box / "relay").read_text(), text)
                self.assertEqual(list(self.box.glob("relay.damaged-*")), [])
                self.cloud_state(rows=(("alice-inbox", "cli:alice-inbox"),))
                result = self.setup(invite(), "demo", "alice")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((self.box / "relay").read_text(), "alice\n")
                (backup,) = self.box.glob("relay.damaged-*")
                self.assertEqual(backup.read_text(), text)
        for f in self.box.glob("relay*"):
            f.unlink()
        (self.box / "relay").write_text("\n")
        result = self.setup(invite(), "demo", "bot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.box / "relay").read_text(), "bot\n")
        self.assertEqual(len(list(self.box.glob("relay.damaged-*"))), 1)

    def test_cloud_setup_interrupted_between_the_two_files_can_run_again(self):
        self.cloud_box()
        (self.mock / "fail-mv").write_text("")
        result = self.setup(invite(), "demo", "alice")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not write", result.stderr)
        self.assertEqual(self.names_files(), {"relay": "alice\n"})
        self.assertEqual([f.name for f in self.box.iterdir() if f.name.startswith(".")], [])
        (self.mock / "fail-mv").unlink()
        (self.mock / "team").write_text("team demo · relay: n0 public relays\n  demo  cloud  this machine\n")
        self.cloud_state(json.dumps({"id": TEAM_ID, "name": "demo"}))
        result = self.setup(invite(), "demo", "alice")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.names_files(), {"relay": "alice\n", "person": "demo\n"})

    def test_cloud_setup_invalid_relay_names_stop_before_any_download(self):
        self.cloud_box()
        for name in ("Bot", "a--b", "terminal", "terminal-2", "x" * 35, "a/b"):
            with self.subTest(name=name):
                result = self.setup(invite(), "demo", name)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid Bot address name", result.stderr)
        self.assertNotIn("curl", self.calls())
        self.assertEqual(self.names_files(), {})

    def test_cloud_runtime_on_mac_fails_before_accessing_cloud_paths(self):
        result = self.run_script("box/rn-cloud.sh", "who")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Bot's Linux cloud computer", result.stderr)
        self.assertNotIn("No such file", result.stderr)

    def test_cloud_purge_stops_engine_first_and_checks_days(self):
        box = self.base / "box"
        (box / "state").mkdir(parents=True)
        (box / "person").write_text("demo\n")
        (box / "outbound.key").write_text("k" * 64)
        (box / "rn-fetch.py").symlink_to(ROOT / "box/rn-fetch.py")
        (box / "rn-engine.py").symlink_to(ROOT / "box/rn-engine.py")
        self.tool("reason-network", 'echo "rn $*" >> "$CALL_LOG"', box / "bin/reason-network")
        db = sqlite3.connect(box / "state/network.db")
        db.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, sender TEXT, recipient TEXT, body TEXT, sent_at INTEGER)")
        db.execute("INSERT INTO messages VALUES ('m1', 'demo/terminal', 'demo/bot-inbox', 'old text', 1)")
        db.commit()
        db.close()
        self.env.update(MOCK_OS="Linux", MOCK_ARCH="x86_64", RN_CLOUD_BASE=str(box),
                        RN_INBOX_DIR=str(self.base / "inbox"), RN_THREADS_DIR=str(self.base / "threads"))
        for bad in ((), ("x",), ("-1",)):
            result = self.run_script("box/rn-cloud.sh", "purge", *bad)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("usage: rn-cloud.sh purge <days>", result.stderr)
        self.assertNotIn("rn stop", self.calls())
        result = self.run_script("box/rn-cloud.sh", "purge", "0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"messages": 1', result.stdout)
        self.assertEqual(self.calls().strip(), "rn stop")
