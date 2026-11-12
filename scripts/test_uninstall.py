"""Uninstaller checks with disposable homes and fake system and engine commands; never touch real services."""
import hashlib
import hmac
import json
import os
import pty
import re
import select
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from render_prompt import ROOT, cloud_uninstall_script, render_prompt
from write_launch_agent import launch_agent_bytes

WEBHOOK_URL = "https://example.invalid/hook/very-secret-path"
WEBHOOK_KEY = "webhook-key-0123456789"
KEY = "f" * 64

FAKE_RN = r'''#!/usr/bin/env python3
import json, os, sys
state_path = os.path.join(os.environ["MOCK_RN"], "state.json")
args = sys.argv[1:]
if args[:1] != ["--version"]:
    with open(os.environ["CALL_LOG"], "a") as log:
        log.write("rn " + " ".join(args) + "\n")
s = json.load(open(state_path))
fail = s.get("fail", {})
def save():
    json.dump(s, open(state_path, "w"))
cmd = args[0] if args else ""
if cmd == "--version":
    print("reason-network 0.12.17")
elif cmd == "team":
    if fail.get("team"):
        print(fail["team"]); sys.exit(1)
    if not s["in"]:
        print("error: not in a team"); sys.exit(1)
    print(f"team {s['team']} · relay: https://relay.example")
    v = " (verified)" if s.get("verified") else ""
    print(f"  {s['me'][0]}{v}  {s['me'][1]}  this machine")
    for row in s["rows"]:
        print("  " + "  ".join([row[0] + v, *row[1:]]))
elif cmd == "leave":
    if not s["in"]:
        print("error: not in a team"); sys.exit(1)
    if fail.get("leave"):
        print(fail["leave"]); sys.exit(1)
    if "--force" not in args and s["rows"] and not any(not r[2].startswith("offline") for r in s["rows"]):
        print("error: no teammate is online to hear it; use --force"); sys.exit(1)
    s["in"] = False; save(); print("left the team")
elif cmd == "remove":
    person, _, machine = args[1].partition("/")
    if [person, machine] == s["me"]:
        print("error: that is this machine; use leave"); sys.exit(1)
    if fail.get("remove"):
        print(fail["remove"]); sys.exit(1)
    keep = [r for r in s["rows"] if not (r[0] == person and r[1] == machine)]
    if len(keep) == len(s["rows"]):
        print("error: no such member"); sys.exit(1)
    s["rows"] = keep; save(); print("removed")
elif cmd == "setup":
    if fail.get("setup"):
        print(fail["setup"]); sys.exit(1)
    print("unwired")
'''

LAUNCHCTL = r'''echo "launchctl $*" >> "$CALL_LOG"
S="$MOCK_LAUNCHD"
case "$1" in
  print)
    case "$2" in
      */com.local.agent-relay) [ -f "$S/loaded" ] || { echo "Could not find service" >&2; exit 113; }
        echo "gui/501/com.local.agent-relay = {"; [ ! -s "$S/loaded" ] || echo "	path = $(cat "$S/loaded")"; echo "	state = running"; echo "	pid = 4242" ;;
    esac ;;
  bootout)
    [ ! -f "$S/stuck" ] || exit 5
    rm -f "$S/loaded"
    [ ! -f "$S/grow.py" ] || python3 "$S/grow.py" ;;
  bootstrap) printf '%s' "$3" > "$S/loaded"; echo "agent-relay up" >> "$MOCK_LOG"
    mkdir -p "$HOME/Library/Application Support/agent-relay"
    printf '{"name": "demo/bot", "pid": 4242}' > "$HOME/Library/Application Support/agent-relay/registered" ;;
esac
exit 0'''

PS = r'''if [ "$1" = -p ]; then [ -f "$MOCK_PS/one" ] || exit 1; cat "$MOCK_PS/one"; exit 0; fi
[ ! -f "$MOCK_PS/fail" ] || { echo "ps: broken" >&2; exit 1; }
[ ! -f "$MOCK_PS/all" ] || cat "$MOCK_PS/all"
exit 0'''

GIT = r'''[ "$1" = -C ] && shift 2
echo "git $*" >> "$CALL_LOG"
S="$MOCK_GIT"
[ ! -f "$S/fail" ] || { echo "fatal: broken repository" >&2; exit 128; }
emit() { if [ "$1" = z ]; then tr '\n' '\0' < "$2"; else cat "$2"; fi; }
case "$*" in
  "ls-files -z --others --exclude-standard") [ ! -f "$S/others" ] || emit z "$S/others" ;;
  "ls-files -z --others --ignored --exclude-standard") [ ! -f "$S/ignored" ] || emit z "$S/ignored" ;;
  "ls-files --others --exclude-standard") [ ! -f "$S/others" ] || cat "$S/others" ;;
  "ls-files --others --ignored --exclude-standard") [ ! -f "$S/ignored" ] || cat "$S/ignored" ;;
  "diff --name-only HEAD") [ ! -f "$S/changed" ] || cat "$S/changed" ;;
  "rev-parse --verify -q @{upstream}") [ -f "$S/upstream" ] || exit 1; echo abc ;;
  "diff --name-only @{upstream} HEAD") [ ! -f "$S/committed" ] || cat "$S/committed" ;;
  "stash list") [ ! -f "$S/stash" ] || cat "$S/stash" ;;
  "for-each-ref --format=%(refname) refs/heads refs/tags") if [ -f "$S/refs" ]; then cat "$S/refs"; else echo refs/heads/main; fi ;;
  "rev-list --count "*" --not --remotes") if [ -f "$S/ahead" ] && grep -qx "$3" "$S/ahead"; then echo 2; else echo 0; fi ;;
  *) echo "unexpected git $*" >&2; exit 99 ;;
esac'''


def ack(key, challenge, kind="ack"):
    msg = "agent-mesh-uninstall-v1\n" + kind + "\n" + challenge
    return hmac.new(key.encode(), msg.encode(), hashlib.sha256).hexdigest()[:16]


