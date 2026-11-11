#!/usr/bin/env python3
"""agent-relay: the always-on Mac side of agent-mesh. Runs under launchd.

Names: <LISTEN_NAME>, <CLOUD_NAME> and <INBOX_NAME> come from the config (defaults bot, bot-cloud, bot-inbox;
          install.sh derives all three from --relay-name).
Inbound  (local agents -> Grok Bot): loops `reason-network listen --as <LISTEN_NAME>`. Each message for
          <team-name>/<LISTEN_NAME> is forwarded in full over Reason Network to
          <team-name>/<INBOX_NAME>@<CLOUD_MACHINE> as a signed RN-FWD v1 message
          (sent as <team-name>/terminal, which the engine's agent-loop guard never refuses). Then the routine
          webhook is rung with a small doorbell: {id, from, agent, thread, text_len, text_sha256, preview}.
          The routine fetches the full text by id with `rn-cloud.sh fetch` and verifies it.
Outbound (Grok Bot -> local agents): messages from <team-name>/<CLOUD_NAME> that carry a valid RN-OUT v1
          envelope (HMAC, 15-minute window) are re-sent here as <team-name>/<LISTEN_NAME>, so replies always land
          on this machine, never on the sleeping cloud box. Each envelope nonce gets a send record (sending, sent, failed, unknown)
          saved before the engine is called and kept NONCE_KEEP_SECONDS: a nonce already sent is dropped
          silently, one whose result is unknown is not sent again (one alert), a failed one may be sent again.
          Refused, failed and unknown sends are reported as durable alerts.
Re-forward: a copy of every forwarded text is kept for FWD_COPY_DAYS (dir 0700, files 0600). A signed
          RN-REFWD v1 from <CLOUD_NAME> or <INBOX_NAME> makes the relay send the copy again, at most REFWD_MAX_PER_ID
          per message and REFWD_MAX_PER_HOUR overall, answered with a signed RN-REFWD-ACK v1. It rings again
          only if the request does not carry "caps: got1" (a cloud that sends receipts is still waiting).
          If the first forward fails, the doorbell still rings (flagged forward_failed) and the cloud's fetch
          asks for the copy the same way.
Receipts: every forwarded message stays pending until the cloud reports it done (signed RN-GOT v1 from
          <CLOUD_NAME>, <INBOX_NAME> or a <team-name>/terminal session; older engines may number it, e.g. terminal-3,
          newer ones name it terminal@<CLOUD_MACHINE>) or a valid RN-OUT replies to it. Without a "stored"
          receipt GOT_TIMEOUT_SECONDS after its doorbell went out, the relay forwards the copy again and rings
          again (field rering); without "done" GOT_DONE_SECONDS after a stored message's doorbell, it rings again.
          After GOT_MAX_RERINGS re-rings it gives up with an alert. Alerts are kept outside the spool, go out in
          their own batch (relay-alerts-<ms>) and stay until the routine confirms that batch with a done receipt.
Notices: the relay's own messages to the routine (refused envelope, spool overflow, a message it could not
          handle, network.db not readable at startup) are doorbell:false items with the text inline. They never
          share a POST with doorbells.
Crash safety: `listen` marks a batch read as soon as it returns. Each message is handled on its own, so one
          failure never touches the rest of the batch (it is logged and reported in a notice). For inbound mail the
          copy is kept and the doorbell queued and saved BEFORE the text is forwarded: a relay killed after that
          still rings, and the cloud's fetch asks for the copy. At startup, and after a listen that timed out or
          failed, the relay reads the engine's network.db (read-only) for messages to itself that were marked read
          but never handled, from the later of its first start on this version and FWD_COPY_DAYS ago, and handles
          them. Messages the engine holds for inbound approval are left to listen.

Standard library only. Config: ~/.config/agent-relay/env (mode 0600). Never logs secrets or message bodies.
Message formats: README.md, appendix.
"""
import hashlib, hmac, html, json, logging, logging.handlers, os, re, sqlite3, subprocess, sys, time, urllib.request
from pathlib import Path

HOME = Path.home()
ENV_FILE = Path(os.environ.get("AGENT_RELAY_ENV", HOME / ".config/agent-relay/env"))
STATE_DIR = Path(os.environ.get("AGENT_RELAY_STATE", HOME / "Library/Application Support/agent-relay"))
LOG_FILE = Path(os.environ.get("AGENT_RELAY_LOG", HOME / "Library/Logs/agent-relay.log"))
RN_DB = HOME / ".reason/network/network.db"     # the engine's message store; RN_DB in the config is for tests only
RECONCILE_PAGE = 200                             # network.db rows read per query during recovery

NAME_SEG = r"[a-z0-9][a-z0-9-]*"
OUT_TO = re.compile(rf"{NAME_SEG}/{NAME_SEG}(?:@{NAME_SEG})?")
OUT_REPLY_TO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}")
BLOCK = re.compile(r'<reason-message ([^>]*)>\n(.*?)\n</reason-message>', re.S)
ATTR = re.compile(r'(\w+)="([^"]*)"')


def load_env():
    if not ENV_FILE.exists():
        sys.exit(f"missing {ENV_FILE}")
    if ENV_FILE.stat().st_mode & 0o077:
        sys.exit(f"{ENV_FILE} must be chmod 600")
    cfg = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    kf = cfg.get("OUTBOUND_KEY_FILE")
    if kf and not cfg.get("OUTBOUND_KEY"):
        kp = Path(os.path.expanduser(kf))
        if kp.exists():
            if kp.stat().st_mode & 0o077:
                sys.exit(f"{kp} must be chmod 600")
            cfg["OUTBOUND_KEY"] = kp.read_text().strip()
    need = ["RN_BIN", "PERSON", "WEBHOOK_URL", "WEBHOOK_KEY", "OUTBOUND_KEY"]
    missing = [k for k in need if not cfg.get(k)]
    if missing:
        sys.exit(f"fill in {', '.join(missing)} in {ENV_FILE}")
    if not cfg["WEBHOOK_URL"].startswith("https://"):
        sys.exit(f"WEBHOOK_URL in {ENV_FILE} must start with https:// (the webhook key travels in a request header)")
    cfg.setdefault("LISTEN_NAME", "bot")
    cfg.setdefault("CLOUD_NAME", "bot-cloud")
    cfg.setdefault("INBOX_NAME", "bot-inbox")       # box session that receives RN-FWD (never used to send)
    cfg.setdefault("CLOUD_MACHINE", "cloud")          # the box's machine name in `reason-network team`
    for k, d in [("MAX_WAKES_PER_HOUR", 20), ("MIN_SECONDS_BETWEEN_WAKES", 20), ("MAX_BATCH", 10),
                 ("MAX_PROXY_PER_HOUR", 60), ("MAX_SPOOL", 1000), ("WEBHOOK_MAX_BODY_CHARS", 3500),
                 ("DOORBELL_PREVIEW_CHARS", 200), ("FWD_COPY_DAYS", 7), ("REFWD_MAX_PER_ID", 2),
                 ("REFWD_MAX_PER_HOUR", 20), ("GOT_TIMEOUT_SECONDS", 900), ("GOT_DONE_SECONDS", 7200),
                 ("GOT_MAX_RERINGS", 2), ("NONCE_KEEP_SECONDS", 86400)]:
        cfg[k] = int(cfg.get(k, d))
    cfg["WEBHOOK_MAX_BODY_CHARS"] = max(600, cfg["WEBHOOK_MAX_BODY_CHARS"])
    if cfg["NONCE_KEEP_SECONDS"] <= 0:
        logging.getLogger("agent-relay").warning("NONCE_KEEP_SECONDS=%d is not positive; using 86400",
                                                 cfg["NONCE_KEEP_SECONDS"])
        cfg["NONCE_KEEP_SECONDS"] = 86400
    return cfg


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


