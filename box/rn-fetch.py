#!/usr/bin/env python3
"""rn-fetch: read doorbell messages' full text on the box, byte-exact, from the engine's own database.

The Mac relay (relay_version 3) sends each inbound message's full text to <team-name>/<inbox> on this box as a
signed "RN-FWD v1" Reason Network message, then rings the routine's webhook with just the ids. The engine
stores what arrives in $REASON_NETWORK_HOME/network.db (table messages, kept even after it is marked read);
`listen` would print it rewritten (it defangs <reason-message> tags), so this reads the database read-only.

  rn-fetch.py scan [ID...]                 one pass (diagnostic: writes the inbox folder, claims nothing)
  rn-fetch.py wait [--timeout S] [--got] [ID...]
                                           poll until every ID is complete or mismatched (default 120 s)
  rn-fetch.py fetch [--timeout S] [--refwd-timeout S] [--got] ID...
                                           wait; then, for ids still missing, ask the Mac relay once to
                                           re-forward its kept copy (RN-REFWD v1, signed) and wait again
  rn-fetch.py working|replying|done ID... [--via send]
                                           record that a turn started work on / started a reply to /
                                           finished these ids; waits up to RN_DONE_LOCK_WAIT (default
                                           300 s) for the inbox lock and exits 1 without writing if it cannot
  rn-fetch.py send-status                  read an `rn send` output on stdin, print only the engine's status words
                                           (no addresses), exit 0; reads no credentials, takes no lock
  rn-fetch.py purge DAYS                   delete received messages, inbox folders, stray logs, remembered
                                           bad rows and thread notes older than DAYS (0 = all); the engine
                                           must be stopped
With no IDs (scan/wait): every RN-FWD message not yet written to the inbox folder.

Re-forward (fetch only, relay v3.1): Reason Network 0.10.5 can silently drop a message after `send` said
queued, so a missing id gets ONE signed RN-REFWD request per fetch run, sent as <team-name>/<RN_CLOUD_NAME> to
<team-name>/<RN_RELAY_NAME>, and at most RN_REFWD_MAX (default 2) per id in total (counted in
$RN_INBOX_DIR/_refwd/<id>.json), so it can never loop. The relay answers with a signed RN-REFWD-ACK v1
(resent|no-copy|limit|failed); results then carry "refwd": {"attempt": N, "ack": status|null} or
{"skipped": reason}. ACK messages are never reported as mismatches.

Claims (wait/fetch): just before printing, each complete message is claimed for one turn through marker files
in $RN_INBOX_DIR/<id>/ (claimed, working, replying, done, legacy; written atomically). A claim lasts
RN_CLAIM_LEASE seconds (default 3600), counted from the marker file's mtime.
Orphans (wait/fetch; RN_ORPHANS=0 turns this off): verified messages from the last RN_ORPHAN_DAYS days
(default 7) that no doorbell asked for, with no done or legacy marker and no claimed/working/replying marker
within the lease, are added with "orphan": true. A bad row found this way, or by wait/fetch without ids, is
reported once per id (remembered in $RN_INBOX_DIR/_mismatch/<row id>); ids asked for explicitly, and the
diagnostic scan, report it every time.
Upgrade: the first locked run marks every existing message folder without markers "legacy" (skipped by the
orphan scan, still handed out when a doorbell asks for it), then writes $RN_INBOX_DIR/_claims_v1.
Receipts (RN_GOT=0 turns them off): the relay adds "caps: got1" to RN-FWD (kept in meta.json "caps"). With
--got (the Mac is connected), wait/fetch send one signed RN-GOT v1 to <team-name>/<RN_RELAY_NAME> after
printing: "stored" for complete, claimed and uncertain, "done" for handled, only for ids whose RN-FWD carried
got1, at most 100 ids per message; those results carry "got": "stored"|"done". `done` (not --via send) sends
a done-only RN-GOT for such ids, and always for relay-alerts-* notice batches. After sending, it waits up to
30 s for the box's outbox to empty. RN-REFWD carries "caps: got1" too, so the relay rings no extra doorbell.

Output: one JSON line per message: status complete (claimed by this run: "claim" new, reclaimed or legacy;
full = path of full.txt, verified against the signed text_len/text_sha256), handled (has done; no full),
claimed (another turn holds it within the lease; no full), uncertain (a turn started work or a reply and
never recorded done, and the lease is over; full kept, plus working_at/replying_at), missing (not on this box
yet), or mismatch (reason: bad signature, length/hash differs, malformed, id collision). Exit 5 if any
mismatch, else 4 if any missing, else 0 (handled, claimed and uncertain do not change it).
Writes $RN_INBOX_DIR/<id>/full.txt and meta.json.
Env: REASON_NETWORK_HOME, RN_PERSON, RN_INBOX_NAME (default <name>-inbox), RN_KEY_FILE, RN_INBOX_DIR,
RN_CLAIM_LEASE, RN_ORPHANS, RN_ORPHAN_DAYS, RN_DONE_LOCK_WAIT, RN_GOT (1; 0 = off); for fetch and receipts also
RN_BIN, RN_CLOUD_NAME (<name>-cloud), RN_RELAY_NAME (<name>), RN_REFWD (1; 0 = off), RN_REFWD_MAX.
<name> is the Bot address name in $RN_BOX/relay (written by rn-cloud-setup.sh; bot if the file does not exist). It
is read only if one of those three is unset; an empty or damaged file stops scan/wait/fetch and done.
Standard library only.
"""
import hashlib, hmac, importlib.util, json, os, re, shutil, sqlite3, subprocess, sys, time
from pathlib import Path

