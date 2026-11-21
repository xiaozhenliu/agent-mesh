#!/usr/bin/env python3
"""Bot address names: <name> for the Mac relay, <name>-cloud and <name>-inbox on the cloud.

  relay_names.py check NAME            exit 0 if NAME is a valid Bot address name, else 1 with the reason
  relay_names.py env [PATH]            print the name the relay env uses (empty line: no env file); exit 1 if the
                                       env is incomplete or its three names are not derived from one name
  relay_names.py registered NAME [--db PATH]
                                       print the session name the engine registered for `listen --as NAME`
                                       (empty: none); exit 3 without a database, 2 if it cannot be read
  relay_names.py marker [PATH]         print "<name> <pid> <mtime>" from the relay's registered marker (default in
                                       the relay state directory); exit 3 if there is none, 2 if it is not valid
  relay_names.py conflicts NAME [--db PATH]
                                       read `reason-network who --all --json` on stdin; exit 1 with the reason if
                                       NAME, NAME-cloud or NAME-inbox is already taken, 2 if it cannot be checked
  relay_names.py add-check PERSON NAME [--db PATH]
                                       read `reason-network who --all --json` on stdin; exit 1 with the reasons if a
                                       Bot named NAME cannot be added: NAME, NAME-cloud or NAME-inbox is a session of
                                       PERSON on any machine (online or offline) or a cli: key in this Mac's session
                                       table, or NAME is a Bot here or was removed; 2 if it cannot be checked
  relay_names.py name-check PERSON NAME [--slot SLOT] [--relay-pid PID] [--db PATH] [--ps-file PATH]
                                       read `reason-network who --all --json` on stdin and decide whether NAME can
                                       become the address name of the Bot SLOT (- for the first Bot; without --slot,
                                       a Bot being added). Prints "class new|own|removed|left|claim", then "evidence TEXT",
                                       "unread N" and "sender ADDRESS N" lines for a name taken back; exit 1 with the
                                       reasons if NAME cannot be used, 2 if it cannot be checked
  relay_names.py cloud-sessions PERSON NAME
                                       read `reason-network who --all --json` on stdin; exit 0 if NAME-cloud and
                                       NAME-inbox are both sessions of PERSON on the machine cloud, 1 if not, 2 if
                                       the list cannot be read
  relay_names.py grok-check NAME       exit 0 if NAME can be stored as the Bot's name in Grok, else 1 with the reason
  relay_names.py suggest GROK_NAME     print a Bot address name derived from the Bot's name in Grok; exit 1 if none
                                       can be derived (for example a name without Latin letters or digits)
  relay_names.py webhook PATH          exit 0 if the env file PATH sets a webhook (WEBHOOK_URL starting with https://
                                       and a WEBHOOK_KEY of at least 8 characters), else 1
  relay_names.py env-values PATH NAME...
                                       print NAME=value for each NAME the env file PATH sets (the last line wins;
                                       OUTBOUND_KEY_FILE with ~ expanded)
Apart from env-values, only names are printed, never other values from the env file.
"""
import json
import os
import plistlib
import re
import sqlite3
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

from messages import t

NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
RESERVED = re.compile(r"terminal(?:-[0-9]+)?")
MAX_LEN = 34
DEFAULT = "bot"
KEYS = ("LISTEN_NAME", "CLOUD_NAME", "INBOX_NAME")
RELAY_DEFAULTS = {"LISTEN_NAME": "bot", "CLOUD_NAME": "bot-cloud", "INBOX_NAME": "bot-inbox"}
CLOUD_MACHINE = "cloud"
GROK_MAX_LEN = 64
RELAY_LABEL = "com.local.agent-relay"
ENV_VARS = ("PATH", "REASON_NETWORK_HOME", "AGENT_RELAY_ENV", "AGENT_RELAY_STATE", "AGENT_RELAY_LOG")


