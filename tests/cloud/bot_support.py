import hashlib, json, os, shutil, signal, sqlite3, subprocess, sys, tempfile, time
from pathlib import Path

from tests.cloud.support import HERE, KEY, SCHEMA


SESSIONS = """CREATE TABLE sessions (name TEXT PRIMARY KEY, key TEXT NOT NULL, agent TEXT, cwd TEXT, repo TEXT,
branch TEXT, doing TEXT, tier TEXT, last_seen INTEGER NOT NULL);"""
TEAM_ID = "team-0001"
TEAM_HASH = hashlib.sha256(TEAM_ID.encode()).hexdigest()[:16]
MAINT_STOP = "STOP: other cloud commands are still running; nothing was changed"

FAKE_ENGINE = r'''#!/usr/bin/env python3
import json, os, sqlite3, sys, time
args = sys.argv[1:]
try:
    os.fstat(9)
    fd9 = True
except OSError:
    fd9 = False
with open(os.environ["FAKE_LOG"], "a") as fh:
    fh.write(json.dumps({"args": args, "fd9": fd9, "mode": os.environ.get("RN_MAINT_MODE"),
                         "inbox": os.environ.get("RN_INBOX_DIR"), "threads": os.environ.get("RN_THREADS_DIR")}) + "\n")
if args[:1] == ["doctor"]:
    print("peers: 0 connected" if os.environ.get("FAKE_OFFLINE") else "peers: 1 connected")
elif args[:2] == ["team", "--json"]:
    team = os.environ.get("FAKE_TEAM", "")
    if team == "none":
        print("error: not in a team", file=sys.stderr)
        sys.exit(1)
    print(team or json.dumps({"team_id": "team-0001", "person": "demo", "machine": "cloud", "members": []}))
elif args[:1] == ["listen"]:
    who = args[args.index("--as") + 1]
    if who in os.environ.get("FAKE_DIE_ON", "").split(","):
        print("error: interrupted", file=sys.stderr)
        sys.exit(1)
    name = json.loads(os.environ.get("FAKE_REGISTER", "{}")).get(who, who)
    db = sqlite3.connect(os.path.join(os.environ["REASON_NETWORK_HOME"], "network.db"))
    db.execute("INSERT OR IGNORE INTO sessions (name, key, last_seen) VALUES (?, ?, ?)",
               (name, "cli:" + who, int(time.time() * 1000)))
    db.commit()
    db.close()
    print(f"No messages. You are demo/{name}.")
elif args[:1] == ["who"]:
    print("demo/mac  online")
'''


def pin_file(path, binary, version="0.12.17"):
    target = {"x86_64": "x86_64-unknown-linux-musl"}.get(os.uname().machine, "aarch64-unknown-linux-musl")
    path.write_text(json.dumps({version: {target: hashlib.sha256(Path(binary).read_bytes()).hexdigest()}}))