BASE = Path(os.environ.get("RN_INBOX_DIR", "/workspace/agent-mesh/inbox"))
HOME = Path(os.environ.get("REASON_NETWORK_HOME", "/workspace/agent-mesh/box/state"))
TRAILER = "\n--- RN-FWD END"
ACK_HEAD = "RN-REFWD-ACK v1\n"
CAPS = "got1"
GOT_ID = re.compile(r"[A-Za-z0-9._-]{1,120}")
GOT_MAX_IDS = 100
GOT_FROM = None
SEND_REFUSED = re.compile(r"refused|not on this machine|no session|nobody matches|ambiguous", re.I)
SEND_UNSURE = re.compile(r"held|error", re.I)
SEND_ACCEPTED = "→"
RELAY_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
RESERVED_NAME = re.compile(r"terminal(?:-[0-9]+)?")
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


def sha(t):
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def out(d):
    print(json.dumps(d, ensure_ascii=False), flush=True)


def safe(mid):
    s = re.sub(r"[^A-Za-z0-9._-]", "_", mid)[:120].lstrip(".")
    return s or "_"


def read(p):
    with open(p, encoding="utf-8", newline="") as fh:            # no newline translation (\r\n stays)
        return fh.read()


def write(p, text):
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:   # keep \r exactly
        fh.write(text)
    tmp.replace(p)


def sign_fwd(key, mid, frm, agent, thread, text_len, text_sha, ts):
    msg = "\n".join(["fwd1", mid, frm, agent, thread, str(text_len), text_sha, ts]).encode()
    return hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()


def sign_refwd(key, mid, ts, nonce):
    return hmac.new(key.encode(), "\n".join(["refwd1", mid, ts, nonce]).encode(), hashlib.sha256).hexdigest()


def receipts_on():
    return os.environ.get("RN_GOT", "1") != "0"


def refwd_body(key, mid, ts=None, nonce=None):
    ts = str(int(time.time())) if ts is None else str(ts)
    nonce = nonce or os.urandom(12).hex()
    body = f"RN-REFWD v1\nid: {mid}\nts: {ts}\nnonce: {nonce}\nsig: {sign_refwd(key, mid, ts, nonce)}"
    return body + f"\ncaps: {CAPS}" if receipts_on() else body


def sign_got(key, stored, done, ts, nonce):
    return hmac.new(key.encode(), "\n".join(["got1", stored, done, ts, nonce]).encode(), hashlib.sha256).hexdigest()


def got_body(key, stored, done, ts=None, nonce=None):
    stored, done = ",".join(stored), ",".join(done)
    ts = str(int(time.time())) if ts is None else str(ts)
    nonce = nonce or os.urandom(12).hex()
    return (f"RN-GOT v1\nstored: {stored}\ndone: {done}\nts: {ts}\nnonce: {nonce}\n"
            f"sig: {sign_got(key, stored, done, ts, nonce)}")


def caps_of(f):
    return sorted({c.strip() for c in ((f or {}).get("caps") or "").split(",") if c.strip()})


def merge_caps(d, caps):
    """Adds caps from a later RN-FWD of the same id to an existing meta.json (the body is not rewritten)."""
    if not caps:
        return
    mp = d / "meta.json"
    try:
        m = json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return
    have = m.get("caps") if isinstance(m.get("caps"), list) else []
    new = sorted(set(have) | set(caps))
    if new != have:
        m["caps"], m["updated"] = new, time.time()
        write(mp, json.dumps(m, ensure_ascii=False))


def wants_receipt(oid, d=None):
    """True if an RN-FWD of oid declared got1 (meta.json in its result folder or its main folder)."""
    for folder in ([Path(d)] if d else []) + [BASE / safe(oid)]:
        try:
            if CAPS in json.loads((folder / "meta.json").read_text(encoding="utf-8")).get("caps", []):
                return True
        except Exception:
            pass
    return False


def parse_ack(body, key):
    """Signed RN-REFWD-ACK v1 -> {id, status, attempt, reason, ts} or None (bad/forged acks are ignored)."""
    f = {}
    for line in body.split("\n")[1:]:
        k, _, v = line.partition(":")
        f[k.strip()] = v.strip()
    if not all(f.get(k) for k in ("id", "status", "attempt", "ts", "sig")):
        return None
    msg = "\n".join(["refwdack1", f["id"], f["status"], f["attempt"], f["ts"]]).encode()
    if not hmac.compare_digest(f["sig"], hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()):
        return None
    try:
        f["ts"] = int(f["ts"])
    except ValueError:
        return None
    return {k: f.get(k) for k in ("id", "status", "attempt", "reason", "ts")}