def problem(name):
    if not isinstance(name, str) or not name:
        return t("names.empty")
    if len(name) > MAX_LEN:
        return t("names.too_long", repr(name[:60]), MAX_LEN)
    if not NAME.fullmatch(name):
        return t("names.bad_chars", repr(name[:60]))
    if RESERVED.fullmatch(name):
        return t("names.reserved", repr(name))
    return None


def grok_problem(name):
    if not isinstance(name, str) or not name.strip():
        return t("names.grok_empty")
    if len(name) > GROK_MAX_LEN:
        return t("names.grok_too_long", GROK_MAX_LEN)
    if name != name.strip():
        return t("names.grok_spaces")
    if any(unicodedata.category(c) in ("Cc", "Zl", "Zp") for c in name):
        return t("names.grok_control")
    return None


def suggest(grok_name):
    text = unicodedata.normalize("NFKD", grok_name)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    text = text[:MAX_LEN].rstrip("-")
    return text if problem(text) is None else None


def derived(name):
    return name, f"{name}-cloud", f"{name}-inbox"


def parse_env(text):
    cfg = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def webhook_set(cfg):
    return cfg.get("WEBHOOK_URL", "").startswith("https://") and len(cfg.get("WEBHOOK_KEY", "")) >= 8


def has_webhook(env):
    try:
        cfg = parse_env(Path(env).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return False
    return webhook_set(cfg)


def from_env(path):
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError(t("names.cannot_read", path, error.strerror or error))
    cfg = parse_env(text)
    if not cfg.get("RN_BIN"):
        raise ValueError(t("names.env_incomplete", path))
    listen, cloud, inbox = (cfg.get(k, RELAY_DEFAULTS[k]) for k in KEYS)
    why = problem(listen)
    if why:
        raise ValueError(t("names.env_listen", path, why))
    if (cloud, inbox) != derived(listen)[1:]:
        raise ValueError(t("names.env_not_derived", path, listen, cloud, inbox))
    return listen


def open_ro(path):
    return sqlite3.connect(f"{Path(path).absolute().as_uri()}?mode=ro", uri=True, timeout=5)


def default_db():
    return Path(os.environ.get("HOME", "~")).expanduser() / ".reason/network/network.db"


def registered(db, name):
    """Latest session name the engine stored for key cli:<name> (same query as the engine's session_by_key), or
    None. Raises FileNotFoundError without a database and sqlite3.Error if it cannot be read."""
    if not Path(db).exists():
        raise FileNotFoundError(str(db))
    con = open_ro(db)
    try:
        row = con.execute("SELECT name FROM sessions WHERE key = ? ORDER BY last_seen DESC LIMIT 1",
                          (f"cli:{name}",)).fetchone()
    finally:
        con.close()
    return row[0] if row is not None else None


def default_marker():
    home = Path(os.environ.get("HOME", "~")).expanduser()
    state = os.environ.get("AGENT_RELAY_STATE") or home / "Library/Application Support/agent-relay"
    return Path(state) / "registered"


def marker(path):
    """(name, pid, mtime) the relay wrote after its last listen. Raises FileNotFoundError or ValueError."""
    path = Path(path)
    st = path.stat()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError(t("names.marker_invalid", path, error))
    name, pid = (data.get("name"), data.get("pid")) if isinstance(data, dict) else (None, None)
    if (not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*", name)
            or not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0):
        raise ValueError(t("names.marker_fields", path))
    return name, pid, int(st.st_mtime)


def conflicts(directory, name, db):
    """Reasons why NAME cannot be used, from `who --all --json` and the local session table. Raises ValueError if
    the listing has an unexpected shape."""
    if not isinstance(directory, dict) or not isinstance(directory.get("sessions"), list):
        raise ValueError(t("names.no_sessions"))
    me_person, me_machine = directory.get("person"), directory.get("machine")
    wanted = derived(name)
    local = None
    if Path(db).exists():
        local = registered(db, name)
    found = []
    for s in directory["sessions"]:
        if not isinstance(s, dict) or not isinstance(s.get("name"), str):
            raise ValueError(t("names.session_unnamed"))
        if s["name"] not in wanted:
            continue
        person, machine = s.get("person"), s.get("machine")
        if person == me_person and machine == me_machine and s["name"] == name and local == name:
            continue
        if (person == me_person and machine == CLOUD_MACHINE and machine != me_machine
                and s["name"] in wanted[1:]):
            continue
        found.append(t("names.taken", f"{person}/{s['name']}", machine, s.get("status") or t("names.unknown")))
    if local is not None and local != name:
        found.append(t("names.key_taken", name, local))
    return found


def sessions_of(directory):
    if not isinstance(directory, dict) or not isinstance(directory.get("sessions"), list):
        raise ValueError(t("names.no_sessions"))
    for s in directory["sessions"]:
        if not isinstance(s, dict) or not isinstance(s.get("name"), str):
            raise ValueError(t("names.session_unnamed"))
    return directory["sessions"]


def local_keys(db, names):
    if not Path(db).exists():
        return []
    con = open_ro(db)
    try:
        return [(n, row[0]) for n in names
                for row in con.execute("SELECT name FROM sessions WHERE key = ?", (f"cli:{n}",)).fetchall()]
    finally:
        con.close()


def add_conflicts(directory, person, name, db, home):
    import bots
    why = problem(name)
    if why:
        return [why]
    data = bots.load_record(home)
    found = []
    if any(b["name"] == name for b in bots.roster(home, data)):
        found.append(t("names.add_is_bot", name))
    removed = data.get("removed_bots")
    if isinstance(removed, list) and name in removed:
        found.append(t("names.add_removed", name))
    return found + taken(directory, person, name, db)


def former_of(data):
    value = data.get("former")
    if not isinstance(value, list):
        return []
    return [f for f in value if isinstance(f, dict) and isinstance(f.get("name"), str) and problem(f["name"]) is None
            and isinstance(f.get("slot"), str) and isinstance(f.get("since_ms"), int)
            and isinstance(f.get("until_ms"), int) and f.get("reserved", False) in (False, True)]


def removed_of(data):
    value = data.get("removed_bots")
    return [n for n in value if isinstance(n, str)] if isinstance(value, list) else []


def owner(data, roster, name, slot):
    for b in roster:
        if b["slot"] != slot and name in (b["address"], b["name"] if b["kind"] == "added" else None):
            return "other", b["address"]
    for f in former_of(data):
        if f["name"] == name and f["slot"] != slot:
            return "other", next((b["address"] for b in roster if b["slot"] == f["slot"]), f["slot"])
    if slot is not None and ((slot != "-" and name == slot)
                             or any(f["name"] == name and f["slot"] == slot for f in former_of(data))):
        return "own", None
    if name in removed_of(data):
        return "removed", None
    return None, None


def local_names(db, names):
    if not Path(db).exists():
        return []
    con = open_ro(db)
    try:
        return [(n, row[0]) for n in names
                for row in con.execute("SELECT key FROM sessions WHERE name = ?", (n,)).fetchall()
                if row[0] != f"cli:{n}"]
    finally:
        con.close()


def local_traces(db, wanted):
    found = [t("names.add_key_taken", f"cli:{key}", session) for key, session in local_keys(db, wanted)]
    return found + [t("names.reclaim_key_other", session, key) for session, key in local_names(db, wanted)]


def taken(directory, person, name, db):
    found = []
    wanted = derived(name)
    for s in sessions_of(directory):
        if s.get("person") == person and s["name"] in wanted:
            found.append(t("names.taken", f"{person}/{s['name']}", s.get("machine") or t("names.unknown"),
                           s.get("status") or t("names.unknown")))
    return found + local_traces(db, wanted)


def reserved_only(data, name, slot):
    mine = [f for f in former_of(data) if f["name"] == name and f["slot"] == slot]
    return bool(mine) and all(f.get("reserved") is True for f in mine) and name != slot


def claim_asked(data, name, slot):
    return any(f["name"] == name and f["slot"] == slot and f.get("claim") is True for f in former_of(data))


def taken_elsewhere(directory, person, name, db):
    found = []
    wanted = derived(name)
    for s in sessions_of(directory):
        if s.get("person") != person or s["name"] not in wanted:
            continue
        if s["name"] != name and s.get("machine") == CLOUD_MACHINE:
            continue
        found.append(t("names.taken", f"{person}/{s['name']}", s.get("machine") or t("names.unknown"),
                       s.get("status") or t("names.unknown")))
    return found + local_traces(db, wanted)


def when(ms):
    if not isinstance(ms, int) or isinstance(ms, bool) or ms <= 0:
        return t("names.unknown")
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ms / 1000))


