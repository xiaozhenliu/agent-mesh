import base64
import hashlib
import importlib.util
import io
import json
import os
import pty
import select
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from render_prompt import ROOT


def own_group_with_default_signals():
    os.setpgrp()
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGQUIT, signal.SIG_DFL)


def rn_engine():
    spec = importlib.util.spec_from_file_location("rn_engine", ROOT / "box/rn-engine.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InstallHarness:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "custom homes" / "Alex's & Team"
        self.home.mkdir(parents=True)
        self.bin = self.base / "tools"
        self.bin.mkdir()
        for name in ("dirname", "sed", "head", "awk", "tar", "shasum", "openssl", "mkdir",
                     "chmod", "cp", "date", "grep", "cat", "install", "sleep", "tail", "wc", "mktemp", "rm", "mv",
                     "cmp", "ps", "gzip"):
            found = shutil.which(name)
            if found:
                (self.bin / name).symlink_to(found)
        (self.bin / "python3").symlink_to(sys.executable)
        self.env = {"PATH": str(self.bin), "HOME": str(self.home),
                    "MOCK_OS": "Darwin", "MOCK_ARCH": "arm64", "MOCK_VERSION": "0.12.17", "AGENT_MESH_REGISTER_WAIT": "2",
                    "CALL_LOG": str(self.base / "calls")}
        self.tool("uname", 'if [ "$1" = -s ]; then echo "$MOCK_OS"; else echo "$MOCK_ARCH"; fi')
        self.tool("id", "echo 501")
        self.tool("launchctl", 'echo "launchctl $*" >> "$CALL_LOG"; exit 0')
        self.tool("plutil", "exit 0")
        self.tool("curl", 'echo "curl $*" >> "$CALL_LOG"; case "$*" in *https://aps1-1*|*.compute.reasonmachines.com/*) printf 200 ;; *) exit 73 ;; esac')
        self.git = self.base / "git"
        self.git.mkdir()
        (self.git / "release-commit").write_text("a" * 40 + "\n")
        self.env.update(GIT_STATE=str(self.git), RELEASE_ROOT=str(ROOT))
        self.env["RELEASE_GIT_STUB"] = str(self.tool("release-git", RELEASE_GIT))
        self.tool("git", RELEASE_GIT)

    def tool(self, name, body, path=None):
        path = path or self.bin / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or path.exists():
            path.unlink()
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
        return path

    def engine(self):
        path = self.home / ".local/bin/reason-network"
        self.who = self.base / "who"
        self.who.mkdir(exist_ok=True)
        (self.who / "all.json").write_text(json.dumps(EMPTY_DIRECTORY))
        (self.who / "bot.py").write_text(BOT)
        self.env["MOCK_WHO"] = str(self.who)
        self.tool("reason-network", '[ "$1" = --version ] || echo "rn $*" >> "$CALL_LOG"\n'
                                    'case "$1" in --version) echo "reason-network $MOCK_VERSION" ;; '
                                    'invite) echo "For a cloud agent of yours send it: fake-invite" ;; '
                                    'who) [ ! -f "$MOCK_WHO/fail" ] || exit 1; '
                                    'if [ "$*" = "who --all --json" ]; then cat "$MOCK_WHO/all.json"; '
                                    'elif [ -f "$MOCK_WHO/text" ]; then cat "$MOCK_WHO/text"; fi ;; '
                                    'team) [ ! -f "$MOCK_WHO/team" ] || cat "$MOCK_WHO/team" ;; '
                                    'send) [ "$3" != - ] || exec python3 "$MOCK_WHO/bot.py" "$2" ;; '
                                    '*) exit 0 ;; esac', path)
        cfg = self.home / ".reason/network/config.toml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text('person = "demo"\n')
        return path

    def run_script(self, script="install.sh", *args, root=ROOT, timeout=10):
        return subprocess.run(["/bin/bash", str(root / script), *args], env=self.env,
                              input="", capture_output=True, text=True, timeout=timeout)

    def calls(self):
        path = self.base / "calls"
        return path.read_text() if path.exists() else ""

    def configured(self, relay=None):
        """An installed engine plus a configured relay: env with a webhook, and the current key."""
        self.engine()
        conf = self.home / ".config/agent-relay"
        conf.mkdir(parents=True)
        (conf / "env").write_text(f"RN_BIN={self.home}/.local/bin/reason-network\nPERSON=demo\n"
                                  "WEBHOOK_URL=https://example.invalid/hook\nWEBHOOK_KEY=0123456789\n"
                                  "OUTBOUND_KEY_FILE=~/.config/agent-relay/outbound.key\n")
        if relay:
            self.set_names(relay)
        key = conf / "outbound.key"
        key.write_text("a" * 64 + "\n")
        key.chmod(0o600)
        self.launchd()
        return key

    def launchd(self):
        """A launchctl that remembers whether the relay is loaded and which relay file each bootstrap started."""
        self.state = self.base / "launchd"
        self.state.mkdir(exist_ok=True)
        log = self.home / "Library/Logs/agent-relay.log"
        self.env["MOCK_LAUNCHD"] = str(self.state)
        (self.state / "register.py").write_text(REGISTER)
        self.tool("launchctl", f"""echo "launchctl $*" >> "$CALL_LOG"
S="$MOCK_LAUNCHD"
case "$1" in
  print)
    case "$2" in
      */com.local.agent-relay) [ -f "$S/loaded" ] || exit 113; [ ! -s "$S/loaded" ] || echo "	path = $(cat "$S/loaded")"; [ -f "$S/dead" ] || {{ echo "state = running"; [ -f "$S/nopid" ] || echo "	pid = $(cat "$S/pid")"; }} ;;
    esac ;;
  bootout) rm -f "$S/loaded" ;;
  bootstrap)
    rm -f "$S/dead"
    echo "$$" > "$S/pid"
    if [ -f "$S/fail-bootstrap" ]; then rm -f "$S/fail-bootstrap"; exit 5; fi
    if [ -f "$S/hang" ]; then rm -f "$S/hang"; : > "$S/hanging"; while [ ! -f "$S/go" ]; do /bin/sleep 0.1; done; fi
    printf '%s' "$3" > "$S/loaded"
    python3 -c 'import hashlib, plistlib, sys; p = plistlib.load(open(sys.argv[1], "rb"))["ProgramArguments"][1]; print(hashlib.sha256(open(p, "rb").read()).hexdigest())' "$3" >> "$S/started"
    if [ -f "$S/die" ]; then rm -f "$S/die"; : > "$S/dead"; fi
    if [ -f "$S/no-up" ]; then rm -f "$S/no-up"; else echo "agent-relay up" >> "{log}"; python3 "$S/register.py"; fi ;;
esac
exit 0""")
        self.tool("sleep", "exit 0")

    def started(self):
        path = self.state / "started"
        return path.read_text().split() if path.exists() else []

    def loaded(self):
        return (self.state / "loaded").exists()

    def cloud_box(self):
        """A cloud directory with this checkout's rn-cloud-setup.sh, a fake download and a fake engine."""
        self.box = self.base / "workspace" / "box"
        self.box.mkdir(parents=True)
        shutil.copy(ROOT / "box/rn-engine.py", self.box / "rn-engine.py")
        self.mock = self.base / "mock"
        self.mock.mkdir()
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as tar:
            item = tarfile.TarInfo("reason-network")
            item.mode, item.size = 0o755, len(CLOUD_ENGINE)
            tar.addfile(item, io.BytesIO(CLOUD_ENGINE.encode()))
        (self.mock / "e.tgz").write_bytes(archive.getvalue())
        setup = (ROOT / "box/rn-cloud-setup.sh").read_text()
        line = next(l for l in setup.splitlines() if "/x86_64-unknown-linux-musl) echo " in l)
        fake = hashlib.sha256(archive.getvalue()).hexdigest()
        (self.box / "rn-cloud-setup.sh").write_text(setup.replace(line, line.rsplit(" echo ", 1)[0] + f" echo {fake} ;;"))
        for name in ("cut", "gzip"):
            found = shutil.which(name)
            if found and not (self.bin / name).exists():
                (self.bin / name).symlink_to(found)
        self.tool("curl", 'echo "curl $*" >> "$CALL_LOG"; out=""; prev=""\n'
                          'for a in "$@"; do [ "$prev" != -o ] || out="$a"; prev="$a"; done\n'
                          'case "$*" in *.sha256*) echo "fakesum  e.tgz" > "$out" ;; *) cat "$MOCK_BOX/e.tgz" > "$out" ;; esac')
        self.tool("sha256sum", 'python3 -c "import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], \'rb\').read()).hexdigest() + \'  \' + sys.argv[1])" "$1"')
        real_sed, real_mv = shutil.which("sed"), shutil.which("mv")
        self.tool("sed", f'[ "$1" != -i ] || exit 0\nexec "{real_sed}" "$@"')
        self.tool("mv", f'eval "last=\\${{$#}}"\n'
                        f'if [ -f "$MOCK_BOX/fail-mv" ] && [ "$last" = "$RN_CLOUD_BASE/person" ]; then exit 1; fi\n'
                        f'exec "{real_mv}" "$@"')
        self.builds = self.mock / "builds.json"
        self.pin_builds({"0.12.17": CLOUD_ENGINE.encode()})
        self.env.update(MOCK_OS="Linux", MOCK_ARCH="x86_64", RN_CLOUD_BASE=str(self.box), MOCK_BOX=str(self.mock),
                        RN_PINNED_BUILDS_FILE=str(self.builds))

    def pin_builds(self, builds):
        target = rn_engine().linux_target()
        self.builds.write_text(json.dumps({v: {target: hashlib.sha256(data).hexdigest()} for v, data in builds.items()}))

    def cloud_state(self, team=None, rows=()):
        (self.box / "state").mkdir(exist_ok=True)
        db = sqlite3.connect(self.box / "state" / "network.db")
        db.executescript(SESSIONS)
        if team is not None:
            db.execute("INSERT OR REPLACE INTO kv VALUES ('team', ?)", (team,))
        for name, key in rows:
            db.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?, 'cli', '/', NULL, NULL, NULL, 'pull', 1)",
                       (name, key))
        db.commit()
        db.close()

    def joined_before(self, team=None, me="demo  cloud"):
        self.cloud_state(json.dumps({"id": team or TEAM_ID, "name": "demo"}))
        (self.mock / "joined").write_text("")
        (self.mock / "team").write_text(f"team demo · relay: n0 public relays\n  demo  mac    direct 4ms\n"
                                        f"  {me}  this machine\n")

    def setup(self, *args):
        return subprocess.run(["/bin/bash", str(self.box / "rn-cloud-setup.sh"), *args], env=self.env, input="",
                              capture_output=True, text=True, timeout=30)

    def names_files(self):
        return {f.name: f.read_text() for f in self.box.iterdir()
                if f.is_file() and f.name not in ("rn-cloud-setup.sh", "rn-engine.py")}

    def dsh_installed(self):
        self.engine()
        app = self.home / "Applications/DeepSeek Harness.app/Contents/MacOS/DeepSeek Harness"
        self.tool("app", "echo 'ℹ pass 7'", app)
        patch = self.home / ".dsh/cordis.patch.yml"
        patch.parent.mkdir(parents=True)
        own = "# === reason-network MCP BEGIN ===\nold\n# === reason-network MCP END ===\n"
        patch.write_text("- before: 1\n" + own + "- after: 2\n")
        result = self.run_script("dsh/mac-install.sh", "demo")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        plugin = self.home / ".dsh/plugins/reason-network-bridge.mjs"
        self.assertTrue(plugin.is_file())
        patch.write_text(patch.read_text() + "- added later: 3\n")
        return patch, own, plugin

    def relay_files(self):
        return (self.home / ".local/lib/agent-mesh/agent_relay.py",
                self.home / "Library/LaunchAgents/com.local.agent-relay.plist")

    def record(self):
        path = self.home / ".local/lib/agent-mesh/deployed.json"
        return json.loads(path.read_text()) if path.exists() else None

    def write_record(self, data):
        path = self.home / ".local/lib/agent-mesh/deployed.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def mark(self, repo, field, state, lang="en"):
        subprocess.run([sys.executable, str(repo / "scripts/deploy_state.py"), "mark", field, state, "--lang", lang],
                       env=self.env, check=True)

    def repo(self):
        repo = self.base / "repo"
        shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns("__pycache__", ".git"))
        (repo / "check.sh").write_text("#!/bin/bash\necho 'RESULT ok'\n")
        (self.git / "head").write_text("a" * 40 + "\n")
        (self.git / "upstream").write_text("a" * 40 + "\n")
        self.env.update(GIT_STATE=str(self.git), RELEASE_ROOT=str(repo), PYTHONDONTWRITEBYTECODE="1")
        self.tool("git", GIT_STUB)
        return repo

    def installed(self, record=True, relay=None):
        self.configured(relay)
        repo = self.repo()
        result = self.run_script("install.sh", "--restart-relay", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        if record:
            for field in ("cloud", "cloud_setup", "prompt", "cloud_engine"):
                self.mark(repo, field, "confirmed")
        (self.base / "calls").unlink()
        return repo

    def call_lines(self):
        return self.calls().splitlines()

    def service_changes(self):
        return [line for line in self.call_lines() if line.startswith(("launchctl bootout", "launchctl bootstrap"))]

    def run_tty(self, root, *args, answers=(), script="update.sh"):
        master, slave = pty.openpty()
        try:
            os.write(master, "".join(answer + "\n" for answer in answers).encode() + b"\x04")
            return subprocess.run(["/bin/bash", str(root / script), *args], env=self.env, stdin=slave,
                                  capture_output=True, text=True, timeout=60)
        finally:
            os.close(slave)
            os.close(master)

    def run_tty_until(self, root, *args, answers=(), wait_for="", sig=signal.SIGTERM):
        master, slave = pty.openpty()
        try:
            os.write(master, "".join(answer + "\n" for answer in answers).encode())
            proc = subprocess.Popen(["/bin/bash", str(root / "update.sh"), *args], env=self.env, stdin=slave,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    preexec_fn=own_group_with_default_signals)
            seen, deadline = b"", time.time() + 90
            while wait_for.encode() not in seen and time.time() < deadline:
                ready, _, _ = select.select([proc.stderr], [], [], 0.5)
                if ready:
                    seen += os.read(proc.stderr.fileno(), 4096)
            if wait_for.encode() not in seen:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate(timeout=30)
                self.fail(f"update.sh never printed {wait_for!r}; stderr so far:\n{seen.decode()}")
            time.sleep(1)
            os.killpg(proc.pid, sig)
            out, err = proc.communicate(timeout=60)
            return proc.returncode, out.decode(), (seen + err).decode()
        finally:
            os.close(slave)
            os.close(master)

    def sha(self, path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def release_tag(self, repo):
        return "v" + (repo / "VERSION").read_text().strip()

    def cloud_reply(self, repo, commit="a" * 40, fetch_sum=None, engine=True, tag=None):
        lines = ["Done. Output:", "```", "origin: https://github.com/example/agent-mesh.git"]
        if engine:
            build = next(iter(rn_engine().PINNED_BUILDS["0.12.17"].values()))
            lines += ["engine: reason-network 0.12.17 running (daemon pid 4321, started 2026-11-12T08:00:00Z, "
                      "binary replaced 2026-11-12T07:59:58Z)",
                      f"engine: pinned build {build} at box/bin/engine/reason-network", "engine: launcher ok"]
        r = "agent-mesh-result"
        lines += [f"{r} commit {commit}",
                  f"{r} sum {self.sha(repo / 'box/rn-cloud.sh')}  box/rn-cloud.sh",
                  f"{r} sum {fetch_sum or self.sha(repo / 'box/rn-fetch.py')}  box/rn-fetch.py",
                  f"{r} sum {self.sha(repo / 'box/rn-cloud-setup.sh')}  box/rn-cloud-setup.sh",
                  f"{r} sum {self.sha(repo / 'box/rn-engine.py')}  box/rn-engine.py",
                  f"{r} DONE {tag or self.release_tag(repo)}", "```"]
        return lines + ["."]

    def cloud_reply_check(self, repo, text, *extra):
        return subprocess.run([sys.executable, str(repo / "scripts/deploy_state.py"), "cloud-reply",
                               "--commit", "a" * 40, "--tag", self.release_tag(repo), *extra], input=text,
                              env=self.env, capture_output=True, text=True)

    def paused_after_cloud(self):
        repo = self.installed(record=False)
        result = self.run_tty(repo, "--no-pull", "--manual", answers=["y", "y", *self.cloud_reply(repo), "n"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("The routine is still paused: resume it in Grok Bot", result.stdout)
        self.assertTrue(self.loaded())
        data = self.record()
        self.assertEqual((data["txn"]["stage"], data["cloud"]["state"]), ("cloud_done", "confirmed"))
        self.assertEqual(data["cloud_engine"]["state"], "confirmed")
        (self.base / "calls").unlink()
        return repo

    def cloud_changed(self, repo, name="box/rn-cloud.sh"):
        with open(repo / name, "a") as stream:
            stream.write("\n")

    def good_reply(self, repo, **kwargs):
        return "\n".join(["agent-mesh-result request {req}"] + self.cloud_reply(repo, **kwargs)[:-1]) + "\n"

    def bot_replies(self, repo=None, body=None, **conf):
        conf["body"] = body if body is not None else self.good_reply(repo)
        (self.who / "bot.json").write_text(json.dumps(conf))

    def bot_silent(self):
        (self.who / "bot.json").unlink(missing_ok=True)

    def sent(self):
        path = self.who / "sent"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def put_reply(self, req, body, sender="demo/bot"):
        db = sqlite3.connect(self.home / ".reason/network/network.db")
        db.execute(BOT_TABLE)
        db.execute("INSERT INTO messages VALUES (?, ?, 'demo/terminal', ?, ?, 0)",
                   (os.urandom(8).hex(), sender, body.replace("{req}", req), int(time.time() * 1000)))
        db.commit()
        db.close()

    def digest(self, repo, field):
        return subprocess.run([sys.executable, str(repo / "scripts/deploy_state.py"), "digest", field],
                              env=self.env, capture_output=True, text=True, check=True).stdout.strip()

    def first_update_pending(self, repo, lang="en"):
        import messages
        self.mark(repo, "prompt", "confirmed", lang)
        data = self.record()
        del data["cloud"]["release"]
        self.write_record(data)
        self.cloud_changed(repo)
        self.env["AGENT_MESH_REPLY_WAIT"] = "5"
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(messages.t("update.sent_first", "demo/bot", lang=lang), result.stdout)
        self.assertIn(messages.t("update.timeout_first", lang=lang), result.stdout)
        self.assertNotIn(messages.t("update.timeout", lang=lang), result.stdout)
        [message] = self.sent()
        self.assertIn("bash -s <<'AGENT_MESH_UPDATE'", message["body"])
        return self.record()["request"]["req"]

    def env_file(self):
        return self.home / ".config/agent-relay/env"

    def set_names(self, relay, extra=""):
        env = self.env_file()
        keep = [line for line in env.read_text().splitlines()
                if not line.startswith(("LISTEN_NAME=", "CLOUD_NAME=", "INBOX_NAME="))]
        names = [f"LISTEN_NAME={relay}", f"CLOUD_NAME={relay}-cloud", f"INBOX_NAME={relay}-inbox"]
        env.write_text("\n".join(keep + names) + "\n" + extra)

    def fresh(self):
        self.engine()
        self.launchd()
        self.env["AGENT_MESH_REGISTER_WAIT"] = "2"
        real_sed = shutil.which("sed")
        if subprocess.run([real_sed, "--version"], capture_output=True).returncode == 0:
            self.tool("sed", f'if [ "$1" = -i ] && [ "$2" = "" ]; then shift 2; exec "{real_sed}" -i "$@"; fi\n'
                             f'exec "{real_sed}" "$@"')

    def sessions(self, rows):
        path = self.home / ".reason/network/network.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        db.executescript(SESSIONS)
        for name, key, last_seen in rows:
            db.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?, 'cli', '/', NULL, NULL, NULL, 'pull', ?)",
                       (name, key, last_seen))
        db.commit()
        db.close()
        return path

    def webhook(self, root=ROOT):
        master, slave = pty.openpty()
        try:
            proc = subprocess.Popen(["/bin/bash", str(root / "install.sh"), "--webhook"], env=self.env, stdin=slave,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            os.write(master, b"https://example.invalid/hook\n")
            out_fd, err_fd = proc.stdout.fileno(), proc.stderr.fileno()
            got = {out_fd: b"", err_fd: b""}
            open_fds, sent, deadline = set(got), False, time.time() + 60
            while open_fds and time.time() < deadline:
                ready, _, _ = select.select(list(open_fds) + [master], [], [], 0.2)
                for fd in ready:
                    try:
                        chunk = os.read(fd, 4096)
                    except OSError:
                        chunk = b""
                    if fd == master:
                        continue
                    if not chunk:
                        open_fds.discard(fd)
                    got[fd] += chunk
                if not sent and b"(not echoed): " in got[err_fd]:
                    os.write(master, b"0123456789\n\x04")
                    sent = True
            proc.wait(timeout=10)
            proc.stdout.close()
            proc.stderr.close()
            return subprocess.CompletedProcess(proc.args, proc.returncode, got[out_fd].decode(), got[err_fd].decode())
        finally:
            os.close(slave)
            os.close(master)

    def rn_calls(self):
        return [line for line in self.call_lines() if line.startswith("rn ")]

    def state_cmd(self, *args, root=ROOT):
        return subprocess.run([sys.executable, str(root / "scripts/deploy_state.py"), *args], env=self.env,
                              capture_output=True, text=True)

    def fake_daemon(self):
        home = self.home / ".reason/network"
        home.mkdir(parents=True, exist_ok=True)
        code = ("import fcntl, os, sys, time\n"
                "f = open(sys.argv[1], 'a+')\n"
                "fcntl.flock(f, fcntl.LOCK_EX)\n"
                "f.truncate(0); f.write(str(os.getpid())); f.flush()\n"
                "print('locked', flush=True)\n"
                "time.sleep(300)\n")
        proc = subprocess.Popen([sys.executable, "-c", code, str(home / "daemon.lock")], stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (proc.kill(), proc.wait(), proc.stdout.close()))
        self.assertEqual(proc.stdout.readline().strip(), "locked")
        return proc

    def write_marker(self, name, pid=None):
        marker = self.home / "Library/Application Support/agent-relay/registered"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"name": name, "pid": pid or os.getpid(), "at": int(time.time())}))
        return marker


