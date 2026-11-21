import hashlib, http.server, json, os, random, tempfile, threading, time
from pathlib import Path


tmp = tempfile.mkdtemp()
os.environ["AGENT_RELAY_STATE"] = tmp
import agent_relay as rr  # noqa: E402

REAL_WEBHOOK_OPEN = rr.webhook_open

KEY = "0123456789abcdef" * 4          # obviously fake test key (64 hex chars, like `openssl rand -hex 32`)
CFG = {"RN_BIN": "/nonexistent", "PERSON": "demo", "LISTEN_NAME": "bot", "CLOUD_NAME": "bot-cloud",
       "INBOX_NAME": "bot-inbox", "CLOUD_MACHINE": "cloud", "DOORBELL_PREVIEW_CHARS": 200,
       "WEBHOOK_URL": "http://127.0.0.1:9/", "WEBHOOK_KEY": "x", "OUTBOUND_KEY": KEY, "MAX_WAKES_PER_HOUR": 20,
       "MIN_SECONDS_BETWEEN_WAKES": 20, "MAX_BATCH": 10, "MAX_PROXY_PER_HOUR": 60,
       "MAX_SPOOL": 50, "WEBHOOK_MAX_BODY_CHARS": 3500, "FORMER_POLL_SECONDS": 60,
       "RN_DB": str(Path(tempfile.mkdtemp()) / "missing-network.db")}   # never the real engine database
LIMIT = CFG["WEBHOOK_MAX_BODY_CHARS"]

FWD_VECTOR_SIG = "a48eb3bbb089a3d6b31fed070d76063bb3b3ddabfac08b7651f4093ce358c576"


def clean_state():
    import shutil
    for f in Path(tmp).glob("*"):
        shutil.rmtree(f) if f.is_dir() else f.unlink()


class FakeResp:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): return False


def sha(t):
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def samples():
    rnd = random.Random(7)
    zh = "".join(rnd.choice("的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动") for _ in range(9000))
    ascii_ = "".join(rnd.choice("abcdefghijklmnopqrstuvwxyz0123456789 .,") for _ in range(20000))
    nasty = "".join(rnd.choice(['"', "\\", "\n", "\t", "\r", "\x01", "\u2028", "a", "中", "😀", "</x>"]) for _ in range(8000))
    lines = "\n".join(f"line {i}: \"quoted\" 路径 C:\\tmp\\{i}" for i in range(600))
    return {"zh": zh, "ascii": ascii_, "nasty": nasty, "lines": lines}


def envelope(to, reply_to, body, ts=None, nonce="0123456789abcdef01234567", key=KEY):
    ts = str(int(time.time())) if ts is None else str(ts)
    sig = rr.sign(key, to, reply_to, ts, nonce, body)
    return f"RN-OUT v1\nto: {to}\nreply_to: {reply_to}\nts: {ts}\nnonce: {nonce}\nsig: {sig}\n---\n{body}"


class FakeRun:
    returncode, stdout, stderr = 0, "→ demo/claude-session: queued\nmessage id 01JX\n", ""


class RunOK:
    returncode, stderr = 0, ""
    stdout = "→ demo/bot-inbox: queued: not connected to cloud right now; it goes out as soon as it is\nmessage id 01JX\n"


class RunOut:
    def __init__(self, rc, out): self.returncode, self.stdout, self.stderr = rc, out, ""


def parse_fwd(body):
    """Independent re-implementation of the box-side parser (box/rn-fetch.py) for cross-checking."""
    assert body.endswith(rr.FWD_TRAILER)
    head, _, rest = body.partition("\n---\n")
    text = rest[: -len(rr.FWD_TRAILER)]
    lines = head.split("\n")
    assert lines[0] == "RN-FWD v1"
    f = dict(l.split(": ", 1) if ": " in l else (l.rstrip(":"), "") for l in lines[1:])
    return f, text


