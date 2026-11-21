"""The Bot roster, per-Bot paths and the deployment record helpers, with disposable homes."""
import json
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import bots
from render_prompt import ROOT
from write_launch_agent import launch_agent_bytes

ENV = "RN_BIN={home}/.local/bin/reason-network\nPERSON=demo\nOUTBOUND_KEY_FILE={home}/.config/agent-relay/outbound.key\n"


class Roster(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name) / "home dir"
        self.home.mkdir()

    def legacy_env(self, name=None):
        conf = self.home / ".config/agent-relay"
        conf.mkdir(parents=True, exist_ok=True)
        names = f"LISTEN_NAME={name}\nCLOUD_NAME={name}-cloud\nINBOX_NAME={name}-inbox\n" if name else ""
        (conf / "env").write_text(ENV.format(home=self.home) + names)

    def added_env(self, name, plist=False):
        conf = self.home / ".config/agent-relay-bots" / name
        conf.mkdir(parents=True, exist_ok=True)
        (conf / "env").write_text(ENV.format(home=self.home)
                                  + f"LISTEN_NAME={name}\nCLOUD_NAME={name}-cloud\nINBOX_NAME={name}-inbox\n")
        if plist:
            agents = self.home / "Library/LaunchAgents"
            agents.mkdir(parents=True, exist_ok=True)
            (agents / f"com.local.agent-relay.{name}.plist").write_bytes(b"")

    def record(self, data):
        path = self.home / ".local/lib/agent-mesh/deployed.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data) if isinstance(data, dict) else data)

    def cli(self, *args):
        env = dict(os.environ, HOME=str(self.home))
        return subprocess.run([sys.executable, str(ROOT / "scripts/bots.py"), *args], env=env, capture_output=True,
                              text=True)

    def summary(self):
        return [(b["name"], b["kind"], b["stage"], b["cloud"], b["removing"], b["record"])
                for b in bots.roster(self.home)]

    def test_paths_of_the_first_bot_are_the_original_ones(self):
        p = bots.paths(self.home, "bot", bots.LEGACY)
        h = str(self.home)
        self.assertEqual(p, {
            "name": "bot", "kind": "legacy", "slot": "-", "address": "bot", "conf_dir": f"{h}/.config/agent-relay",
            "env": f"{h}/.config/agent-relay/env", "label": "com.local.agent-relay",
            "plist": f"{h}/Library/LaunchAgents/com.local.agent-relay.plist",
            "state_dir": f"{h}/Library/Application Support/agent-relay",
            "marker": f"{h}/Library/Application Support/agent-relay/registered",
            "log": f"{h}/Library/Logs/agent-relay.log", "launchd_log": f"{h}/Library/Logs/agent-relay.launchd.log"})

    def test_paths_of_an_added_bot_sit_beside_the_original_ones(self):
        p = bots.paths(self.home, "luna", bots.ADDED)
        h = str(self.home)
        self.assertEqual(p, {
            "name": "luna", "kind": "added", "slot": "luna", "address": "luna",
            "conf_dir": f"{h}/.config/agent-relay-bots/luna",
            "env": f"{h}/.config/agent-relay-bots/luna/env", "label": "com.local.agent-relay.luna",
            "plist": f"{h}/Library/LaunchAgents/com.local.agent-relay.luna.plist",
            "state_dir": f"{h}/Library/Application Support/agent-relay-bots/luna",
            "marker": f"{h}/Library/Application Support/agent-relay-bots/luna/registered",
            "log": f"{h}/Library/Logs/agent-relay-luna.log",
            "launchd_log": f"{h}/Library/Logs/agent-relay-luna.launchd.log"})

    def test_nothing_installed_is_an_empty_roster(self):
        self.assertEqual(bots.roster(self.home), [])
        self.assertFalse(bots.legacy_suppressed(self.home, {}))
        r = self.cli("list")
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        self.assertEqual(self.cli("paths", "bot").returncode, 3)

    def test_existing_single_bot_installs(self):
        cases = (("env with default names, no record", None, None, ("bot", "missing")),
                 ("env with a custom name and its record", "alice", {"schema": 1, "relay_name": "alice"},
                  ("alice", "ok")),
                 ("record without env", None, {"schema": 1, "relay_name": "carol"}, ("carol", "ok")))
        for why, name, record, want in cases:
            with self.subTest(why):
                for path in (self.home / ".config/agent-relay/env", self.home / ".local/lib/agent-mesh/deployed.json"):
                    path.unlink(missing_ok=True)
                if why != "record without env":
                    self.legacy_env(name)
                if record:
                    self.record(record)
                self.assertEqual(self.summary(), [(want[0], "legacy", "relay", "-", False, want[1])])
                r = self.cli("paths", want[0])
                self.assertEqual(r.returncode, 0, r.stderr)
                lines = dict(line.split("\t", 1) for line in r.stdout.splitlines())
                self.assertEqual(list(lines), ["KIND", "CONF_DIR", "ENV_FILE", "LABEL", "PLIST", "STATE_DIR",
                                               "MARKER", "LOG", "LAUNCHD_LOG", "STAGE", "SLOT", "ADDRESS"])
                self.assertEqual((lines["KIND"], lines["LABEL"], lines["STAGE"], lines["SLOT"], lines["ADDRESS"]),
                                 ("legacy", "com.local.agent-relay", "relay", "-", want[0]))
                self.assertEqual(self.cli("paths", "--kind", "legacy").stdout, r.stdout)

    def test_order_stage_cloud_and_removing_come_from_the_record(self):
        self.legacy_env()
        self.added_env("zed")
        self.added_env("amy", plist=True)
        self.added_env("lost")
        self.added_env("found", plist=True)
        self.record({"schema": 1, "relay_name": "bot", "grok_name": "Main",
                     "removing": {"name": "amy", "kind": "added", "step": "service"},
                     "bots": {"zed": {"stage": "relay", "cloud": {"state": "confirmed"}, "added_ms": 10,
                                      "grok_name": "Zed"},
                              "amy": {"stage": "configured", "cloud": {"state": "pending"}, "added_ms": 20},
                              "gone": {"stage": "relay", "cloud": {"state": "confirmed"}, "added_ms": 5}}})
        self.assertEqual(self.summary(), [
            ("bot", "legacy", "relay", "-", False, "ok"),
            ("gone", "added", "relay", "confirmed", False, "ok"),
            ("zed", "added", "relay", "confirmed", False, "ok"),
            ("amy", "added", "configured", "pending", True, "ok"),
            ("found", "added", "relay", "pending", False, "missing"),
            ("lost", "added", "configured", "pending", False, "missing")])
        grok = {b["name"]: b["grok_name"] for b in bots.roster(self.home)}
        self.assertEqual((grok["bot"], grok["zed"], grok["found"]), ("Main", "Zed", None))
        r = self.cli("list")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines()[0], "bot\tlegacy\trelay\t-\t0\tok\tbot")
        self.assertEqual(r.stdout.splitlines()[3], "amy\tadded\tconfigured\tpending\t1\tok\tamy")
        lines = dict(line.split("\t", 1) for line in self.cli("paths", "found").stdout.splitlines())
        self.assertEqual((lines["KIND"], lines["STAGE"], lines["LABEL"]),
                         ("added", "relay", "com.local.agent-relay.found"))

    def test_a_renamed_added_bot_keeps_its_slot_and_is_found_by_either_name(self):
        self.legacy_env()
        self.added_env("luna", plist=True)
        self.record({"schema": 1, "relay_name": "bot",
                     "bots": {"luna": {"stage": "relay", "cloud": {"state": "confirmed"}, "added_ms": 1, "name": "moon"}}})
        [first, added] = bots.roster(self.home)
        self.assertEqual((added["name"], added["slot"], added["address"]), ("luna", "luna", "moon"))
        self.assertEqual((first["slot"], first["address"]), ("-", "bot"))
        self.assertEqual(self.cli("list").stdout.splitlines()[1], "luna\tadded\trelay\tconfirmed\t0\tok\tmoon")
        for name in ("luna", "moon"):
            lines = dict(line.split("\t", 1) for line in self.cli("paths", name).stdout.splitlines())
            self.assertEqual((lines["LABEL"], lines["SLOT"], lines["ADDRESS"]),
                             ("com.local.agent-relay.luna", "luna", "moon"), name)
        self.record({"schema": 1, "relay_name": "bot",
                     "bots": {"luna": {"stage": "relay", "added_ms": 1, "name": "Bad_Name"}}})
        self.assertEqual(bots.roster(self.home)[1]["address"], "luna")

    def test_a_recovered_added_bot_without_a_plist_is_only_configured(self):
        self.added_env("luna")
        self.assertEqual(self.summary(), [("luna", "added", "configured", "pending", False, "missing")])
        self.added_env("luna", plist=True)
        self.assertEqual(self.summary(), [("luna", "added", "relay", "pending", False, "missing")])

    def test_an_unreadable_record_stops_instead_of_counting_as_empty(self):
        self.legacy_env()
        for text in ("{not json", json.dumps({"schema": 2}), json.dumps([1]),
                     json.dumps({"schema": 1, "bots": ["x"]}), json.dumps({"schema": 1, "bots": {"x": 1}})):
            with self.subTest(text):
                self.record(text)
                with self.assertRaises(ValueError):
                    bots.roster(self.home)
                for args in (("list",), ("paths", "bot"), ("paths", "--kind", "legacy")):
                    r = self.cli(*args)
                    self.assertEqual(r.returncode, 4, args)
                    self.assertIn("deployed.json", r.stderr)
                    self.assertEqual(r.stdout, "")
                r = self.cli("paths", "--kind", "legacy", "--lenient")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn(f"ENV_FILE\t{self.home}/.config/agent-relay/env\n", r.stdout)

    def test_the_first_bot_is_not_revived_by_default_values(self):
        self.added_env("luna", plist=True)
        self.assertTrue(bots.legacy_suppressed(self.home, {}))
        self.assertEqual([b["name"] for b in bots.roster(self.home)], ["luna"])
        self.record({"schema": 1, "relay_name": "bot", "legacy_removed": True,
                     "bots": {"luna": {"stage": "relay", "cloud": {"state": "confirmed"}, "added_ms": 1}}})
        data = bots.load_record(self.home)
        self.assertTrue(bots.legacy_suppressed(self.home, data))
        self.assertEqual([b["name"] for b in bots.roster(self.home)], ["luna"])
        self.assertIn("STAGE\tnew", self.cli("paths", "--kind", "legacy").stdout.splitlines())
        self.legacy_env()
        self.assertFalse(bots.legacy_suppressed(self.home, {}))
        self.assertEqual([b["name"] for b in bots.roster(self.home, {})], ["bot", "luna"])
        self.assertFalse(bots.legacy_suppressed(self.home, {"schema": 1, "relay_name": "bot"}))

    def test_paths_for_a_bot_not_here_yet(self):
        r = self.cli("paths", "--kind", "legacy")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"ENV_FILE\t{self.home}/.config/agent-relay/env\n", r.stdout)
        self.assertTrue(r.stdout.endswith("STAGE\tnew\nSLOT\t-\nADDRESS\t\n"))
        r = self.cli("paths", "luna", "--kind", "added")
        self.assertIn("LABEL\tcom.local.agent-relay.luna\n", r.stdout)
        self.assertEqual(self.cli("paths", "luna").returncode, 3)
        self.assertEqual(self.cli("paths", "Bad_Name", "--kind", "added").returncode, 3)
        self.assertEqual(self.cli("paths", "--kind", "added").returncode, 2)
        self.legacy_env("alice")
        self.assertEqual(self.cli("paths", "bot", "--kind", "legacy").returncode, 3)
        self.assertIn("STAGE\trelay", self.cli("paths", "alice", "--kind", "legacy").stdout)