TEAM_ID = "5f0c2a9be14d4c7aa3d1e2f30415a6b7"
OTHER_TEAM_ID = "0123456789abcdef0123456789abcdef"


def invite(team=TEAM_ID, cut=None):
    data = bytes.fromhex(team) + bytes(range(16)) + bytes([4]) + b"demo" + bytes([0, 0, 0]) + bytes(range(32))
    ticket = "tincan1" + base64.urlsafe_b64encode(data[:cut]).decode().rstrip("=")
    return f"join my Reason Network team as demo: https://reason.network/join/#{ticket}"


CLOUD_ENGINE = """#!/bin/sh
[ "$1" != --version ] || { echo "reason-network ${MOCK_CLOUD_VERSION:-0.12.17}"; exit 0; }
echo "rn $*" >> "$CALL_LOG"
case "$1" in
  join) if [ -f "$MOCK_BOX/joined" ]; then echo "Error: already in team 'demo' (with demo/mac); leave it first"; exit 1; fi
        : > "$MOCK_BOX/joined"; echo "joined team demo as demo/cloud" ;;
  team) [ ! -f "$MOCK_BOX/team" ] || cat "$MOCK_BOX/team" ;;
  doctor) echo "peers: 1 connected" ;;
esac
exit 0
"""


EMPTY_DIRECTORY = {"team": "demo", "person": "demo", "machine": "mac", "machines": [], "sessions": []}

