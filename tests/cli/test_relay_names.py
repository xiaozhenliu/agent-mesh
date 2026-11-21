import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import relay_names
from relay_names import from_env, problem
from tests.helpers.paths import BOX, SCRIPTS

SCRIPT = SCRIPTS / "relay_names.py"
SESSIONS = """CREATE TABLE sessions (name TEXT PRIMARY KEY, key TEXT NOT NULL, agent TEXT NOT NULL, cwd TEXT NOT NULL,
repo TEXT, branch TEXT, doing TEXT, tier TEXT NOT NULL, last_seen INTEGER NOT NULL);
CREATE INDEX sessions_key ON sessions (key);
CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);"""

VALID = ["a", "7", "bot", "alice", "a-b", "a1-b2-c3", "terminal-x", "terminals", "xterminal", "terminal-3a",
         "x" * 34, "a" + "-b" * 16]
INVALID = ["", "a--b", "a-", "-a", "Bot", "aB", "a b", "a/b", "a@b", "a_b", "a.b", "terminal", "terminal-3",
           "terminal-12", "terminal--3", "x" * 35, "a\n", "é", "a\tb", "a-b-", "--"]


def slug(text):
    out = ""
    for c in text:
        if len(out) > 40:
            break
        if c.isascii() and c.isalnum():
            out += c.lower()
        elif out and not out.endswith("-"):
            out += "-"
    return out.rstrip("-")[:40]


def session_db(path, rows):
    db = sqlite3.connect(path)
    db.executescript(SESSIONS)
    for name, key, last_seen in rows:
        db.execute("INSERT INTO sessions VALUES (?, ?, 'cli', '/', NULL, NULL, NULL, 'pull', ?)", (name, key, last_seen))
    db.commit()
    db.close()


def directory(*sessions, person="demo", machine="mac"):
    rows = []
    for who, where, name, status in sessions:
        rows.append({"address": f"{who}/{name}", "name": name, "person": who, "machine": where, "agent": "cli",
                     "cwd": "~", "repo": None, "branch": None, "status": status, "since": 1, "doing": None,
                     "tier": "pull", "unread": 0, "is_me": False})
    return {"team": "demo", "person": person, "machine": machine, "machines": [], "sessions": rows}


class Rules(unittest.TestCase):
    def test_valid_and_invalid_names(self):
        for name in VALID:
            self.assertIsNone(problem(name), name)
        for name in INVALID:
            self.assertIsNotNone(problem(name), repr(name))
        self.assertIn("reserved", problem("terminal-3"))
        self.assertIn("34", problem("x" * 35))

    def test_reasons_follow_the_language_and_english_is_unchanged(self):
        with mock.patch.dict(os.environ, {"AGENT_MESH_LANG": "en"}):
            self.assertEqual(problem(""), "the Bot address name is empty")
            self.assertEqual(problem("x" * 35), f"the Bot address name {'x' * 35!r} is longer than 34 characters")
        with mock.patch.dict(os.environ, {"AGENT_MESH_LANG": "zh-CN"}):
            self.assertEqual(problem(""), "Bot 地址名为空")
            self.assertEqual(problem("terminal"), "Bot 地址名 'terminal' 是保留名：Reason Network 自己使用 terminal 和 terminal-<数字>")
            self.assertEqual(relay_names.grok_problem(" a"), "Bot 在 Grok 里的名字开头或结尾有空格")

    def test_valid_names_survive_the_engine_slug_and_invalid_ones_may_not(self):
        for name in VALID:
            for session in relay_names.derived(name):
                self.assertEqual(slug(session), session)
                self.assertLessEqual(len(session), 40)
        self.assertEqual(slug("a--b"), "a-b")
        self.assertEqual(slug("terminal--3"), "terminal-3")
        self.assertEqual(slug("x" * 35 + "-inbox"), "x" * 35 + "-inbo")

    def test_suggested_addresses_come_from_the_grok_name_or_not_at_all(self):
        cases = {"Nova": "nova", "Zoë Bot": "zoe-bot", "Grok 2": "grok-2", "My  -- Bot!": "my-bot",
                 "Ｎｏｖａ": "nova", "小助手 Nova": "nova", "R" * 40: "r" * 34, "a" * 33 + " b": "a" * 33}
        for grok, want in cases.items():
            self.assertEqual(relay_names.suggest(grok), want, grok)
            self.assertIsNone(problem(want), grok)
        for grok in ("小助手", "Terminal", "terminal 3", "---", "🤖"):
            self.assertIsNone(relay_names.suggest(grok), grok)

    def test_grok_names_are_kept_as_given_within_limits(self):
        for grok in ("Nova", "小助手", 'say "hi"', "it's a bot", "a  b", "👨‍💻 Coder", "x" * 64):
            self.assertIsNone(relay_names.grok_problem(grok), grok)
        for grok in ("", "   ", " Nova", "Nova ", "a\nb", "a\tb", "a\x00b", "a\u2028b", "x" * 65, None):
            self.assertIsNotNone(relay_names.grok_problem(grok), repr(grok))

    def test_derived(self):
        self.assertEqual(relay_names.derived("alice"), ("alice", "alice-cloud", "alice-inbox"))


