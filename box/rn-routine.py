#!/usr/bin/env python3
"""rn-routine: the routine rules and the maintenance requests of agent-mesh on [BOT-CLOUD].

  rn-routine.py instructions N [TEAM-NAME TEAM-HASH]
      rn-cloud.sh instructions: print "agent-mesh-instructions BEGIN N <release> <lang> <slot>", the rules
      rendered from prompts/routine-body*.md and "agent-mesh-instructions OK N". Any problem prints
      "STOP: <reason>", exits 1 and never prints the OK line. An added Bot (RN_BOT) passes its team name and
      team hash; if it is not registered yet, rn-cloud-add-bot.sh registers it first. Records N and the time in
      .git/agent-mesh/stub (an added Bot: .git/agent-mesh/bots/<slot>/stub).
  rn-routine.py request ID
      rn-cloud.sh request: if message ID is the user's own maintenance request (the signed copy is verified
      again, the sender is box/person, the first line is "agent-mesh update <tag> <commit> <req>",
      "agent-mesh add-bot <name> <req>" or "agent-mesh stub <req>" and every other line an allowed key=value),
      run it once per <req>, reply with rn-cloud.sh send and print "agent-mesh-request DONE ID"; a failure prints
      "STOP: <reason>". Any other message: "agent-mesh-request ORDINARY", nothing changed.
  rn-routine.py bootstrap N RUN-DIR|- ID TAG
      run by rn-cloud-update.sh --stdin of release TAG. Exit 3, nothing changed, unless ID is the user's update
      request to TAG (and, with RUN-DIR, complete in that run's fetch); otherwise update, reply, release the
      claims of that run and print "agent-mesh-bootstrap RELOAD" (with -: "agent-mesh-bootstrap DONE"), exit 0.
The first 8 hex digits of <req> are the Mac's Unix time when it made the request. A request is recorded in
.git/agent-mesh/requests/ before it runs; a run cut off part-way runs again from the start the next time (every
step is safe to repeat; a language is written only if no request with a later time and id has written one).
Requests made more than 30 days ago are refused and their records deleted. Standard library only.
"""
import fcntl, hashlib, importlib.util, json, os, re, shutil, sqlite3, subprocess, sys, time
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
BASE = Path(os.environ.get("RN_CLOUD_BASE", "/workspace/agent-mesh/box"))
ROOT = BASE.parent
CLOUD_ROOT = "/workspace/agent-mesh"
STUBS = (1,)
LANGS = ("en", "zh-CN")
CLOUD_FILES = ("box/rn-cloud.sh", "box/rn-fetch.py", "box/rn-cloud-setup.sh", "box/rn-engine.py",
               "box/rn-cloud-add-bot.sh", "box/rn-routine.py", "prompts/routine-body.md",
               "prompts/routine-body.zh-CN.md")
UPDATE_SCRIPT = "box/rn-cloud-update.sh"
OVERRIDES = ("AGENT_RELAY_NAME", "RN_CLOUD_NAME", "RN_INBOX_NAME", "RN_INBOX_DIR", "RN_THREADS_DIR")
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
RESERVED = re.compile(r"terminal(?:-[0-9]+)?")
REQ = r"[0-9a-f]{8,32}"
VERBS = {
    "update": (re.compile(rf"agent-mesh update (v[0-9]+\.[0-9]+\.[0-9]+) ([0-9a-f]{{40}}) ({REQ})"), ("lang",)),
    "add-bot": (re.compile(rf"agent-mesh add-bot ([a-z0-9]+(?:-[a-z0-9]+)*) ({REQ})"), ()),
    "stub": (re.compile(rf"agent-mesh stub ({REQ})"), ("lang",)),
}
PLACEHOLDERS = ("<team-name>", "<relay-name>", "<rn-cloud>", "<threads-dir>")
RESULT = "agent-mesh-result"
STUB_WINDOW = 900
REQUEST_DAYS = 30
RUN_DIR = re.compile(r"\.git/agent-mesh/boot/[A-Za-z0-9._-]+")
ORIG_ENV = dict(os.environ)


class Stop(Exception):
    pass