BOT_TABLE = ("CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, sender TEXT, recipient TEXT, body TEXT, "
             "sent_at INTEGER, held INTEGER)")

BOT = r'''import json, os, re, sqlite3, sys, time
from pathlib import Path
who = Path(os.environ["MOCK_WHO"])
if (who / "send-fail").exists():
    sys.exit(1)
body = sys.stdin.read()
with open(who / "sent", "a") as stream:
    stream.write(json.dumps({"to": sys.argv[1], "body": body}) + "\n")
conf_path = who / "bot.json"
if not conf_path.exists():
    sys.exit(0)
conf = json.loads(conf_path.read_text())
found = re.search(r"^agent-mesh update \S+ \S+ ([0-9a-f]{16})$|REQ=([0-9a-f]{16})$", body, re.M)
req = conf.get("req") or found.group(1) or found.group(2)
db = sqlite3.connect(Path(os.environ["HOME"]) / ".reason/network/network.db")
db.execute(TABLE)
db.execute("INSERT INTO messages VALUES (?, ?, 'demo/terminal', ?, ?, ?)",
           (os.urandom(8).hex(), conf.get("sender", "demo/bot"), conf["body"].replace("{req}", req),
            int(time.time() * 1000) - conf.get("ago_ms", 0), conf.get("held", 0)))
db.commit()
'''.replace("TABLE", repr(BOT_TABLE))

