#!/usr/bin/env python3
"""rn-fetch: read doorbell messages' full text on the box, byte-exact, from the engine's own database.

The Mac relay (relay_version 3) sends each inbound message's full text to <team-name>/<inbox> on this box as a
signed "RN-FWD v1" Reason Network message, then rings the routine's webhook with just the ids. The engine
stores what arrives in $REASON_NETWORK_HOME/network.db (table messages, kept even after it is marked read);
`listen` would print it rewritten (it defangs <reason-message> tags), so this reads the database read-only.

  rn-fetch.py scan [ID...]                 one pass
  rn-fetch.py wait [--timeout S] [ID...]   poll until every ID is complete or mismatched (default 120 s)
  rn-fetch.py fetch [--timeout S] [--refwd-timeout S] ID...
                                           wait; then, for ids still missing, ask the Mac relay once to
                                           re-forward its kept copy (RN-REFWD v1, signed) and wait again
With no IDs (scan/wait): every RN-FWD message not yet written to the inbox folder.

Re-forward (fetch only, relay v3.1): Reason Network 0.10.5 can silently drop a message after `send` said
queued, so a missing id gets ONE signed RN-REFWD request per fetch run, sent as <team-name>/<RN_CLOUD_NAME> to
<team-name>/<RN_RELAY_NAME>, and at most RN_REFWD_MAX (default 2) per id in total (counted in
$RN_INBOX_DIR/_refwd/<id>.json), so it can never loop. The relay answers with a signed RN-REFWD-ACK v1
(resent|no-copy|limit|failed); results then carry "refwd": {"attempt": N, "ack": status|null} or
{"skipped": reason}. ACK messages are never reported as mismatches.

Output: one JSON line per message: status complete (full = path of full.txt, verified against the signed
text_len/text_sha256), missing (not on this box yet), or mismatch (reason: bad signature, length/hash
differs, malformed). Exit 0 if all complete, 4 if any missing, 5 if any mismatch (5 wins).
Writes $RN_INBOX_DIR/<id>/full.txt and meta.json.
Env: REASON_NETWORK_HOME, RN_PERSON, RN_INBOX_NAME (default bot-inbox), RN_KEY_FILE, RN_INBOX_DIR;
for fetch also RN_BIN, RN_CLOUD_NAME (bot-cloud), RN_RELAY_NAME (bot), RN_REFWD (1; 0 = off), RN_REFWD_MAX.
Standard library only.
"""
import hashlib, hmac, json, os, re, sqlite3, subprocess, sys, time
from pathlib import Path

BASE = Path(os.environ.get("RN_INBOX_DIR", "/workspace/agent-mesh/inbox"))
HOME = Path(os.environ.get("REASON_NETWORK_HOME", "/workspace/agent-mesh/box/state"))
TRAILER = "\n--- RN-FWD END"
ACK_HEAD = "RN-REFWD-ACK v1\n"


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


def refwd_body(key, mid, ts=None, nonce=None):
    ts = str(int(time.time())) if ts is None else str(ts)
    nonce = nonce or os.urandom(12).hex()
    return f"RN-REFWD v1\nid: {mid}\nts: {ts}\nnonce: {nonce}\nsig: {sign_refwd(key, mid, ts, nonce)}"


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
            return d, True
    d.mkdir(parents=True, exist_ok=True)
    write(d / "full.txt", text)
    meta = {"id": f["id"], "from": f["from"], "agent": f["agent"] or None, "thread": f["thread"] or None,
            "parts": 1, "text_len": len(text), "text_sha256": f["text_sha256"], "via": "rn-fwd",
            "rn_msg_id": rn_id, "rn_from": rn_from, "sent_at": sent_at, "first_seen": time.time(),
            "updated": time.time()}
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
    rn = os.environ.get("RN_BIN") or str(Path(os.environ.get("RN_BOX", "/workspace/agent-mesh/box")) / "bin/reason-network")
    cloud = os.environ.get("RN_CLOUD_NAME", "bot-cloud")
    relay = os.environ.get("RN_RELAY_NAME", "bot")
    done.append(time.time())
    write(cf, json.dumps(done))            # count before sending: a crash never allows extra requests
    try:
        r = subprocess.run([rn, "send", f"{person}/{relay}", "--from", f"{person}/{cloud}", "-"],
                           input=refwd_body(key, mid), capture_output=True, text=True, timeout=60)
    except Exception as e:
        return None, f"send failed: {type(e).__name__}"
    res = (r.stdout + r.stderr).strip()
    if r.returncode != 0 or re.search(r"refused|held|nobody matches|ambiguous|error", res, re.I):
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


def main(argv):
    if not argv or argv[0] in ("-h", "--help") or argv[0] not in ("scan", "wait", "fetch"):
        print(__doc__)
        return 0 if argv and argv[0] in ("-h", "--help") else 1
    cmd, rest, timeout, refwd_timeout = argv[0], argv[1:], 120, 90
    while rest[:1] in (["--timeout"], ["--refwd-timeout"]):
        if rest[0] == "--timeout":
            timeout = int(rest[1])
        else:
            refwd_timeout = int(rest[1])
        rest = rest[2:]
    ids = list(dict.fromkeys(rest))
    key_file = Path(os.environ.get("RN_KEY_FILE", "/workspace/agent-mesh/box/outbound.key"))
    key = key_file.read_text().strip()
    person = os.environ.get("RN_PERSON") or (Path(os.environ.get("RN_BOX", "/workspace/agent-mesh/box")) / "person").read_text().strip()
    inbox = os.environ.get("RN_INBOX_NAME", "bot-inbox")
    if cmd == "fetch" and ids:
        res = fetch(ids, key, person, inbox, timeout, refwd_timeout)
    else:
        res = wait(ids, key, person, inbox, timeout if cmd in ("wait", "fetch") else 0)
    for r in res.values():
        out(r)
    st = {r["status"] for r in res.values()}
    return 5 if "mismatch" in st else 4 if "missing" in st else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
