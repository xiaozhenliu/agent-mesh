"""Engine upgrade checks with a fake Reason Network that runs a real daemon process; never contact real services."""
import hashlib
import importlib.util
import io
import json
import os
import pty
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

import test_install_portability as tip
from render_prompt import ROOT

OLD, NEW = "0.10.7", "0.12.17"

FAKE = r'''#!@PYTHON@
import fcntl, os, signal, sqlite3, subprocess, sys, time
from pathlib import Path
VERSION = "@VERSION@"
home = Path(os.environ.get("REASON_NETWORK_HOME") or Path(os.environ["HOME"]) / ".reason/network")
ctl = Path(os.environ.get("FAKE_RN_CTL") or home)


def flag(name):
    return (ctl / f"{name}-{VERSION}").exists()


def held():
    try:
        f = open(home / "daemon.lock", "rb")
    except OSError:
        return None
    with f:
        try:
            fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            text = f.read().strip()
            return int(text) if text.isdigit() else None
        fcntl.flock(f, fcntl.LOCK_UN)
        return None


def daemon():
    home.mkdir(parents=True, exist_ok=True)
    if flag("nostart"):
        return
    f = open(home / "daemon.lock", "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return
    f.seek(0)
    f.truncate()
    f.write(str(os.getpid()))
    f.flush()
    con = sqlite3.connect(home / "network.db")
    con.execute("CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
    if tuple(map(int, VERSION.split("."))) >= (0, 12, 0):
        con.execute("CREATE TABLE IF NOT EXISTS terminals (name TEXT)")
    con.commit()
    con.close()
    if flag("noexit"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    with open(ctl / "daemons", "a") as log:
        log.write(f"{os.getpid()} {VERSION}\n")
    while True:
        time.sleep(1)


def ensure():
    if held():
        return True
    subprocess.Popen([sys.executable, __file__, "daemon", "--service"], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        if held():
            return True
        time.sleep(0.1)
    print("Error: the daemon did not start", file=sys.stderr)
    return False


args = sys.argv[1:]
cmd = args[0] if args else ""
if cmd == "--version":
    print(f"reason-network {VERSION}")
    sys.exit(0)
with open(ctl / "calls", "a") as log:
    log.write(f"{VERSION} {' '.join(args)}\n")
if cmd == "daemon":
    daemon()
    sys.exit(0)
if cmd == "stop":
    if flag("slowafterstop"):
        (ctl / "stopped").write_text("")
    pid = held()
    if pid:
        os.kill(pid, signal.SIGTERM)
    sys.exit(0)
if flag("slow") or (flag("slowafterstop") and (ctl / "stopped").exists()):
    time.sleep(20)
if not ensure():
    sys.exit(1)
if cmd == "team":
    if flag("teamfail"):
        sys.exit(1)
    print("team demo · relay: Reason's (the default)")
    print("  demo  mac    this machine")
    if not flag("nocloud"):
        print("  demo  cloud  direct 4ms")
elif cmd == "who":
    if flag("whofail"):
        print("Error: who failed", file=sys.stderr)
        sys.exit(1)
    print("  bot  cli  ~  idle")
elif cmd == "send":
    con = sqlite3.connect(home / "network.db")
    con.execute("INSERT INTO messages VALUES (?, ?)", (args[1], args[2]))
    con.commit()
sys.exit(0)
'''


def fake_engine(version):
    return FAKE.replace("@PYTHON@", sys.executable).replace("@VERSION@", version)


def tarball(version):
    data = fake_engine(version).encode()
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        item = tarfile.TarInfo("reason-network")
        item.mode, item.size = 0o755, len(data)
        tar.addfile(item, io.BytesIO(data))
    return archive.getvalue()


def pin(script, target, digest):
    text = script.read_text()
    line = next(l for l in text.splitlines() if f"v{NEW}/{target}) echo " in l)
    script.write_text(text.replace(line, line.rsplit(" echo ", 1)[0] + f" echo {digest} ;;"))


