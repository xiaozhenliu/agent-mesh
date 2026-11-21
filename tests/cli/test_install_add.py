import hashlib
import json
import sqlite3
import time
import unittest

import messages
from tests.cli.install_support import EMPTY_DIRECTORY, TEAM_ID, InstallHarness

HOOK = "https://example.invalid/beta-hook"
KEY = "beta-secret-key-0123"
TEAM_HASH = hashlib.sha256(TEAM_ID.encode()).hexdigest()[:16]
LEGACY_LABEL = "com.local.agent-relay"


def m(key, *args):
    return messages.t(key, *args, lang="en")


class AddSupport(InstallHarness):
    def add(self, repo, *args, grok="Beta", address="", steps=None):
        self.env.update(AGENT_MESH_ADD_WAIT="10", AGENT_MESH_ADD_REPLY_WAIT="10")
        if steps is None:
            steps = [("Bot address name [", address), ("OK to apply?", "y"), ("Webhook URL:", HOOK),
                     ("(not echoed): ", KEY)]
        return self.run_staged(repo, "--add-bot", "--grok-name", grok, *args, steps=steps)

    def added(self, repo, name="beta"):
        self.registers_on_wake(name)
        self.bot_confirms(name)
        result = self.add(repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        (self.base / "calls").unlink()
        return result

    def bot(self, name="beta"):
        return (self.record() or {}).get("bots", {}).get(name)

    def plist(self, name):
        return self.home / f"Library/LaunchAgents/{LEGACY_LABEL}.{name}.plist"

    def legacy_plist(self):
        return self.home / f"Library/LaunchAgents/{LEGACY_LABEL}.plist"

    def assert_no_fd9(self):
        self.assertFalse((self.who / "fd9").exists(), (self.who / "fd9").read_text() if (self.who / "fd9").exists() else "")

    def legacy_touched(self):
        return [line for line in self.call_lines()
                if line in (f"launchctl bootout gui/501/{LEGACY_LABEL}", f"launchctl bootstrap gui/501 {self.legacy_plist()}")]


class AddBotFlow(AddSupport, unittest.TestCase):
    def test_add_registers_on_the_cloud_starts_its_relay_and_confirms(self):
        repo = self.two_ready()
        relay = self.home / ".local/lib/agent-mesh/agent_relay.py"
        inode, legacy_plist, legacy_pid = relay.stat().st_ino, self.legacy_plist().read_bytes(), None
        legacy_pid = self.multi_loaded()[LEGACY_LABEL]["pid"]
        self.registers_on_wake("beta")
        self.bot_confirms("beta")
        result = self.add(repo)
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertNotIn(KEY, out)
        self.assertIn(m("install.add_done", "demo/beta"), out)
        bot = self.bot()
        self.assertEqual((bot["stage"], bot["cloud"]["state"], bot["prompt"]["state"], bot["grok_name"], bot["team_hash"]),
                         ("relay", "confirmed", "confirmed", "Beta", TEAM_HASH))
        [post] = self.posts()
        self.assertEqual(post["url"], HOOK)
        headers = {k.lower(): v for k, v in post["headers"].items()}
        self.assertEqual(headers, {"content-type": "application/json", "authorization": f"Bearer {KEY}",
                                   "x-automation-key": KEY})
        body = post["body"]
        self.assertEqual((body["doorbell"], body["to"], body["reply_via"], body["source"]),
                         (False, "demo/beta", "demo/beta-cloud", "reason-network"))
        [item] = body["messages"]
        self.assertEqual((item["from"], item["agent"]), ("relay", "agent-relay"))
        self.assertIn("demo/beta", item["text"])
        [sent] = self.sent()
        self.assertEqual(sent["to"], "demo/beta")
        self.assertEqual(sent["body"], f"agent-mesh add-bot beta {bot['cloud']['req']}")
        self.assertTrue(self.plist("beta").is_file())
        self.assertIn(f"{LEGACY_LABEL}.beta", self.multi_loaded())
        self.assertEqual(self.multi_loaded()[LEGACY_LABEL]["pid"], legacy_pid)
        self.assertEqual(self.legacy_plist().read_bytes(), legacy_plist)
        self.assertEqual(relay.stat().st_ino, inode)
        self.assertEqual(self.legacy_touched(), [])
        env = self.home / ".config/agent-relay-bots/beta/env"
        self.assertEqual(env.stat().st_mode & 0o777, 0o600)
        self.assertIn("LISTEN_NAME=beta\n", env.read_text())
        template = (self.home / ".local/lib/agent-mesh/templates/AGENTS.reason-network.md").read_text()
        self.assertIn("  - `demo/alpha`", template)
        self.assertIn("  - `demo/beta` (called “Beta” in Grok)", template)
        self.assertFalse((self.home / ".local/lib/agent-mesh/rollback").exists())
        self.assert_no_fd9()

    def test_the_relay_waits_for_the_cloud_sessions_and_the_add_resumes(self):
        repo = self.two_ready()
        result = self.add(repo)
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 3, out)
        self.assertIn(m("install.add_wait_timeout", "demo/beta",
                        "./install.sh --relay-name beta --wake-bot --lang en"), out)
        bot = self.bot()
        self.assertEqual((bot["stage"], bot["cloud"]["state"]), ("configured", "pending"))
        self.assertFalse(self.plist("beta").exists())
        self.assertNotIn(f"{LEGACY_LABEL}.beta", self.multi_loaded())
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual(self.sent(), [])
        result = self.run_script("install.sh", "--restart-relay", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([label for label, _ in self.multi_started()][-1], LEGACY_LABEL)
        self.assertNotIn(f"{LEGACY_LABEL}.beta", [label for label, _ in self.multi_started()])
        self.assertFalse(self.plist("beta").exists())
        result = self.run_script("install.sh", "--add-bot", "--grok-name", "Beta", root=repo)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("install.add_unfinished", "beta", "./install.sh --add-bot --relay-name beta --lang en"),
                      result.stderr)
        (self.who / "all.json").write_text(json.dumps(self.cloud_listing("beta-cloud", "beta-inbox")))
        self.bot_confirms("beta")
        result = self.run_script("install.sh", "--add-bot", "--relay-name", "beta", root=repo, timeout=60)
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn(m("install.add_resume", "demo/beta"), out)
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual(self.bot()["cloud"]["state"], "confirmed")
        self.assertIn(f"{LEGACY_LABEL}.beta", self.multi_loaded())
        result = self.run_script("install.sh", "--add-bot", "--relay-name", "beta", root=repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn(m("install.add_exists", "beta", "beta"), result.stderr)
        self.assert_no_fd9()

    def test_a_reply_that_does_not_confirm_leaves_the_bot_pending(self):
        repo = self.two_ready()
        self.registers_on_wake("beta")
        self.bot_confirms("beta", team_hash="0" * 16)
        result = self.add(repo)
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertIn(m("install.add_reply_bad", "./install.sh --relay-name beta --wake-bot --lang en"), out)
        self.assertIn(f"agent-mesh-result add-bot {self.bot()['cloud']['req']} ok beta {'0' * 16}", out)
        self.assertEqual(self.bot()["cloud"]["state"], "pending")
        wake = "./install.sh --relay-name beta --wake-bot --lang en"
        for sender, name, code, key in (("demo/other", "beta", 3, "install.add_reply_timeout"),
                                        ("demo/beta", "gamma", 1, "install.add_reply_bad")):
            self.bot_confirms(name, sender=sender)
            result = self.run_script("install.sh", "--relay-name", "beta", "--wake-bot", root=repo, timeout=60)
            out = result.stdout + result.stderr
            self.assertEqual(result.returncode, code, out)
            self.assertIn(m(key, "demo/beta", wake) if code == 3 else m(key, wake), out)
            self.assertEqual(self.bot()["cloud"]["state"], "pending")
        self.bot_silent()
        req = self.bot()["cloud"]["req"]
        sent, posts = len(self.sent()), len(self.posts())
        self.put_reply(req, f"agent-mesh-result add-bot {{req}} ok beta {TEAM_HASH}\n", sender="demo/beta")
        result = self.run_script("install.sh", "--relay-name", "beta", "--wake-bot", root=repo, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.bot()["cloud"]["state"], self.bot()["prompt"]["state"]), ("confirmed", "pending"))
        self.assertIn(m("state.stub_missing", "demo/beta"), result.stdout)
        self.assertEqual(len(self.sent()), sent)
        self.assertEqual(len(self.posts()), posts + 1)
        self.assert_no_fd9()

    def test_a_relay_that_does_not_start_is_removed_and_the_bot_stays_pending(self):
        repo = self.two_ready()
        legacy_pid = self.multi_loaded()[LEGACY_LABEL]["pid"]
        self.registers_on_wake("beta")
        self.bot_confirms("beta")
        for flag in ("fail-bootstrap", "no-up", "die", "noreg"):
            (self.state / f"{flag}.{LEGACY_LABEL}.beta").write_text("")
            if self.bot() is None:
                result = self.add(repo, "--relay-name", "beta",
                                  steps=[("OK to apply?", "y"), ("Webhook URL:", HOOK), ("(not echoed): ", KEY)])
            else:
                result = self.run_script("install.sh", "--relay-name", "beta", "--wake-bot", root=repo, timeout=60)
            out = result.stdout + result.stderr
            self.assertEqual(result.returncode, 1, flag + out)
            self.assertIn(m("install.add_start_failed", "./install.sh --relay-name beta --wake-bot --lang en"), out)
            self.assertEqual(self.bot()["stage"], "configured", flag)
            self.assertFalse(self.plist("beta").exists(), flag)
            self.assertNotIn(f"{LEGACY_LABEL}.beta", self.multi_loaded(), flag)
            self.assertEqual(self.multi_loaded()[LEGACY_LABEL]["pid"], legacy_pid, flag)
            self.assertFalse((self.home / ".local/lib/agent-mesh/rollback").exists(), flag)
            self.assertEqual(self.sent(), [], flag)
        result = self.run_script("install.sh", "--relay-name", "beta", "--wake-bot", root=repo, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.bot()["stage"], self.bot()["cloud"]["state"]), ("relay", "confirmed"))

    def test_a_failed_wake_stops_without_showing_the_key(self):
        repo = self.two_ready()
        (self.hooks / "code").write_text("500")
        result = self.add(repo)
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertIn(m("install.add_wake_failed", "./install.sh --relay-name beta --wake-bot --lang en"), out)
        self.assertIn(m("state.wake_failed", "demo/beta", 500), out)
        self.assertNotIn(KEY, out)
        self.assertNotIn(HOOK, out)
        self.assertEqual(self.bot()["stage"], "configured")
        self.assertFalse(self.plist("beta").exists())

    def test_without_a_name_in_grok_the_installer_asks_for_it(self):
        repo = self.two_ready()
        self.registers_on_wake("helper")
        self.bot_confirms("helper")
        self.env.update(AGENT_MESH_ADD_WAIT="10", AGENT_MESH_ADD_REPLY_WAIT="10")
        result = self.run_staged(repo, "--add-bot", steps=[
            ("called in Grok? ", ""), ("called in Grok? ", "Helper"), ("Bot address name [helper]: ", "Bad_Name"),
            ("Bot address name [helper]: ", ""), ("OK to apply?", "y"), ("Webhook URL:", HOOK),
            ("(not echoed): ", KEY)])
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn(m("install.try_again", m("names.grok_empty")), out)
        self.assertIn(m("install.try_again", m("names.bad_chars", repr("Bad_Name"))), out)
        self.assertEqual(self.bot("helper")["grok_name"], "Helper")
        self.assertEqual(self.bot("helper")["cloud"]["state"], "confirmed")


