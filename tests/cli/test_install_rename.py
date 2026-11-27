import json
import re
import time
import unittest

from tests.cli.install_support import EMPTY_DIRECTORY, reshape_bot_lines
from tests.cli.test_install_add import LEGACY_LABEL, AddSupport, m

REQ = re.compile(r"[0-9a-f]{16}")


class RenameSupport(AddSupport):
    def setUp(self):
        super().setUp()
        self.ps = self.base / "ps.txt"
        self.ps.write_text("")
        self.env.update(MOCK_PS=str(self.ps), AGENT_MESH_RENAME_REPLY_WAIT="10")

    def accepts(self, slot, old, new, held=0, extra=""):
        self.bot_replies(body=f"Done.\nagent-mesh-result request {{req}}\n"
                              f"agent-mesh-result rename {{req}} pending {slot} {old} {new} held {held}\n"
                              f"agent-mesh-result lang en\n{extra}agent-mesh-result DONE rename\n", sender="{to}")

    def rename(self, repo, *args):
        return self.run_script("install.sh", "--rename", *args, root=repo)

    def rename_tty(self, repo, *args, answer="y"):
        return self.run_staged(repo, "--rename", *args, steps=[("[y/N] ", answer)])

    def listing(self, name, mac_status="offline", since=None, cloud_status="online", cloud=True):
        rows = [{"person": "demo", "machine": "mac", "name": name, "status": mac_status, "since": since or 1}]
        if cloud:
            rows += [{"person": "demo", "machine": "cloud", "name": f"{name}-cloud", "status": cloud_status, "since": 1},
                     {"person": "demo", "machine": "cloud", "name": f"{name}-inbox", "status": cloud_status, "since": 1}]
        (self.who / "all.json").write_text(json.dumps(dict(EMPTY_DIRECTORY, sessions=rows)))

    def env_lines(self, path=None):
        return (path or self.env_file()).read_text().splitlines()

    def former_env(self, path=None):
        return [line for line in self.env_lines(path) if line.startswith("FORMER_NAMES=")]

    def marker_name(self, slot=None):
        base = self.home / "Library/Application Support"
        path = base / ("agent-relay" if slot is None else f"agent-relay-bots/{slot}") / "registered"
        return json.loads(path.read_text())["name"]

    def assert_until(self, value, name):
        found, until = value.split(":")
        self.assertEqual(found, name)
        self.assertAlmostEqual(int(until), int(time.time()) + 30 * 86400, delta=300)


