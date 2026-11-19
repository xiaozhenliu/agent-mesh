import json
import os
import shutil
import signal
import subprocess
import sys
import time
import unittest

import messages
from render_prompt import CLOUD_FILES, ROOT
from tests.cli.install_support import (
    BASE_PYTHON,
    InstallHarness,
    fake_python,
    rn_engine,
)
from tests.cli.test_install_add import LEGACY_LABEL, TEAM_HASH, AddSupport, m


class Update(InstallHarness, unittest.TestCase):
    def test_deploy_state_refuses_confirmations_that_no_longer_match(self):
        repo = self.installed(record=False)
        state = [sys.executable, str(repo / "scripts/deploy_state.py")]
        run = lambda *args: subprocess.run(state + list(args), env=self.env, capture_output=True, text=True)
        self.assertNotEqual(run("mark", "prompt", "confirmed", "--expect", "0" * 64).returncode, 0)
        self.assertNotEqual(run("mark", "cloud", "confirmed", "--if-pending").returncode, 0)
        self.assertIsNone(self.record())
        self.assertEqual(run("mark", "cloud", "pending").returncode, 0)
        with open(repo / "box/rn-fetch.py", "a") as stream:
            stream.write("\n")
        self.assertNotEqual(run("mark", "cloud", "confirmed", "--if-pending").returncode, 0)
        self.assertEqual(self.record()["cloud"]["state"], "pending")
        shown = run("digest", "cloud").stdout.strip()
        self.assertEqual(run("mark", "cloud", "confirmed", "--expect", shown).returncode, 0)
        self.assertEqual(self.record()["cloud"], {"hash": shown, "state": "confirmed"})

    def test_update_ignores_a_damaged_unfinished_update_record(self):
        repo = self.installed()
        data = self.record()
        data["txn"] = {"stage": "routine_paused", "need": ["prompt"], "lang": "en", "commit": "x", "target": {}}
        self.write_record(data)
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("already up to date", result.stdout)
        self.assertIn("damaged and was ignored", result.stderr)
        self.assertEqual(self.service_changes(), [])

    def test_installer_records_cloud_and_prompt_as_pending_only(self):
        self.configured()
        self.assertEqual(self.run_script("install.sh", "--cloud-prompt").returncode, 0)
        data = self.record()
        self.assertEqual((data["cloud"]["state"], data["cloud_setup"]["state"]), ("pending", "pending"))
        self.assertNotIn("prompt", data)
        result = self.run_script("install.sh", "--webhook", "--lang", "zh-CN")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.record()
        self.assertEqual((data["prompt"]["state"], data["lang"]), ("pending", "zh-CN"))
        self.mark(ROOT, "cloud", "confirmed")
        self.mark(ROOT, "prompt", "confirmed", "zh-CN")
        self.assertEqual(self.run_script("install.sh", "--cloud-prompt").returncode, 0)
        self.assertEqual(self.run_script("install.sh", "--webhook", "--lang", "zh-CN").returncode, 0)
        data = self.record()
        self.assertEqual((data["cloud"]["state"], data["prompt"]["state"]), ("pending", "pending"))
        result = self.run_script("uninstall.sh", "--level", "program", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(any(path.exists() for path in self.relay_files()))
        self.assertEqual(self.record()["prompt"]["state"], "pending")

    def test_update_reports_up_to_date_without_touching_anything(self):
        repo = self.installed()
        for args in (("--no-pull",), ("--no-pull", "--plan")):
            result = self.run_script("update.sh", *args, root=repo)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("already up to date" if len(args) == 1 else "== plan", result.stdout)
        self.assertEqual(self.service_changes(), [])
        self.assertNotIn("rn setup", self.calls())
        self.assertNotIn("rn invite", self.calls())

    def test_update_relay_only_runs_unattended(self):
        repo = self.installed()
        with open(repo / "agent_relay.py", "a") as stream:
            stream.write("\n")
        result = self.run_script("update.sh", "--no-pull", root=repo, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        relay, _ = self.relay_files()
        self.assertEqual(relay.read_bytes(), (repo / "agent_relay.py").read_bytes())
        self.assertNotIn("[GROK-UI]", result.stdout)
        self.assertRegex(result.stdout, r"relay\s+updated")
        self.assertIn("RESULT ok", result.stdout)
        self.assertNotIn("rn setup", self.calls())
        self.assertNotIn("rn invite", self.calls())
        self.assertNotIn("rn send", self.calls())

    def test_update_compares_generated_output(self):
        repo = self.installed()
        generator = repo / "scripts/write_launch_agent.py"
        original = generator.read_text()
        generator.write_text(original.replace('"ThrottleInterval": 30', '"ThrottleInterval": 31'))
        self.assertRegex(self.run_script("update.sh", "--no-pull", "--plan", root=repo).stdout, r"relay\s+changed")
        generator.write_text(original)
        prompt = repo / "prompts/routine.md"
        text = prompt.read_text()
        prompt.write_text(text + "\nA note outside the prompt block.\n")
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("already up to date", result.stdout)
        fence = text.index("```\n") + 4
        prompt.write_text(text[:fence] + "Changed. " + text[fence:])
        self.assertRegex(self.run_script("update.sh", "--no-pull", "--plan", root=repo).stdout, r"routine prompt\s+changed")

    def test_update_plan_and_non_terminal_never_change_the_deployment(self):
        repo = self.installed(record=False)
        result = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"cloud scripts\s+unknown")
        self.assertIsNone(self.record())
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIsNone(self.record())
        self.mark(repo, "cloud_setup", "pending")
        data = self.record()
        data["txn"] = {"stage": "routine_paused", "relay_was_loaded": True, "need": ["prompt"], "lang": "en",
                       "commit": "a" * 40, "target": {}}
        self.write_record(data)
        path = self.home / ".local/lib/agent-mesh/deployed.json"
        before = path.read_bytes()
        for args in (("--no-pull", "--plan"), ("--no-pull",)):
            result = self.run_script("update.sh", *args, root=repo)
            self.assertEqual(result.returncode, 0 if "--plan" in args else 3, result.stdout + result.stderr)
            self.assertIn("unfinished update stopped at 'routine_paused'", result.stdout)
            self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.service_changes(), [])
        path.write_text("{not json")
        result = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("unreadable", result.stderr)

    def test_update_guided_flow_runs_in_order_and_records_each_step(self):
        self.guided_flow("--manual")

    def test_update_without_a_record_of_the_cloud_side_uses_the_guided_flow_by_itself(self):
        self.guided_flow()

    def guided_flow(self, *args):
        repo = self.installed(record=False)
        result = self.run_tty(repo, "--no-pull", *args, answers=["y", "y", *self.cloud_reply(repo), "y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        if args:
            self.assertNotIn(m("update.plan_no_record"), result.stdout)
        else:
            self.assertIn(m("update.plan_no_record"), result.stdout)
        [message] = self.sent()
        self.assertRegex(message["body"], r"\Aagent-mesh stub [0-9a-f]{16}\nlang=en\Z")
        self.assertLess(result.stdout.index("match: commit, checksums and engine"),
                        result.stdout.index("agent-mesh routine stub v1"))
        self.assertIn("match: commit, checksums and engine", result.stdout)
        self.assertIn("bash -s <<'AGENT_MESH_UPDATE'", result.stdout)
        self.assertNotIn("pull --ff-only", result.stdout)
        self.assertIn(f"TAG={self.release_tag(repo)}; COMMIT={'a' * 40}", result.stdout)
        self.assertRegex(result.stdout, r"cloud release\s+ok")
        self.assertRegex(result.stdout, r"; REQ=[0-9a-f]{16}\n")
        self.assertIn('exec bash "$A/box/rn-cloud-update.sh" --after-checkout', result.stdout)
        data = self.record()
        self.assertEqual(data["cloud_engine"]["version"], "0.12.17")
        for field in ("cloud", "cloud_setup", "prompt", "cloud_engine"):
            self.assertEqual(data[field]["state"], "confirmed", field)
        self.assertEqual((data["prompt"]["mode"], data["prompt"]["stub"]), ("thin", 1))
        self.assertNotIn("txn", data)
        lines = self.service_changes()
        self.assertEqual(lines[0].split()[1], "bootout")
        self.assertEqual(lines[-1].split()[1], "bootstrap")
        self.assertIn("a" * 40, result.stdout)
        self.assertIn(self.sha(repo / "box/rn-cloud.sh"), result.stdout)
        self.assertTrue(self.loaded())

    def test_update_stop_restores_the_relay_and_rerun_continues(self):
        repo = self.installed(record=False)
        relay, _ = self.relay_files()
        result = self.run_tty(repo, "--no-pull", "--manual", answers=["y", "n"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(self.loaded())
        self.assertEqual(self.started()[-1], self.sha(relay))
        self.assertEqual(self.record()["txn"]["stage"], "relay_stopped")
        self.assertIn("stopped at 'relay_stopped'", result.stdout)
        result = self.run_tty(repo, "--no-pull", answers=["y", "y", *self.cloud_reply(repo), "y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("continuing the update that stopped at 'relay_stopped'", result.stdout)
        self.assertNotIn("txn", self.record())

    def test_update_rerun_stops_the_restored_relay_before_continuing(self):
        repo = self.paused_after_cloud()
        result = self.run_tty(repo, "--no-pull", answers=["y", "y", "y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.service_changes()[0].split()[1], "bootout")
        self.assertIn("still paused", result.stderr)
        self.assertNotIn("Send this to Grok Bot", result.stdout)
        self.assertNotIn("txn", self.record())

    def test_update_rerun_redoes_a_step_whose_target_moved(self):
        repo = self.paused_after_cloud()
        with open(repo / "box/rn-cloud.sh", "a") as stream:
            stream.write("\n")
        result = self.run_tty(repo, "--no-pull", answers=["y", "y", *self.cloud_reply(repo), "y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Send this to Grok Bot", result.stdout)
        self.assertEqual(self.record()["cloud"]["hash"],
                         subprocess.run([sys.executable, str(repo / "scripts/deploy_state.py"), "digest", "cloud"],
                                        env=self.env, capture_output=True, text=True).stdout.strip())

    def test_update_rerun_with_a_rejected_cloud_reply_keeps_the_routine_paused(self):
        repo = self.paused_after_cloud()
        with open(repo / "box/rn-cloud.sh", "a") as stream:
            stream.write("\n")
        result = self.run_tty(repo, "--no-pull", answers=["y", "y", "."])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("mismatch:", result.stdout)
        self.assertIn("Keep it paused until ./update.sh records the cloud step", result.stdout)
        self.assertNotIn("resume it in Grok Bot", result.stdout)
        data = self.record()
        self.assertEqual(data["txn"]["stage"], "routine_paused")
        result = self.run_tty(repo, "--no-pull", answers=["y", "y", *self.cloud_reply(repo), "y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("txn", self.record())

    def test_update_cloud_step_needs_the_pasted_reply_to_match(self):
        cases = {
            "bare yes": ["y", "."],
            "empty answer": ["."],
            "wrong commit": None,
            "wrong checksum": None,
        }
        for label, answer in cases.items():
            with self.subTest(label):
                self.setUp()
                repo = self.installed(record=False)
                if label == "wrong commit":
                    answer = self.cloud_reply(repo, commit="b" * 40)
                elif label == "wrong checksum":
                    answer = self.cloud_reply(repo, fetch_sum="c" * 64)
                result = self.run_tty(repo, "--no-pull", "--manual", answers=["y", "y", *answer, "y", "y"])
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("mismatch:", result.stdout)
                self.assertIn("The routine is still paused. Keep it paused until ./update.sh records the cloud step",
                              result.stdout)
                self.assertNotIn("resume it in Grok Bot", result.stdout)
                self.assertTrue(self.loaded())
                data = self.record()
                self.assertNotEqual(data.get("cloud", {}).get("state"), "confirmed")
                self.assertEqual(data["txn"]["stage"], "routine_paused")
                result = self.run_tty(repo, "--no-pull", answers=["y", "y", *self.cloud_reply(repo), "y", "y"])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("Send this to Grok Bot", result.stdout)
                self.assertEqual(self.record()["cloud"]["state"], "confirmed")

    def test_update_checks_the_release_before_changing_anything(self):
        cases = {
            "not published": ("release-fail", "could not be fetched"),
            "differs": ("release/box/rn-cloud.sh", "box/rn-cloud.sh in this checkout differs from the release"),
        }
        for label, (path, why) in cases.items():
            with self.subTest(label):
                self.setUp()
                repo = self.installed(record=False)
                (self.git / path).parent.mkdir(parents=True, exist_ok=True)
                (self.git / path).write_text("other\n")
                path_ = self.home / ".local/lib/agent-mesh/deployed.json"
                before = path_.read_bytes() if path_.exists() else None
                plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
                self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
                self.assertRegex(plan.stdout, r"cloud release\s+failed")
                self.assertIn(why, plan.stdout)
                result = self.run_tty(repo, "--no-pull", answers=["y", "y", "y"])
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(why, result.stderr)
                self.assertNotIn("Send this to Grok Bot", result.stdout)
                self.assertEqual(self.sent(), [])
                self.assertEqual(self.service_changes(), [])
                self.assertEqual(path_.read_bytes() if path_.exists() else None, before)
                self.assertTrue(self.loaded())

    def test_update_without_cloud_changes_does_not_check_the_release(self):
        repo = self.installed()
        (self.git / "release-fail").write_text("")
        with open(repo / "agent_relay.py", "a") as stream:
            stream.write("\n")
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertNotIn("cloud release", plan.stdout)
        result = self.run_tty(repo, "--no-pull", answers=["y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("git init", self.calls())
        self.assertNotIn("Send this to Grok Bot", result.stdout)

    def test_update_release_points_elsewhere_and_the_reply_must_show_it(self):
        repo = self.installed(record=False)
        (self.git / "release-commit").write_text("b" * 40 + "\n")
        result = self.run_tty(repo, "--no-pull", "--manual", answers=["y", "y", *self.cloud_reply(repo), "y", "y"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"TAG={self.release_tag(repo)}; COMMIT={'b' * 40}", result.stdout)
        self.assertIn("mismatch: the reply shows commit aaaaaaaaaaaa, not bbbbbbbbbbbb", result.stdout)
        result = self.run_tty(repo, "--no-pull", answers=["y", "y", *self.cloud_reply(repo, commit="b" * 40), "y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.record()["cloud"]["state"], "confirmed")

    def test_update_interrupted_while_pasting_keeps_the_cloud_unconfirmed(self):
        for sig, code in ((signal.SIGINT, 130), (signal.SIGTERM, 143)):
            with self.subTest(sig=sig):
                self.setUp()
                repo = self.installed(record=False)
                partial = self.cloud_reply(repo)[:4]
                rc, out, err = self.run_tty_until(repo, "--no-pull", "--manual", answers=["y", "y", *partial],
                                                  wait_for="Paste Grok Bot's reply", sig=sig)
                self.assertEqual(rc, code, out + err)
                self.assertIn("The routine is still paused", out)
                self.assertTrue(self.loaded())
                data = self.record()
                self.assertNotEqual(data.get("cloud", {}).get("state"), "confirmed")
                self.assertEqual(data["txn"]["stage"], "routine_paused")
                self.assertFalse((self.home / ".local/lib/agent-mesh/update.lock").exists())
                result = self.run_tty(repo, "--no-pull", answers=["y", "y", *self.cloud_reply(repo), "y", "y"])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.record()["cloud"]["state"], "confirmed")

    def test_cloud_reply_reads_only_the_result_lines(self):
        repo = self.installed()
        r = "agent-mesh-result"
        cloud, fetch = self.sha(repo / "box/rn-cloud.sh"), self.sha(repo / "box/rn-fetch.py")
        tag = self.release_tag(repo)
        c, a = f"{r} sum {cloud}  box/rn-cloud.sh\n", f"{r} commit {'a' * 40}\n"
        f = f"{r} sum {fetch}  box/rn-fetch.py\n"
        rest = "".join(f"{r} sum {self.sha(repo / n)}  {n}\n" for n in CLOUD_FILES[2:]) + f"{r} DONE {tag}\n"
        good = [
            a + c + f + rest,
            f"Previously HEAD was {'b' * 40}.\n{r} commit {'A' * 40}\n{r} sum {cloud.upper()} *box/rn-cloud.sh\n"
            f"{r} sum {fetch}  ./box/rn-fetch.py\n{rest}",
            f"`{a.strip()}`\n> {c}- {f}**{r} DONE {tag}**\n{rest}",
            a + c + f + rest + "I would have printed STOP: here, but it is not at the start of a line\n",
            a + c + f + rest + f"commit {'c' * 40}\n{'d' * 64}  box/rn-fetch.py\nDONE v0.0.1\n",
        ]
        bad = [
            a + f"{r} commit {'b' * 40}\n" + c + f + rest,
            a + c + rest,
            f"{r} commit {'a' * 39}\n" + c + f + rest,
            a + c + f + f"{r} sum {'d' * 64}  box/rn-fetch.py\n" + rest,
            a + c.replace("rn-cloud.sh", "rn-cloud.sh.bak") + f + rest,
            a + c + f.replace("rn-fetch.py", "rn-fetch.py~") + rest,
            a + c + f + rest.replace(tag, "v0.0.1"),
            a + c + f + rest.replace(tag, tag + ".1"),
            a + c + f + rest + "STOP: the engine step failed\n",
            a + c + f + rest + "> **STOP: no clone**\n",
            a + c + f + rest.replace(f"{r} DONE {tag}", f"The block failed before {r} DONE {tag}"),
            a + c + f + rest.replace(f"{r} DONE {tag}", f"DONE {tag}"),
            (a + c + f + rest).replace(f"{r} ", ""),
        ]
        for text in good:
            with self.subTest(text=text):
                result = self.cloud_reply_check(repo, text)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for text in bad:
            with self.subTest(text=text):
                result = self.cloud_reply_check(repo, text)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("mismatch:", result.stdout)

    def test_cloud_reply_with_the_engine_needs_a_pinned_build_and_the_launcher(self):
        repo = self.installed()
        good = "\n".join(self.cloud_reply(repo)[:-1])
        build = next(iter(rn_engine().PINNED_BUILDS["0.12.17"].values()))
        result = self.cloud_reply_check(repo, good, "--engine")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        cases = {
            "no build line": (good.replace(f"engine: pinned build {build} at box/bin/engine/reason-network\n", ""),
                              "no line 'engine: pinned build"),
            "unpinned build": (good.replace(build, "e" * 64), "not a pinned Reason Network 0.12.17 build"),
            "two builds": (good + f"\nengine: pinned build {'e' * 64} at box/bin/engine/reason-network",
                           "not a pinned Reason Network 0.12.17 build"),
            "old path": (good.replace("box/bin/engine/reason-network", "box/bin/reason-network"),
                         "no line 'engine: pinned build"),
            "no launcher": (good.replace("engine: launcher ok\n", ""), "no line 'engine: launcher ok'"),
        }
        for label, (text, why) in cases.items():
            with self.subTest(label):
                result = self.cloud_reply_check(repo, text, "--engine")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(why, result.stdout)

    def test_update_terminated_while_waiting_restores_the_relay(self):
        repo = self.installed(record=False)
        code, out, err = self.run_tty_until(repo, "--no-pull", "--manual", answers=["y"], wait_for="Pause the routine")
        self.assertEqual(code, 143, out + err)
        self.assertTrue(self.loaded())
        self.assertEqual(self.record()["txn"]["stage"], "relay_stopped")
        self.assertFalse((self.home / ".local/lib/agent-mesh/update.lock").exists())

    def test_update_after_a_kill_reclaims_the_lock_and_continues(self):
        repo = self.installed()
        data = self.record()
        data["txn"] = {"stage": "routine_paused", "relay_was_loaded": True, "need": ["prompt"], "lang": "en",
                       "commit": "a" * 40, "target": {}}
        self.write_record(data)
        (self.state / "loaded").unlink()
        lock = self.home / ".local/lib/agent-mesh/update.lock"
        lock.mkdir()
        (lock / "pid").write_text("999999\n")
        result = self.run_tty(repo, "--no-pull", answers=["y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("removing the lock", result.stdout)
        self.assertTrue(self.loaded())
        self.assertNotIn("txn", self.record())
        self.assertFalse(lock.exists())
        lock.mkdir()
        holder = subprocess.Popen(["/bin/sh", "-c", "sleep 30; :", "update.sh"])
        try:
            (lock / "pid").write_text(f"{holder.pid}\n")
            for ps in ("present", "missing"):
                if ps == "missing":
                    (self.bin / "ps").unlink(missing_ok=True)
                result = self.run_script("update.sh", "--no-pull", root=repo)
                self.assertEqual(result.returncode, 1, ps)
                self.assertIn("another ./update.sh is running", result.stderr, ps)
                self.assertTrue(lock.exists(), ps)
        finally:
            holder.kill()
            holder.wait()

    def test_update_refuses_cloud_files_that_differ_from_the_release(self):
        repo = self.installed()
        before = self.record()
        (self.git / "release/box").mkdir(parents=True)
        shutil.copy(repo / "box/rn-cloud.sh", self.git / "release/box/rn-cloud.sh")
        with open(repo / "box/rn-cloud.sh", "a") as stream:
            stream.write("\n")
        result = self.run_tty(repo, "--no-pull")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("box/rn-cloud.sh in this checkout differs from the release", result.stderr)
        self.assertEqual(self.service_changes(), [])
        self.assertEqual(self.record(), before)
        (self.git / "release/box/rn-cloud.sh").unlink()
        result = self.run_tty(repo, "--no-pull", answers=["n"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.record()["cloud"], before["cloud"])
        self.assertTrue(self.loaded())

    def test_update_asks_once_about_steps_the_installer_printed(self):
        repo = self.installed(record=False)
        for field in ("cloud", "cloud_setup", "prompt", "cloud_engine"):
            self.mark(repo, field, "pending")
        result = self.run_tty(repo, "--no-pull", answers=["y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = self.record()
        for field in ("cloud", "cloud_setup", "prompt", "cloud_engine"):
            self.assertEqual(data[field]["state"], "confirmed", field)
        self.assertEqual(data["cloud"]["release"], (repo / "VERSION").read_text().strip())
        self.assertEqual(data["prompt"]["stub"], 1)
        self.assertEqual(self.service_changes(), [])
        [message] = self.sent()
        self.assertRegex(message["body"], r"\Aagent-mesh stub [0-9a-f]{16}\nlang=en\Z")
        data["prompt"]["state"] = "pending"
        self.write_record(data)
        result = self.run_tty(repo, "--no-pull", answers=["y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Replace the prompt saved in the routine", result.stdout)
        self.assertEqual(len(self.sent()), 2)
        self.assertEqual(self.record()["prompt"]["state"], "confirmed")

    def test_update_asks_grok_bot_to_update_the_cloud_and_records_its_reply(self):
        repo = self.installed()
        version = (repo / "VERSION").read_text().strip()
        self.assertEqual(self.record()["cloud"]["release"], version)
        self.cloud_changed(repo)
        self.bot_replies(repo)
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertRegex(plan.stdout, r"cloud release\s+ok")
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertEqual(self.sent(), [])
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        [message] = self.sent()
        self.assertEqual(message["to"], "demo/bot")
        self.assertRegex(message["body"], rf"\Aagent-mesh update v{version} {'a' * 40} [0-9a-f]{{16}}\Z")
        self.assertIn("rn send demo/bot -", self.calls())
        self.assertIn("match: commit, checksums and engine 0.12.17", result.stdout)
        data = self.record()
        self.assertEqual(data["cloud"], {"hash": self.digest(repo, "cloud"), "state": "confirmed", "release": version})
        for field in ("cloud_setup", "cloud_engine"):
            self.assertEqual(data[field]["state"], "confirmed", field)
        self.assertNotIn("request", data)
        self.assertEqual(self.service_changes(), [])
        self.assertIn("RESULT ok", result.stdout)
        self.assertNotIn("[GROK-UI]", result.stdout)
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertIn("already up to date", result.stdout)
        self.assertEqual(len(self.sent()), 1)

    def test_update_records_only_the_reply_to_its_own_request(self):
        repo = self.installed()
        self.cloud_changed(repo)
        self.env["AGENT_MESH_REPLY_WAIT"] = "10"
        good = self.good_reply(repo)
        cases = {
            "another request": {"req": "0" * 16},
            "another sender": {"sender": "demo/other"},
            "sent before the request": {"ago_ms": 3600000},
            "held by the engine": {"held": 1},
            "no request line": {"body": good.replace("agent-mesh-result request {req}", "request {req}")},
        }
        for n, (label, conf) in enumerate(cases.items(), 1):
            with self.subTest(label):
                body = conf.pop("body", good)
                self.bot_replies(body=body, **conf)
                result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("No reply from Grok Bot within 10 s.", result.stdout)
                self.assertIn("(usually 1 to 3 minutes, at most 10 s)", result.stdout)
                self.assertNotIn("this first update request", result.stdout)
                self.assertEqual(len(self.sent()), n)
                data = self.record()
                self.assertNotEqual(data["cloud"]["hash"], self.digest(repo, "cloud"))
                self.assertEqual(data["request"]["req"], self.sent()[-1]["body"].split()[-1])
        self.bot_silent()
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.put_reply(self.record()["request"]["req"], good)
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("reply to the earlier request arrived and matches", result.stdout)
        self.assertEqual(len(self.sent()), len(cases) + 1)
        self.assertEqual(self.record()["cloud"]["hash"], self.digest(repo, "cloud"))

    def test_update_shows_a_stop_reply_and_records_nothing(self):
        repo = self.installed()
        self.cloud_changed(repo)
        before = self.record()
        self.bot_replies(body="agent-mesh-result request {req}\nchanged files in /workspace/agent-mesh:\n  M box/x\n"
                              "STOP: /workspace/agent-mesh has local changes; nothing was changed\n")
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("STOP: /workspace/agent-mesh has local changes", result.stdout)
        self.assertIn("mismatch: the reply has a STOP line", result.stdout)
        self.assertIn("the cloud side was not recorded as updated", result.stderr)
        data = self.record()
        self.assertEqual(data["cloud"], before["cloud"])
        self.assertNotIn("request", data)
        self.bot_replies(repo)
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.sent()), 2)

    def test_update_sends_again_when_the_checkout_moved_after_the_request(self):
        repo = self.installed()
        self.cloud_changed(repo)
        self.env["AGENT_MESH_REPLY_WAIT"] = "5"
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        old = self.record()["request"]["req"]
        self.put_reply(old, self.good_reply(repo))
        self.cloud_changed(repo, "box/rn-fetch.py")
        self.bot_replies(repo)
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("this checkout changed after the update request was sent", result.stdout)
        self.assertEqual(len(self.sent()), 2)
        self.assertNotIn(old, self.sent()[-1]["body"])
        self.assertEqual(self.record()["cloud"]["hash"], self.digest(repo, "cloud"))

    def test_first_update_reply_after_the_timeout_is_recorded_without_sending_again(self):
        repo = self.installed()
        version = (repo / "VERSION").read_text().strip()
        req = self.first_update_pending(repo, "zh-CN")
        self.put_reply(req, self.good_reply(repo))
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        import messages
        self.assertIn(messages.t("update.cloud_earlier_reply", lang="zh-CN"), result.stdout)
        self.assertEqual(len(self.sent()), 1)
        self.assertEqual(self.record()["cloud"]["release"], version)

    def test_first_update_reply_to_a_request_for_another_checkout_is_not_recorded(self):
        repo = self.installed()
        old = self.first_update_pending(repo)
        self.put_reply(old, self.good_reply(repo))
        self.cloud_changed(repo, "box/rn-fetch.py")
        self.bot_replies(repo)
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("this checkout changed after the update request was sent", result.stdout)
        self.assertEqual(len(self.sent()), 2)
        body = self.sent()[-1]["body"]
        self.assertTrue(body.endswith("\nlang=en"), body)
        self.assertNotIn(old, body)
        self.assertEqual(self.record()["cloud"]["hash"], self.digest(repo, "cloud"))

    def test_first_update_reply_to_a_request_for_another_release_is_not_recorded(self):
        repo = self.installed()
        old = self.first_update_pending(repo)
        self.put_reply(old, self.good_reply(repo))
        (repo / "VERSION").write_text("9.9.9\n")
        (self.git / "release-commit").write_text("b" * 40 + "\n")
        self.bot_replies(body=self.good_reply(repo, commit="b" * 40))
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.sent()), 2, result.stdout)
        body = self.sent()[-1]["body"]
        self.assertTrue(body.endswith("\nlang=en"), body)
        self.assertIn("b" * 40, body)
        self.assertNotIn(old, body)
        self.assertIn("v9.9.9", body)
        self.assertEqual(self.record()["cloud"]["release"], "9.9.9")

    def test_update_has_grok_bot_update_the_cloud_before_it_shows_a_changed_stub(self):
        repo = self.installed()
        prompt = repo / "prompts/routine.md"
        text = prompt.read_text()
        fence = text.index("```\n") + 4
        prompt.write_text(text[:fence] + "Changed. " + text[fence:])
        self.cloud_changed(repo)
        before = self.record()["prompt"]
        self.bot_replies(body="agent-mesh-result request {req}\nSTOP: /workspace/agent-mesh has local changes\n")
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertEqual(self.sent(), [])
        result = self.run_tty(repo, "--no-pull", answers=["y"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("the cloud side was not recorded as updated", result.stderr)
        self.assertNotIn("Changed. ", result.stdout)
        self.assertEqual(self.record()["prompt"], before)
        [message] = self.sent()
        self.assertRegex(message["body"], rf"\Aagent-mesh update v\S+ {'a' * 40} [0-9a-f]{{16}}\Z")
        self.bot_replies(repo)
        result = self.run_tty(repo, "--no-pull", answers=["y", "n"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertLess(result.stdout.index("Sent the update request to Grok Bot"), result.stdout.index("Changed. "))
        self.assertIn("the routine prompt was not recorded as updated", result.stderr)
        self.assertEqual(self.record()["cloud"]["hash"], self.digest(repo, "cloud"))
        self.assertEqual(self.record()["prompt"], before)
        self.assertEqual(len(self.sent()), 2)
        result = self.run_tty(repo, "--no-pull", answers=["y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Changed. ", result.stdout)
        self.assertEqual(len(self.sent()), 3)
        self.assertRegex(self.sent()[-1]["body"], r"\Aagent-mesh stub [0-9a-f]{16}\nlang=en\Z")
        data = self.record()["prompt"]
        self.assertEqual((data["state"], data["mode"], data["stub"]), ("confirmed", "thin", 1))

    def test_update_without_a_terminal_updates_the_cloud_and_leaves_a_changed_stub_for_later(self):
        repo = self.installed()
        version = (repo / "VERSION").read_text().strip()
        prompt = repo / "prompts/routine.md"
        text = prompt.read_text()
        fence = text.index("```\n") + 4
        prompt.write_text(text[:fence] + "Changed. " + text[fence:])
        self.cloud_changed(repo)
        before = self.record()["prompt"]
        self.bot_replies(repo)
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertIn(m("update.plan_prompt_later"), plan.stdout)
        self.assertNotIn(m("update.plan_prompt_later"), self.run_tty(repo, "--no-pull", "--plan").stdout)
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertEqual(self.sent(), [])
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Changed. ", result.stdout)
        self.assertIn(m("update.prompt_later", "en"), result.stdout)
        self.assertIn(m("r.prompt_later"), result.stdout)
        [message] = self.sent()
        self.assertRegex(message["body"], rf"\Aagent-mesh update v{version} {'a' * 40} [0-9a-f]{{16}}\Z")
        data = self.record()
        self.assertEqual(data["cloud"]["hash"], self.digest(repo, "cloud"))
        self.assertEqual(data["prompt"], before)
        self.assertNotIn("txn", data)
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertRegex(plan.stdout, r"cloud scripts\s+unchanged")
        self.assertRegex(plan.stdout, r"routine prompt\s+changed")
        result = self.run_tty(repo, "--no-pull", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Changed. ", result.stdout)
        self.assertIn(m("r.prompt_later"), result.stdout)
        self.assertNotIn(m("update.up_to_date"), result.stdout)
        self.assertEqual(len(self.sent()), 1)
        self.assertEqual(self.record()["prompt"], before)
        result = self.run_tty(repo, "--no-pull", answers=["y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Changed. ", result.stdout)
        self.assertEqual(len(self.sent()), 2)
        self.assertRegex(self.sent()[-1]["body"], r"\Aagent-mesh stub [0-9a-f]{16}\nlang=en\Z")
        data = self.record()["prompt"]
        self.assertEqual((data["state"], data["mode"], data["stub"]), ("confirmed", "thin", 1))

    def test_update_without_a_terminal_still_stops_for_a_prompt_that_needs_an_answer(self):
        repo = self.installed()
        self.cloud_changed(repo)
        self.bot_replies(repo)
        full = subprocess.run([sys.executable, str(repo / "scripts/deploy_state.py"), "--prompt-mode", "full",
                               "digest", "prompt"], env=self.env, capture_output=True, text=True,
                              check=True).stdout.strip()
        data = self.record()
        data["prompt"].update(state="pending", mode="full", hash=full)
        self.write_record(data)
        before = self.record()
        for args in (("--full-prompt",), ("--manual",)):
            result = self.run_script("update.sh", "--no-pull", "--yes", *args, root=repo)
            self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
            self.assertNotIn(m("update.plan_prompt_later"), result.stdout)
            self.assertEqual(self.sent(), [])
            self.assertEqual(self.record(), before)
        self.assertRegex(self.run_script("update.sh", "--no-pull", "--plan", "--full-prompt", root=repo).stdout,
                         r"routine prompt\s+pending")
        data = self.record()
        del data["prompt"]
        self.write_record(data)
        before = self.record()
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"routine prompt\s+unknown")
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.record(), before)

    def test_update_from_a_record_before_v0_8_2_shows_the_stub_first_and_puts_the_full_prompt_back(self):
        repo = self.installed()
        version = (repo / "VERSION").read_text().strip()
        data = self.record()
        del data["cloud"]["release"]
        data["prompt"].update(hash="0" * 64, text="0" * 64)
        self.write_record(data)
        self.cloud_changed(repo)
        self.bot_silent()
        self.env["AGENT_MESH_REPLY_WAIT"] = "5"
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertIn(m("update.plan_cloud_stub_first", "0.12.17"), plan.stdout)
        self.assertNotIn(m("update.plan_prompt_later"), plan.stdout)
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertEqual(self.sent(), [])
        result = self.run_tty(repo, "--no-pull", answers=["y", "y", "n"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        out = result.stdout
        self.assertLess(out.index("agent-mesh routine stub v1"), out.index("Sent the update request to Grok Bot"))
        self.assertLess(out.index("Sent the update request to Grok Bot"), out.index(m("update.full_back", "en")))
        self.assertIn("--stdin 0 - ", out[out.index(m("update.full_back", "en")):])
        self.assertIn(m("update.full_back_later"), out)
        [message] = self.sent()
        self.assertRegex(message["body"], rf"\Aagent-mesh update v{version} {'a' * 40} [0-9a-f]{{16}}\nlang=en\Z")
        prompt = self.record()["prompt"]
        self.assertEqual((prompt["state"], prompt["recover"]), ("pending", 1))
        result = self.run_tty(repo, "--no-pull", answers=["y", "y"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("update.recover_check"), result.stdout)
        self.assertIn(m("update.full_back", "en"), result.stdout)
        prompt = self.record()["prompt"]
        self.assertEqual((prompt["state"], prompt["mode"]), ("confirmed", "full"))
        self.assertNotIn("recover", prompt)
        self.assertEqual(len(self.sent()), 1)
        self.put_reply(self.record()["request"]["req"], self.good_reply(repo))
        result = self.run_tty(repo, "--no-pull", answers=["y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(m("update.cloud_earlier_reply"), result.stdout)
        self.assertIn("agent-mesh routine stub v1", result.stdout)
        self.assertEqual(len(self.sent()), 2)
        self.assertRegex(self.sent()[-1]["body"], r"\Aagent-mesh stub [0-9a-f]{16}\nlang=en\Z")
        data = self.record()
        self.assertEqual((data["prompt"]["state"], data["prompt"]["mode"], data["prompt"]["stub"]),
                         ("confirmed", "thin", 1))
        self.assertEqual(data["cloud"]["release"], version)

    def test_update_read_back_reports_each_kind_of_answer(self):
        repo = self.installed()
        self.env["AGENT_MESH_REPLY_WAIT"] = "5"

        def pending():
            data = self.record()
            data["prompt"]["state"] = "pending"
            for key in ("req", "sent_ms", "stub"):
                data["prompt"].pop(key, None)
            self.write_record(data)

        pending()
        (self.who / "stub").write_text("silent")
        result = self.run_script("update.sh", "--no-pull", root=repo, timeout=60)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("update.readback_timeout", m("update.n_seconds", 5), "demo/bot"), result.stdout)
        self.assertIn(m("r.stub_missing"), result.stdout)
        prompt = self.record()["prompt"]
        self.assertEqual((prompt["state"], prompt["missing"]), ("pending", 1))
        req = prompt["req"]
        self.put_reply(req, "agent-mesh-result request {req}\nagent-mesh-result lang en\n"
                            "agent-mesh-result stub 1\nagent-mesh-result DONE stub\n")
        result = self.run_script("update.sh", "--no-pull", root=repo, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(m("state.stub_confirmed", "demo/bot", 1), result.stdout)
        self.assertNotIn("agent-mesh routine stub v1", result.stdout)
        self.assertEqual(len(self.sent()), 1)
        self.assertEqual(self.record()["prompt"]["state"], "confirmed")
        self.assertNotIn("missing", self.record()["prompt"])
        pending()
        (self.who / "stub").write_text("nostub")
        result = self.run_script("update.sh", "--no-pull", root=repo, timeout=60)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("state.stub_missing", "demo/bot"), result.stdout)
        self.assertIn(m("r.stub_missing"), result.stdout)
        self.assertEqual((self.record()["prompt"]["state"], self.record()["prompt"]["missing"]), ("pending", 1))
        self.assertEqual(len(self.sent()), 2)
        self.assertRegex(self.run_script("update.sh", "--no-pull", "--plan", root=repo).stdout,
                         r"routine prompt\s+changed")
        (self.who / "stub").write_text("ok")
        result = self.run_tty(repo, "--no-pull", answers=["y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("agent-mesh routine stub v1", result.stdout)
        self.assertEqual(len(self.sent()), 3)
        self.assertNotIn("missing", self.record()["prompt"])
        self.assertEqual(self.record()["prompt"]["state"], "confirmed")
        pending()
        (self.state / "loaded").unlink()
        result = self.run_script("update.sh", "--no-pull", root=repo, timeout=60)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("update.readback_relay_down", "bot"), result.stdout)
        self.assertEqual(len(self.sent()), 3)
        (self.state / "loaded").write_text("")
        result = self.run_script("update.sh", "--no-pull", root=repo, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(m("r.stub_confirmed"), result.stdout)
        self.assertEqual(self.record()["prompt"]["state"], "confirmed")

    def test_update_read_back_replies_that_do_not_confirm_the_stub(self):
        repo = self.installed()
        self.env["AGENT_MESH_REPLY_WAIT"] = "5"
        head = "agent-mesh-result request {req}\n"
        cases = {
            "stop": (head + "STOP: box/lang cannot be written (disk full)\n",
                     "STOP: box/lang cannot be written (disk full)"),
            "language": (head + "agent-mesh-result lang zh-CN\nagent-mesh-result stub 1\nagent-mesh-result DONE stub\n",
                         m("state.stub_lang", "zh-CN", "en")),
            "stub": (head + "agent-mesh-result lang {lang}\nagent-mesh-result stub 2\nagent-mesh-result DONE stub\n",
                     m("state.stub_other", "2", 1)),
            "done": (head + "agent-mesh-result lang {lang}\nagent-mesh-result stub 1\n", m("reply.no_done", "stub")),
        }
        for name, (body, problem) in cases.items():
            with self.subTest(name):
                data = self.record()
                data["prompt"]["state"] = "pending"
                for key in ("req", "sent_ms", "stub"):
                    data["prompt"].pop(key, None)
                self.write_record(data)
                (self.who / "bot.json").write_text(json.dumps({"stub_body": body}))
                sent = len(self.sent())
                result = self.run_script("update.sh", "--no-pull", root=repo, timeout=60)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(m("state.stub_mismatch", "demo/bot"), result.stdout)
                self.assertIn(problem, result.stdout)
                self.assertIn(m("r.readback_waiting"), result.stdout)
                self.assertEqual(len(self.sent()), sent + 1)
                prompt = self.record()["prompt"]
                self.assertEqual(prompt["state"], "pending")
                for key in ("req", "sent_ms", "missing"):
                    self.assertNotIn(key, prompt)
                plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
                self.assertRegex(plan.stdout, r"routine prompt\s+read-back")
                self.bot_silent()
                result = self.run_script("update.sh", "--no-pull", root=repo, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(len(self.sent()), sent + 2)
                self.assertNotEqual(self.sent()[-1]["body"], self.sent()[-2]["body"])
                self.assertNotIn("agent-mesh routine stub v1", result.stdout)
                self.assertEqual(self.record()["prompt"]["state"], "confirmed", result.stdout + result.stderr)

    def test_update_read_back_with_an_unreadable_message_store_keeps_its_request(self):
        repo = self.installed()
        self.env["AGENT_MESH_REPLY_WAIT"] = "5"
        data = self.record()
        data["prompt"]["state"] = "pending"
        for key in ("req", "sent_ms", "stub"):
            data["prompt"].pop(key, None)
        self.write_record(data)
        (self.who / "stub").write_text("silent")
        self.assertEqual(self.run_script("update.sh", "--no-pull", root=repo, timeout=60).returncode, 1)
        req = self.record()["prompt"]["req"]
        db = self.home / ".reason/network/network.db"
        db.rename(db.with_name("aside.db"))
        result = self.run_script("update.sh", "--no-pull", root=repo, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(m("request.no_db", db), result.stdout)
        self.assertIn(m("r.prompt_later"), result.stdout)
        self.assertEqual(len(self.sent()), 1)
        self.assertEqual((self.record()["prompt"]["req"], self.record()["prompt"]["missing"]), (req, 1))
        data = self.record()
        del data["prompt"]["missing"]
        self.write_record(data)
        result = self.run_script("update.sh", "--no-pull", root=repo, timeout=60)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("request.no_db", db), result.stdout)
        self.assertIn(m("r.readback_waiting"), result.stdout)
        self.assertEqual(len(self.sent()), 1)
        self.assertEqual(self.record()["prompt"]["req"], req)
        self.assertNotIn("missing", self.record()["prompt"])
        db.with_name("aside.db").rename(db)
        self.put_reply(req, "agent-mesh-result request {req}\nagent-mesh-result lang en\n"
                            "agent-mesh-result stub 1\nagent-mesh-result DONE stub\n")
        result = self.run_script("update.sh", "--no-pull", root=repo, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.sent()), 1)
        self.assertEqual(self.record()["prompt"]["state"], "confirmed")

    def test_update_after_a_read_back_timeout_shows_the_stub_again(self):
        repo = self.installed()
        self.env["AGENT_MESH_REPLY_WAIT"] = "5"
        data = self.record()
        data["prompt"]["state"] = "pending"
        for key in ("req", "sent_ms", "stub"):
            data["prompt"].pop(key, None)
        self.write_record(data)
        (self.who / "stub").write_text("silent")
        result = self.run_script("update.sh", "--no-pull", root=repo, timeout=60)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("update.readback_timeout", m("update.n_seconds", 5), "demo/bot"), result.stdout)
        self.assertIn(m("r.stub_missing"), result.stdout)
        self.assertEqual(len(self.sent()), 1)
        old = self.sent()[0]["body"]
        self.assertRegex(self.run_script("update.sh", "--no-pull", "--plan", root=repo).stdout,
                         r"routine prompt\s+changed")
        result = self.run_script("update.sh", "--no-pull", root=repo, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(m("update.prompt_later", "en"), result.stdout)
        self.assertNotIn("agent-mesh routine stub v1", result.stdout)
        self.assertEqual(len(self.sent()), 1)
        self.assertEqual(self.record()["prompt"]["missing"], 1)
        (self.who / "stub").write_text("ok")
        result = self.run_tty(repo, "--no-pull", answers=["y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("agent-mesh routine stub v1", result.stdout)
        self.assertLess(result.stdout.index("agent-mesh routine stub v1"), result.stdout.index(m("update.readback_heading", "demo/bot")))
        self.assertEqual(len(self.sent()), 2)
        self.assertRegex(self.sent()[-1]["body"], r"\Aagent-mesh stub [0-9a-f]{16}\nlang=en\Z")
        self.assertNotEqual(self.sent()[-1]["body"], old)
        prompt = self.record()["prompt"]
        self.assertEqual((prompt["state"], prompt["stub"]), ("confirmed", 1))
        for key in ("req", "sent_ms", "missing"):
            self.assertNotIn(key, prompt)

    def test_update_late_read_back_replies_are_checked_and_a_plan_takes_none(self):
        repo = self.installed()
        self.env["AGENT_MESH_REPLY_WAIT"] = "5"
        data = self.record()
        data["prompt"]["state"] = "pending"
        for key in ("req", "sent_ms", "stub"):
            data["prompt"].pop(key, None)
        self.write_record(data)
        (self.who / "stub").write_text("silent")
        self.assertEqual(self.run_script("update.sh", "--no-pull", root=repo, timeout=60).returncode, 1)
        req = self.record()["prompt"]["req"]
        self.put_reply(req, "agent-mesh-result request {req}\nagent-mesh-result lang zh-CN\n"
                            "agent-mesh-result stub 1\nagent-mesh-result DONE stub\n")
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertRegex(plan.stdout, r"routine prompt\s+changed")
        self.assertNotIn(m("state.stub_mismatch", "demo/bot"), plan.stdout)
        prompt = self.record()["prompt"]
        self.assertEqual((prompt["req"], prompt["missing"]), (req, 1))
        result = self.run_script("update.sh", "--no-pull", root=repo, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(m("r.prompt_later"), result.stdout)
        self.assertIn(m("state.stub_mismatch", "demo/bot"), result.stdout)
        self.assertIn(m("state.stub_lang", "zh-CN", "en"), result.stdout)
        prompt = self.record()["prompt"]
        self.assertEqual((prompt["state"], prompt["missing"]), ("pending", 1))
        self.assertNotIn("req", prompt)
        self.assertEqual(len(self.sent()), 1)
        (self.who / "stub").write_text("ok")
        result = self.run_tty(repo, "--no-pull", answers=["y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("agent-mesh routine stub v1", result.stdout)
        self.assertEqual(len(self.sent()), 2)
        self.assertEqual(self.record()["prompt"]["state"], "confirmed")

    def test_update_summary_separates_a_failed_check_from_failed_steps(self):
        repo = self.installed()
        (repo / "check.sh").write_text("#!/bin/bash\necho 'RESULT fix the FAIL lines above'\nexit 1\n")
        self.cloud_changed(repo)
        self.bot_replies(repo)
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("update.steps_done"), result.stdout)
        self.assertIn(m("update.check_failed_only"), result.stdout)
        self.assertNotIn(m("update.check_failed"), result.stdout)
        self.assertEqual(self.record()["cloud"]["hash"], self.digest(repo, "cloud"))
        self.cloud_changed(repo)
        self.env["AGENT_MESH_REPLY_WAIT"] = "5"
        self.bot_silent()
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("update.check_failed"), result.stdout)
        self.assertNotIn(m("update.steps_done"), result.stdout)

    def test_update_does_not_ask_grok_bot_without_a_running_relay_or_a_sent_request(self):
        repo = self.installed()
        self.cloud_changed(repo)
        self.bot_replies(repo)
        (self.state / "loaded").unlink()
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("the relay is not running", result.stderr)
        self.assertEqual(self.sent(), [])
        self.assertNotIn("request", self.record())
        (self.state / "loaded").write_text("")
        (self.who / "send-fail").write_text("")
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("could not send the update request to demo/bot", result.stderr)
        self.assertNotEqual(self.record()["cloud"]["hash"], self.digest(repo, "cloud"))

    def test_update_speaks_the_chosen_language_and_passes_it_on(self):
        import messages
        repo = self.installed()
        (repo / "check.sh").write_text('#!/bin/bash\necho "RESULT ok lang=$AGENT_MESH_LANG"\n')
        self.mark(repo, "prompt", "confirmed", "zh-CN")
        self.cloud_changed(repo)
        self.bot_replies(repo)
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(messages.t("update.sent", "demo/bot", lang="zh-CN"), result.stdout)
        self.assertIn(messages.t("update.waiting", messages.t("update.n_minutes", 15, lang="zh-CN"), lang="zh-CN"),
                      result.stdout)
        self.assertIn("RESULT ok lang=zh-CN", result.stdout)
        self.assertRegex(self.sent()[0]["body"], r"\Aagent-mesh update v")

    def test_installer_and_uninstaller_keep_the_recorded_language(self):
        import messages
        self.configured()
        self.assertEqual(self.run_script("install.sh", "--cloud-prompt").returncode, 0)
        self.assertEqual(self.run_script("install.sh", "--webhook", "--lang", "zh-CN").returncode, 0)
        self.assertEqual(self.record()["lang"], "zh-CN")
        result = self.run_script("install.sh", "--webhook")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.record()["lang"], "zh-CN")
        result = self.run_script("install.sh", "--help")
        self.assertEqual(result.stdout, messages.t("install.help", lang="zh-CN") + "\n")
        result = self.run_script("install.sh", "--name")
        self.assertEqual(result.returncode, 2)
        self.assertIn(messages.t("install.option_value", "--name", lang="zh-CN"), result.stderr)
        result = self.run_script("install.sh", "--bogus")
        self.assertEqual(result.returncode, 2)
        self.assertIn(messages.t("install.see_help", lang="zh-CN"), result.stderr)
        self.assertEqual(self.run_script("install.sh", "--help", "--lang", "en").stdout,
                         messages.t("install.help", lang="en") + "\n")
        result = self.run_script("uninstall.sh", "--plan", "--help")
        self.assertEqual(result.stdout, messages.t("uninstall.help", lang="zh-CN") + "\n")
        result = self.run_script("uninstall.sh", "--level", "nothing")
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr.startswith("uninstall："), result.stderr)
        self.assertEqual(self.record()["lang"], "zh-CN")

    def test_update_checks_the_release_before_pending_questions_when_a_cloud_part_changed(self):
        repo = self.installed()
        self.mark(repo, "cloud_engine", "pending")
        self.cloud_changed(repo)
        (self.git / "release-fail").write_text("")
        before = self.record()
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertRegex(plan.stdout, r"cloud release\s+failed")
        result = self.run_tty(repo, "--no-pull", answers=["y", "y", "y"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("could not be fetched", result.stderr)
        self.assertNotIn("Did Grok Bot finish the cloud setup", result.stdout)
        self.assertEqual(self.record(), before)
        self.assertEqual(self.sent(), [])

    def test_update_asks_grok_bot_when_only_the_update_or_setup_script_changed(self):
        for name, label in (("box/rn-cloud-update.sh", "cloud scripts"), ("box/rn-cloud-add-bot.sh", "cloud scripts"),
                            ("box/rn-cloud-setup.sh", "cloud setup")):
            with self.subTest(name):
                self.setUp()
                repo = self.installed()
                self.cloud_changed(repo, name)
                self.bot_replies(repo)
                plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
                self.assertRegex(plan.stdout, label + r"\s+changed")
                result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(len(self.sent()), 1)
                for field in ("cloud", "cloud_setup"):
                    self.assertEqual(self.record()[field]["hash"], self.digest(repo, field))

    def test_update_after_a_manual_cloud_step_asks_grok_bot_next_time(self):
        repo = self.installed()
        data = self.record()
        del data["cloud"]["release"]
        self.write_record(data)
        self.cloud_changed(repo)
        result = self.run_tty(repo, "--no-pull", "--manual", answers=["y", "y", *self.cloud_reply(repo), "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.record()["cloud"]["release"], (repo / "VERSION").read_text().strip())
        self.assertEqual(self.sent(), [])
        self.cloud_changed(repo, "box/rn-fetch.py")
        self.bot_replies(repo)
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        [message] = self.sent()
        self.assertTrue(message["body"].startswith("agent-mesh update v"), message["body"])

    def test_update_interrupted_while_waiting_for_grok_bot_keeps_the_request(self):
        repo = self.installed()
        self.cloud_changed(repo)
        self.tool("sleep", 'exec /bin/sleep "$@"')
        proc = subprocess.Popen(["/bin/bash", str(repo / "update.sh"), "--no-pull", "--yes"], env=self.env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                preexec_fn=os.setpgrp)
        deadline = time.time() + 60
        while time.time() < deadline and not self.sent():
            time.sleep(0.2)
        time.sleep(1)
        os.killpg(proc.pid, signal.SIGTERM)
        out, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 143, out.decode() + err.decode())
        self.assertIn("cloud scripts", out.decode())
        req = self.record()["request"]["req"]
        self.assertFalse((self.home / ".local/lib/agent-mesh/update.lock").exists())
        self.assertTrue(self.loaded())
        self.tool("sleep", "exit 0")
        self.put_reply(req, self.good_reply(repo))
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("reply to the earlier request arrived and matches", result.stdout)
        self.assertEqual(len(self.sent()), 1)

    def test_update_pulls_once_and_hands_over_to_the_new_script(self):
        repo = self.installed()
        (self.git / "dirty").write_text(" M install.sh\n")
        result = self.run_script("update.sh", root=repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn("uncommitted changes", result.stderr)
        self.assertNotIn("git fetch", self.calls())
        (self.git / "dirty").unlink()
        (self.git / "fetch-fail").write_text("")
        result = self.run_script("update.sh", root=repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn("fetching the upstream branch and its release tags failed", result.stderr)
        (self.git / "fetch-fail").unlink()
        commits = {name: c * 40 for name, c in zip(("v0.9.1", "v0.9.10", "v0.10.0", "tip"), "bcde")}
        (self.git / "history").write_text("\n".join(["a" * 40, *commits.values()]) + "\n")
        (self.git / "tags").write_text("v0.10.0-rc1 " + "f" * 40 + "\nv1.0 " + "f" * 40 + "\n"
                                       + "".join(f"{name} {sha}\n" for name, sha in commits.items() if name != "tip"))
        (self.git / "merge-fail").write_text("")
        result = self.run_script("update.sh", root=repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn("git merge --ff-only to release v0.10.0 failed", result.stderr)
        (self.git / "merge-fail").unlink()
        newer = self.base / "newer-update.sh"
        newer.write_text('#!/bin/bash\nfor a in "$@"; do echo "NEW-SCRIPT [$a]"; done\n')
        (self.git / "pull.sh").write_text(f"cp '{newer}' '{repo}/update.sh'\n")
        (self.base / "calls").unlink()
        result = self.run_script("update.sh", "--lang", "zh-CN", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("NEW-SCRIPT [--no-pull]\nNEW-SCRIPT [--lang]\nNEW-SCRIPT [zh-CN]", result.stdout)
        self.assertIn(f"git merge --ff-only -q {commits['v0.10.0']}\n", self.calls())
        self.assertEqual(self.calls().count("git merge --ff-only"), 1)
        self.assertEqual((self.git / "head").read_text().strip(), commits["v0.10.0"])
        self.assertIn("发布版本 v0.10.0", result.stdout)
        self.assertFalse((self.home / ".local/lib/agent-mesh/update.lock").exists())

    def test_update_moves_only_to_a_newer_release_on_the_upstream_branch(self):
        repo = self.installed()
        (repo / "update.sh").write_text((repo / "update.sh").read_text().replace(
            'exec "$BASH" "$HERE/update.sh" --no-pull "$@"', 'echo HANDOVER; exit 0'))
        old, release = "a" * 40, "c" * 40
        (self.git / "tags").write_text(f"v0.9.0 {old}\nnightly {release}\nv0.9.0.1 {release}\n")
        result = self.run_script("update.sh", "--plan", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("already contains the latest release v0.9.0", result.stdout)
        self.assertNotIn("git merge --ff-only", self.calls())
        (self.git / "tags").write_text("nightly " + release + "\n")
        result = self.run_script("update.sh", "--plan", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("has no release tag vX.Y.Z", result.stdout)
        (self.git / "tags").write_text(f"v0.9.0 {old}\nv0.9.1 {release}\n")
        (self.git / "history").write_text("b" * 40 + "\n" + release + "\n")
        result = self.run_script("update.sh", "--plan", root=repo)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("commits that are not in the latest release v0.9.1", result.stderr)
        self.assertIn("./update.sh --no-pull --lang en --plan", result.stderr)
        (self.git / "history").write_text(old + "\n" + "b" * 40 + "\n" + release + "\n")
        (self.git / "head").write_text("b" * 40 + "\n")
        result = self.run_script("update.sh", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"moved the checkout {'b' * 12}..{'c' * 12} (release v0.9.1)\nHANDOVER", result.stdout)
        result = self.run_script("update.sh", "--plan", root=repo)
        self.assertIn("already contains the latest release v0.9.1", result.stdout)
        self.assertNotIn("HANDOVER", result.stdout)
        self.assertEqual(self.calls().count("git merge --ff-only"), 1)

    def test_update_writes_the_agent_mesh_command_for_this_checkout(self):
        repo = self.installed()
        entry = self.home / ".local/bin/agent-mesh"
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertFalse(entry.exists())
        self.assertNotIn("checkout", self.record())
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(os.access(entry, os.X_OK))
        self.assertIn("# agent-mesh-entry v1", entry.read_text())
        self.assertEqual(self.record()["checkout"], str(repo.resolve()))
        self.assertIn(f"{entry.parent} is not in PATH", result.stdout)

        def run(*args):
            return subprocess.run([str(entry), *args], env=self.env, capture_output=True, text=True, timeout=60)

        (repo / "check.sh").write_text('#!/bin/bash\nfor a in "$@"; do echo "CHECK [$a]"; done\n')
        self.assertEqual(run("check", "--lang", "zh-CN", "a b").stdout, "CHECK [--lang]\nCHECK [zh-CN]\nCHECK [a b]\n")
        for args in ((), ("rename",), ("--help",)):
            bad = run(*args)
            self.assertEqual((bad.returncode, bad.stdout), (2, ""), args)
            self.assertIn("usage: agent-mesh update|check", bad.stderr)
        moved = self.base / "moved repo"
        repo.rename(moved)
        gone = run("update", "--plan")
        self.assertEqual(gone.returncode, 1)
        self.assertIn(f"the recorded checkout {repo.resolve()} is gone; run ./update.sh once", gone.stderr)
        self.env["PATH"] = f"{entry.parent}{os.pathsep}{self.env['PATH']}"
        result = self.run_script("update.sh", "--no-pull", "--yes", root=moved)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.record()["checkout"], str(moved.resolve()))
        self.assertNotIn("PATH", result.stdout)
        self.assertEqual(run("check").stdout, "")
        entry.write_text("#!/bin/sh\necho mine\n")
        result = self.run_script("update.sh", "--no-pull", "--yes", root=moved)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"{entry} exists and was not written by agent-mesh; left it as it is", result.stdout)
        self.assertEqual(entry.read_text(), "#!/bin/sh\necho mine\n")

    def test_update_lists_the_changed_files_and_offers_to_use_the_checkout_as_it_is(self):
        repo = self.installed()
        names = [f" M file-{i:02d}.md" for i in range(1, 26)]
        (self.git / "dirty").write_text("\n".join(names) + "\n")
        result = self.run_script("update.sh", "--lang", "en", root=repo)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("tracked files have uncommitted changes:\n   M file-01.md\n", result.stderr)
        self.assertIn("   M file-20.md\n  ... 25 files in total\n", result.stderr)
        self.assertNotIn("file-21.md", result.stderr)
        self.assertIn("or use this checkout as it is: ./update.sh --no-pull --lang en\n", result.stderr)
        (self.git / "dirty").write_text(" M install.sh\nM  notes/a b.md\n")
        result = self.run_tty(repo, "--yes", "--plan", "--manual", "--repo-url", "https://example.invalid/fork.git")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("   M install.sh\n  M  notes/a b.md\n", result.stderr)
        self.assertNotIn("files in total", result.stderr)
        self.assertIn("./update.sh --no-pull --lang en --yes --plan --manual --repo-url "
                      "https://example.invalid/fork.git\n", result.stderr)
        result = self.run_script("update.sh", "--lang", "en", "--repo-url", "https://example.invalid/my fork.git;x",
                                 root=repo)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("./update.sh --no-pull --lang en --repo-url https://example.invalid/my\\ fork.git\\;x\n",
                      result.stderr)
        result = self.run_tty(repo, answers=("n",))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Continue with this checkout as it is (the same as --no-pull)?", result.stdout + result.stderr)
        self.assertIn("stopped; nothing was changed", result.stderr)
        self.assertEqual(self.service_changes(), [])
        with open(repo / "agent_relay.py", "a") as stream:
            stream.write("\n")
        result = self.run_tty(repo, answers=("y", "y"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"relay\s+updated")
        self.assertEqual(self.relay_files()[0].read_bytes(), (repo / "agent_relay.py").read_bytes())
        self.assertNotIn("git pull", self.calls())
        result = self.run_script("update.sh", "--lang", "zh-CN", root=repo)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("./update.sh --no-pull --lang zh-CN\n", result.stderr)

    def test_update_from_a_release_without_the_relay_python_changes_nothing_and_records_it(self):
        repo = self.installed()
        python = fake_python(self.base / "old tools/python3")
        before = self.as_v090(python)
        self.assertNotIn("relay_python", self.record())
        record = (self.home / ".local/lib/agent-mesh/deployed.json").read_bytes()
        for args in (("--no-pull", "--plan"), ("--plan",)):
            plan = self.run_script("update.sh", *args, root=repo)
            self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
            self.assertRegex(plan.stdout, r"relay\s+unchanged")
            self.assertIn(f"relay Python: {python} is in a virtual environment or a temporary directory; it is kept. "
                          "To move the relay: ./install.sh --python <path>", plan.stdout)
            self.assertEqual((self.home / ".local/lib/agent-mesh/deployed.json").read_bytes(), record)
        other = self.base / "newer tools"
        other.mkdir()
        (other / "python3").symlink_to(BASE_PYTHON)
        self.env["PATH"] = f"{other}:{self.env['PATH']}"
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("already up to date", result.stdout)
        self.assertEqual(self.relay_files()[1].read_bytes(), before)
        self.assertEqual(self.plist_python(), str(python))
        self.assertEqual(self.service_changes(), [])
        self.assertNotIn("rn send", self.calls())
        self.assertNotIn("launchctl kickstart", self.calls())
        self.assertEqual(self.record()["relay_python"], str(python))
        self.assertEqual(self.relay_python(), {"python": str(python), "source": "recorded"})
        with open(repo / "agent_relay.py", "a") as stream:
            stream.write("\n")
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"relay\s+updated")
        self.assertEqual(self.relay_files()[1].read_bytes(), before)
        self.assertEqual(self.record()["relay_python"], str(python))

    def test_update_moves_the_relay_when_its_python_is_gone(self):
        repo = self.installed()
        python = fake_python(self.base / "old tools/python3")
        self.as_v090(python)
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.record()["relay_python"], str(python))
        python.unlink()
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertRegex(plan.stdout, r"relay\s+changed")
        self.assertIn(f"relay Python: {python} is missing or no longer works; the relay moves to {BASE_PYTHON} "
                      "and restarts", plan.stdout)
        self.assertEqual(self.record()["relay_python"], str(python))
        self.assertEqual(self.service_changes(), [])
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"relay\s+updated")
        self.assertEqual(self.plist_python(), BASE_PYTHON)
        self.assertEqual(self.record()["relay_python"], BASE_PYTHON)
        self.assertTrue(self.loaded())
        again = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertIn("already up to date", again.stdout)
        self.assertNotIn("relay Python", again.stdout)
        (self.base / "calls").unlink()
        self.env["AGENT_MESH_PYTHON_CANDIDATES"] = ""
        before = self.as_v090(python)
        result = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("no Python for the relay was found; choose one with ./install.sh --python <path>", result.stderr)
        self.assertEqual(self.service_changes(), [])
        self.assertEqual(self.relay_files()[1].read_bytes(), before)
        self.assertNotIn("relay_python", self.record())

    def test_update_stops_when_the_engine_is_newer_or_unknown(self):
        repo = self.installed()
        for version in ("0.13.0", "garbage"):
            self.env["MOCK_VERSION"] = version
            result = self.run_script("update.sh", "--no-pull", root=repo)
            self.assertEqual(result.returncode, 1)
            self.assertIn("never downgrades or replaces a newer or unrecognised engine", result.stderr)
            self.assertEqual(self.service_changes(), [])

    def test_update_replaces_only_the_dsh_plugin_file(self):
        repo = self.installed()
        app = self.home / "Applications/DeepSeek Harness.app/Contents/MacOS/DeepSeek Harness"
        self.tool("app", "echo 'ℹ pass 7'", app)
        patch = self.home / ".dsh/cordis.patch.yml"
        patch.parent.mkdir(parents=True)
        patch.write_text("# === reason-network MCP BEGIN ===\nold\n# === reason-network MCP END ===\n")
        self.assertEqual(self.run_script("dsh/mac-install.sh", "demo", root=repo).returncode, 0)
        patch.write_text(patch.read_text().replace("        allowPeople:", "        pollMs: 2000\n        allowPeople:"))
        before = patch.read_bytes()
        with open(repo / "dsh/reason-network-bridge.mjs", "a") as stream:
            stream.write("\n")
        result = self.run_script("update.sh", "--no-pull", root=repo, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        plugin = self.home / ".dsh/plugins/reason-network-bridge.mjs"
        self.assertEqual(plugin.read_bytes(), (repo / "dsh/reason-network-bridge.mjs").read_bytes())
        self.assertEqual(patch.read_bytes(), before)
        self.assertRegex(result.stdout, r"dsh plugin\s+updated")
        self.assertEqual(self.service_changes(), [])

    def test_dsh_installer_keeps_the_previous_plugin_when_it_fails(self):
        patch, _, plugin = self.dsh_installed()
        plugin.write_bytes(b"previous plugin")
        app = self.home / "Applications/DeepSeek Harness.app/Contents/MacOS/DeepSeek Harness"
        self.tool("app", "echo 'ℹ fail 1'; exit 1", app)
        result = self.run_script("dsh/mac-install.sh", "--plugin-only")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(plugin.read_bytes(), b"previous plugin")
        self.tool("app", "echo 'ℹ pass 7'", app)
        begin = "# === reason-network-bridge BEGIN (replaces reason-network's MCP block) ===\n"
        broken = begin + "- extra: 0\n" + patch.read_text()
        patch.write_text(broken)
        result = self.run_script("dsh/mac-install.sh", "demo")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(plugin.read_bytes(), b"previous plugin")
        self.assertEqual(patch.read_text(), broken)
        result = self.run_script("dsh/mac-install.sh", "--plugin-only")
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not hold exactly one bridge block", result.stdout)
        self.assertEqual(plugin.read_bytes(), b"previous plugin")
        self.assertEqual(list(plugin.parent.glob(".reason-network-bridge.mjs.*")), [])


class MultiBotUpdate(AddSupport, unittest.TestCase):
    """update.sh with two Bots: one cloud request, per-Bot prompts, guided steps for all of them."""

    def setUp(self):
        super().setUp()
        self.repo_dir = self.two_ready()
        self.added(self.repo_dir)
        (self.who / "sent").unlink(missing_ok=True)
        self.labels = [LEGACY_LABEL, f"{LEGACY_LABEL}.beta"]

    def launchctl(self, *args):
        subprocess.run([str(self.bin / "launchctl"), *args], env=self.env, check=True)

    def update(self, *args, timeout=60):
        return self.run_script("update.sh", "--no-pull", *args, root=self.repo_dir, timeout=timeout)

    def prompt_hash(self, lang, name=None):
        args = ["digest", "prompt", "--lang", lang] + (["--relay-name", name] if name else [])
        return self.state_cmd(*args, root=self.repo_dir).stdout.strip()

    def test_the_cloud_request_goes_to_the_first_bot_and_its_reply_is_read_from_there(self):
        self.cloud_changed(self.repo_dir)
        self.bot_replies(self.repo_dir, sender="demo/alpha")
        plan = self.update("--plan")
        self.assertIn(m("update.plan_bot", "alpha", "unchanged", "unchanged"), plan.stdout)
        self.assertIn(m("update.plan_bot", "beta", "unchanged", "unchanged"), plan.stdout)
        result = self.update("--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        [message] = self.sent()
        self.assertEqual(message["to"], "demo/alpha")
        self.assertEqual(self.record()["cloud"]["hash"], self.digest(self.repo_dir, "cloud"))
        self.assertNotIn("request", self.record())
        self.assertEqual(self.service_changes(), [])

    def test_the_cloud_request_goes_to_the_next_bot_when_the_first_relay_is_stopped(self):
        self.cloud_changed(self.repo_dir)
        self.launchctl("bootout", f"gui/501/{LEGACY_LABEL}")
        self.bot_silent()
        self.env["AGENT_MESH_REPLY_WAIT"] = "5"
        result = self.update("--yes")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        [message] = self.sent()
        self.assertEqual(message["to"], "demo/beta")
        self.assertIn(m("update.timeout", m("update.n_seconds", 5)), result.stdout)
        request = self.record()["request"]
        self.assertEqual(request["address"], "demo/beta")
        self.put_reply(request["req"], self.good_reply(self.repo_dir), sender="demo/beta")
        result = self.update("--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(m("update.cloud_earlier_reply"), result.stdout)
        self.assertEqual(len(self.sent()), 1)
        self.assertEqual(self.record()["cloud"]["hash"], self.digest(self.repo_dir, "cloud"))

    def test_without_a_bot_that_can_take_the_request_nothing_is_sent(self):
        self.cloud_changed(self.repo_dir)
        for label in self.labels:
            self.launchctl("bootout", f"gui/501/{label}")
        result = self.update("--yes")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("update.no_target"), result.stderr)
        self.assertEqual(self.sent(), [])
        self.assertNotIn("request", self.record())
        self.launchctl("bootstrap", "gui/501", str(self.legacy_plist()))
        (self.home / "Library/Application Support/agent-relay/registered").unlink()
        result = self.update("--yes")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("update.no_target"), result.stderr)
        self.assertEqual(self.sent(), [])

    def test_an_old_request_without_an_address_is_answered_on_the_first_bot(self):
        self.cloud_changed(self.repo_dir)
        self.bot_silent()
        self.env["AGENT_MESH_REPLY_WAIT"] = "5"
        self.assertEqual(self.update("--yes").returncode, 1)
        data = self.record()
        self.assertEqual(data["request"].pop("address"), "demo/alpha")
        req = data["request"]["req"]
        self.write_record(data)
        self.put_reply(req, self.good_reply(self.repo_dir), sender="demo/alpha")
        result = self.update("--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(m("update.cloud_earlier_reply"), result.stdout)
        self.assertEqual(len(self.sent()), 1)

    def test_a_reply_to_an_old_request_does_not_confirm_a_moved_checkout(self):
        self.cloud_changed(self.repo_dir)
        self.bot_silent()
        self.env["AGENT_MESH_REPLY_WAIT"] = "5"
        self.assertEqual(self.update("--yes").returncode, 1)
        old = self.record()["request"]["req"]
        self.put_reply(old, self.good_reply(self.repo_dir), sender="demo/alpha")
        self.cloud_changed(self.repo_dir)
        result = self.update("--yes")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(len(self.sent()), 2)
        self.assertNotEqual(self.record()["request"]["req"], old)
        self.assertNotEqual(self.record()["cloud"]["hash"], self.digest(self.repo_dir, "cloud"))

    def old_stubs(self):
        data = self.record()
        for item in (data, data["bots"]["beta"]):
            item["prompt"].update(hash="0" * 64, text="0" * 64)
        self.write_record(data)

    def test_each_bot_confirms_its_own_prompt_and_a_rerun_shows_only_the_unconfirmed_one(self):
        self.old_stubs()
        result = self.run_tty(self.repo_dir, "--no-pull", "--lang", "zh-CN", answers=["y", "y", "n"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        zh = lambda key, *a: messages.t(key, *a, lang="zh-CN")
        self.assertIn(zh("update.prompt_replace_bot", "alpha", "zh-CN"), result.stdout)
        self.assertIn(zh("update.prompt_replace_bot", "beta", "zh-CN"), result.stdout)
        self.assertIn(zh("update.bot_prompt_not_recorded", "beta"), result.stderr)
        data = self.record()
        self.assertEqual((data["prompt"]["hash"], data["prompt"]["state"]), (self.prompt_hash("zh-CN"), "pending"))
        self.assertEqual(data["bots"]["beta"]["prompt"]["hash"], "0" * 64)
        self.assertEqual(self.sent(), [])
        result = self.run_tty(self.repo_dir, "--no-pull", "--lang", "zh-CN", answers=["y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(zh("update.prompt_replace_bot", "alpha", "zh-CN"), result.stdout)
        self.assertIn(zh("update.prompt_replace_bot", "beta", "zh-CN"), result.stdout)
        self.assertEqual(sorted(message["to"] for message in self.sent()), ["demo/alpha", "demo/beta"])
        data = self.record()
        for item, name in ((data, None), (data["bots"]["beta"], "beta")):
            self.assertEqual((item["prompt"]["hash"], item["prompt"]["state"], item["prompt"]["stub"]),
                             (self.prompt_hash("zh-CN", name), "confirmed", 1))
        self.assertEqual(self.service_changes(), [])
        again = self.update("--lang", "zh-CN")
        self.assertIn(zh("update.up_to_date"), again.stdout)

    def test_a_language_change_is_only_read_back_by_each_bot(self):
        result = self.run_tty(self.repo_dir, "--no-pull", "--lang", "zh-CN", answers=["y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(messages.t("update.prompt_replace_bot", "alpha", "zh-CN", lang="zh-CN"), result.stdout)
        self.assertEqual(sorted((message["to"], message["body"].split("\n")[1]) for message in self.sent()),
                         [("demo/alpha", "lang=zh-CN"), ("demo/beta", "lang=zh-CN")])
        data = self.record()
        for item, name in ((data, None), (data["bots"]["beta"], "beta")):
            self.assertEqual((item["prompt"]["hash"], item["prompt"]["state"]),
                             (self.prompt_hash("zh-CN", name), "confirmed"))

    def test_a_language_change_needs_no_terminal(self):
        plan = self.update("--plan", "--lang", "zh-CN")
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertIn(messages.t("update.plan_bot", "alpha", messages.t("st.unchanged", lang="zh-CN"),
                                 messages.t("st.readback", lang="zh-CN"), lang="zh-CN"), plan.stdout)
        result = self.update("--yes", "--lang", "zh-CN")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sorted((message["to"], message["body"].split("\n")[1]) for message in self.sent()),
                         [("demo/alpha", "lang=zh-CN"), ("demo/beta", "lang=zh-CN")])
        data = self.record()
        for item, name in ((data, None), (data["bots"]["beta"], "beta")):
            self.assertEqual((item["prompt"]["hash"], item["prompt"]["state"]),
                             (self.prompt_hash("zh-CN", name), "confirmed"))

    def test_a_pending_stub_is_read_back_only_by_its_bot(self):
        data = self.record()
        data["bots"]["beta"]["prompt"]["state"] = "pending"
        self.write_record(data)
        result = self.run_tty(self.repo_dir, "--no-pull", answers=["y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(m("update.prompt_replace_bot", "beta", "en"), result.stdout)
        [message] = self.sent()
        self.assertEqual(message["to"], "demo/beta")
        self.assertRegex(message["body"], r"\Aagent-mesh stub [0-9a-f]{16}\nlang=en\Z")
        self.assertEqual(self.record()["bots"]["beta"]["prompt"]["state"], "confirmed")

    def test_a_pending_add_is_confirmed_from_its_reply_without_asking(self):
        repo = self.repo_dir
        self.registers_on_wake("gamma")
        self.bot_silent()
        result = self.add(repo, grok="Gamma", address="gamma")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.bot("gamma")["cloud"]["state"], "pending")
        plan = self.update("--plan")
        self.assertIn(m("update.plan_bot_waiting", "gamma", "gamma"), plan.stdout)
        self.assertEqual(self.bot("gamma")["cloud"]["state"], "pending")
        result = self.update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(m("update.plan_bot_waiting", "gamma", "gamma"), result.stdout)
        self.assertEqual(self.bot("gamma")["cloud"]["state"], "pending")
        req = self.bot("gamma")["cloud"]["req"]
        sent = len(self.sent())
        self.put_reply(req, f"agent-mesh-result request {{req}}\nagent-mesh-result add-bot {{req}} ok gamma {TEAM_HASH}\n"
                            "agent-mesh-result lang en\nagent-mesh-result stub 1\nagent-mesh-result DONE add-bot\n",
                       sender="demo/gamma")
        result = self.update()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(m("state.add_confirmed", "demo/gamma"), result.stdout)
        self.assertNotIn(m("update.plan_bot_waiting", "gamma", "gamma"), result.stdout)
        self.assertEqual(self.bot("gamma")["cloud"]["state"], "confirmed")
        self.assertEqual(self.bot("gamma")["prompt"]["state"], "confirmed")
        self.assertEqual(len(self.sent()), sent)

    def test_a_bot_being_removed_stops_the_update(self):
        data = self.record()
        data["removing"] = {"name": "beta", "kind": "added", "step": "stopped"}
        self.write_record(data)
        result = self.update("--plan")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("update.bot_removing", "beta", "beta"), result.stderr)
        self.assertEqual(self.service_changes(), [])

    def test_the_guided_flow_stops_all_relays_and_pauses_and_resumes_every_routine(self):
        self.old_stubs()
        result = self.run_tty(self.repo_dir, "--no-pull", "--manual", "--lang", "zh-CN", answers=["y", "y", "y", "y", "n"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        zh = lambda key, *a: messages.t(key, *a, lang="zh-CN")
        self.assertIn(zh("update.g_stop_relays", "alpha, beta"), result.stdout)
        self.assertIn(zh("update.g_pause_all", "alpha, beta"), result.stderr)
        self.assertIn(zh("update.exit_paused_bots", "alpha, beta"), result.stdout)
        self.assertIn(zh("update.bot_running_again", "alpha"), result.stdout)
        self.assertIn(zh("update.bot_running_again", "beta"), result.stdout)
        self.assertEqual(sorted(self.multi_loaded()), self.labels)
        txn = self.record()["txn"]
        self.assertEqual(txn["stage"], "prompt_done")
        self.assertEqual([(p["name"], p["label"], p["was_loaded"]) for p in txn["participants"]],
                         [("alpha", self.labels[0], True), ("beta", self.labels[1], True)])
        self.assertEqual(self.record()["bots"]["beta"]["prompt"]["state"], "pending")
        self.assertEqual(self.sent(), [])
        before = len(self.multi_started())
        result = self.run_tty(self.repo_dir, "--no-pull", "--lang", "zh-CN", answers=["y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(zh("update.g_resume_all", "alpha, beta"), result.stderr)
        self.assertNotIn(zh("update.prompt_replace_bot", "alpha", "zh-CN"), result.stdout)
        self.assertEqual([label for label, _ in self.multi_started()[before:]], self.labels)
        self.assertEqual(sorted(self.multi_loaded()), self.labels)
        self.assertNotIn("txn", self.record())
        self.assertEqual(sorted(message["to"] for message in self.sent()), ["demo/alpha", "demo/beta"])
        self.assertEqual(self.record()["bots"]["beta"]["prompt"]["state"], "confirmed")
