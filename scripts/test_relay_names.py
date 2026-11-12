import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import relay_names
from relay_names import from_env, problem

SCRIPT = Path(__file__).resolve().parent / "relay_names.py"
BOX = Path(__file__).resolve().parent.parent / "box"
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


if __name__ == "__main__":
    unittest.main()
