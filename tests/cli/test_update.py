import os
import shutil
import signal
import subprocess
import sys
import time
import unittest

from render_prompt import ROOT
from tests.cli.install_support import (
    InstallHarness,
    rn_engine,
)


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
        repo = self.installed(record=False)
        result = self.run_tty(repo, "--no-pull", "--manual", answers=["y", "y", *self.cloud_reply(repo), "y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.sent(), [])
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
        rest = (f"{r} sum {self.sha(repo / 'box/rn-cloud-setup.sh')}  box/rn-cloud-setup.sh\n"
                f"{r} sum {self.sha(repo / 'box/rn-engine.py')}  box/rn-engine.py\n{r} DONE {tag}\n")
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
        self.assertEqual(self.service_changes(), [])
        self.assertEqual(self.sent(), [])
        data["prompt"]["state"] = "pending"
        self.write_record(data)
        result = self.run_tty(repo, "--no-pull", answers=["n", "y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Replace the prompt saved in the routine", result.stdout)
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
                self.assertIn("No reply from Grok Bot within 15 minutes", result.stdout)
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
        self.assertIn("bash -s <<'AGENT_MESH_UPDATE'", body)
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
        self.assertIn("bash -s <<'AGENT_MESH_UPDATE'", body)
        self.assertIn("b" * 40, body)
        self.assertNotIn(old, body)
        self.assertIn("v9.9.9", body)
        self.assertEqual(self.record()["cloud"]["release"], "9.9.9")

    def test_update_has_the_prompt_pasted_before_it_asks_grok_bot(self):
        repo = self.installed()
        prompt = repo / "prompts/routine.md"
        text = prompt.read_text()
        fence = text.index("```\n") + 4
        prompt.write_text(text[:fence] + "Changed. " + text[fence:])
        self.cloud_changed(repo)
        self.bot_replies(repo)
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        result = self.run_tty(repo, "--no-pull", answers=["y", "n"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("You will paste a new routine prompt into Grok", result.stdout)
        self.assertIn("the routine prompt was not recorded as updated", result.stderr)
        self.assertEqual(self.sent(), [])
        result = self.run_tty(repo, "--no-pull", answers=["y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertLess(result.stdout.index("Changed. "), result.stdout.index("Sent the update request to Grok Bot"))
        data = self.record()
        self.assertEqual(data["prompt"]["state"], "confirmed")
        self.assertEqual(data["cloud"]["hash"], self.digest(repo, "cloud"))
        self.assertEqual(len(self.sent()), 1)

    def test_update_from_a_record_without_the_release_sends_the_checked_block(self):
        repo = self.installed()
        version = (repo / "VERSION").read_text().strip()
        data = self.record()
        del data["cloud"]["release"]
        self.write_record(data)
        self.cloud_changed(repo)
        self.bot_replies(repo)
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        [message] = self.sent()
        body = message["body"]
        self.assertIn("bash -s <<'AGENT_MESH_UPDATE'", body)
        self.assertRegex(body, rf"TAG=v{version}; COMMIT={'a' * 40}; REQ=[0-9a-f]{{16}}\n")
        self.assertIn(f"{self.sha(repo / 'box/rn-fetch.py')}  box/rn-fetch.py", body)
        self.assertIn('exec bash "$A/box/rn-cloud-update.sh" --after-checkout "$TAG" "$COMMIT" "$REQ"', body)
        self.assertIn("This first time, Grok Bot does not run it by itself: it tells you in Grok", result.stdout)
        self.assertEqual(self.record()["cloud"]["release"], version)

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
        self.assertIn(messages.t("update.waiting", lang="zh-CN"), result.stdout)
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
        for name, label in (("box/rn-cloud-update.sh", "cloud scripts"), ("box/rn-cloud-setup.sh", "cloud setup")):
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
        self.assertNotIn("git pull", self.calls())
        (self.git / "dirty").unlink()
        (self.git / "pull-fail").write_text("")
        result = self.run_script("update.sh", root=repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn("git pull --ff-only failed", result.stderr)
        (self.git / "pull-fail").unlink()
        newer = self.base / "newer-update.sh"
        newer.write_text('#!/bin/bash\nfor a in "$@"; do echo "NEW-SCRIPT [$a]"; done\n')
        (self.git / "pull.sh").write_text(f"cp '{newer}' '{repo}/update.sh'\n")
        (self.git / "upstream").write_text("b" * 40 + "\n")
        (self.base / "calls").unlink()
        result = self.run_script("update.sh", "--lang", "zh-CN", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("NEW-SCRIPT [--no-pull]\nNEW-SCRIPT [--lang]\nNEW-SCRIPT [zh-CN]", result.stdout)
        self.assertEqual(self.calls().count("git pull"), 1)
        self.assertFalse((self.home / ".local/lib/agent-mesh/update.lock").exists())

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