class AddPreconditions(AddSupport, unittest.TestCase):
    def run_add(self, repo, *extra):
        return self.run_script("install.sh", "--add-bot", "--grok-name", "Beta", "--relay-name", "beta", *extra,
                               root=repo, timeout=30)

    def test_add_needs_an_existing_installation(self):
        self.fresh()
        result = self.run_script("install.sh", "--add-bot", "--grok-name", "Beta")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("install.add_no_deployment"), result.stderr)
        self.assertIsNone(self.record())

    def test_add_refuses_yes_and_other_modes(self):
        self.fresh()
        for extra, key in ((["--yes"], "install.add_no_yes"), (["--webhook"], "install.add_alone"),
                           (["--restart-relay"], "install.add_alone")):
            result = self.run_script("install.sh", "--add-bot", "--grok-name", "Beta", *extra)
            self.assertEqual(result.returncode, 2, extra)
            self.assertIn(m(key), result.stderr)

    def test_add_waits_until_every_bot_and_the_cloud_are_complete(self):
        repo = self.two_ready()
        env = self.env_file()
        lib = self.home / ".local/lib/agent-mesh"
        base_record, base_env = self.record(), env.read_text()
        valid_request = {"req": "0" * 16, "tag": "v1", "commit": "a" * 40, "mode": "line", "sent_ms": 1, "target": {}}
        cases = [
            ("release", lambda d: d["cloud"].pop("release"), None, "install.add_update_first"),
            ("cloud pending", lambda d: d["cloud"].update(state="pending"), None, "install.add_update_first"),
            ("cloud_setup old", lambda d: d["cloud_setup"].update(hash="0" * 64), None, "install.add_update_first"),
            ("cloud_engine missing", lambda d: d.pop("cloud_engine"), None, "install.add_update_first"),
            ("prompt pending", lambda d: d["prompt"].update(state="pending"), None, "install.add_update_first"),
            ("request", lambda d: d.update(request=valid_request), None, "install.add_update_first"),
            ("removing", lambda d: d.update(removing={"name": "alpha", "kind": "legacy", "step": "stopped"}), None,
             "install.bot_removing"),
            ("webhook", None, lambda: env.write_text("".join(l + "\n" for l in base_env.splitlines()
                                                             if not l.startswith("WEBHOOK_"))),
             "install.add_finish_first"),
            ("engine txn", None, lambda: (lib / "engine-upgrade.json").write_text('{"stage": "prepared"}'),
             "install.add_update_first"),
            ("relay plist", None, lambda: self.legacy_plist().write_bytes(b"other"), "install.add_update_first"),
        ]
        plist = self.legacy_plist().read_bytes()
        for name, change, files, key in cases:
            data = json.loads(json.dumps(base_record))
            if change:
                change(data)
            self.write_record(data)
            if files:
                files()
            result = self.run_add(repo)
            self.assertEqual(result.returncode, 1, name + result.stdout + result.stderr)
            args = {"install.bot_removing": ("alpha", "alpha"),
                    "install.add_finish_first": ("alpha", "./install.sh --webhook --lang en")}.get(key, ())
            self.assertIn(m(key, *args), result.stderr, name)
            self.assertNotIn("bots", self.record() or {}, name)
            self.assertEqual(self.posts(), [], name)
            env.write_text(base_env)
            (lib / "engine-upgrade.json").unlink(missing_ok=True)
            self.legacy_plist().write_bytes(plist)
        result = self.state_cmd("txn-start", "--lang", "en", "--need", "cloud", "--was", "1", "--commit", "a" * 40,
                                root=repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_add(repo)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("install.add_update_first"), result.stderr)
        self.write_record(base_record)
        result = self.run_add(repo)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn(m("install.add_plan", "demo/beta", "Beta", "demo/alpha"), result.stdout)
        self.assertIn(m("install.add_plan_no_tty",
                        "./install.sh --add-bot --grok-name 'Beta' --relay-name beta --lang en"), result.stdout)
        self.assertNotIn("bots", self.record())
        self.assertEqual(self.record()["cloud"]["release"], "0.0.1")
        self.assert_no_fd9()

    def test_add_uses_the_existing_team(self):
        repo = self.two_ready()
        result = self.run_add(repo, "--name", "other")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("install.add_team_other", "other", "demo"), result.stderr)
        result = self.run_add(repo, "--name", "demo")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertNotIn("rn setup", self.calls())
        self.assertNotIn("rn init", self.calls())
        self.assertNotIn("curl", self.calls())
        (self.who / "team.json").write_text(json.dumps({"team_id": TEAM_ID, "person": "someone", "machine": "mac"}))
        result = self.run_add(repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn(m("install.add_team_person", "someone", "demo"), result.stderr)

    def test_without_a_terminal_the_name_in_grok_must_be_given(self):
        repo = self.two_ready()
        result = self.run_script("install.sh", "--add-bot", root=repo)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("install.add_need_grok", "./install.sh --add-bot --grok-name '<its name in Grok>' --lang en"),
                      result.stderr)
        self.assertNotIn("bots", self.record())