def parse(body, key):
    """(fields, text, None) or (fields_or_None, None, reason)."""
    if not body.startswith("RN-FWD v1\n"):
        return None, None, "not an RN-FWD v1 message"
    head, sep, rest = body.partition("\n---\n")
    f = {}
    for line in head.split("\n")[1:]:
        k, _, v = line.partition(":")
        f[k.strip()] = v[1:] if v.startswith(" ") else v
    need = ("id", "from", "agent", "thread", "text_len", "text_sha256", "ts", "sig")
    if not sep or any(k not in f for k in need):
        return f or None, None, "malformed header"
    if not rest.endswith(TRAILER):
        return f, None, "trailer missing (body cut off?)"
    text = rest[: len(rest) - len(TRAILER)]
    good = hmac.compare_digest(f["sig"], sign_fwd(key, f["id"], f["from"], f["agent"], f["thread"],
                                                  f["text_len"], f["text_sha256"], f["ts"]))
    if not good:
        return f, None, "bad signature (not from the relay, or a different key)"
    if str(len(text)) != f["text_len"] or sha(text) != f["text_sha256"]:
        return f, None, f"length/hash mismatch: got {len(text)} chars, header says {f['text_len']}"
    return f, text, None


def rows(person, inbox):
    db = sqlite3.connect(f"file:{HOME / 'network.db'}?mode=ro", uri=True, timeout=5)
    try:
        return db.execute("SELECT id, sender, body, sent_at FROM messages WHERE recipient = ? ORDER BY sent_at, id",
                          (f"{person}/{inbox}",)).fetchall()
    finally:
        db.close()


def store(f, text, rn_id, rn_from, sent_at):
    d = BASE / safe(f["id"])
    mp = d / "meta.json"
    if mp.exists():
        m = json.loads(mp.read_text(encoding="utf-8"))
        if m.get("text_sha256") != f["text_sha256"]:
            d = BASE / f"{safe(f['id'])}-{f['text_sha256'][:8]}"
        elif (d / "full.txt").exists() and sha(read(d / "full.txt")) == f["text_sha256"]:
            merge_caps(d, caps_of(f))
            return d, True
    d.mkdir(parents=True, exist_ok=True)
    write(d / "full.txt", text)
    meta = {"id": f["id"], "from": f["from"], "agent": f["agent"] or None, "thread": f["thread"] or None,
            "parts": 1, "text_len": len(text), "text_sha256": f["text_sha256"], "via": "rn-fwd",
            "rn_msg_id": rn_id, "rn_from": rn_from, "sent_at": sent_at, "first_seen": time.time(),
            "updated": time.time(), "caps": caps_of(f)}
    write(d / "meta.json", json.dumps(meta, ensure_ascii=False))
    return d, False


def scan(ids, key, person, inbox, quiet_missing=False, acks=None):
    """Returns {id: result} for the wanted ids (or every unwritten RN-FWD message when ids is empty).
    acks, if a dict, is filled with the newest valid RN-REFWD-ACK per id."""
    res, mism = {}, {}
    for rn_id, rn_from, body, sent_at in rows(person, inbox):
        if body.startswith(ACK_HEAD):
            a = parse_ack(body, key)
            if a is not None and acks is not None and (a["id"] not in acks or a["ts"] >= acks[a["id"]]["ts"]):
                acks[a["id"]] = a
            continue
        f, text, err = parse(body, key)
        oid = (f or {}).get("id") or f"rn:{rn_id}"
        if ids and oid not in ids:
            continue
        if err:
            mism.setdefault(oid, {"id": oid, "status": "mismatch", "reason": err, "rn_msg_id": rn_id,
                                  "rn_from": rn_from})
            continue
        if oid in res:
            merge_caps(Path(res[oid]["dir"]), caps_of(f))
            continue                       # a resend of something already handled in this pass
        d, already = store(f, text, rn_id, rn_from, sent_at)
        if not ids and already:
            continue
        res[oid] = {"id": oid, "status": "complete", "from": f["from"], "agent": f["agent"] or None,
                    "thread": f["thread"] or None, "text_len": len(text), "text_sha256": f["text_sha256"],
                    "full": str(d / "full.txt"), "dir": str(d), "already": already, "verified": True}
    for oid, r in mism.items():
        res.setdefault(oid, r)             # a valid copy beats a bad one
    for oid in ids:
        res.setdefault(oid, {"id": oid, "status": "missing",
                             "reason": "not on this box yet (Mac asleep/offline, or still in its outbox)"})
    return res


def wait(ids, key, person, inbox, timeout, acks=None, since=None):
    """Polls until no id is missing or the deadline. With since, an id also stops counting as pending once a
    non-"resent" ACK newer than since arrived (the relay cannot help, so waiting longer is pointless)."""
    deadline = time.time() + timeout
    while True:
        res = scan(ids, key, person, inbox, acks=acks)
        pending = [i for i, r in res.items() if r["status"] == "missing" and not
                   (since is not None and acks and i in acks and acks[i]["ts"] >= since
                    and acks[i]["status"] != "resent")]
        if not ids or not pending or time.time() >= deadline:
            return res
        time.sleep(2)