def session_problems(directory, person, name, cls):
    me = directory.get("machine")
    problems, evidence = [], []
    for i, wanted in enumerate(derived(name)):
        machine = me if i == 0 else CLOUD_MACHINE
        rows = [s for s in sessions_of(directory) if s.get("person") == person and s["name"] == wanted]
        if not rows and i:
            evidence.append(t("names.evidence_absent", f"{person}/{wanted}"))
        for s in rows:
            status, since = s.get("status") or t("names.unknown"), s.get("since")
            evidence.append(t("names.evidence_session", f"{person}/{wanted}", s.get("machine") or t("names.unknown"),
                              status, when(since)))
            if s.get("machine") != machine:
                problems.append(t("names.reclaim_machine", f"{person}/{wanted}", s.get("machine") or t("names.unknown"),
                                  machine))
            elif cls in ("left", "claim") and i == 0 and status != "offline":
                problems.append(t("names.reclaim_online", f"{person}/{wanted}", status))
    return problems, evidence


def key_problems(db, name):
    if not Path(db).exists():
        return [], []
    problems = []
    have = registered(db, name)
    if have is not None and have != name:
        problems.append(t("names.reclaim_key", name, have))
    for key, session in local_keys(db, derived(name)[1:]):
        problems.append(t("names.add_key_taken", f"cli:{key}", session))
    for session, key in local_names(db, derived(name)):
        problems.append(t("names.reclaim_key_other", session, key))
    return problems, ([t("names.evidence_key", name)] if have == name and not problems else [])


