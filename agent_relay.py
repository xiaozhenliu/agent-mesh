#!/usr/bin/env python3
"""agent-relay: the always-on Mac side of agent-mesh. Runs under launchd.

Inbound  (local agents -> Grok Bot): loops `reason-network listen --as bot`. Each message for <team-name>/bot is
          forwarded in full over Reason Network to <team-name>/bot-inbox@cloud as a signed RN-FWD v1 message
          (sent as <team-name>/terminal, which the engine's agent-loop guard never refuses). Then the routine
          webhook is rung with a small doorbell: {id, from, agent, thread, text_len, text_sha256, preview}.
          The routine fetches the full text by id with `rn-cloud.sh fetch` and verifies it.
Outbound (Grok Bot -> local agents): messages from <team-name>/bot-cloud that carry a valid RN-OUT v1 envelope
          (HMAC, 15-minute window, unused nonce) are re-sent here as <team-name>/bot, so replies always land on
          this machine, never on the sleeping cloud box.
Re-forward: a copy of every forwarded text is kept for FWD_COPY_DAYS (dir 0700, files 0600). A signed
          RN-REFWD v1 from bot-cloud or bot-inbox makes the relay send the copy again and ring again, at most
          REFWD_MAX_PER_ID per message and REFWD_MAX_PER_HOUR overall, answered with a signed RN-REFWD-ACK v1.
          If the first forward fails, the doorbell still rings (flagged forward_failed) and the cloud's fetch
          asks for the copy the same way.
Notices: the relay's own messages to the routine (refused envelope, spool overflow) are doorbell:false items
          with the text inline. They never share a POST with doorbells.

Standard library only. Config: ~/.config/agent-relay/env (mode 0600). Never logs secrets or message bodies.
Message formats: README.md, appendix.
"""
import hashlib, hmac, html, json, logging, logging.handlers, os, re, subprocess, sys, time, urllib.request
from pathlib import Path

HOME = Path.home()
ENV_FILE = Path(os.environ.get("AGENT_RELAY_ENV", HOME / ".config/agent-relay/env"))
STATE_DIR = Path(os.environ.get("AGENT_RELAY_STATE", HOME / "Library/Application Support/agent-relay"))
LOG_FILE = Path(os.environ.get("AGENT_RELAY_LOG", HOME / "Library/Logs/agent-relay.log"))

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
                 ("REFWD_MAX_PER_HOUR", 20)]:
        cfg[k] = int(cfg.get(k, d))
    cfg["WEBHOOK_MAX_BODY_CHARS"] = max(600, cfg["WEBHOOK_MAX_BODY_CHARS"])
    return cfg


def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def notice_item(mid, text):
    """A relay notice for the routine: a doorbell:false item with the text inline."""
    return {"id": mid, "from": "relay", "agent": "agent-relay", "thread": None, "text": text[:2000]}