class FourCopies(unittest.TestCase):
    """The cloud scripts cannot import relay_names, so each has its own copy of the rule; they must agree."""

    SAMPLES = [n for n in VALID + INVALID + ["café", "ä-b", "a-ä", "Ａ"] if n and "\n" not in n]

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.tools = base / "tools"
        self.tools.mkdir()
        for name, body in (("uname", 'if [ "$1" = -s ]; then echo Linux; else echo x86_64; fi'),
                           ("curl", "exit 1"), ("sha256sum", "exit 1")):
            (self.tools / name).write_text("#!/bin/sh\n" + body + "\n")
            (self.tools / name).chmod(0o755)
        (self.tools / "python3").symlink_to(sys.executable)
        self.box = base / "box"
        self.box.mkdir()
        (self.box / "person").write_text("demo\n")
        (self.box / "rn-engine.py").symlink_to(BOX / "rn-engine.py")
        self.env = dict(os.environ, PATH=f"{self.tools}{os.pathsep}{os.environ['PATH']}", RN_CLOUD_BASE=str(self.box))
        for key in ("AGENT_RELAY_NAME", "RN_CLOUD_NAME", "RN_INBOX_NAME", "MACHINE_NAME"):
            self.env.pop(key, None)
        spec = __import__("importlib.util").util.spec_from_file_location("rn_fetch_rules", BOX / "rn-fetch.py")
        self.fetch = __import__("importlib.util").util.module_from_spec(spec)
        spec.loader.exec_module(self.fetch)

    def run_box(self, script, *args):
        return subprocess.run(["bash", str(BOX / script), *args], env=self.env, capture_output=True, text=True,
                              timeout=30)

    def cloud_accepts(self, name):
        (self.box / "relay").write_text(name + "\n", encoding="utf-8")
        p = self.run_box("rn-cloud.sh", "who")
        self.assertNotEqual(p.returncode, 0, name)
        return "relay is damaged" not in p.stderr

    def setup_accepts(self, name):
        p = self.run_box("rn-cloud-setup.sh", "fake-invite", "demo", name)
        self.assertNotEqual(p.returncode, 0, name)
        return "invalid Bot address name" not in p.stderr

    def test_all_four_copies_agree(self):
        for name in self.SAMPLES:
            with self.subTest(name=name):
                want = problem(name) is None
                self.assertEqual(self.fetch.relay_name_ok(name), want)
                self.assertEqual(self.cloud_accepts(name), want)
                self.assertEqual(self.setup_accepts(name), want)

    def test_an_empty_box_relay_is_damaged_for_both_readers(self):
        (self.box / "relay").write_text("")
        self.assertIn("relay is damaged", self.run_box("rn-cloud.sh", "who").stderr)
        self.assertFalse(self.fetch.relay_name_ok(""))