class AddNames(AddSupport, unittest.TestCase):
    def run_add(self, repo, *extra):
        return self.run_script("install.sh", "--add-bot", *extra, root=repo, timeout=30)

    def test_the_three_names_are_checked_without_exemptions(self):
        repo = self.two_ready()
        for listing in (self.cloud_listing("beta", machine="mac", status="offline"),
                        self.cloud_listing("beta-inbox", status="offline"), self.cloud_listing("beta-cloud")):
            (self.who / "all.json").write_text(json.dumps(listing))
            result = self.run_add(repo, "--grok-name", "Beta", "--relay-name", "beta")
            self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
            self.assertIn(m("install.name_class_left"), result.stdout)
        for listing, why in ((self.cloud_listing("beta", machine="mac"), m("names.reclaim_online", "demo/beta", "online")),
                             (self.cloud_listing("beta-cloud", machine="laptop"),
                              m("names.reclaim_machine", "demo/beta-cloud", "laptop", "cloud"))):
            (self.who / "all.json").write_text(json.dumps(listing))
            result = self.run_add(repo, "--grok-name", "Beta", "--relay-name", "beta")
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn(why, result.stderr)
        (self.who / "all.json").write_text(json.dumps(self.cloud_listing()))
        self.sessions([("old", "cli:beta-inbox", 1)])
        result = self.run_add(repo, "--grok-name", "Beta", "--relay-name", "beta")
        self.assertEqual(result.returncode, 1)
        self.assertIn(m("names.add_key_taken", "cli:beta-inbox", "old"), result.stderr)
        data = self.record()
        data["removed_bots"] = ["gone"]
        self.write_record(data)
        result = self.run_add(repo, "--grok-name", "Beta", "--relay-name", "gone")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn(m("install.name_class_removed"), result.stdout)
        self.assertIn(m("names.evidence_absent", "demo/gone-cloud"), result.stdout)
        result = self.run_add(repo, "--grok-name", "Beta", "--relay-name", "alpha")
        self.assertEqual(result.returncode, 1)
        self.assertIn(m("install.add_exists", "alpha", "alpha"), result.stderr)
        (self.who / "fail").write_text("")
        result = self.run_add(repo, "--grok-name", "Beta", "--relay-name", "fresh")
        self.assertEqual(result.returncode, 1)
        self.assertIn(m("install.add_who_failed"), result.stderr)
        self.assertNotIn("bots", self.record())

    def test_a_removed_name_is_reclaimed_after_a_confirmation_and_its_set_aside_mail_is_recorded(self):
        repo = self.two_ready()
        ps = self.base / "ps.txt"
        ps.write_text("")
        self.env["MOCK_PS"] = str(ps)
        data = self.record()
        data["removed_bots"] = ["beta"]
        self.write_record(data)
        rows = [{"person": "demo", "machine": "mac", "name": "beta", "status": "offline", "since": 1},
                {"person": "demo", "machine": "cloud", "name": "beta-cloud", "status": "online", "since": 1},
                {"person": "demo", "machine": "cloud", "name": "beta-inbox", "status": "online", "since": 1}]
        (self.who / "all.json").write_text(json.dumps(dict(EMPTY_DIRECTORY, sessions=rows)))
        self.sessions([("beta", "cli:beta", 1)])
        result = self.run_add(repo, "--grok-name", "Beta", "--relay-name", "beta")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn(m("install.name_class_removed"), result.stdout)
        self.assertIn(m("names.evidence_key", "beta"), result.stdout)
        self.assertNotIn("bots", self.record())
        self.registers_on_wake("beta")
        self.bot_replies(body=f"agent-mesh-result request {{req}}\nagent-mesh-result lang en\n"
                              f"agent-mesh-result stub 1\nagent-mesh-result add-bot {{req}} ok beta {TEAM_HASH}\n"
                              "agent-mesh-result held add 3\n", sender="demo/beta")
        result = self.add(repo, "--relay-name", "beta",
                          steps=[("OK to apply?", "y"), ("Webhook URL:", HOOK), ("(not echoed): ", KEY)])
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertRegex(out, r"RN_BOT=beta \S+ \S*rn-cloud\.sh instructions \S+ '[^']*' '[0-9a-f]{16}' reclaim\n")
        self.assertIn(m("state.held_note", 3, "agent-mesh rename --accept-history --relay-name beta"), out)
        data = self.record()
        self.assertNotIn("removed_bots", data)
        self.assertEqual(data["held"], {"beta": {"add": {"count": 3}}})
        self.assertEqual(data["bots"]["beta"]["reclaim"], 1)

    def test_unread_mail_to_a_name_left_by_an_earlier_setup_is_shown_as_set_aside(self):
        repo = self.two_ready()
        ps = self.base / "ps.txt"
        ps.write_text("")
        self.env["MOCK_PS"] = str(ps)
        old = int(time.time() * 1000) - 2 * 86400 * 1000
        rows = [{"person": "demo", "machine": "mac", "name": "beta", "status": "offline", "since": old},
                {"person": "demo", "machine": "cloud", "name": "beta-cloud", "status": "offline", "since": old},
                {"person": "demo", "machine": "cloud", "name": "beta-inbox", "status": "offline", "since": old}]
        (self.who / "all.json").write_text(json.dumps(dict(EMPTY_DIRECTORY, sessions=rows)))
        db = sqlite3.connect(self.sessions([("beta", "cli:beta", 1)]))
        db.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, sender TEXT, recipient TEXT, body TEXT, "
                   "sent_at INTEGER, held INTEGER, read_at INTEGER)")
        db.executemany("INSERT INTO messages VALUES (?, ?, 'demo/beta', 'x', 1, 0, ?)",
                       [("m1", "demo/a", None), ("m2", "demo/a", None), ("m3", "demo/b", 5)])
        db.commit()
        db.close()
        result = self.run_add(repo, "--grok-name", "Beta", "--relay-name", "beta")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn(m("install.name_class_left"), result.stdout)
        self.assertIn(m("install.name_unread_held", 2, "demo/beta"), result.stdout)
        self.assertNotIn(m("install.name_unread", 2, "demo/beta"), result.stdout)
        self.assertIn("  demo/a 2", result.stdout)
        data = self.record()
        data["removed_bots"] = ["beta"]
        self.write_record(data)
        result = self.run_add(repo, "--grok-name", "Beta", "--relay-name", "beta")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn(m("install.name_class_removed"), result.stdout)
        self.assertIn(m("install.name_unread", 2, "demo/beta"), result.stdout)
        self.assertNotIn(m("install.name_unread_held", 2, "demo/beta"), result.stdout)

    def test_the_default_address_skips_names_in_use(self):
        repo = self.two_ready()
        cases = [("Beta", (), "demo/beta"), ("Beta", ("beta-cloud",), "demo/bot-2"),
                 ("小助手", (), "demo/bot-2"), ("小助手", ("bot-2-inbox", "bot-3"), "demo/bot-4")]
        for grok, taken, want in cases:
            (self.who / "all.json").write_text(json.dumps(self.cloud_listing(*taken, machine="laptop")))
            result = self.run_add(repo, "--grok-name", grok)
            self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
            self.assertIn(m("install.add_plan", want, grok, "demo/alpha"), result.stdout, (grok, taken))
        self.assertNotIn("bots", self.record())