class Session:
    def __init__(self, cmd, env):
        self.master, slave = pty.openpty()
        self.proc = subprocess.Popen(cmd, env=env, stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        os.close(slave)
        self.out = b""
        self.pos = 0

    def pump(self, wait):
        ready, _, _ = select.select([self.proc.stdout, self.master], [], [], wait)
        got = False
        for fd in ready:
            try:
                chunk = os.read(fd if fd == self.master else fd.fileno(), 65536)
            except OSError:
                chunk = b""
            if fd != self.master and chunk:
                self.out += chunk
                got = True
        return got

    def expect(self, text, timeout=60):
        deadline = time.time() + timeout
        while text.encode() not in self.out[self.pos:]:
            if not self.pump(0.2) and self.proc.poll() is not None:
                self.pump(0)
                if text.encode() in self.out[self.pos:]:
                    break
                raise AssertionError(f"exited before {text!r}:\n{self.text()}")
            if time.time() > deadline:
                raise AssertionError(f"timed out waiting for {text!r}:\n{self.text()}")
        end = self.out.index(text.encode(), self.pos) + len(text)
        seen = self.out[self.pos:end].decode()
        self.pos = end
        return seen

    def send(self, line):
        os.write(self.master, (line + "\n").encode())

    def finish(self, timeout=60):
        try:
            os.write(self.master, b"\x04")
        except OSError:
            pass
        deadline = time.time() + timeout
        while self.proc.poll() is None and time.time() < deadline:
            self.pump(0.2)
        while self.pump(0.05):
            pass
        rest = self.proc.stdout.read()
        if rest:
            self.out += rest
        self.proc.wait(timeout=5)
        self.proc.stdout.close()
        os.close(self.master)
        return self.proc.returncode

    def text(self):
        return self.out.decode(errors="replace")


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bin = self.base / "tools"
        self.bin.mkdir()
        for name in ("dirname", "sed", "head", "awk", "tar", "shasum", "openssl", "mkdir", "chmod", "cp", "date",
                     "grep", "cat", "install", "tail", "wc", "mktemp", "rm", "mv", "cmp", "tr", "ls", "env"):
            found = shutil.which(name)
            if found:
                (self.bin / name).symlink_to(found)
        (self.bin / "python3").symlink_to(sys.executable)
        self.calls_path = self.base / "calls"
        self.env = {"PATH": str(self.bin), "CALL_LOG": str(self.calls_path), "PYTHONDONTWRITEBYTECODE": "1",
                    "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
        self.tool("sleep", "exit 0")

    def tool(self, name, body, path=None, shell="#!/bin/sh\n"):
        path = path or self.bin / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or path.exists():
            path.unlink()
        path.write_text(shell + body + "\n")
        path.chmod(0o755)
        return path

    def calls(self):
        return self.calls_path.read_text() if self.calls_path.exists() else ""

    def rn_state(self, folder, **values):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "state.json"
        data = json.loads(path.read_text()) if path.exists() else {}
        data.update(values)
        path.write_text(json.dumps(data))
        return data


class Mac(Base):
    def setUp(self):
        super().setUp()
        self.home = self.base / "home dir" / "Alex's"
        self.home.mkdir(parents=True)
        self.env.update(HOME=str(self.home), MOCK_LAUNCHD=str(self.base / "launchd"),
                        MOCK_LOG=str(self.home / "Library/Logs/agent-relay.log"), MOCK_PS=str(self.base / "ps"),
                        MOCK_RN=str(self.base / "rn"))
        (self.base / "launchd").mkdir()
        (self.base / "ps").mkdir()
        self.tool("uname", 'if [ "$1" = -s ]; then echo Darwin; else echo arm64; fi')
        self.tool("id", "echo 501")
        self.tool("launchctl", LAUNCHCTL)
        self.tool("plutil", "exit 0")
        self.tool("curl", "exit 99")
        self.tool("ps", PS)
        self.rn_state(self.base / "rn", **{"in": True, "team": "demo", "me": ["demo", "mac"],
                                           "rows": [["demo", "cloud", "offline", "4m"]], "fail": {}})

    def install(self, name=None, dsh=False, record=True, queue=None, relay_text=None, rollback=False):
        h = self.home
        lib = h / ".local/lib/agent-mesh"
        lib.mkdir(parents=True)
        relay = lib / "agent_relay.py"
        relay.write_text(relay_text) if relay_text is not None else shutil.copy(ROOT / "agent_relay.py", relay)
        agents = h / "Library/LaunchAgents"
        agents.mkdir(parents=True)
        (agents / "com.local.agent-relay.plist").write_bytes(launch_agent_bytes(h, sys.executable, str(relay)))
        (agents / ".com.local.agent-relay.Ab12Cd").write_text("tmp")
        (lib / ".agent_relay.py.Xy34Zw").write_text("tmp")
        if rollback:
            (lib / "rollback").mkdir()
            (lib / "rollback/agent_relay.py").write_text("old")
        if record:
            (lib / "deployed.json").write_text(json.dumps({"schema": 1, "relay_name": name or "bot", "lang": "en"}))
        conf = h / ".config/agent-relay"
        conf.mkdir(parents=True)
        lines = [f"RN_BIN={h}/.local/bin/reason-network", "PERSON=demo", f"WEBHOOK_URL={WEBHOOK_URL}",
                 f"WEBHOOK_KEY={WEBHOOK_KEY}", "OUTBOUND_KEY_FILE=~/.config/agent-relay/outbound.key"]
        if name:
            lines += [f"LISTEN_NAME={name}", f"CLOUD_NAME={name}-cloud", f"INBOX_NAME={name}-inbox"]
        (conf / "env").write_text("\n".join(lines) + "\n")
        (conf / "env").chmod(0o600)
        (conf / "outbound.key").write_text(KEY + "\n")
        (conf / "outbound.key").chmod(0o600)
        state = h / "Library/Application Support/agent-relay"
        (state / "fwd-copies").mkdir(parents=True)
        (state / "state.json").write_text(json.dumps(queue if queue is not None else {"spool": [], "pending": {}}))
        logs = h / "Library/Logs"
        logs.mkdir(parents=True)
        for log in ("agent-relay.log", "agent-relay.log.1", "agent-relay.launchd.log"):
            (logs / log).write_text("agent-relay up\n")
        rn_home = h / ".reason/network"
        rn_home.mkdir(parents=True)
        (rn_home / "machine.key").write_text("machine")
        (rn_home / "config.toml").write_text('person = "demo"\nauto_update = false\n')
        con = sqlite3.connect(rn_home / "network.db")
        con.execute("CREATE TABLE sessions (name TEXT PRIMARY KEY, key TEXT NOT NULL, last_seen INTEGER NOT NULL)")
        con.execute("INSERT INTO sessions VALUES (?, ?, 1)", (name or "bot", f"cli:{name or 'bot'}"))
        con.commit()
        con.close()
        self.tool("reason-network", FAKE_RN, h / ".local/bin/reason-network", shell="")
        (self.base / "launchd/loaded").write_text(str(agents / "com.local.agent-relay.plist"))
        if dsh:
            self.dsh()
        return h

    def dsh(self):
        d = self.home / ".dsh"
        (d / "plugins").mkdir(parents=True)
        (d / "plugins/reason-network-bridge.mjs").write_text("plugin")
        (d / "backup-reason-network-bridge").mkdir()
        (d / "backup-reason-network-bridge/reason-network-block.yml").write_text("own")
        (d / "logs").mkdir()
        (d / "logs/reason-network-bridge.log").write_text("log")
        (d / "cordis.patch.yml").write_text("- a: 1\n# === reason-network-bridge BEGIN (x) ===\nbridge\n"
                                            "# === reason-network-bridge END ===\n- b: 2\n")

    def p(self, rel):
        return self.home / rel

    def run_un(self, *args):
        r = subprocess.run(["/bin/bash", str(ROOT / "uninstall.sh"), *args], env=self.env, input="",
                           capture_output=True, text=True, timeout=60)
        self.no_secrets(r.stdout + r.stderr)
        return r

    def session(self, *args):
        return Session(["/bin/bash", str(ROOT / "uninstall.sh"), *args], self.env)

    def close(self, s):
        code = s.finish()
        self.no_secrets(s.text())
        return code

    def no_secrets(self, text):
        for secret in (WEBHOOK_URL, WEBHOOK_KEY, "very-secret-path", KEY):
            self.assertNotIn(secret, text)

    def challenge(self, text):
        return re.search(r'CHALLENGE="([0-9a-f]{16})"', text).group(1)

    def rn_calls(self):
        return [line for line in self.calls().splitlines() if line.startswith("rn ")]

    def cloud_gone(self):
        self.rn_state(self.base / "rn", rows=[])

    def run_deep(self, level="identity", phrase="demo", routine="deleted", extra=()):
        s = self.session("--level", level)
        s.expect("to go on (anything else stops): ")
        s.send(phrase)
        for line in extra:
            s.send(line)
        seen = s.expect("or 'skip': ")
        challenge = self.challenge(seen)
        s.send(ack(KEY, challenge, "proof"))
        s.expect(ack(KEY, challenge))
        s.expect("or skip if it could not finish: ")
        self.cloud_gone()
        s.send("done")
        s.expect("Type deleted when it is gone, or later: ")
        s.send(routine)
        return s, self.close(s)

    def test_without_arguments_only_shows_what_is_installed(self):
        self.install(name="alice")
        before = sorted(str(p) for p in self.home.rglob("*"))
        r = self.run_un()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for text in ("nothing removed yet", "same as this checkout", "team/person   demo", "demo/mac",
                     "alice, alice-cloud, alice-inbox;", "webhook configured", "signing key present",
                     "Nothing was removed"):
            self.assertIn(text, r.stdout)
        self.assertEqual(sorted(str(p) for p in self.home.rglob("*")), before)
        self.assertNotIn("bootout", self.calls())
        self.assertEqual(self.rn_calls(), ["rn team"])

    def test_plan_shows_every_level_and_never_asks(self):
        self.install()
        r = self.run_un("--plan")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for level in ("program", "identity", "all"):
            self.assertIn(f"== plan for level {level}:", r.stdout)
        self.assertIn("bot, bot-cloud, bot-inbox (default)", r.stdout)
        r = self.run_un("--plan", "--level", "all")
        self.assertIn("Only listed, never removed", r.stdout)
        self.assertNotIn("== plan for level program", r.stdout)
        self.assertNotIn("bootout", self.calls())
        self.assertTrue(self.p(".config/agent-relay/env").exists())

    def test_without_a_terminal_deep_levels_exit_3_and_options_are_checked(self):
        self.install()
        for args in (("--level", "identity"), ("--level", "all"), ("--all",), ("--level", "program"),
                     ("--residual", "--level", "all")):
            r = self.run_un(*args)
            self.assertEqual(r.returncode, 3, args)
            self.assertIn("own terminal", r.stdout)
        for args in (("-y",), ("--all", "-y"), ("--level", "identity", "--yes"), ("--residual",),
                     ("--cloud-prompt", "--level", "program"), ("--bogus",)):
            self.assertEqual(self.run_un(*args).returncode, 2, args)
        self.assertNotIn("bootout", self.calls())
        self.assertTrue(self.p(".local/lib/agent-mesh/agent_relay.py").exists())

    def test_program_level_keeps_identity_and_install_restart_relay_brings_it_back(self):
        self.install(rollback=True)
        r = self.run_un("--level", "program", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("level program complete", r.stdout)
        self.assertIn("./install.sh --restart-relay", r.stdout)
        for gone in ("Library/LaunchAgents/com.local.agent-relay.plist", "Library/LaunchAgents/.com.local.agent-relay.Ab12Cd",
                     ".local/lib/agent-mesh/agent_relay.py", ".local/lib/agent-mesh/.agent_relay.py.Xy34Zw",
                     ".local/lib/agent-mesh/rollback"):
            self.assertFalse(self.p(gone).exists(), gone)
        for kept in (".local/lib/agent-mesh/deployed.json", ".config/agent-relay/env", ".config/agent-relay/outbound.key",
                     "Library/Application Support/agent-relay/state.json", "Library/Logs/agent-relay.log",
                     ".reason/network/machine.key", ".local/bin/reason-network"):
            self.assertTrue(self.p(kept).exists(), kept)
        self.assertFalse((self.base / "launchd/loaded").exists())
        self.assertEqual(self.rn_calls(), ["rn team"])
        r = subprocess.run(["/bin/bash", str(ROOT / "install.sh"), "--restart-relay"], env=self.env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.p(".local/lib/agent-mesh/agent_relay.py").read_bytes(), (ROOT / "agent_relay.py").read_bytes())
        self.assertTrue(self.p("Library/LaunchAgents/com.local.agent-relay.plist").exists())
        again = self.run_un("--level", "program", "--yes")
        self.assertEqual(again.returncode, 0, again.stdout)
        r = self.run_un("--level", "program", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("relay login service: absent", r.stdout)

    def test_identity_level_runs_in_order_and_keeps_logs_and_engine(self):
        self.install(name="alice", queue={"spool": [], "pending": {}})
        s, code = self.run_deep()
        out = s.text()
        self.assertEqual(code, 0, out)
        self.assertIn("level identity complete", out)
        self.assertIn("mode normal", out)
        self.assertIn("Bot address name alice", out)
        prompt = re.search(r"-{64}\n(.*?)-{64}", out, re.S).group(1)
        script = re.search(r"<<'AGENT_MESH_UNINSTALL_EOF'\n(.*?\n)AGENT_MESH_UNINSTALL_EOF", prompt, re.S).group(1)
        self.assertIn(hashlib.sha256(script.encode()).hexdigest(), prompt)
        self.assertIn('TEAM="demo"; MAC="mac"; RELAY_NAME="alice"; LEVEL="identity"; MODE="normal"', script)
        for gone in (".local/lib/agent-mesh", ".config/agent-relay", "Library/Application Support/agent-relay",
                     ".reason/network", "Library/LaunchAgents/com.local.agent-relay.plist"):
            self.assertFalse(self.p(gone).exists(), gone)
        for kept in ("Library/Logs/agent-relay.log", ".local/bin/reason-network"):
            self.assertTrue(self.p(kept).exists(), kept)
        calls = self.calls().splitlines()
        order = [next(i for i, c in enumerate(calls) if c.startswith(p))
                 for p in ("launchctl bootout", "rn leave", "rn setup --remove", "rn stop")]
        self.assertEqual(order, sorted(order))
        self.assertNotIn("rn remove", self.calls())
        self.assertNotIn("rn leave --force", self.calls())

    def test_all_level_removes_logs_engine_dsh_and_lists_manual_items(self):
        self.install(dsh=True)
        (self.home / ".zshrc").write_text("# keep\nexport PATH=\"$HOME/.local/bin:$PATH\"\n")
        s, code = self.run_deep("all", "delete demo", routine="later")
        out = s.text()
        self.assertEqual(code, 1, out)
        self.assertIn("level all is not complete yet", out)
        self.assertIn("Grok routine: user-pending", out)
        self.assertIn(f"{self.home}/.zshrc:2", out)
        for gone in ("Library/Logs/agent-relay.log", "Library/Logs/agent-relay.log.1", "Library/Logs/agent-relay.launchd.log",
                     ".local/bin/reason-network", ".dsh/plugins/reason-network-bridge.mjs",
                     ".dsh/backup-reason-network-bridge", ".dsh/logs/reason-network-bridge.log", ".reason/network"):
            self.assertFalse(self.p(gone).exists(), gone)
        self.assertEqual(self.p(".dsh/cordis.patch.yml").read_text(), "- a: 1\n- b: 2\n")
        self.assertTrue(self.p(".zshrc").exists())

    def test_dsh_failure_stops_before_reason_network_is_touched(self):
        self.install(dsh=True)
        patch = self.p(".dsh/cordis.patch.yml")
        patch.write_text(patch.read_text().replace("# === reason-network-bridge END ===\n", ""))
        s = self.session("--level", "identity")
        s.expect("to go on (anything else stops): ")
        s.send("demo")
        s.expect("or 'skip': ")
        s.send("skip")
        s.expect("Remove demo/cloud from the team now? Type y: ")
        s.send("n")
        code = self.close(s)
        self.assertEqual(code, 1, s.text())
        self.assertIn("Reason Network was left in place", s.text())
        self.assertTrue(self.p(".reason/network/machine.key").exists())
        self.assertTrue(self.p(".config/agent-relay/outbound.key").exists())
        self.assertNotIn("rn leave", self.calls())

    def test_wrong_proof_stops_the_cloud_step_and_offers_remove(self):
        self.install()
        s = self.session("--level", "identity")
        s.expect("to go on (anything else stops): ")
        s.send("demo")
        s.expect("or 'skip': ")
        s.send("0123456789abcdef")
        s.expect("does not match")
        s.expect("Remove demo/cloud from the team now? Type y: ")
        s.send("y")
        s.expect("Type deleted when it is gone, or later: ")
        s.send("deleted")
        code = self.close(s)
        out = s.text()
        self.assertEqual(code, 1, out)
        self.assertIn("cloud team membership: done (removed with reason-network remove demo/cloud)", out)
        self.assertIn("cloud identity files: skipped (PROOF did not match)", out)
        self.assertIn("not complete yet", out)
        self.assertIn("rn remove demo/cloud", self.calls())

    def test_a_mac_named_cloud_never_runs_remove(self):
        self.install()
        self.rn_state(self.base / "rn", me=["demo", "cloud"], rows=[["demo", "cloud", "direct"]])
        s = self.session("--level", "identity")
        s.expect("to go on (anything else stops): ")
        s.send("demo")
        s.expect("or 'skip': ")
        s.send("skip")
        s.expect("Type deleted when it is gone, or later: ")
        s.send("deleted")
        self.close(s)
        self.assertIn("this Mac is also named cloud", s.text())
        self.assertNotIn("rn remove", self.calls())

    def test_remove_needs_an_online_member_and_never_touches_others(self):
        self.install()
        self.rn_state(self.base / "rn", rows=[["demo", "cloud", "offline", "4m"], ["bob", "cloud", "offline", "1h"],
                                              ["demo", "laptop", "offline", "2d"]])
        s = self.session("--level", "identity")
        s.expect("to go on (anything else stops): ")
        s.send("demo")
        s.expect("or 'skip': ")
        s.send("skip")
        s.expect("Type force to leave anyway: ")
        s.send("no")
        self.close(s)
        out = s.text()
        self.assertIn("the other members are all offline", out)
        self.assertIn("bob/cloud (offline 1h)", out)
        self.assertNotIn("rn remove", self.calls())
        self.assertIn("did not leave the team", out)
        self.assertTrue(self.p(".reason/network/machine.key").exists())

    def test_leave_with_everyone_offline_needs_typed_force(self):
        self.install()
        self.rn_state(self.base / "rn", rows=[["bob", "mac", "offline", "1h"]])
        s = self.session("--level", "identity")
        s.expect("to go on (anything else stops): ")
        s.send("demo")
        s.expect("or 'skip': ")
        s.send("skip")
        s.expect("Type force to leave anyway: ")
        s.send("force")
        s.expect("Type deleted when it is gone, or later: ")
        s.send("deleted")
        self.close(s)
        self.assertIn("with --force; nobody confirmed hearing it", s.text())
        self.assertIn("rn leave --force", self.calls())
        self.assertFalse(self.p(".reason/network").exists())

    def test_foreign_deployments_block_deep_levels_and_are_never_touched(self):
        self.install()
        foreign = self.p("Library/LaunchAgents/com.local.other-relay.plist")
        foreign.write_bytes(b"""<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict><key>Label</key><string>com.local.other-relay</string>
<key>ProgramArguments</key><array><string>/usr/bin/python3</string><string>/opt/x/other_relay.py</string></array>
</dict></plist>""")
        (self.base / "ps/all").write_text(f"  77 /usr/bin/python3 /opt/x/other_relay.py\n"
                                          f"  78 /usr/bin/python3 /srv/second/agent_relay.py\n"
                                          f"  79 {sys.executable} {self.home}/.local/lib/agent-mesh/agent_relay.py\n")
        r = self.run_un("--plan")
        self.assertIn("com.local.other-relay: login service", r.stdout)
        self.assertIn("STOP unless you type I accept com.local.other-relay", r.stdout)
        self.assertEqual(r.stdout.count("STOP unless you type I accept com.local.other-relay"), 2)
        self.assertIn("pid 78: process", r.stdout)
        self.assertNotIn("pid 77", r.stdout)
        self.assertNotIn("pid 79", r.stdout)
        data = foreign.read_bytes()
        s = self.session("--level", "identity")
        s.expect("Type I accept com.local.other-relay to go on anyway (anything else stops): ")
        s.send("yes")
        self.close(s)
        self.assertIn("nothing was changed", s.text())
        self.assertTrue(self.p(".config/agent-relay/env").exists())
        self.assertNotIn("bootout", self.calls())
        r = self.run_un("--level", "program", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(foreign.read_bytes(), data)
        s = self.session("--level", "identity")
        s.expect("Type I accept com.local.other-relay to go on anyway (anything else stops): ")
        s.send("I accept com.local.other-relay")
        s.expect("Type I accept pid 78 to go on anyway (anything else stops): ")
        s.send("I accept pid 78")
        s.expect("to go on (anything else stops): ")
        s.send("nope")
        self.close(s)
        self.assertIn("not confirmed", s.text())
        self.assertEqual(foreign.read_bytes(), data)

    def test_stop_conditions_keep_everything(self):
        self.install()
        plist = self.p("Library/LaunchAgents/com.local.agent-relay.plist")
        good = plist.read_bytes()
        plist.write_bytes(good.replace(b"<key>PATH</key>", b"<key>AGENT_RELAY_ENV</key><string>/x</string><key>PATH</key>"))
        r = self.run_un("--level", "program", "--yes")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("overrides AGENT_RELAY_* settings", r.stdout)
        plist.write_bytes(b"not a plist")
        r = self.run_un("--level", "program", "--yes")
        self.assertIn("cannot be read", r.stdout)
        self.assertNotIn("bootout", self.calls())
        plist.write_bytes(good)
        self.assertTrue(self.p(".local/lib/agent-mesh/agent_relay.py").exists())

    def test_disagreeing_names_stop_deep_levels_only(self):
        self.install()
        env = self.p(".config/agent-relay/env")
        env.write_text(env.read_text().replace("PERSON=demo", "PERSON=other"))
        r = self.run_un("--plan", "--level", "identity")
        self.assertIn("the team name differs between sources", r.stdout)
        self.assertIn("relay env other", r.stdout)
        env.write_text(env.read_text().replace("PERSON=other", "PERSON=demo") + "LISTEN_NAME=alice\n")
        r = self.run_un("--plan", "--level", "identity")
        self.assertIn("not derived from one Bot address name", r.stdout)
        env.write_text(env.read_text() + "CLOUD_NAME=alice-cloud\nINBOX_NAME=alice-inbox\n")
        r = self.run_un("--plan", "--level", "identity")
        self.assertIn("installed with bot (deployed.json)", r.stdout)
        r = self.run_un("--level", "program", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_unsafe_paths_stop_before_any_engine_call(self):
        self.install()
        real = self.base / "elsewhere"
        shutil.move(str(self.p(".config/agent-relay")), str(real))
        self.p(".config/agent-relay").symlink_to(real)
        r = self.run_un("--plan", "--level", "identity")
        self.assertIn(".config/agent-relay is a symlink", r.stdout)
        self.assertEqual(self.rn_calls(), [])
        self.p(".config/agent-relay").unlink()
        shutil.move(str(real), str(self.p(".config/agent-relay")))
        state = self.p("Library/Application Support/agent-relay")
        shutil.move(str(state), str(real))
        state.symlink_to(real)
        s, code = self.run_deep()
        self.assertEqual(code, 1, s.text())
        self.assertIn("relay state (queue, replay protection, kept copies): skipped", s.text())
        self.assertTrue((real / "state.json").exists())

    def test_live_update_lock_stops_and_stale_or_reused_locks_do_not(self):
        self.install()
        lock = self.p(".local/lib/agent-mesh/update.lock")
        lock.mkdir()
        (lock / "pid").write_text(f"{os.getpid()}\n")
        (self.base / "ps/one").write_text("/bin/bash ./update.sh --lang en\n")
        r = self.run_un("--level", "program", "--yes")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("./update.sh is running", r.stdout)
        (self.base / "ps/one").write_text("/usr/bin/python3 something else\n")
        r = self.run_un("--plan")
        self.assertIn("left", r.stdout)
        self.assertNotIn("is running", r.stdout)
        (self.base / "ps/one").unlink()
        r = self.run_un("--plan")
        self.assertIn("./update.sh is running", r.stdout)
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        (lock / "pid").write_text(f"{dead.pid}\n")
        r = self.run_un("--level", "program", "--yes")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertTrue(lock.exists())

    def test_queue_counts_damage_and_growth_while_stopping(self):
        self.install(queue={"spool": [{"id": "1"}], "pending": {"a": {}},
                            "sends": {"n1": {"state": "sending"}, "n2": {"state": "unknown"}, "n3": {"state": "ok"}},
                            "alerts": []})
        r = self.run_un("--plan")
        self.assertIn("spool 1, pending 1, sending 1, unknown 1, alerts 0", r.stdout)
        state = self.p("Library/Application Support/agent-relay/state.json")
        (self.base / "launchd/grow.py").write_text(
            "import json\np = %r\nd = json.load(open(p))\nd['spool'].append({'id': '2'})\njson.dump(d, open(p, 'w'))\n"
            % str(state))
        s = self.session("--level", "program")
        s.expect("Remove the relay program now? Type y: ")
        s.send("y")
        s.expect("The queue grew while the relay stopped. Go on? Type y: ")
        s.send("y")
        self.assertEqual(self.close(s), 0, s.text())
        self.assertIn("spool 2", s.text())
        state.write_text("{broken")
        r = self.run_un("--plan")
        self.assertIn("queue         cannot be read", r.stdout)

    def test_residual_mode_asks_the_team_and_uses_no_proof(self):
        conf = self.p(".config/agent-relay")
        conf.mkdir(parents=True)
        (conf / "env").write_text("RN_BIN=x\n")
        self.p(".reason/network").mkdir(parents=True)
        s = self.session("--residual", "--level", "identity")
        s.expect("Type the team name you installed with: ")
        s.send("demo")
        s.expect("to go on (anything else stops): ")
        s.send("demo")
        seen = s.expect("or 'skip': ")
        self.assertIn('MODE="residual"', seen)
        s.send("none")
        s.expect("or skip if it could not finish: ")
        s.send("done")
        s.expect("Type deleted when it is gone, or later: ")
        s.send("deleted")
        code = self.close(s)
        self.assertEqual(code, 0, s.text())
        self.assertIn("level identity complete", s.text())
        self.assertFalse(conf.exists())
        self.assertFalse(self.p(".reason/network").exists())
        self.assertEqual(self.rn_calls(), [])

    def test_residual_mode_refuses_while_an_identity_exists_and_normal_mode_points_to_it(self):
        self.install()
        s = self.session("--residual", "--level", "identity")
        self.close(s)
        self.assertIn("still has a Reason Network identity", s.text())
        (self.p(".reason/network/machine.key")).unlink()
        s = self.session("--level", "identity")
        self.close(s)
        self.assertIn("run ./uninstall.sh --residual --level identity", s.text())
        self.assertTrue(self.p(".config/agent-relay/env").exists())

    def test_cloud_prompt_only_runs_the_cloud_step(self):
        self.install()
        s = self.session("--cloud-prompt", "--level", "all")
        seen = s.expect("or 'skip': ")
        challenge = self.challenge(seen)
        self.assertIn('LEVEL="all"', seen)
        s.send("prompt")
        s.expect("or 'skip': ")
        s.send(ack(KEY, challenge, "proof"))
        s.expect(ack(KEY, challenge))
        s.expect("or skip if it could not finish: ")
        self.cloud_gone()
        s.send("done")
        code = self.close(s)
        self.assertEqual(code, 0, s.text())
        self.assertIn("nothing on this Mac was removed", s.text())
        self.assertTrue(self.p(".local/lib/agent-mesh/agent_relay.py").exists())
        self.assertNotIn("bootout", self.calls())

    def test_a_symlinked_dsh_temp_file_stops_before_dsh_is_touched(self):
        self.install(dsh=True)
        target = self.base / "victim.txt"
        target.write_text("keep me")
        self.p(".dsh/cordis.patch.yml.reason-network-bridge.tmp").symlink_to(target)
        patch = self.p(".dsh/cordis.patch.yml").read_text()
        r = self.run_un("--plan", "--level", "identity")
        self.assertIn("reason-network-bridge.tmp is a symlink", r.stdout)
        s = self.session("--level", "identity")
        self.assertEqual(self.close(s), 1, s.text())
        self.assertIn("resolve the STOP lines", s.text())
        self.assertNotIn("bootout", self.calls())
        r = subprocess.run(["/bin/bash", str(ROOT / "dsh/mac-uninstall.sh"), "--no-restore"], env=self.env,
                           capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn("is a symlink or not a regular file", r.stdout + r.stderr)
        self.assertEqual(target.read_text(), "keep me")
        self.assertEqual(self.p(".dsh/cordis.patch.yml").read_text(), patch)

    def test_cloud_prompt_obeys_stop_lines_and_the_foreign_gate(self):
        self.install()
        real = self.base / "rn-elsewhere"
        shutil.move(str(self.p(".reason/network")), str(real))
        self.p(".reason/network").symlink_to(real)
        s = self.session("--cloud-prompt", "--level", "identity")
        self.assertEqual(self.close(s), 1, s.text())
        self.assertIn(".reason/network is a symlink", s.text())
        self.assertNotIn("AGENT_MESH_UNINSTALL_EOF", s.text())
        self.assertEqual(self.rn_calls(), [])
        self.p(".reason/network").unlink()
        shutil.move(str(real), str(self.p(".reason/network")))
        (self.base / "ps/all").write_text("  78 /usr/bin/python3 /srv/second/agent_relay.py\n")
        s = self.session("--cloud-prompt", "--level", "identity")
        s.expect("Type I accept pid 78 to go on anyway (anything else stops): ")
        s.send("no")
        self.assertEqual(self.close(s), 1, s.text())
        self.assertNotIn("AGENT_MESH_UNINSTALL_EOF", s.text())
        self.assertNotIn("rn remove", self.calls())

    def test_cloud_prompt_is_incomplete_while_the_cloud_stays_in_the_team(self):
        self.install()
        s = self.session("--cloud-prompt", "--level", "identity")
        seen = s.expect("or 'skip': ")
        challenge = self.challenge(seen)
        s.send(ack(KEY, challenge, "proof"))
        s.expect("or skip if it could not finish: ")
        s.send("done")
        s.expect("Remove demo/cloud from the team now? Type y: ")
        s.send("n")
        self.assertEqual(self.close(s), 1, s.text())
        self.assertIn("The cloud step is not complete.", s.text())
        self.rn_state(self.base / "rn", fail={"team": "error: the network is down"})
        s = self.session("--cloud-prompt", "--level", "identity")
        s.expect("or 'skip': ")
        s.send("skip")
        self.assertEqual(self.close(s), 1, s.text())
        self.assertIn("the team list is unknown", s.text())

    def test_a_service_loaded_from_another_home_is_never_booted_out(self):
        self.install()
        other = self.base / "real home/Library/LaunchAgents/com.local.agent-relay.plist"
        (self.base / "launchd/loaded").write_text(str(other))
        r = self.run_un("--plan")
        self.assertIn(f"comes from {other}", r.stdout)
        r = self.run_un("--level", "program", "--yes")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertNotIn("bootout", self.calls())
        self.assertTrue(self.p("Library/LaunchAgents/com.local.agent-relay.plist").exists())
        self.assertTrue((self.base / "launchd/loaded").exists())
        (self.base / "launchd/loaded").write_text("")
        r = self.run_un("--level", "program", "--yes")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("an unknown plist", r.stdout)
        self.assertNotIn("bootout", self.calls())

    def test_a_key_file_reached_through_dotdot_is_a_custom_key(self):
        self.install()
        saved = self.home / "saved.key"
        saved.write_text(KEY + "\n")
        env = self.p(".config/agent-relay/env")
        env.write_text(env.read_text().replace("OUTBOUND_KEY_FILE=~/.config/agent-relay/outbound.key",
                                               "OUTBOUND_KEY_FILE=~/.config/agent-relay/../../saved.key"))
        raw = f"{self.home}/.config/agent-relay/../../saved.key"
        r = self.run_un("--plan")
        self.assertIn(f"custom key    {raw} (reported only, never removed)", r.stdout)
        s, code = self.run_deep()
        self.assertEqual(code, 1, s.text())
        self.assertIn(f"{raw} is never removed automatically", s.text())
        self.assertTrue(saved.exists())

    def test_foreign_processes_are_shown_without_arguments(self):
        self.install()
        (self.base / "ps/all").write_text("  78 /usr/bin/python3 /srv/second/agent_relay.py --webhook-key=SUPERSECRET"
                                          " --url https://example.invalid/very-private\n")
        r = self.run_un("--plan")
        self.assertIn("pid 78: process python3 running agent_relay.py (arguments not shown)", r.stdout)
        self.assertIn("STOP unless you type I accept pid 78", r.stdout)
        for secret in ("SUPERSECRET", "very-private", "/srv/second"):
            self.assertNotIn(secret, r.stdout)

    def test_an_incomplete_cloud_never_counts_as_done(self):
        conf = self.p(".config/agent-relay")
        conf.mkdir(parents=True)
        (conf / "env").write_text("RN_BIN=x\n")
        s = self.session("--cloud-prompt", "--residual", "--level", "identity")
        s.expect("Type the team name you installed with: ")
        s.send("demo")
        s.expect("or 'skip': ")
        s.send("none")
        s.expect("or skip if it could not finish: ")
        s.send("incomplete")
        self.assertEqual(self.close(s), 1, s.text())
        self.assertIn("cloud team membership                                skipped (the Bot reported INCOMPLETE",
                      s.text())
        self.assertIn("The cloud step is not complete.", s.text())
        s = self.session("--residual", "--level", "identity")
        s.expect("Type the team name you installed with: ")
        s.send("demo")
        s.expect("to go on (anything else stops): ")
        s.send("demo")
        s.expect("or 'skip': ")
        s.send("none")
        s.expect("or skip if it could not finish: ")
        s.send("incomplete")
        s.expect("Type deleted when it is gone, or later: ")
        s.send("deleted")
        self.assertEqual(self.close(s), 1, s.text())
        self.assertIn("level identity is not complete yet", s.text())
        self.assertIn("reason-network remove demo/cloud", s.text())
        self.assertIn("cloud identity files                                 done", s.text())

    def test_the_key_is_read_from_the_exact_path_the_relay_uses(self):
        self.install()
        vault = self.base / "vault"
        (vault / "keys").mkdir(parents=True)
        other = "e" * 64
        (vault / "outbound.key").write_text(other + "\n")
        self.p(".config/agent-relay/keys").symlink_to(vault / "keys")
        env = self.p(".config/agent-relay/env")
        env.write_text(env.read_text().replace("OUTBOUND_KEY_FILE=~/.config/agent-relay/outbound.key",
                                               "OUTBOUND_KEY_FILE=~/.config/agent-relay/keys/../outbound.key"))
        r = self.run_un("--plan")
        self.assertIn(f"custom key    {self.home}/.config/agent-relay/keys/../outbound.key", r.stdout)
        s = self.session("--cloud-prompt", "--level", "identity")
        seen = s.expect("or 'skip': ")
        challenge = self.challenge(seen)
        s.send(ack(other, challenge, "proof"))
        s.expect(ack(other, challenge))
        s.expect("or skip if it could not finish: ")
        s.send("skip")
        s.expect("Remove demo/cloud from the team now? Type y: ")
        s.send("n")
        self.close(s)
        self.assertIn("PROOF matches", s.text())
        self.assertNotIn(other, s.text().split("PROOF matches")[0])

    def test_older_layouts_without_record_or_names(self):
        self.install(record=False, relay_text="print('old relay')\n")
        r = self.run_un("--plan")
        self.assertIn("unknown version, same layout", r.stdout)
        self.assertNotIn("deployed.json present", r.stdout)
        self.assertIn("bot, bot-cloud, bot-inbox (default)", r.stdout)
        s, code = self.run_deep()
        self.assertEqual(code, 0, s.text())
        self.assertFalse(self.p(".reason/network").exists())


class CloudScript(Base):
    def setUp(self):
        super().setUp()
        self.w = self.base / "work space"
        self.a = self.w / "agent-mesh"
        self.b = self.a / "box"
        self.b.mkdir(parents=True)
        for name in ("rn-cloud.sh", "rn-cloud-setup.sh"):
            shutil.copy(ROOT / "box" / name, self.b / name)
        (self.b / "person").write_text("demo\n")
        (self.b / "relay").write_text("bot\n")
        (self.b / "outbound.key").write_text(KEY + "\n")
        (self.b / "state").mkdir()
        (self.b / "state/machine.key").write_text("m")
        (self.b / "relay.damaged-20261101T000000Z").write_text("x")
        self.tool("reason-network", FAKE_RN, self.b / "bin/reason-network", shell="")
        (self.a / "inbox/01A").mkdir(parents=True)
        (self.a / "inbox/01A/full.txt").write_text("body")
        (self.a / "threads").mkdir()
        (self.a / "threads/t.md").write_text("note")
        self.git = self.base / "git"
        self.git.mkdir()
        (self.git / "upstream").write_text("")
        self.env.update(MOCK_RN=str(self.base / "rn"), MOCK_GIT=str(self.git))
        self.tool("uname", 'if [ "$1" = -s ]; then echo Linux; else echo x86_64; fi')
        self.tool("git", GIT)
        self.rn_state(self.base / "rn", **{"in": True, "team": "demo", "me": ["demo", "cloud"],
                                           "rows": [["demo", "mac", "direct"]], "fail": {}})
        self.challenge = "0123456789abcdef"
        self.script = self.render()

    def render(self, level="identity", mode="normal", team="demo", relay="bot", raw=False):
        if raw:
            text = (ROOT / "box/rn-cloud-uninstall.sh").read_text()
        else:
            text, _ = cloud_uninstall_script(team, "mac", relay, level, mode, self.challenge)
        self.assertEqual(text.count("\nW=/workspace\n"), 1)
        text = text.replace("\nW=/workspace\n", f"\nW={shlex.quote(str(self.w))}\n")
        fd, name = tempfile.mkstemp(suffix=".sh", dir=self.base)
        os.close(fd)
        path = Path(name)
        path.write_text(text)
        return path

    def run_cloud(self, *args, script=None):
        r = subprocess.run(["/bin/bash", str(script or self.script), *args], env=self.env, capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.stderr, "", r.stderr)
        return r

    def identity_gone(self):
        for name in ("state", "bin", "person", "relay", "outbound.key", "relay.damaged-20261101T000000Z"):
            self.assertFalse((self.b / name).exists(), name)

    def test_check_changes_nothing_and_prints_the_proof(self):
        r = self.run_cloud("check")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(f"PROOF: {ack(KEY, self.challenge, 'proof')}", r.stdout)
        self.assertIn("team demo", r.stdout)
        self.assertIn("WILL REMOVE: box/state", r.stdout)
        self.assertNotIn("SHARED:", r.stdout)
        self.assertTrue((self.b / "person").exists())
        self.assertEqual([c for c in self.calls().splitlines() if c.startswith("rn ")], ["rn team"])

    def test_check_reads_a_team_list_with_verified_names(self):
        self.rn_state(self.base / "rn", verified=True)
        r = self.run_cloud("check")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn(f"PROOF: {ack(KEY, self.challenge, 'proof')}", r.stdout)
        self.assertNotIn("STOP", r.stdout)
        self.rn_state(self.base / "rn", rows=[["demo", "mac", "offline", "4m"]])
        r = self.run_cloud("check")
        self.assertIn("is not online in this cloud's team", r.stdout)

    def test_apply_with_the_code_leaves_and_removes_identity_only(self):
        r = self.run_cloud("apply", ack(KEY, self.challenge))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("DONE: level identity", r.stdout)
        self.identity_gone()
        self.assertTrue((self.a / "inbox/01A/full.txt").exists())
        self.assertTrue((self.b / "rn-cloud.sh").exists())
        self.assertIn("rn leave\n", self.calls())
        r = self.run_cloud("check")
        self.assertIn("STATE: cloud identity already removed", r.stdout)
        self.assertIn("PROOF: none", r.stdout)
        r = self.run_cloud("apply", ack(KEY, self.challenge))
        self.assertIn("STOP: no box/outbound.key", r.stdout)
        r = self.run_cloud("apply-no-proof")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("DONE", r.stdout)

    def test_wrong_code_or_no_proof_with_a_key_removes_nothing(self):
        for args, said in ((("apply", "0" * 16), "does not match"), (("apply-no-proof",), "a signing key is present"),
                           (("apply",), "usage"), (("check", "force"), "usage"), (("bogus",), "usage")):
            r = self.run_cloud(*args)
            self.assertEqual(r.returncode, 1, args)
            self.assertIn(said, r.stdout)
        self.assertTrue((self.b / "outbound.key").exists())
        self.assertNotIn("rn leave", self.calls())

    def test_binding_checks_stop_before_anything_changes(self):
        cases = [
            (lambda: (self.b / "person").write_text("other\n"), "box/person is not demo"),
            (lambda: (self.b / "relay").write_text("alice\n"), "box/relay is alice"),
            (lambda: self.rn_state(self.base / "rn", team="other"), "in the team other"),
            (lambda: self.rn_state(self.base / "rn", me=["demo", "mac"], rows=[["demo", "cloud", "direct"]]),
             "this computer is demo/mac"),
            (lambda: self.rn_state(self.base / "rn", rows=[["demo", "mac", "offline", "3m"]]), "is not online"),
        ]
        for change, said in cases:
            snapshot = json.loads((self.base / "rn/state.json").read_text())
            change()
            r = self.run_cloud("apply", ack(KEY, self.challenge))
            self.assertEqual(r.returncode, 1, said)
            self.assertIn(said, r.stdout)
            self.assertTrue((self.b / "outbound.key").exists(), said)
            (self.b / "person").write_text("demo\n")
            (self.b / "relay").write_text("bot\n")
            (self.base / "rn/state.json").write_text(json.dumps(snapshot))
        self.assertNotIn("rn leave", self.calls())

    def test_symlinks_stop_before_the_engine_runs(self):
        real = self.base / "real-state"
        shutil.move(str(self.b / "state"), str(real))
        (self.b / "state").symlink_to(real)
        r = self.run_cloud("check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("is a symlink", r.stdout)
        self.assertEqual(self.calls(), "")

    def test_an_unfilled_script_and_an_empty_workspace(self):
        r = self.run_cloud("check", script=self.render(raw=True))
        self.assertIn("STOP: this script was not filled in", r.stdout)
        shutil.rmtree(self.a)
        r = self.run_cloud("check")
        self.assertEqual(r.returncode, 0)
        self.assertIn("STATE: nothing at", r.stdout)

    def test_already_left_skips_leave_and_failed_leave_needs_force(self):
        self.rn_state(self.base / "rn", fail={"leave": "error: the network is down"})
        r = self.run_cloud("apply", ack(KEY, self.challenge))
        self.assertEqual(r.returncode, 1)
        self.assertIn("leave failed: error: the network is down", r.stdout)
        self.assertTrue((self.b / "outbound.key").exists())
        self.rn_state(self.base / "rn", fail={})
        r = self.run_cloud("apply", ack(KEY, self.challenge), "force")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("rn leave --force", self.calls())
        self.identity_gone()

    def test_already_left_only_removes_files(self):
        self.rn_state(self.base / "rn", **{"in": False})
        r = self.run_cloud("apply", ack(KEY, self.challenge))
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("STATE: already left the team", r.stdout)
        self.assertNotIn("rn leave", self.calls())
        self.identity_gone()

    def test_shared_use_of_box_needs_accept_shared(self):
        other = self.w / "other-app"
        other.mkdir()
        (other / "run.sh").write_text(f"export REASON_NETWORK_HOME={self.b}/state\n")
        (self.a / "second").mkdir()
        (self.a / "second/run.sh").write_text("RN=../box/bin/reason-network\n")
        (self.git / "ignored").write_text("second/run.sh\nbox/person\ninbox/01A/full.txt\n")
        (self.w / "link").symlink_to(self.b / "state")
        r = self.run_cloud("check")
        self.assertIn(f"SHARED: {other / 'run.sh'}", r.stdout)
        self.assertIn(f"SHARED: {self.a / 'second/run.sh'}", r.stdout)
        self.assertIn(f"SHARED: {self.w / 'link'} -> ", r.stdout)
        self.assertNotIn("box/person", r.stdout.split("WILL REMOVE")[0].replace("box/person is", ""))
        r = self.run_cloud("apply", ack(KEY, self.challenge))
        self.assertEqual(r.returncode, 1)
        self.assertIn("add accept-shared only after the user confirms", r.stdout)
        self.assertTrue((self.b / "outbound.key").exists())
        r = self.run_cloud("apply", ack(KEY, self.challenge), "accept-shared")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.identity_gone()

    def test_git_failure_counts_as_shared_and_keeps_the_clone(self):
        (self.git / "fail").write_text("")
        script = self.render(level="all")
        r = self.run_cloud("check", script=script)
        self.assertIn("SHARED: could not check everything", r.stdout)
        self.assertIn("KEEP: git", r.stdout)
        self.assertIn(f"WILL KEEP: {self.a}", r.stdout)
        r = self.run_cloud("apply", ack(KEY, self.challenge), "accept-shared", script=script)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("KEPT:", r.stdout)
        self.assertTrue(self.a.exists())
        self.assertFalse((self.a / "inbox").exists())
        self.assertFalse((self.a / "threads").exists())

    def test_all_level_lists_user_files_and_removes_the_clone(self):
        (self.git / "others").write_text("notes.txt\n")
        (self.git / "ignored").write_text("box/person\nbox/state/machine.key\ninbox/01A/full.txt\nlocal.env\n")
        (self.git / "changed").write_text("README.md\n")
        (self.git / "committed").write_text("box/rn-cloud.sh\n")
        (self.git / "stash").write_text("stash@{0}: WIP\n")
        (self.a / "notes.txt").write_text("mine")
        (self.a / "local.env").write_text("x")
        script = self.render(level="all")
        r = self.run_cloud("check", script=script)
        for line in ("FILE: notes.txt", "FILE: local.env", "FILE: README.md", "FILE: box/rn-cloud.sh",
                     "FILE: stash: stash@{0}: WIP", f"WILL REMOVE: {self.a}"):
            self.assertIn(line, r.stdout)
        self.assertNotIn("FILE: box/person", r.stdout)
        self.assertNotIn("FILE: inbox", r.stdout)
        r = self.run_cloud("apply", ack(KEY, self.challenge), script=script)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertFalse(self.a.exists())

    def test_all_level_keeps_the_clone_when_local_refs_have_unpushed_commits(self):
        (self.git / "upstream").unlink()
        (self.git / "refs").write_text("refs/heads/main\nrefs/heads/experiment\nrefs/tags/v-local\n")
        (self.git / "ahead").write_text("refs/heads/experiment\nrefs/tags/v-local\n")
        script = self.render(level="all")
        r = self.run_cloud("apply", ack(KEY, self.challenge), script=script)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("FILE: commits: refs/heads/experiment has 2 commit(s) that no remote branch has", r.stdout)
        self.assertIn("KEEP: refs/tags/v-local has commits that no remote branch has", r.stdout)
        self.assertNotIn("refs/heads/main has", r.stdout)
        self.assertTrue((self.b / "rn-cloud.sh").exists())
        self.assertFalse((self.a / "inbox").exists())
        self.identity_gone()

    def test_relative_references_to_box_outside_the_clone_are_shared(self):
        other = self.w / "tools"
        other.mkdir()
        (other / "run.sh").write_text("../agent-mesh/box/bin/reason-network team\n")
        r = self.run_cloud("check")
        self.assertIn(f"SHARED: {other / 'run.sh'}", r.stdout)
        r = self.run_cloud("apply", ack(KEY, self.challenge))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("add accept-shared only after the user confirms", r.stdout)
        self.assertTrue((self.b / "bin/reason-network").exists())

    def test_files_too_large_to_check_count_as_shared(self):
        big = self.w / "data.bin"
        with open(big, "wb") as stream:
            stream.truncate(21 * 1024 * 1024)
        r = self.run_cloud("check")
        self.assertIn(f"SHARED: could not check everything: not checked, larger than 20 MiB: {big}", r.stdout)
        r = self.run_cloud("apply", ack(KEY, self.challenge))
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertTrue((self.b / "outbound.key").exists())

    def test_an_identity_without_engine_is_reported_incomplete(self):
        (self.b / "bin/reason-network").unlink()
        r = self.run_cloud("check")
        self.assertIn("can be neither checked nor left", r.stdout)
        r = self.run_cloud("apply", ack(KEY, self.challenge))
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("INCOMPLETE: level identity files removed, but this cloud did not leave the team demo", r.stdout)
        self.assertNotIn("DONE:", r.stdout)
        self.assertNotIn("rn leave", self.calls())
        self.identity_gone()

    def test_residual_mode_stops_for_other_people_and_allows_no_proof(self):
        script = self.render(mode="residual")
        self.rn_state(self.base / "rn", rows=[["bob", "mac", "direct"]])
        r = self.run_cloud("check", script=script)
        self.assertIn("STOP: other people are in this team", r.stdout)
        self.rn_state(self.base / "rn", rows=[["demo", "mac", "offline", "9d"]])
        (self.b / "relay").write_text("alice\n")
        r = self.run_cloud("apply-no-proof", script=script)
        self.assertIn("only with their consent run the same command again with force added", r.stdout)
        self.assertTrue((self.b / "outbound.key").exists())
        r = self.run_cloud("apply-no-proof", "force", script=script)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("STATE: box/relay holds alice", r.stdout)
        self.identity_gone()

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_real_git_lists_ignored_user_files(self):
        real = shutil.which("git")
        (self.bin / "git").unlink()
        (self.bin / "git").symlink_to(real)
        env = dict(self.env, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        shutil.copy(ROOT / ".gitignore", self.a / ".gitignore")
        git = lambda *a: subprocess.run([real, "-C", str(self.a), *a], env=env, check=True, capture_output=True)
        git("init", "-q")
        git("add", ".gitignore", "box/rn-cloud.sh", "box/rn-cloud-setup.sh")
        git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "x")
        (self.a / "second").mkdir()
        (self.a / "second/app.log").write_text("uses box/state\n")
        (self.a / "mine.txt").write_text("x")
        self.env = env
        script = self.render(level="all")
        r = self.run_cloud("check", script=script)
        self.assertIn("FILE: mine.txt", r.stdout)
        self.assertIn("FILE: second/app.log", r.stdout)
        self.assertIn(f"SHARED: {self.a / 'second/app.log'}", r.stdout)
        self.assertIn("KEEP: refs/heads/", r.stdout)
        self.assertNotIn("FILE: box/person", r.stdout)
        remote = self.base / "remote.git"
        subprocess.run([real, "init", "-q", "--bare", str(remote)], env=env, check=True, capture_output=True)
        git("remote", "add", "origin", str(remote))
        git("push", "-q", "origin", "HEAD:refs/heads/main")
        git("fetch", "-q", "origin")
        r = self.run_cloud("check", script=script)
        self.assertNotIn("KEEP: refs/", r.stdout)
        self.assertNotIn("KEEP: HEAD", r.stdout)
        git("checkout", "-q", "-b", "side")
        git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--allow-empty", "-m", "y")
        git("checkout", "-q", "-")
        git("tag", "local-only", "side")
        r = self.run_cloud("check", script=script)
        self.assertIn("KEEP: refs/heads/side has commits that no remote branch has", r.stdout)
        self.assertIn("KEEP: refs/tags/local-only has commits", r.stdout)
        self.assertIn(f"WILL KEEP: {self.a}", r.stdout)


class RosterParsing(unittest.TestCase):
    def test_verified_names_parse_like_plain_ones(self):
        import uninstall
        for v in ("", " (verified)"):
            with self.subTest(v=v):
                out = (f"team demo · relay: Reason's (the default)\n  demo{v}  mac    this machine\n"
                       f"  demo{v}  cloud  offline 4m\n  eve{v}   pc     direct 3ms\n")
                r = uninstall.Roster.parse(0, out)
                self.assertEqual((r.status, r.team, r.me), ("in", "demo", ("demo", "mac")))
                self.assertEqual(r.rows, [("demo", "cloud", "offline 4m"), ("eve", "pc", "direct 3ms")])


class Renderer(unittest.TestCase):
    def test_both_languages_embed_the_same_filled_script_and_its_digest(self):
        script, digest = cloud_uninstall_script("demo", "mac", "alice", "all", "normal", "0123456789abcdef")
        for lang in ("en", "zh-CN"):
            text = render_prompt("cloud-uninstall", lang, team_name="demo", mac_machine="mac", relay_name="alice",
                                 level="all", mode="normal", challenge="0123456789abcdef")
            body = re.search(r"<<'AGENT_MESH_UNINSTALL_EOF'\n(.*?\n)AGENT_MESH_UNINSTALL_EOF", text, re.S).group(1)
            self.assertEqual(body, script)
            self.assertIn(digest, text)
            self.assertNotIn("```", text)
            for token in ("[LOCAL]", "[BOT-CLOUD]", "[GROK-UI]", "apply-no-proof", "accept-shared", "force",
                          "rn-cloud.sh fetch"):
                self.assertIn(token, text)
            self.assertIsNone(re.search(r"<(team-name|mac-machine|relay-name|level|mode|challenge|script-sha256|"
                                        r"cloud-script)>", text))
        self.assertEqual(hashlib.sha256(script.encode()).hexdigest(), digest)

    def test_invalid_values_are_refused(self):
        good = dict(team_name="demo", mac_machine="mac", relay_name="bot", level="identity", mode="normal",
                    challenge="0123456789abcdef")
        for key, bad in (("team_name", "a;b"), ("mac_machine", "A"), ("relay_name", "terminal"), ("level", "program"),
                         ("mode", "x"), ("challenge", "xyz"), ("team_name", "a--b")):
            with self.assertRaises(ValueError, msg=(key, bad)):
                render_prompt("cloud-uninstall", **dict(good, **{key: bad}))
        r = subprocess.run([sys.executable, str(ROOT / "scripts/render_prompt.py"), "cloud-uninstall", "--lang", "zh-CN",
                            "--team-name", "demo", "--mac-machine", "mac", "--relay-name", "bot", "--level", "identity",
                            "--mode", "residual", "--challenge", "0123456789abcdef"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('MODE="residual"', r.stdout)
        self.assertIn("偏好的语言", r.stdout)


if __name__ == "__main__":
    unittest.main()