def outbox_count():
    db = sqlite3.connect(f"file:{HOME / 'network.db'}?mode=ro", uri=True, timeout=5)
    try:
        return db.execute("select count(*) from outbox").fetchone()[0]
    finally:
        db.close()


def relay_name_ok(name):
    return bool(RELAY_NAME.fullmatch(name)) and len(name) <= 34 and not RESERVED_NAME.fullmatch(name)


def names():
    given = [os.environ.get(k) for k in ("RN_RELAY_NAME", "RN_CLOUD_NAME", "RN_INBOX_NAME")]
    if all(given):
        return tuple(given)
    path = Path(os.environ.get("RN_BOX", "/workspace/agent-mesh/box")) / "relay"
    try:
        base = path.read_text(encoding="utf-8", errors="replace").rstrip("\n")
    except FileNotFoundError:
        base = "bot"
    except OSError as e:
        raise SystemExit(f"rn-fetch: cannot read {path}: {e.strerror or e}")
    else:
        if not relay_name_ok(base):
            raise SystemExit(f"rn-fetch: {path} is damaged; it must hold the Bot address name the Mac uses "
                             "(README: Troubleshooting)")
    return tuple(g or d for g, d in zip(given, (base, f"{base}-cloud", f"{base}-inbox")))


def rn_bin():
    if os.environ.get("RN_BIN"):
        return os.environ["RN_BIN"]
    spec = importlib.util.spec_from_file_location("rn_engine", Path(__file__).resolve().with_name("rn-engine.py"))
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    try:
        return engine.cloud_bin(Path(os.environ.get("RN_BOX", "/workspace/agent-mesh/box")))
    except engine.Failed as e:
        raise SystemExit(f"rn-fetch: {e}")


def engine_env(base=None):
    env = dict(os.environ if base is None else base)
    env.update(REASON_NETWORK_HOME=str(HOME), REASON_NETWORK_NO_UPDATE="1")
    return env


def send_got(stored, done, key, person):
    """Sends signed RN-GOT v1 receipts (at most GOT_MAX_IDS ids each) to the Mac relay, then waits up to 30 s
    for the outbox to empty. Returns a list of errors (empty: all handed to the engine)."""
    done = [i for i in dict.fromkeys(done) if GOT_ID.fullmatch(i)]
    stored = [i for i in dict.fromkeys(stored) if GOT_ID.fullmatch(i) and i not in done]
    items = [("stored", i) for i in stored] + [("done", i) for i in done]
    to = f"{person}/{names()[0]}"
    args = [rn_bin(), "send", to] + (["--from", f"{person}/{GOT_FROM}"] if GOT_FROM else []) + ["-"]
    env = engine_env({k: v for k, v in os.environ.items() if k not in AGENT_ENV})
    sent, errors = 0, []
    for k in range(0, len(items), GOT_MAX_IDS):
        part = items[k:k + GOT_MAX_IDS]
        body = got_body(key, [i for kind, i in part if kind == "stored"], [i for kind, i in part if kind == "done"])
        try:
            r = subprocess.run(args, input=body, capture_output=True, text=True, timeout=60, env=env)
        except Exception as e:
            errors.append(f"send failed: {type(e).__name__}")
            continue
        res = (r.stdout + r.stderr).strip()
        if send_outcome(r.returncode, res) != "sent":
            errors.append(f"send rc={r.returncode}: {res.splitlines()[0][:80] if res else ''}")
            continue
        sent += 1
    for e in errors:
        print(f"rn-fetch: receipt (RN-GOT) not sent: {e}", file=sys.stderr)
    if sent:
        try:
            for _ in range(15):
                if outbox_count() == 0:
                    break
                time.sleep(2)
            else:
                print("rn-fetch: the receipt is still queued on the box after 30 s; it goes out the next time the "
                      "box is awake and connected", file=sys.stderr)
        except Exception:
            pass
    return errors


def credentials():
    key = Path(os.environ.get("RN_KEY_FILE", "/workspace/agent-mesh/box/outbound.key")).read_text().strip()
    person = os.environ.get("RN_PERSON") or (Path(os.environ.get("RN_BOX", "/workspace/agent-mesh/box")) / "person").read_text().strip()
    return key, person


def refwd_count_file(mid):
    d = BASE / "_refwd"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{safe(mid)}.json"


def request_refwd(mid, key, person):
    """Sends one signed RN-REFWD for mid. Returns (attempt, None) or (None, reason it was not sent)."""
    cap = int(os.environ.get("RN_REFWD_MAX", "2"))
    cf = refwd_count_file(mid)
    try:
        done = json.loads(cf.read_text())
    except Exception:
        done = []
    if len(done) >= cap:
        return None, f"box limit: {len(done)} re-forward request(s) already sent for this id"
    rn = rn_bin()
    relay, cloud, _ = names()
    done.append(time.time())
    write(cf, json.dumps(done))            # count before sending: a crash never allows extra requests
    try:
        r = subprocess.run([rn, "send", f"{person}/{relay}", "--from", f"{person}/{cloud}", "-"],
                           input=refwd_body(key, mid), capture_output=True, text=True, timeout=60,
                           env=engine_env())
    except Exception as e:
        return None, f"send failed: {type(e).__name__}"
    res = (r.stdout + r.stderr).strip()
    if send_outcome(r.returncode, res) != "sent":
        return None, f"send rc={r.returncode}: {res.splitlines()[0][:80] if res else ''}"
    return len(done), None