def valid_name(name):
    return bool(NAME.fullmatch(name)) and len(name) <= 34 and not RESERVED.fullmatch(name)


def valid_team(team):
    return bool(NAME.fullmatch(team)) and len(team) <= 40


def present(p):
    return p.exists() or p.is_symlink()


def child_env(**extra):
    env = {k: v for k, v in ORIG_ENV.items() if k not in ("RN_MAINT_FD", "RN_MAINT_MODE")}
    env.update(extra)
    return env


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def template(root, lang, name="routine-body"):
    suffix = "" if lang == "en" else f".{lang}"
    source = Path(root) / "prompts" / f"{name}{suffix}.md"
    blocks = re.findall(r"^```(?:text)?\n(.*?)^```\s*$", source.read_text(encoding="utf-8"), re.M | re.S)
    if len(blocks) != 1:
        raise ValueError(f"{source.name}: expected exactly one code block")
    return blocks[0]


def fill(text, values):
    keys = [k for k in PLACEHOLDERS if k in text]
    missing = [k for k in keys if not values.get(k)]
    if missing:
        raise ValueError(f"no value for {', '.join(missing)}")
    if not keys:
        return text
    return re.sub("|".join(map(re.escape, keys)), lambda m: values[m[0]], text)


def body_values(team, name, slot, threads=None):
    added = slot != "-"
    return {"<team-name>": team, "<relay-name>": name,
            "<rn-cloud>": (f"RN_BOT={slot} " if added else "") + f"{CLOUD_ROOT}/box/rn-cloud.sh",
            "<threads-dir>": threads or (f"{CLOUD_ROOT}/bots/{slot}/threads" if added else f"{CLOUD_ROOT}/threads")}


def render_body(root, lang, team, name, slot, threads=None):
    if lang not in LANGS:
        raise ValueError(f"unsupported language {lang!r}")
    return fill(template(root, lang), body_values(team, name, slot, threads))


def first_bot():
    p = BASE / "relay"
    if not p.exists():
        return "bot"
    try:
        name = p.read_text(encoding="utf-8").rstrip("\n")
    except (OSError, UnicodeDecodeError) as e:
        raise Stop(f"cannot read box/relay: {e}")
    if not valid_name(name):
        raise Stop("box/relay is damaged; it must hold the Bot address name the Mac uses (README: Troubleshooting)")
    return name


def selector(registered=True):
    given = [k for k in OVERRIDES if ORIG_ENV.get(k)]
    threads_shown = None
    if "RN_BOT" in ORIG_ENV:
        slot = ORIG_ENV["RN_BOT"]
        if not valid_name(slot):
            raise Stop(f"invalid RN_BOT {slot[:60]!r}: a Bot address name is 1-34 lowercase letters and digits, "
                       "groups joined by a single '-', not terminal")
        if given:
            raise Stop(f"RN_BOT cannot be combined with {given[0]}; nothing was run")
        if present(BASE / "bots-removed" / slot):
            raise Stop(f"the Bot {slot} was removed from this cloud (box/bots-removed/{slot}); nothing was run")
        bot = ROOT / "bots" / slot
        if bot.is_symlink() or (registered and not (bot / ".registered").is_file()):
            raise Stop(f"the Bot {slot} is not registered on this cloud; nothing was run")
        name, inbox_dir, threads_dir = slot, bot / "inbox", bot / "threads"
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
            name = first_bot()
            if present(BASE / "bots-removed" / name):
                raise Stop(f"the Bot {name} was removed from this cloud (box/bots-removed/{name}); nothing was run")
            inbox_dir, threads_dir = ROOT / "inbox", ROOT / "threads"
    return SimpleNamespace(slot=slot, name=name, inbox_dir=inbox_dir, threads_dir=threads_dir,
                           threads_shown=threads_shown, relay=ORIG_ENV.get("AGENT_RELAY_NAME") or name,
                           cloud=ORIG_ENV.get("RN_CLOUD_NAME") or f"{name}-cloud",
                           inbox=ORIG_ENV.get("RN_INBOX_NAME") or f"{name}-inbox")


