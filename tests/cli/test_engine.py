"""Engine upgrade checks with a fake Reason Network that runs a real daemon process; never contact real services."""
import fcntl
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

from tests.cli import install_support as tip
from tests.cli.install_support import InstallHarness
from tests.cli.test_install_add import LEGACY_LABEL, AddSupport, m
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


def effective(text):
    values, table = {"auto_update": True, "lan": True}, ""
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("["):
            table = line.strip("[]").strip()
            continue
        key, _, value = (part.strip() for part in line.partition("="))
        quoted = len(value) > 1 and value[0] == value[-1] == '"'
        if table == "inbound.people" and value not in ('"accept"', '"hold"', '"refuse"'):
            return "defaults"
        if table == "folders" and not quoted:
            return "defaults"
        if not table and key in values:
            if value not in ("true", "false"):
                return "defaults"
            values[key] = value == "true"
    return "auto_update=%s lan=%s" % (str(values["auto_update"]).lower(), str(values["lan"]).lower())


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
    try:
        loaded = (home / "config.toml").read_text()
    except OSError:
        loaded = ""
    (ctl / f"config-{os.getpid()}").write_text(loaded)
    (ctl / f"effective-{os.getpid()}").write_text(effective(loaded))
    with open(ctl / "daemons", "a") as log:
        log.write(f"{os.getpid()} {VERSION} {'fd9' if fd_open(9) else '-'}\n")
    while True:
        time.sleep(1)


def fd_open(fd):
    try:
        os.fstat(fd)
    except OSError:
        return False
    return True


def ensure():
    if held():
        return True
    subprocess.Popen([sys.executable, __file__, "daemon", "--service"], start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     pass_fds=(9,) if fd_open(9) else ())
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
    if flag("slowstop"):
        (ctl / "stopping").write_text("")
        time.sleep(30)
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


UNTRUSTED = '#!/bin/sh\necho "$0 $*" >> "$FAKE_RN_CTL/untrusted-ran"\nexit 1\n'

CLOUD_ENV_ENGINE = """#!/bin/sh
[ "$1" != --version ] || { echo "reason-network 0.12.17"; exit 0; }
echo "$1 NO_UPDATE=${REASON_NETWORK_NO_UPDATE:-} MACHINE=${REASON_NETWORK_MACHINE:-}" >> "@LOG@"
case "$1" in
  join) echo "joined team demo as demo/cloud" ;;
  doctor) echo "peers: 1 connected" ;;
esac
exit 0
"""


def fake_engine(version):
    return FAKE.replace("@PYTHON@", sys.executable).replace("@VERSION@", version)