def fetch(ids, key, person, inbox, timeout, refwd_timeout):
    acks = {}
    res = wait(ids, key, person, inbox, timeout, acks=acks)
    missing = [i for i, r in res.items() if r["status"] == "missing"]
    if not missing or os.environ.get("RN_REFWD", "1") == "0":
        return res
    started, asked, notes = time.time() - 1, [], {}
    for mid in missing:
        attempt, why = request_refwd(mid, key, person)
        if attempt:
            asked.append(mid)
            notes[mid] = {"attempt": attempt, "ack": None}
        else:
            notes[mid] = {"skipped": why}
    if asked:
        for _ in range(30):                     # let the request leave the box before we start the clock
            if outbox_count() == 0:
                break
            time.sleep(2)
        acks = {}
        res2 = wait(asked, key, person, inbox, refwd_timeout, acks=acks, since=started)
        for mid in asked:
            res[mid] = res2[mid]
            a = acks.get(mid)
            if a and a["ts"] >= started:
                notes[mid]["ack"] = a["status"]
                if res[mid]["status"] == "missing":
                    res[mid]["reason"] = (f"relay re-forward {a['status']}: {a.get('reason') or ''}".strip()
                                          if a["status"] != "resent" else
                                          "relay re-forwarded it, but it has not arrived yet")
            elif res[mid]["status"] == "missing":
                res[mid]["reason"] = "still missing after a re-forward request (no answer from the relay yet)"
    for mid, n in notes.items():
        res[mid]["refwd"] = n
    return res


# --- claims: marker files in a message's main folder BASE/<safe(id)> (the one store() writes first) ---------
# claimed  this run handed it to a turn (lease by mtime; content {"id","token","ts"} only for diagnostics)
# working  the turn passed its duplicate check and is about to act (rn-cloud.sh working)
# replying a reply is about to be handed to the engine (rn-cloud.sh send, before the engine)
# done     handled (after "handed to the Mac", or rn-cloud.sh done)
# legacy   folder existed before the upgrade: only keeps it out of the orphan scan
# Everything below runs under inbox_lock(), so markers never race each other.
MARKS = ("claimed", "working", "replying", "done", "legacy")
NOTICE_PREFIX = "relay-alerts-"            # relay notice batches: not messages, never get a folder or markers


def lease():
    return float(os.environ.get("RN_CLAIM_LEASE", "3600"))


def put_marker(p, content):
    """Writes p atomically (temporary file named with pid and a random part, then os.replace)."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.{os.urandom(6).hex()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(content, ensure_ascii=False))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    finally:
        if tmp.exists():
            tmp.unlink()


def markers(d):
    """{name: (mtime, content dict or None)}. Empty or unreadable content still counts as the marker."""
    st = {}
    for m in MARKS:
        try:
            mt = (d / m).stat().st_mtime
        except OSError:
            continue
        try:
            c = json.loads((d / m).read_text(encoding="utf-8"))
        except Exception:
            c = None
        st[m] = (mt, c if isinstance(c, dict) else None)
    return st


def collision(d, mid, st):
    """True if the folder's markers or meta.json name a different id (safe() maps several ids to one folder)."""
    owners = [c.get("id") for _, c in st.values() if c and c.get("id") is not None]
    try:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        if isinstance(meta, dict) and meta.get("id") is not None:
            owners.append(meta["id"])
    except Exception:
        pass
    return any(o != mid for o in owners)


def migrate():
    """One-time upgrade step: message folders written before claims existed get "legacy" (not "done": a folder
    only proves store() ran). Only folders with no marker at all are touched, so a rerun after an interrupted
    migration keeps what it wrote and never relabels a folder claimed since. _claims_v1 is written last."""
    sentinel = BASE / "_claims_v1"
    if sentinel.exists():
        return
    n = 0
    if BASE.is_dir():
        for d in sorted(BASE.iterdir()):
            if d.name.startswith(("_", ".")) or d.is_symlink() or not d.is_dir() or markers(d):
                continue
            try:
                lid = json.loads((d / "meta.json").read_text(encoding="utf-8")).get("id")
            except Exception:
                lid = None
            put_marker(d / "legacy", {"id": lid, "ts": time.time()} if isinstance(lid, str) else {"ts": time.time()})
            n += 1
    put_marker(sentinel, {"migrated_at": time.time(), "folders": n})