class BotBox:
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        self.procs = []
        self.addCleanup(self.stop_procs)
        self.root = self.d / "agent-mesh"
        self.box = self.root / "box"
        (self.box / "state").mkdir(parents=True)
        (self.box / "person").write_text("demo\n")
        (self.box / "outbound.key").write_text(KEY + "\n")
        for name in ("rn-engine.py", "rn-fetch.py", "rn-cloud.sh", "rn-cloud-setup.sh"):
            (self.box / name).symlink_to(HERE / name)
        self.engine = self.box / "bin" / "engine" / "reason-network"
        self.engine.parent.mkdir(parents=True)
        self.engine.write_text(FAKE_ENGINE.replace("#!/usr/bin/env python3", "#!" + sys.executable, 1))
        self.engine.chmod(0o755)
        self.builds = self.d / "builds.json"
        pin_file(self.builds, self.engine)
        db = sqlite3.connect(self.box / "state" / "network.db")
        db.executescript(SCHEMA + SESSIONS + "CREATE TABLE outbox (id TEXT);")
        db.close()
        self.tools = self.d / "tools"
        self.tools.mkdir()
        for name, script in (("uname", 'if [ "${1:-}" = -m ]; then echo x86_64; else echo Linux; fi'),
                             ("sleep", "exit 0"), ("curl", "exit 9"), ("tar", "exit 9"), ("sha256sum", "exit 9")):
            (self.tools / name).write_text("#!/bin/sh\n" + script + "\n")
            (self.tools / name).chmod(0o755)
        (self.tools / "python3").symlink_to(sys.executable)
        self.log = self.d / "rn.log"
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("RN_", "REASON_NETWORK_", "FAKE_")) and k not in ("AGENT_RELAY_NAME",
                                                                                      "MACHINE_NAME")}
        self.env.update(PATH=f"{self.tools}{os.pathsep}{os.environ['PATH']}", RN_CLOUD_BASE=str(self.box),
                        FAKE_LOG=str(self.log), RN_PINNED_BUILDS_FILE=str(self.builds))
        self.lock = self.root / ".maint.lock"

    def stop_procs(self):
        for p in self.procs:
            if p.poll() is None:
                try:
                    os.killpg(p.pid, signal.SIGKILL)
                except OSError:
                    pass
                p.wait()

    def cloud(self, *args, env=None, body=""):
        return subprocess.run(["bash", str(self.box / "rn-cloud.sh"), *args], env=dict(self.env, **(env or {})),
                              input=body, capture_output=True, text=True, timeout=120)

    def add_bot(self, *args, env=None):
        return subprocess.run(["bash", str(HERE / "rn-cloud-add-bot.sh"), *args], env=dict(self.env, **(env or {})),
                              capture_output=True, text=True, timeout=120)

    def calls(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def listened(self):
        return [c["args"][c["args"].index("--as") + 1] for c in self.calls() if c["args"][:1] == ["listen"]]

    def sessions(self):
        db = sqlite3.connect(self.box / "state" / "network.db")
        try:
            return sorted(tuple(r) for r in db.execute("SELECT name, key FROM sessions"))
        finally:
            db.close()

    def add_session(self, name, key=None):
        db = sqlite3.connect(self.box / "state" / "network.db")
        db.execute("INSERT INTO sessions (name, key, last_seen) VALUES (?, ?, 1)", (name, key or "cli:" + name))
        db.commit()
        db.close()

    def claim(self, name, team_hash=TEAM_HASH, registered=False):
        d = self.root / "bots" / name
        (d / "inbox").mkdir(parents=True)
        (d / "threads").mkdir()
        (d / ".claim").write_text(f"team {team_hash}\ntime 2026-11-15T00:00:00Z\n")
        if registered:
            (d / ".registered").write_text("2026-11-15T00:00:00Z\n")
            self.add_session(f"{name}-cloud")
            self.add_session(f"{name}-inbox")
        return d

    def tombstone(self, name):
        (self.box / "bots-removed").mkdir(exist_ok=True)
        (self.box / "bots-removed" / name).write_text("2026-11-15T00:00:00Z\n")

    def holder(self, mode, lockfile=None, seconds=30, ready=None):
        ready = ready or self.d / f"ready-{len(self.procs)}"
        code = f"import pathlib, time; pathlib.Path({str(ready)!r}).touch(); time.sleep({seconds})"
        env = {k: v for k, v in self.env.items() if not k.startswith("RN_MAINT_")}
        p = subprocess.Popen([sys.executable, str(HERE / "rn-engine.py"), "hold", f"--{mode}",
                              str(lockfile or self.lock), "--", sys.executable, "-c", code], env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        self.procs.append(p)
        end = time.monotonic() + 20
        while not ready.exists():
            if p.poll() is not None or time.monotonic() > end:
                raise AssertionError("the lock holder did not start")
            time.sleep(0.05)
        return p