class LaunchAgent(unittest.TestCase):
    LEGACY_DIGEST_INPUT = ("/Users/example", "/usr/bin/python3", "/Users/example/.local/lib/agent-mesh/agent_relay.py")

    def test_the_first_bot_plist_is_byte_for_byte_the_old_one(self):
        home, python, relay = self.LEGACY_DIGEST_INPUT
        old = plistlib.dumps({
            "Label": "com.local.agent-relay",
            "ProgramArguments": [python, relay],
            "EnvironmentVariables": {"PATH": f"{home}/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                                     "REASON_NETWORK_HOME": f"{home}/.reason/network"},
            "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 30, "ProcessType": "Background",
            "StandardOutPath": f"{home}/Library/Logs/agent-relay.launchd.log",
            "StandardErrorPath": f"{home}/Library/Logs/agent-relay.launchd.log"})
        self.assertEqual(launch_agent_bytes(home, python, relay), old)
        self.assertEqual(launch_agent_bytes(Path(home), python, relay), old)
        self.assertEqual(launch_agent_bytes(home, python, relay, label=None, env=None, state=None, log=None,
                                            launchd_log=None), old)

    def test_an_added_bot_gets_its_own_label_env_state_and_logs(self):
        home, python, relay = self.LEGACY_DIGEST_INPUT
        p = bots.paths(home, "luna", bots.ADDED)
        data = plistlib.loads(launch_agent_bytes(home, python, relay, label=p["label"], env=p["env"],
                                                 state=p["state_dir"], log=p["log"], launchd_log=p["launchd_log"]))
        self.assertEqual(data["Label"], "com.local.agent-relay.luna")
        self.assertEqual(data["ProgramArguments"], [python, relay])
        self.assertEqual(data["EnvironmentVariables"], {
            "PATH": f"{home}/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "REASON_NETWORK_HOME": f"{home}/.reason/network",
            "AGENT_RELAY_ENV": f"{home}/.config/agent-relay-bots/luna/env",
            "AGENT_RELAY_STATE": f"{home}/Library/Application Support/agent-relay-bots/luna",
            "AGENT_RELAY_LOG": f"{home}/Library/Logs/agent-relay-luna.log"})
        self.assertEqual(data["StandardOutPath"], f"{home}/Library/Logs/agent-relay-luna.launchd.log")
        self.assertEqual(data["StandardErrorPath"], f"{home}/Library/Logs/agent-relay-luna.launchd.log")
        for key in ("RunAtLoad", "KeepAlive", "ThrottleInterval", "ProcessType"):
            self.assertEqual(data[key], plistlib.loads(launch_agent_bytes(home, python, relay))[key])

    def test_the_command_line_writes_both_kinds(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, HOME=tmp)
            script = str(ROOT / "scripts/write_launch_agent.py")
            dest = Path(tmp) / "a.plist"
            subprocess.run([sys.executable, script, "/r.py", str(dest)], env=env, check=True)
            self.assertEqual(dest.read_bytes(), launch_agent_bytes(tmp, sys.executable, "/r.py"))
            p = bots.paths(tmp, "luna", bots.ADDED)
            subprocess.run([sys.executable, script, "/r.py", str(dest), "--label", p["label"], "--env", p["env"],
                            "--state", p["state_dir"], "--log", p["log"], "--launchd-log", p["launchd_log"]],
                           env=env, check=True)
            self.assertEqual(plistlib.loads(dest.read_bytes())["Label"], "com.local.agent-relay.luna")
            r = subprocess.run([sys.executable, script, "/r.py", str(dest), "--label", "x"], env=env,
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