FWD_MAX_BYTES = 60 * 1024          # engine limit is 64 KB per message body; keep room for the header
FWD_TRAILER = "\n--- RN-FWD END"
AGENT_ENV = ("REASON_NETWORK_AGENT", "REASON_NETWORK_SESSION", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID",
             "CODEX_THREAD_ID", "CODEX_SESSION_ID", "GEMINI_CLI", "CURSOR_AGENT", "OPENCODE", "COPILOT_CLI",
             "COPILOT_AGENT_SESSION_ID")


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
            f"text_sha256: {h}\nts: {ts}\nsig: {sig}\n---\n{text}{FWD_TRAILER}")


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
        self.s = State(cfg["MAX_SPOOL"])
        self.me = f"{cfg['PERSON']}/{cfg['LISTEN_NAME']}"
        self.cloud = f"{cfg['PERSON']}/{cfg['CLOUD_NAME']}"
        self.inbox = f"{cfg['PERSON']}/{cfg.get('INBOX_NAME', 'bot-inbox')}"
        self.fwd_to = f"{self.inbox}@{cfg.get('CLOUD_MACHINE', 'cloud')}"
        self.log = logging.getLogger("agent-relay")
        self.copies = Copies(cfg.get("FWD_COPY_DAYS", 7))
        self.last_prune = 0

    # ---- webhook payloads ----
    def payload(self, items):
        if items and all(i.get("doorbell") for i in items):
            keep = ("id", "from", "agent", "thread", "text_len", "text_sha256", "preview", "refwd", "forward_failed")
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
        bad = re.search(r"refused|held|not on this machine|no session|nobody matches|ambiguous|error", res, re.I)
        if r.returncode != 0 or bad or "→" not in res:
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
        item = self.doorbell_item(copy)
        item["refwd"] = attempt
        self.s.spool.append(item)
        self.log.info("refwd %s attempt %d requested by %s (%s): re-forwarded, doorbell queued", mid, attempt,
                      frm, m["id"])
        self.ack(mid, "resent", attempt)

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
    def proxy(self, m):
        env = parse_envelope(m["text"])
        now = time.time()
        if not env:
            return self.notice(f"bot-cloud sent {m['id']} without a valid RN-OUT envelope; not forwarded")
        good = hmac.compare_digest(env["sig"], sign(self.c["OUTBOUND_KEY"], env["to"], env["reply_to"],
                                                    env["ts"], env["nonce"], env["body"]))
        try:
            fresh = abs(now - int(env["ts"])) < 900
        except ValueError:
            fresh = False
        if not good or not fresh or env["nonce"] in self.s.nonces:
            self.log.warning("proxy refused %s (sig=%s fresh=%s)", m["id"], good, fresh)
            return self.notice(f"outbound {m['id']} refused: bad signature, stale or replayed")
        if len(self.s.proxies) >= self.c["MAX_PROXY_PER_HOUR"]:
            return self.notice(f"outbound {m['id']} refused: over {self.c['MAX_PROXY_PER_HOUR']} sends/hour")
        to = env["to"]
        if to in ("*",) or to.endswith("/*") or to.startswith("repo:") or to in (self.me, self.cloud):
            return self.notice(f"outbound {m['id']} refused: group or self address '{to}'")
        self.s.nonces[env["nonce"]] = now
        self.s.proxies.append(now)
        args = ["send", to, "--from", self.me]
        if env["reply_to"]:
            args += ["--reply-to", env["reply_to"]]
        args.append("-")  # body from stdin
        r = self.rn(*args, stdin=env["body"])
        result = (r.stdout + r.stderr).strip()
        self.log.info("proxied %s -> %s rc=%s %s", m["id"], to, r.returncode, result.splitlines()[0] if result else "")
        if r.returncode != 0 or "refused" in result:
            self.notice(f"outbound to {to} failed: {result[:500]}")

    def notice(self, text):
        self.s.spool.append(notice_item(f"relay-{int(time.time() * 1000)}", text))

    # ---- inbound (network -> Grok Bot) ----
    def handle(self, msgs):
        for m in msgs:
            if m["id"] in self.s.seen:
                continue
            self.s.seen.append(m["id"])
            if is_refwd(m.get("text")):
                self.refwd(m)           # allowed senders are honoured; anyone else is logged and ignored
                continue
            if m["from"] == self.cloud:
                self.proxy(m)
                continue
            if m["from"] == self.me:
                continue
            self.keep_copy(m)
            ok, why = self.forward(m)
            item = self.doorbell_item(m)
            if not ok:
                # ring anyway: the cloud's fetch will ask for the kept copy with a signed RN-REFWD
                item["forward_failed"] = why[:80]
                self.log.warning("forward %s failed (%s); doorbell flagged, copy kept for re-forward", m["id"], why)
            self.s.spool.append(item)
        self.s.save()

    def can_wake(self):
        now = time.time()
        recent = [t for t in self.s.wakes if now - t < 3600]
        if len(recent) >= self.c["MAX_WAKES_PER_HOUR"]:
            return False, 3600 - (now - recent[0])
        if recent and now - recent[-1] < self.c["MIN_SECONDS_BETWEEN_WAKES"]:
            return False, self.c["MIN_SECONDS_BETWEEN_WAKES"] - (now - recent[-1])
        return True, 0

    def flush(self):
        if not self.s.spool:
            return 600
        ok, wait = self.can_wake()
        if not ok:
            return max(5, int(wait))
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
            with urllib.request.urlopen(req, timeout=15) as resp:
                code = resp.status
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception as e:
            code = f"error {type(e).__name__}"
        self.log.info("POST %d chars %d bytes sha256=%s code=%s items=[%s]", len(body), len(data),
                      hashlib.sha256(data).hexdigest(), code, ", ".join(str(i["id"]) for i in batch))
        if code == 200:
            if any(i.get("id") == "relay-spool-overflow" for i in batch):
                self.s.overflow_dropped = 0      # the overflow notice was delivered; count afresh
            self.s.spool = self.s.spool[len(batch):]
            self.log.info("woke Grok Bot with %d item(s)", len(batch))
        else:
            self.log.warning("webhook POST failed: %s; %d item(s) kept in spool", code, len(self.s.spool))
        self.s.save()
        return 5 if self.s.spool else 600

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
        wait = min(600, self.flush())      # deliver anything restored from the spool before the first listen
        while True:
            try:
                r = self.rn("listen", "--as", self.c["LISTEN_NAME"], "--wait", str(wait), timeout=wait + 60)
                if r.returncode != 0:
                    self.log.warning("listen rc=%s: %s", r.returncode, (r.stderr or r.stdout).strip()[:300])
                    time.sleep(15)
                else:
                    self.handle(parse(r.stdout))
            except subprocess.TimeoutExpired:
                self.log.warning("listen timed out; retrying")
            except Exception:
                self.log.exception("loop error")
                time.sleep(15)
            wait = min(600, self.flush())


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
