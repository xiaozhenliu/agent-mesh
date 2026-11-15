import importlib.util, json, os, sqlite3, subprocess, sys, tempfile, time
from pathlib import Path

from tests.helpers.paths import BOX


HERE = BOX
KEY = "0123456789abcdef" * 4                      # fake test key


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


os.environ.setdefault("AGENT_RELAY_STATE", tempfile.mkdtemp())
rr = load("agent_relay", HERE.parent / "agent_relay.py")
SCHEMA = """CREATE TABLE messages (id TEXT PRIMARY KEY, sender TEXT NOT NULL, sender_agent TEXT, sender_repo TEXT,
recipient TEXT NOT NULL, via TEXT, reply_to TEXT, body TEXT NOT NULL, wake INTEGER NOT NULL, sent_at INTEGER NOT NULL,
read_at INTEGER, held INTEGER NOT NULL DEFAULT 0);"""


class FetchFixture:
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "state").mkdir()
        (self.d / "key").write_text(KEY + "\n")
        self.db = sqlite3.connect(self.d / "state" / "network.db")
        self.db.executescript("PRAGMA journal_mode=WAL;" + SCHEMA)
        self.n = 0
        self.env = dict(os.environ, REASON_NETWORK_HOME=str(self.d / "state"), RN_PERSON="demo",
                        RN_INBOX_NAME="bot-inbox", RN_KEY_FILE=str(self.d / "key"), RN_INBOX_DIR=str(self.d / "inbox"),
                        RN_BOX=str(self.d / "box"))

    def put(self, body, to="demo/bot-inbox", read=False):
        self.n += 1
        body = body.strip()                                     # what the engine stores (src/daemon/mail.rs:83)
        self.db.execute("INSERT INTO messages (id, sender, recipient, body, wake, sent_at, read_at) VALUES (?,?,?,?,1,?,?)",
                        (f"01RN{self.n:022d}", "demo/terminal", to, body, 1000 + self.n, 5 if read else None))
        self.db.commit()

    def fwd(self, mid, text, key=KEY, frm="demo/other"):
        return rr.fwd_body(key, mid, frm, "codex", "01JTHREAD", text)

    def run_fetch(self, *args):
        p = subprocess.run([sys.executable, str(HERE / "rn-fetch.py"), *args], env=self.env, capture_output=True,
                           text=True, timeout=60)
        return p.returncode, [json.loads(l) for l in p.stdout.splitlines() if l.strip()]

    def row(self, rid, body, sent_ms):
        self.db.execute("INSERT INTO messages (id, sender, recipient, body, wake, sent_at) VALUES (?,?,?,?,1,?)",
                        (rid, "demo/terminal", "demo/bot-inbox", body.strip(), sent_ms))
        self.db.commit()

    def purge_fixture(self):
        import time
        now = int(time.time() * 1000)
        self.put(self.fwd("old1", "old secret"))                  # sent_at ~1970: older than any cutoff
        self.put(self.fwd("old2", "also old"))
        self.row("01RNRECENT", self.fwd("new1", "recent"), now)
        self.put(self.fwd("dup", "sent long ago"))                # original before the cutoff ...
        self.row("01RNDUPAGAIN", self.fwd("dup", "sent long ago"), now)   # ... its re-forward after it
        self.row("01RNFUTURE", self.fwd("fut", "clock skew"), now + 10 * 86400 * 1000)
        rc, res = self.run_fetch("scan")
        self.assertEqual(sorted(r["id"] for r in res), ["dup", "fut", "new1", "old1", "old2"])
        inbox, threads = self.d / "inbox", self.d / "threads"
        (inbox / "_refwd").mkdir()
        (inbox / "_refwd" / "old1.json").write_text("[1]")
        (inbox / "_stray").mkdir()
        (inbox / "_stray" / "bot-cloud.log").write_text("stray text")
        threads.mkdir()
        for name in ("demo_old.md", "demo_new.md"):
            (threads / name).write_text("notes")
        os.utime(threads / "demo_old.md", (1, 1))
        os.utime(inbox / "_stray" / "bot-cloud.log", (1, 1))
        self.env["RN_THREADS_DIR"] = str(threads)
        self.db.close()
        return inbox, threads

    def rows_left(self):
        db = sqlite3.connect(self.d / "state" / "network.db")
        try:
            return sorted(r[0] for r in db.execute("SELECT id FROM messages"))
        finally:
            db.close()


FAKE_RN = r'''#!/usr/bin/env python3
# fake reason-network for test_rn_fetch: records every send; on RN-REFWD acts like the Mac relay
import json, os, sqlite3, sys, time, importlib.util
args, body = sys.argv[1:], sys.stdin.read()
log = os.environ["FAKE_LOG"]
with open(log, "a") as fh:
    fh.write(json.dumps({"args": args, "body": body}) + "\n")
mode = os.environ.get("FAKE_MODE", "resend")
if args[:1] == ["send"] and body.startswith("RN-REFWD v1"):
    spec = importlib.util.spec_from_file_location("rr", os.environ["FAKE_RELAY"])
    rr = importlib.util.module_from_spec(spec); spec.loader.exec_module(rr)
    key = open(os.environ["RN_KEY_FILE"]).read().strip()
    f = rr.parse_refwd(body)
    assert rr.hmac.compare_digest(f["sig"], rr.sign_refwd(key, f["id"], f["ts"], f["nonce"]))
    db = sqlite3.connect(os.path.join(os.environ["REASON_NETWORK_HOME"], "network.db"))
    n = int(time.time() * 1000)
    def put(b, k):
        db.execute("INSERT INTO messages (id, sender, recipient, body, wake, sent_at) VALUES (?,?,?,?,1,?)",
                   (f"01FAKE{n}{k}", "demo/terminal", "demo/bot-inbox", b.strip(), n))
    if mode == "resend":
        put(rr.fwd_body(key, f["id"], "demo/other", "codex", "", "re-sent text " + f["id"]), "a")
    if mode != "silent":
        put(rr.refwd_ack_body(key, f["id"], "resent" if mode == "resend" else "no-copy", 1), "b")
    db.commit()
print(f"→ {args[1]}: queued" if args[:1] == ["send"] else "→ demo/bot: queued")
'''