def rn_engine():
    spec = importlib.util.spec_from_file_location("rn_engine", ROOT / "box/rn-engine.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeEngineMixin:
    def setup_ctl(self, base, env):
        self.ctl = Path(base) / "rnctl"
        self.ctl.mkdir(exist_ok=True)
        env["FAKE_RN_CTL"] = str(self.ctl)
        env.setdefault("RN_ENGINE_STOP_TIMEOUT", "3")
        env.setdefault("RN_ENGINE_START_TIMEOUT", "4")
        self.addCleanup(self.kill_daemons)

    def kill_daemons(self):
        path = self.ctl / "daemons"
        if not path.exists():
            return
        for line in path.read_text().split("\n"):
            if line.strip():
                try:
                    os.kill(int(line.split()[0]), signal.SIGKILL)
                except (OSError, ValueError):
                    pass

    def put_engine(self, path, version):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(fake_engine(version))
        tmp.chmod(0o755)
        os.replace(tmp, path)

    def flag(self, name, version):
        (self.ctl / f"{name}-{version}").write_text("")

    def unflag(self, name, version):
        (self.ctl / f"{name}-{version}").unlink(missing_ok=True)

    def daemon(self, home):
        return rn_engine().daemon_pid(home)

    def daemon_version(self, home):
        pid = self.daemon(home)
        if pid is None:
            return None
        for line in reversed((self.ctl / "daemons").read_text().splitlines()):
            if line.split()[0] == str(pid):
                return line.split()[1]
        return "?"

    def version(self, binary):
        return subprocess.run([str(binary), "--version"], capture_output=True, text=True).stdout.split()[1]

    def start(self, binary, home):
        env = dict(os.environ, REASON_NETWORK_HOME=str(home), FAKE_RN_CTL=str(self.ctl))
        subprocess.run([str(binary), "team"], env=env, capture_output=True, check=True)
        return self.daemon(home)

    def send(self, binary, home, mid, body):
        env = dict(os.environ, REASON_NETWORK_HOME=str(home), FAKE_RN_CTL=str(self.ctl))
        subprocess.run([str(binary), "send", mid, body], env=env, capture_output=True, check=True)

    def messages(self, home):
        con = sqlite3.connect(Path(home) / "network.db")
        try:
            return dict(con.execute("SELECT id, body FROM messages"))
        finally:
            con.close()


class EngineCommands(FakeEngineMixin, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.env = dict(os.environ)
        self.setup_ctl(self.base, self.env)
        self.home = self.base / "state"
        self.home.mkdir()
        self.bin = self.base / "bin/reason-network"
        self.put_engine(self.bin, OLD)
        self.new = self.base / "new/reason-network"
        self.put_engine(self.new, NEW)
        self.txn = self.base / "engine-upgrade.json"

    def engine(self, *args, timeout=60):
        return subprocess.run([sys.executable, str(ROOT / "box/rn-engine.py"), *args], env=self.env,
                              capture_output=True, text=True, timeout=timeout)

    def switch(self, *extra):
        return self.engine("switch", "--home", str(self.home), "--bin", str(self.bin), "--new", str(self.new),
                           "--pinned", NEW, "--backup", str(self.base / "backup"), "--txn", str(self.txn), *extra)

    def test_compare_orders_versions_and_rejects_garbage(self):
        eng = rn_engine()
        self.assertEqual(eng.compare(OLD, NEW), "older")
        self.assertEqual(eng.compare(NEW, NEW), "same")
        self.assertEqual(eng.compare("0.13.0", NEW), "newer")
        self.assertEqual(eng.compare("0.12.9", "0.12.17"), "older")
        for bad in ("", "garbage", "0.12", "v0.12.17", "0.12.17-rc1"):
            self.assertEqual(eng.compare(bad, NEW), "unknown", bad)

    def test_started_reads_the_process_start_on_the_wall_clock(self):
        before = time.time()
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        after = time.time()
        start = rn_engine().started(proc.pid)
        slack = 0.1 if Path(f"/proc/{proc.pid}/stat").exists() else 1.0
        self.assertGreaterEqual(start, before - slack)
        self.assertLessEqual(start, after + slack)

    def test_daemon_pid_follows_the_lock_not_the_file(self):
        self.assertIsNone(self.daemon(self.home))
        pid = self.start(self.bin, self.home)
        self.assertIsNotNone(pid)
        self.assertEqual(self.engine("pid", "--home", str(self.home)).stdout.strip(), str(pid))
        os.kill(pid, signal.SIGKILL)
        deadline = time.time() + 5
        while self.daemon(self.home) and time.time() < deadline:
            time.sleep(0.1)
        self.assertEqual((self.home / "daemon.lock").read_text(), str(pid))
        self.assertIsNone(self.daemon(self.home))
        self.assertEqual(self.engine("pid", "--home", str(self.home)).stdout.strip(), "")

    def test_info_tells_a_stale_daemon_from_a_fresh_one(self):
        out = self.engine("info", "--home", str(self.home), "--bin", str(self.bin)).stdout
        self.assertTrue(out.strip().endswith("state=none"), out)
        self.start(self.bin, self.home)
        time.sleep(1.1)
        self.assertTrue(self.engine("info", "--home", str(self.home), "--bin", str(self.bin)).stdout.strip()
                        .endswith("state=fresh"))
        time.sleep(1.1)
        self.put_engine(self.bin, NEW)
        out = self.engine("info", "--home", str(self.home), "--bin", str(self.bin)).stdout
        self.assertTrue(out.strip().endswith("state=stale"), out)
        status = self.engine("status", "--home", str(self.home), "--bin", str(self.bin))
        self.assertEqual(status.returncode, 1)
        self.assertIn("(stale)", status.stdout)
        restart = self.engine("restart", "--home", str(self.home), "--bin", str(self.bin))
        self.assertEqual(restart.returncode, 0, restart.stdout + restart.stderr)
        self.assertRegex(restart.stdout, rf"engine: reason-network {NEW} running \(daemon pid \d+, started ")
        self.assertEqual(self.daemon_version(self.home), NEW)

    def test_switch_backs_up_replaces_and_verifies(self):
        old_pid = self.start(self.bin, self.home)
        self.send(self.bin, self.home, "m1", "before")
        result = self.switch("--keep", "relay_was_loaded=1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.version(self.bin), NEW)
        self.assertNotEqual(self.daemon(self.home), old_pid)
        self.assertEqual(self.daemon_version(self.home), NEW)
        backup = self.base / "backup"
        self.assertEqual(oct(backup.stat().st_mode & 0o777), "0o700")
        self.assertEqual(self.version(backup / "reason-network"), OLD)
        con = sqlite3.connect(backup / "state/network.db")
        self.assertEqual(con.execute("SELECT body FROM messages").fetchall(), [("before",)])
        con.close()
        self.assertFalse((backup / "state/daemon.lock").exists())
        data = json.loads(self.txn.read_text())
        self.assertEqual((data["stage"], data["old_version"], data["machines"]), ("verified", OLD, ["cloud", "mac"]))
        self.assertEqual(self.engine("txn-get", "--txn", str(self.txn), "relay_was_loaded").stdout.strip(), "1")
        self.assertEqual(oct(self.txn.stat().st_mode & 0o777), "0o600")
        self.assertRegex(result.stdout, rf"engine: reason-network {NEW} running \(daemon pid \d+, started ")
        self.assertEqual(self.engine("rollback", "--txn", str(self.txn)).returncode, 0)
        self.assertEqual(self.version(self.bin), OLD)
        self.assertEqual(self.daemon_version(self.home), OLD)
        self.assertIn("m1", self.messages(self.home))

    def test_switch_rolls_back_when_the_new_daemon_does_not_start_and_keeps_new_mail(self):
        self.start(self.bin, self.home)
        self.send(self.bin, self.home, "m1", "before")
        self.flag("nostart", NEW)
        result = self.switch()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("reason-network team failed", result.stdout)
        self.assertIn("rolled back; the state directory was not restored", result.stdout)
        self.assertEqual(self.version(self.bin), OLD)
        self.assertEqual(self.daemon_version(self.home), OLD)
        self.assertEqual(json.loads(self.txn.read_text())["stage"], "rolled_back")
        self.assertEqual(self.engine("rollback", "--txn", str(self.txn)).returncode, 0)

    def test_switch_rolls_back_when_a_machine_disappears_or_who_fails(self):
        self.start(self.bin, self.home)
        self.flag("nocloud", NEW)
        result = self.switch()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("no longer lists machine(s) cloud", result.stdout)
        self.assertEqual(self.version(self.bin), OLD)

    def test_new_mail_received_on_the_new_engine_survives_a_rollback(self):
        self.start(self.bin, self.home)
        self.assertEqual(self.switch().returncode, 0)
        self.send(self.bin, self.home, "m2", "after the switch")
        self.assertEqual(self.engine("rollback", "--txn", str(self.txn)).returncode, 0)
        self.assertEqual(self.version(self.bin), OLD)
        self.assertEqual(self.messages(self.home), {"m2": "after the switch"})

    def test_a_daemon_that_ignores_stop_is_ended_and_the_switch_goes_on(self):
        self.flag("noexit", OLD)
        old = self.start(self.bin, self.home)
        result = self.switch()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"the daemon (pid {old}) is still running after reason-network stop; sending SIGTERM", result.stdout)
        self.assertIn("sending SIGKILL", result.stdout)
        self.assertEqual(self.daemon_version(self.home), NEW)

    def test_a_failed_who_on_the_new_engine_rolls_back(self):
        self.start(self.bin, self.home)
        self.flag("whofail", NEW)
        result = self.switch()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("reason-network who failed", result.stdout)
        self.assertEqual(self.version(self.bin), OLD)
        self.assertEqual(self.daemon_version(self.home), OLD)

    def test_switch_reports_an_unconfirmed_rollback_with_manual_steps_that_work(self):
        odd = self.base / "odd homes" / "Alex's & Team"
        self.home, self.bin = odd / "state", odd / "bin/reason-network"
        self.home.mkdir(parents=True)
        self.put_engine(self.bin, OLD)
        backup = odd / "backup"
        self.start(self.bin, self.home)
        self.flag("nostart", NEW)
        self.flag("nostart", OLD)
        result = self.engine("switch", "--home", str(self.home), "--bin", str(self.bin), "--new", str(self.new),
                             "--pinned", NEW, "--backup", str(backup), "--txn", str(self.txn))
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("Restore it by hand", result.stdout)
        self.assertEqual(json.loads(self.txn.read_text())["stage"], "replaced")
        steps = dict(re.findall(r"^  ([1-4])\. (.*)$", result.stdout, re.M))
        self.put_engine(self.bin, NEW)
        self.unflag("nostart", OLD)
        for step in ("1", "3"):
            done = subprocess.run(["/bin/bash", "-c", steps[step]], env=self.env, capture_output=True, text=True,
                                  timeout=60)
            self.assertEqual(done.returncode, 0, steps[step] + done.stdout + done.stderr)
        self.assertEqual(self.version(self.bin), OLD)
        self.assertEqual(self.daemon_version(self.home), OLD)
        self.assertEqual(list(self.bin.parent.glob("*.restore")), [])

    def test_switch_refuses_a_new_binary_of_another_version_and_changes_nothing(self):
        self.put_engine(self.new, "0.12.16")
        result = self.switch()
        self.assertEqual(result.returncode, 1)
        self.assertIn("nothing was switched", result.stdout)
        self.assertEqual(self.version(self.bin), OLD)
        self.assertFalse(self.txn.exists())

    def test_switch_interrupted_while_the_new_daemon_starts_rolls_back(self):
        self.start(self.bin, self.home)
        self.flag("slow", NEW)
        proc = subprocess.Popen([sys.executable, str(ROOT / "box/rn-engine.py"), "switch", "--home", str(self.home),
                                 "--bin", str(self.bin), "--new", str(self.new), "--pinned", NEW,
                                 "--backup", str(self.base / "backup"), "--txn", str(self.txn)],
                                env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        deadline = time.time() + 20
        while time.time() < deadline:
            if self.txn.exists() and json.loads(self.txn.read_text())["stage"] == "replaced":
                break
            time.sleep(0.1)
        time.sleep(0.5)
        proc.send_signal(signal.SIGTERM)
        self.unflag("slow", NEW)
        out, _ = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 1, out)
        self.assertIn("engine: interrupted", out)
        self.assertEqual(self.version(self.bin), OLD)
        self.assertEqual(self.daemon_version(self.home), OLD)

    def test_rollback_of_an_unfinished_record_and_of_a_damaged_one(self):
        self.start(self.bin, self.home)
        backup = self.base / "backup"
        backup.mkdir()
        shutil.copy2(self.bin, backup / "reason-network")
        self.put_engine(self.bin, NEW)
        self.txn.write_text(json.dumps({"stage": "replacing", "bin": str(self.bin), "home": str(self.home),
                                        "backup": str(backup)}))
        self.assertEqual(self.engine("rollback", "--txn", str(self.txn)).returncode, 0)
        self.assertEqual(self.version(self.bin), OLD)
        self.txn.write_text("{")
        result = self.engine("rollback", "--txn", str(self.txn))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unreadable", result.stderr)
        self.txn.write_text(json.dumps({"stage": "backup"}))
        self.assertIn("damaged", self.engine("rollback", "--txn", str(self.txn)).stderr)
        self.assertEqual(self.engine("rollback", "--txn", str(self.base / "missing.json")).returncode, 0)

    def test_others_lists_sessions_but_not_the_daemon_tray_or_the_relay_child(self):
        tools = self.base / "tools"
        tools.mkdir()
        (tools / "ps").write_text(f"""#!/bin/sh
cat <<'EOF'
  101     1 {self.bin} daemon --service
  102   101 {self.bin} tray
  103   500 {self.bin} listen --as bot
  104     1 {self.bin} mcp
  105     1 /usr/bin/other {self.bin}
EOF
""")
        (tools / "ps").chmod(0o755)
        self.env["PATH"] = f"{tools}:{self.env['PATH']}"
        out = self.engine("others", "--bin", str(self.bin), "--skip-parent", "500").stdout
        self.assertEqual(out.splitlines(), [f"104 {self.bin} mcp"])

    def test_registration_needs_a_marker_written_after_the_daemon_started(self):
        eng = rn_engine()
        marker = self.base / "registered"
        name = "demo/bot"
        self.assertEqual(eng.registration(self.home, marker, name, None, 660)[0], "fail")
        pid = self.start(self.bin, self.home)
        dstart = eng.started(pid)
        self.assertEqual(eng.registration(self.home, marker, name, None, 660, now=dstart + 10)[0], "wait")
        state, reason = eng.registration(self.home, marker, name, None, 660, now=dstart + 700)
        self.assertEqual(state, "fail")
        self.assertIn("no registered marker", reason)
        marker.write_text(json.dumps({"name": name, "pid": 77}))
        os.utime(marker, (dstart - 5, dstart - 5))
        self.assertEqual(eng.registration(self.home, marker, name, None, 660, now=dstart + 10)[0], "wait")
        state, reason = eng.registration(self.home, marker, name, None, 660, now=dstart + 700)
        self.assertEqual(state, "fail")
        self.assertIn("since it started", reason)
        os.utime(marker, (dstart + 1, dstart + 1))
        self.assertEqual(eng.registration(self.home, marker, name, None, 660, now=dstart + 700)[0], "ok")
        self.assertEqual(eng.registration(self.home, marker, name, 78, 660, now=dstart + 700)[0], "fail")
        self.assertEqual(eng.registration(self.home, marker, name, 77, 660, now=dstart + 700)[0], "ok")
        marker.write_text(json.dumps({"name": "demo/bot-2", "pid": 77}))
        self.assertIn("registered as demo/bot-2", eng.registration(self.home, marker, name, None, 660)[1])


class PinnedChecksums(unittest.TestCase):
    TARGETS = ("aarch64-apple-darwin", "x86_64-apple-darwin", "aarch64-unknown-linux-musl", "x86_64-unknown-linux-musl")

    def table(self, path):
        text = (ROOT / path).read_text()
        version = re.search(r'^VERSION="(v[0-9.]+)"$', text, re.M).group(1)
        body = text[text.index("pinned_sha256() {"):]
        body = body[:body.index("\n}\n")]
        return version, dict(re.findall(r"^\s+(v[0-9.]+/[a-z0-9_-]+)\) echo ([0-9a-f]{64}) ;;$", body, re.M))

    def test_both_scripts_pin_the_same_version_and_checksums_for_all_targets(self):
        mac_version, mac = self.table("install.sh")
        cloud_version, cloud = self.table("box/rn-cloud-setup.sh")
        self.assertEqual(mac_version, cloud_version)
        self.assertEqual(mac, cloud)
        self.assertEqual(sorted(mac), sorted(f"{mac_version}/{t}" for t in self.TARGETS))

    def test_a_version_change_without_new_checksums_stops_before_downloading(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            repo = base / "repo"
            shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns("__pycache__", ".git"))
            text = (repo / "install.sh").read_text().replace(f'VERSION="v{NEW}"', 'VERSION="v0.12.18"')
            (repo / "install.sh").write_text(text)
            out = base / "out"
            out.mkdir()
            tools = base / "tools"
            tools.mkdir()
            (tools / "uname").write_text('#!/bin/sh\nif [ "$1" = -s ]; then echo Darwin; else echo arm64; fi\n')
            (tools / "curl").write_text('#!/bin/sh\necho called > "$CURL_LOG"; exit 1\n')
            for tool in ("launchctl", "plutil"):
                (tools / tool).write_text("#!/bin/sh\nexit 0\n")
            for tool in ("uname", "curl", "launchctl", "plutil"):
                (tools / tool).chmod(0o755)
            env = dict(os.environ, PATH=f"{tools}:/usr/bin:/bin", CURL_LOG=str(base / "curl"), HOME=str(base))
            result = subprocess.run(["/bin/bash", str(repo / "install.sh"), "--fetch-engine", str(out)], env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("no pinned sha256 for Reason Network v0.12.18 on aarch64-apple-darwin", result.stderr)
            self.assertFalse((base / "curl").exists())


class MacUpgrade(FakeEngineMixin, tip.Installation):
    """update.sh with a real (fake) engine daemon behind the fake launchd."""

    def upgradable(self, version=OLD):
        repo = self.installed()
        self.setup_ctl(self.base, self.env)
        self.rn = self.home / ".local/bin/reason-network"
        self.rn_home = self.home / ".reason/network"
        self.put_engine(self.rn, version)
        self.mock = self.base / "mock"
        self.mock.mkdir(exist_ok=True)
        data = tarball(NEW)
        (self.mock / "e.tgz").write_bytes(data)
        pin(repo / "install.sh", "aarch64-apple-darwin", hashlib.sha256(data).hexdigest())
        cut = shutil.which("cut")
        if cut and not (self.bin / "cut").exists():
            (self.bin / "cut").symlink_to(cut)
        self.tool("curl", 'echo "curl $*" >> "$CALL_LOG"; out=""; prev=""\n'
                          'for a in "$@"; do [ "$prev" != -o ] || out="$a"; prev="$a"; done\n'
                          '[ ! -f "$MOCK_BOX/fail-download" ] || exit 22\n'
                          'case "$*" in *releases/download*) cat "$MOCK_BOX/e.tgz" > "$out" ;; '
                          '*https://aps1-1*|*.compute.reasonmachines.com/*) printf 200 ;; *) exit 73 ;; esac')
        self.env["MOCK_BOX"] = str(self.mock)
        self.start(self.rn, self.rn_home)
        self.send(self.rn, self.rn_home, "m1", "kept")
        return repo

    def txn(self):
        return self.home / ".local/lib/agent-mesh/engine-upgrade.json"

    def backups(self):
        return sorted((self.home / ".local/lib/agent-mesh").glob("engine-backup-*"))

    def same_second(self):
        real = shutil.which("date")
        self.tool("date", f'if [ "$2" = "+%Y%m%dT%H%M%SZ" ]; then echo 20261111T000000Z; else exec "{real}" "$@"; fi')

    def leftovers(self):
        return sorted((self.home / ".local/lib/agent-mesh").glob(".engine-new.*"))

    def test_engine_only_upgrade_switches_the_relay_first_then_the_engine(self):
        repo = self.upgradable()
        old_pid = self.daemon(self.rn_home)
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertRegex(plan.stdout, rf"engine\s+changed\s+upgrade Reason Network {OLD} to {NEW}")
        self.assertEqual(self.service_changes(), [])
        self.assertEqual((self.version(self.rn), self.daemon(self.rn_home)), (OLD, old_pid))
        result = self.run_tty(repo, "--no-pull", "--yes", answers=[])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("stopped; the engine was not changed", result.stderr)
        self.assertEqual((self.version(self.rn), self.daemon(self.rn_home)), (OLD, old_pid))
        self.assertTrue(self.loaded())
        (self.base / "calls").unlink()
        result = self.run_tty(repo, "--no-pull", answers=["y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.version(self.rn), NEW)
        self.assertEqual(self.daemon_version(self.rn_home), NEW)
        self.assertNotEqual(self.daemon(self.rn_home), old_pid)
        self.assertEqual(self.messages(self.rn_home), {"m1": "kept"})
        self.assertFalse(self.txn().exists())
        self.assertEqual(self.leftovers(), [])
        [backup] = self.backups()
        self.assertEqual(self.version(backup / "reason-network"), OLD)
        self.assertTrue((backup / "state/network.db").exists())
        steps = [line.split()[1] for line in self.service_changes()]
        steps = [step for i, step in enumerate(steps) if i == 0 or steps[i - 1] != step]
        self.assertEqual(steps, ["bootout", "bootstrap", "bootout", "bootstrap"])
        self.assertIn(f"upgraded {OLD} -> {NEW}", result.stdout)
        self.assertTrue(self.loaded())
        marker = json.loads((self.home / "Library/Application Support/agent-relay/registered").read_text())
        self.assertEqual(marker["name"], "demo/bot")
        again = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("already up to date", again.stdout)

    def test_relay_failure_before_the_switch_leaves_the_engine_alone(self):
        repo = self.upgradable()
        old_pid = self.daemon(self.rn_home)
        (self.state / "fail-bootstrap").write_text("")
        result = self.run_tty(repo, "--no-pull", answers=["y"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("the relay did not switch, so the engine was not changed", result.stderr)
        self.assertEqual((self.version(self.rn), self.daemon(self.rn_home)), (OLD, old_pid))
        self.assertEqual(self.backups(), [])
        self.assertTrue(self.loaded())

    @unittest.skipUnless(shutil.which("ps"), "needs ps")
    def test_other_sessions_are_listed_and_do_not_block(self):
        repo = self.upgradable()
        real = shutil.which("ps")
        self.tool("ps", f'if [ "$1" = -axo ]; then "{real}" "$@"; echo "  999     1 {self.rn} mcp"; '
                        f'else exec "{real}" "$@"; fi')
        result = self.run_tty(repo, "--no-pull", answers=["y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("These processes also use Reason Network", result.stdout)
        self.assertIn(f"999 {self.rn} mcp", result.stdout)
        self.assertEqual(self.version(self.rn), NEW)

    def test_download_or_checksum_failure_changes_nothing(self):
        repo = self.upgradable()
        old_pid = self.daemon(self.rn_home)
        for case in ("download", "checksum"):
            with self.subTest(case):
                if case == "download":
                    (self.mock / "fail-download").write_text("")
                else:
                    (self.mock / "fail-download").unlink()
                    (self.mock / "e.tgz").write_bytes(tarball("0.12.99"))
                result = self.run_tty(repo, "--no-pull", answers=["y"])
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("could not be downloaded or verified; the engine was not changed", result.stderr)
                if case == "checksum":
                    self.assertIn("checksum mismatch", result.stderr)
                self.assertEqual((self.version(self.rn), self.daemon(self.rn_home)), (OLD, old_pid))
                self.assertEqual((self.backups(), self.leftovers()), ([], []))
                self.assertTrue(self.loaded())

    def test_a_new_daemon_that_does_not_start_is_rolled_back(self):
        repo = self.upgradable()
        self.same_second()
        self.flag("nostart", NEW)
        result = self.run_tty(repo, "--no-pull", answers=["y"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"the engine upgrade failed; Reason Network {OLD} is back", result.stderr)
        self.assertIn(f"failed and rolled back to {OLD}", result.stdout)
        self.assertEqual(self.version(self.rn), OLD)
        self.assertEqual(self.daemon_version(self.rn_home), OLD)
        self.assertFalse(self.txn().exists())
        self.assertEqual(len(self.backups()), 1)
        self.assertTrue(self.loaded())
        self.assertEqual(self.messages(self.rn_home), {"m1": "kept"})
        self.unflag("nostart", NEW)
        result = self.run_tty(repo, "--no-pull", answers=["y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.version(self.rn), NEW)
        self.assertEqual([b.name for b in self.backups()],
                         ["engine-backup-20261111T000000Z", "engine-backup-20261111T000000Z-2"])

    def test_a_relay_that_never_registers_on_the_new_engine_rolls_back(self):
        repo = self.upgradable()
        (self.state / "noreg-when").write_text(NEW)
        result = self.run_tty(repo, "--no-pull", answers=["y"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("reported no registration", result.stdout)
        self.assertIn(f"the relay did not register on the new engine; Reason Network {OLD} is back", result.stderr)
        self.assertEqual(self.version(self.rn), OLD)
        self.assertEqual(self.daemon_version(self.rn_home), OLD)
        self.assertFalse(self.txn().exists())
        self.assertTrue(self.loaded())
        marker = json.loads((self.home / "Library/Application Support/agent-relay/registered").read_text())
        self.assertEqual(marker["pid"], int((self.state / "pid").read_text()))

    def test_unconfirmed_rollback_keeps_the_record_and_the_next_run_retries(self):
        repo = self.upgradable()
        self.flag("nostart", NEW)
        self.flag("nostart", OLD)
        result = self.run_tty(repo, "--no-pull", answers=["y"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("Restore it by hand", result.stdout)
        self.assertIn("was not fully undone", result.stderr)
        self.assertTrue(self.txn().exists())
        check = self.run_script("check.sh", root=ROOT, timeout=90)
        self.assertIn("FAIL  an engine upgrade did not finish", check.stdout)
        self.unflag("nostart", NEW)
        self.unflag("nostart", OLD)
        result = self.run_tty(repo, "--no-pull", answers=["n"])
        self.assertIn("an engine upgrade did not finish ('replaced'); rolling it back first", result.stdout)
        self.assertIn("stopped; the engine was not changed", result.stderr)
        self.assertEqual(self.version(self.rn), OLD)
        self.assertFalse(self.txn().exists())
        self.assertTrue(self.loaded())

    def test_interrupted_during_the_switch_rolls_back_and_restores_the_relay(self):
        repo = self.upgradable()
        self.flag("slow", NEW)
        code, out, err = self.interrupt_at(repo, "replaced")
        self.assertEqual(code, 143, out + err)
        self.assertIn("rolling it back", out)
        self.assertEqual(self.version(self.rn), OLD)
        self.assertEqual(self.daemon_version(self.rn_home), OLD)
        self.assertFalse(self.txn().exists())
        self.assertEqual(self.leftovers(), [])
        self.assertTrue(self.loaded())
        self.assertFalse((self.home / ".local/lib/agent-mesh/update.lock").exists())

    def interrupt_at(self, repo, stage):
        master, slave = pty.openpty()
        try:
            os.write(master, b"y\n")
            proc = subprocess.Popen(["/bin/bash", str(repo / "update.sh"), "--no-pull"], env=self.env, stdin=slave,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=os.setpgrp)
            deadline = time.time() + 60
            while time.time() < deadline:
                try:
                    if json.loads(self.txn().read_text())["stage"] == stage:
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(0.1)
            else:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate(timeout=30)
                self.fail(f"the switch never reached {stage}")
            time.sleep(0.5)
            os.killpg(proc.pid, signal.SIGTERM)
            self.unflag("slow", NEW)
            out, err = proc.communicate(timeout=90)
            return proc.returncode, out.decode(), err.decode()
        finally:
            os.close(slave)
            os.close(master)

    def test_a_stale_daemon_is_restarted_on_the_installed_binary(self):
        repo = self.upgradable(NEW)
        old_pid = self.daemon(self.rn_home)
        time.sleep(1.1)
        self.put_engine(self.rn, NEW)
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertRegex(plan.stdout, r"engine\s+changed\s+restart the daemon")
        result = self.run_tty(repo, "--no-pull", answers=["y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"daemon restarted on {NEW}", result.stdout)
        self.assertNotEqual(self.daemon(self.rn_home), old_pid)
        self.assertEqual(self.backups(), [])
        self.assertTrue(self.loaded())

    def test_an_interrupted_daemon_restart_starts_the_relay_again(self):
        repo = self.upgradable(NEW)
        time.sleep(1.1)
        self.put_engine(self.rn, NEW)
        self.flag("slowafterstop", NEW)
        master, slave = pty.openpty()
        try:
            os.write(master, b"y\n")
            proc = subprocess.Popen(["/bin/bash", str(repo / "update.sh"), "--no-pull"], env=self.env, stdin=slave,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=os.setpgrp)
            deadline, seen = time.time() + 60, False
            while time.time() < deadline and proc.poll() is None:
                if (self.ctl / "stopped").exists():
                    seen = True
                    break
                time.sleep(0.01)
            time.sleep(0.5)
            unloaded = not self.loaded()
            os.killpg(proc.pid, signal.SIGTERM)
            self.unflag("slowafterstop", NEW)
            out, err = proc.communicate(timeout=90)
        finally:
            os.close(slave)
            os.close(master)
        self.assertTrue(seen and unloaded, out.decode() + err.decode())
        self.assertEqual(proc.returncode, 143, out.decode() + err.decode())
        self.assertTrue(self.loaded(), out.decode())
        self.assertFalse((self.home / ".local/lib/agent-mesh/update.lock").exists())

    def test_a_changed_cloud_engine_helper_asks_for_the_cloud_engine_step_again(self):
        repo = self.installed()
        with open(repo / "box/rn-engine.py", "a") as stream:
            stream.write("\n")
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertRegex(plan.stdout, r"cloud engine\s+changed")

    def test_cloud_engine_step_can_be_postponed_and_then_done(self):
        repo = self.installed()
        data = self.record()
        del data["cloud_engine"]
        self.write_record(data)
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertRegex(plan.stdout, r"cloud engine\s+unknown\s+Grok Bot runs box/rn-cloud-setup.sh --upgrade-engine")
        result = self.run_tty(repo, "--no-pull", answers=["n"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("not done; run ./update.sh again to do it", result.stdout)
        self.assertEqual(self.service_changes(), [])
        reply = self.cloud_reply(repo)
        wrong = [line.replace(f"reason-network {NEW} running", f"reason-network {OLD} running") for line in reply]
        result = self.run_tty(repo, "--no-pull", answers=["y", "y", *wrong])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"mismatch: the cloud engine runs {OLD}, not {NEW}", result.stdout)
        self.assertNotIn("cloud_engine", self.record())
        result = self.run_tty(repo, "--no-pull", answers=["y", *reply, "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.record()["cloud_engine"]["state"], "confirmed")
        self.assertEqual(self.record()["cloud_engine"]["version"], NEW)
        self.assertNotIn("txn", self.record())
        self.assertTrue(self.loaded())


class CloudUpgrade(FakeEngineMixin, tip.Installation):
    def cloud(self, version=OLD, joined=True):
        self.cloud_box()
        self.setup_ctl(self.base, self.env)
        if joined:
            self.joined_before()
            (self.box / "person").write_text("demo\n")
        self.rn = self.box / "bin/reason-network"
        self.put_engine(self.rn, version)
        data = tarball(NEW)
        (self.mock / "e.tgz").write_bytes(data)
        pin(self.box / "rn-cloud-setup.sh", "x86_64-unknown-linux-musl", hashlib.sha256(data).hexdigest())
        self.rn_home = self.box / "state"

    def upgrade(self):
        return subprocess.run(["/bin/bash", str(self.box / "rn-cloud-setup.sh"), "--upgrade-engine"], env=self.env,
                              input="", capture_output=True, text=True, timeout=120)

    def engine_line(self, out):
        lines = re.findall(r"^engine: reason-network (\S+) running \(daemon pid \d+, started .*$", out, re.M)
        return lines[-1] if lines else None

    def test_cloud_upgrade_refuses_before_the_cloud_joined(self):
        self.cloud(joined=False)
        result = self.upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("has not joined a team yet", result.stderr)
        self.assertEqual(self.version(self.rn), OLD)

    def test_cloud_upgrade_upgrades_with_a_backup_and_a_rerun_does_nothing(self):
        self.cloud()
        self.start(self.rn, self.rn_home)
        self.send(self.rn, self.rn_home, "m1", "kept")
        result = self.upgrade()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"upgrading Reason Network {OLD} to {NEW}", result.stdout)
        self.assertEqual(self.engine_line(result.stdout), NEW)
        self.assertEqual(self.version(self.rn), NEW)
        self.assertEqual(self.daemon_version(self.rn_home), NEW)
        self.assertEqual(self.messages(self.rn_home), {"m1": "kept"})
        [backup] = sorted(self.box.glob("engine-backup-*"))
        self.assertEqual(self.version(backup / "reason-network"), OLD)
        again = self.upgrade()
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("engine: nothing to upgrade", again.stdout)
        self.assertEqual(self.engine_line(again.stdout), NEW)

    def test_cloud_upgrade_with_the_pinned_version_starts_or_restarts_the_daemon(self):
        self.cloud(NEW)
        result = self.upgrade()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.engine_line(result.stdout), NEW)
        self.assertIn("nothing to upgrade", result.stdout)
        old_pid = self.daemon(self.rn_home)
        time.sleep(1.1)
        self.put_engine(self.rn, NEW)
        result = self.upgrade()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("started before the binary was replaced; restarting it", result.stdout)
        self.assertNotEqual(self.daemon(self.rn_home), old_pid)
        self.assertEqual(self.engine_line(result.stdout), NEW)

    def test_cloud_upgrade_rolls_back_when_the_new_daemon_fails(self):
        self.cloud()
        real = shutil.which("date")
        self.tool("date", f'if [ "$2" = "+%Y%m%dT%H%M%SZ" ]; then echo 20261111T000000Z; else exec "{real}" "$@"; fi')
        self.start(self.rn, self.rn_home)
        self.flag("nostart", NEW)
        result = self.upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("the previous engine is back", result.stderr)
        self.assertEqual(self.version(self.rn), OLD)
        self.assertEqual(self.daemon_version(self.rn_home), OLD)
        self.assertEqual(self.engine_line(result.stdout), OLD)
        self.unflag("nostart", NEW)
        result = self.upgrade()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.version(self.rn), NEW)
        self.assertEqual([b.name for b in sorted(self.box.glob("engine-backup-*"))],
                         ["engine-backup-20261111T000000Z", "engine-backup-20261111T000000Z-2"])

    def test_cloud_upgrade_never_downgrades(self):
        for version in ("0.13.0", "garbage"):
            with self.subTest(version):
                self.setUp()
                self.cloud(version)
                result = self.upgrade()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("never downgrades or replaces it", result.stderr)
                self.assertEqual(self.version(self.rn), version)

    def test_cloud_setup_with_an_older_engine_points_to_the_upgrade(self):
        self.cloud()
        result = self.setup(tip.invite(), "demo")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--upgrade-engine", result.stderr)
        self.assertEqual(self.version(self.rn), OLD)


def load_tests(loader, standard_tests, pattern):
    suite = unittest.TestSuite()
    for cls in (EngineCommands, PinnedChecksums, MacUpgrade, CloudUpgrade):
        for name in sorted(n for n in vars(cls) if n.startswith("test")):
            suite.addTest(cls(name))
    return suite


if __name__ == "__main__":
    unittest.main()