class TwoBots(AddSupport, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.repo_dir = self.two_ready()
        self.added(self.repo_dir)

    def run_in(self, *args, timeout=60):
        return self.run_script("install.sh", *args, root=self.repo_dir, timeout=timeout)

    def test_per_bot_modes_need_a_relay_name(self):
        for args in (["--webhook"], ["--cloud-prompt"], ["--purge-copies"], ["--wake-bot"], ["--grok-name", "X"]):
            result = self.run_in(*args)
            self.assertEqual(result.returncode, 1, args)
            self.assertIn(m("install.which_bot"), result.stderr, args)
            self.assertIn("demo/alpha", result.stderr, args)
            self.assertIn("demo/beta → Grok: Beta", result.stderr, args)
        result = self.run_in("--webhook", "--relay-name", "gamma")
        self.assertEqual(result.returncode, 1)
        self.assertIn(m("install.not_a_bot", "gamma"), result.stderr)
        result = self.run_in("--name", "demo", "--relay-name", "gamma", "--yes")
        self.assertEqual(result.returncode, 1)
        self.assertIn(m("install.not_a_bot", "gamma"), result.stderr)
        self.assertEqual(self.service_changes(), [])

    def test_restart_covers_every_bot_and_refuses_a_name(self):
        result = self.run_in("--restart-relay", "--relay-name", "beta")
        self.assertEqual(result.returncode, 1)
        self.assertIn(m("install.all_bots_no_name"), result.stderr)
        for args in (["--keep-rollback"], ["--webhook", "--keep-rollback"]):
            result = self.run_in(*args)
            self.assertEqual(result.returncode, 2, args)
            self.assertIn(m("install.keep_rollback_alone"), result.stderr)
        result = self.run_in("--undo-relay", "--relay-name", "beta")
        self.assertEqual(result.returncode, 1)
        self.assertIn(m("install.all_bots_no_name"), result.stderr)
        changes = len(self.service_changes())
        result = self.run_in("--undo-relay")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.service_changes()), changes)
        before = len(self.multi_started())
        result = self.run_in("--restart-relay")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([label for label, _ in self.multi_started()[before:]], [LEGACY_LABEL, f"{LEGACY_LABEL}.beta"])
        self.assertFalse((self.home / ".local/lib/agent-mesh/rollback").exists())
        self.assert_no_fd9()

    def test_per_bot_modes_work_on_the_named_bot(self):
        result = self.run_in("--cloud-prompt", "--relay-name", "beta")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.posts()), 2)
        self.assertNotIn("rn invite", self.calls())
        result = self.run_in("--grok-name", "Beta Two", "--relay-name", "beta")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.bot()["grok_name"], "Beta Two")
        self.assertNotEqual(self.record().get("grok_name"), "Beta Two")
        template = (self.home / ".local/lib/agent-mesh/templates/AGENTS.reason-network.md").read_text()
        self.assertIn("(called “Beta Two” in Grok)", template)
        copies = {}
        for name, state in (("alpha", "agent-relay"), ("beta", "agent-relay-bots/beta")):
            folder = self.home / "Library/Application Support" / state / "fwd-copies"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "x.json").write_text("{}")
            copies[name] = folder / "x.json"
        result = self.run_in("--purge-copies", "--relay-name", "beta")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(copies["beta"].exists())
        self.assertTrue(copies["alpha"].exists())
        result = self.run_in("--name", "demo", "--relay-name", "beta", "--yes")
        self.assertEqual(result.returncode, 1)
        self.assertIn(m("install.full_added", "beta", "beta"), result.stderr)
        result = self.run_in("--wake-bot", "--relay-name", "alpha")
        self.assertEqual(result.returncode, 1)
        self.assertIn(m("install.wake_legacy"), result.stderr)
        data = self.record()
        data["removing"] = {"name": "beta", "kind": "added", "step": "stopped"}
        self.write_record(data)
        for args in (["--webhook"], ["--purge-copies"], ["--wake-bot"]):
            result = self.run_in(*args, "--relay-name", "beta")
            self.assertEqual(result.returncode, 1, args)
            self.assertIn(m("install.bot_removing", "beta", "beta"), result.stderr, args)
        template = (self.home / ".local/lib/agent-mesh/templates/AGENTS.reason-network.md")
        result = self.run_in("--grok-name", "Alpha", "--relay-name", "alpha")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("demo/beta", template.read_text())
        self.assertIn("(called “Alpha” in Grok).", template.read_text())
        self.assertEqual(self.service_changes(), [])

    def test_webhook_for_an_added_bot_restarts_only_that_relay(self):
        before = self.multi_loaded()
        result = self.run_staged(self.repo_dir, "--webhook", "--relay-name", "beta",
                                 steps=[("Webhook URL:", HOOK + "2"), ("(not echoed): ", KEY + "2")])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("WEBHOOK_URL=" + HOOK + "2", (self.home / ".config/agent-relay-bots/beta/env").read_text())
        after = self.multi_loaded()
        self.assertEqual(after[LEGACY_LABEL], before[LEGACY_LABEL])
        self.assertNotEqual(after[f"{LEGACY_LABEL}.beta"]["pid"], before[f"{LEGACY_LABEL}.beta"]["pid"])
        self.assertEqual(self.legacy_touched(), [])


class SingleBot(AddSupport, unittest.TestCase):
    def test_a_new_name_without_add_bot_is_refused_with_a_hint(self):
        repo = self.two_ready()
        result = self.run_script("install.sh", "--name", "demo", "--relay-name", "other", "--yes", root=repo)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("install.rename_refused", "alpha", "other", "other"), result.stderr)
        self.assertIn(m("install.add_hint"), result.stderr)
        result = self.run_script("install.sh", "--webhook", "--relay-name", "other", root=repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn(m("install.add_hint"), result.stderr)
        result = self.run_script("install.sh", "--wake-bot", root=repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn(m("install.wake_legacy"), result.stderr)


if __name__ == "__main__":
    unittest.main()
