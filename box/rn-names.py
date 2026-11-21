#!/usr/bin/env python3
"""rn-names: the Bot address names of agent-mesh on [BOT-CLOUD], loaded by rn-routine.py.

  rn-names.py apply rename SLOT OLD NEW REQ [reclaim] | apply history SLOT CLAIM REQ
      run by rn-routine.py request under the exclusive maintenance lock. A rename registers <new>-cloud and
      <new>-inbox and records the switch in rename.pending; the Bot answers as <old> until the first verified
      message reaches <new>-inbox, then <old> is kept as an earlier name for 30 days. Messages already in an
      address taken back are recorded in exclude.json and stay hidden until a history request accepts them.
  rn-names.py names SLOT | owner NAME | claim-rows DIR NAME | held SLOT add
      helpers for rn-cloud.sh and rn-cloud-add-bot.sh: a Bot's current name, earlier inboxes and exclude file;
      which Bot holds NAME; list the earlier messages of NAME-inbox to hold back when NAME is taken back (for a
      removed name the ones its box/bots-removed file lists or a fetch already read, else all of them); how many
      of them are held back.
  rn-names.py tombstone NAME...
      run by rn-cloud-uninstall.sh under the exclusive maintenance lock: writes box/bots-removed/NAME for each
      NAME that has none: one JSON line {"removed": UTC time, "rows": [[engine id, source id or null], ...]}
      listing the messages then in NAME-inbox. A file holding only the time was written by an older version;
      anything else stops a reclaim of NAME.
Every command that reads a Bot's name first finishes a switch whose first message has arrived. Standard library
only; it never loads rn-routine.py.
"""
import fcntl, importlib.util, json, os, re, sqlite3, subprocess, sys, time
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
BASE = Path(os.environ.get("RN_CLOUD_BASE", "/workspace/agent-mesh/box"))
ROOT = BASE.parent
CLOUD_ROOT = "/workspace/agent-mesh"
OVERRIDES = ("AGENT_RELAY_NAME", "RN_CLOUD_NAME", "RN_INBOX_NAME", "RN_INBOX_DIR", "RN_THREADS_DIR")
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
RESERVED = re.compile(r"terminal(?:-[0-9]+)?")
REQ = r"[0-9a-f]{8,32}"
FORMER_DAYS = 30
RESULT = "agent-mesh-result"
ORIG_ENV = dict(os.environ)


class Stop(Exception):
    pass


def valid_name(name):
    return bool(NAME.fullmatch(name)) and len(name) <= 34 and not RESERVED.fullmatch(name)


def valid_team(team):
    return bool(NAME.fullmatch(team)) and len(team) <= 40


def present(p):
    return p.exists() or p.is_symlink()


def read_or_stop(p, stop, parse=str):
    try:
        return parse(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise Stop(stop(e))


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def first_bot():
    p = BASE / "relay"
    if not p.exists():
        return "bot"
    name = read_or_stop(p, lambda e: f"cannot read box/relay: {e}").rstrip("\n")
    if not valid_name(name):
        raise Stop("box/relay is damaged; it must hold the Bot address name the Mac uses (README: Troubleshooting)")
    return name


def slot_dir(slot):
    return BASE if slot == "-" else ROOT / "bots" / slot


def shown(p):
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def current_name(slot):
    if slot == "-":
        return first_bot()
    p = slot_dir(slot) / "name"
    if not present(p):
        return slot
    name = read_or_stop(p, lambda e: f"cannot read bots/{slot}/name: {e}").rstrip("\n")
    if not valid_name(name):
        raise Stop(f"bots/{slot}/name is damaged; it must hold the Bot address name the Mac uses (README: "
                   "Troubleshooting)")
    return name


def read_former(slot):
    p = slot_dir(slot) / "former"
    if not present(p):
        return []
    found = []
    for line in read_or_stop(p, lambda e: f"cannot read {shown(p)}: {e}").splitlines():
        m = re.fullmatch(r"(\S+) ([0-9]{1,12})", line)
        if not m or not valid_name(m[1]):
            raise Stop(f"{shown(p)} is damaged; ask the user (README: Troubleshooting)")
        found.append((m[1], int(m[2])))
    return found


def add_former(slot, name, drop=None):
    items = [(n, u) for n, u in read_former(slot) if n not in (name, drop)]
    items.append((name, int(time.time()) + FORMER_DAYS * 86400))
    put(slot_dir(slot) / "former", "".join(f"{n} {u}\n" for n, u in items))


def read_pending(slot):
    p = slot_dir(slot) / "rename.pending"
    if not present(p):
        return None
    d = read_or_stop(p, lambda e: f"cannot read {shown(p)}: {e}", json.loads)
    if not (isinstance(d, dict) and all(isinstance(d.get(k), str) for k in ("old", "new", "req"))
            and valid_name(d["old"]) and valid_name(d["new"]) and re.fullmatch(REQ, d["req"])
            and isinstance(d.get("before"), list)):
        raise Stop(f"{shown(p)} is damaged; ask the user (README: Troubleshooting)")
    return d


class NamesLock:
    def __enter__(self):
        self.fh = open(state_dir() / "names.lock", "a")
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_):
        self.fh.close()