def fake_effective(text):
    scope = {}
    exec(FAKE[FAKE.index("def effective("):FAKE.index("def daemon(")], scope)
    return scope["effective"](text)


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
        self.addCleanup(self.assert_no_daemons_under, Path(base))
        self.addCleanup(self.kill_daemons, self.ctl)

    def kill_daemons(self, ctl):
        path = ctl / "daemons"
        if not path.exists():
            return
        for line in path.read_text().split("\n"):
            if line.strip():
                try:
                    os.kill(int(line.split()[0]), signal.SIGKILL)
                except (OSError, ValueError):
                    pass

    def assert_no_daemons_under(self, base):
        if not shutil.which("ps"):
            return
        prefix, deadline = f"{base}/", time.time() + 3
        while True:
            out = subprocess.run(["ps", "-ww", "-axo", "pid=,command="], capture_output=True, text=True,
                                 check=True, timeout=10).stdout
            left = [line.strip() for line in out.splitlines() if prefix in line and "daemon --service" in line]
            if not left or time.time() > deadline:
                break
            time.sleep(0.1)
        self.assertEqual(left, [], "fake engine daemons outlived the test")

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

    def daemons_with_fd9(self):
        path = self.ctl / "daemons"
        lines = path.read_text().splitlines() if path.exists() else []
        return [line for line in lines if line.split()[2:] == ["fd9"]]

    def daemon_version(self, home):
        pid = self.daemon(home)
        if pid is None:
            return None
        for line in reversed((self.ctl / "daemons").read_text().splitlines()):
            if line.split()[0] == str(pid):
                return line.split()[1]
        return "?"

    def daemon_config(self, home):
        return (self.ctl / f"config-{self.daemon(home)}").read_text()

    def daemon_settings(self, home):
        return (self.ctl / f"effective-{self.daemon(home)}").read_text()

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
        out = self.engine("others", "--bin", str(self.bin), "--skip-parent", "500", "--skip-parent", "1").stdout
        self.assertEqual(out.splitlines(), [])

    def prepare(self, kind="upgrade", *keep):
        return self.engine("prepare", "--txn", str(self.txn), "--home", str(self.home), "--bin", str(self.bin),
                           "--kind", kind, *[a for kv in keep for a in ("--keep", kv)])

    def test_switch_keeps_the_participants_of_a_prepared_transaction_and_adds_its_phases(self):
        self.start(self.bin, self.home)
        members = json.dumps([{"name": "alpha", "label": "a", "plist": "/p a", "was_loaded": True}])
        self.assertEqual(self.prepare("upgrade", f"participants={members}").returncode, 0)
        data = json.loads(self.txn.read_text())
        self.assertEqual((data["stage"], data["phase"], data["kind"], data["extra"]["participants"]),
                         ("prepared", "prepared", "upgrade", members))
        self.assertEqual(self.engine("rollback", "--txn", str(self.txn)).returncode, 0)
        self.assertEqual(self.engine("phase", "--txn", str(self.txn), "stopped").returncode, 0)
        result = self.switch("--keep", "note=1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(self.txn.read_text())
        self.assertEqual((data["stage"], data["phase"], data["kind"]), ("verified", "switched", "upgrade"))
        self.assertEqual(data["extra"], {"participants": members, "note": "1"})
        self.assertEqual(self.engine("txn-get", "--txn", str(self.txn), "participants").stdout.strip(), members)
        self.assertEqual(self.engine("phase", "--txn", str(self.txn), "committed").returncode, 0)
        self.assertEqual(self.engine("rollback", "--txn", str(self.txn)).returncode, 0)
        self.assertEqual(self.version(self.bin), NEW)
        self.txn.unlink()
        self.assertEqual(self.engine("phase", "--txn", str(self.txn), "committed").returncode, 1)
        self.assertNotEqual(self.prepare("upgrade", "novalue").returncode, 0)
        self.assertFalse(self.txn.exists())

    def test_a_switch_without_a_prepared_transaction_starts_a_new_record(self):
        self.start(self.bin, self.home)
        self.txn.write_text(json.dumps({"stage": "verified", "phase": "committed", "bin": str(self.bin),
                                        "home": str(self.home), "backup": "", "extra": {"participants": "[]"}}))
        result = self.switch("--keep", "relay_was_loaded=1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(self.txn.read_text())
        self.assertNotIn("phase", data)
        self.assertEqual(data["extra"], {"relay_was_loaded": "1"})

    def test_a_daemon_restart_in_a_prepared_transaction_records_its_stage_and_never_rolls_back(self):
        old_pid = self.start(self.bin, self.home)
        self.assertEqual(self.prepare("restart", "participants=[]").returncode, 0)
        self.assertEqual(self.engine("phase", "--txn", str(self.txn), "stopped").returncode, 0)
        result = self.engine("restart", "--home", str(self.home), "--bin", str(self.bin), "--txn", str(self.txn))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual(self.daemon(self.home), old_pid)
        data = json.loads(self.txn.read_text())
        self.assertEqual((data["stage"], data["phase"], data["kind"], data["extra"]), ("restarted", "switched",
                                                                                       "restart", {"participants": "[]"}))
        self.assertEqual(self.engine("rollback", "--txn", str(self.txn)).returncode, 0)
        self.assertEqual(self.version(self.bin), OLD)

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


class CloudEngineCommands(FakeEngineMixin, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.env = dict(os.environ)
        self.env.pop("REASON_NETWORK_HOME", None)
        self.env.pop("TINCAN_HOME", None)
        self.setup_ctl(self.base, self.env)
        self.box = self.base / "box"
        self.home = self.box / "state"
        self.home.mkdir(parents=True)
        self.rn = self.box / "bin/engine/reason-network"
        self.launcher = self.box / "bin/reason-network"
        self.new = self.base / "new/reason-network"
        self.put_engine(self.new, NEW)
        self.txn = self.base / "engine-upgrade.json"
        self.builds = self.base / "builds.json"
        target = rn_engine().linux_target()
        self.builds.write_text(json.dumps({v: {target: hashlib.sha256(fake_engine(v).encode()).hexdigest()}
                                           for v in (OLD, NEW)}))
        self.env["RN_PINNED_BUILDS_FILE"] = str(self.builds)

    def engine(self, *args, timeout=60):
        return subprocess.run([sys.executable, "-I", str(ROOT / "box/rn-engine.py"), *args], env=self.env,
                              capture_output=True, text=True, timeout=timeout)

    def untrusted(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(UNTRUSTED)
        path.chmod(0o755)

    def untrusted_ran(self):
        return (self.ctl / "untrusted-ran").exists()

    def switch(self, *extra):
        return self.engine("switch", "--home", str(self.home), "--bin", str(self.rn), "--new", str(self.new),
                           "--pinned", NEW, "--backup", str(self.base / "backup"), "--txn", str(self.txn), *extra)

    def test_build_reports_only_pinned_regular_files(self):
        self.put_engine(self.rn, NEW)
        digest = hashlib.sha256(fake_engine(NEW).encode()).hexdigest()
        result = self.engine("build", "--bin", str(self.rn))
        self.assertEqual((result.returncode, result.stdout.strip()), (0, f"{NEW} {digest}"))
        link = self.base / "link"
        link.symlink_to(self.rn)
        self.untrusted(self.base / "other")
        for path in (link, self.base / "other", self.base / "missing", self.box):
            with self.subTest(path=path.name):
                result = self.engine("build", "--bin", str(path))
                self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertFalse(self.untrusted_ran())
        self.env.pop("RN_PINNED_BUILDS_FILE")
        self.assertEqual(self.engine("build", "--bin", str(self.rn)).returncode, 1)

    def test_the_real_table_pins_the_setup_version_for_both_linux_targets(self):
        version = re.search(r'^VERSION="v([0-9.]+)"$', (ROOT / "box/rn-cloud-setup.sh").read_text(), re.M).group(1)
        table = rn_engine().PINNED_BUILDS
        self.assertEqual(sorted(table[version]), ["aarch64-unknown-linux-musl", "x86_64-unknown-linux-musl"])
        for digest in table[version].values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_launcher_runs_only_allowed_commands_with_the_engine_env(self):
        self.rn.parent.mkdir(parents=True)
        self.rn.write_text('#!/bin/sh\necho "args=$* NO_UPDATE=$REASON_NETWORK_NO_UPDATE '
                           'NO_PROMPT=$REASON_NETWORK_NO_PROMPT MACHINE=$REASON_NETWORK_MACHINE"\n')
        self.rn.chmod(0o755)
        result = self.engine("launcher", "--path", str(self.launcher))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("launcher ok", result.stdout)
        self.assertEqual(oct(self.launcher.stat().st_mode & 0o777), "0o755")

        def run(*args, **env):
            e = dict(self.env, REASON_NETWORK_NO_UPDATE="0", **env)
            return subprocess.run([str(self.launcher), *args], env=e, capture_output=True, text=True, timeout=30)

        result = run("who")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no state directory is set", result.stderr)
        for args in (("upgrade",), ("setup",), ("join", "x"), ("init",), ("",), ("mcp",), ("folder", "x"),
                     ("bridge",)):
            with self.subTest(args=args):
                result = run(*args, REASON_NETWORK_HOME=str(self.home))
                self.assertEqual((result.returncode, result.stdout), (2, ""), result.stderr)
        result = run("who", REASON_NETWORK_HOME=str(self.home))
        self.assertEqual(result.stdout.strip(), "args=who NO_UPDATE=1 NO_PROMPT=1 MACHINE=cloud")
        result = run("relay", "default", REASON_NETWORK_HOME=str(self.home))
        self.assertEqual(result.stdout.strip(), "args=relay default NO_UPDATE=1 NO_PROMPT=1 MACHINE=cloud")
        result = run("join", "link", "--as", "demo", "--no-setup", TINCAN_HOME=str(self.home),
                     REASON_NETWORK_MACHINE="other")
        self.assertEqual(result.stdout.strip(), "args=join link --as demo --no-setup NO_UPDATE=1 NO_PROMPT=1 MACHINE=other")
        before = self.launcher.stat().st_ino
        result = self.engine("launcher", "--path", str(self.launcher))
        self.assertNotIn("wrote the launcher", result.stdout)
        self.assertEqual(self.launcher.stat().st_ino, before)

    def test_cloud_bin_checks_the_payload_and_puts_the_launcher_back(self):
        self.put_engine(self.launcher, OLD)
        result = self.engine("cloud-bin", "--base", str(self.box))
        self.assertEqual((result.returncode, result.stdout.strip()), (0, str(self.launcher)), result.stderr)
        self.assertIn("has not been moved to box/bin/engine yet", result.stderr)
        self.put_engine(self.rn, NEW)
        result = self.engine("cloud-bin", "--base", str(self.box))
        self.assertEqual((result.returncode, result.stdout.strip()), (0, str(self.rn)), result.stderr)
        self.assertIn("the launcher is back", result.stderr)
        self.assertEqual(self.launcher.read_text(), rn_engine().LAUNCHER)
        result = self.engine("cloud-bin", "--base", str(self.box))
        self.assertEqual((result.returncode, result.stdout.strip(), result.stderr), (0, str(self.rn), ""))
        self.launcher.unlink()
        self.launcher.symlink_to(self.rn)
        result = self.engine("cloud-bin", "--base", str(self.box))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.launcher.is_symlink())
        self.untrusted(self.rn)
        result = self.engine("cloud-bin", "--base", str(self.box))
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertIn("is not a Reason Network build pinned by agent-mesh", result.stderr)
        self.rn.unlink()
        self.rn.symlink_to(self.new)
        self.assertEqual(self.engine("cloud-bin", "--base", str(self.box)).returncode, 1)
        self.assertFalse(self.untrusted_ran())

    def test_config_off_sets_top_level_keys_and_keeps_the_rest(self):
        path = self.home / "config.toml"
        result = self.engine("config-off", "--path", str(path), "auto_update", "lan", "tray")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(path.read_text(), "auto_update = false\nlan = false\ntray = false\n")
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        path.write_text("# kept\nauto_update = true\nlan=true # old\nauto_update = 1\nname = \"x\"\n"
                        "[tray]\ntray = true\nlan = true\n")
        path.chmod(0o640)
        result = self.engine("config-off", "--path", str(path), "auto_update", "lan", "tray")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(path.read_text(), "tray = false\n# kept\nauto_update = false\nlan = false\nname = \"x\"\n"
                                           "[tray]\ntray = true\nlan = true\n")
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o640")
        self.assertEqual(sorted(p.name for p in self.home.iterdir()), ["config.toml"])
        self.assertNotEqual(self.engine("config-off", "--path", str(path), "relay").returncode, 0)

    def test_strays_lists_other_daemons_under_the_bin_directory(self):
        proc = self.base / "proc"
        bindir = Path(os.path.realpath(self.box)) / "bin"
        bindir.mkdir(parents=True)

        def fake(pid, exe, args, env=b""):
            d = proc / str(pid)
            d.mkdir(parents=True)
            (d / "cmdline").write_bytes(b"\0".join(args) + b"\0")
            (d / "exe").symlink_to(exe)
            (d / "environ").write_bytes(env)

        fake(10, f"{bindir}/reason-network (deleted)", [b"x", b"daemon", b"--service"],
             b"HOME=/root\0REASON_NETWORK_HOME=/workspace/other\0")
        fake(11, f"{bindir}/engine/reason-network", [b"x", b"daemon"], b"HOME=/root\0")
        fake(12, "/usr/bin/reason-network", [b"x", b"daemon"])
        fake(13, f"{bindir}/reason-network", [b"x", b"who"])
        fake(14, f"{self.base}/binx/reason-network", [b"x", b"daemon"])
        (proc / "self").mkdir()
        result = self.engine("strays", "--bin-dir", str(bindir), "--home", str(self.home), "--proc", str(proc))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(),
                         [f"pid 10 {bindir}/reason-network state /workspace/other",
                          f"pid 11 {bindir}/engine/reason-network state /root/.reason/network"])
        with open(self.home / "daemon.lock", "w") as lock:
            lock.write("11\n")
            lock.flush()
            fcntl.flock(lock, fcntl.LOCK_EX)
            result = self.engine("strays", "--bin-dir", str(bindir), "--home", str(self.home), "--proc", str(proc))
        self.assertEqual(result.stdout.splitlines(), [f"pid 10 {bindir}/reason-network state /workspace/other"])
        result = self.engine("strays", "--bin-dir", str(bindir), "--home", str(self.home),
                             "--proc", str(self.base / "none"))
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_cloud_bin_returns_the_checked_payload_when_the_launcher_cannot_be_written(self):
        self.put_engine(self.rn, NEW)
        self.launcher.write_text("#!/bin/sh\necho stray\n")
        self.launcher.parent.chmod(0o555)
        try:
            result = self.engine("cloud-bin", "--base", str(self.box))
        finally:
            self.launcher.parent.chmod(0o755)
        self.assertEqual((result.returncode, result.stdout.strip()), (0, str(self.rn)), result.stderr)
        self.assertIn("could not put the launcher back", result.stderr)
        self.assertEqual(self.launcher.read_text(), "#!/bin/sh\necho stray\n")

    def test_switch_with_old_bin_moves_the_engine_and_writes_the_launcher(self):
        self.put_engine(self.launcher, OLD)
        self.start(self.launcher, self.home)
        self.send(self.launcher, self.home, "m1", "kept")
        result = self.switch("--old-bin", str(self.launcher))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.version(self.rn), NEW)
        self.assertEqual(self.launcher.read_text(), rn_engine().LAUNCHER)
        self.assertEqual(self.daemon_version(self.home), NEW)
        self.assertEqual(self.version(self.base / "backup/reason-network"), OLD)
        self.assertEqual(self.messages(self.home), {"m1": "kept"})
        self.assertEqual(self.engine("rollback", "--txn", str(self.txn)).returncode, 0)
        self.assertEqual(self.version(self.launcher), OLD)
        self.assertFalse(os.path.lexists(self.rn))
        self.assertEqual(self.daemon_version(self.home), OLD)

    def test_switch_with_old_bin_rolls_back_to_the_old_path(self):
        self.put_engine(self.launcher, OLD)
        self.start(self.launcher, self.home)
        self.flag("nostart", NEW)
        result = self.switch("--old-bin", str(self.launcher))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.version(self.launcher), OLD)
        self.assertFalse(os.path.lexists(self.rn))
        self.assertEqual(self.daemon_version(self.home), OLD)
        self.assertEqual(json.loads(self.txn.read_text())["stage"], "rolled_back")

    def test_switch_with_an_untrusted_old_binary_never_runs_it(self):
        self.put_engine(self.rn, "9.9.9")
        old_pid = self.start(self.rn, self.home)
        self.untrusted(self.rn)
        result = self.switch("--untrusted-old")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual(self.daemon(self.home), old_pid)
        self.assertEqual(self.daemon_version(self.home), NEW)
        self.assertEqual(json.loads(self.txn.read_text())["old_version"], "")
        self.assertEqual(self.engine("rollback", "--txn", str(self.txn)).returncode, 0)
        self.assertEqual(self.version(self.rn), NEW)
        self.assertFalse(self.untrusted_ran())

    def test_an_untrusted_daemon_that_ignores_stop_is_killed_without_running_its_binary(self):
        self.flag("noexit", "9.9.9")
        self.put_engine(self.rn, "9.9.9")
        old_pid = self.start(self.rn, self.home)
        self.untrusted(self.rn)
        result = self.switch("--untrusted-old")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("sending SIGKILL", result.stdout)
        self.assertNotEqual(self.daemon(self.home), old_pid)
        self.assertEqual(self.daemon_version(self.home), NEW)
        self.assertFalse(self.untrusted_ran())

    def test_engine_commands_run_with_no_update(self):
        probe = self.base / "probe"
        probe.write_text('#!/bin/sh\necho "$REASON_NETWORK_HOME $REASON_NETWORK_NO_UPDATE"\n')
        probe.chmod(0o755)
        done = rn_engine().run(probe, self.home, "who")
        self.assertEqual(done.stdout.strip(), f"{self.home} 1")

    def test_a_failed_untrusted_switch_keeps_the_new_binary_and_prints_steps(self):
        self.untrusted(self.launcher)
        self.flag("nostart", NEW)
        result = self.switch("--untrusted-old", "--old-bin", str(self.launcher))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("so it is not put back", result.stdout)
        self.assertIn("REASON_NETWORK_NO_UPDATE=1", result.stdout)
        self.assertEqual(self.version(self.rn), NEW)
        self.assertEqual(self.launcher.read_text(), rn_engine().LAUNCHER)
        self.assertEqual((self.base / "backup/reason-network").read_text(), UNTRUSTED)
        self.assertEqual(json.loads(self.txn.read_text())["stage"], "failed_untrusted")
        self.assertEqual(self.engine("rollback", "--txn", str(self.txn)).returncode, 0)
        data = json.loads(self.txn.read_text())
        data["stage"] = "replaced"
        self.txn.write_text(json.dumps(data))
        result = self.engine("rollback", "--txn", str(self.txn))
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("so it is not put back", result.stdout)
        self.assertEqual(self.version(self.rn), NEW)
        self.assertFalse(self.untrusted_ran())

    def test_an_untrusted_switch_that_cannot_install_the_payload_keeps_the_old_entry_point(self):
        self.untrusted(self.launcher)
        self.rn.parent.mkdir(parents=True)
        self.rn.parent.chmod(0o555)
        try:
            result = self.switch("--untrusted-old", "--old-bin", str(self.launcher))
        finally:
            self.rn.parent.chmod(0o755)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("the pinned build could not be installed", result.stdout)
        self.assertNotIn("is now the checked pinned build", result.stdout)
        self.assertNotIn(f"{self.rn} team", result.stdout)
        self.assertEqual(self.launcher.read_text(), UNTRUSTED)
        self.assertFalse(os.path.lexists(self.rn))
        self.assertEqual(list(self.rn.parent.iterdir()), [])
        self.assertEqual(json.loads(self.txn.read_text())["stage"], "failed_untrusted")
        data = json.loads(self.txn.read_text())
        data["stage"] = "replacing"
        self.txn.write_text(json.dumps(data))
        result = self.engine("rollback", "--txn", str(self.txn))
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("the pinned build could not be installed", result.stdout)
        self.assertEqual(self.launcher.read_text(), UNTRUSTED)
        self.untrusted(self.rn)
        data["stage"] = "replacing"
        self.txn.write_text(json.dumps(data))
        result = self.engine("rollback", "--txn", str(self.txn))
        self.assertIn("the pinned build could not be installed", result.stdout)
        self.assertEqual(self.launcher.read_text(), UNTRUSTED)
        self.put_engine(self.rn, NEW)
        self.txn.write_text(json.dumps(data))
        result = self.engine("rollback", "--txn", str(self.txn))
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("is now the checked pinned build", result.stdout)
        self.assertEqual(self.launcher.read_text(), rn_engine().LAUNCHER)
        self.assertFalse(self.untrusted_ran())