def listening(ps_text, name, relay_pid):
    found = []
    for line in ps_text.splitlines():
        parts = line.split(None, 2)
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        if relay_pid is not None and relay_pid in (parts[0], parts[1]):
            continue
        words = parts[2].split()
        if "listen" not in words:
            continue
        names = {words[i + 1] for i, w in enumerate(words[:-1]) if w == "--as"}
        names |= {w[5:] for w in words if w.startswith("--as=")}
        if names & set(derived(name)):
            found.append(t("names.reclaim_listening", parts[0], name, parts[2][:120]))
    return found


def env_names(path):
    try:
        cfg = parse_env(Path(path).expanduser().read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return set()
    names = {cfg.get(k) for k in KEYS}
    for item in cfg.get("FORMER_NAMES", "").split(","):
        names.add(item.split(":", 1)[0].strip())
    return {n for n in names if n}


def plist_users(agents, name, skip, home):
    found = []
    names = "|".join(re.escape(n) for n in derived(name))
    word = re.compile(r"(?<![A-Za-z0-9_.-])(?:" + names + r")(?![A-Za-z0-9_.-])")
    try:
        paths = sorted(Path(agents).glob("*.plist"))
    except OSError:
        paths = []
    for path in paths:
        if str(path) in skip:
            continue
        try:
            with open(path, "rb") as stream:
                data = plistlib.load(stream)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        label = str(data.get("Label") or path.name[:-6])
        env = data.get("EnvironmentVariables") if isinstance(data.get("EnvironmentVariables"), dict) else {}
        target = env.get("AGENT_RELAY_ENV") or (Path(home) / ".config/agent-relay/env" if label == RELAY_LABEL else None)
        hit = bool(target) and bool(set(derived(name)) & env_names(target))
        if not label.startswith(RELAY_LABEL):
            args = data.get("ProgramArguments") if isinstance(data.get("ProgramArguments"), list) else []
            words = [str(a) for a in args] + [str(data.get("Program") or "")]
            words += [str(v) for k, v in env.items() if k not in ENV_VARS]
            hit = hit or any(word.search(w) for w in words)
        if hit:
            found.append(t("names.reclaim_plist", path, name))
    return found


def unread_of(db, address):
    if not Path(db).exists():
        return []
    con = open_ro(db)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(messages)")}
        if not {"recipient", "sender", "read_at"} <= cols:
            return []
        return con.execute("SELECT sender, COUNT(*) FROM messages WHERE recipient = ? AND read_at IS NULL "
                           "GROUP BY sender ORDER BY sender", (address,)).fetchall()
    finally:
        con.close()


def own_paths(home, roster, slot, name):
    import bots
    if slot is None:
        return {bots.paths(home, name, bots.ADDED)["plist"]}
    return {b["plist"] for b in roster if b["slot"] == slot}


def name_check(directory, person, name, slot, db, home, ps_text, relay_pid=None):
    import bots
    why = problem(name)
    if why:
        return None, [why], [], []
    sessions_of(directory)
    data = bots.load_record(home)
    roster = bots.roster(home, data)
    if slot is not None and any(b["slot"] == slot and b["address"] == name for b in roster):
        return None, [t("names.same", name)], [], []
    cls, holder = owner(data, roster, name, slot)
    if cls == "other":
        return None, [t("names.add_is_bot", name) if slot is None else t("names.other_slot", name, holder)], [], []
    if cls == "own" and claim_asked(data, name, slot):
        cls = "claim"
    elif cls == "own" and reserved_only(data, name, slot):
        return "own", taken_elsewhere(directory, person, name, db), [], []
    if cls is None:
        if not taken(directory, person, name, db):
            return "new", [], [], []
        cls = "left"
    problems, evidence = session_problems(directory, person, name, cls)
    more, keys = key_problems(db, name)
    problems += more
    evidence += keys
    problems += listening(ps_text, name, relay_pid)
    problems += plist_users(Path(home) / "Library/LaunchAgents", name, own_paths(home, roster, slot, name), home)
    unread = unread_of(db, f"{person}/{name}") if not problems else []
    return cls, problems, evidence, unread


def ps_listing():
    try:
        done = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError(t("names.ps_failed", error))
    if done.returncode != 0:
        raise ValueError(t("names.ps_failed", done.stderr.decode("utf-8", "replace").strip()[:120] or done.returncode))
    return done.stdout.decode("utf-8", "replace")


def cloud_sessions(directory, person, name):
    have = {s["name"] for s in sessions_of(directory)
            if s.get("person") == person and s.get("machine") == CLOUD_MACHINE}
    return set(derived(name)[1:]) <= have


def option(args, flag):
    if flag not in args:
        return None
    i = args.index(flag)
    if i + 1 >= len(args):
        raise SystemExit(t("names.option_value", flag))
    value = args[i + 1]
    del args[i:i + 2]
    return value


def main(argv):
    args = list(argv)
    cmd = args.pop(0) if args else ""
    if cmd == "check" and len(args) == 1:
        why = problem(args[0])
        if why:
            print(why, file=sys.stderr)
            return 1
        return 0
    if cmd == "grok-check" and len(args) == 1:
        why = grok_problem(args[0])
        if why:
            print(why, file=sys.stderr)
            return 1
        return 0
    if cmd == "suggest" and len(args) == 1:
        name = suggest(args[0])
        if name is None:
            return 1
        print(name)
        return 0
    if cmd == "env" and len(args) <= 1:
        path = Path(args[0]) if args else Path(os.environ.get("HOME", "~")).expanduser() / ".config/agent-relay/env"
        try:
            name = from_env(path)
        except ValueError as error:
            print(error, file=sys.stderr)
            return 1
        print(name or "")
        return 0
    if cmd == "webhook" and len(args) == 1:
        return 0 if has_webhook(args[0]) else 1
    if cmd == "env-values" and args:
        with open(args[0], encoding="utf-8") as stream:
            cfg = parse_env(stream.read())
        for k in args[1:]:
            if k in cfg:
                v = os.path.expanduser(cfg[k]) if k == "OUTBOUND_KEY_FILE" else cfg[k]
                print(f"{k}={v}")
        return 0
    if cmd == "marker" and len(args) <= 1:
        path = Path(args[0]) if args else default_marker()
        try:
            print(*marker(path))
        except FileNotFoundError:
            return 3
        except ValueError as error:
            print(error, file=sys.stderr)
            return 2
        return 0
    if cmd == "name-check":
        db = option(args, "--db") or default_db()
        slot = option(args, "--slot")
        pid = option(args, "--relay-pid")
        ps_file = option(args, "--ps-file")
        if len(args) != 2 or (slot is not None and slot != "-" and problem(slot)):
            print(__doc__, file=sys.stderr)
            return 2
        person, name = args
        home = Path(os.environ.get("HOME", "~")).expanduser()
        try:
            directory = json.loads(sys.stdin.read())
            text = Path(ps_file).read_text(encoding="utf-8") if ps_file else ps_listing()
            cls, problems, evidence, unread = name_check(directory, person, name, slot, db, home, text,
                                                         pid if pid and pid.isdigit() else None)
        except (OSError, ValueError, sqlite3.Error) as error:
            print(t("names.check_failed", error), file=sys.stderr)
            return 2
        if problems:
            print("\n".join(problems), file=sys.stderr)
            return 1
        print(f"class {cls}")
        for line in evidence:
            print(f"evidence {line}")
        if cls != "new":
            print(f"unread {sum(n for _, n in unread)}")
            for sender, n in unread:
                print(f"sender {sender} {n}")
        return 0
    if cmd in ("add-check", "cloud-sessions"):
        db = option(args, "--db") or default_db()
        if len(args) != 2:
            print(__doc__, file=sys.stderr)
            return 2
        person, name = args
        try:
            directory = json.loads(sys.stdin.read())
            if cmd == "cloud-sessions":
                return 0 if cloud_sessions(directory, person, name) else 1
            found = add_conflicts(directory, person, name, db, Path(os.environ.get("HOME", "~")).expanduser())
        except (ValueError, sqlite3.Error) as error:
            print(t("names.check_failed", error), file=sys.stderr)
            return 2
        if found:
            print("\n".join(found), file=sys.stderr)
            return 1
        return 0
    if cmd in ("registered", "conflicts"):
        db = option(args, "--db") or default_db()
        if len(args) != 1:
            print(__doc__, file=sys.stderr)
            return 2
        name = args[0]
        if cmd == "registered":
            try:
                print(registered(db, name) or "")
            except FileNotFoundError:
                print(t("names.no_db", db), file=sys.stderr)
                return 3
            except sqlite3.Error as error:
                print(t("names.cannot_read", db, error), file=sys.stderr)
                return 2
            return 0
        try:
            found = conflicts(json.loads(sys.stdin.read()), name, db)
        except (ValueError, sqlite3.Error) as error:
            print(t("names.check_failed", error), file=sys.stderr)
            return 2
        if found:
            print("\n".join(found), file=sys.stderr)
            return 1
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
