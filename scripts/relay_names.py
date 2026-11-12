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
  relay_names.py grok-check NAME       exit 0 if NAME can be stored as the Bot's name in Grok, else 1 with the reason
  relay_names.py suggest GROK_NAME     print a Bot address name derived from the Bot's name in Grok; exit 1 if none
                                       can be derived (for example a name without Latin letters or digits)
Only names are printed, never other values from the env file.
"""
import json
import os
import re
import sqlite3
import sys
import unicodedata
from pathlib import Path

NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
RESERVED = re.compile(r"terminal(?:-[0-9]+)?")
MAX_LEN = 34
DEFAULT = "bot"
KEYS = ("LISTEN_NAME", "CLOUD_NAME", "INBOX_NAME")
RELAY_DEFAULTS = {"LISTEN_NAME": "bot", "CLOUD_NAME": "bot-cloud", "INBOX_NAME": "bot-inbox"}
CLOUD_MACHINE = "cloud"
GROK_MAX_LEN = 64


def problem(name):
    if not isinstance(name, str) or not name:
        return "the Bot address name is empty"
    if len(name) > MAX_LEN:
        return f"the Bot address name {name[:60]!r} is longer than {MAX_LEN} characters"
    if not NAME.fullmatch(name):
        return (f"the Bot address name {name[:60]!r} must be lowercase letters and digits, in groups joined by a "
                "single '-' (no '-' at the start or end, none repeated)")
    if RESERVED.fullmatch(name):
        return f"the Bot address name {name!r} is reserved: Reason Network uses terminal and terminal-<number>"
    return None


def grok_problem(name):
    if not isinstance(name, str) or not name.strip():
        return "the Bot's name in Grok is empty"
    if len(name) > GROK_MAX_LEN:
        return f"the Bot's name in Grok is longer than {GROK_MAX_LEN} characters"
    if name != name.strip():
        return "the Bot's name in Grok starts or ends with a space"
    if any(unicodedata.category(c) in ("Cc", "Zl", "Zp") for c in name):
        return "the Bot's name in Grok contains a line break or another control character"
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


def from_env(path):
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError(f"cannot read {path}: {error.strerror or error}")
    cfg = parse_env(text)
    if not cfg.get("RN_BIN"):
        raise ValueError(f"{path} is incomplete (it has no RN_BIN line); remove it and run ./install.sh --webhook "
                         "again, or restore it from a backup")
    listen, cloud, inbox = (cfg.get(k, RELAY_DEFAULTS[k]) for k in KEYS)
    why = problem(listen)
    if why:
        raise ValueError(f"{path}: LISTEN_NAME: {why}")
    if (cloud, inbox) != derived(listen)[1:]:
        raise ValueError(f"{path}: the relay names are not derived from one Bot address name: "
                         f"{listen}, {cloud}, {inbox}")
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
        raise ValueError(f"{path} is not valid: {error}")
    name, pid = (data.get("name"), data.get("pid")) if isinstance(data, dict) else (None, None)
    if (not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*", name)
            or not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0):
        raise ValueError(f"{path} has no valid name and pid")
    return name, pid, int(st.st_mtime)


def conflicts(directory, name, db):
    """Reasons why NAME cannot be used, from `who --all --json` and the local session table. Raises ValueError if
    the listing has an unexpected shape."""
    if not isinstance(directory, dict) or not isinstance(directory.get("sessions"), list):
        raise ValueError("the session list has no sessions array")
    me_person, me_machine = directory.get("person"), directory.get("machine")
    wanted = derived(name)
    local = None
    if Path(db).exists():
        local = registered(db, name)
    found = []
    for s in directory["sessions"]:
        if not isinstance(s, dict) or not isinstance(s.get("name"), str):
            raise ValueError("a session in the list has no name")
        if s["name"] not in wanted:
            continue
        person, machine = s.get("person"), s.get("machine")
        if person == me_person and machine == me_machine and s["name"] == name and local == name:
            continue
        if (person == me_person and machine == CLOUD_MACHINE and machine != me_machine
                and s["name"] in wanted[1:]):
            continue
        found.append(f"the session {person}/{s['name']} (machine {machine}, {s.get('status') or 'unknown'}) "
                     "already exists in this team; choose another --relay-name")
    if local is not None and local != name:
        found.append(f"this Mac's engine already registered the relay key for {name} as {local}; "
                     "choose another --relay-name")
    return found


def option(args, flag):
    if flag not in args:
        return None
    i = args.index(flag)
    if i + 1 >= len(args):
        raise SystemExit(f"relay_names: {flag} needs a value")
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
                print(f"no engine database at {db}", file=sys.stderr)
                return 3
            except sqlite3.Error as error:
                print(f"cannot read {db}: {error}", file=sys.stderr)
                return 2
            return 0
        try:
            found = conflicts(json.loads(sys.stdin.read()), name, db)
        except (ValueError, sqlite3.Error) as error:
            print(f"could not check the session names in this team ({error}); nothing was changed", file=sys.stderr)
            return 2
        if found:
            print("\n".join(found), file=sys.stderr)
            return 1
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