def read_person():
    try:
        person = (BASE / "person").read_text(encoding="utf-8").rstrip("\n")
    except (OSError, UnicodeDecodeError) as e:
        raise Stop(f"cloud setup is incomplete; box/person cannot be read ({e})")
    if not valid_team(person):
        raise Stop("box/person does not hold a team name")
    return person


def read_lang():
    p = BASE / "lang"
    if not present(p):
        return "en"
    try:
        lang = p.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as e:
        raise Stop(f"box/lang cannot be read ({e})")
    if lang not in LANGS:
        raise Stop("box/lang must hold en or zh-CN")
    return lang


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


def put(p, text):
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)


def write_lang(lang):
    tmp = state_dir() / f".lang.{os.getpid()}.tmp"
    tmp.write_text(lang + "\n", encoding="utf-8")
    os.replace(tmp, BASE / "lang")


def made(req):
    return int(req[:8], 16)


def set_lang(lang, req):
    stamp = state_dir("requests") / ".lang"
    try:
        last = json.loads(stamp.read_text(encoding="utf-8"))
    except FileNotFoundError:
        last = None
    except (OSError, ValueError) as e:
        raise Stop(f"{stamp} cannot be read ({e})")
    last = last.get("req") if isinstance(last, dict) else None
    if isinstance(last, str) and re.fullmatch(REQ, last) and (made(last), last) > (made(req), req):
        return read_lang()
    put(stamp, json.dumps({"req": req, "lang": lang}))
    write_lang(lang)
    return None


def stub_file(slot):
    return state_dir() / "stub" if slot == "-" else state_dir("bots", slot) / "stub"


def git(*args):
    r = subprocess.run(["git", "--no-optional-locks", *args], cwd=ROOT, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, env=child_env(GIT_TERMINAL_PROMPT="0"))
    return r.returncode, r.stdout.strip()


def release():
    try:
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError as e:
        raise Stop(f"cannot read VERSION ({e})")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise Stop("VERSION is not a version")
    rc, head = git("rev-parse", "HEAD")
    rc2, tag = git("rev-parse", "-q", "--verify", f"refs/tags/v{version}^{{commit}}")
    if rc or rc2 or head != tag:
        raise Stop(f"this clone is not at the commit of release v{version} (an update may not have finished)")
    rc, changed = git("diff", "--no-ext-diff", "--name-only", "HEAD", "--", *CLOUD_FILES, UPDATE_SCRIPT)
    if rc or changed:
        raise Stop(f"files of release v{version} were changed in this clone: {' '.join(changed.split())}")
    return version


def claim_hash(slot):
    try:
        for line in (ROOT / "bots" / slot / ".claim").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition(" ")
            if key == "team":
                return value.strip()
    except (OSError, UnicodeDecodeError):
        pass
    return None


def maint_problem(mode):
    return module("rn_engine", HERE / "rn-engine.py").maint_problem(str(ROOT / ".maint.lock"), mode)