WEBHOOK_OPENER = urllib.request.build_opener(NoRedirect)


def webhook_open(req, timeout):
    return WEBHOOK_OPENER.open(req, timeout=timeout)


def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def notice_item(mid, text):
    """A relay notice for the routine: a doorbell:false item with the text inline."""
    return {"id": mid, "from": "relay", "agent": "agent-relay", "thread": None, "text": text[:2000]}


CAPS = "got1"
SEND_REFUSED = re.compile(r"refused|not on this machine|no session|nobody matches|ambiguous", re.I)
SEND_UNSURE = re.compile(r"held|error", re.I)
SEND_ACCEPTED = "→"
FWD_MAX_BYTES = 60 * 1024          # engine limit is 64 KB per message body; keep room for the header
FWD_TRAILER = "\n--- RN-FWD END"
AGENT_ENV = ("REASON_NETWORK_AGENT", "REASON_NETWORK_SESSION", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID",
             "CODEX_THREAD_ID", "CODEX_SESSION_ID", "GEMINI_CLI", "CURSOR_AGENT", "OPENCODE", "COPILOT_CLI",
             "COPILOT_AGENT_SESSION_ID")


def send_status(out):
    """The engine's own words in an `rn send` output, without addresses or quoted names: on a line
    "→ <address>: <status>" exactly the text after the first ": "; elsewhere the line minus quoted text and minus
    whole words shaped like an address (they contain "/" or "@"). A bare name is never removed by value, since it
    may equal one of the engine's own words."""
    words = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith(SEND_ACCEPTED):
            words.append(line[len(SEND_ACCEPTED):].strip().partition(": ")[2])
            continue
        line = re.sub(r"'[^']*'|\"[^\"]*\"", " ", line)
        words.append(" ".join(w for w in line.split() if not re.search(r"[/@]", w)))
    return "\n".join(words)


def send_outcome(rc, out):
    """Result of an `rn send`, judged on send_status() only: "failed" (clearly refused, whatever the exit code),
    "sent" (exit 0, a "→ <address>:" line, no held/error), else "unknown"."""
    status = send_status(out)
    if SEND_REFUSED.search(status):
        return "failed"
    accepted = any(l.strip().startswith(SEND_ACCEPTED) for l in out.splitlines())
    if rc == 0 and accepted and not SEND_UNSURE.search(status):
        return "sent"
    return "unknown"


def one_line(v):
    return re.sub(r"[\r\n]", " ", "" if v is None else str(v))


def sign_fwd(key, mid, frm, agent, thread, text_len, text_sha, ts):
    msg = "\n".join(["fwd1", mid, frm, agent, thread, str(text_len), text_sha, ts]).encode()
    return hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()


def fwd_body(key, mid, frm, agent, thread, text, ts=None):
    """RN-FWD v1: header lines, '---', the exact text, then a trailer line (so the engine's trim of the whole
    body never touches the text's own leading/trailing whitespace)."""
    mid, frm, agent, thread = (one_line(x) for x in (mid, frm, agent, thread))
    ts = str(int(time.time())) if ts is None else str(ts)
    n, h = len(text), sha256_hex(text)
    sig = sign_fwd(key, mid, frm, agent, thread, n, h, ts)
    return (f"RN-FWD v1\nid: {mid}\nfrom: {frm}\nagent: {agent}\nthread: {thread}\ntext_len: {n}\n"
            f"text_sha256: {h}\nts: {ts}\nsig: {sig}\ncaps: {CAPS}\n---\n{text}{FWD_TRAILER}")


REFWD_HEAD = "RN-REFWD v1"
REFWD_ACK_HEAD = "RN-REFWD-ACK v1"
REFWD_FRESH_SECONDS = 900


def sign_refwd(key, mid, ts, nonce):
    return hmac.new(key.encode(), "\n".join(["refwd1", mid, ts, nonce]).encode(), hashlib.sha256).hexdigest()


def sign_refwd_ack(key, mid, status, attempt, ts):
    msg = "\n".join(["refwdack1", mid, status, str(attempt), ts]).encode()
    return hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()


def refwd_ack_body(key, mid, status, attempt, reason="", ts=None):
    mid, status, reason = one_line(mid), one_line(status), one_line(reason)[:200]
    ts = str(int(time.time())) if ts is None else str(ts)
    sig = sign_refwd_ack(key, mid, status, attempt, ts)
    return (f"{REFWD_ACK_HEAD}\nid: {mid}\nstatus: {status}\nattempt: {attempt}\nreason: {reason}\n"
            f"ts: {ts}\nsig: {sig}")


def parse_refwd(text):
    """RN-REFWD v1 request -> {id, ts, nonce, sig}, {} if malformed, None if not a request at all."""
    lines = text.strip().splitlines()
    if not lines or lines[0].strip() != REFWD_HEAD:
        return None
    f = {}
    for l in lines[1:]:
        k, _, v = l.partition(":")
        f[k.strip()] = v.strip()
    return f if all(f.get(k) for k in ("id", "ts", "nonce", "sig")) else {}


def is_refwd(text):
    return (text or "").lstrip().startswith(REFWD_HEAD + "\n") or (text or "").strip() == REFWD_HEAD


def has_caps(value):
    return CAPS in [c.strip() for c in (value or "").split(",")]


GOT_HEAD = "RN-GOT v1"
GOT_ID = re.compile(r"[A-Za-z0-9._-]{1,120}")
GOT_MAX_IDS = 100
ALERT_PREFIX = "relay-alerts-"
ALERT_OVERFLOW = "alerts-overflow"
ALERT_ITEM_CHARS = 300
MAX_ALERTS = 200
SUPERSEDED = "Replaces the earlier failed/refused report; do not resend. "


def sign_got(key, stored, done, ts, nonce):
    return hmac.new(key.encode(), "\n".join(["got1", stored, done, ts, nonce]).encode(), hashlib.sha256).hexdigest()


def got_body(key, stored, done, ts=None, nonce=None):
    stored, done = ",".join(stored), ",".join(done)
    ts = str(int(time.time())) if ts is None else str(ts)
    nonce = nonce or os.urandom(12).hex()
    return (f"{GOT_HEAD}\nstored: {stored}\ndone: {done}\nts: {ts}\nnonce: {nonce}\n"
            f"sig: {sign_got(key, stored, done, ts, nonce)}")


def parse_got(text):
    """RN-GOT v1 receipt -> fields plus id lists "stored_ids"/"done_ids", {} if malformed, None if not one."""
    lines = (text or "").strip().splitlines()
    if not lines or lines[0].strip() != GOT_HEAD:
        return None
    f = {}
    for l in lines[1:]:
        k, sep, v = l.partition(":")
        if sep:
            f[k.strip()] = v.strip()
    if "stored" not in f or "done" not in f or not all(f.get(k) for k in ("ts", "nonce", "sig")):
        return {}
    if not re.fullmatch(r"[0-9a-f]{64}", f["sig"]):
        return {}
    ids = {k: f[k].split(",") if f[k] else [] for k in ("stored", "done")}
    every = ids["stored"] + ids["done"]
    if not every or len(every) > GOT_MAX_IDS or not all(GOT_ID.fullmatch(i) for i in every):
        return {}
    f["stored_ids"], f["done_ids"] = ids["stored"], ids["done"]
    return f