class FirstBot(RenameSupport, unittest.TestCase):
    def test_the_first_bot_takes_a_new_address_and_keeps_the_old_one_for_thirty_days(self):
        repo = self.two_ready()
        self.accepts("-", "alpha", "gamma")
        result = self.rename(repo, "gamma", "--yes")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        [sent] = self.sent()
        self.assertEqual(sent["to"], "demo/alpha")
        self.assertRegex(sent["body"], r"^agent-mesh rename - alpha gamma [0-9a-f]{16}$")
        self.assertIn(m("install.rename_plan", "demo/alpha", "demo/gamma"), out)
        self.assertIn(m("install.name_class_new"), out)
        self.assertIn(m("install.rename_done", "demo/alpha", "demo/gamma"), out)
        lines = self.env_lines()
        for line in ("LISTEN_NAME=gamma", "CLOUD_NAME=gamma-cloud", "INBOX_NAME=gamma-inbox",
                     "WEBHOOK_URL=https://example.invalid/hook", "WEBHOOK_KEY=0123456789"):
            self.assertIn(line, lines)
        [former] = self.former_env()
        self.assert_until(former.split("=", 1)[1], "alpha")
        self.assertEqual(self.env_file().stat().st_mode & 0o777, 0o600)
        data = self.record()
        self.assertEqual(data["relay_name"], "gamma")
        self.assertNotIn("rename", data)
        [item] = data["former"]
        self.assertEqual((item["name"], item["slot"]), ("alpha", "-"))
        self.assertEqual(item["until_ms"] - item["since_ms"], 30 * 86400 * 1000)
        self.assertFalse((self.home / ".local/lib/agent-mesh/rename-backup").exists())
        self.assertIn(LEGACY_LABEL, self.multi_loaded())
        self.assertEqual(self.marker_name(), "demo/gamma")
        template = (self.home / ".local/lib/agent-mesh/templates/AGENTS.reason-network.md").read_text()
        self.assertIn("`demo/gamma`", template)
        self.assertNotIn("`demo/alpha`", template)
        former = self.state_cmd("former", root=repo).stdout.split()
        self.assertEqual(former[:4], ["former", "-", "alpha", "live"])
        self.assertEqual(former[5], "gamma")

        self.listing("alpha")
        self.accepts("-", "gamma", "alpha")
        result = self.rename(repo, "alpha", "--yes")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn(m("install.name_class_own"), out)
        self.assertEqual(self.sent()[-1]["to"], "demo/gamma")
        self.assertRegex(self.sent()[-1]["body"], r"^agent-mesh rename - gamma alpha [0-9a-f]{16}$")
        data = self.record()
        self.assertEqual(data["relay_name"], "alpha")
        self.assertEqual([f["name"] for f in data["former"]], ["gamma"])
        [former] = self.former_env()
        self.assert_until(former.split("=", 1)[1], "gamma")
        self.assertIn("LISTEN_NAME=alpha", self.env_lines())
        self.assertEqual(self.marker_name(), "demo/alpha")
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertRegex(plan.stdout, r"routine prompt\s+unchanged")
        self.assertRegex(plan.stdout, r"address name\s+ok\s+alpha")

    def test_a_new_name_needs_yes_or_a_terminal_and_the_same_name_is_refused(self):
        repo = self.two_ready()
        self.accepts("-", "alpha", "gamma")
        before = (self.env_file().read_bytes(), self.record())
        result = self.rename(repo, "gamma")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn(m("install.rename_no_tty", "agent-mesh rename gamma --yes"), result.stdout)
        result = self.rename_tty(repo, "gamma", answer="n")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("install.rename_stopped"), result.stderr)
        result = self.rename(repo, "alpha", "--yes")
        self.assertEqual(result.returncode, 1)
        self.assertIn(m("install.rename_same", "demo/alpha"), result.stderr)
        result = self.rename(repo, "Gamma", "--yes")
        self.assertEqual(result.returncode, 1)
        result = self.rename(repo, "--yes")
        self.assertEqual(result.returncode, 1)
        self.assertIn(m("install.rename_need_name"), result.stderr)
        result = self.run_script("install.sh", "--rename", "gamma", "--webhook", root=repo)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        result = self.run_script("install.sh", "--cancel", root=repo)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual((self.env_file().read_bytes(), self.record()), before)
        self.assertEqual(self.sent(), [])
        result = self.rename_tty(repo, "gamma")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.record()["relay_name"], "gamma")

    def test_a_name_in_use_is_refused_before_anything_is_sent(self):
        repo = self.two_ready()
        (self.who / "all.json").write_text(json.dumps(self.cloud_listing("gamma-inbox", machine="laptop")))
        result = self.rename(repo, "gamma", "--yes")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("install.rename_name_refused", "demo/gamma"), result.stderr)
        self.assertNotIn("rename", self.record())
        self.assertEqual(self.sent(), [])

    def test_a_silent_bot_leaves_the_rename_open_and_a_rerun_resends_the_same_request(self):
        repo = self.two_ready()
        self.env["AGENT_MESH_RENAME_REPLY_WAIT"] = "0"
        before = self.env_file().read_bytes()
        result = self.rename(repo, "gamma", "--yes")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn(m("install.rename_reply_timeout", "demo/alpha"), result.stdout)
        self.assertEqual(self.record()["rename"]["stage"], "requested")
        self.assertEqual(self.env_file().read_bytes(), before)
        result = self.run_script("install.sh", "--webhook", root=repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn(m("install.rename_busy"), result.stderr)
        result = self.rename(repo, "delta", "--yes")
        self.assertEqual(result.returncode, 1)
        self.assertIn(m("install.rename_other", "demo/alpha", "demo/gamma"), result.stderr)
        self.env["AGENT_MESH_RENAME_REPLY_WAIT"] = "10"
        self.accepts("-", "alpha", "gamma")
        result = self.rename(repo)
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn(m("install.rename_resume", "demo/alpha", "demo/gamma"), out)
        first, second = self.sent()
        self.assertEqual(first["body"], second["body"])
        self.assertEqual(self.record()["relay_name"], "gamma")

    def test_a_refusal_drops_the_rename_and_changes_nothing(self):
        repo = self.two_ready()
        self.bot_replies(body="agent-mesh-result request {req}\nSTOP: the cloud is busy\n"
                              "agent-mesh-result lang en\nagent-mesh-result DONE rename\n", sender="{to}")
        before = self.env_file().read_bytes()
        result = self.rename(repo, "gamma", "--yes")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertIn(m("state.rename_reply_mismatch", "demo/alpha"), out)
        self.assertIn("STOP: the cloud is busy", out)
        self.assertIn(m("install.rename_cloud_refused"), out)
        data = self.record()
        self.assertNotIn("rename", data)
        self.assertEqual(data["relay_name"], "alpha")
        self.assertEqual([(f["name"], f["slot"], f["reserved"]) for f in data["former"]], [("gamma", "-", True)])
        self.assertEqual(self.former_env(), [])
        self.assertEqual(self.env_file().read_bytes(), before)
        self.assert_retry_reaches_the_pending_name(repo)

    def test_a_name_the_cloud_used_before_is_confirmed_and_claimed_on_the_next_run(self):
        repo = self.two_ready()
        self.bot_replies(body="agent-mesh-result request {req}\nSTOP: gamma is an earlier address name on this cloud; "
                              "the Mac must confirm taking it back (nothing was changed)\n"
                              "agent-mesh-result lang en\nagent-mesh-result DONE rename\n", sender="{to}")
        result = self.rename(repo, "gamma", "--yes")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertIn(m("state.rename_claim_again"), out)
        self.assertNotIn("reclaim=1", self.sent()[-1]["body"])
        self.assertEqual([(f["name"], f["reserved"], f["claim"]) for f in self.record()["former"]],
                         [("gamma", True, True)])
        result = self.rename(repo, "gamma", "--yes")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn(m("install.name_class_claim"), result.stdout)
        self.assertIn(m("names.evidence_absent", "demo/gamma-inbox"), result.stdout)
        self.assertEqual(len(self.sent()), 1)
        self.accepts("-", "alpha", "gamma")
        result = self.rename_tty(repo, "gamma")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertTrue(self.sent()[-1]["body"].endswith("\nreclaim=1"))
        data = self.record()
        self.assertEqual(data["relay_name"], "gamma")
        self.assertEqual([f["name"] for f in data["former"]], ["alpha"])

        self.bot_replies(body="agent-mesh-result request {req}\nSTOP: alpha is an earlier address name on this cloud; "
                              "the Mac must confirm taking it back (nothing was changed)\n"
                              "agent-mesh-result lang en\nagent-mesh-result DONE rename\n", sender="{to}")
        self.listing("alpha", cloud=False)
        result = self.rename(repo, "alpha", "--yes")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertIn(m("state.rename_claim_again"), out)
        self.assertEqual([(f["name"], f.get("claim")) for f in self.record()["former"]], [("alpha", True)])
        self.accepts("-", "gamma", "alpha")
        result = self.rename_tty(repo, "alpha")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn(m("install.name_class_claim"), out)
        self.assertTrue(self.sent()[-1]["body"].endswith("\nreclaim=1"))
        self.assertEqual(self.record()["relay_name"], "alpha")
        self.assertNotIn("claim", self.record()["former"][0])

    def test_other_refusals_print_no_claim_hint(self):
        repo = self.two_ready()
        self.bot_replies(body="agent-mesh-result request {req}\nSTOP: gamma is an address name of beta on this cloud; "
                              "choose another name (nothing was changed)\n"
                              "agent-mesh-result lang en\nagent-mesh-result DONE rename\n", sender="{to}")
        result = self.rename(repo, "gamma", "--yes")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn(m("state.rename_claim_again"), result.stdout + result.stderr)
        self.assertNotIn("claim", self.record()["former"][0])

    def assert_retry_reaches_the_pending_name(self, repo):
        self.assertEqual(self.state_cmd("former", root=repo).stdout.strip(), "")
        (self.who / "all.json").write_text(json.dumps(self.cloud_listing("gamma-cloud", "gamma-inbox")))
        self.env["AGENT_MESH_RENAME_REPLY_WAIT"] = "10"
        self.accepts("-", "alpha", "gamma")
        result = self.rename(repo, "gamma", "--yes")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn(m("install.name_class_own"), out)
        self.assertNotIn("reclaim=1", self.sent()[-1]["body"])
        data = self.record()
        self.assertEqual(data["relay_name"], "gamma")
        self.assertEqual([(f["name"], f.get("reserved")) for f in data["former"]], [("alpha", None)])
        [former] = self.former_env()
        self.assert_until(former.split("=", 1)[1], "alpha")

    def test_another_bot_cannot_take_a_name_held_back_for_a_cancelled_rename(self):
        repo = self.two_ready()
        self.added(repo)
        self.env["AGENT_MESH_RENAME_REPLY_WAIT"] = "0"
        self.assertEqual(self.rename(repo, "gamma", "--relay-name", "alpha", "--yes").returncode, 3)
        self.assertEqual(self.rename(repo, "--cancel", "--relay-name", "alpha").returncode, 0)
        result = self.rename(repo, "gamma", "--relay-name", "beta", "--yes")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("names.other_slot", "gamma", "alpha"), result.stderr)

    def test_cancel_before_the_switch_only_drops_the_record(self):
        repo = self.two_ready()
        result = self.rename(repo, "--cancel")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(m("install.rename_none"), result.stdout)
        self.env["AGENT_MESH_RENAME_REPLY_WAIT"] = "0"
        self.assertEqual(self.rename(repo, "gamma", "--yes").returncode, 3)
        result = self.rename(repo, "--cancel")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(m("install.rename_cancelled", "demo/alpha", "demo/gamma"), result.stdout)
        data = self.record()
        self.assertNotIn("rename", data)
        self.assertEqual([(f["name"], f["reserved"]) for f in data["former"]], [("gamma", True)])
        self.assertEqual(len(self.sent()), 1)
        check = self.run_script("check.sh", root=repo)
        self.assertNotIn("demo/gamma", check.stdout + check.stderr)
        self.assert_retry_reaches_the_pending_name(repo)

    def test_a_template_that_cannot_be_written_keeps_the_rename_open_until_a_rerun(self):
        repo = self.two_ready()
        self.accepts("-", "alpha", "gamma")
        template = self.home / ".local/lib/agent-mesh/templates/AGENTS.reason-network.md"
        template.unlink(missing_ok=True)
        template.mkdir(parents=True)
        result = self.rename(repo, "gamma", "--yes")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertIn(m("install.rename_reply_failed", self.home / ".local/lib/agent-mesh/templates"), out)
        self.assertNotIn(m("install.rename_done", "demo/alpha", "demo/gamma"), out)
        data = self.record()
        self.assertEqual(data["rename"]["stage"], "launched")
        self.assertEqual(data["relay_name"], "gamma")
        self.assertTrue((self.home / ".local/lib/agent-mesh/rename-backup").exists())
        template.rmdir()
        result = self.rename(repo)
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn(m("install.rename_done", "demo/alpha", "demo/gamma"), out)
        self.assertIn("`demo/gamma`", template.read_text())
        self.assertNotIn("rename", self.record())
        self.assertEqual(len(self.sent()), 1)
        self.assertEqual([f["name"] for f in self.record()["former"]], ["alpha"])

    def test_a_relay_that_does_not_stop_can_be_cancelled_back_to_the_old_name(self):
        repo = self.two_ready()
        self.accepts("-", "alpha", "gamma")
        env, record = self.env_file().read_bytes(), self.record()
        (self.state / f"stuck.{LEGACY_LABEL}").write_text("")
        result = self.rename(repo, "gamma", "--yes")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertIn(m("install.rename_unload_failed", f"gui/501/{LEGACY_LABEL}"), out)
        self.assertEqual(self.record()["rename"]["stage"], "switching")
        self.assertTrue((self.home / ".local/lib/agent-mesh/rename-backup/SHA256SUMS").is_file())
        result = self.rename(repo, "--cancel")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn(m("install.rename_cancelled", "demo/alpha", "demo/gamma"), out)
        self.assertEqual(self.env_file().read_bytes(), env)
        after = self.record()
        self.assertNotIn("rename", after)
        self.assertEqual(after["relay_name"], record["relay_name"])
        self.assertEqual([(f["name"], f["reserved"]) for f in after["former"]], [("gamma", True)])
        self.assertEqual(self.former_env(), [])
        self.assertEqual(self.marker_name(), "demo/alpha")
        self.assertIn(LEGACY_LABEL, self.multi_loaded())
        self.assertFalse((self.home / ".local/lib/agent-mesh/rename-backup").exists())

    def test_a_relay_that_does_not_start_finishes_on_the_next_run_and_cannot_be_cancelled(self):
        repo = self.two_ready()
        self.accepts("-", "alpha", "gamma")
        (self.state / f"fail-bootstrap.{LEGACY_LABEL}").write_text("")
        result = self.rename(repo, "gamma", "--yes")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, out)
        self.assertIn(m("install.rename_start_failed", "demo/gamma"), out)
        self.assertEqual(self.record()["rename"]["stage"], "launched")
        self.assertIn("LISTEN_NAME=gamma", self.env_lines())
        result = self.rename(repo, "--cancel")
        self.assertEqual(result.returncode, 1)
        self.assertIn(m("install.rename_cancel_launched", "demo/gamma"), result.stderr)
        for script, key in (("update.sh", "update.rename_busy"), ("install.sh", "install.rename_busy")):
            args = ("--no-pull", "--yes") if script == "update.sh" else ("--restart-relay",)
            result = self.run_script(script, *args, root=repo)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn(m(key), result.stderr)
        result = self.rename(repo)
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn(m("install.rename_done", "demo/alpha", "demo/gamma"), out)
        self.assertEqual(len(self.sent()), 1)
        self.assertEqual(self.record()["relay_name"], "gamma")
        self.assertEqual(self.marker_name(), "demo/gamma")

    def test_a_removed_name_needs_a_terminal_and_is_reclaimed_with_its_set_aside_messages(self):
        repo = self.two_ready()
        data = self.record()
        data["removed_bots"] = ["gone", "other"]
        self.write_record(data)
        self.listing("gone", since=int(time.time() * 1000) - 10 * 86400 * 1000)
        self.sessions([("gone", "cli:gone", 1)])
        result = self.rename(repo, "gone", "--yes")
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn(m("install.rename_need_tty", "agent-mesh rename gone"), result.stdout)
        self.assertIn(m("install.name_class_removed"), result.stdout)
        self.assertEqual(self.sent(), [])
        self.accepts("-", "alpha", "gone", held=4)
        result = self.rename_tty(repo, "gone")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        [sent] = self.sent()
        self.assertRegex(sent["body"], r"^agent-mesh rename - alpha gone [0-9a-f]{16}\nreclaim=1$")
        req = sent["body"].split()[5]
        hint = "agent-mesh rename --accept-history"
        self.assertIn(m("state.held_note", 4, hint), out)
        data = self.record()
        self.assertEqual(data["removed_bots"], ["other"])
        self.assertEqual(data["held"], {"-": {req: {"count": 4}}})

        self.bot_replies(body="agent-mesh-result request {req}\nagent-mesh-result history {req} accepted "
                              + req + " 4\nagent-mesh-result lang en\nagent-mesh-result DONE history\n",
                         sender="{to}")
        result = self.run_script("install.sh", "--rename", "--accept-history", root=repo)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn(m("install.history_plan", 4, "demo/gone"), result.stdout)
        result = self.run_script("install.sh", "--rename", "--accept-history", "--yes", root=repo)
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertEqual(self.sent()[-1]["to"], "demo/gone")
        self.assertRegex(self.sent()[-1]["body"], rf"^agent-mesh history - accept {req} [0-9a-f]{{16}}$")
        self.assertIn(m("state.history_accepted", 4, "demo/gone"), out)
        self.assertNotIn("held", self.record())
        result = self.run_script("install.sh", "--rename", "--accept-history", "--yes", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(m("install.history_none", "demo/gone"), result.stdout)

    def test_two_taken_back_names_keep_both_batches_until_each_is_accepted(self):
        repo = self.two_ready()
        data = self.record()
        data["removed_bots"] = ["gone", "lost"]
        self.write_record(data)
        reqs = []
        for old, name, count in (("alpha", "gone", 4), ("gone", "lost", 3)):
            self.listing(name, since=int(time.time() * 1000) - 10 * 86400 * 1000)
            self.sessions([(name, f"cli:{name}", 1)])
            self.accepts("-", old, name, held=count)
            result = self.rename_tty(repo, name)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            reqs.append(self.sent()[-1]["body"].split()[5])
        self.assertEqual(self.record()["held"], {"-": {reqs[0]: {"count": 4}, reqs[1]: {"count": 3}}})
        listed = self.state_cmd("held", "--slot", "-", root=repo)
        self.assertEqual(listed.stdout.splitlines(), [f"claim {reqs[0]} 4", f"claim {reqs[1]} 3"])

        self.bot_replies(body="agent-mesh-result request {req}\nagent-mesh-result history {req} accepted {claim} 5\n"
                              "agent-mesh-result lang en\nagent-mesh-result DONE history\n", sender="{to}")
        result = self.run_script("install.sh", "--rename", "--accept-history", "--yes", root=repo)
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn(m("install.history_plan", 7, "demo/lost"), out)
        sent = [s for s in self.sent() if s["body"].startswith("agent-mesh history")]
        self.assertEqual([s["to"] for s in sent], ["demo/lost", "demo/lost"])
        self.assertEqual([s["body"].split()[4] for s in sent], reqs)
        self.assertEqual(out.count(m("state.history_accepted", 5, "demo/lost")), 2)
        self.assertNotIn("held", self.record())

    def test_a_left_name_must_be_offline_and_quiet_on_this_mac(self):
        repo = self.two_ready()
        self.listing("old", mac_status="online")
        self.sessions([("old", "cli:old", 1)])
        result = self.rename_tty(repo, "old")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("names.reclaim_online", "demo/old", "online"), result.stderr)
        self.assertNotIn("demo/old-cloud", result.stderr)
        self.listing("old", since=int(time.time() * 1000) - 3600 * 1000, cloud=False)
        self.ps.write_text("777 1 /opt/reason-network listen --as old-inbox\n")
        result = self.rename_tty(repo, "old")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("777", result.stderr)
        self.assertNotIn("demo/old-cloud", result.stderr)
        self.ps.write_text("")
        self.accepts("-", "alpha", "old")
        result = self.rename_tty(repo, "old")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn(m("install.name_class_left"), out)
        for name in ("demo/old-cloud", "demo/old-inbox"):
            self.assertIn(m("names.evidence_absent", name), out)
        self.assertTrue(self.sent()[-1]["body"].endswith("\nreclaim=1"))
        self.assertEqual(self.record()["relay_name"], "old")

    def test_names_come_back_when_their_cloud_sessions_are_out_of_sight(self):
        repo = self.two_ready()
        self.accepts("-", "alpha", "gamma")
        result = self.rename(repo, "gamma", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for old, new in (("gamma", "alpha"), ("alpha", "gamma")):
            self.listing(new, cloud=False)
            self.sessions([(new, f"cli:{new}", 1)])
            self.accepts("-", old, new)
            result = self.rename(repo, new, "--yes")
            out = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, out)
            self.assertIn(m("install.name_class_own"), out)
            self.assertIn(m("names.evidence_absent", f"demo/{new}-cloud"), out)
            self.assertNotIn("reclaim", self.sent()[-1]["body"])
            self.assertEqual(self.record()["relay_name"], new)
        data = self.record()
        data["removed_bots"] = ["gone"]
        self.write_record(data)
        (self.who / "all.json").write_text(json.dumps(EMPTY_DIRECTORY))
        self.accepts("-", "gamma", "gone")
        result = self.rename_tty(repo, "gone")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn(m("install.name_class_removed"), out)
        self.assertIn(m("names.evidence_absent", "demo/gone-inbox"), out)
        self.assertTrue(self.sent()[-1]["body"].endswith("\nreclaim=1"))
        self.assertEqual(self.record()["relay_name"], "gone")


