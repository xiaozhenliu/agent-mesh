#!/usr/bin/env python3
"""Reason Network engine facts and engine switches for update.sh, check.sh and the cloud setup (Mac and Linux).

  rn-engine.py compare <installed> <pinned>       older | same | newer | unknown
  rn-engine.py pid --home DIR                      pid of the running daemon (holder of DIR/daemon.lock), or nothing
  rn-engine.py info --home DIR --bin PATH          pid=<n|-> started=<t|-> replaced=<t> state=<fresh|stale|none>
  rn-engine.py status --home DIR --bin PATH        one line: version, daemon pid, start and replace time; exit 1
                                                   if no daemon runs or it is stale
  rn-engine.py registered --home DIR --marker FILE --name TEAM/NAME [--relay-pid PID] [--grace S]
                                                   ok | wait | fail and a reason: has the relay registered (its
                                                   marker) since the running daemon started
  rn-engine.py others --bin PATH [--skip-parent PID]
                                                   other processes running PATH (not the daemon or its tray)
  rn-engine.py switch --home DIR --bin PATH --new NEWBIN --pinned V --backup DEST [--txn FILE] [--keep K=V]...
                     [--old-bin OLD] [--untrusted-old]
                                                   back up PATH and DIR, replace PATH atomically, restart the daemon
                                                   and verify it; on failure put the old binary back. --old-bin: the
                                                   binary being replaced lives at OLD (cloud layout migration): it is
                                                   backed up and restored there, and the launcher is written there on
                                                   success. --untrusted-old: the old binary is not a pinned build; it
                                                   is backed up but never run, and never put back
  rn-engine.py restart --home DIR --bin PATH       restart the daemon on PATH and verify it started after PATH was
                                                   last replaced
  rn-engine.py rollback --txn FILE                 undo an unfinished switch recorded in FILE (binary only; a binary
                                                   that was not a pinned build is never put back)
  rn-engine.py txn-get --txn FILE KEY              print one value of the switch record (nothing if absent)
  rn-engine.py backup-state --home DIR --to DEST   copy DIR to DEST (mode 700) with a consistent network.db
  rn-engine.py build --bin PATH                    "<version> <sha256>" if PATH is a pinned Linux build, else exit 1
  rn-engine.py cloud-bin --base BOX                the cloud engine to run: BOX/bin/engine/reason-network after
                                                   checking its bytes (exit 1 if it is not a pinned build), and the
                                                   launcher at BOX/bin/reason-network is put back if it was replaced;
                                                   BOX/bin/reason-network while the engine has not been moved yet
  rn-engine.py launcher --path PATH                write the cloud launcher to PATH unless it is already there
  rn-engine.py config-off --path FILE KEY...       set each KEY (auto_update, lan, tray) to false at the top level
                                                   of the engine config FILE: replaced in place, or added at the start
  rn-engine.py strays --bin-dir DIR --home DIR     engine daemons running a binary under DIR other than the daemon
                                                   of DIR's state (report only)

A daemon holds an exclusive lock on daemon.lock and writes its pid there; the file keeps the last pid after the
daemon exits, so the lock decides, as in the engine itself. A daemon runs the new binary only if it started after
the binary was replaced (inode change time); on Linux its program must also not be a deleted file. Start times on
macOS have one-second resolution, so a daemon counts as started after T when its start second is not before T's.
switch never restores the state directory: the engine only adds tables and columns, the old binary opens the newer
database, and mail received after the switch stays. Exit: 0 done, 1 failed and rolled back (or nothing changed),
3 failed and the old binary could not be confirmed running (manual steps are printed).
"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
STOP_TIMEOUT = float(os.environ.get("RN_ENGINE_STOP_TIMEOUT", 30))
START_TIMEOUT = float(os.environ.get("RN_ENGINE_START_TIMEOUT", 15))
PINNED_BUILDS = {
    "0.12.17": {
        "x86_64-unknown-linux-musl": "b17efa53714f9991e77b592cb62bc09fdf7991e1f612a785ea22ed04ab653f0b",
        "aarch64-unknown-linux-musl": "be0dd5698bf252acc62acb1c1503b9136ef7d6972d0ac603f54772ccd5620e6a",
    },
}
LAUNCHER = """#!/bin/sh
here=$(dirname "$0")
refuse() {
  echo "reason-network: $1. On this cloud computer Reason Network runs through /workspace/agent-mesh/box/rn-cloud.sh; to run a command by hand, set REASON_NETWORK_HOME to the state directory." >&2
  exit 2
}
[ -n "${REASON_NETWORK_HOME:-}${TINCAN_HOME:-}" ] || refuse "no state directory is set"
case "${1:-}" in
  who|send|status|team|doctor|stop|listen|leave|relay|invite|link|endpoint|held|approve|reject|remove|proof|daemon|help|--help|-h|--version|-V) ;;
  join|init)
    case " $* " in
      *" --no-setup "*) ;;
      *) refuse "$1 without --no-setup would wire agents or install a service" ;;
    esac ;;
  *) refuse "the command '${1:-}' is not available here" ;;