class EngineConfigCheck(unittest.TestCase):
    KEYS = ("auto_update", "lan")
    TOP = "auto_update = false\nlan = false\n"
    SAMPLES = (
        ("", 1),
        ("auto_update = false\nlan = false\n", 0),
        ('auto_update = false\nlan = false\nperson = "demo"\n[relay]\nurl = "x"\nvlan = true\n', 0),
        ('auto_update = false\nlan = false\nperson = "demo"\n[relay]\nurl = "x"\nlan = true\n', 2),
        ("  auto_update=false # off\nlan   =   false\n", 0),
        ('peers = [\n  "a",\n  "b",\n]\nauto_update = false\nlan = false\n', 0),
        ('person = "demo"\n[network]\nauto_update = false\nlan = false\n', 1),
        ("auto_update = true\nlan = false\n", 1),
        ("lan = false\n", 1),
        ("auto_update = false\nauto_update = false\nlan = false\n", 1),
        ("auto_update = false\nlan = 0\n", 1),
        ('"auto_update" = false\nlan = false\n', 2),
        ("'lan' = false\nauto_update = false\n", 2),
        ('note = """\nauto_update = false\n"""\nlan = false\n', 2),
        ("note = '''\nlan = false\n'''\nauto_update = false\n", 2),
        ('peers = [\n[1, 2],\n]\nauto_update = false\nlan = false\n', 2),
        ("auto_update = false\nlan =\n", 2),
        ("auto_update = false\nlan = false\nfolders.lan = false\n", 2),
        ('auto_update = false\nlan = false\ninbound.people.auto_update = "hold"\n', 2),
        ("auto_update = false\nlan = false\nfolders = { lan = false }\n", 2),
        ("auto_update = false\nlan = false\nrules = [\n  { auto_update = false },\n]\n", 2),
        ("auto_update = false\nlan = false # lan\nvlan.x = 1\nlan-x = 2\n"
         'note = "folders.lan = false"\npeers = [\n  "lan",\n]\n', 0),
    )
    APPENDED = (
        (TOP + 'person = "demo"\n\n[inbound]\ndefault = "accept"\n\n[inbound.people]\nbob = "hold"\n'
         "auto_update = false\nlan = false\n",
         TOP + 'person = "demo"\n\n[inbound]\ndefault = "accept"\n\n[inbound.people]\nbob = "hold"\n', 2),
        (TOP + '[folders]\n"~/personal" = "off"\nauto_update = false\nlan = false\n',
         TOP + '[folders]\n"~/personal" = "off"\n', 2),
        ('person = "demo"\n[inbound.people]\nauto_update = false\n"a b" = "refuse"  # lan\n\n[folders]\n'
         'lan = false\n"~/lan" = "plan"\n[extra]\nlan = false\nauto_update = false\nvlan = "lan"\n',
         'person = "demo"\n[inbound.people]\n"a b" = "refuse"  # lan\n\n[folders]\n'
         '"~/lan" = "plan"\n[extra]\nvlan = "lan"\n', 4),
        ('[folders]\nx = "y"\nlan = false', '[folders]\nx = "y"', 1),
        ("lan = true\n[folders]\nlan = false\n", "lan = true\n[folders]\n", 1),
        (TOP + '[inbound.people]\nlan = "hold"\nauto_update = false\n\n[folders]\nauto_update = "~/updates"\n'
         "lan = false\n",
         TOP + '[inbound.people]\nlan = "hold"\n\n[folders]\nauto_update = "~/updates"\n', 2),
    )
    STRINGS = (
        '[inbound.people]\nlan = "hold"\n',
        '[folders]\nauto_update = "~/updates"\nlan = "~/lan"\n',
        '[inbound.people]\n"lan" = "accept"\n\'auto_update\' = \'refuse\'\n',
        '[x]\n  lan="a#b"  # lan = false\nauto_update = ""\n',
        '[x]\nlan = "say \\"lan = false\\""\n',
    )
    OTHER = (
        "[folders]\nlan = true\n",
        "[folders]\nlan=false\n",
        "[folders]\n  lan = false\n",
        "[folders]\nlan = false \n",
        "[folders]\nlan = false # off\n",
        "[folders]\nlan  = false\n",
        "[folders]\nauto_update = FALSE\n",
        '[folders]\n"lan" = false\n',
        "[folders]\n'auto_update' = false\n",
        '[x]\nlan = "hold" x\n',
        '[x]\nlan = "a" "b"\n',
        "[x]\nlan = 1\n",
        '[x]\nlan = ["a"]\n',
        '[x]\nlan.y = "a"\n',
        "[folders]\nlan = false\r\n",
        "[lan]\nx = 1\n",
        "[inbound.lan]\nx = 1\n",
        '[inbound."lan"]\nx = 1\n',
        "[[auto_update]]\nx = 1\n",
        "[x]\ny = { lan = false }\n",
        "[x]\ny.lan = false\n",
        '[x]\ny."auto_update" = false\n',
        '[x]\ndefault = "accept"auto_update = false\n',
        '[x]\nlan = false\ny = """\nz"""\n',
        '[x]\ny = """\nlan = false\n"""\n',
        '[x]\nlan = false\ny = "open\n',
        "[x]\nlan = false\ny = [\n1,\n]\n",
    )
    ALIKE = (
        '[x]\ny = "lan"\n# lan = true\n"~/lan" = "plan"\nvlan = 1\nlan-party = 2\nwlan_x = 3\n',
        '[x]\ny = "auto_update = false"\nz = \'lan\'\n',
        "[auto_updates]\nplan = 1\n",
        '[x]\ny = "open\nz = [\n1,\n]\n',
    )

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "config.toml"
        self.backup = Path(temp.name) / "config.toml.agent-mesh.bak"

    def run_cli(self, cmd):
        return subprocess.run([sys.executable, str(ROOT / "scripts/engine_config.py"), cmd, "--path",
                               str(self.path), *self.KEYS], capture_output=True, text=True)

    def check(self):
        return self.run_cli("check")

    def clean(self):
        return self.run_cli("clean")

    def test_the_check_agrees_with_the_repair_on_every_sample(self):
        engine = rn_engine()
        for text, want in self.SAMPLES:
            with self.subTest(text=text):
                self.path.write_text(text)
                self.backup.unlink(missing_ok=True)
                result = self.check()
                self.assertEqual((result.returncode, result.stdout, result.stderr), (want, "", ""))
                cleaned = self.clean()
                if want == 2:
                    self.assertEqual((cleaned.returncode, cleaned.stdout, self.path.read_text()), (2, "", text))
                    self.assertFalse(self.backup.exists())
                    engine.config_off(self.path, self.KEYS)
                    self.assertIn(self.check().returncode, (0, 2))
                    continue
                self.assertEqual(cleaned.returncode, 0, cleaned.stderr)
                engine.config_off(self.path, self.KEYS)
                after = self.path.read_text()
                self.assertEqual(self.check().returncode, 0, after)
                if want == 1:
                    self.assertNotEqual(after, text)
                self.assertEqual(self.clean().stdout, f"0 {self.backup}\n")
                engine.config_off(self.path, self.KEYS)
                self.assertEqual(self.path.read_text(), after)

    def test_lines_an_old_installer_appended_inside_a_table_are_removed_and_nothing_else(self):
        engine = rn_engine()
        self.assertEqual(len(self.APPENDED), 6)
        for mode, (text, want, count) in zip((0o600, 0o640, 0o644, 0o600, 0o400, 0o600), self.APPENDED):
            with self.subTest(text=text):
                self.backup.unlink(missing_ok=True)
                self.path.unlink(missing_ok=True)
                self.path.write_bytes(text.encode())
                self.path.chmod(mode)
                self.assertEqual(self.check().returncode, 1)
                result = self.clean()
                self.assertEqual((result.returncode, result.stdout, result.stderr),
                                 (0, f"{count} {self.backup}\n", ""))
                self.assertEqual(self.path.read_bytes(), want.encode())
                self.assertEqual(self.backup.read_bytes(), text.encode())
                self.assertEqual((self.path.stat().st_mode & 0o777, self.backup.stat().st_mode & 0o777), (mode, mode))
                self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()),
                                 ["config.toml", "config.toml.agent-mesh.bak"])
                again = self.clean()
                self.assertEqual((again.returncode, again.stdout), (0, f"0 {self.backup}\n"))
                self.assertEqual((self.path.read_bytes(), self.backup.read_bytes()), (want.encode(), text.encode()))
                self.path.chmod(0o600)
                engine.config_off(self.path, self.KEYS)
                self.assertEqual(self.check().returncode, 0, self.path.read_text())
                if want.startswith(self.TOP):
                    self.assertEqual(self.path.read_text(), want)

    def test_a_copy_kept_by_an_interrupted_repair_is_not_replaced(self):
        first = self.TOP + "[folders]\nlan = false\n"
        self.path.write_text(first)
        self.assertEqual(self.clean().stdout, f"1 {self.backup}\n")
        self.path.write_text(self.TOP + "[folders]\nauto_update = false\n")
        self.assertEqual(self.clean().stdout, f"1 {self.backup}\n")
        self.assertEqual((self.path.read_text(), self.backup.read_text()), (self.TOP + "[folders]\n", first))
        import engine_config
        engine_config.drop_backup(self.path)
        engine_config.drop_backup(self.path)
        self.assertFalse(self.backup.exists())

    def test_any_other_mention_of_the_keys_inside_a_table_is_never_repaired(self):
        for top in (self.TOP, 'person = "demo"\n'):
            for tail in self.OTHER:
                text = top + tail
                with self.subTest(text=text):
                    self.path.write_bytes(text.encode())
                    self.assertEqual(self.check().returncode, 2)
                    result = self.clean()
                    self.assertEqual((result.returncode, result.stdout), (2, ""))
                    self.assertEqual(self.path.read_bytes(), text.encode())
                    self.assertFalse(self.backup.exists())
                    self.path.write_bytes((text + "[last]\nlan = false\n").encode())
                    self.assertEqual((self.check().returncode, self.clean().returncode), (2, 2))
                    self.assertFalse(self.backup.exists())

    def test_a_person_or_a_folder_named_like_a_setting_is_left_alone(self):
        engine = rn_engine()
        for tail in self.STRINGS:
            with self.subTest(tail=tail):
                text = self.TOP + tail
                self.path.write_text(text)
                self.assertEqual(self.check().returncode, 0)
                self.assertEqual(self.clean().stdout, f"0 {self.backup}\n")
                self.assertEqual(self.path.read_text(), text)
                self.assertFalse(self.backup.exists())
                self.path.write_text('person = "demo"\n' + tail)
                self.assertEqual(self.check().returncode, 1)
                self.assertEqual(self.clean().stdout, f"0 {self.backup}\n")
                engine.config_off(self.path, self.KEYS)
                self.assertEqual(self.path.read_text(), self.TOP + 'person = "demo"\n' + tail)
                self.assertEqual(self.check().returncode, 0)
                self.path.write_text(text + "[last]\nlan = false\n")
                self.assertEqual(self.check().returncode, 1)
                self.assertEqual(self.clean().stdout, f"1 {self.backup}\n")
                self.assertEqual(self.path.read_text(), text + "[last]\n")
                self.backup.unlink()

    def test_words_that_only_look_like_the_keys_are_left_alone(self):
        for tail in self.ALIKE:
            text = self.TOP + tail
            with self.subTest(text=text):
                self.path.write_text(text)
                self.assertEqual(self.check().returncode, 0)
                self.assertEqual(self.clean().stdout, f"0 {self.backup}\n")
                self.assertEqual(self.path.read_text(), text)
                self.assertFalse(self.backup.exists())
                self.path.write_text(tail)
                self.assertEqual(self.check().returncode, 1)

    def test_keys_the_engine_would_read_twice_are_never_reported_as_off(self):
        engine = rn_engine()
        for text in ('"auto_update" = false\nlan = false\n', "'lan' = true\nauto_update = false\nlan = false\n",
                     'note = """\nx\n"""\nauto_update = false\nlan = false\n'):
            with self.subTest(text=text):
                self.path.write_text(text)
                self.assertEqual(self.check().returncode, 2)
                engine.config_off(self.path, self.KEYS)
                self.assertEqual(self.check().returncode, 2)

    def test_a_missing_file_is_not_off_and_an_unreadable_one_is_never_reported_as_off(self):
        self.assertEqual(self.check().returncode, 1)
        self.assertEqual(self.clean().stdout, f"0 {self.backup}\n")
        self.assertFalse(self.path.exists())
        rn_engine().config_off(self.path, self.KEYS)
        self.assertEqual(self.check().returncode, 0)
        self.path.write_bytes(b"auto_update = false\nlan = false\nname = \"\xff\"\n")
        self.assertEqual((self.check().returncode, self.clean().returncode), (2, 2))
        self.path.unlink()
        self.path.mkdir()
        self.assertEqual((self.check().returncode, self.clean().returncode), (2, 2))
        self.assertFalse(self.backup.exists())

    def test_the_top_is_judged_per_key_and_the_tables_for_both(self):
        import engine_config
        self.path.write_text("auto_update = false\n[x]\ny = 1\n")
        self.assertEqual(engine_config.check(self.path, ("auto_update",)), 0)
        self.assertEqual(engine_config.check(self.path, ("lan",)), 1)
        self.assertEqual(engine_config.check(self.path, self.KEYS), 1)
        self.path.write_text(self.TOP + "[x]\nlan = false\n")
        self.assertEqual(engine_config.check(self.path, ("auto_update",)), 1)
        self.assertEqual(engine_config.check(self.path, ("lan",)), 1)
        self.path.write_text(self.TOP + "[x]\nlan = true\n")
        self.assertEqual(engine_config.check(self.path, ("auto_update",)), 2)
        self.assertEqual(engine_config.check(self.path, ("lan",)), 2)


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