def settle(res):
    """Claims every complete result in one go, just before the results are printed (never per poll, so a run
    never sees its own fresh copy as claimed). Changes res in place."""
    now, ttl = time.time(), lease()
    for oid, r in res.items():
        if r["status"] != "complete":
            continue
        d = BASE / safe(oid)
        st = markers(d)
        if collision(d, oid, st):
            keep = {k: r[k] for k in ("orphan", "refwd") if k in r}
            r.clear()
            r.update(id=oid, status="mismatch", reason=f"id collision: folder {d.name} belongs to another id", **keep)
            continue
        stamps = {f"{m}_at": int(st[m][0]) for m in ("claimed", "working", "replying", "done") if m in st}
        busy = [st[m][0] for m in ("working", "replying") if m in st]
        if "done" in st:
            r["status"] = "handled"
        elif busy:
            r["status"] = "claimed" if now - max(busy) < ttl else "uncertain"
        elif "claimed" in st and now - st["claimed"][0] < ttl:
            r["status"] = "claimed"
        else:
            r["claim"] = "reclaimed" if "claimed" in st else "legacy" if "legacy" in st else "new"
            put_marker(d / "claimed", {"id": oid, "token": os.urandom(8).hex(), "ts": now})
            continue
        r.update(stamps)
        if r["status"] != "uncertain":
            r.pop("full", None)


def orphans(exclude, key, person, inbox):
    """Verified RN-FWD messages from the last RN_ORPHAN_DAYS days that no doorbell asked for and nobody handles:
    no done or legacy marker and no claimed/working/replying marker within the lease. One result per id.
    Bad rows are reported once each (remembered in _mismatch/<row id>), unless a valid copy of that id exists."""
    now, ttl = time.time(), lease()
    cut = (now - float(os.environ.get("RN_ORPHAN_DAYS", "7")) * 86400) * 1000
    found, bad, valid = {}, {}, set()
    for rn_id, rn_from, body, sent_at in rows(person, inbox):
        if body.startswith(ACK_HEAD) or sent_at < cut:
            continue
        f, text, err = parse(body, key)
        oid = (f or {}).get("id") or f"rn:{rn_id}"
        if oid in exclude:
            continue
        if err:
            bad.setdefault(oid, []).append((rn_id, rn_from, err))
            continue
        valid.add(oid)
        if oid in found:
            merge_caps(Path(found[oid]["dir"]), caps_of(f))
            continue
        d = BASE / safe(oid)
        st = markers(d)
        if not collision(d, oid, st) and ("done" in st or "legacy" in st or any(
                now - st[m][0] < ttl for m in ("claimed", "working", "replying") if m in st)):
            continue
        sd, already = store(f, text, rn_id, rn_from, sent_at)
        found[oid] = {"id": oid, "status": "complete", "from": f["from"], "agent": f["agent"] or None,
                      "thread": f["thread"] or None, "text_len": len(text), "text_sha256": f["text_sha256"],
                      "full": str(sd / "full.txt"), "dir": str(sd), "already": already, "verified": True,
                      "orphan": True}
    for oid, items in bad.items():
        fresh = [it for it in items if not reported(it[0])]
        if oid in valid or not fresh:
            continue
        for rn_id, _, err in fresh:
            remember_bad(rn_id, oid, err)
        if len(fresh) < len(items):        # this id was reported before through another row: once per id
            continue
        rn_id, rn_from, err = fresh[0]
        found[oid] = {"id": oid, "status": "mismatch", "reason": err, "rn_msg_id": rn_id, "rn_from": rn_from,
                      "orphan": True}
    return found


def reported(rn_id):
    """True if this bad row was already reported by a scan that no doorbell asked for."""
    return (BASE / "_mismatch" / safe(rn_id)).exists()


def remember_bad(rn_id, oid, err):
    put_marker(BASE / "_mismatch" / safe(rn_id), {"id": oid, "rn_msg_id": rn_id, "reason": err, "ts": time.time()})


def report_bad_once(res):
    """For wait/fetch without ids: same rule as the orphan scan, a bad row is reported once. Drops mismatches
    whose row was reported before and remembers the rest. (Ids asked for explicitly never come through here.)"""
    for oid, r in list(res.items()):
        if r["status"] != "mismatch":
            continue
        if reported(r["rn_msg_id"]):
            del res[oid]
        else:
            remember_bad(r["rn_msg_id"], oid, r["reason"])


def mark(name, ids, via=None):
    """rn-fetch.py working|replying|done: writes one marker per id under the inbox lock. Creates the folder if
    needed (done may come before the copy arrives; that id is then handled at once). Checks every id first, so a
    refusal writes nothing."""
    lock = inbox_lock(int(os.environ.get("RN_DONE_LOCK_WAIT", "300")))
    receipt = []
    try:
        migrate()
        todo = []
        for mid in ids:
            if mid.startswith(NOTICE_PREFIX):
                todo.append((mid, None))
                continue
            d = BASE / safe(mid)
            if d.name.startswith("_") or d.is_symlink():
                raise SystemExit(f"rn-fetch: refusing id {mid!r}: its folder name {d.name!r} is reserved")
            if collision(d, mid, markers(d)):
                raise SystemExit(f"rn-fetch: id collision: folder {d.name} belongs to another id; nothing recorded")
            todo.append((mid, d))
        for mid, d in todo:
            if name == "done" and not via and receipts_on() and (d is None or wants_receipt(mid)):
                receipt.append(mid)
            if d is None:
                out({"id": mid, "skipped": "relay notice batch"})
                continue
            content = {"id": mid, "ts": time.time()}
            if via:
                content["via"] = via
            put_marker(d / name, content)
            out({"id": mid, "marked": name})
    finally:
        lock.close()
    if receipt:
        try:
            errors = send_got([], receipt, *credentials())
        except OSError as e:
            errors = [f"cannot read the key or team name: {e}"]
            print(f"rn-fetch: receipt (RN-GOT) not sent: {errors[0]}", file=sys.stderr)
        if errors and any(i.startswith(NOTICE_PREFIX) for i in receipt):
            print("rn-fetch: the relay alert batch was not confirmed; the relay sends it again later",
                  file=sys.stderr)
            return 1
    return 0