esac
REASON_NETWORK_NO_UPDATE=1
REASON_NETWORK_NO_PROMPT=1
REASON_NETWORK_MACHINE="${REASON_NETWORK_MACHINE:-cloud}"
export REASON_NETWORK_NO_UPDATE REASON_NETWORK_NO_PROMPT REASON_NETWORK_MACHINE
exec "$here/engine/reason-network" "$@"
"""


class Failed(Exception):
    pass


class Interrupted(BaseException):
    pass


def compare(installed, pinned):
    a, b = VERSION.fullmatch(installed or ""), VERSION.fullmatch(pinned or "")
    if not a or not b:
        return "unknown"
    x, y = tuple(map(int, a.groups())), tuple(map(int, b.groups()))
    return "same" if x == y else ("older" if x < y else "newer")


def daemon_pid(home):
    try:
        f = open(Path(home) / "daemon.lock", "rb")
    except OSError:
        return None
    with f:
        try:
            fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            text = f.read().decode("ascii", "replace").strip()
            return int(text) if text.isdigit() else None
        fcntl.flock(f, fcntl.LOCK_UN)
        return None


def started(pid):
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        try:
            ticks = int(stat.read_text().rsplit(")", 1)[1].split()[19])
            try:
                up = time.clock_gettime(time.CLOCK_BOOTTIME)
            except (AttributeError, OSError):
                up = float(Path("/proc/uptime").read_text().split()[0])
            return time.time() - (up - (ticks + 1) / os.sysconf("SC_CLK_TCK"))
        except (OSError, ValueError, IndexError):
            return None
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                             env=dict(os.environ, LC_ALL="C"), timeout=10).stdout.strip()
        return time.mktime(time.strptime(out, "%a %b %d %H:%M:%S %Y")) if out else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def replaced(binary):
    return os.stat(binary).st_ctime


def deleted_program(pid):
    try:
        return os.readlink(f"/proc/{pid}/exe").endswith(" (deleted)")
    except OSError:
        return False


def after(start, since):
    return start is not None and int(start) >= int(since)


def info(home, binary):
    pid = daemon_pid(home)
    when = replaced(binary)
    start = started(pid) if pid else None
    if pid is None:
        state = "none"
    else:
        state = "fresh" if after(start, when) and not deleted_program(pid) else "stale"
    return pid, start, when, state


def stamp(t):
    return "-" if t is None else f"{t:.0f}"


def iso(t):
    return "-" if t is None else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def say(text):
    print(f"engine: {text}", flush=True)


def default_signals():
    for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, signal.SIG_DFL)


def pinned_builds():
    path = os.environ.get("RN_PINNED_BUILDS_FILE")
    return json.loads(Path(path).read_text()) if path else PINNED_BUILDS


def linux_target():
    machine = os.uname().machine
    if machine == "x86_64":
        return "x86_64-unknown-linux-musl"
    if machine in ("aarch64", "arm64"):
        return "aarch64-unknown-linux-musl"
    return None


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pinned_build(path):
    if os.path.islink(path) or not os.path.isfile(path):
        return None, None
    digest = file_sha256(path)
    target = linux_target()
    for version, table in pinned_builds().items():
        if target and table.get(target) == digest:
            return version, digest
    return None, digest


def config_off(path, keys):
    path = Path(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        lines, mode = [], 0o600
    seen, out, top = set(), [], True
    for line in lines:
        if line.lstrip().startswith("["):
            top = False
        m = re.match(r"\s*([A-Za-z0-9_-]+)\s*=", line) if top else None
        if m and m.group(1) in keys:
            if m.group(1) not in seen:
                seen.add(m.group(1))
                out.append(f"{m.group(1)} = false")
            continue
        out.append(line)
    out = [f"{k} = false" for k in keys if k not in seen] + out
    tmp = path.with_name(f".{path.name}.new.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def put_launcher(path):
    path = Path(path)
    want = LAUNCHER.encode()
    try:
        if not path.is_symlink() and path.read_bytes() == want and os.access(path, os.X_OK):
            return False
    except OSError:
        pass
    tmp = path.with_name(f".{path.name}.launcher.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o755)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(want)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o755)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


def cloud_bin(base, warn=None):
    warn = warn or (lambda text: print(f"rn-engine: {text}", file=sys.stderr, flush=True))
    base = Path(base)
    payload, launcher = base / "bin/engine/reason-network", base / "bin/reason-network"
    if not os.path.lexists(payload):
        warn("the engine has not been moved to box/bin/engine yet; run ./update.sh on the Mac and do the cloud "
             "engine step")
        return str(launcher)
    version, digest = pinned_build(payload)
    if version is None:
        raise Failed(f"{payload} is not a Reason Network build pinned by agent-mesh (sha256 {digest or 'unreadable'}); "
                     "do not run it. Stop and tell the user (README: Troubleshooting)")
    try:
        if put_launcher(launcher):
            warn(f"{launcher} had been replaced (most likely by another engine daemon updating itself); "
                 "the launcher is back (README: Troubleshooting)")
    except OSError as error:
        warn(f"could not put the launcher back at {launcher}: {error}")
    return str(payload)


def strays(bin_dir, home, proc="/proc"):
    root = os.path.realpath(bin_dir).rstrip("/") + "/"
    own = daemon_pid(home)
    found = []
    try:
        entries = sorted((p for p in Path(proc).iterdir() if p.name.isdigit()), key=lambda p: int(p.name))
    except OSError:
        return found
    for p in entries:
        try:
            args = (p / "cmdline").read_bytes().split(b"\0")
            exe = os.readlink(p / "exe")
        except OSError:
            continue
        if exe.endswith(" (deleted)"):
            exe = exe[:-len(" (deleted)")]
        if len(args) < 2 or args[1] != b"daemon" or not exe.startswith(root) or int(p.name) == own:
            continue
        state = "unknown"
        try:
            env = dict(item.split(b"=", 1) for item in (p / "environ").read_bytes().split(b"\0") if b"=" in item)
            if env.get(b"REASON_NETWORK_HOME"):
                state = env[b"REASON_NETWORK_HOME"].decode(errors="replace")
            elif env.get(b"HOME"):
                state = env[b"HOME"].decode(errors="replace") + "/.reason/network"
        except OSError:
            pass
        found.append((int(p.name), exe, state))
    return found


def run(binary, home, *args, timeout=60):
    env = dict(os.environ, REASON_NETWORK_HOME=str(home), REASON_NETWORK_NO_UPDATE="1")
    try:
        return subprocess.run([str(binary), *args], capture_output=True, text=True, env=env, timeout=timeout,
                              stdin=subprocess.DEVNULL, preexec_fn=default_signals)
    except subprocess.TimeoutExpired:
        raise Failed(f"reason-network {' '.join(args)} did not finish within {timeout:.0f} s")
    except OSError as error:
        raise Failed(f"reason-network {' '.join(args)} could not run: {error}")


def version_of(binary):
    try:
        out = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    parts = out.split()
    return parts[1] if len(parts) >= 2 else ""


def machines(text):
    found = set()
    for line in (text or "").splitlines():
        if not line[:1].isspace():
            continue
        parts = line.replace(" (verified)", "").split()
        if len(parts) >= 2:
            found.add(parts[1])
    return found


def wait_exit(home, pid, timeout):
    deadline = time.time() + timeout
    while daemon_pid(home) == pid:
        if time.time() > deadline:
            return False
        time.sleep(0.2)
    return True


def end_daemon(home, pid):
    for sig, timeout in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
        if daemon_pid(home) != pid:
            return True
        say(f"the daemon (pid {pid}) is still running after reason-network stop; sending {sig.name}")
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        if wait_exit(home, pid, timeout):
            return True
    return False


def verify(home, since, old_pid, timeout):
    deadline = time.time() + timeout
    while True:
        pid = daemon_pid(home)
        start = started(pid) if pid else None
        problem = None
        if pid is None:
            problem = "no engine daemon is running"
        elif old_pid and pid == old_pid:
            problem = f"the daemon is still the old one (pid {pid})"
        elif not after(start, since):
            problem = f"the running daemon (pid {pid}) started before the binary was replaced"
        elif deleted_program(pid):
            problem = f"the running daemon (pid {pid}) runs a binary that was replaced"
        if problem is None:
            return pid, start
        if time.time() > deadline:
            raise Failed(problem)
        time.sleep(0.2)


def restart_daemon(binary, home, since, lenient_stop=False):
    """Stops the running daemon, starts it again with `team` and checks it started after `since`."""
    old = daemon_pid(home)
    try:
        r = run(binary, home, "stop", timeout=STOP_TIMEOUT + 30)
        problem = None if r.returncode == 0 else (r.stderr or r.stdout).strip()[:300]
    except Failed as error:
        problem = str(error)
    if problem is not None:
        if not lenient_stop:
            raise Failed(f"reason-network stop failed: {problem}")
        say(f"reason-network stop did not work ({problem}); ending the daemon by signal")
    if old and not wait_exit(home, old, STOP_TIMEOUT) and not end_daemon(home, old):
        raise Failed(f"the daemon (pid {old}) did not exit within {STOP_TIMEOUT:.0f} s of reason-network stop, "
                     "nor after SIGTERM and SIGKILL")
    team = run(binary, home, "team")
    if team.returncode != 0:
        raise Failed(f"reason-network team failed: {(team.stderr or team.stdout).strip()[:300]}")
    pid, start = verify(home, since, old, START_TIMEOUT)
    return pid, start, team.stdout


def report(binary, pid, start, since):
    say(f"reason-network {version_of(binary)} running (daemon pid {pid}, started {iso(start)}, "
        f"binary replaced {iso(since)})")


def put_binary(source, binary):
    binary = Path(binary)
    binary.parent.mkdir(parents=True, exist_ok=True)
    tmp = binary.with_name(f".{binary.name}.new.{os.getpid()}")
    try:
        shutil.copyfile(source, tmp)
        os.chmod(tmp, 0o755)
        os.replace(tmp, binary)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return replaced(binary)


def save_txn(path, data):
    if not path:
        return
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def load_txn(path):
    try:
        data = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise SystemExit(f"rn-engine: the switch record {path} is unreadable ({error}); restore by hand from the "
                         "engine-backup-* directory next to it")
    if not isinstance(data, dict) or not all(isinstance(data.get(k), str) for k in ("stage", "bin", "home", "backup")):
        raise SystemExit(f"rn-engine: the switch record {path} is damaged; restore by hand from the engine-backup-* "
                         "directory next to it")
    return data


def manual_steps(data):
    q = shlex.quote
    h, backup = data["home"], data["backup"]
    b = data.get("old_bin") or data["bin"]
    pid = daemon_pid(h)
    holder = f"pid {pid}" if pid else f"the pid written in {q(h + '/daemon.lock')}"
    first = f"cp {q(backup + '/reason-network')} {q(b + '.restore')} && mv -f {q(b + '.restore')} {q(b)}"
    if b != data["bin"]:
        first += f" && rm -f {q(data['bin'])}"
    print(f"""engine: the previous engine could not be confirmed running. Restore it by hand:
  1. {first}
  2. REASON_NETWORK_HOME={q(h)} REASON_NETWORK_NO_UPDATE=1 {q(b)} stop
     if a daemon still runs ({holder}): kill it, and kill -9 it if it does not exit
  3. REASON_NETWORK_HOME={q(h)} REASON_NETWORK_NO_UPDATE=1 {q(b)} team
  4. only if it still does not start: end the daemon as in step 2, move {q(h)} aside and copy
     {q(backup + '/state')} to {q(h)} (mode 700). That loses mail received after the switch.
  The backup stays in {q(backup)}.""", flush=True)


def guarded(fn):
    def handler(signum, _frame):
        raise Interrupted(signum)
    old = {s: signal.signal(s, handler) for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
    try:
        return fn()
    finally:
        for s, h in old.items():
            signal.signal(s, h)


def quiet():
    for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, lambda *_: None)


def untrusted_failed(data, txn):
    quiet()
    q = shlex.quote
    b, h = data["bin"], data["home"]
    try:
        installed = pinned_build(b)[0] == data.get("pinned")
    except OSError:
        installed = False
    data["stage"] = "failed_untrusted"
    if not installed:
        save_txn(txn, data)
        print(f"""engine: the pinned build could not be installed at {b}; nothing that starts the engine was changed.
  The current engine is not a pinned build, so do not run it. Fix the problem shown above, then run this upgrade
  again (a copy of the replaced binary is in {q(data['backup'])}).""", flush=True)
        return
    if data.get("old_bin") and data["old_bin"] != b:
        try:
            put_launcher(data["old_bin"])
        except OSError as error:
            print(f"engine: could not write the launcher to {data['old_bin']}: {error}", flush=True)
    save_txn(txn, data)
    print(f"""engine: the engine is now the checked pinned build at {b}, but its daemon was not confirmed running.
  The replaced binary was not a pinned build, so it is not put back (a copy is in {q(data['backup'])}).
  1. REASON_NETWORK_HOME={q(h)} REASON_NETWORK_NO_UPDATE=1 {q(b)} stop
     if a daemon still runs: kill it, and kill -9 it if it does not exit
  2. REASON_NETWORK_HOME={q(h)} REASON_NETWORK_NO_UPDATE=1 {q(b)} team
  3. then run this upgrade again; if the daemon still does not start, tell the user""", flush=True)


def undo(data, txn):
    """Puts the backed-up binary back and checks the daemon runs it. Returns True when confirmed."""
    quiet()
    b, h = data.get("old_bin") or data["bin"], data["home"]
    say(f"putting Reason Network {data.get('old_version') or 'from the backup'} back")
    try:
        since = put_binary(Path(data["backup"]) / "reason-network", b)
        if b != data["bin"] and os.path.lexists(data["bin"]):
            os.unlink(data["bin"])
        pid, start, _ = restart_daemon(b, h, since)
    except (Failed, OSError) as error:
        print(f"engine: rollback failed: {error}", flush=True)
        manual_steps(data)
        return False
    data["stage"] = "rolled_back"
    save_txn(txn, data)
    report(b, pid, start, since)
    say(f"rolled back; the state directory was not restored (backup kept in {data['backup']})")
    return True


def cmd_switch(a):
    binary, home = Path(a.bin), Path(a.home)
    old = Path(a.old_bin) if a.old_bin else binary
    backup = Path(a.backup)
    data = {"stage": "backup", "bin": str(binary), "home": str(home), "backup": str(backup),
            "old_version": "" if a.untrusted_old else version_of(old), "pinned": a.pinned,
            "old_pid": daemon_pid(home), "machines": [], "extra": dict(kv.split("=", 1) for kv in a.keep)}
    if a.old_bin:
        data["old_bin"] = str(old)
    if a.untrusted_old:
        data["untrusted_old"] = True

    def prepare():
        if version_of(a.new) != a.pinned:
            raise Failed(f"the new binary {a.new} does not report Reason Network {a.pinned}")
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.mkdir(mode=0o700)
        os.chmod(backup, 0o700)
        shutil.copy2(old, backup / "reason-network")
        backup_state(home, backup / "state")
        if data["old_pid"]:
            try:
                team = run(a.new if a.untrusted_old else old, home, "team")
            except Failed:
                team = None
            if team is not None and team.returncode == 0:
                data["machines"] = sorted(machines(team.stdout))
        save_txn(a.txn, data)
        say(f"backed up the binary and {home} to {backup}")

    try:
        guarded(prepare)
    except (Failed, OSError) as error:
        print(f"engine: {error}; nothing was switched", flush=True)
        return 1
    except Interrupted:
        print("engine: interrupted before the switch; nothing was switched", flush=True)
        return 1

    def switch():
        data["stage"] = "replacing"
        save_txn(a.txn, data)
        data["since"] = put_binary(a.new, binary)
        data["stage"] = "replaced"
        save_txn(a.txn, data)
        say(f"replaced {binary}; restarting the daemon")
        pid, start, team = restart_daemon(binary, home, data["since"], lenient_stop=a.untrusted_old)
        have = version_of(binary)
        if have != a.pinned:
            raise Failed(f"{binary} reports {have or 'no version'}, not {a.pinned}")
        missing = sorted(set(data["machines"]) - machines(team))
        if missing:
            raise Failed(f"reason-network team no longer lists machine(s) {', '.join(missing)}")
        who = run(binary, home, "who")
        if who.returncode != 0:
            raise Failed(f"reason-network who failed: {(who.stderr or who.stdout).strip()[:300]}")
        if a.old_bin and old != binary:
            put_launcher(old)
            say(f"{old} is now the launcher")
        data["stage"] = "verified"
        save_txn(a.txn, data)
        report(binary, pid, start, data["since"])

    try:
        guarded(switch)
        return 0
    except (Failed, OSError) as error:
        print(f"engine: {error}", flush=True)
    except Interrupted:
        print("engine: interrupted", flush=True)
    if data["stage"] == "backup":
        return 1
    if a.untrusted_old:
        untrusted_failed(data, a.txn)
        return 1
    return 1 if undo(data, a.txn) else 3


def cmd_restart(a):
    try:
        since = replaced(a.bin)
        pid, start, _ = guarded(lambda: restart_daemon(a.bin, a.home, since))
    except (Failed, OSError) as error:
        print(f"engine: restart failed: {error}", flush=True)
        return 1
    except Interrupted:
        print("engine: interrupted", flush=True)
        return 1
    report(a.bin, pid, start, since)
    return 0


def cmd_status(a):
    pid, start, when, state = info(a.home, a.bin)
    if pid is None:
        say(f"reason-network {version_of(a.bin)} installed, no daemon running")
        return 1
    report(a.bin, pid, start, when)
    if state != "fresh":
        say("the running daemon started before the binary was replaced (stale)")
        return 1
    return 0


def registration(home, marker, name, relay_pid, grace, now=None):
    """ok | wait | fail, with a reason: did the relay register on the running daemon (marker newer than its start)?"""
    now = time.time() if now is None else now
    try:
        st = os.stat(marker)
        data = json.loads(Path(marker).read_text())
    except FileNotFoundError:
        data, st = None, None
    except (OSError, ValueError) as error:
        return "fail", f"{marker} is unreadable ({error})"
    if data is not None and not isinstance(data, dict):
        return "fail", f"{marker} is damaged"
    pid = daemon_pid(home)
    if pid is None:
        return "fail", "no engine daemon is running, so the relay is not listening"
    if data is not None and data.get("name") != name:
        return "fail", f"the relay registered as {data.get('name')}, not {name}"
    dstart = started(pid)
    rstart = started(relay_pid) if relay_pid else None
    fresh = (data is not None and dstart is not None and int(st.st_mtime) >= int(dstart)
             and (not relay_pid or data.get("pid") == relay_pid))
    if fresh:
        return "ok", f"registered as {name} since the daemon (pid {pid}) started"
    since = max([t for t in (dstart, rstart) if t is not None], default=None)
    if since is not None and now - since < grace:
        return "wait", f"the engine daemon or the relay restarted {now - since:.0f} s ago; the relay registers again within {grace:.0f} s"
    if data is None:
        return "fail", "the relay has not reported a registration (no registered marker)"
    return "fail", "the relay has not registered on the running engine daemon since it started"


def cmd_registered(a):
    state, reason = registration(a.home, a.marker, a.name, a.relay_pid, a.grace)
    print(f"{state} {reason}")
    return 0


def cmd_rollback(a):
    data = load_txn(a.txn)
    if data is None:
        return 0
    if data["stage"] in ("backup", "rolled_back", "failed_untrusted"):
        return 0
    if data.get("untrusted_old"):
        if data["stage"] == "verified":
            say("the replaced binary was not a pinned build, so it is not put back")
            return 0
        untrusted_failed(data, a.txn)
        return 3
    return 0 if undo(data, a.txn) else 3


def cmd_txn_get(a):
    data = load_txn(a.txn)
    if data is None:
        return 0
    value = data.get(a.key, data.get("extra", {}).get(a.key))
    if value is not None:
        print(value)
    return 0


def cmd_others(a):
    names = {a.bin, os.path.realpath(a.bin)}
    out = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True, timeout=10).stdout
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid, ppid, cmd = parts
        program = next((n for n in names if cmd == n or cmd.startswith(n + " ")), None)
        if program is None:
            continue
        words = cmd[len(program):].split()
        if words and words[0] in ("daemon", "tray"):
            continue
        if a.skip_parent and ppid == str(a.skip_parent):
            continue
        print(f"{pid} {cmd}")
    return 0


def cmd_build(a):
    version, digest = pinned_build(a.bin)
    if version is None:
        return 1
    print(f"{version} {digest}")
    return 0


def cmd_cloud_bin(a):
    try:
        print(cloud_bin(a.base))
    except Failed as error:
        print(f"rn-engine: {error}", file=sys.stderr, flush=True)
        return 1
    return 0


def cmd_launcher(a):
    if put_launcher(a.path):
        say(f"wrote the launcher to {a.path}")
    say("launcher ok")
    return 0


def cmd_strays(a):
    for pid, exe, state in strays(a.bin_dir, a.home, a.proc):
        print(f"pid {pid} {exe} state {state}")
    return 0


def backup_state(src, dest):
    src, dest = Path(src), Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(dest.parent, 0o700)
    shutil.copytree(src, dest, symlinks=True,
                    ignore=shutil.ignore_patterns("*.sock", "daemon.lock", "network.db", "network.db-*"))
    os.chmod(dest, 0o700)
    db = src / "network.db"
    if db.exists():
        con = sqlite3.connect(f"{db.absolute().as_uri()}?mode=ro", uri=True, timeout=30)
        out = sqlite3.connect(dest / "network.db")
        try:
            con.backup(out)
        finally:
            out.close()
            con.close()
        os.chmod(dest / "network.db", 0o600)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("compare")
    s.add_argument("installed")
    s.add_argument("pinned")
    s.set_defaults(run=lambda a: print(compare(a.installed, a.pinned)))
    s = sub.add_parser("pid")
    s.add_argument("--home", required=True)
    s.set_defaults(run=lambda a: print(daemon_pid(a.home) or ""))
    s = sub.add_parser("info")
    s.add_argument("--home", required=True)
    s.add_argument("--bin", required=True)
    s.set_defaults(run=lambda a: print("pid={} started={} replaced={} state={}".format(
        *(lambda i: (i[0] or "-", stamp(i[1]), stamp(i[2]), i[3]))(info(a.home, a.bin)))))
    s = sub.add_parser("status")
    s.add_argument("--home", required=True)
    s.add_argument("--bin", required=True)
    s.set_defaults(run=cmd_status)
    s = sub.add_parser("registered")
    s.add_argument("--home", required=True)
    s.add_argument("--marker", required=True)
    s.add_argument("--name", required=True)
    s.add_argument("--relay-pid", type=int)
    s.add_argument("--grace", type=float, default=660)
    s.set_defaults(run=cmd_registered)
    s = sub.add_parser("others")
    s.add_argument("--bin", required=True)
    s.add_argument("--skip-parent", type=int)
    s.set_defaults(run=cmd_others)
    s = sub.add_parser("switch")
    for flag in ("--home", "--bin", "--new", "--pinned", "--backup"):
        s.add_argument(flag, required=True)
    s.add_argument("--txn")
    s.add_argument("--keep", action="append", default=[])
    s.add_argument("--old-bin")
    s.add_argument("--untrusted-old", action="store_true")
    s.set_defaults(run=cmd_switch)
    s = sub.add_parser("restart")
    s.add_argument("--home", required=True)
    s.add_argument("--bin", required=True)
    s.set_defaults(run=cmd_restart)
    s = sub.add_parser("rollback")
    s.add_argument("--txn", required=True)
    s.set_defaults(run=cmd_rollback)
    s = sub.add_parser("txn-get")
    s.add_argument("--txn", required=True)
    s.add_argument("key")
    s.set_defaults(run=cmd_txn_get)
    s = sub.add_parser("build")
    s.add_argument("--bin", required=True)
    s.set_defaults(run=cmd_build)
    s = sub.add_parser("cloud-bin")
    s.add_argument("--base", required=True)
    s.set_defaults(run=cmd_cloud_bin)
    s = sub.add_parser("launcher")
    s.add_argument("--path", required=True)
    s.set_defaults(run=cmd_launcher)
    s = sub.add_parser("config-off")
    s.add_argument("--path", required=True)
    s.add_argument("keys", nargs="+", choices=("auto_update", "lan", "tray"))
    s.set_defaults(run=lambda a: config_off(a.path, a.keys) or 0)
    s = sub.add_parser("strays")
    s.add_argument("--bin-dir", required=True)
    s.add_argument("--home", required=True)
    s.add_argument("--proc", default="/proc")
    s.set_defaults(run=cmd_strays)
    s = sub.add_parser("backup-state")
    s.add_argument("--home", required=True)
    s.add_argument("--to", required=True)
    s.set_defaults(run=lambda a: backup_state(a.home, a.to))
    a = p.parse_args(argv)
    try:
        sys.exit(a.run(a) or 0)
    except OSError as error:
        sys.exit(f"rn-engine: {error}")


if __name__ == "__main__":
    main()