def engine_rows(person, inbox, seen=False):
    db = BASE / "state" / "network.db"
    if not db.exists():
        return []
    con = sqlite3.connect(f"{db.absolute().as_uri()}?mode=ro", uri=True, timeout=5)
    try:
        return con.execute("SELECT id, body FROM messages WHERE recipient = ?"
                           + (" AND read_at IS NOT NULL" if seen else "") + " ORDER BY sent_at, id",
                           (f"{person}/{inbox}",)).fetchall()
    finally:
        con.close()


def fetch_module():
    return module("rn_fetch_names", HERE / "rn-fetch.py")


def arrived(slot, pend):
    try:
        person = read_person()
        key = (BASE / "outbound.key").read_text(encoding="utf-8").strip()
        fm = fetch_module()
        ids, srcs, _ = fm.exclusion(person, str(slot_dir(slot) / "exclude.json"))
        rows = engine_rows(person, f"{pend['new']}-inbox")
    except (OSError, UnicodeDecodeError, sqlite3.Error, Stop, SystemExit):
        return False
    who, before = f"{person}/{pend['new']}-inbox", {str(i) for i in pend["before"]}
    for rid, body in rows:
        if str(rid) in before or (who, str(rid)) in ids or (who, fm.header_id(body)) in srcs:
            continue
        if body.startswith("RN-FWD v1\n") and fm.parse(body, key)[2] is None:
            return True
    return False


def finish(slot, pend):
    add_former(slot, pend["old"], drop=pend["new"])
    put(BASE / "relay" if slot == "-" else slot_dir(slot) / "name", pend["new"] + "\n")
    (slot_dir(slot) / "rename.pending").unlink()


def settle(slot):
    if read_pending(slot) is None:
        return
    with NamesLock():
        pend = read_pending(slot)
        if pend is None:
            return
        name = current_name(slot)
        if name == pend["new"] or (name == pend["old"] and arrived(slot, pend)):
            finish(slot, pend)


def resolve(slot):
    settle(slot)
    name = current_name(slot)
    now = time.time()
    others = [f"{n}-inbox" for n, until in read_former(slot) if until > now and n != name]
    pend = read_pending(slot)
    if pend and pend["new"] != name:
        others.append(f"{pend['new']}-inbox")
    return name, list(dict.fromkeys(others))