class Env(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = Path(self.temp.name) / "env"

    def write(self, *lines):
        self.env.write_text("\n".join(lines) + "\n")
        return self.env

    def test_missing_file_is_none_and_incomplete_files_fail(self):
        self.assertIsNone(from_env(self.env))
        self.env.write_text("")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            from_env(self.env)
        self.write("PERSON=demo", "LISTEN_NAME=alice")
        with self.assertRaisesRegex(ValueError, "incomplete"):
            from_env(self.env)

    def test_relay_defaults_fill_missing_keys_before_the_check(self):
        base = ("RN_BIN=/x", "PERSON=demo")
        self.assertEqual(from_env(self.write(*base)), "bot")
        self.assertEqual(from_env(self.write(*base, "CLOUD_NAME=bot-cloud")), "bot")
        self.assertEqual(from_env(self.write(*base, "LISTEN_NAME=alice", "CLOUD_NAME=alice-cloud",
                                             "INBOX_NAME=alice-inbox")), "alice")
        with self.assertRaises(ValueError) as caught:
            from_env(self.write(*base, "LISTEN_NAME=alice"))
        self.assertIn("not derived", str(caught.exception))
        self.assertIn("alice, bot-cloud, bot-inbox", str(caught.exception))

    def test_invalid_listen_name_last_value_wins_and_quotes(self):
        base = ("RN_BIN=/x",)
        for bad in ("Alice", "a--b", "terminal", ""):
            with self.assertRaisesRegex(ValueError, "LISTEN_NAME"):
                from_env(self.write(*base, f"LISTEN_NAME={bad}", f"CLOUD_NAME={bad}-cloud",
                                    f"INBOX_NAME={bad}-inbox"))
        self.assertEqual(from_env(self.write(*base, "LISTEN_NAME=x", "CLOUD_NAME=x-cloud", "INBOX_NAME=x-inbox",
                                             "LISTEN_NAME=alice", "CLOUD_NAME=alice-cloud",
                                             'INBOX_NAME="alice-inbox"')), "alice")
        self.assertEqual(from_env(self.write("RN_BIN='/x'", "LISTEN_NAME='bob'", 'CLOUD_NAME="bob-cloud"',
                                             "  INBOX_NAME = bob-inbox  ", "# LISTEN_NAME=other")), "bob")


class Cli(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def run_cli(self, *args, stdin=""):
        env = dict(os.environ, HOME=str(self.base))
        return subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, env=env,
                              capture_output=True, text=True)

    def test_check(self):
        self.assertEqual(self.run_cli("check", "alice").returncode, 0)
        result = self.run_cli("check", "a--b")
        self.assertEqual(result.returncode, 1)
        self.assertIn("repeated", result.stderr)
        self.assertEqual(result.stdout, "")

    def test_grok_check_and_suggest(self):
        self.assertEqual(self.run_cli("grok-check", "小助手").returncode, 0)
        result = self.run_cli("grok-check", "a\nb")
        self.assertEqual(result.returncode, 1)
        self.assertIn("line break", result.stderr)
        result = self.run_cli("suggest", "Zoë Bot")
        self.assertEqual((result.returncode, result.stdout), (0, "zoe-bot\n"))
        result = self.run_cli("suggest", "小助手")
        self.assertEqual((result.returncode, result.stdout), (1, ""))

    def test_env_prints_only_the_name(self):
        result = self.run_cli("env", str(self.base / "missing"))
        self.assertEqual((result.returncode, result.stdout), (0, "\n"))
        env = self.base / "env"
        secret = "WEBHOOK-SECRET-0123456789"
        env.write_text(f"RN_BIN=/x\nWEBHOOK_URL=https://example.invalid/{secret}\nWEBHOOK_KEY={secret}\n"
                       "LISTEN_NAME=alice\nCLOUD_NAME=alice-cloud\nINBOX_NAME=alice-inbox\n")
        result = self.run_cli("env", str(env))
        self.assertEqual((result.returncode, result.stdout), (0, "alice\n"))
        env.write_text(f"RN_BIN=/x\nWEBHOOK_KEY={secret}\nLISTEN_NAME=x\n")
        result = self.run_cli("env", str(env))
        self.assertEqual(result.returncode, 1)
        self.assertIn("not derived", result.stderr)
        self.assertNotIn(secret, result.stdout + result.stderr)
        default = self.base / ".config/agent-relay/env"
        default.parent.mkdir(parents=True)
        default.write_text("RN_BIN=/x\n")
        self.assertEqual(self.run_cli("env").stdout, "bot\n")

    def test_registered_reads_the_latest_row_for_the_key(self):
        db = self.base / "network.db"
        result = self.run_cli("registered", "alice", "--db", str(db))
        self.assertEqual(result.returncode, 3)
        session_db(db, [("alice-2", "cli:alice", 2000), ("alice", "cli:alice", 1000), ("alice-x", "cli:other", 3000),
                        ("carol", "cli:carol", 1)])
        self.assertEqual(self.run_cli("registered", "alice", "--db", str(db)).stdout, "alice-2\n")
        self.assertEqual(self.run_cli("registered", "bob", "--db", str(db)).stdout, "\n")
        self.assertEqual(self.run_cli("registered", "carol", "--db", str(db)).stdout, "carol\n")
        broken = self.base / "broken.db"
        broken.write_text("not a database")
        result = self.run_cli("registered", "alice", "--db", str(broken))
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot read", result.stderr)

    def test_conflicts_cover_offline_machines_and_the_remembered_key(self):
        db = self.base / "network.db"
        check = lambda listing, name="alice": self.run_cli("conflicts", name, "--db", str(db),
                                                           stdin=json.dumps(listing))
        self.assertEqual(check(directory(("demo", "mac", "repo-claude", "idle"))).returncode, 0)
        result = check(directory(("demo", "laptop", "alice-inbox", "offline")))
        self.assertEqual(result.returncode, 1)
        self.assertIn("demo/alice-inbox (machine laptop, offline)", result.stderr)
        self.assertEqual(check(directory(("demo", "mac", "alice", "offline"))).returncode, 1)
        self.assertEqual(check(directory(("demo", "cloud", "alice", "offline"))).returncode, 1)
        self.assertEqual(check(directory(("friend", "laptop", "alice-cloud", "idle"))).returncode, 1)
        for bad in ("not json", "[]", '{"sessions": {}}', '{"sessions": [{}]}'):
            result = self.run_cli("conflicts", "alice", "--db", str(db), stdin=bad)
            self.assertEqual(result.returncode, 2, bad)
            self.assertIn("could not check", result.stderr)
        session_db(db, [("alice-2", "cli:alice", 1000)])
        result = check(directory())
        self.assertEqual(result.returncode, 1)
        self.assertIn("as alice-2", result.stderr)

    def test_conflicts_accept_this_deployments_own_sessions(self):
        db = self.base / "network.db"
        session_db(db, [("alice", "cli:alice", 1000)])
        own = directory(("demo", "mac", "alice", "offline"), ("demo", "cloud", "alice-cloud", "offline"),
                        ("demo", "cloud", "alice-inbox", "offline"))
        result = self.run_cli("conflicts", "alice", "--db", str(db), stdin=json.dumps(own))
        self.assertEqual(result.returncode, 0, result.stderr)
        for other in (("demo", "mac", "alice-inbox", "idle"), ("demo", "cloud", "alice", "offline"),
                      ("friend", "cloud", "alice-cloud", "offline")):
            listing = directory(("demo", "mac", "alice", "offline"), other)
            self.assertEqual(self.run_cli("conflicts", "alice", "--db", str(db),
                                          stdin=json.dumps(listing)).returncode, 1, other)


class NameCheck(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.db = self.base / "network.db"
        self.ps = self.base / "ps.txt"
        self.ps.write_text("")
        record = self.base / ".local/lib/agent-mesh/deployed.json"
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({
            "schema": 1, "relay_name": "bot", "removed_bots": ["gone"],
            "bots": {"luna": {"stage": "relay", "cloud": {"state": "confirmed"}, "added_ms": 1, "name": "moon"}},
            "former": [{"name": "old", "slot": "luna", "since_ms": 1, "until_ms": 2}]}))
        self.agents = self.base / "Library/LaunchAgents"
        self.agents.mkdir(parents=True)

    def check(self, name, *args, listing=None):
        env = dict(os.environ, HOME=str(self.base), AGENT_MESH_LANG="en")
        return subprocess.run([sys.executable, str(SCRIPT), "name-check", "demo", name, "--db", str(self.db),
                               "--ps-file", str(self.ps), *args], input=json.dumps(listing or directory()),
                              env=env, capture_output=True, text=True)

    def own(self, name, status="offline", since=1):
        rows = [{"person": "demo", "machine": "mac", "name": name, "status": status, "since": since},
                {"person": "demo", "machine": "cloud", "name": f"{name}-cloud", "status": "online", "since": 1},
                {"person": "demo", "machine": "cloud", "name": f"{name}-inbox", "status": "online", "since": 1}]
        return dict(directory(), sessions=rows)

    def test_a_new_name_needs_nothing_else(self):
        result = self.check("fresh", "--slot", "-")
        self.assertEqual((result.returncode, result.stdout), (0, "class new\n"), result.stderr)
        self.assertEqual(self.check("fresh").stdout, "class new\n")
        result = self.check("Bad_Name")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.check("fresh", "--slot", "Bad_Name").returncode, 2)

    def test_names_of_other_bots_stay_with_them(self):
        for name, slot in (("luna", "-"), ("moon", "-"), ("old", "-"), ("bot", "luna")):
            result = self.check(name, "--slot", slot)
            self.assertEqual(result.returncode, 1, (name, slot))
            self.assertIn("another Bot on this Mac", result.stderr)
        self.assertIn("(moon)", self.check("old", "--slot", "-").stderr)
        result = self.check("moon", "--slot", "luna")
        self.assertEqual(result.returncode, 1)
        self.assertIn("already this Bot's address name", result.stderr)
        result = self.check("moon")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("class", result.stdout)

    def test_a_former_name_of_the_same_bot_comes_back_with_its_evidence(self):
        session_db(self.db, [("old", "cli:old", 1)])
        result = self.check("old", "--slot", "luna", listing=self.own("old"))
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "class own")
        self.assertEqual(lines[-1], "unread 0")
        self.assertEqual(len([line for line in lines if line.startswith("evidence ")]), 4)
        self.assertFalse([line for line in lines if "not in this team's list" in line])
        result = self.check("luna", "--slot", "luna", listing=self.own("luna"))
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "class own")
        self.assertEqual(len([line for line in lines if line.startswith("evidence ")]), 3)
        session_db(self.base / "other.db", [("luna", "cli:luna", 1)])
        env = dict(os.environ, HOME=str(self.base))
        result = subprocess.run([sys.executable, str(SCRIPT), "name-check", "demo", "luna", "--slot", "luna", "--db",
                                 str(self.base / "other.db"), "--ps-file", str(self.ps)],
                                input=json.dumps(self.own("luna")), env=env, capture_output=True, text=True)
        self.assertEqual(result.stdout.splitlines()[0], "class own", result.stderr)

    def test_a_name_held_back_for_an_unfinished_rename_needs_only_its_cloud_sessions(self):
        record = self.base / ".local/lib/agent-mesh/deployed.json"
        data = json.loads(record.read_text())
        data["former"].append({"name": "next", "slot": "-", "since_ms": 5, "until_ms": 5, "reserved": True})
        record.write_text(json.dumps(data))
        cloud = dict(directory(), sessions=self.own("next")["sessions"][1:])
        result = self.check("next", "--slot", "-", listing=cloud)
        self.assertEqual((result.returncode, result.stdout), (0, "class own\nunread 0\n"), result.stderr)
        result = self.check("next", "--slot", "luna", listing=cloud)
        self.assertEqual(result.returncode, 1)
        self.assertIn("another Bot on this Mac", result.stderr)
        result = self.check("next", "--slot", "-", listing=self.own("next"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("demo/next", result.stderr)
        session_db(self.db, [("next", "cli:next", 1)])
        result = self.check("next", "--slot", "-", listing=cloud)
        self.assertEqual(result.returncode, 1)
        self.assertIn("cli:next", result.stderr)

    def test_a_removed_name_is_checked_on_this_mac_and_its_cloud_sessions_are_only_shown(self):
        result = self.check("gone", "--slot", "-", listing=self.own("gone"))
        self.assertEqual((result.returncode, result.stdout.splitlines()[0]), (0, "class removed"), result.stderr)
        self.assertNotIn("evidence this Mac's engine holds", result.stdout)
        session_db(self.db, [("gone", "cli:gone", 1)])
        idle = self.own("gone", status="idle", since=int(time.time() * 1000))
        result = self.check("gone", "--slot", "-", listing=idle)
        self.assertEqual((result.returncode, result.stdout.splitlines()[0]), (0, "class removed"), result.stderr)
        self.ps.write_text("  700 1 /x/reason-network listen --as gone --wait 600\n")
        result = self.check("gone", "--slot", "-", listing=idle)
        self.assertEqual(result.returncode, 1)
        self.assertIn("process 700 is listening as gone", result.stderr)
        self.ps.write_text("")
        for kept in (2, 1, 0):
            listing = dict(directory(), sessions=self.own("gone")["sessions"][:kept])
            result = self.check("gone", "--slot", "-", listing=listing)
            self.assertEqual(result.returncode, 0, (kept, result.stderr))
            lines = result.stdout.splitlines()
            self.assertEqual(lines[0], "class removed")
            absent = [n for n in ("gone-cloud", "gone-inbox") if n not in [r["name"] for r in listing["sessions"]]]
            for n in absent:
                self.assertIn(f"evidence session demo/{n}: not in this team's list; the Bot's cloud computer checks its "
                              "own sessions when it registers the name", lines)
            self.assertIn("evidence this Mac's engine holds the relay key of gone", lines)
        moved = self.own("gone")
        moved["sessions"][1]["machine"] = "laptop"
        self.assertIn("on machine laptop, not on cloud", self.check("gone", "--slot", "-", listing=moved).stderr)
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO sessions VALUES ('gone-2', 'cli:gone', 'cli', '/', NULL, NULL, NULL, 'pull', 2)")
        con.commit()
        con.close()
        result = self.check("gone", "--slot", "-", listing=self.own("gone"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("holds the relay key of gone as the session gone-2", result.stderr)

    def test_a_left_name_must_be_offline_on_this_mac_and_its_cloud_sessions_are_only_shown(self):
        session_db(self.db, [("left", "cli:left", 1)])
        now = int(time.time() * 1000)
        listing = self.own("left", since=now - 3600 * 1000)
        listing["sessions"][0]["status"] = "offline"
        result = self.check("left", "--slot", "-", listing=listing)
        self.assertEqual(result.stdout.splitlines()[0], "class left", result.stderr)
        listing["sessions"][0]["status"] = "online"
        self.assertIn("demo/left is online", self.check("left", "--slot", "-", listing=listing).stderr)
        listing["sessions"][0]["status"] = "offline"
        result = self.check("left", "--slot", "-", listing=dict(directory(), sessions=[]))
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "class left")
        self.assertEqual(len([line for line in lines if "not in this team's list" in line]), 2)
        self.assertIn("evidence this Mac's engine holds the relay key of left", lines)
        con = sqlite3.connect(self.db)
        con.execute("INSERT INTO sessions VALUES ('other', 'cli:left-cloud', 'cli', '/', NULL, NULL, NULL, 'pull', 1)")
        con.commit()
        con.close()
        self.assertIn("cli:left-cloud", self.check("left", "--slot", "-", listing=listing).stderr)
        named = dict(directory(machine="cloud"), sessions=[dict(listing["sessions"][0], machine="cloud", status="online")])
        self.assertIn("demo/left is online", self.check("left", "--slot", "-", listing=named).stderr)

    def test_sessions_of_this_mac_under_other_keys_block_the_name(self):
        session_db(self.db, [("hid", "cli:other", 1), ("hid2-inbox", "cli:zzz", 1)])
        result = self.check("hid", "--slot", "-")
        self.assertEqual(result.returncode, 1)
        self.assertIn("holds the session hid under the key cli:other", result.stderr)
        result = self.check("hid2", "--slot", "-")
        self.assertEqual(result.returncode, 1)
        self.assertIn("holds the session hid2-inbox under the key cli:zzz", result.stderr)

    def test_a_held_name_the_cloud_asked_to_claim_goes_through_the_full_checks(self):
        record = self.base / ".local/lib/agent-mesh/deployed.json"
        data = json.loads(record.read_text())
        data["former"].append({"name": "next", "slot": "-", "since_ms": 5, "until_ms": 5, "reserved": True,
                               "claim": True})
        record.write_text(json.dumps(data))
        result = self.check("next", "--slot", "-")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "class claim")
        self.assertEqual(len([line for line in lines if "not in this team's list" in line]), 2)
        self.ps.write_text("  700 1 /x/reason-network listen --as next-inbox\n")
        result = self.check("next", "--slot", "-")
        self.assertEqual(result.returncode, 1)
        self.assertIn("process 700 is listening as next", result.stderr)
        self.assertEqual(self.check("next", "--slot", "luna").returncode, 1)
        data["former"][0]["claim"] = True
        record.write_text(json.dumps(data))
        session_db(self.db, [("old", "cli:old", 1)])
        result = self.check("old", "--slot", "luna", listing=self.own("old"))
        self.assertEqual(result.stdout.splitlines()[0], "class claim", result.stderr)

    def test_cloud_sessions_alone_make_a_left_name_that_this_mac_can_take(self):
        cloud = dict(directory(), sessions=self.own("far")["sessions"][1:])
        result = self.check("far", "--slot", "-", listing=cloud)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "class left")
        since = time.strftime("%Y-%m-%d %H:%M", time.localtime(1 / 1000))
        for name in ("far-cloud", "far-inbox"):
            self.assertIn(f"evidence session demo/{name} on machine cloud: online since {since}", lines)
        self.assertFalse([line for line in lines if "not in this team's list" in line or "relay key" in line])

    def test_listening_processes_and_launchd_jobs_block_a_reclaim(self):
        session_db(self.db, [("gone", "cli:gone", 1)])
        listing = self.own("gone")
        self.ps.write_text("  500 1 /usr/bin/python3 relay.py\n  600 500 /x/reason-network listen --as gone-cloud --pull\n"
                           "  700 1 /x/reason-network listen --as=gone\n  800 1 grep listen --as gone\n")
        result = self.check("gone", "--slot", "-", listing=listing)
        self.assertEqual(result.returncode, 1)
        self.assertIn("process 600 is listening as gone", result.stderr)
        self.assertIn("process 700 is listening as gone", result.stderr)
        self.assertIn("process 800 is listening as gone", result.stderr)
        result = self.check("gone", "--slot", "-", "--relay-pid", "500", listing=listing)
        self.assertNotIn("process 600", result.stderr)
        self.assertIn("process 700", result.stderr)
        self.ps.write_text("")
        import plistlib
        job = self.agents / "com.example.helper.plist"
        job.write_bytes(plistlib.dumps({"Label": "com.example.helper", "ProgramArguments": ["/bin/run", "--as", "gone"]}))
        result = self.check("gone", "--slot", "-", listing=listing)
        self.assertIn(f"the launchd job {job} still uses gone", result.stderr)
        job.write_bytes(plistlib.dumps({"Label": "com.example.helper", "ProgramArguments": ["/bin/run", "/data/gone/x"]}))
        self.assertEqual(self.check("gone", "--slot", "-", listing=listing).returncode, 1)
        for other in ("gone-ish", "gonex", "agone", "/opt/gone_relay.py", "/opt/gone.sh", "gone_x", "x.gone",
                      "gone-inboxes"):
            job.write_bytes(plistlib.dumps({"Label": "com.example.helper", "ProgramArguments": ["/bin/run", other]}))
            self.assertEqual(self.check("gone", "--slot", "-", listing=listing).returncode, 0, other)
        job.write_bytes(plistlib.dumps({"Label": "com.example.helper", "ProgramArguments": ["/bin/run", "--as",
                                                                                         "gone-inbox"]}))
        self.assertIn(f"the launchd job {job} still uses gone", self.check("gone", "--slot", "-", listing=listing).stderr)
        env = self.base / "elsewhere/env"
        env.parent.mkdir()
        env.write_text("LISTEN_NAME=moon\nFORMER_NAMES=gone:99999999999\n")
        relay = self.agents / "com.local.agent-relay.zed.plist"
        relay.write_bytes(plistlib.dumps({"Label": "com.local.agent-relay.zed", "ProgramArguments": ["/bin/python3"],
                                          "EnvironmentVariables": {"AGENT_RELAY_ENV": str(env)}}))
        result = self.check("gone", "--slot", "-", listing=listing)
        self.assertIn(f"the launchd job {relay} still uses gone", result.stderr)
        result = self.check("gone", "--slot", "zed", listing=listing)
        self.assertEqual(result.returncode, 1)

    def test_unread_mail_to_the_name_is_counted_by_sender(self):
        session_db(self.db, [("gone", "cli:gone", 1)])
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE messages (id TEXT, sender TEXT, recipient TEXT, body TEXT, sent_at INTEGER, "
                    "read_at INTEGER)")
        con.executemany("INSERT INTO messages VALUES (?, ?, ?, 'x', 1, ?)",
                        [("1", "demo/b", "demo/gone", None), ("2", "demo/a", "demo/gone", None),
                         ("3", "demo/a", "demo/gone", None), ("4", "demo/a", "demo/gone", 5),
                         ("5", "demo/a", "demo/other", None)])
        con.commit()
        con.close()
        result = self.check("gone", "--slot", "-", listing=self.own("gone"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-3:], ["unread 3", "sender demo/a 2", "sender demo/b 1"])