SESSIONS = """CREATE TABLE IF NOT EXISTS sessions (name TEXT PRIMARY KEY, key TEXT NOT NULL, agent TEXT NOT NULL,
cwd TEXT NOT NULL, repo TEXT, branch TEXT, doing TEXT, tier TEXT NOT NULL, last_seen INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);"""

REGISTER = f'''import json, os, re, sqlite3, subprocess, sys, time
from pathlib import Path
home = Path(os.environ["HOME"])
name = os.environ.get("MOCK_REGISTER", "")
if name == "-":
    sys.exit(0)
skip = Path(os.environ["MOCK_LAUNCHD"]) / "noreg-when"
if skip.exists():
    out = subprocess.run([str(home / ".local/bin/reason-network"), "--version"], capture_output=True, text=True).stdout
    if skip.read_text().strip() in out.split():
        sys.exit(0)
listen = "bot"
env = home / ".config/agent-relay/env"
if env.exists():
    for line in env.read_text().splitlines():
        if line.startswith("LISTEN_NAME="):
            listen = line.split("=", 1)[1]
db = home / ".reason/network/network.db"
db.parent.mkdir(parents=True, exist_ok=True)
if not os.environ.get("MOCK_NO_DB"):
    con = sqlite3.connect(db)
    con.executescript("""{SESSIONS}""")
    con.execute("INSERT OR IGNORE INTO sessions VALUES (?, ?, 'cli', '/', NULL, NULL, NULL, 'pull', ?)",
                (name or listen, "cli:" + listen, int(time.time() * 1000)))
    con.commit()
if os.environ.get("MOCK_NO_MARKER"):
    sys.exit(0)
person = re.search(r'^person = "(.*)"', (home / ".reason/network/config.toml").read_text(), re.M).group(1)
marker = home / "Library/Application Support/agent-relay/registered"
marker.parent.mkdir(parents=True, exist_ok=True)
pid = int((Path(os.environ["MOCK_LAUNCHD"]) / "pid").read_text())
marker.write_text(json.dumps({{"name": f"{{person}}/{{name or listen}}", "pid": pid, "at": int(time.time())}}))
'''

