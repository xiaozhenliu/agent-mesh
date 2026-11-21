import json
import os
import time
import unittest

from render_prompt import ROOT
from tests.cli.install_support import (
    BASE_PYTHON,
    EMPTY_DIRECTORY,
    InstallHarness,
    fake_python,
)
from tests.cli.test_install_add import AddSupport


class Check(InstallHarness, unittest.TestCase):
    def test_check_reports_the_relay_name_and_its_registration(self):
        self.configured(relay="alice")
        (self.state / "loaded").write_text("")
        (self.state / "pid").write_text(str(os.getpid()))
        text = self.who / "text"
        now = int(time.time() * 1000)

        def check():
            return self.run_script("check.sh").stdout

        out = check()
        self.assertIn("PASS  Bot address: demo/alice (no Grok name recorded;", out)
        self.assertIn("FAIL  relay not registered on the running engine: no engine daemon is running", out)
        self.fake_daemon()
        self.assertIn("SKIP  relay registration (the engine daemon or the relay restarted", check())
        time.sleep(1.1)
        marker = self.write_marker("demo/alice")
        text.write_text("  alice  cli  ~/work  idle\n  repo-claude  claude-code  ~/repo  idle\n")
        out = check()
        self.assertIn("PASS  relay listening as demo/alice (registered on the running engine daemon)", out)
        self.assertIn("PASS  1 agent session(s) on the roster", out)
        self.write_marker("demo/alice-2")
        self.assertIn("FAIL  relay not registered on the running engine: the relay registered as demo/alice-2, "
                      "not demo/alice", check())
        marker.write_text("{")
        self.assertIn("is unreadable", check())
        self.write_marker("demo/alice")
        db = self.sessions([("alice", "cli:alice", now)])
        self.assertIn("PASS  relay listening as demo/alice", check())
        for who in ("  alice  cli  ~/work  idle\n", ""):
            text.write_text(who)
            self.sessions([("alice-2", "cli:alice", now + 1)])
            self.assertIn("FAIL  relay registered under another name: demo/alice-2", check(), who)
        db.unlink()
        self.assertIn("PASS  relay listening as demo/alice", check())
        (self.state / "dead").write_text("")
        self.assertIn("FAIL  relay not registered on the running engine: the relay service has no running process", check())
        (self.state / "dead").unlink()
        self.write_marker("demo/alice", pid=os.getppid())
        self.assertNotIn("PASS  relay listening", check())
        self.write_marker("demo/alice")
        env = self.env_file()
        env.write_text(env.read_text() + "LISTEN_NAME=x\n")
        out = check()
        self.assertIn("FAIL  relay names in env are not derived from one name", out)
        self.assertNotIn("0123456789", out)
        env.unlink()
        out = check()
        self.assertIn("SKIP  Bot address (no address name chosen yet", out)
        self.assertIn("SKIP  relay registration (the Bot address name is not known)", out)
        self.write_record({"schema": 1, "relay_name": "alice", "grok_name": "Alice"})
        out = check()
        self.assertIn("PASS  Bot address: demo/alice → Grok: Alice", out)
        self.assertIn("relay listening as demo/alice", out)
        env.write_text(f"RN_BIN={self.home}/.local/bin/reason-network\n")
        self.write_record({"schema": 1, "relay_name": "bot", "grok_name": "Nova"})
        self.assertIn("PASS  Bot address: demo/bot → Grok: Nova", check())

    def test_check_reads_team_lists_with_and_without_verified_names(self):
        self.configured()
        team = self.who / "team"
        head = "team demo · relay: Reason's (the default)\n"
        for v in ("", " (verified)"):
            with self.subTest(v=v):
                team.write_text(f"{head}  demo{v}  mac    this machine\n  demo{v}  cloud  direct 4ms\n")
                out = self.run_script("check.sh").stdout
                self.assertIn("PASS  cloud machine joined", out)
                self.assertIn("PASS  team holds only this machine and cloud", out)
                team.write_text(f"{head}  demo{v}  mac    this machine\n  demo{v}  cloud  direct 4ms\n"
                                f"  eve{v}   pc     offline 1h\n")
                self.assertIn("FAIL  unexpected team members: eve/pc", self.run_script("check.sh").stdout)
                team.write_text(f"{head}  demo{v}  mac    this machine\n")
                self.assertIn("SKIP  cloud machine", self.run_script("check.sh").stdout)

    def test_check_language_follows_the_flag_then_the_record_then_the_environment(self):
        self.configured(relay="alice")
        english = "PASS  Bot address: demo/alice (no Grok name recorded;"
        chinese = "PASS  Bot 地址：demo/alice（没有记录 Grok 名字；"

        def check(*args, lang=None):
            self.env.pop("AGENT_MESH_LANG", None)
            if lang is not None:
                self.env["AGENT_MESH_LANG"] = lang
            return self.run_script("check.sh", *args)

        for args, lang, expected in (((), None, english), ((), "fr", english), ((), "zh-CN", chinese),
                                     (("--lang", "en"), "zh-CN", english), (("--lang=zh-CN",), "en", chinese)):
            out = check(*args, lang=lang).stdout
            self.assertIn(expected, out, (args, lang))
            self.assertIn("RESULT fix the FAIL lines above" if expected == english else "RESULT 请修复上面的 FAIL 行", out)
        zh = check(lang="zh-CN").stdout
        self.assertIn("FAIL  relay 没有在运行中的引擎上注册：", zh)
        self.assertNotIn("PASS  Bot address", zh)
        self.mark(ROOT, "prompt", "confirmed", "zh-CN")
        self.assertIn(chinese, check(lang="en").stdout)
        self.assertIn(english, check("--lang", "en", lang="zh-CN").stdout)
        self.mark(ROOT, "prompt", "confirmed", "en")
        self.assertIn(english, check(lang="zh-CN").stdout)
        bad = check("--lang", "fr")
        self.assertEqual(bad.returncode, 2)
        self.assertEqual(bad.stdout, "")
        self.assertIn("check: unsupported language 'fr'; choose en or zh-CN / check：不支持的语言“fr”", bad.stderr)
        self.assertEqual(check("--bogus").returncode, 2)

    def test_check_probes_the_relay_the_engine_actually_uses(self):
        self.configured()
        self.env["MOCK_VERSION"] = "0.10.7"
        out = self.run_script("check.sh").stdout
        self.assertIn("PASS  relay reachable: n0 public relays (Reason Network 0.10.7)", out)
        self.env["MOCK_VERSION"] = "0.12.17"
        self.tool("curl", 'echo "curl $*" >> "$CALL_LOG"; case "$*" in *https://aps1-1*) printf 200 ;; *) exit 73 ;; esac')
        log = self.home / ".reason/network/daemon.log"
        log.write_text("[1] net: on relay https://relay-sfo1.compute.reasonmachines.com/\n")
        out = self.run_script("check.sh").stdout
        self.assertIn("FAIL  relay not reachable (home relay from daemon.log (Reason Network 0.12.17)): "
                      "https://relay-sfo1.compute.reasonmachines.com/", out)
        self.assertIn("iroh.link and reasonmachines.com", out)
        log.write_text("[1] net: on relay https://aps1-1.relay.n0.iroh.link/\n")
        self.assertIn("PASS  relay reachable: home relay from daemon.log", self.run_script("check.sh").stdout)
        log.unlink()
        out = self.run_script("check.sh").stdout
        self.assertIn("SKIP  relay reachability unknown (unknown: Reason Network 0.12.17 has not logged", out)
        self.assertNotIn("relay not reachable", out)
        (self.who / "team").write_text("team demo · relay: https://aps1-1.relay.n0.iroh.link/\n"
                                       "  demo  mac  this machine\n")
        self.assertIn("PASS  relay reachable: the team's relay", self.run_script("check.sh").stdout)

    def test_check_probes_a_failed_relay_once_more_before_failing(self):
        self.configured()
        self.tool("sleep", 'echo "sleep $*" >> "$CALL_LOG"')
        self.tool("curl", 'echo "curl $*" >> "$CALL_LOG"; for a; do url="$a"; done\n'
                          'case "$url" in *"$PROBE_DOWN"*) [ -z "$PROBE_DOWN" ] || exit 28 ;; esac\n'
                          '[ -z "$PROBE_SLOW" ] || [ "$(grep -c -F " $url" "$CALL_LOG")" -gt 1 ] || exit 28\n'
                          'printf 200')
        self.env.update(PROBE_DOWN="", PROBE_SLOW="", MOCK_VERSION="0.10.7")
        (self.base / "calls").unlink(missing_ok=True)
        out = self.run_script("check.sh").stdout
        urls = sorted({line.split()[-1] for line in self.calls().splitlines() if line.startswith("curl ")})
        self.assertTrue(urls)
        self.assertIn("PASS  relay reachable: ", out)
        self.assertNotIn("second probe", out)
        self.assertNotIn("sleep 3", self.calls())
        self.env["PROBE_SLOW"] = "1"
        (self.base / "calls").unlink()
        out = self.run_script("check.sh").stdout
        line = next(l for l in out.splitlines() if "relay reachable" in l)
        self.assertTrue(line.startswith("PASS  relay reachable: "), out)
        self.assertTrue(line.endswith("(the second probe passed: " + " ".join(urls) + ")"), line)
        self.assertEqual(self.calls().count("sleep 3\n"), len(urls))
        self.assertEqual(sum(l.startswith("curl ") for l in self.calls().splitlines()), 2 * len(urls))
        self.env["PROBE_DOWN"] = urls[0]
        (self.base / "calls").unlink()
        out = self.run_script("check.sh", "--lang", "zh-CN").stdout
        self.assertIn(f"FAIL  relay 无法连接（", out)
        line = next(l for l in out.splitlines() if "relay 无法连接" in l)
        self.assertTrue(line.endswith(f"： {urls[0]}"), line)
        self.assertEqual(self.calls().count("sleep 3\n"), len(urls))
        self.assertEqual(self.run_script("check.sh").returncode, 1)

    def test_install_probes_the_pinned_default_before_the_engine_exists(self):
        result = self.run_script("install.sh", "--name", "demo", "--relay-name", "nova", "--yes")
        self.assertIn("ok (default relays of Reason Network 0.12.17 (not installed yet))", result.stdout)
        self.assertIn("curl -sS -o /dev/null -m 10 -w %{http_code} https://relay-sfo1.compute.reasonmachines.com/",
                      self.calls())

    def test_check_judges_the_latest_webhook_post(self):
        self.configured()
        log = self.home / "Library/Logs/agent-relay.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        up = "2026-11-10 09:00:00,000 INFO agent-relay up"

        def post(code):
            return f"2026-11-10 09:00:01,000 INFO POST 120 chars 120 bytes sha256={'e' * 64} code={code} items=[m1]"

        failed = "2026-11-10 09:00:01,001 WARNING webhook POST failed: error URLError; 1 item(s) kept in spool"
        cases = [
            ("failure then success", [post("error URLError"), failed, post(200), "INFO woke Grok Bot with 1 item(s)"],
             "PASS  latest webhook POST succeeded (code=200; recovered after an earlier failure)", False),
            ("failure only", [post("error URLError"), failed], "FAIL  latest webhook POST failed (code=error URLError)",
             True),
            ("success only", [post(200)], "PASS  latest webhook POST succeeded (code=200)", False),
            ("redirect", [post(302), failed.replace("error URLError", "302")],
             "FAIL  latest webhook POST failed (code=302)", True),
            ("no POST lines", [], None, False),
            ("success then failure", [post(200), post("error URLError"), failed],
             "FAIL  latest webhook POST failed (code=error URLError)", True),
            ("success then 500", [post(200), post(500)], "FAIL  latest webhook POST failed (code=500)", True),
            ("success then redirect", [post(200), post(302)], "FAIL  latest webhook POST failed (code=302)", True),
            ("204", [post(204)], "PASS  latest webhook POST succeeded (code=204)", False),
            ("recovered long ago", [post("error URLError"), failed, post(200)] + ["INFO note"] * 60,
             "recovered after an earlier failure", False),
            ("success twice", [post("error URLError"), post(200), post(200)],
             "PASS  latest webhook POST succeeded (code=200)\n", False),
            ("only a failure warning", [failed], "FAIL  recent webhook POST failures", True),
        ]
        for label, lines, expected, fails in cases:
            with self.subTest(label):
                log.write_text("\n".join([up, *lines]) + "\n")
                out = self.run_script("check.sh").stdout
                if expected:
                    self.assertIn(expected, out)
                self.assertEqual("webhook POST failed" in out or "webhook POST failures" in out, fails, out)

    def test_check_shows_the_relay_python_and_fails_when_it_is_gone(self):
        def check():
            return self.run_script("check.sh").stdout

        self.configured(relay="alice")
        self.assertIn("SKIP  relay Python (no relay installed yet)", check())
        result = self.run_script("install.sh", "--restart-relay")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"PASS  relay Python: {BASE_PYTHON}\n", check())
        python = fake_python(self.base / "old tools/python3")
        self.as_v090(python)
        path = self.home / ".local/lib/agent-mesh/deployed.json"
        before = path.read_bytes() if path.exists() else None
        self.assertIn(f"PASS  relay Python: {python} (in a virtual environment or a temporary directory; to move the "
                      "relay: ./install.sh --python <path>)\n", check())
        self.assertEqual(path.read_bytes() if path.exists() else None, before)
        gone = (f"FAIL  the relay's Python {python} is missing or no longer works\n      -> ./update.sh picks another "
                "one and restarts the relay; or choose it yourself: ./install.sh --python <path>\n")
        python.unlink()
        self.assertIn(gone, check())
        self.assertEqual(path.read_bytes() if path.exists() else None, before)
        data = self.record() or {"schema": 1}
        data["relay_python"] = str(python)
        self.write_record(data)
        self.env["AGENT_MESH_PYTHON_CANDIDATES"] = ""
        out = check()
        self.assertIn(gone, out)
        self.assertIn("RESULT fix the FAIL lines above", out)
        fake_python(python)
        self.assertNotIn("FAIL  the relay's Python", check())

    def test_check_fails_while_a_shown_stub_waits_for_its_cloud_update(self):
        self.configured(relay="alice")
        fail = "FAIL  the routine stub may be in the routine, but the cloud update it needs was not confirmed"
        self.write_record({"schema": 1, "prompt": {"hash": "0" * 64, "state": "pending", "mode": "thin"}})
        self.assertNotIn(fail, self.run_script("check.sh").stdout)
        for data in ({"schema": 1, "prompt": {"hash": "0" * 64, "state": "pending", "mode": "thin", "recover": 1}},
                     {"schema": 1, "bots": {"beta": {"prompt": {"hash": "0" * 64, "state": "pending", "recover": 1}}}}):
            with self.subTest(data=data):
                self.write_record(data)
                out = self.run_script("check.sh").stdout
                self.assertIn(fail, out)
                self.assertIn("-> ./update.sh (it looks for the cloud reply first", out)
                self.assertIn("RESULT fix the FAIL lines above", out)

    def test_check_reads_the_engine_settings_the_way_the_engine_does(self):
        def check():
            return self.run_script("check.sh").stdout

        self.configured(relay="alice")
        cfg = self.home / ".reason/network/config.toml"
        out = check()
        self.assertIn("PASS  auto_update off\nPASS  lan broadcast off\n", out)
        cfg.write_text('person = "demo"\n[network]\nauto_update = false\nlan = false\n')
        out = check()
        self.assertIn(f"FAIL  auto_update not off\n      -> ./update.sh (it sets  auto_update = false  above the first "
                      f"[table] line of {cfg}, removes the lines an earlier version appended inside tables, and "
                      "restarts the engine)\n", out)
        self.assertIn("FAIL  lan not off\n      -> ./update.sh (it sets  lan = false  above the first [table] line", out)
        cfg.write_text('auto_update = false\nperson = "demo"\n[network]\nurl = "x"\n')
        out = check()
        self.assertIn("PASS  auto_update off\nFAIL  lan not off\n", out)
        top = 'auto_update = false\nlan = false\nperson = "demo"\n'
        for table in ('[inbound.people]\nbob = "hold"\nauto_update = false\nlan = false\n',
                      '[folders]\n"~/personal" = "off"\nlan = false\n'):
            cfg.write_text(top + table)
            out = check()
            self.assertIn("FAIL  auto_update not off\n", out)
            self.assertIn("FAIL  lan not off\n", out)
            self.assertNotIn("PASS  auto_update off", out)
            self.assertNotIn("PASS  lan broadcast off", out)
        fix = (f"      -> ./update.sh (it stops before changing anything and says what to change in {cfg} by hand; "
               "after the edit run ./update.sh again, which restarts the engine so that it loads the corrected file)\n")
        for table in ('[folders]\nlan = true\n', "[inbound.people]\nlan = 1\n", "[folders]\n lan = false\n"):
            cfg.write_text(top + table)
            out = check()
            self.assertIn("FAIL  auto_update cannot be checked\n" + fix, out)
            self.assertIn("FAIL  lan cannot be checked\n" + fix, out)
            self.assertNotIn("PASS  auto_update off", out)
        for table in ('[folders]\n"~/lan" = "vlan"\n', '[inbound.people]\nlan = "accept"\n',
                      '[folders]\nauto_update = "~/updates"\n'):
            cfg.write_text(top + table)
            self.assertIn("PASS  auto_update off\nPASS  lan broadcast off\n", check())
        cfg.write_text('"auto_update" = false\nlan = false\n')
        out = check()
        self.assertIn("FAIL  auto_update cannot be checked\n" + fix, out)
        self.assertIn("FAIL  lan cannot be checked\n" + fix, out)
        self.assertNotIn("PASS  auto_update off", out)
        cfg.write_text('auto_update = false\nlan = false\nperson = "demo"\n')
        reload = ("FAIL  the engine settings are corrected, but the running engine daemon has not loaded them yet\n"
                  "      -> ./update.sh (it restarts the engine and the relay)\n")
        self.write_record({"schema": 1, "engine_config": {"state": "pending", "written_s": int(time.time()) + 3600}})
        self.assertNotIn(reload, check())
        self.fake_daemon()
        self.assertIn(reload, check())
        self.write_record({"schema": 1, "engine_config": {"state": "pending"}})
        self.assertIn(reload, check())
        self.write_record({"schema": 1, "engine_config": {"state": "pending", "written_s": 1}})
        out = check()
        self.assertNotIn(reload, out)
        self.assertIn("PASS  auto_update off\nPASS  lan broadcast off\n", out)
        self.assertEqual(self.record()["engine_config"], {"state": "pending", "written_s": 1})