class AddedBot(RenameSupport, unittest.TestCase):
    def test_an_added_bot_keeps_its_slot_and_takes_back_its_first_name(self):
        repo = self.two_ready()
        self.added(repo)
        plist, legacy = self.plist("beta").read_bytes(), self.legacy_plist().read_bytes()
        legacy_pid = self.multi_loaded()[LEGACY_LABEL]["pid"]
        result = self.rename(repo, "delta", "--yes")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("install.which_bot"), result.stderr)
        self.accepts("beta", "beta", "delta")
        result = self.rename(repo, "delta", "--relay-name", "beta", "--yes")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        [sent] = [s for s in self.sent() if s["body"].startswith("agent-mesh rename")]
        self.assertEqual(sent["to"], "demo/beta")
        self.assertRegex(sent["body"], r"^agent-mesh rename beta beta delta [0-9a-f]{16}$")
        env = self.home / ".config/agent-relay-bots/beta/env"
        self.assertIn("LISTEN_NAME=delta", self.env_lines(env))
        [former] = self.former_env(env)
        self.assert_until(former.split("=", 1)[1], "beta")
        self.assertEqual(self.former_env(), [])
        bot = self.bot()
        self.assertEqual((bot["name"], bot["stage"]), ("delta", "relay"))
        self.assertEqual(self.record()["relay_name"], "alpha")
        self.assertEqual(self.marker_name("beta"), "demo/delta")
        self.assertEqual(self.legacy_plist().read_bytes(), legacy)
        self.assertEqual(self.multi_loaded()[LEGACY_LABEL]["pid"], legacy_pid)
        self.assertEqual(self.legacy_touched(), [])
        self.assertEqual(self.plist("beta").read_bytes(), plist)
        template = (self.home / ".local/lib/agent-mesh/templates/AGENTS.reason-network.md").read_text()
        self.assertIn("  - `demo/delta` (called “Beta” in Grok)", template)
        listed = self.run_script("install.sh", "--cloud-prompt", "--relay-name", "delta", root=repo)
        self.assertEqual(listed.returncode, 0, listed.stdout + listed.stderr)
        self.assertIn("demo/delta", listed.stdout)
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertIn("    alpha: relay unchanged, prompt unchanged\n    delta: relay unchanged, prompt unchanged\n",
                      plan.stdout)

        self.listing("beta")
        self.accepts("beta", "delta", "beta")
        result = self.rename(repo, "beta", "--relay-name", "delta", "--yes")
        out = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, out)
        self.assertIn(m("install.name_class_own"), out)
        self.assertNotIn("name", self.bot())
        self.assertEqual([f["name"] for f in self.record()["former"]], ["delta"])
        self.assertEqual(self.marker_name("beta"), "demo/beta")
        self.assertEqual(self.legacy_touched(), [])

    def test_renaming_reads_the_status_lines_by_key_name(self):
        repo = self.two_ready()
        self.added(repo)
        rename = lambda: self.rename(repo, "gamma", "--relay-name", "alpha")
        sent = self.sent()
        base = rename()
        self.assertEqual(base.returncode, 3, base.stdout + base.stderr)
        for spec in ({"*": "reorder"}, {"*": "drop record"}, {"beta": "set address nova"}):
            reshape_bot_lines(repo, spec)
            result = rename()
            self.assertEqual((result.returncode, result.stdout), (3, base.stdout), (spec, result.stderr))
        data = self.record()
        data["removing"] = {"name": "beta", "kind": "added", "step": "stopped"}
        self.write_record(data)
        for spec, shown in ((None, "beta"), ({"*": "reorder"}, "beta"), ({"beta": "set address nova"}, "nova")):
            reshape_bot_lines(repo, spec)
            result = self.rename(repo, "gamma", "--relay-name", "alpha", "--yes")
            self.assertEqual(result.returncode, 1, (spec, result.stdout + result.stderr))
            self.assertIn(m("install.bot_removing", shown, shown), result.stderr, spec)
        del data["removing"]
        self.write_record(data)
        for spec in ({"beta": "drop address"}, {"beta": "drop removing"}, {"beta": "dangling"}):
            reshape_bot_lines(repo, spec)
            result = self.rename(repo, "gamma", "--relay-name", "alpha", "--yes")
            self.assertEqual(result.returncode, 1, (spec, result.stdout + result.stderr))
            self.assertIn(m("install.rename_status_failed"), result.stderr, spec)
            self.assertNotIn("rename", self.record(), spec)
        self.assertEqual(self.sent(), sent)

    def test_a_name_of_another_bot_is_refused(self):
        repo = self.two_ready()
        self.added(repo)
        result = self.rename(repo, "alpha", "--relay-name", "beta", "--yes")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("names.other_slot", "alpha", "alpha"), result.stderr)
        self.assertNotIn("rename", self.record())


if __name__ == "__main__":
    unittest.main()