def inbox_lock(wait):
    """Exclusive lock shared by scan/wait/fetch and purge, so a purge never overlaps a fetch that is writing the
    inbox. Python fds are not inherited by child processes, so the engine never ends up holding it."""
    import fcntl
    BASE.mkdir(parents=True, exist_ok=True)
    fh = open(BASE / "_lock", "a")
    deadline = time.time() + wait
    while True:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fh
        except BlockingIOError:
            if time.time() >= deadline:
                fh.close()
                raise SystemExit("rn-fetch: another fetch or purge is still running on this box; try again later")
            time.sleep(1)


def header_id(body):
    """The message id in an RN-FWD header (read like parse() does), or None for ACKs and other rows."""
    if not body.startswith("RN-FWD v1\n"):
        return None
    f = {}
    for line in body.partition("\n---\n")[0].split("\n")[1:]:
        k, _, v = line.partition(":")
        f[k.strip()] = v[1:] if v.startswith(" ") else v
    return f.get("id") or None


def engine_pids(rn_bin):
    """Running processes of this box's engine binary (Linux /proc); empty where that cannot be checked."""
    proc = Path("/proc")
    if not rn_bin or not proc.is_dir():
        return []
    target, pids = os.path.realpath(rn_bin), []
    for p in proc.iterdir():
        if p.name.isdigit():
            try:
                if os.readlink(p / "exe") == target:
                    pids.append(p.name)
            except OSError:
                pass
    return pids


def live_ids(db_path):
    """Message ids that still have rows in the engine database (to keep their folders)."""
    db = sqlite3.connect(db_path, timeout=5)
    try:
        return {lid for (body,) in db.execute("SELECT body FROM messages") if (lid := header_id(body))}
    finally:
        db.close()


def purge(days, threads, rn_bin=None):
    """Deletes plaintext older than `days` (0 = everything, whatever its timestamp): the engine's received
    messages, inbox folders, stray mail logs and thread notes. Age is decided per message id: an RN-FWD and its
    later re-forwards share one inbox folder, so all of them go or stay by the newest copy, and an argument-less
    scan never reports a purged message as new again. A message folder goes with its claim markers. Entries whose
    name starts with "_" are never deleted, except the old contents of _stray/ and _mismatch/: _refwd/ counters
    hold no text and stay, so the re-forward cap per id still holds, and the migration sentinel _claims_v1 and
    _lock stay. Refuses before deleting anything if a folder is a symlink or the engine still runs.
    Order: take the inbox lock, stop the engine, then read and delete rows in one write transaction (no new mail
    lands in between), and before removing a folder check again that no row of that id is left. A copy that
    arrives after that is new mail, and the next scan fetches it again."""
    everything = days == 0
    cut = time.time() - days * 86400
    old = lambda t: everything or t < cut                  # t in seconds
    linked = [str(p) for p in (BASE, BASE / "_stray", BASE / "_mismatch", threads) if p.is_symlink()]
    if linked:
        raise SystemExit(f"rn-fetch: refusing to purge through symlinked folder(s): {', '.join(linked)}")
    lock = inbox_lock(int(os.environ.get("RN_PURGE_LOCK_WAIT", "30")))
    try:
        if rn_bin:
            subprocess.run([rn_bin, "stop"], stdin=subprocess.DEVNULL, capture_output=True, timeout=60,
                           env=engine_env())
        running = engine_pids(rn_bin)
        if running:
            raise SystemExit(f"rn-fetch: the engine is still running (pid {', '.join(running)}); stop it, then purge")
        n = {"messages": 0, "inbox": 0, "stray": 0, "threads": 0, "mismatch": 0}
        gone_ids, kept_ids = set(), set()
        db_path = HOME / "network.db"
        if db_path.exists():
            db = sqlite3.connect(db_path, timeout=5, isolation_level=None)
            try:
                db.execute("PRAGMA secure_delete = ON")          # freed pages are zeroed, not left readable
                db.execute("BEGIN IMMEDIATE")                     # writers wait from here until COMMIT
                rows = [(rid, header_id(body), sent) for rid, body, sent in
                        db.execute("SELECT id, body, sent_at FROM messages")]
                newest = {}
                for _, lid, sent in rows:
                    if lid is not None:
                        newest[lid] = max(newest.get(lid, sent), sent)
                gone_ids = {lid for lid, t in newest.items() if old(t / 1000)}
                kept_ids = set(newest) - gone_ids
                doomed = [(rid,) for rid, lid, sent in rows if (lid in gone_ids if lid is not None else old(sent / 1000))]
                db.executemany("DELETE FROM messages WHERE id = ?", doomed)
                db.execute("COMMIT")
                n["messages"] = len(doomed)
                db.execute("VACUUM")
                busy = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
                if busy:
                    raise SystemExit("rn-fetch: rows deleted, but the database was busy, so old pages may remain in "
                                     "its WAL file; run purge again when nothing else uses the engine")
            finally:
                db.close()
            kept_ids |= live_ids(db_path)                         # a re-forward that landed after COMMIT
        if BASE.is_dir():
            for d in BASE.iterdir():
                if d.name.startswith("_") or d.is_symlink() or not d.is_dir():
                    continue
                try:
                    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
                lid = meta.get("id") if isinstance(meta, dict) else None
                sent = meta.get("sent_at") if isinstance(meta, dict) else None
                sent = sent / 1000 if isinstance(sent, (int, float)) else d.lstat().st_mtime
                if lid in kept_ids or not (lid in gone_ids or old(sent)):
                    continue
                shutil.rmtree(d)
                n["inbox"] += 1
            for p in (BASE / "_stray").glob("*.log"):
                if old(p.lstat().st_mtime):
                    p.unlink()
                    n["stray"] += 1
            for p in (BASE / "_mismatch").glob("*"):          # rows already reported once; they hold no text
                if not p.is_dir() and old(p.lstat().st_mtime):
                    p.unlink()
                    n["mismatch"] += 1
        for p in threads.glob("*.md"):
            if old(p.lstat().st_mtime):
                p.unlink()
                n["threads"] += 1
        return {"purged": n, "older_than_days": days}
    finally:
        lock.close()