class CheckBots(AddSupport, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.repo_dir = self.two_ready()
        self.added(self.repo_dir)
        self.lib = self.home / ".local/lib/agent-mesh"

    def snapshot(self):
        found = {}
        for path in sorted(self.home.rglob("*")):
            if path.is_symlink() or path.is_dir():
                found[str(path)] = os.readlink(path) if path.is_symlink() else "dir"
            else:
                found[str(path)] = (path.stat().st_mtime_ns, path.stat().st_mode, path.read_bytes())
        return found

    def check(self):
        before = self.snapshot()
        result = self.run_script("check.sh", timeout=60)
        self.assertEqual(self.snapshot(), before, "check.sh changed a file")
        self.assertEqual(result.stdout.count("RESULT"), 1, result.stdout)
        return result.stdout

    def section(self, out, name):
        start = out.index(f"== Bot address: demo/{name}")
        rest = out[start + 1:]
        end = min([i for i in (rest.find("\n== "), rest.find("\n\n")) if i >= 0] or [len(rest)])
        return out[start:start + 1 + end]

    def edit(self, change):
        data = self.record()
        change(data)
        self.write_record(data)

    def test_one_section_per_bot_shared_checks_once_and_cloud_registration(self):
        out = self.check()
        alpha, beta = self.section(out, "alpha"), self.section(out, "beta")
        self.assertTrue(beta.startswith("== Bot address: demo/beta → Grok: Beta\n"), beta)
        self.assertNotIn("Grok:", alpha.splitlines()[0])
        for line in ("PASS  signing key present", "PASS  relay installed (this checkout's version)",
                     "PASS  management lock file is private to you"):
            self.assertEqual(out.count(line), 1, line)
            self.assertLess(out.index(line), out.index("== Bot address"))
        for text in (alpha, beta):
            self.assertIn("PASS  relay service loaded", text)
            self.assertIn("PASS  webhook configured", text)
        self.assertIn("PASS  relay names in env derive from beta", beta)
        self.assertIn("PASS  cloud registration confirmed", beta)
        self.assertNotIn("cloud registration", alpha)
        self.edit(lambda d: d["bots"]["beta"]["cloud"].update(state="pending"))
        self.assertIn("PASS  the Bot's reply confirms the cloud registration; the next ./update.sh records it",
                      self.check())
        self.assertEqual(self.record()["bots"]["beta"]["cloud"]["state"], "pending")
        self.edit(lambda d: d["bots"]["beta"]["cloud"].update(req="0" * 16))
        self.assertIn("SKIP  cloud registration (the cloud sessions exist but the Bot has not replied yet; "
                      "./install.sh --relay-name beta --wake-bot wakes it again)", self.check())
        (self.who / "all.json").write_text(json.dumps(EMPTY_DIRECTORY))
        self.assertIn("SKIP  cloud registration (not registered on the cloud yet; ./install.sh --relay-name beta "
                      "--wake-bot asks the Bot to do it)", self.check())
        self.put_reply("0" * 16, "agent-mesh-result add-bot {req} failed beta\n", sender="demo/beta")
        out = self.check()
        self.assertIn("FAIL  the Bot's reply does not confirm the cloud registration", out)
        self.assertIn("-> ./install.sh --relay-name beta --wake-bot", out)
        self.edit(lambda d: d["bots"]["beta"].update(stage="configured"))
        beta = self.section(self.check(), "beta")
        self.assertIn("SKIP  relay service (not started until the cloud registers this Bot; "
                      "./install.sh --relay-name beta --wake-bot asks the Bot to do it)", beta)
        self.assertNotIn("relay service loaded", beta)

    def test_old_addresses_and_an_unfinished_rename_are_reported(self):
        now = int(time.time() * 1000)
        day = 86400 * 1000
        env = self.home / ".config/agent-relay-bots/beta/env"
        lines = [line for line in env.read_text().splitlines()
                 if not line.startswith(("LISTEN_NAME=", "CLOUD_NAME=", "INBOX_NAME="))]
        env.write_text("\n".join(lines + ["LISTEN_NAME=delta", "CLOUD_NAME=delta-cloud", "INBOX_NAME=delta-inbox"]) + "\n")
        marker = self.home / "Library/Application Support/agent-relay-bots/beta/registered"
        marker.write_text(json.dumps(dict(json.loads(marker.read_text()), name="demo/delta")))
        until = now - 10 * day
        self.edit(lambda d: (d["bots"]["beta"].update(name="delta"),
                             d.update(former=[{"name": "beta", "slot": "beta", "since_ms": now - 40 * day,
                                               "until_ms": until}])))
        out = self.check()
        delta = self.section(out, "delta")
        self.assertTrue(delta.startswith("== Bot address: demo/delta → Grok: Beta\n"), delta)
        self.assertIn("PASS  relay names in env derive from delta", delta)
        date = time.strftime("%Y-%m-%d", time.localtime(until / 1000))
        self.assertIn(f"WARN  the old address demo/beta no longer reaches demo/delta (since {date}); messages sent "
                      "to it wait unread\n      -> tell the senders the new address; to use the old one again: "
                      "agent-mesh rename beta --relay-name delta", out)
        until = now + 10 * day
        self.edit(lambda d: d["former"][0].update(until_ms=until))
        date = time.strftime("%Y-%m-%d", time.localtime(until / 1000))
        out = self.check()
        self.assertIn(f"PASS  the old address demo/beta still reaches demo/delta until {date}", out)
        self.assertNotIn("WARN", out)
        self.edit(lambda d: d.update(rename={"slot": "beta", "old": "delta", "new": "gamma", "req": "0" * 16,
                                             "stage": "replied", "sent_ms": now}))
        out = self.check()
        self.assertIn("FAIL  the rename of demo/delta to demo/gamma is not finished (stage replied)\n"
                      "      -> run agent-mesh rename to finish it, or agent-mesh rename --cancel to undo it", out)
        self.assertIn("RESULT fix the FAIL lines above", out)
        self.edit(lambda d: d.update(rename={"slot": "beta"}))
        self.assertIn("FAIL  the rename record in deployed.json cannot be read", self.check())

    def test_a_bot_being_removed_and_an_unreadable_roster_fail(self):
        self.edit(lambda d: d.update(removing={"name": "beta", "kind": "added", "step": "stopped"}))
        out = self.check()
        beta = self.section(out, "beta")
        self.assertIn("FAIL  the Bot beta is being uninstalled and gets no mail\n"
                      "      -> ./uninstall.sh --level bot --relay-name beta", beta)
        self.assertNotIn("relay service", beta)
        self.assertIn("RESULT fix the FAIL lines above", out)
        self.edit(lambda d: d.update(removing={"name": "alpha", "kind": "legacy", "step": "started"}))
        self.assertIn("FAIL  the Bot alpha is being uninstalled", self.check())
        (self.lib / "deployed.json").write_text("{damaged")
        out = self.check()
        self.assertIn("FAIL  the list of Bots on this Mac cannot be read", out)
        self.assertIn("RESULT fix the FAIL lines above", out)

    def test_engine_switch_record_states_and_lock_file(self):
        record = self.lib / "engine-upgrade.json"
        base = {"stage": "prepared", "bin": "b", "home": "h", "backup": ""}
        for values, expected in (({"phase": "committed", "stage": "committed"},
                                  "PASS  an earlier engine upgrade finished; its leftover record is harmless"),
                                 ({"phase": "prepared"},
                                  "FAIL  an engine upgrade stopped before the engine was switched; the engine is "
                                  "unchanged"),
                                 ({"phase": "stopped"}, "stopped before the engine was switched"),
                                 ({"phase": "switched", "backup": "/old/engine"},
                                  "FAIL  an engine upgrade did not finish\n      -> run ./update.sh in a terminal: it "
                                  "rolls the switch back first (backup: /old/engine)"),
                                 ({"phase": "stopped", "backup": "/old/engine"}, "(backup: /old/engine)")):
            with self.subTest(values=values):
                record.write_text(json.dumps(dict(base, **values)))
                self.assertIn(expected, self.check())
        record.unlink()
        lock = self.home / ".local/lib/agent-mesh.lock"
        self.assertTrue(lock.is_file())
        lock.chmod(0o666)
        self.assertIn(f"FAIL  others can write the management lock file {lock}\n      -> chmod go-w {lock}",
                      self.check())
        lock.unlink()
        self.assertIn("SKIP  management lock file (not created yet;", self.check())
        lock.symlink_to(self.home / "elsewhere")
        self.assertIn(f"FAIL  the management lock file {lock} is a symlink", self.check())
        lock.unlink()
        lock.mkdir()
        self.assertIn(f"FAIL  the management lock file {lock} is not a regular file", self.check())