class DoorbellFixture:
    def setUp(self):
        self.addCleanup(setattr, rr, "webhook_open", REAL_WEBHOOK_OPEN)
        clean_state()
        self.r = rr.Relay(dict(CFG))
        self.calls, self.posts, self.result = [], [], RunOK()
        self.r.rn = lambda *a, **k: (self.calls.append((a, k)), self.result)[1]
        rr.webhook_open = lambda req, timeout=None: (self.posts.append(req.data.decode()), FakeResp())[1]

    def m(self, text, i="m1", frm="demo/other", agent="codex", thread="01JTHREAD000000000000000000"):
        return {"id": i, "from": frm, "agent": agent, "thread": thread, "text": text}

    def drain(self):
        while self.r.s.spool:
            self.r.s.wakes = []
            self.r.flush()
        return [json.loads(b) for b in self.posts]

    def servers(self, code):
        """A answers every POST with `code` and a Location pointing at B; B counts whatever reaches it."""
        hits = {"a": [], "b": []}
        holder = {}
        class A(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                hits["a"].append(("POST", self.headers))
                self.send_response(code)
                self.send_header("Location", holder["b"])
                self.send_header("Content-Length", "0")
                self.end_headers()
            def log_message(self, *a):
                pass
        class B(http.server.BaseHTTPRequestHandler):
            def hit(self):
                n = int(self.headers.get("Content-Length") or 0)
                if n:
                    self.rfile.read(n)
                hits["b"].append((self.command, self.headers))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
            do_GET = do_POST = do_HEAD = hit
            def log_message(self, *a):
                pass
        out = []
        for handler in (A, B):
            srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
            self.addCleanup(srv.server_close)
            self.addCleanup(srv.shutdown)
            out.append(srv)
        holder["b"] = f"http://127.0.0.1:{out[1].server_port}/b-landing-xyz"
        return f"http://127.0.0.1:{out[0].server_port}/hook", holder["b"], hits


class Crash(BaseException):
    """Stands in for the process dying: not an Exception, so nothing in the relay catches it."""


# The engine's messages table (the columns the relay reads).
SCHEMA = """CREATE TABLE messages (id TEXT PRIMARY KEY, sender TEXT NOT NULL, sender_agent TEXT, sender_repo TEXT,
recipient TEXT NOT NULL, via TEXT, reply_to TEXT, body TEXT NOT NULL, wake INTEGER NOT NULL, sent_at INTEGER NOT NULL,
read_at INTEGER, held INTEGER NOT NULL DEFAULT 0);"""

# State exactly as released in v0.1.1 (before seen_t and recv_floor), to check that a downgrade still works.
PREV_STATE = r'''
class State:
    def __init__(self, max_spool=1000):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(STATE_DIR, 0o700)
        self.path = STATE_DIR / "state.json"
        try:
            d = json.loads(self.path.read_text())
        except Exception:
            d = {}
        self.seen = d.get("seen", [])            # message ids already handled (dedupe)
        self.nonces = d.get("nonces", {})        # envelope nonce -> ts (replay guard)
        self.wakes = d.get("wakes", [])          # timestamps of webhook POSTs
        self.proxies = d.get("proxies", [])      # timestamps of proxied sends
        self.spool = d.get("spool", [])          # items waiting to be POSTed
        self.overflow_dropped = d.get("overflow_dropped", 0)   # dropped since the overflow notice last went out
        self.refwds = d.get("refwds", {})        # message id -> [timestamps of honoured RN-REFWD]
        self.max_spool = max_spool

    def trim_spool(self):
        """Never drop silently: past MAX_SPOOL the oldest items go, get logged, and one relay notice says so."""
        cap = max(10, self.max_spool)
        if len(self.spool) <= cap:
            return
        notice = next((i for i in self.spool if i.get("id") == "relay-spool-overflow"), None)
        rest = [i for i in self.spool if i is not notice]
        drop = len(rest) - (cap - 1)
        dropped, rest = rest[:drop], rest[drop:]
        self.overflow_dropped += len(dropped)
        uniq = list(dict.fromkeys(str(i.get("id")) for i in dropped))
        ids = ", ".join(uniq[:20]) + (" ..." if len(uniq) > 20 else "")
        logging.getLogger("agent-relay").warning(
            "spool over %d: dropped %d oldest item(s) (%d since last delivery): %s",
            cap, len(dropped), self.overflow_dropped, ids)
        notice = notice_item("relay-spool-overflow",
                             f"relay spool was full ({cap} items, webhook down or rate-limited for long): "
                             f"dropped {self.overflow_dropped} oldest item(s) so far. Last dropped ids: {ids}. "
                             f"Ask the senders to resend if needed; see the relay log on the Mac.")
        self.spool = [notice] + rest   # the notice goes out first, in the next webhook batch

    def save(self):
        now = time.time()
        self.seen = self.seen[-2000:]
        self.nonces = {n: t for n, t in self.nonces.items() if now - t < 3600}
        self.wakes = [t for t in self.wakes if now - t < 3600]
        self.proxies = [t for t in self.proxies if now - t < 3600]
        self.refwds = {k: v for k, v in self.refwds.items() if v and now - max(v) < 8 * 86400}
        self.trim_spool()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.__dict__ | {"path": None, "max_spool": None}, ensure_ascii=False))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
'''


def prev_state_class():
    ns = {"STATE_DIR": rr.STATE_DIR, "json": json, "os": os, "time": time, "logging": rr.logging,
          "notice_item": rr.notice_item}
    exec(PREV_STATE, ns)
    return ns["State"]


def refwd_req(mid, ts=None, nonce=None, key=KEY):
    ts = str(int(time.time())) if ts is None else str(ts)
    nonce = nonce or os.urandom(12).hex()
    return f"RN-REFWD v1\nid: {mid}\nts: {ts}\nnonce: {nonce}\nsig: {rr.sign_refwd(key, mid, ts, nonce)}"