def selector(registered=True, reclaim=False):
    given = [k for k in OVERRIDES if ORIG_ENV.get(k)]
    threads_shown, others, exclude = None, [], None
    if "RN_BOT" in ORIG_ENV:
        slot = ORIG_ENV["RN_BOT"]
        if not valid_name(slot):
            raise Stop(f"invalid RN_BOT {slot[:60]!r}: a Bot address name is 1-34 lowercase letters and digits, "
                       "groups joined by a single '-', not terminal")
        if given:
            raise Stop(f"RN_BOT cannot be combined with {given[0]}; nothing was run")
        if present(BASE / "bots-removed" / slot) and not reclaim:
            raise Stop(f"the Bot {slot} was removed from this cloud (box/bots-removed/{slot}); nothing was run")
        bot = ROOT / "bots" / slot
        if bot.is_symlink() or (registered and not (bot / ".registered").is_file()):
            raise Stop(f"the Bot {slot} is not registered on this cloud; nothing was run")
        name, others = resolve(slot)
        inbox_dir, threads_dir, exclude = bot / "inbox", bot / "threads", bot / "exclude.json"
    else:
        slot = "-"
        if given:
            name = first_bot()
            inbox_dir = Path(ORIG_ENV.get("RN_INBOX_DIR") or f"{CLOUD_ROOT}/inbox")
            threads_dir = Path(ORIG_ENV.get("RN_THREADS_DIR") or f"{CLOUD_ROOT}/threads")
            threads_shown = str(threads_dir)
        else:
            if not (BASE / "relay").exists() and present(BASE / "relay.removed"):
                raise Stop("the first Bot was removed from this cloud (box/relay.removed); select a Bot with "
                           "RN_BOT=<name>")
            first_bot()
            name, others = resolve(slot)
            if present(BASE / "bots-removed" / name):
                raise Stop(f"the Bot {name} was removed from this cloud (box/bots-removed/{name}); nothing was run")
            inbox_dir, threads_dir, exclude = ROOT / "inbox", ROOT / "threads", BASE / "exclude.json"
    return SimpleNamespace(slot=slot, name=name, inbox_dir=inbox_dir, threads_dir=threads_dir,
                           threads_shown=threads_shown, relay=ORIG_ENV.get("AGENT_RELAY_NAME") or name,
                           cloud=ORIG_ENV.get("RN_CLOUD_NAME") or f"{name}-cloud",
                           inbox=ORIG_ENV.get("RN_INBOX_NAME") or f"{name}-inbox", others=others,
                           exclude=exclude, overridden=bool(given))


def read_person():
    person = read_or_stop(BASE / "person",
                          lambda e: f"cloud setup is incomplete; box/person cannot be read ({e})").rstrip("\n")
    if not valid_team(person):
        raise Stop("box/person does not hold a team name")
    return person


def state_dir(*parts):
    git = ROOT / ".git"
    if git.is_symlink() or not git.is_dir():
        raise Stop(f"{ROOT} is not a git clone")
    d = git / "agent-mesh"
    for p in (d, *[d.joinpath(*parts[:i + 1]) for i in range(len(parts))]):
        if p.is_symlink():
            raise Stop(f"{p} is a symlink; not touching it")
    d = d.joinpath(*parts)
    d.mkdir(parents=True, exist_ok=True)
    return d