RELEASE_GIT = r'''[ "$1" = -C ] && shift 2
S="$GIT_STATE"
case "$*" in
  "init -q --bare "*) [ $# = 4 ] ;;
  "fetch -q --depth 1 --no-tags "*) echo "git fetch $6 $7" >> "$CALL_LOG"
    [ ! -f "$S/release-fail" ] || { echo "fatal: couldn't find remote ref $7" >&2; exit 128; } ;;
  "rev-parse --verify -q refs/tags/"*) cat "$S/release-commit" ;;
  "cat-file blob "*) f="${3#*:}"; if [ -f "$S/release/$f" ]; then cat "$S/release/$f"; else cat "$RELEASE_ROOT/$f"; fi ;;
  *) echo "unexpected git call $*" >> "$CALL_LOG"; exit 99 ;;
esac'''

GIT_STUB = r'''[ "$1" = -C ] && shift 2
echo "git $*" >> "$CALL_LOG"
S="$GIT_STATE"
case "$*" in
  "rev-parse --is-inside-work-tree") echo true ;;
  "rev-parse --abbrev-ref @{u}") echo origin/main ;;
  "rev-parse @{u}") cat "$S/upstream" ;;
  "rev-parse HEAD") cat "$S/head" ;;
  "rev-parse --short HEAD") head -c 7 "$S/head"; echo ;;
  "status --porcelain --untracked-files=no") [ ! -f "$S/dirty" ] || cat "$S/dirty" ;;
  "status --porcelain -- box") [ ! -f "$S/boxdirty" ] || cat "$S/boxdirty" ;;
  "pull --ff-only") [ ! -f "$S/pull-fail" ] || exit 1; [ ! -f "$S/pull.sh" ] || /bin/sh "$S/pull.sh"; cp "$S/upstream" "$S/head" ;;
  "init -q --bare "*|"fetch -q --depth 1 --no-tags "*|"rev-parse --verify -q refs/tags/"*|"cat-file blob "*)
    exec /bin/sh "$RELEASE_GIT_STUB" "$@" ;;
  *) echo "unexpected git $*" >> "$CALL_LOG"; exit 99 ;;
esac'''