def is_got(text):
    return (text or "").lstrip().startswith(GOT_HEAD + "\n") or (text or "").strip() == GOT_HEAD


def safe_name(mid):
    s = re.sub(r"[^A-Za-z0-9._-]", "_", str(mid))[:120].lstrip(".")
    return s or "_"


class Copies:
    """Exact copies of forwarded messages, for RN-REFWD. Dir 0700, files 0600, pruned after `days`."""
    def __init__(self, days=7):
        self.dir = STATE_DIR / "fwd-copies"
        self.days = days

    def put(self, m):
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        p = self.dir / f"{safe_name(m['id'])}.json"
        tmp = p.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"id": m["id"], "from": m.get("from", ""), "agent": m.get("agent"),
                       "thread": m.get("thread"), "text": m.get("text") or "", "saved": time.time()},
                      fh, ensure_ascii=False)
        os.chmod(tmp, 0o600)
        tmp.replace(p)

    def get(self, mid):
        p = self.dir / f"{safe_name(mid)}.json"
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
        if d.get("id") != mid or time.time() - d.get("saved", 0) > self.days * 86400:
            return None
        return d

    def prune(self):
        if not self.dir.exists():
            return 0
        n, cut = 0, time.time() - self.days * 86400
        for p in self.dir.glob("*.json"):
            try:
                if p.stat().st_mtime < cut:
                    p.unlink(); n += 1
            except FileNotFoundError:
                pass
        return n


class State:
    def __init__(self, max_spool=1000, seen_days=8, nonce_keep=86400):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(STATE_DIR, 0o700)
        self.path = STATE_DIR / "state.json"
        try:
            d = json.loads(self.path.read_text())
        except Exception:
            d = {}
        now = time.time()
        self.seen = d.get("seen", [])            # message ids already handled (dedupe); a list, as in earlier versions
        self.seen_t = d.get("seen_t")            # message id -> when it was handled; `seen` is kept by this age
        if not isinstance(self.seen_t, dict):    # first start on this version, or saved by an earlier one
            self.seen_t = {}
        for i in self.seen:
            self.seen_t.setdefault(i, now)
        self.nonces = d.get("nonces", {})        # envelope nonce -> ts (replay guard)
        self.sends = d.get("sends") if isinstance(d.get("sends"), dict) else {}
        if "sends" not in d:
            for n, t in self.nonces.items():
                if isinstance(t, (int, float)):
                    self.sends.setdefault(n, {"t": t, "state": "unknown", "id": "", "to": "", "noticed": False})
        self.wakes = d.get("wakes", [])          # timestamps of webhook POSTs
        self.proxies = d.get("proxies", [])      # timestamps of proxied sends
        self.spool = d.get("spool", [])          # items waiting to be POSTed
        self.overflow_dropped = d.get("overflow_dropped", 0)   # dropped since the overflow notice last went out
        self.refwds = d.get("refwds", {})        # message id -> [timestamps of honoured RN-REFWD]
        self.recv_floor = d.get("recv_floor")    # first start on this version: startup recovery never looks earlier
        self.pending = d.get("pending") if isinstance(d.get("pending"), dict) else {}
        self.alerts = d.get("alerts") if isinstance(d.get("alerts"), list) else []
        self.got_last = d.get("got_last")
        self.caps_lost = d.get("caps_lost") is True
        self.got_probe = d.get("got_probe") if isinstance(d.get("got_probe"), dict) else None
        self.last_batch = d.get("last_batch")
        self.logged_counts = None
        self.max_spool = max_spool
        self.seen_days = seen_days
        self.nonce_keep = nonce_keep
        stopped = self.stopped_sends()
        if not isinstance(self.recv_floor, (int, float)):
            self.recv_floor = now
            stopped = True
        if stopped:
            self.save()

    def stopped_sends(self):
        """Sends still marked sending were cut off by the last stop: unknown now, each with an alert (before any
        cleanup by age)."""
        found = False
        for n, r in self.sends.items():
            if isinstance(r, dict) and r.get("state") == "sending":
                r["state"], r["noticed"], found = "unknown", True, True
                lead = SUPERSEDED if self.supersede(n) else ""
                self.alert(f"unknown:{n}", f"{lead}outbound {r.get('id') or 'unknown'} to "
                                           f"{r.get('to') or 'unknown recipient'} was being sent when the relay "
                                           f"stopped; result unknown. Check with the recipient; to send anyway, "
                                           f"change the text.")
        return found

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
        for i in dropped:
            p = self.pending.get(str(i.get("id"))) if i.get("doorbell") else None
            if p is not None:
                if p["stage"] == "ringing":
                    p["stage"] = "unacked"
                p["t"], p["keep"] = time.time(), True
                if self.got_probe and self.got_probe.get("id") == str(i.get("id")):
                    self.got_probe = None
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
        for i in self.seen:
            self.seen_t.setdefault(i, now)
        self.seen_t = {i: t for i, t in self.seen_t.items() if now - t < self.seen_days * 86400}
        self.seen = [i for i in self.seen if i in self.seen_t]
        self.nonces = {n: t for n, t in self.nonces.items() if now - t < 3600}
        self.sends = {n: r for n, r in self.sends.items() if isinstance(r, dict)
                      and isinstance(r.get("t"), (int, float)) and now - r["t"] < self.nonce_keep}
        self.wakes = [t for t in self.wakes if now - t < 3600]
        self.proxies = [t for t in self.proxies if now - t < 3600]
        self.refwds = {k: v for k, v in self.refwds.items() if v and now - max(v) < 8 * 86400}
        self.trim_spool()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.__dict__ | {"path": None, "max_spool": None, "seen_days": None,
                                                   "nonce_keep": None, "logged_counts": None}, ensure_ascii=False))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)
        counts, log = (len(self.pending), len(self.alerts)), logging.getLogger("agent-relay")
        last = self.logged_counts or (None, None)
        if counts[0] != last[0]:
            log.info("pending: %d", counts[0])
        if counts[1] != last[1]:
            log.info("alerts: %d", counts[1])
        self.logged_counts = counts

    def alert(self, key, text):
        """A durable relay alert: kept outside the spool until the routine confirms the batch that carried it.
        Saved with the caller's next save()."""
        if any(a["key"] == key for a in self.alerts):
            return
        logging.getLogger("agent-relay").warning("ALERT %s: %s", key, text)
        if sum(1 for a in self.alerts if a["key"] != ALERT_OVERFLOW) >= MAX_ALERTS:
            o = next((a for a in self.alerts if a["key"] == ALERT_OVERFLOW), None)
            if o is None:
                o = {"key": ALERT_OVERFLOW, "text": "", "t": time.time(), "batch": None, "posted": None,
                     "tries": 0, "n": 0, "sent_n": 0}
                self.alerts.append(o)
            o["n"] += 1
            return
        self.alerts.append({"key": key, "text": text, "t": time.time(), "batch": None, "posted": None, "tries": 0})

    def drop_alert(self, key):
        self.alerts = [a for a in self.alerts if a["key"] != key]

    def supersede(self, nonce):
        old = (f"reject:{nonce}", f"failed:{nonce}")
        kept = [a for a in self.alerts if a["key"] not in old]
        dropped = len(kept) < len(self.alerts)
        self.alerts = kept
        return dropped

    def confirm_alerts(self, batch):
        kept = []
        for a in self.alerts:
            if a.get("batch") != batch:
                kept.append(a)
            elif a["key"] == ALERT_OVERFLOW:
                a["n"], a["sent_n"] = max(0, a.get("n", 0) - a.get("sent_n", 0)), 0
                if a["n"] > 0:
                    a.update(batch=None, posted=None, tries=0)
                    kept.append(a)
        self.alerts = kept