def instructions(args):
    if not args or not re.fullmatch(r"[0-9]{1,4}", args[0]) or int(args[0]) not in STUBS:
        raise Stop(f"stub version {args[0][:20] if args else '(none)'} is not supported by this cloud computer "
                   f"(it supports {', '.join(map(str, STUBS))}); paste the routine prompt that ./update.sh shows")
    n, rest = int(args[0]), args[1:]
    if len(rest) == 3 and rest[2] == "reclaim":
        raise Stop("this cloud computer cannot take back an earlier Bot name; update agent-mesh first (./update.sh)")
    if len(rest) not in (0, 2):
        raise Stop("usage: rn-cloud.sh instructions N [TEAM-NAME TEAM-HASH]")
    added = "RN_BOT" in ORIG_ENV
    if added != bool(rest):
        raise Stop("an added Bot's stub passes its team name and team hash, the first Bot's stub passes neither")
    if added and not (valid_team(rest[0]) and re.fullmatch(r"[0-9a-f]{16}", rest[1])):
        raise Stop("invalid team name or team hash in the stub")
    if not os.environ.get("RN_MAINT_FD"):
        sel = selector(registered=False)
        if added and not (ROOT / "bots" / sel.slot / ".registered").is_file():
            r = subprocess.run(["bash", str(BASE / "rn-cloud-add-bot.sh"), sel.slot, *rest], env=child_env(),
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            if r.returncode:
                raise Stop("registering this Bot on the cloud computer did not finish:\n" + r.stdout.rstrip())
        os.execv(sys.executable, [sys.executable, "-I", str(HERE / "rn-engine.py"), "hold", "--shared", "--wait",
                                  os.environ.get("RN_MAINT_WAIT", "600"), str(ROOT / ".maint.lock"), "--",
                                  sys.executable, "-I", str(Path(__file__).resolve()), "instructions", *args])
    problem = maint_problem("shared")
    if problem:
        raise Stop(f"the maintenance lock is not held ({problem})")
    sel = selector()
    team = read_person()
    if added:
        if rest[0] != team:
            raise Stop(f"box/person is {team}, not {rest[0]}; this stub belongs to another deployment")
        if claim_hash(sel.slot) != rest[1]:
            raise Stop(f"bots/{sel.slot} was claimed for another team than the one in this stub")
    version = release()
    lang = read_lang()
    try:
        body = render_body(ROOT, lang, team, sel.name, sel.slot, sel.threads_shown)
    except (OSError, ValueError) as e:
        raise Stop(f"the rules cannot be rendered: {e}")
    try:
        put(stub_file(sel.slot), json.dumps({"stub": n, "ts": time.time()}))
    except OSError as e:
        raise Stop(f"cannot record the stub version: {e}")
    sys.stdout.write(f"agent-mesh-instructions BEGIN {n} v{version} {lang} {sel.slot}\n{body}"
                     f"agent-mesh-instructions OK {n}\n")
    return 0


def load_fetch(sel, person):
    os.environ.update(RN_INBOX_DIR=str(sel.inbox_dir), RN_THREADS_DIR=str(sel.threads_dir), RN_BOX=str(BASE),
                      RN_PERSON=person, RN_KEY_FILE=str(BASE / "outbound.key"), RN_INBOX_NAME=sel.inbox,
                      RN_RELAY_NAME=sel.relay, RN_CLOUD_NAME=sel.cloud, REASON_NETWORK_HOME=str(BASE / "state"))
    return module("rn_fetch", HERE / "rn-fetch.py")


def fetch_env(sel, person):
    env = child_env()
    env.pop("RN_BOT", None)
    env.update(RN_INBOX_DIR=str(sel.inbox_dir), RN_THREADS_DIR=str(sel.threads_dir), RN_BOX=str(BASE),
               RN_PERSON=person, RN_KEY_FILE=str(BASE / "outbound.key"), RN_INBOX_NAME=sel.inbox,
               RN_RELAY_NAME=sel.relay, RN_CLOUD_NAME=sel.cloud, REASON_NETWORK_HOME=str(BASE / "state"))
    return env


def verify(fm, sel, mid, person):
    if not mid or fm.safe(mid) != mid or mid.startswith("_"):
        return None
    d = sel.inbox_dir / mid
    if d.is_symlink() or not d.is_dir():
        return None
    try:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        text = fm.read(d / "full.txt")
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or meta.get("id") != mid:
        return None
    if meta.get("text_len") != len(text) or meta.get("text_sha256") != fm.sha(text):
        return None
    try:
        key = (BASE / "outbound.key").read_text(encoding="utf-8").strip()
        rows = fm.rows(person, sel.inbox)
    except (OSError, sqlite3.Error) as e:
        raise Stop(f"the signed copy of {mid} cannot be checked: {e}")
    sender = None
    for _, _, body, _ in rows:
        f, signed, err = fm.parse(body, key)
        if err is None and f.get("id") == mid and signed == text:
            sender = f.get("from")
    if not sender or sender.count("/") != 1 or sender.partition("/")[0] != person:
        return None
    return sender, text


def parse_request(text):
    lines = text.split("\n")
    if len(lines) > 1 and lines[-1] == "":
        lines.pop()
    for verb, (rx, keys) in VERBS.items():
        m = rx.fullmatch(lines[0])
        if m:
            break
    else:
        return None
    extras = {}
    for line in lines[1:]:
        km = re.fullmatch(r"([a-z]+)=([A-Za-z0-9._-]{1,40})", line)
        if not km or km[1] not in keys or km[1] in extras:
            return None
        extras[km[1]] = km[2]
    if extras.get("lang", "en") not in LANGS:
        return None
    values = list(m.groups())
    return SimpleNamespace(verb=verb, args=values[:-1], req=values[-1], extras=extras)


class RequestLock:
    def __enter__(self):
        self.fh = open(state_dir("requests") / ".lock", "a")
        deadline = time.time() + float(os.environ.get("RN_REQUEST_WAIT", "1800"))
        while True:
            try:
                fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.time() >= deadline:
                    self.fh.close()
                    raise Stop("another maintenance request is still running; nothing was changed")
                time.sleep(1)

    def __exit__(self, *_):
        self.fh.close()


def record_file(req):
    return state_dir("requests") / f"{req}.json"


def load_record(req):
    try:
        rec = json.loads(record_file(req).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return rec if isinstance(rec, dict) else None


def save_record(req, rec):
    put(record_file(req), json.dumps(rec, ensure_ascii=False))


def prune_records(oldest):
    for f in state_dir("requests").glob("*.json"):
        if re.fullmatch(REQ + r"\.json", f.name) and made(f.stem) < oldest:
            try:
                f.unlink()
            except OSError:
                pass


def run_update(parsed, script, env):
    tag, commit = parsed.args
    r = subprocess.run(["bash", str(script), tag, commit, parsed.req], env=env, stdin=subprocess.DEVNULL,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    lines = r.stdout.rstrip("\n").split("\n") if r.stdout.strip() else []
    if lines[:1] == [f"{RESULT} request {parsed.req}"]:
        lines = lines[1:]
    if r.returncode == 0 and lines[-1:] == [f"{RESULT} DONE {tag}"]:
        return True, lines[:-1], tag
    if not any(line.startswith("STOP: ") for line in lines):
        lines.append(f"STOP: the update did not finish (exit {r.returncode})")
    return False, lines, tag


def execute(parsed, sel, update_script, update_env):
    if parsed.verb == "update":
        ok, lines, word = run_update(parsed, update_script, update_env)
    elif parsed.verb == "add-bot":
        name = parsed.args[0]
        if sel.slot == "-" or name != sel.name:
            return False, [f"STOP: this request is for the Bot {name}, but it reached the Bot {sel.name}"], "add-bot"
        r = subprocess.run(["bash", str(BASE / "rn-cloud-add-bot.sh"), "--check", name, parsed.req],
                           env=child_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, errors="replace")
        lines = r.stdout.rstrip("\n").split("\n") if r.stdout.strip() else []
        ok = r.returncode == 0 and bool(lines) and lines[-1].startswith(f"{RESULT} add-bot {parsed.req} ok {name} ")
        if not ok and not any(line.startswith("STOP: ") for line in lines):
            lines.append(f"STOP: the check did not finish (exit {r.returncode})")
        word = "add-bot"
    else:
        ok, lines, word = True, [], "stub"
    if ok and "lang" in parsed.extras:
        try:
            kept = set_lang(parsed.extras["lang"], parsed.req)
        except (OSError, Stop) as e:
            return False, lines + [f"STOP: box/lang cannot be written ({e})"], word
        if kept and kept != parsed.extras["lang"]:
            lines = lines + [f"box/lang stays {kept}: a request made later already set the language"]
    return ok, lines, word


def claim_time(sel, mid):
    p = sel.inbox_dir / mid / "claimed"
    try:
        c = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(c, dict) and isinstance(c.get("ts"), (int, float)):
            return float(c["ts"])
        return p.stat().st_mtime
    except (OSError, ValueError):
        try:
            return p.stat().st_mtime
        except OSError:
            return None


def seen_stub(sel, mid):
    try:
        rec = json.loads(stub_file(sel.slot).read_text(encoding="utf-8"))
        n, ts = int(rec["stub"]), float(rec["ts"])
    except (OSError, ValueError, KeyError, TypeError, Stop):
        return None
    claimed = claim_time(sel, mid)
    if claimed is None or not ts <= claimed <= ts + STUB_WINDOW:
        return None
    return n


def reply_text(req, ok, lines, word, stub):
    out = [f"{RESULT} request {req}", *lines]
    if ok:
        try:
            lang = read_lang()
        except Stop:
            lang = "unknown"
        out.append(f"{RESULT} lang {lang}")
        if stub:
            out.append(f"{RESULT} stub {stub}")
        out.append(f"{RESULT} DONE {word}")
    return "\n".join(out) + "\n"


def send(to, mid, text):
    r = subprocess.run(["bash", str(BASE / "rn-cloud.sh"), "send", to, mid], input=text, env=child_env(),
                       capture_output=True, text=True, errors="replace")
    return r.returncode == 0 and "rn-cloud: handed to the Mac" in r.stdout, (r.stdout + r.stderr).rstrip("\n")


def mark_done(sel, mid):
    if (sel.inbox_dir / mid / "done").exists():
        return
    subprocess.run(["bash", str(BASE / "rn-cloud.sh"), "done", mid], env=child_env(), stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def handle(sel, mid, sender, parsed, update_script, update_env, stub=None, after_ok=None):
    key = {"verb": parsed.verb, "args": parsed.args, "extras": parsed.extras}
    with RequestLock():
        oldest = time.time() - REQUEST_DAYS * 86400
        prune_records(oldest)
        rec = load_record(parsed.req)
        if made(parsed.req) < oldest:
            ok, rec = False, None
            lines, word = [f"STOP: the request {parsed.req} was made more than {REQUEST_DAYS} days ago by the Mac's "
                           "clock; nothing was run. Run ./update.sh on the Mac again"], parsed.verb
        elif rec and {k: rec.get(k) for k in key} != key:
            ok, rec = False, None
            lines, word = [f"STOP: the request id {parsed.req} was already used with other values; nothing was run"], \
                parsed.verb
        elif rec and rec.get("state") == "done":
            ok, lines, word = True, rec.get("lines", []), rec.get("word", parsed.verb)
        else:
            save_record(parsed.req, dict(key, state="running", started=time.time()))
            ok, lines, word = execute(parsed, sel, update_script, update_env)
            if ok:
                rec = dict(key, state="done", lines=lines, word=word, executed=time.time(), texts={}, replied=[])
                save_record(parsed.req, rec)
            else:
                try:
                    record_file(parsed.req).unlink()
                except OSError:
                    pass
        if ok and after_ok:
            after_ok()
        if ok and mid in rec.get("replied", []):
            text = rec["texts"].get(mid, "")
            sys.stdout.write(text)
            mark_done(sel, mid)
            return True, True
        if ok and mid in rec.get("texts", {}):
            text = rec["texts"][mid]
        else:
            text = reply_text(parsed.req, ok, lines, word, stub if stub is not None else
                              (seen_stub(sel, mid) if ok else None))
            if ok:
                rec["texts"][mid] = text
                save_record(parsed.req, rec)
        sys.stdout.write(text)
        sent, out = send(sender, mid, text)
        if out:
            print(out)
        if sent and ok:
            rec["replied"].append(mid)
            save_record(parsed.req, rec)
        return ok, sent


def request(args):
    if len(args) != 1:
        raise Stop("usage: rn-cloud.sh request <id>")
    mid = args[0]
    sel = selector()
    person = read_person()
    fm = load_fetch(sel, person)
    found = verify(fm, sel, mid, person)
    parsed = parse_request(found[1]) if found else None
    if parsed is None:
        print("agent-mesh-request ORDINARY")
        return 0
    ok, sent = handle(sel, mid, found[0], parsed, ROOT / UPDATE_SCRIPT, child_env())
    if not sent:
        raise Stop(f"the reply to {mid} was not handed to the Mac (see the lines above); run this command again later")
    if not ok:
        raise Stop(f"the request in {mid} failed; the reply above was sent to the Mac")
    print(f"agent-mesh-request DONE {mid}")
    return 0


def run_dir_problem(run, mid):
    if not RUN_DIR.fullmatch(run):
        return "the run folder must be a folder directly under .git/agent-mesh/boot/"
    d = ROOT / run
    for p in (ROOT / ".git", ROOT / ".git" / "agent-mesh", d.parent, d):
        if p.is_symlink() or not p.is_dir():
            return f"{p} is not a folder"
    for name in ("start", "snap", "fetch"):
        if (d / name).is_symlink() or not (d / name).is_file():
            return f"the run folder has no {name}"
    try:
        lines = (d / "fetch").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        return f"cannot read the fetch output ({e})"
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("id") == mid and r.get("status") == "complete":
            return None
    return f"{mid} is not complete in this run's fetch"


def clean_boot(keep):
    boot = ROOT / ".git" / "agent-mesh" / "boot"
    if boot.is_symlink() or not boot.is_dir():
        return
    now = time.time()
    for p in boot.iterdir():
        try:
            if p.resolve() in keep or p.is_symlink() or not p.is_dir() or now - p.stat().st_mtime < 86400:
                continue
            shutil.rmtree(p)
        except OSError:
            pass


def bootstrap(args):
    if len(args) != 4:
        print("usage: rn-routine.py bootstrap N RUN-DIR|- ID TAG", file=sys.stderr)
        return 3
    n, run, mid, tag = args
    if not ((n == "0" and run == "-") or (re.fullmatch(r"[0-9]{1,4}", n) and int(n) in STUBS and run != "-")):
        print(f"agent-mesh-bootstrap: stub version {n[:20]} with {run[:60]} is not supported", file=sys.stderr)
        return 3
    try:
        sel = selector()
        person = read_person()
    except Stop as e:
        print(f"agent-mesh-bootstrap: {e}", file=sys.stderr)
        return 3
    if run != "-":
        problem = run_dir_problem(run, mid)
        if problem:
            print(f"agent-mesh-bootstrap: {problem}", file=sys.stderr)
            return 3
    clean_boot({HERE, (ROOT / run).resolve()} if run != "-" else {HERE})
    fm = load_fetch(sel, person)
    try:
        found = verify(fm, sel, mid, person)
    except Stop as e:
        print(f"agent-mesh-bootstrap: {e}", file=sys.stderr)
        return 3
    parsed = parse_request(found[1]) if found else None
    if parsed is None or parsed.verb != "update" or parsed.args[0] != tag:
        print(f"agent-mesh-bootstrap: {mid} is not the user's update request to {tag}", file=sys.stderr)
        return 3
    stub = int(n) if int(n) > 0 else None

    def record_stub():
        if stub:
            try:
                put(stub_file(sel.slot), json.dumps({"stub": stub, "ts": time.time()}))
            except (OSError, Stop):
                pass

    env = child_env(RN_UPDATE_ROOT=str(ROOT), RN_UPDATE_ENGINE=str(HERE / "rn-engine.py"))
    try:
        ok, sent = handle(sel, mid, found[0], parsed, HERE / "rn-cloud-update.sh", env, stub, record_stub)
    except Stop as e:
        print(f"STOP: {e}")
        return 0
    if not sent:
        print(f"agent-mesh-bootstrap: the reply to {mid} was not handed to the Mac; it is sent again when this "
              "message is handled next time")
    if not ok:
        return 0
    if run != "-":
        r = subprocess.run([sys.executable, "-I", str(BASE / "rn-engine.py"), "hold", "--shared", "--wait",
                            os.environ.get("RN_MAINT_WAIT", "600"), str(ROOT / ".maint.lock"), "--",
                            sys.executable, "-I", str(BASE / "rn-fetch.py"), "release", str(ROOT / run)],
                           env=fetch_env(sel, person), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, text=True, errors="replace")
        if r.stdout.strip():
            print(r.stdout.rstrip("\n"))
        print("agent-mesh-bootstrap RELOAD")
    else:
        print("agent-mesh-bootstrap DONE")
    if HERE.parent == ROOT / ".git" / "agent-mesh" / "boot" and HERE.name.startswith("tools."):
        shutil.rmtree(HERE, ignore_errors=True)
    return 0


def main(argv):
    commands = {"instructions": instructions, "request": request, "bootstrap": bootstrap}
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