class UpgradeSupport(FakeEngineMixin):
    def upgradable(self, version=OLD):
        return self.engine_ready(self.installed(), version)

    def engine_ready(self, repo, version):
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


class MacUpgrade(UpgradeSupport, InstallHarness, unittest.TestCase):
    """update.sh with a real (fake) engine daemon behind the fake launchd."""

    def test_engine_only_upgrade_stops_the_relay_switches_the_engine_then_starts_the_relay_once(self):
        repo = self.upgradable()
        old_pid = self.daemon(self.rn_home)
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertRegex(plan.stdout, rf"engine\s+changed\s+upgrade Reason Network {OLD} to {NEW}")
        self.assertEqual(self.service_changes(), [])
        self.assertEqual((self.version(self.rn), self.daemon(self.rn_home)), (OLD, old_pid))
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        result = self.run_tty(repo, "--no-pull", answers=["n"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("stopped; nothing was changed", result.stderr)
        self.assertEqual((self.version(self.rn), self.daemon(self.rn_home)), (OLD, old_pid))
        self.assertEqual(self.service_changes(), [])
        self.assertTrue(self.loaded())
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=120)
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
        self.assertEqual(steps, ["bootout", "bootstrap"])
        self.assertFalse((self.home / ".local/lib/agent-mesh/rollback").exists())
        self.assertIn(f"upgraded {OLD} -> {NEW}", result.stdout)
        self.assertTrue(self.loaded())
        marker = json.loads((self.home / "Library/Application Support/agent-relay/registered").read_text())
        self.assertEqual(marker["name"], "demo/bot")
        self.assertEqual(self.daemons_with_fd9(), [])
        again = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("already up to date", again.stdout)

    def test_a_failed_release_check_stops_before_the_engine_step(self):
        repo = self.upgradable()
        old_pid = self.daemon(self.rn_home)
        with open(repo / "box/rn-cloud.sh", "a") as stream:
            stream.write("\n")
        (self.git / "release-fail").write_text("")
        result = self.run_tty(repo, "--no-pull", answers=["y", "y", "y"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("could not be fetched", result.stderr)
        self.assertEqual((self.version(self.rn), self.daemon(self.rn_home)), (OLD, old_pid))
        self.assertEqual((self.backups(), self.service_changes()), ([], []))
        self.assertFalse(self.txn().exists())
        data = self.record()
        data["txn"] = {"stage": "relay_stopped", "relay_was_loaded": True, "need": ["cloud"], "lang": "en",
                       "commit": "a" * 40, "target": {}}
        self.write_record(data)
        self.txn().write_text(json.dumps({"stage": "replaced", "bin": str(self.rn), "home": str(self.rn_home),
                                          "backup": str(self.base / "nobackup")}))
        result = self.run_tty(repo, "--no-pull", answers=["y", "y", "y"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("could not be fetched", result.stderr)
        self.assertNotIn("rolling it back", result.stdout)
        self.assertTrue(self.txn().exists())
        self.assertEqual(self.version(self.rn), OLD)

    def test_a_relay_that_does_not_start_after_the_switch_rolls_back_the_engine_and_the_relay_code(self):
        repo = self.upgradable()
        relay = self.home / ".local/lib/agent-mesh/agent_relay.py"
        old_code = relay.read_bytes()
        with open(repo / "agent_relay.py", "a") as stream:
            stream.write("\n")
        (self.state / "fail-bootstrap").write_text("")
        result = self.run_tty(repo, "--no-pull", answers=["y"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"the relay did not register on the new engine; Reason Network {OLD} is back", result.stderr)
        self.assertEqual((self.version(self.rn), self.daemon_version(self.rn_home)), (OLD, OLD))
        self.assertEqual(len(self.backups()), 1)
        self.assertEqual(relay.read_bytes(), old_code)
        self.assertFalse(self.txn().exists())
        self.assertFalse((self.home / ".local/lib/agent-mesh/rollback").exists())
        self.assertTrue(self.loaded())
        self.assertEqual(self.messages(self.rn_home), {"m1": "kept"})

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
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("stopped; nothing was changed", result.stderr)
        self.assertTrue(self.txn().exists())
        result = self.run_tty(repo, "--no-pull", answers=["y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("an engine upgrade did not finish ('replaced'); rolling it back first", result.stdout)
        self.assertIn(f"upgraded {OLD} -> {NEW}", result.stdout)
        self.assertEqual(self.version(self.rn), NEW)
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
        self.assertEqual(self.daemons_with_fd9(), [])

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

    KEPT = 'person = "demo"\n\n[inbound]\ndefault = "accept"\n\n[inbound.people]\nbob = "hold"\n'
    APPENDED = "auto_update = false\nlan = false\n"

    def settings_in_a_table(self, version=NEW, text=None):
        repo = self.installed()
        self.cfg = self.home / ".reason/network/config.toml"
        self.kept = self.cfg.with_name("config.toml.agent-mesh.bak")
        self.cfg.write_text(self.KEPT + self.APPENDED if text is None else text)
        self.cfg.chmod(0o640)
        self.engine_ready(repo, version)
        self.assertEqual(self.daemon_settings(self.rn_home), "defaults")
        self.sent = self.engine_sends()
        self.asked = {key: self.record().get(key) for key in ("cloud", "cloud_setup", "prompt", "cloud_engine")}
        return repo

    def engine_sends(self):
        return [line for line in (self.ctl / "calls").read_text().splitlines() if line.split()[1:2] == ["send"]]

    def settings_off(self):
        import engine_config
        return engine_config.check(self.cfg, ("auto_update", "lan"))

    def config_state(self):
        return self.state_cmd("config-state").stdout.strip()

    def assert_settings_loaded(self, old_pid, want=None):
        want = self.APPENDED + self.KEPT if want is None else want
        pid = self.daemon(self.rn_home)
        self.assertNotEqual(pid, old_pid)
        self.assertEqual(self.cfg.read_text(), want)
        self.assertEqual(self.daemon_config(self.rn_home), want)
        self.assertEqual(self.daemon_settings(self.rn_home), "auto_update=false lan=false")
        self.assertEqual(self.settings_off(), 0)
        self.assertEqual(self.cfg.stat().st_mode & 0o777, 0o640)
        self.assertFalse(self.kept.exists())
        self.assertEqual(sorted(p.name for p in self.cfg.parent.glob("*config.toml*")), ["config.toml"])
        self.assertNotIn("engine_config", self.record())
        self.assertEqual(self.config_state(), "ok")
        self.assertFalse(self.txn().exists())
        self.assertTrue(self.loaded())
        check = self.run_script("check.sh")
        self.assertIn("PASS  auto_update off\nPASS  lan broadcast off\n", check.stdout)
        self.assertNotIn("has not loaded them yet", check.stdout)
        self.assertNotIn("cannot be checked", check.stdout)
        self.assertEqual(self.engine_sends(), self.sent)
        self.assertEqual({key: self.record().get(key) for key in self.asked}, self.asked)
        self.assertNotIn("request", self.record())

    def test_settings_in_a_table_are_corrected_and_the_daemon_is_restarted_once(self):
        repo = self.settings_in_a_table()
        old_pid, before = self.daemon(self.rn_home), self.cfg.read_bytes()
        queue = self.home / "Library/Application Support/agent-relay/state.json"
        queue.write_text(json.dumps({"spool": [{"id": "a"}], "pending": {"b": {"stage": "ringing"}}}))
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertRegex(plan.stdout, r"engine\s+unchanged")
        self.assertIn("security hardening: correct the engine settings and restart the engine", plan.stdout)
        self.assertIn("the relays still have 2 message(s) to hand over", plan.stdout)
        self.assertEqual(self.config_state(), "repair")
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.env["AGENT_MESH_PENDING_WAIT"] = "10"
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("waiting up to 0 minutes", result.stdout)
        self.assertIn("still have 2 message(s) to hand over; the engine was not restarted", result.stderr)
        self.assertEqual((self.cfg.read_bytes(), self.daemon(self.rn_home)), (before, old_pid))
        self.assertEqual(self.service_changes(), [])
        self.assertNotIn("engine_config", self.record())
        queue.write_text(json.dumps({"spool": [], "pending": {}}))
        started = time.time()
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"== engine 1. security hardening: correct the engine settings and restart the daemon "
                      f"(Reason Network {NEW})", result.stdout)
        self.assertIn(f"daemon restarted on {NEW}", result.stdout)
        self.assertIn(f"Removed 2 line(s) that an earlier version appended inside a table of {self.cfg}; the file "
                      f"as it was is kept as {self.kept} until the engine has loaded the corrected settings.",
                      result.stdout)
        self.assert_settings_loaded(old_pid)
        self.assertGreaterEqual(int(rn_engine().started(self.daemon(self.rn_home))), int(started))
        steps = [line.split()[1] for line in self.service_changes()]
        steps = [step for i, step in enumerate(steps) if i == 0 or steps[i - 1] != step]
        self.assertEqual(steps, ["bootout", "bootstrap"])
        self.assertEqual(self.backups(), [])
        self.assertEqual(self.daemons_with_fd9(), [])
        pid, final = self.daemon(self.rn_home), self.cfg.read_bytes()
        again = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("already up to date", again.stdout)
        self.assertNotIn("security hardening", again.stdout)
        self.assertEqual((self.daemon(self.rn_home), self.cfg.read_bytes()), (pid, final))
        self.assertFalse(self.kept.exists())

    def test_lines_left_in_several_tables_are_removed_and_their_neighbours_stay(self):
        top = self.APPENDED + 'person = "demo"\n\n'
        table = ('[inbound]\ndefault = "accept"\n\n[inbound.people]\nauto_update = false\nbob = "hold"\n'
                 'lan = "refuse"\n\n[folders]\n"~/lan" = "off"\nlan = false\nauto_update = "~/updates"\n\n[extra]\n')
        want = top + table.replace("auto_update = false\n", "").replace("lan = false\n", "")
        self.assertEqual(fake_effective(top + '[folders]\n"~/personal" = "off"\n' + self.APPENDED), "defaults")
        repo = self.settings_in_a_table(text=top + table + self.APPENDED)
        old_pid = self.daemon(self.rn_home)
        self.assertTrue(self.daemon_config(self.rn_home).startswith(self.APPENDED))
        self.assertEqual((self.settings_off(), self.config_state()), (1, "repair"))
        check = self.run_script("check.sh")
        self.assertEqual(check.returncode, 1, check.stdout + check.stderr)
        self.assertIn("FAIL  auto_update not off\n      -> ./update.sh (it sets  auto_update = false  above the "
                      f"first [table] line of {self.cfg}, removes the lines an earlier version appended inside "
                      "tables, and restarts the engine)\n", check.stdout)
        self.assertIn("FAIL  lan not off\n", check.stdout)
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertIn("security hardening: correct the engine settings and restart the engine", plan.stdout)
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("line(s) that an earlier version appended inside a table", result.stdout)
        self.assert_settings_loaded(old_pid, want)

    def test_a_table_that_names_a_setting_in_another_way_stops_the_update_before_anything_changes(self):
        top = self.APPENDED + 'person = "demo"\n\n[folders]\n"~/personal" = "off"\n'
        repo = self.settings_in_a_table(text=top + "lan = true\nauto_update = false\n")
        old_pid, before = self.daemon(self.rn_home), self.cfg.read_bytes()
        self.assertEqual((self.settings_off(), self.config_state()), (2, "unsupported"))
        check = self.run_script("check.sh")
        self.assertEqual(check.returncode, 1, check.stdout + check.stderr)
        for key in ("auto_update", "lan"):
            self.assertIn(f"FAIL  {key} cannot be checked\n      -> ./update.sh (it stops before changing anything and "
                          f"says what to change in {self.cfg} by hand; after the edit run ./update.sh again, which "
                          "restarts the engine so that it loads the corrected file)\n", check.stdout)
        self.assertNotIn("PASS  auto_update off", check.stdout)
        self.assertNotIn("engine_config", self.record())
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertNotIn("engine_config", self.record())
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertIn("cannot be checked; the update stops before changing anything", plan.stdout)
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("cannot be checked. Edit the file by hand", result.stderr)
        self.assertIn("a line that gives auto_update or lan a quoted text value may stay", result.stderr)
        self.assertIn("remove every other line, dotted key and table header that names them; then run ./update.sh "
                      "again (it restarts the engine so that it loads the corrected file)", result.stderr)
        self.assertEqual((self.cfg.read_bytes(), self.daemon(self.rn_home)), (before, old_pid))
        self.assertFalse(self.kept.exists())
        self.assertEqual(self.service_changes(), [])
        self.assertEqual(self.record()["engine_config"], {"state": "pending"})
        self.assertEqual(self.config_state(), "unsupported")
        check = self.run_script("check.sh")
        self.assertEqual(check.returncode, 1, check.stdout + check.stderr)
        self.assertIn("FAIL  auto_update cannot be checked\n      " + '-> ./update.sh (it stops before changing anything and says what to change in ', check.stdout)
        self.assertIn("FAIL  lan cannot be checked\n      " + '-> ./update.sh (it stops before changing anything and says what to change in ', check.stdout)
        self.assertNotIn("PASS  auto_update off", check.stdout)
        self.cfg.write_text(top)
        self.assertEqual(self.config_state(), "reload")
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_settings_loaded(old_pid, top)

    def test_a_record_that_cannot_be_written_stops_before_any_promise_of_a_restart(self):
        top = self.APPENDED + 'person = "demo"\n\n[folders]\n"~/personal" = "off"\n'
        repo = self.settings_in_a_table(text=top + "lan = true\nauto_update = false\n")
        old_pid, before = self.daemon(self.rn_home), self.cfg.read_bytes()
        lib = self.home / ".local/lib/agent-mesh"
        mode = lib.stat().st_mode & 0o777
        lib.chmod(0o555)
        try:
            result = self.run_script("update.sh", "--no-pull", "--yes", root=repo)
        finally:
            lib.chmod(mode)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("could not write the deployment record", result.stderr)
        self.assertNotIn("Edit the file by hand", result.stderr)
        self.assertNotIn("engine_config", self.record())
        self.assertEqual((self.cfg.read_bytes(), self.daemon(self.rn_home)), (before, old_pid))
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("cannot be checked. Edit the file by hand", result.stderr)
        self.assertEqual(self.record()["engine_config"], {"state": "pending"})
        self.cfg.write_text(top)
        self.assertEqual(self.config_state(), "reload")
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_settings_loaded(old_pid, top)

    def test_a_repair_that_stopped_before_the_write_was_recorded_is_finished_by_the_next_run(self):
        repo = self.settings_in_a_table()
        old_pid, before = self.daemon(self.rn_home), self.cfg.read_bytes()
        self.assertEqual(self.locked_state("config-pending").returncode, 0)
        cleaned = subprocess.run([sys.executable, str(ROOT / "scripts/engine_config.py"), "clean", "--path",
                                  str(self.cfg), "auto_update", "lan"], capture_output=True, text=True)
        self.assertEqual(cleaned.stdout, f"2 {self.kept}\n")
        self.assertEqual((self.settings_off(), self.config_state()), (1, "repair"))
        self.assertEqual(self.locked_state("config-verified").returncode, 1)
        self.assertEqual(self.kept.read_bytes(), before)
        rn_engine().config_off(self.cfg, ("auto_update", "lan"))
        self.assertEqual(self.record()["engine_config"], {"state": "pending"})
        self.assertEqual((self.settings_off(), self.config_state()), (0, "reload"))
        self.assertEqual(self.locked_state("config-verified").returncode, 1)
        self.assertEqual((self.kept.read_bytes(), self.daemon(self.rn_home)), (before, old_pid))
        self.finish_repair(repo, old_pid)

    def interrupted_repair(self, sig):
        repo = self.settings_in_a_table()
        old_pid, before = self.daemon(self.rn_home), self.cfg.read_bytes()
        self.flag("slowstop", NEW)
        proc = subprocess.Popen(["/bin/bash", str(repo / "update.sh"), "--no-pull", "--yes"], env=self.env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                preexec_fn=os.setpgrp)
        deadline = time.time() + 60
        while time.time() < deadline and proc.poll() is None and not (self.ctl / "stopping").exists():
            time.sleep(0.05)
        reached = (self.ctl / "stopping").exists()
        time.sleep(0.5)
        written, same_daemon = self.settings_off() == 0, self.daemon(self.rn_home) == old_pid
        os.killpg(proc.pid, sig)
        self.unflag("slowstop", NEW)
        out, err = proc.communicate(timeout=90)
        text = out.decode() + err.decode()
        self.assertTrue(reached and written and same_daemon, text)
        self.assertEqual(self.daemon(self.rn_home), old_pid, text)
        self.assertEqual(self.settings_off(), 0)
        record = self.record()["engine_config"]
        self.assertEqual(record["state"], "pending")
        self.assertIsInstance(record["written_s"], int)
        self.assertEqual(self.config_state(), "reload")
        self.assertIn("config reload", self.state_cmd("status", "--lang", "en").stdout.splitlines())
        self.assertEqual(self.cfg.read_text(), self.APPENDED + self.KEPT)
        self.assertEqual((self.kept.read_bytes(), self.kept.stat().st_mode & 0o777), (before, 0o640))
        self.assertEqual(self.daemon_settings(self.rn_home), "defaults")
        return repo, proc.returncode, text, old_pid

    def finish_repair(self, repo, old_pid):
        check = self.run_script("check.sh")
        self.assertEqual(check.returncode, 1, check.stdout + check.stderr)
        self.assertIn("PASS  auto_update off", check.stdout)
        self.assertIn("FAIL  the engine settings are corrected, but the running engine daemon has not loaded them "
                      "yet\n      -> ./update.sh", check.stdout)
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertIn("security hardening: correct the engine settings and restart the engine", plan.stdout)
        self.assertEqual(self.daemon(self.rn_home), old_pid)
        self.assertTrue(self.kept.exists())
        started = time.time()
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("appended inside a table", result.stdout)
        self.assert_settings_loaded(old_pid)
        self.assertGreaterEqual(int(rn_engine().started(self.daemon(self.rn_home))), int(started))
        check = self.run_script("check.sh")
        self.assertNotIn("has not loaded them yet", check.stdout)
        again = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("already up to date", again.stdout)

    def test_a_repair_terminated_before_the_restart_is_reported_and_finished_by_the_next_run(self):
        repo, code, text, old_pid = self.interrupted_repair(signal.SIGTERM)
        self.assertEqual(code, 143, text)
        self.assertIn("the engine settings repair is not finished; run ./update.sh again", text)
        self.assertFalse(self.txn().exists())
        self.assertTrue(self.loaded(), text)
        self.finish_repair(repo, old_pid)

    def test_a_repair_killed_before_the_restart_is_finished_by_the_next_run(self):
        repo, code, text, old_pid = self.interrupted_repair(signal.SIGKILL)
        self.assertEqual(code, -signal.SIGKILL, text)
        self.assertTrue(self.txn().exists())
        self.finish_repair(repo, old_pid)

    def test_a_pending_record_without_a_write_time_still_asks_for_the_restart(self):
        repo = self.upgradable(NEW)
        old_pid = self.daemon(self.rn_home)
        self.cfg = self.home / ".reason/network/config.toml"
        data = self.record()
        data["engine_config"] = {"state": "pending"}
        self.write_record(data)
        self.assertEqual(self.config_state(), "reload")
        self.assertEqual(self.locked_state("config-verified").returncode, 1)
        self.assertEqual(self.record()["engine_config"], {"state": "pending"})
        data["engine_config"] = {"state": "pending", "written_s": int(time.time()) + 3600}
        self.write_record(data)
        self.assertEqual(self.config_state(), "reload")
        data["engine_config"] = {"state": "pending", "written_s": 1}
        self.write_record(data)
        self.assertEqual(self.config_state(), "ok")
        data["engine_config"] = {"state": "pending"}
        self.write_record(data)
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual(self.daemon(self.rn_home), old_pid)
        self.assertNotIn("engine_config", self.record())

    def test_an_engine_upgrade_corrects_the_settings_before_the_switch(self):
        repo = self.settings_in_a_table(OLD)
        old_pid = self.daemon(self.rn_home)
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertRegex(plan.stdout, rf"engine\s+changed\s+upgrade Reason Network {OLD} to {NEW}")
        self.assertIn("security hardening: correct the engine settings and restart the engine", plan.stdout)
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Security hardening: the engine settings are corrected before the switch.", result.stdout)
        self.assertEqual((self.version(self.rn), self.daemon_version(self.rn_home)), (NEW, NEW))
        self.assert_settings_loaded(old_pid)
        [backup] = self.backups()
        self.assertEqual(self.version(backup / "reason-network"), OLD)

    def test_settings_the_check_cannot_read_stop_the_update_with_the_lines_to_write(self):
        repo = self.upgradable(NEW)
        old_pid = self.daemon(self.rn_home)
        cfg = self.home / ".reason/network/config.toml"
        cfg.write_text('"auto_update" = false\nlan = false\nperson = "demo"\n')
        before = cfg.read_bytes()
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertIn("cannot be checked; the update stops before changing anything", plan.stdout)
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("cannot be checked. Edit the file by hand", result.stderr)
        self.assertIn("auto_update = false", result.stderr)
        self.assertEqual((cfg.read_bytes(), self.daemon(self.rn_home)), (before, old_pid))
        self.assertEqual(self.service_changes(), [])
        self.assertEqual(self.record()["engine_config"], {"state": "pending"})
        check = self.run_script("check.sh")
        self.assertIn("FAIL  auto_update cannot be checked\n      " + '-> ./update.sh (it stops before changing anything and says what to change in ', check.stdout)
        self.assertIn("FAIL  lan cannot be checked\n      " + '-> ./update.sh (it stops before changing anything and says what to change in ', check.stdout)
        self.assertNotIn("PASS  auto_update off", check.stdout)

    def test_a_changed_cloud_engine_helper_asks_for_the_cloud_engine_step_again(self):
        repo = self.installed()
        with open(repo / "box/rn-engine.py", "a") as stream:
            stream.write("\n")
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertRegex(plan.stdout, r"cloud engine\s+changed")

    def test_an_unknown_cloud_engine_is_checked_by_grok_bot(self):
        repo = self.installed()
        data = self.record()
        del data["cloud_engine"]
        self.write_record(data)
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertRegex(plan.stdout, r"cloud engine\s+unknown")
        self.assertIn(f"checks Reason Network {NEW} by itself", plan.stdout)
        reply = self.good_reply(repo)
        self.bot_replies(body=reply.replace(f"reason-network {NEW} running", f"reason-network {OLD} running"))
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"mismatch: the cloud engine runs {OLD}, not {NEW}", result.stdout)
        self.assertNotIn("cloud_engine", self.record())
        self.bot_replies(body=reply)
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.record()["cloud_engine"]["state"], "confirmed")
        self.assertEqual(self.record()["cloud_engine"]["version"], NEW)
        self.assertEqual(len(self.sent()), 2)
        self.assertEqual(self.service_changes(), [])
        self.assertTrue(self.loaded())


class MultiBotUpgrade(UpgradeSupport, AddSupport, unittest.TestCase):
    """update.sh on a Mac with two Bots: one engine transaction covers the relays of both."""

    def setUp(self):
        super().setUp()
        self.labels = [LEGACY_LABEL, f"{LEGACY_LABEL}.beta"]

    def two_upgradable(self, version=OLD):
        repo = self.two_ready()
        self.added(repo)
        con = sqlite3.connect(self.home / ".reason/network/network.db")
        con.execute("DROP TABLE IF EXISTS messages")
        con.commit()
        con.close()
        return self.engine_ready(repo, version)

    def lib(self):
        return self.home / ".local/lib/agent-mesh"

    def plists(self):
        return [self.legacy_plist().read_bytes(), self.plist("beta").read_bytes()]

    def members(self, beta_loaded=True):
        return [{"name": "alpha", "label": LEGACY_LABEL, "plist": str(self.legacy_plist()), "was_loaded": True},
                {"name": "beta", "label": self.labels[1], "plist": str(self.plist("beta")), "was_loaded": beta_loaded}]

    def launchctl(self, *args):
        subprocess.run([str(self.bin / "launchctl"), *args], env=self.env, check=True)

    def new_code(self, repo):
        with open(repo / "agent_relay.py", "a") as stream:
            stream.write("\n")
        return hashlib.sha256((repo / "agent_relay.py").read_bytes()).hexdigest()

    def assert_registered(self):
        loaded = self.multi_loaded()
        for label, state in ((self.labels[0], "agent-relay"), (self.labels[1], "agent-relay-bots/beta")):
            marker = json.loads((self.home / "Library/Application Support" / state / "registered").read_text())
            self.assertEqual(marker["pid"], loaded[label]["pid"], label)

    def test_both_relays_stop_before_the_switch_and_each_starts_once_on_the_new_engine(self):
        repo = self.two_upgradable()
        new = self.new_code(repo)
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertIn(m("update.plan_bots"), plan.stdout)
        for name in ("alpha", "beta"):
            self.assertIn(m("update.plan_bot", name, "changed", "unchanged"), plan.stdout)
        self.assertIn(m("update.plan_engine_order"), plan.stdout)
        before = len(self.multi_started())
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(m("update.e_stop_relays", "alpha, beta"), result.stdout)
        self.assertEqual((self.version(self.rn), self.daemon_version(self.rn_home)), (NEW, NEW))
        self.assertEqual(self.messages(self.rn_home), {"m1": "kept"})
        changes = self.service_changes()
        self.assertEqual(changes[:2], [f"launchctl bootout gui/501/{label}" for label in self.labels])
        starts = [line.split(" ", 3)[3] for line in changes if line.startswith("launchctl bootstrap")]
        self.assertEqual(starts, [str(self.legacy_plist()), str(self.plist("beta"))])
        first = next(i for i, line in enumerate(changes) if line.startswith("launchctl bootstrap"))
        self.assertTrue(all(line.startswith("launchctl bootout") for line in changes[:first]))
        self.assertEqual(self.multi_started()[before:], [[label, new] for label in self.labels])
        self.assertEqual(sorted(self.multi_loaded()), self.labels)
        self.assert_registered()
        self.assertFalse(self.txn().exists())
        self.assertFalse((self.lib() / "rollback").exists())
        self.assertEqual(self.daemons_with_fd9(), [])
        again = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("already up to date", again.stdout)

    def test_the_settings_repair_restarts_the_relays_of_both_bots_once_and_asks_the_cloud_nothing(self):
        repo = self.two_upgradable(NEW)
        cfg = self.home / ".reason/network/config.toml"
        kept = cfg.with_name("config.toml.agent-mesh.bak")
        old_pid, top = self.daemon(self.rn_home), cfg.read_text()
        self.assertEqual(self.state_cmd("config-state").stdout, "ok\n")
        cfg.write_text(top + '\n[inbound.people]\nbob = "hold"\nauto_update = false\nlan = false\n')
        sends = [line for line in (self.ctl / "calls").read_text().splitlines() if line.split()[1:2] == ["send"]]
        asked = {key: self.record().get(key) for key in ("cloud", "cloud_setup", "prompt", "cloud_engine", "bots")}
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertIn("security hardening: correct the engine settings and restart the engine", plan.stdout)
        for name in ("alpha", "beta"):
            self.assertIn(m("update.plan_bot", name, "unchanged", "unchanged"), plan.stdout)
        before = len(self.multi_started())
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotEqual(self.daemon(self.rn_home), old_pid)
        self.assertEqual(cfg.read_text(), top + '\n[inbound.people]\nbob = "hold"\n')
        self.assertEqual(self.daemon_settings(self.rn_home), "auto_update=false lan=false")
        self.assertFalse(kept.exists())
        changes = self.service_changes()
        self.assertEqual(changes[:2], [f"launchctl bootout gui/501/{label}" for label in self.labels])
        starts = [line.split(" ", 3)[3] for line in changes if line.startswith("launchctl bootstrap")]
        self.assertEqual(starts, [str(self.legacy_plist()), str(self.plist("beta"))])
        self.assertEqual([label for label, _ in self.multi_started()[before:]], self.labels)
        self.assert_registered()
        self.assertFalse(self.txn().exists())
        self.assertNotIn("engine_config", self.record())
        self.assertNotIn("request", self.record())
        self.assertEqual({key: self.record().get(key) for key in asked}, asked)
        self.assertEqual([line for line in (self.ctl / "calls").read_text().splitlines()
                          if line.split()[1:2] == ["send"]], sends)
        check = self.run_script("check.sh")
        self.assertIn("PASS  auto_update off\nPASS  lan broadcast off\n", check.stdout)
        self.assertNotIn("has not loaded them yet", check.stdout)
        again = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("already up to date", again.stdout)

    def test_a_relay_that_does_not_register_rolls_back_the_engine_and_every_relay(self):
        repo = self.two_upgradable()
        relay = self.lib() / "agent_relay.py"
        old_code, plists = relay.read_bytes(), self.plists()
        old = hashlib.sha256(old_code).hexdigest()
        self.new_code(repo)
        for label in self.labels:
            with self.subTest(label):
                (self.state / f"noreg.{label}").write_text("")
                before = len(self.multi_started())
                result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=120)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(f"the relay did not register on the new engine; Reason Network {OLD} is back",
                              result.stderr)
                self.assertEqual((self.version(self.rn), self.daemon_version(self.rn_home)), (OLD, OLD))
                self.assertEqual((relay.read_bytes(), self.plists()), (old_code, plists))
                self.assertFalse(self.txn().exists())
                self.assertFalse((self.lib() / "rollback").exists())
                self.assertEqual(sorted(self.multi_loaded()), self.labels)
                self.assertEqual(self.multi_started()[before:][-2:], [[name, old] for name in self.labels])
                self.assertFalse((self.state / f"noreg.{label}").exists())
                self.assert_registered()
                self.assertEqual(self.messages(self.rn_home), {"m1": "kept"})

    def test_interrupted_during_the_switch_both_relays_come_back_on_the_old_engine(self):
        repo = self.two_upgradable()
        self.flag("slow", NEW)
        code, out, err = self.interrupt_at(repo, "replaced")
        self.assertEqual(code, 143, out + err)
        self.assertIn("rolling it back", out)
        self.assertEqual((self.version(self.rn), self.daemon_version(self.rn_home)), (OLD, OLD))
        self.assertFalse(self.txn().exists())
        self.assertEqual(sorted(self.multi_loaded()), self.labels)
        self.assertIn(m("update.bot_running_again", "alpha"), out)
        self.assertIn(m("update.bot_running_again", "beta"), out)

    def test_a_transaction_left_in_prepared_starts_only_the_relays_that_were_already_stopped(self):
        repo = self.two_upgradable()
        self.txn().write_text(json.dumps({"stage": "prepared", "phase": "prepared", "kind": "upgrade",
                                          "bin": str(self.rn), "home": str(self.rn_home), "backup": "",
                                          "extra": {"participants": json.dumps(self.members())}}))
        self.launchctl("bootout", f"gui/501/{self.labels[1]}")
        (self.base / "calls").unlink()
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertIn(m("update.plan_etxn", "prepared"), plan.stdout)
        self.assertEqual(self.service_changes(), [])
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(m("update.etxn_first", "prepared"), result.stdout)
        self.assertIn(m("update.bot_running_again", "beta"), result.stdout)
        self.assertNotIn(m("update.bot_running_again", "alpha"), result.stdout)
        self.assertEqual(self.service_changes()[0], f"launchctl bootstrap gui/501 {self.plist('beta')}")
        self.assertEqual(self.version(self.rn), NEW)
        self.assertEqual(sorted(self.multi_loaded()), self.labels)
        self.assertFalse(self.txn().exists())

    def prepared_txn(self, members):
        self.txn().write_text(json.dumps({"stage": "prepared", "phase": "prepared", "kind": "upgrade",
                                          "bin": str(self.rn), "home": str(self.rn_home), "backup": "",
                                          "extra": {"participants": json.dumps(members)}}))

    def test_a_relay_that_does_not_come_back_keeps_the_engine_record_for_the_next_run(self):
        repo = self.two_upgradable()
        self.prepared_txn(self.members())
        self.launchctl("bootout", f"gui/501/{self.labels[1]}")
        (self.state / f"fail-bootstrap.{self.labels[1]}").write_text("")
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=120)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("update.engine_not_undone", str(self.txn())), result.stdout + result.stderr)
        self.assertTrue(self.txn().exists())
        self.assertEqual(sorted(self.multi_loaded()), [LEGACY_LABEL])
        self.assertEqual(self.version(self.rn), OLD)
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(m("update.bot_running_again", "beta"), result.stdout)
        self.assertFalse(self.txn().exists())
        self.assertEqual(sorted(self.multi_loaded()), self.labels)
        self.assertEqual(self.version(self.rn), NEW)

    def test_the_saved_relays_stay_until_the_engine_step_is_recorded_as_committed(self):
        repo = self.two_upgradable()
        relay = self.lib() / "agent_relay.py"
        old_code = relay.read_bytes()
        self.new_code(repo)
        self.env["MOCK_PY_FAIL"] = "rn-engine.py phase committed"
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=120)
        del self.env["MOCK_PY_FAIL"]
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(m("update.e_not_committed", str(self.txn())), result.stderr)
        self.assertIn("rolling it back", result.stdout)
        self.assertEqual((self.version(self.rn), self.daemon_version(self.rn_home)), (OLD, OLD))
        self.assertEqual(relay.read_bytes(), old_code)
        self.assertFalse(self.txn().exists())
        self.assertFalse((self.lib() / "rollback").exists())
        self.assertEqual(sorted(self.multi_loaded()), self.labels)
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.version(self.rn), NEW)
        self.assertNotEqual(relay.read_bytes(), old_code)

    def test_engine_recovery_leaves_out_a_bot_removed_since_the_transaction(self):
        repo = self.two_upgradable()
        result = self.run_script("install.sh", "--restart-relay", "--keep-rollback", root=repo, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.lib() / "rollback/bots/beta/loaded").is_file())
        self.prepared_txn(self.members())
        self.launchctl("bootout", f"gui/501/{self.labels[1]}")
        self.plist("beta").unlink()
        shutil.rmtree(self.home / ".config/agent-relay-bots/beta")
        data = self.record()
        del data["bots"]["beta"]
        data["removed_bots"] = ["beta"]
        self.write_record(data)
        (self.base / "calls").unlink()
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(m("install.rollback_skipped", "beta"), result.stdout + result.stderr)
        self.assertNotIn(m("update.bot_running_again", "beta"), result.stdout)
        self.assertEqual(sorted(self.multi_loaded()), [LEGACY_LABEL])
        self.assertFalse(self.plist("beta").exists())
        self.assertNotIn(str(self.plist("beta")), self.calls())
        self.assertFalse(self.txn().exists())
        self.assertFalse((self.lib() / "rollback").exists())

    def test_a_committed_transaction_is_only_removed(self):
        repo = self.two_upgradable(NEW)
        self.txn().write_text(json.dumps({"stage": "verified", "phase": "committed", "kind": "upgrade",
                                          "bin": str(self.rn), "home": str(self.rn_home), "backup": "",
                                          "extra": {"participants": json.dumps(self.members())}}))
        (self.lib() / "rollback").mkdir()
        (self.lib() / "rollback/agent_relay.py").write_text("old")
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertIn(m("update.plan_etxn_committed"), plan.stdout)
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(m("update.etxn_committed"), result.stdout)
        self.assertNotIn("rolling it back", result.stdout)
        self.assertFalse(self.txn().exists())
        self.assertFalse((self.lib() / "rollback").exists())
        self.assertEqual(self.service_changes(), [])
        self.assertEqual(self.version(self.rn), NEW)

    def test_an_interrupted_relay_switch_after_the_engine_switch_puts_back_the_old_code_first(self):
        repo = self.two_upgradable()
        old = hashlib.sha256((self.lib() / "agent_relay.py").read_bytes()).hexdigest()
        new = self.new_code(repo)
        eng = [sys.executable, str(repo / "box/rn-engine.py")]
        run = lambda *args: subprocess.run(eng + list(args), env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(run("prepare", "--txn", str(self.txn()), "--home", str(self.rn_home), "--bin", str(self.rn),
                             "--keep", f"participants={json.dumps(self.members())}").returncode, 0)
        for label in self.labels:
            self.launchctl("bootout", f"gui/501/{label}")
        self.assertEqual(run("phase", "--txn", str(self.txn()), "stopped").returncode, 0)
        self.put_engine(self.base / "new/reason-network", NEW)
        switched = run("switch", "--home", str(self.rn_home), "--bin", str(self.rn), "--new",
                       str(self.base / "new/reason-network"), "--pinned", NEW, "--backup", str(self.base / "bk"),
                       "--txn", str(self.txn()))
        self.assertEqual(switched.returncode, 0, switched.stdout + switched.stderr)
        result = self.run_script("install.sh", "--restart-relay", "--keep-rollback", root=repo, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.lib() / "rollback").exists())
        self.assertEqual(json.loads(self.txn().read_text())["phase"], "switched")
        before = len(self.multi_started())
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(m("update.etxn_first", "verified"), result.stdout)
        self.assertEqual(self.multi_started()[before:], [[label, old] for label in self.labels]
                         + [[label, new] for label in self.labels])
        self.assertEqual((self.version(self.rn), self.daemon_version(self.rn_home)), (NEW, NEW))
        self.assertFalse((self.lib() / "rollback").exists())
        self.assertFalse(self.txn().exists())

    def test_a_relay_that_was_stopped_stays_stopped_on_rollback_and_a_configured_bot_is_never_touched(self):
        repo = self.two_upgradable()
        data = self.record()
        data["bots"]["gamma"] = {"stage": "configured", "added_ms": data["bots"]["beta"]["added_ms"] + 1,
                                 "cloud": {"state": "pending"}}
        self.write_record(data)
        self.launchctl("bootout", f"gui/501/{self.labels[1]}")
        (self.state / f"noreg.{self.labels[1]}").write_text("")
        self.new_code(repo)
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertIn(m("update.plan_bot_waiting", "gamma", "gamma"), plan.stdout)
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=120)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(sorted(self.multi_loaded()), [LEGACY_LABEL])
        self.assertEqual(self.version(self.rn), OLD)
        self.assertNotIn(m("update.bot_running_again", "beta"), result.stdout)
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(sorted(self.multi_loaded()), self.labels)
        self.assertNotIn("gamma", self.calls())

    def test_a_stale_daemon_restart_records_its_transaction_before_stopping_the_relays(self):
        repo = self.two_upgradable(NEW)
        time.sleep(1.1)
        self.put_engine(self.rn, NEW)
        self.flag("slowafterstop", NEW)
        master, slave = pty.openpty()
        seen = None
        try:
            os.write(master, b"y\n")
            proc = subprocess.Popen(["/bin/bash", str(repo / "update.sh"), "--no-pull"], env=self.env, stdin=slave,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=os.setpgrp)
            deadline = time.time() + 60
            while time.time() < deadline and proc.poll() is None:
                if (self.ctl / "stopped").exists():
                    seen = json.loads(self.txn().read_text())
                    break
                time.sleep(0.01)
            time.sleep(0.5)
            unloaded = self.multi_loaded() == {}
            os.killpg(proc.pid, signal.SIGTERM)
            self.unflag("slowafterstop", NEW)
            out, err = proc.communicate(timeout=90)
        finally:
            os.close(slave)
            os.close(master)
        self.assertIsNotNone(seen, out.decode() + err.decode())
        self.assertEqual((seen["kind"], seen["phase"], seen["stage"]), ("restart", "stopped", "restarting"))
        self.assertEqual([p["name"] for p in json.loads(seen["extra"]["participants"])], ["alpha", "beta"])
        self.assertTrue(unloaded)
        self.assertEqual(proc.returncode, 143, out.decode() + err.decode())
        self.assertEqual(sorted(self.multi_loaded()), self.labels)
        self.assertFalse(self.txn().exists())

    @unittest.skipUnless(shutil.which("ps"), "needs ps")
    def test_an_unmanaged_relay_is_listed_but_never_stopped(self):
        repo = self.two_upgradable()
        managed = self.multi_loaded()[LEGACY_LABEL]["pid"]
        real = shutil.which("ps")
        self.tool("ps", f'if [ "$1" = -axo ]; then "{real}" "$@"; echo "  888 {managed} {self.rn} listen --as alpha"; '
                        f'echo "  777  4242 {self.rn} listen --as helper"; else exec "{real}" "$@"; fi')
        result = self.run_script("update.sh", "--no-pull", "--yes", root=repo, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("These processes also use Reason Network", result.stdout)
        self.assertIn(f"777 {self.rn} listen --as helper", result.stdout)
        self.assertNotIn(f"888 {self.rn}", result.stdout)
        touched = {line.split()[2].rsplit("/", 1)[-1] for line in self.service_changes() if "bootout" in line}
        self.assertEqual(touched, set(self.labels))
        self.assertNotIn("kill", self.calls())
        self.assertEqual(self.version(self.rn), NEW)


class CloudUpgrade(FakeEngineMixin, InstallHarness, unittest.TestCase):
    def cloud(self, version=OLD, joined=True, layout="new"):
        self.cloud_box()
        self.setup_ctl(self.base, self.env)
        if joined:
            self.joined_before()
            (self.box / "person").write_text("demo\n")
        self.rn = self.box / "bin/engine/reason-network"
        self.launcher = self.box / "bin/reason-network"
        self.pin_builds({v: fake_engine(v).encode() for v in (OLD, NEW, "0.13.0")})
        if layout == "new":
            self.put_engine(self.rn, version)
            rn_engine().put_launcher(self.launcher)
            self.cur = self.rn
        else:
            self.put_engine(self.launcher, version)
            self.cur = self.launcher
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

    def assert_reported(self, out):
        digest = hashlib.sha256(fake_engine(NEW).encode()).hexdigest()
        self.assertIn(f"engine: pinned build {digest} at box/bin/engine/reason-network", out)
        self.assertIn("engine: launcher ok", out)
        self.assertEqual(self.launcher.read_text(), rn_engine().LAUNCHER)
        self.assertTrue(os.access(self.launcher, os.X_OK))

    def untrusted(self):
        self.cur.write_text(UNTRUSTED)
        self.cur.chmod(0o755)

    def untrusted_ran(self):
        return (self.ctl / "untrusted-ran").exists()

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
        self.assert_reported(result.stdout)
        self.assertEqual(self.version(self.rn), NEW)
        self.assertEqual(self.daemon_version(self.rn_home), NEW)
        self.assertEqual(self.messages(self.rn_home), {"m1": "kept"})
        [backup] = sorted(self.box.glob("engine-backup-*"))
        self.assertEqual(self.version(backup / "reason-network"), OLD)
        again = self.upgrade()
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("engine: nothing to upgrade", again.stdout)
        self.assertEqual(self.engine_line(again.stdout), NEW)
        self.assert_reported(again.stdout)

    def test_cloud_upgrade_moves_an_old_layout_engine_and_leaves_a_launcher(self):
        for version in (OLD, NEW):
            with self.subTest(version):
                self.setUp()
                self.cloud(version, layout="old")
                self.start(self.launcher, self.rn_home)
                self.send(self.launcher, self.rn_home, "m1", "kept")
                result = self.upgrade()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                if version == NEW:
                    self.assertIn("moving Reason Network 0.12.17 to box/bin/engine", result.stdout)
                self.assert_reported(result.stdout)
                self.assertEqual(self.version(self.rn), NEW)
                self.assertEqual(self.daemon_version(self.rn_home), NEW)
                self.assertEqual(self.messages(self.rn_home), {"m1": "kept"})
                [backup] = sorted(self.box.glob("engine-backup-*"))
                self.assertEqual(self.version(backup / "reason-network"), version)

    def test_cloud_upgrade_with_the_pinned_version_starts_or_restarts_the_daemon(self):
        self.cloud(NEW)
        result = self.upgrade()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.engine_line(result.stdout), NEW)
        self.assertIn("nothing to upgrade", result.stdout)
        old_pid = self.daemon(self.rn_home)
        time.sleep(1.1)
        self.put_engine(self.rn, NEW)
        self.launcher.write_text("#!/bin/sh\necho replaced\n")
        result = self.upgrade()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("started before the binary was replaced; restarting it", result.stdout)
        self.assertNotEqual(self.daemon(self.rn_home), old_pid)
        self.assertEqual(self.engine_line(result.stdout), NEW)
        self.assert_reported(result.stdout)
        self.assertIn("wrote the launcher", result.stdout)

    def test_cloud_upgrade_rolls_back_when_the_new_daemon_fails(self):
        for layout in ("new", "old"):
            with self.subTest(layout):
                self.setUp()
                self.cloud(layout=layout)
                real = shutil.which("date")
                self.tool("date", f'if [ "$2" = "+%Y%m%dT%H%M%SZ" ]; then echo 20261111T000000Z; '
                                  f'else exec "{real}" "$@"; fi')
                self.start(self.cur, self.rn_home)
                self.flag("nostart", NEW)
                result = self.upgrade()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("the previous engine is back", result.stderr)
                self.assertEqual(self.version(self.cur), OLD)
                self.assertEqual(self.daemon_version(self.rn_home), OLD)
                self.assertEqual(self.engine_line(result.stdout), OLD)
                if layout == "old":
                    self.assertFalse(os.path.lexists(self.rn))
                self.unflag("nostart", NEW)
                result = self.upgrade()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.version(self.rn), NEW)
                self.assert_reported(result.stdout)
                self.assertEqual([b.name for b in sorted(self.box.glob("engine-backup-*"))],
                                 ["engine-backup-20261111T000000Z", "engine-backup-20261111T000000Z-2"])

    def test_cloud_upgrade_never_downgrades_a_pinned_newer_build(self):
        self.cloud("0.13.0")
        result = self.upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("never downgrades or replaces it", result.stderr)
        self.assertEqual(self.version(self.rn), "0.13.0")

    def test_cloud_upgrade_replaces_an_unpinned_engine_without_running_it(self):
        for layout in ("new", "old"):
            with self.subTest(layout):
                self.setUp()
                self.cloud(layout=layout)
                self.untrusted()
                result = self.upgrade()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("is not a Reason Network build pinned by agent-mesh; replacing it", result.stdout)
                self.assertFalse(self.untrusted_ran())
                self.assertEqual(self.version(self.rn), NEW)
                self.assertEqual(self.daemon_version(self.rn_home), NEW)
                self.assert_reported(result.stdout)
                [backup] = sorted(self.box.glob("engine-backup-*"))
                self.assertEqual((backup / "reason-network").read_text(), UNTRUSTED)

    def test_cloud_upgrade_stops_a_daemon_of_an_unpinned_engine_with_the_new_binary(self):
        self.cloud("9.9.9")
        self.start(self.rn, self.rn_home)
        old_pid = self.daemon(self.rn_home)
        self.untrusted()
        result = self.upgrade()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.untrusted_ran())
        self.assertNotEqual(self.daemon(self.rn_home), old_pid)
        self.assertEqual(self.daemon_version(self.rn_home), NEW)

    def test_cloud_upgrade_of_an_unpinned_engine_never_puts_it_back(self):
        self.cloud()
        self.untrusted()
        self.flag("nostart", NEW)
        result = self.upgrade()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("the upgrade did not finish; follow the steps above", result.stderr)
        self.assertIn("so it is not put back", result.stdout)
        self.assertFalse(self.untrusted_ran())
        self.assertEqual(self.version(self.rn), NEW)
        self.unflag("nostart", NEW)
        result = self.upgrade()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("nothing to upgrade", result.stdout)
        self.assertFalse(self.untrusted_ran())

    def test_cloud_setup_with_an_older_engine_points_to_the_upgrade(self):
        for layout in ("new", "old"):
            with self.subTest(layout):
                self.setUp()
                self.cloud(layout=layout)
                result = self.setup(tip.invite(), "demo")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--upgrade-engine", result.stderr)
                self.assertEqual(self.version(self.cur), OLD)

    def test_cloud_setup_with_an_unpinned_engine_stops_without_running_it(self):
        self.cloud(joined=False)
        self.untrusted()
        result = self.setup(tip.invite(), "demo")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("is not a Reason Network build pinned by agent-mesh; repair it with", result.stderr)
        self.assertFalse(self.untrusted_ran())

    def test_cloud_setup_exports_no_update_to_every_engine_command(self):
        self.cloud_box()
        engine = CLOUD_ENV_ENGINE.replace("@LOG@", str(self.base / "env.log"))
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as tar:
            item = tarfile.TarInfo("reason-network")
            item.mode, item.size = 0o755, len(engine)
            tar.addfile(item, io.BytesIO(engine.encode()))
        (self.mock / "e.tgz").write_bytes(archive.getvalue())
        pin(self.box / "rn-cloud-setup.sh", "x86_64-unknown-linux-musl", hashlib.sha256(archive.getvalue()).hexdigest())
        self.pin_builds({NEW: engine.encode()})
        self.env["REASON_NETWORK_NO_UPDATE"] = "0"
        result = self.setup(tip.invite(), "demo")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = (self.base / "env.log").read_text().splitlines()
        self.assertTrue(any(l.startswith("join ") for l in lines), lines)
        self.assertEqual([l for l in lines if not l.endswith(" NO_UPDATE=1 MACHINE=cloud")], [])
        config = (self.box / "state/config.toml").read_text()
        for key in ("auto_update", "lan", "tray"):
            self.assertIn(f"{key} = false", config)
        self.assertEqual(self.launcher_text(), rn_engine().LAUNCHER)

    def launcher_text(self):
        return (self.box / "bin/reason-network").read_text()