def parse(out):
    msgs = []
    for attrs, body in BLOCK.findall(out):
        a = {k: html.unescape(v) for k, v in ATTR.findall(attrs)}
        if a.get("id"):
            msgs.append({"id": a["id"], "from": a.get("from", ""), "agent": a.get("agent"),
                         "thread": a.get("reply_to"), "text": body})
    return msgs


def sign(key, to, reply_to, ts, nonce, body):
    msg = "\n".join(["v1", to, reply_to, ts, nonce, body]).encode()
    return hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()


def parse_envelope(text):
    """RN-OUT v1 header lines, '---', then the body."""
    head, sep, body = text.partition("\n---\n")
    lines = head.splitlines()
    if not sep or not lines or lines[0].strip() != "RN-OUT v1":
        return None
    f = {}
    for l in lines[1:]:
        if ":" in l:
            k, v = l.split(":", 1)
            f[k.strip()] = v.strip()
    if not all(k in f for k in ("to", "ts", "nonce", "sig")):
        return None
    f.setdefault("reply_to", "")
    f["body"] = body
    return f


class Relay:
    def __init__(self, cfg):
        self.c = cfg
        self.s = State(cfg["MAX_SPOOL"], cfg.get("FWD_COPY_DAYS", 7) + 1, cfg.get("NONCE_KEEP_SECONDS", 86400))
        self.me = f"{cfg['PERSON']}/{cfg['LISTEN_NAME']}"
        self.cloud = f"{cfg['PERSON']}/{cfg['CLOUD_NAME']}"
        self.inbox = f"{cfg['PERSON']}/{cfg.get('INBOX_NAME', 'bot-inbox')}"
        self.terminal = re.compile(re.escape(cfg["PERSON"]) + r"/terminal(-[0-9]+|@"
                                   + re.escape(cfg.get("CLOUD_MACHINE", "cloud")) + r")?")
        self.fwd_to = f"{self.inbox}@{cfg.get('CLOUD_MACHINE', 'cloud')}"
        self.log = logging.getLogger("agent-relay")
        self.copies = Copies(cfg.get("FWD_COPY_DAYS", 7))
        self.last_prune = 0
        self.db_noticed = False
        if self.got_timeout() <= 0 and any(not p.get("keep") for p in self.s.pending.values()):
            self.log.info("GOT_TIMEOUT_SECONDS is 0: %d pending message(s) keep being re-rung until done",
                          len(self.s.pending))
            self.caps_gone(lost=False)
            self.s.save()

    def got_timeout(self):
        return self.c.get("GOT_TIMEOUT_SECONDS", 900)

    def unacked_timeout(self):
        return self.got_timeout() if self.got_timeout() > 0 else 900

    def probing(self):
        return self.got_timeout() > 0 and not self.s.caps_lost

    # ---- webhook payloads ----
    def payload(self, items):
        if items and all(i.get("doorbell") for i in items):
            keep = ("id", "from", "agent", "thread", "text_len", "text_sha256", "preview", "refwd", "rering",
                    "forward_failed")
            return {"source": "reason-network", "relay_version": 3, "doorbell": True, "to": self.me,
                    "reply_via": self.cloud, "inbox": self.inbox,
                    "messages": [{k: i[k] for k in keep if k in i} for i in items]}
        return {"source": "reason-network", "relay_version": 3, "doorbell": False, "to": self.me,
                "reply_via": self.cloud, "messages": items}

    def body_chars(self, items):
        return len(json.dumps(self.payload(items), ensure_ascii=False))

    def rn(self, *args, timeout=60, stdin=None, env=None):
        return subprocess.run([self.c["RN_BIN"], *args], capture_output=True, text=True,
                              timeout=timeout, input=stdin, env=env)

    # ---- inbound: full text over Reason Network to the box, then a doorbell ----
    def forward(self, m):
        """Sends m's full text to the box inbox. Returns (ok, reason)."""
        text = m.get("text") or ""
        body = fwd_body(self.c["OUTBOUND_KEY"], m["id"], m.get("from", ""), m.get("agent"), m.get("thread"), text)
        if len(body.encode("utf-8")) > FWD_MAX_BYTES:
            return False, f"too large for one RN message ({len(body.encode('utf-8'))} bytes)"
        # no --from: sent as <team-name>/terminal, which the engine's agent-loop guard (12 per pair per 10 min) skips
        env = {k: v for k, v in os.environ.items() if k not in AGENT_ENV}
        try:
            r = self.rn("send", self.fwd_to, "-", stdin=body, env=env)
        except Exception as e:
            return False, f"rn send failed: {type(e).__name__}"
        res = (r.stdout + r.stderr).strip()
        first = res.splitlines()[0] if res else ""
        if send_outcome(r.returncode, res) != "sent":
            return False, f"rn send rc={r.returncode}: {first[:60]}"
        self.log.info("forwarded %s (%d chars) to %s: %s", m["id"], len(text), self.fwd_to, first[:120])
        return True, first

    def doorbell_item(self, m):
        text = m.get("text") or ""
        it = {"id": str(m["id"]), "from": m.get("from", ""), "agent": m.get("agent"), "thread": m.get("thread"),
              "doorbell": True, "text_len": len(text), "text_sha256": sha256_hex(text)}
        n = self.c.get("DOORBELL_PREVIEW_CHARS", 200)
        if n > 0:
            it["preview"] = text[:n]
        return it

    # ---- re-forward on the box's request (RN-REFWD v1) ----
    def refwd(self, m):
        """Handles an RN-REFWD request. Never raises; never turns the request into inbound mail."""
        req, frm = parse_refwd(m.get("text") or ""), m.get("from", "")
        if frm not in (self.cloud, self.inbox):
            self.log.warning("refwd request %s from %s ignored: sender not allowed", m["id"], frm)
            return
        if not req:
            self.log.warning("refwd request %s from %s ignored: malformed", m["id"], frm)
            return
        key, now, mid = self.c["OUTBOUND_KEY"], time.time(), req["id"]
        good = hmac.compare_digest(req["sig"], sign_refwd(key, mid, req["ts"], req["nonce"]))
        try:
            fresh = abs(now - int(req["ts"])) < REFWD_FRESH_SECONDS
        except ValueError:
            fresh = False
        if not good or not fresh or req["nonce"] in self.s.nonces:
            self.log.warning("refwd request %s for %s ignored (sig=%s fresh=%s replay=%s)", m["id"], mid, good,
                             fresh, req["nonce"] in self.s.nonces)
            return
        self.s.nonces[req["nonce"]] = now
        done = self.s.refwds.get(mid, [])
        hour = sum(1 for v in self.s.refwds.values() for t in v if now - t < 3600)
        if len(done) >= self.c.get("REFWD_MAX_PER_ID", 2) or hour >= self.c.get("REFWD_MAX_PER_HOUR", 20):
            self.log.warning("refwd %s refused: limit (%d for this id, %d this hour)", mid, len(done), hour)
            return self.ack(mid, "limit", len(done), f"{len(done)} re-forwards already for this id, {hour} this hour")
        copy = self.copies.get(mid)
        if not copy:
            self.log.warning("refwd %s: no copy kept (older than %s days, or never forwarded)", mid,
                             self.c.get("FWD_COPY_DAYS", 7))
            return self.ack(mid, "no-copy", len(done), "no copy on the relay (never forwarded, or expired)")
        attempt = len(done) + 1
        self.s.refwds[mid] = done + [now]
        ok, why = self.forward(copy)
        if not ok:
            self.log.warning("refwd %s attempt %d failed: %s", mid, attempt, why)
            return self.ack(mid, "failed", attempt, why)
        p = self.s.pending.get(mid)
        if has_caps(req.get("caps")):
            if p and p["stage"] == "unacked" and p["t"] is not None:
                p["t"] = now
            self.log.info("refwd %s attempt %d requested by %s (%s): re-forwarded, no doorbell (the cloud is "
                          "waiting for it)", mid, attempt, frm, m["id"])
        else:
            item = self.doorbell_item(copy)
            item["refwd"] = attempt
            self.queue_doorbell(item)
            if p:
                p["t"] = None
            self.log.info("refwd %s attempt %d requested by %s (%s): re-forwarded, doorbell queued", mid, attempt,
                          frm, m["id"])
        self.ack(mid, "resent", attempt)

    def queue_doorbell(self, item):
        """At most one doorbell per id in the spool: a later one updates refwd/rering/forward_failed in place."""
        for it in self.s.spool:
            if it.get("doorbell") and str(it.get("id")) == str(item["id"]):
                it.update({k: item[k] for k in ("refwd", "rering", "forward_failed") if k in item})
                return it
        self.s.spool.append(item)
        return item

    # ---- receipts from the cloud (RN-GOT v1) and pending messages ----
    def got(self, m):
        """Handles an RN-GOT receipt. Never raises; anything invalid is logged and changes nothing."""
        f, frm = parse_got(m.get("text") or ""), m.get("from", "")
        if frm not in (self.cloud, self.inbox) and not self.terminal.fullmatch(frm):
            self.log.warning("receipt %s from %s ignored: sender not allowed", m["id"], frm)
            return
        if not f:
            self.log.warning("receipt %s from %s ignored: malformed", m["id"], frm)
            return
        key, now = self.c["OUTBOUND_KEY"], time.time()
        good = hmac.compare_digest(f["sig"], sign_got(key, f["stored"], f["done"], f["ts"], f["nonce"]))
        try:
            fresh = abs(now - int(f["ts"])) < REFWD_FRESH_SECONDS
        except ValueError:
            fresh = False
        if not good or not fresh or f["nonce"] in self.s.nonces:
            self.log.warning("receipt %s ignored (sig=%s fresh=%s replay=%s)", m["id"], good, fresh,
                             f["nonce"] in self.s.nonces)
            return
        self.s.nonces[f["nonce"]] = now
        done = list(dict.fromkeys(f["done_ids"]))
        stored = [i for i in dict.fromkeys(f["stored_ids"]) if i not in done]
        for mid in done:
            if mid.startswith(ALERT_PREFIX):
                self.s.confirm_alerts(mid)
            self.complete(mid, receipt=True)
        for mid in stored:
            p = self.s.pending.get(mid)
            if p is None:
                continue
            p["keep"] = True
            if p["stage"] != "stored":
                p["stage"] = "stored"
                if p["t"] is not None:
                    p["t"] = now
        self.s.got_last, self.s.caps_lost, self.s.got_probe = now, False, None
        self.log.info("receipt %s from %s: stored %d, done %d", m["id"], frm, len(stored), len(done))

    def complete(self, mid, receipt=False):
        """Proof that mid was handled: a done receipt, or a valid RN-OUT replying to it."""
        mid = str(mid)
        self.s.drop_alert(f"abandon:{mid}")
        if receipt:
            self.s.spool = [i for i in self.s.spool if not (i.get("doorbell") and str(i.get("id")) == mid)]
        if mid in self.s.pending:
            self.drop_pending(mid)

    def drop_pending(self, mid):
        del self.s.pending[mid]
        if self.s.got_probe and self.s.got_probe.get("id") == mid:
            self.s.got_probe = None

    def caps_gone(self, lost):
        for p in self.s.pending.values():
            p["keep"] = True
        if lost:
            self.s.caps_lost = True
        self.s.got_probe = None

    def check_pending(self):
        """Re-rings pending messages that timed out and turns receipts off when the cloud stopped sending them.
        Runs before every flush()."""
        now, s, changed = time.time(), self.s, False
        if not self.probing():
            for mid, p in list(s.pending.items()):
                if p["stage"] == "unacked" and p["rung"] and not p["keep"]:
                    self.drop_pending(mid)
                    changed = True
                elif p["stage"] == "unacked" and p["rung"] and p["keep"]:
                    p["stage"] = "stored"
                    changed = True
        gp = s.got_probe
        if gp and gp.get("id") not in s.pending:
            s.got_probe, gp, changed = None, None, True
        if self.probing() and gp and gp.get("posted") is not None and now - gp["posted"] > self.got_timeout():
            self.caps_gone(lost=True)
            s.alert(f"caps-lost:{gp['id']}",
                    "The cloud stopped sending receipts (RN-GOT), so the relay no longer re-rings mail that may "
                    "have been lost on the way; this turns back on with the next receipt. Messages not yet "
                    "confirmed as handled are still re-rung until they are, or reported one by one.")
            s.save()
            return
        resend, maxr = self.unacked_timeout(), self.c.get("GOT_MAX_RERINGS", 2)
        for a in list(s.alerts):
            if a["posted"] is not None and now - a["posted"] > resend and a["tries"] >= maxr:
                rest = a.get("n", 0) - a.get("sent_n", 0) if a["key"] == ALERT_OVERFLOW else 0
                self.log.warning("alert %s dropped after %d unacknowledged deliveries%s", a["key"], a["tries"] + 1,
                                 f"; {rest} added since then kept" if rest > 0 else "")
                if rest > 0:
                    a.update(n=rest, sent_n=0, batch=None, posted=None, tries=0)
                else:
                    s.alerts.remove(a)
                changed = True
        for mid, p in list(s.pending.items()):
            limit = {"unacked": resend, "stored": self.c.get("GOT_DONE_SECONDS", 7200)}.get(p["stage"])
            if p["t"] is None or limit is None or now - p["t"] <= limit:
                continue
            if p["rerings"] >= maxr:
                s.alert(f"abandon:{mid}", f"Message {mid} (from {p.get('from', '')}) was re-rung {p['rerings']} "
                                          f"time(s) and is still not confirmed as handled. Ask the sender to "
                                          f"resend it, or check it on the cloud computer.")
                self.drop_pending(mid)
                s.save()
                changed = False
                continue
            p["rerings"] += 1
            p["t"] = None
            if p["stage"] == "unacked" and p["rung"] and not p["keep"] and s.got_probe is None:
                s.got_probe = {"id": mid, "posted": None}
            copy = self.copies.get(mid)
            item = self.doorbell_item(copy) if copy else {"id": mid, "from": p.get("from", ""), "agent": None,
                                                          "thread": None, "doorbell": True}
            item["rering"] = p["rerings"]
            item = self.queue_doorbell(item)
            s.save()
            self.log.info("re-ring %s (%s, attempt %d)", mid, p["stage"], p["rerings"])
            if p["stage"] == "unacked":
                ok, why = self.forward(copy) if copy else (False, "no copy kept")
                if not ok:
                    item["forward_failed"] = why[:80]
                    self.log.warning("re-ring %s: forward failed (%s); doorbell flagged", mid, why)
                changed = True
        if changed:
            s.save()

    def next_wait(self, wait):
        """listen's wait: at most until the next pending deadline, receipt check or alert resend, at least 5 s."""
        now, due = time.time(), [wait, 600]
        resend = self.unacked_timeout()
        for p in self.s.pending.values():
            limit = {"unacked": resend, "stored": self.c.get("GOT_DONE_SECONDS", 7200)}.get(p["stage"])
            if p["t"] is not None and limit is not None:
                due.append(p["t"] + limit - now + 1)
        gp = self.s.got_probe
        if gp and gp.get("posted") is not None:
            due.append(gp["posted"] + self.got_timeout() - now + 1)
        for a in self.s.alerts:
            if a["posted"] is not None:
                due.append(a["posted"] + resend - now + 1)
        return max(5, int(min(due)))

    def ack(self, mid, status, attempt, reason=""):
        body = refwd_ack_body(self.c["OUTBOUND_KEY"], mid, status, attempt, reason)
        env = {k: v for k, v in os.environ.items() if k not in AGENT_ENV}
        try:
            r = self.rn("send", self.fwd_to, "-", stdin=body, env=env)
            res = (r.stdout + r.stderr).strip()
            self.log.info("refwd ack %s status=%s rc=%s %s", mid, status, r.returncode,
                          res.splitlines()[0][:120] if res else "")
        except Exception as e:
            self.log.warning("refwd ack %s status=%s not sent: %s", mid, status, type(e).__name__)

    def keep_copy(self, m):
        try:
            self.copies.put(m)
        except Exception as e:
            self.log.warning("could not keep a copy of %s for re-forward: %s", m["id"], type(e).__name__)
        if time.time() - self.last_prune > 3600:
            self.last_prune = time.time()
            n = self.copies.prune()
            if n:
                self.log.info("pruned %d forward copies older than %d days", n, self.copies.days)

    # ---- outbound (Grok Bot -> network) ----
    def signed(self, env):
        return hmac.compare_digest(env["sig"], sign(self.c["OUTBOUND_KEY"], env["to"], env["reply_to"],
                                                    env["ts"], env["nonce"], env["body"]))

    def stored_text(self, mid):
        path = Path(self.c.get("RN_DB") or RN_DB)
        try:
            db = sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True, timeout=5)
            try:
                cols = {r[1] for r in db.execute("PRAGMA table_info(messages)")}
                sql = "SELECT body FROM messages WHERE id = ? AND recipient = ? AND sender = ?"
                if "held" in cols:
                    sql += " AND COALESCE(held, 0) = 0"
                rows = db.execute(sql + " LIMIT 2", (str(mid), self.me, self.cloud)).fetchall()
            finally:
                db.close()
        except Exception as e:
            self.log.warning("could not read %s for the stored text of %s: %s", path, mid, type(e).__name__)
            return None
        return rows[0][0] if len(rows) == 1 and isinstance(rows[0][0], str) else None

    def proxy(self, m):
        env = parse_envelope(m["text"])
        now = time.time()
        if not (env and self.signed(env)):
            stored = self.stored_text(m["id"])
            alt = parse_envelope(stored) if stored is not None and stored != m["text"] else None
            if alt and self.signed(alt):
                self.log.info("proxy %s: verified against the stored text in network.db (listen showed it rewritten)",
                              m["id"])
                env = alt
        if not env:
            return self.notice(f"{self.c['CLOUD_NAME']} sent {m['id']} without a valid RN-OUT envelope; not forwarded")
        if not self.signed(env):
            self.log.warning("proxy refused %s: bad signature", m["id"])
            return self.notice(f"outbound {m['id']} refused: bad signature")
        if env["reply_to"]:
            self.complete(env["reply_to"])
        nonce, to, s = env["nonce"], env["to"], self.s
        rec = s.sends.get(nonce)
        if rec is None and nonce in s.nonces:
            rec = s.sends[nonce] = {"t": s.nonces[nonce], "state": "unknown", "id": "", "to": "", "noticed": False}
        if rec is not None and now - rec.get("t", now) >= s.nonce_keep:
            rec = None
        if rec is not None and rec.get("state") == "sent":
            self.log.info("duplicate outbound %s dropped (first sent at %s)", m["id"],
                          time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rec["t"])))
            s.drop_alert(f"reject:{nonce}")
            s.drop_alert(f"failed:{nonce}")
            return
        if rec is not None and rec.get("state") != "failed":
            self.log.warning("duplicate outbound %s not sent: an earlier send with this nonce is %s", m["id"],
                             rec.get("state"))
            if not rec.get("noticed"):
                rec["noticed"] = True
                s.alert(f"dup-unknown:{nonce}", f"outbound {m['id']} repeats an earlier send to "
                                                f"{rec.get('to') or 'unknown recipient'} whose result is unknown; not "
                                                f"resent to avoid a duplicate. Check with the recipient; to send "
                                                f"anyway, change the text.")
            return
        try:
            fresh = abs(now - int(env["ts"])) < 900
        except ValueError:
            fresh = False
        if not fresh:
            self.log.warning("proxy refused %s: stale", m["id"])
            return s.alert(f"reject:{nonce}", f"outbound {m['id']} to {to} refused: stale (signed more than 15 "
                                              f"minutes before it arrived)")
        if len(s.proxies) >= self.c["MAX_PROXY_PER_HOUR"]:
            return s.alert(f"reject:{nonce}",
                           f"outbound {m['id']} refused: over {self.c['MAX_PROXY_PER_HOUR']} sends/hour")
        if to in ("*",) or to.endswith("/*") or to.startswith("repo:") or to in (self.me, self.cloud):
            return s.alert(f"reject:{nonce}", f"outbound {m['id']} refused: group or self address '{to}'")
        if not OUT_TO.fullmatch(to) or (env["reply_to"] and not OUT_REPLY_TO.fullmatch(env["reply_to"])):
            shown = f"to={repr(to)[:80]} reply_to={repr(env['reply_to'])[:80]}"
            self.log.warning("proxy refused %s: not a plain address/id (%s)", m["id"], shown)
            return s.alert(f"reject:{nonce}", f"outbound {m['id']} refused: recipient or reply_to is not a plain "
                                              f"address/id; not sent. Do not resend; tell the user. {shown}")
        s.sends[nonce] = {"t": now, "state": "sending", "id": str(m["id"]), "to": to, "noticed": False}
        s.nonces[nonce] = now
        s.proxies.append(now)
        s.save()
        args = ["send", to, "--from", self.me]
        if env["reply_to"]:
            args += ["--reply-to", env["reply_to"]]
        args.append("-")  # body from stdin
        unsure = "it may or may not have been delivered. Check with the recipient; to send anyway, change the text."
        try:
            r = self.rn(*args, stdin=env["body"])
        except OSError as e:
            self.log.warning("proxy %s -> %s: send %s", m["id"], to, type(e).__name__)
            state, text = "failed", f"outbound {m['id']} to {to} failed: rn send {type(e).__name__}"
        except Exception as e:     # e.g. TimeoutExpired: the engine may still have queued it
            self.log.warning("proxy %s -> %s: send %s", m["id"], to, type(e).__name__)
            state = "unknown"
            text = f"outbound {m['id']} to {to} not confirmed, result unknown: rn send {type(e).__name__}; {unsure}"
        else:
            result = (r.stdout + r.stderr).strip()
            first = result.splitlines()[0] if result else ""
            self.log.info("proxied %s -> %s rc=%s %s", m["id"], to, r.returncode, first)
            state = send_outcome(r.returncode, result)
            text = (f"outbound {m['id']} to {to} failed: {result[:500]}" if state == "failed" else
                    f"outbound {m['id']} to {to} not confirmed, result unknown: rn send rc={r.returncode}: "
                    f"{first[:200]}; {unsure}")
        rec = s.sends[nonce]
        rec["state"] = state
        if state == "sent":
            s.drop_alert(f"reject:{nonce}")
            s.drop_alert(f"failed:{nonce}")
        elif state == "failed":
            s.alert(f"failed:{nonce}", text)
        else:
            rec["noticed"] = True
            s.alert(f"unknown:{nonce}", (SUPERSEDED if s.supersede(nonce) else "") + text)
        s.save()

    def notice(self, text):
        self.s.spool.append(notice_item(f"relay-{int(time.time() * 1000)}", text))

    # ---- inbound (network -> Grok Bot) ----
    def handle(self, msgs):
        """`listen` has already marked msgs read: one message failing must never lose the rest of the batch."""
        for m in msgs:
            if m["id"] in self.s.seen:
                continue
            self.s.seen.append(m["id"]); self.s.seen_t[m["id"]] = time.time()
            try:
                self.handle_one(m)
            except Exception as e:      # not BaseException: KeyboardInterrupt/SystemExit still stop the relay
                self.log.exception("could not handle %s from %s", m["id"], m.get("from", ""))
                self.notice(f"relay could not handle message {m['id']} from {m.get('from', '')}: "
                            f"{type(e).__name__}. Ask the sender to resend if it mattered; see the relay log on the Mac.")
        self.s.save()

    def handle_one(self, m):
        if is_refwd(m.get("text")):
            return self.refwd(m)        # allowed senders are honoured; anyone else is logged and ignored
        if is_got(m.get("text")):
            return self.got(m)
        if m["from"] == self.cloud:
            return self.proxy(m)
        if m["from"] == self.me:
            return
        self.keep_copy(m)
        # Queue and save the doorbell (with `seen`) before forwarding: if the relay dies after this point the
        # doorbell still rings, and the cloud's fetch asks for the kept copy with a signed RN-REFWD.
        item = self.queue_doorbell(self.doorbell_item(m))
        if self.got_timeout() > 0:
            self.s.pending[str(m["id"])] = {"stage": "ringing", "t": None, "rerings": 0, "rung": False,
                                            "keep": False, "from": m.get("from", "")}
        self.s.save()
        ok, why = self.forward(m)
        if not ok:
            # ring anyway: the cloud's fetch will ask for the kept copy with a signed RN-REFWD
            item["forward_failed"] = why[:80]
            self.log.warning("forward %s failed (%s); doorbell flagged, copy kept for re-forward", m["id"], why)

    # ---- recovery from the engine's network.db (startup, and after a listen that timed out or failed) ----
    def reconcile(self):
        """Messages `listen` marked read but the relay never handled (it died first, or listen timed out after
        marking them) stay in network.db. Hands those sent since max(recv_floor, FWD_COPY_DAYS ago), not in `seen`
        and not held for inbound approval to handle(), a page at a time. Idempotent; never stops the relay.
        Only rows already in the table when this call began (rowid <= snapshot) are candidates; later ones are
        left to the next listen, so mail that keeps arriving cannot keep the relay here (it would never reach
        flush()), whatever the senders' clocks say."""
        path = Path(self.c.get("RN_DB") or RN_DB)
        floor_ms = max(self.s.recv_floor, time.time() - self.c.get("FWD_COPY_DAYS", 7) * 86400) * 1000
        snap = None                    # max(rowid) when this call began, read with the first page
        after = None                   # (sent_at, id) of the last row read: keyset paging, no connection kept open
        found, sample = 0, []          # count, and the first 20 ids for the log
        while True:
            try:
                snap, rows = self.db_page(path, floor_ms, snap, after)
            except Exception as e:
                self.log.warning("could not check %s for messages missed during a restart: %s: %s", path,
                                 type(e).__name__, e)
                if not self.db_noticed:    # once per process: a failing listen must not turn into a notice loop
                    self.db_noticed = True
                    self.notice("relay could not check network.db for messages missed during a restart; anything "
                                "that arrived just before the relay stopped may not have been forwarded. See the "
                                "relay log on the Mac.")
                    self.s.save()
                return
            if not rows:
                break
            after = (rows[-1][3], rows[-1][0])
            # sent_at is in milliseconds (the sender's clock), as the cloud's rn-fetch.py also reads it
            msgs = [{"id": i, "from": frm, "agent": agent, "thread": thread, "text": body}
                    for i, frm, body, sent, agent, thread in rows if i not in self.s.seen]
            if msgs:
                found += len(msgs)
                sample += [str(m["id"]) for m in msgs[:20 - len(sample)]]
                self.handle(msgs)
        if found:
            self.log.info("network.db: handled %d message(s) that listen had marked read: %s", found,
                          ", ".join(sample) + (" ..." if found > len(sample) else ""))

    def db_page(self, path, floor_ms, snap, after):
        """(snap, rows): up to RECONCILE_PAGE rows to this relay, sent at or after floor_ms, with rowid <= snap,
        after the (sent_at, id) `after`, excluding rows the engine holds for inbound approval (held = 1; no such
        column: nothing held). snap None: take max(rowid) now, on this connection (the engine's messages table is
        a plain rowid table, so rows inserted later get a larger rowid)."""
        db = sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True, timeout=5)
        try:
            cols = {r[1] for r in db.execute("PRAGMA table_info(messages)")}
            need = {"id", "sender", "body", "sent_at", "recipient"}
            if not need <= cols:
                raise LookupError(f"table messages lacks {', '.join(sorted(need - cols))}")
            agent = "sender_agent" if "sender_agent" in cols else "NULL"
            thread = "reply_to" if "reply_to" in cols else "NULL"
            if snap is None:
                snap = db.execute("SELECT max(rowid) FROM messages").fetchone()[0]
                if snap is None:
                    return None, []        # empty table
            sql = (f"SELECT id, sender, body, sent_at, {agent}, {thread} FROM messages "
                   "WHERE recipient = ? AND sent_at >= ? AND rowid <= ?")
            args = [self.me, floor_ms, snap]
            if "held" in cols:
                sql += " AND COALESCE(held, 0) = 0"
            if after is not None:
                sql += " AND (sent_at > ? OR (sent_at = ? AND id > ?))"
                args += [after[0], after[0], after[1]]
            return snap, db.execute(sql + " ORDER BY sent_at, id LIMIT ?", args + [RECONCILE_PAGE]).fetchall()
        finally:
            db.close()

    def can_wake(self):
        now = time.time()
        recent = [t for t in self.s.wakes if now - t < 3600]
        if len(recent) >= self.c["MAX_WAKES_PER_HOUR"]:
            return False, 3600 - (now - recent[0])
        if recent and now - recent[-1] < self.c["MIN_SECONDS_BETWEEN_WAKES"]:
            return False, self.c["MIN_SECONDS_BETWEEN_WAKES"] - (now - recent[-1])
        return True, 0

    def flush(self):
        ready = self.ready_alerts()
        if not self.s.spool and not ready:
            return 600
        ok, wait = self.can_wake()
        if not ok:
            return max(5, int(wait))
        use_alerts = bool(ready) and (not self.s.spool or self.s.last_batch == "spool")
        if use_alerts:
            batch, chosen, sent_n = self.alert_batch(ready)
        else:
            batch = self.next_batch()
        body = json.dumps(self.payload(batch), ensure_ascii=False)
        data = body.encode()
        req = urllib.request.Request(self.c["WEBHOOK_URL"], data=data, method="POST", headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.c['WEBHOOK_KEY']}",
            "X-Automation-Key": self.c["WEBHOOK_KEY"],
        })
        self.s.wakes.append(time.time())
        try:
            with webhook_open(req, timeout=15) as resp:
                code = resp.status
        except urllib.error.HTTPError as e:
            code = e.code
            if e.fp is not None:
                e.close()
        except Exception as e:
            code = f"error {type(e).__name__}"
        self.log.info("POST %d chars %d bytes sha256=%s code=%s items=[%s]", len(body), len(data),
                      hashlib.sha256(data).hexdigest(), code, ", ".join(str(i["id"]) for i in batch))
        self.s.last_batch = "alerts" if use_alerts else "spool"
        if isinstance(code, int) and 200 <= code < 300 and use_alerts:
            now = time.time()
            for a in chosen:
                if a["posted"] is not None:
                    a["tries"] += 1
                a["batch"], a["posted"] = batch[0]["id"], now
                if a["key"] == ALERT_OVERFLOW:
                    a["sent_n"] = sent_n
            self.log.info("woke Grok Bot with %d alert(s) in %s, waiting for its confirmation", len(chosen),
                          batch[0]["id"])
        elif isinstance(code, int) and 200 <= code < 300:
            if any(i.get("id") == "relay-spool-overflow" for i in batch):
                self.s.overflow_dropped = 0      # the overflow notice was delivered; count afresh
            self.s.spool = self.s.spool[len(batch):]
            self.rung(batch)
            self.log.info("woke Grok Bot with %d item(s)", len(batch))
        elif use_alerts:
            self.log.warning("webhook POST failed: %s; %d alert(s) kept", code, len(chosen))
        else:
            self.log.warning("webhook POST failed: %s; %d item(s) kept in spool", code, len(self.s.spool))
        if isinstance(code, int) and 300 <= code < 400:
            self.log.warning("webhook answered %d; redirects are not followed (the key is sent only to WEBHOOK_URL "
                             "itself); set WEBHOOK_URL to the final address", code)
        self.s.save()
        return 5 if self.s.spool or self.ready_alerts() else 600

    def rung(self, batch):
        now, gp = time.time(), self.s.got_probe
        for it in batch:
            p = self.s.pending.get(str(it.get("id"))) if it.get("doorbell") else None
            if p is None:
                continue
            if p["stage"] == "ringing":
                p["stage"] = "unacked"
            p["t"], p["rung"] = now, True
            if gp and gp.get("id") == str(it["id"]):
                gp["posted"] = now

    def ready_alerts(self):
        now, resend, maxr = time.time(), self.unacked_timeout(), self.c.get("GOT_MAX_RERINGS", 2)
        return [a for a in self.s.alerts if (a["key"] != ALERT_OVERFLOW or a.get("n", 0) > 0) and (
            a["posted"] is None or (now - a["posted"] > resend and a["tries"] < maxr))]

    def alert_text(self, a):
        if a["key"] == ALERT_OVERFLOW:
            return (f"{a.get('n', 0)} more relay alert(s) were not queued one by one; see the ALERT lines in the "
                    f"relay log on the Mac.")
        return a["text"]

    def alert_batch(self, ready):
        """One notice item relay-alerts-<ms> with as many ready alerts as fit (each cut to 300 characters, the
        item's text to 2000, the body to WEBHOOK_MAX_BODY_CHARS); at least one, cut further if needed."""
        bid, limit = f"{ALERT_PREFIX}{int(time.time() * 1000)}", self.c["WEBHOOK_MAX_BODY_CHARS"]
        texts, chosen = [], []
        for a in ready:
            t = "\n".join(texts + [self.alert_text(a)[:ALERT_ITEM_CHARS]])
            if len(t) > 2000 or self.body_chars([notice_item(bid, t)]) > limit:
                break
            texts.append(self.alert_text(a)[:ALERT_ITEM_CHARS])
            chosen.append(a)
        if not chosen:
            t = self.alert_text(ready[0])[:ALERT_ITEM_CHARS]
            while t and self.body_chars([notice_item(bid, t)]) > limit:
                t = t[:-10]
            texts, chosen = [t], [ready[0]]
        sent_n = next((a.get("n", 0) for a in chosen if a["key"] == ALERT_OVERFLOW), 0)
        return [notice_item(bid, "\n".join(texts))], chosen, sent_n

    def next_batch(self):
        """Spool head plus following items of the same kind (doorbell or notice), in order, up to MAX_BATCH,
        while the whole body stays <= WEBHOOK_MAX_BODY_CHARS."""
        limit, sp = self.c["WEBHOOK_MAX_BODY_CHARS"], self.s.spool
        batch = sp[:1]
        kind = bool(sp[0].get("doorbell"))
        for it in sp[1: self.c["MAX_BATCH"]]:
            if bool(it.get("doorbell")) != kind or self.body_chars(batch + [it]) > limit:
                break
            batch.append(it)
        return batch

    def run(self):
        self.log.info("agent-relay up: listening as %s, proxying for %s, forwarding to %s, copies kept %d days",
                      self.me, self.cloud, self.fwd_to, self.c.get("FWD_COPY_DAYS", 7))
        self.reconcile()                   # messages marked read before a crash, before anything new
        self.check_pending()
        wait = self.next_wait(self.flush())   # deliver anything restored from the spool before the first listen
        while True:
            try:
                r = self.rn("listen", "--as", self.c["LISTEN_NAME"], "--wait", str(wait), timeout=wait + 60)
            except subprocess.TimeoutExpired:
                self.log.warning("listen timed out after %d s; retrying", wait + 60)
                r = None
            except Exception:
                self.log.exception("listen failed")
                time.sleep(15)
                r = None
            if r is None or r.returncode != 0:
                if r is not None:
                    self.log.warning("listen rc=%s: %s", r.returncode, (r.stderr or r.stdout).strip()[:300])
                    time.sleep(15)
                self.reconcile()           # listen may have marked a batch read before it failed
            else:
                try:
                    self.handle(parse(r.stdout))
                except Exception:          # each message is isolated in handle(); this is the final save()
                    self.log.exception("could not save the handled batch")
            try:
                self.check_pending()
            except Exception:
                self.log.exception("could not check pending messages")
            wait = self.next_wait(self.flush())


def main():
    os.umask(0o077)   # everything the relay creates (log and its rotations, spool, copies) stays private to this user
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3)
    for p in [LOG_FILE, *LOG_FILE.parent.glob(LOG_FILE.name + ".*")]:   # logs written by earlier versions may be 644
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger("agent-relay").addHandler(h)
    logging.getLogger("agent-relay").setLevel(logging.INFO)
    Relay(load_env()).run()


if __name__ == "__main__":
    main()