def main(argv):
    if argv == ["send-status"]:
        status = send_status(sys.stdin.buffer.read().decode("utf-8", "replace"))
        sys.stdout.buffer.write(status.encode("utf-8") + b"\n")
        return 0
    if argv[:1] == ["purge"]:
        if len(argv) != 2 or not re.fullmatch(r"[0-9]{1,5}", argv[1]):
            print("usage: rn-fetch.py purge DAYS   (whole days; 0 = everything)", file=sys.stderr)
            return 2
        threads = Path(os.environ.get("RN_THREADS_DIR", "/workspace/agent-mesh/threads"))
        out(purge(int(argv[1]), threads, os.environ.get("RN_BIN")))
        return 0
    if argv[:1] in (["working"], ["replying"], ["done"]):
        rest, via = argv[1:], None
        if "--via" in rest:
            i = rest.index("--via")
            via = rest[i + 1] if i + 1 < len(rest) else ""
            rest = rest[:i] + rest[i + 2:]
        ids = list(dict.fromkeys(rest))
        if not ids or via == "" or any(not i or i.startswith("-") for i in ids):
            print(f"usage: rn-fetch.py {argv[0]} ID... [--via send]", file=sys.stderr)
            return 2
        if argv[0] == "done" and via is None:
            names()
        try:
            return mark(argv[0], ids, via)
        except OSError as e:
            raise SystemExit(f"rn-fetch: could not record {argv[0]}: {e}")
    if not argv or argv[0] in ("-h", "--help") or argv[0] not in ("scan", "wait", "fetch"):
        print(__doc__)
        return 0 if argv and argv[0] in ("-h", "--help") else 1
    cmd, rest, timeout, refwd_timeout, got = argv[0], argv[1:], 120, 90, False
    while rest[:1] in (["--timeout"], ["--refwd-timeout"], ["--got"]):
        if rest[0] == "--got":
            got, rest = True, rest[1:]
            continue
        if rest[0] == "--timeout":
            timeout = int(rest[1])
        else:
            refwd_timeout = int(rest[1])
        rest = rest[2:]
    ids = list(dict.fromkeys(rest))
    inbox = names()[2]
    key, person = credentials()
    lock = inbox_lock(300)
    try:
        migrate()
        if cmd == "fetch" and ids:
            res = fetch(ids, key, person, inbox, timeout, refwd_timeout)
        else:
            res = wait(ids, key, person, inbox, timeout if cmd in ("wait", "fetch") else 0)
        if cmd != "scan":                  # scan stays a diagnostic: it writes folders but claims nothing
            asked = set(ids) | set(res)    # before filtering: a bad id dropped below is not an orphan either
            if not ids:
                report_bad_once(res)
            if os.environ.get("RN_ORPHANS", "1") != "0":
                for oid, r in orphans(asked, key, person, inbox).items():
                    res.setdefault(oid, r)
            settle(res)
            if got and receipts_on():
                for r in res.values():
                    kind = {"complete": "stored", "claimed": "stored", "uncertain": "stored",
                            "handled": "done"}.get(r["status"])
                    if kind and wants_receipt(r["id"], r.get("dir")):
                        r["got"] = kind
    finally:
        lock.close()
    for r in res.values():
        out(r)
    receipt = {k: [r["id"] for r in res.values() if r.get("got") == k] for k in ("stored", "done")}
    if receipt["stored"] or receipt["done"]:
        send_got(receipt["stored"], receipt["done"], key, person)
    st = {r["status"] for r in res.values()}
    return 5 if "mismatch" in st else 4 if "missing" in st else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