def put(p, text, tmp=None, sync=True):
    tmp = tmp or p.with_name(f".{p.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        if sync:
            fh.flush()
            os.fsync(fh.fileno())
    os.replace(tmp, p)


def maint_problem(mode):
    return module("rn_engine", HERE / "rn-engine.py").maint_problem(str(ROOT / ".maint.lock"), mode)


def session_table(names):
    db = BASE / "state" / "network.db"
    try:
        con = sqlite3.connect(f"{db.absolute().as_uri()}?mode=ro", uri=True, timeout=5)
        try:
            table = {}
            for name in names:
                row = con.execute("SELECT name FROM sessions WHERE key = ? ORDER BY last_seen DESC LIMIT 1",
                                  ("cli:" + name,)).fetchone()
                other = con.execute("SELECT key FROM sessions WHERE name = ? AND key != ? LIMIT 1",
                                    (name, "cli:" + name)).fetchone()
                table[name] = (row[0] if row else None, other is not None)
            return table
        finally:
            con.close()
    except sqlite3.Error as e:
        raise Stop(f"the session table of this cloud cannot be read ({e}); nothing was changed")


def first_alive():
    return (BASE / "relay").exists() or not present(BASE / "relay.removed")


def held_names(slot):
    names = set() if slot == "-" else {slot}
    names.add(current_name(slot))
    names.update(n for n, _ in read_former(slot))
    pend = read_pending(slot)
    if pend:
        names.update((pend["old"], pend["new"]))
    return names


def other_holder(name, slot):
    if slot != "-" and first_alive() and name in held_names("-"):
        return "the first Bot"
    bots = ROOT / "bots"
    if bots.is_dir() and not bots.is_symlink():
        for d in sorted(bots.iterdir()):
            if d.name == slot or d.name.startswith(".") or d.is_symlink() or not d.is_dir():
                continue
            if not valid_name(d.name):
                continue
            if name in held_names(d.name):
                return f"the Bot in bots/{d.name}"
    return None


def read_exclude(p):
    if p.is_symlink():
        raise Stop(f"{shown(p)} is a symlink; not touching it")
    if not p.exists():
        return {"claims": {}, "accepted": {}}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not fetch_module().exclusion_valid(data):
            raise ValueError("unexpected structure")
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise Stop(f"{shown(p)} is damaged ({e}); ask the user (README: Troubleshooting)")
    return data


def move_claim(p, old, new):
    data = read_exclude(p)
    if old in data["claims"] and new not in data["claims"]:
        data["claims"][new] = data["claims"].pop(old)
        put(p, json.dumps(data))


def inbox_rows(person, inbox, seen=False):
    fm = fetch_module()
    try:
        return [[str(rid), fm.header_id(body)] for rid, body in engine_rows(person, inbox, seen)]
    except sqlite3.Error as e:
        raise Stop(f"the messages of this cloud cannot be read ({e}); nothing was changed")


STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def removal_rows(tomb):
    text = read_or_stop(tomb, lambda e: f"{shown(tomb)} cannot be read ({e}); ask the user (nothing was changed)")
    if text.endswith("\n") and STAMP.fullmatch(text[:-1]):
        return None
    try:
        if not text.endswith("\n") or "\n" in text[:-1]:
            raise ValueError("not one line")
        data = json.loads(text)
        if not isinstance(data, dict) or set(data) != {"removed", "rows"}:
            raise ValueError("unexpected keys")
        if not (isinstance(data["removed"], str) and STAMP.fullmatch(data["removed"])):
            raise ValueError("bad time")
        rows = data["rows"]
        if not fetch_module().exclusion_valid({"claims": {"x": {"inbox": "x", "rows": rows}}, "accepted": {}}):
            raise ValueError("unexpected structure")
    except (ValueError, TypeError) as e:
        raise Stop(f"{shown(tomb)} is damaged ({e}); ask the user (nothing was changed)")
    return rows


def add_rows(person, name):
    tomb = BASE / "bots-removed" / name
    inbox = f"{name}-inbox"
    if not present(tomb):
        return inbox_rows(person, inbox)
    kept = removal_rows(tomb)
    read = inbox_rows(person, inbox, seen=True)
    if kept is None:
        return read
    ids = {r[0] for r in kept}
    return kept + [r for r in read if r[0] not in ids]


def write_claim(p, claim, inbox, rows):
    data = read_exclude(p)
    if claim in data["claims"] or claim in data["accepted"]:
        return
    for k in [k for k, c in data["claims"].items() if isinstance(c, dict) and c.get("inbox") == inbox]:
        data["claims"][claim] = data["claims"].pop(k)
        put(p, json.dumps(data))
        return
    data["claims"][claim] = {"inbox": inbox, "rows": rows(), "ts": time.time()}
    put(p, json.dumps(data))


def held_count(p, claim, person):
    c = read_exclude(p)["claims"].get(claim)
    if not c:
        return 0
    try:
        key = (BASE / "outbound.key").read_text(encoding="utf-8").strip()
    except OSError as e:
        raise Stop(f"box/outbound.key cannot be read ({e})")
    fm = fetch_module()
    wanted = {str(r[0]) for r in c.get("rows", [])}
    srcs = set()
    for rid, body in engine_rows(person, c["inbox"]):
        if str(rid) in wanted and body.startswith("RN-FWD v1\n"):
            f, _, err = fm.parse(body, key)
            if err is None:
                srcs.add(f["id"])
    return len(srcs)


def register_name(name):
    fd = int(os.environ["RN_MAINT_FD"])
    r = subprocess.run(["bash", str(BASE / "rn-cloud.sh"), "register-name", name],
                       env=dict(os.environ, RN_RECLAIMING="1"), pass_fds=(fd,), stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    for line in r.stdout.rstrip("\n").split("\n") if r.stdout.strip() else []:
        print(line)
    if r.returncode:
        raise Stop(f"registering {name}-cloud and {name}-inbox did not finish (see the lines above); the Mac can "
                   "send the same request again")


def apply_rename(slot_arg, old, new, req, reclaim):
    sel = selector()
    if sel.overridden:
        raise Stop("this cloud selects the first Bot with name overrides; renaming it is not supported")
    slot = sel.slot
    if slot_arg != slot:
        raise Stop(f"this request is for the Bot slot {slot_arg}, but it reached {slot}; nothing was changed")
    person = read_person()
    exclude = slot_dir(slot) / "exclude.json"
    with NamesLock():
        pend = read_pending(slot)
        cur = current_name(slot)
        if pend and cur == pend["new"]:
            finish(slot, pend)
            pend = None
        elif pend and cur == pend["old"]:
            if old == pend["new"]:
                finish(slot, pend)
                pend, cur = None, pend["new"]
            elif old != pend["old"]:
                raise Stop(f"this cloud is switching the Bot from {pend['old']} to {pend['new']}, and this request "
                           f"starts from {old}; nothing was changed")
            elif new != pend["new"]:
                add_former(slot, pend["new"])
                (slot_dir(slot) / "rename.pending").unlink()
                pend = None
            elif req != pend["req"]:
                move_claim(exclude, pend["req"], req)
                pend = dict(pend, req=req)
                put(slot_dir(slot) / "rename.pending", json.dumps(pend))
        elif pend:
            raise Stop(f"the rename record of this Bot names {pend['old']} and {pend['new']}, but the Bot uses "
                       f"{cur}; ask the user (README: Troubleshooting)")
    if cur != old:
        raise Stop(f"the Bot uses the address name {cur} on this cloud, not {old}; nothing was changed")
    if new == old:
        raise Stop(f"the Bot already uses the address name {new}; nothing was changed")
    owned = new == slot or new in {n for n, _ in read_former(slot)} or pend is not None
    holder = other_holder(new, slot)
    if holder:
        raise Stop(f"{new} is an address name of {holder} on this cloud; choose another name (nothing was changed)")
    tomb = BASE / "bots-removed" / new
    removed = present(tomb)
    table = session_table([f"{new}-cloud", f"{new}-inbox"])
    for n, (have, taken) in table.items():
        if taken or (have is not None and have != n):
            raise Stop(f"the session {person}/{n} on this cloud is used by another key; choose another name "
                       "(nothing was changed)")
    exists = any(have for have, _ in table.values())
    claim = removed or (exists and not owned)
    if claim and not reclaim:
        raise Stop(f"{new} is an earlier address name on this cloud; the Mac must confirm taking it back "
                   "(nothing was changed)")
    pending = slot_dir(slot) / "rename.pending"
    if claim:
        write_claim(exclude, req, f"{new}-inbox", lambda: inbox_rows(person, f"{new}-inbox"))
    with NamesLock():
        if current_name(slot) != old:
            raise Stop(f"the Bot's address name changed while this request was checked; the Mac can send the "
                       "request again")
        pend = read_pending(slot)
        if not (pend and (pend["old"], pend["new"], pend["req"]) == (old, new, req)):
            before = [str(rid) for rid, _ in engine_rows(person, f"{new}-inbox")]
            put(pending, json.dumps({"old": old, "new": new, "req": req, "before": before, "ts": time.time(),
                                     "registered": False}))
    register_name(new)
    table = session_table([f"{new}-cloud", f"{new}-inbox"])
    if any(have != n for n, (have, _) in table.items()):
        raise Stop(f"the session table does not show both {new}-cloud and {new}-inbox after registering; the Mac "
                   "can send the same request again")
    if removed:
        tomb.unlink()
    with NamesLock():
        pend = read_pending(slot)
        if current_name(slot) != old or not pend or (pend["old"], pend["new"], pend["req"]) != (old, new, req):
            raise Stop(f"the Bot's address name changed while {new} was registered; the Mac can send the request "
                       "again")
        if "registered" in pend:
            put(pending, json.dumps({k: v for k, v in pend.items() if k != "registered"}))
    count = held_count(exclude, req, person)
    print(f"the Bot keeps answering as {old} until the first message reaches {person}/{new}-inbox")
    print(f"{RESULT} rename {req} pending {slot} {old} {new} held {count}")
    return 0


def apply_history(slot_arg, claim, req):
    sel = selector()
    if slot_arg != sel.slot:
        raise Stop(f"this request is for the Bot slot {slot_arg}, but it reached {sel.slot}; nothing was changed")
    person = read_person()
    p = slot_dir(sel.slot) / "exclude.json"
    with NamesLock():
        count = held_count(p, claim, person)
        data = read_exclude(p)
        c = data["claims"].pop(claim, None)
        if c is not None:
            data["accepted"][claim] = dict(c, ts=time.time())
            put(p, json.dumps(data))
    print(f"{RESULT} history {req} accepted {claim} {count}")
    return 0


def apply(args):
    problem = maint_problem("exclusive")
    if problem:
        raise Stop(f"the maintenance lock is not held exclusively ({problem}); nothing was changed")
    if len(args) in (5, 6) and args[0] == "rename" and args[5:] in ([], ["reclaim"]):
        return apply_rename(args[1], args[2], args[3], args[4], len(args) == 6)
    if len(args) == 4 and args[0] == "history":
        return apply_history(args[1], args[2], args[3])
    raise Stop("usage: rn-names.py apply rename SLOT OLD NEW REQ [reclaim] | apply history SLOT CLAIM REQ")


def names_cmd(args):
    if len(args) != 1 or not (args[0] == "-" or valid_name(args[0])):
        raise Stop("usage: rn-names.py names SLOT")
    name, others = resolve(args[0])
    print(name)
    print(",".join(others))
    print(slot_dir(args[0]) / "exclude.json")
    return 0


def owner_cmd(args):
    if len(args) != 1 or not valid_name(args[0]):
        raise Stop("usage: rn-names.py owner NAME")
    holder = other_holder(args[0], args[0])
    if holder:
        print(holder)
    return 0


def claim_cmd(args):
    if len(args) != 2 or not valid_name(args[1]):
        raise Stop("usage: rn-names.py claim-rows DIR NAME")
    d = Path(args[0])
    if d.is_symlink() or not d.is_dir():
        raise Stop(f"{d} is not a folder")
    person = read_person()
    write_claim(d / "exclude.json", "add", f"{args[1]}-inbox", lambda: add_rows(person, args[1]))
    return 0


def tombstone_cmd(args):
    if not args or not all(valid_name(n) for n in args):
        raise Stop("usage: rn-names.py tombstone NAME...")
    problem = maint_problem("exclusive")
    if problem:
        raise Stop(f"the maintenance lock is not held exclusively ({problem}); nothing was changed")
    person = read_person()
    d = BASE / "bots-removed"
    if d.is_symlink() or not d.is_dir():
        raise Stop(f"{shown(d)} is not a folder; nothing was changed")
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for name in args:
        tomb = d / name
        if tomb.is_symlink():
            raise Stop(f"{shown(tomb)} is a symlink; not touching it")
        if not tomb.exists():
            put(tomb, json.dumps({"removed": stamp, "rows": inbox_rows(person, f"{name}-inbox")}) + "\n")
    return 0


def held_cmd(args):
    if len(args) != 2 or not valid_name(args[0]) or args[1] != "add":
        raise Stop("usage: rn-names.py held SLOT add")
    print(held_count(slot_dir(args[0]) / "exclude.json", "add", read_person()))
    return 0


def main(argv):
    commands = {"apply": apply, "names": names_cmd, "owner": owner_cmd, "claim-rows": claim_cmd, "held": held_cmd,
                "tombstone": tombstone_cmd}
    if not argv or argv[0] not in commands:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        return commands[argv[0]](argv[1:])
    except Stop as e:
        print(f"STOP: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
