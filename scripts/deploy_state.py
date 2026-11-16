#!/usr/bin/env python3
"""Deployment record for update.sh: what this Mac installed locally and last confirmed on the cloud side."""
import argparse
import hashlib
import importlib.util
import json
import os
import plistlib
import re
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
import urllib.error
import urllib.request
from pathlib import Path

import bots
import engine_config
import relay_names
from messages import t
from render_prompt import CLOUD_FILES, ROOT, TEAM, render_prompt
from write_launch_agent import launch_agent_bytes

SCHEMA = 1
REMOTE = ("cloud", "cloud_setup", "prompt", "cloud_engine")
CLOUD_PARTS = ("cloud", "cloud_setup", "cloud_engine")
UPDATE_SCRIPT = "box/rn-cloud-update.sh"
CLOUD_DIGEST = ("box/rn-cloud.sh", "box/rn-fetch.py", UPDATE_SCRIPT, "box/rn-cloud-add-bot.sh")
NEEDS = ("cloud", "prompt", "cloud_engine")
STATES = ("pending", "confirmed")
TXN_KEYS = ("stage", "relay_was_loaded", "need", "lang", "commit", "target")
STAGES = ("stopping", "relay_stopped", "routine_paused", "cloud_done", "prompt_done", "routine_resumed")
PYTHON_CHECK = "import sys, ssl, sqlite3, plistlib; sys.exit(sys.version_info < (3, 9))"
PYTHON_PICKS = ("/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3")
TEMP_ROOTS = ("/tmp", "/private/tmp", "/var/folders", "/private/var/folders")
CONFIG_KEYS = ("auto_update", "lan")


class NoFirstBot(ValueError):
    pass


def home():
    return Path(os.environ["HOME"])


def lib_dir():
    return home() / ".local/lib/agent-mesh"


def engine_txn_path():
    return lib_dir() / "engine-upgrade.json"


def rn_engine():
    spec = importlib.util.spec_from_file_location("rn_engine", ROOT / "box/rn-engine.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pinned():
    m = re.search(r'^VERSION="v(\d+\.\d+\.\d+)"$', (ROOT / "install.sh").read_text(encoding="utf-8"), re.M)
    if not m:
        raise ValueError("install.sh has no pinned Reason Network VERSION")
    return m.group(1)


def engine_status():
    """older | stale | unchanged | refused, and the installed version (or -)."""
    eng = rn_engine()
    binary = home() / ".local/bin/reason-network"
    if not binary.exists():
        return "refused", "-"
    have = eng.version_of(binary)
    state = eng.compare(have, pinned())
    if state == "older":
        return "older", have
    if state != "same":
        return "refused", have or "-"
    try:
        _, _, _, running = eng.info(home() / ".reason/network", binary)
    except OSError:
        return "unchanged", have
    return ("stale" if running == "stale" else "unchanged"), have


def record_path():
    return lib_dir() / "deployed.json"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def env_path():
    return home() / ".config/agent-relay/env"


def relay_name(fallback=None):
    if fallback and relay_names.problem(fallback):
        raise ValueError(relay_names.problem(fallback))
    name = relay_names.from_env(env_path())
    if name is None:
        if fallback:
            return fallback
        if bots.legacy_suppressed(home(), load(warn=False)):
            raise NoFirstBot(t("state.no_first_bot", env_path()))
        return relay_names.DEFAULT
    if fallback and fallback != name:
        raise ValueError(t("state.env_name_differs", env_path(), name, fallback))
    return name


def recorded_name(data):
    value = data.get("relay_name")
    return value if isinstance(value, str) and relay_names.problem(value) is None else None


def recorded_grok(data):
    value = data.get("grok_name")
    return value if relay_names.grok_problem(value) is None else None


def bot_label(person, name, grok):
    address = f"{person}/{name}" if person else name
    return f"{address} → Grok: {grok}" if grok else address


def name_status(data, fallback=None):
    current = relay_name(fallback)
    recorded = recorded_name(data)
    if recorded is None:
        return "unknown"
    return "ok" if recorded == current else f"changed {recorded} {current}"


def legacy_name_status(data, fallback=None):
    try:
        return name_status(data, fallback)
    except NoFirstBot:
        return None


def digest(field, lang, relay=None):
    if field == "cloud":
        parts = [sha((ROOT / name).read_bytes()) for name in CLOUD_DIGEST]
        return sha("\n".join(parts).encode())
    if field == "cloud_setup":
        return sha((ROOT / "box/rn-cloud-setup.sh").read_bytes())
    if field == "prompt":
        return sha(prompt_text(lang, relay).encode("utf-8"))
    if field == "cloud_engine":
        return sha(f"reason-network {pinned()}\n{sha((ROOT / 'box/rn-engine.py').read_bytes())}".encode())
    raise ValueError(f"unknown field: {field}")


def added_bot(relay, data):
    if relay is None or relay_names.problem(relay) or relay_names.from_env(env_path()) == relay:
        return None
    entries = {name: entry for name, entry in bots_of(data).items() if isinstance(entry, dict)}
    bot = bots.find(home(), relay, dict(data, bots=entries))
    return bot if bot is not None and bot["kind"] == bots.ADDED else None


def prompt_text(lang, relay=None):
    data = load(warn=False) if relay is not None else {}
    bot = added_bot(relay, data)
    if bot is None:
        return render_prompt("routine", lang, relay_name=relay_name(relay))
    try:
        text = Path(bot["env"]).read_text(encoding="utf-8", errors="replace")
        person = relay_names.parse_env(text).get("PERSON", "")
    except OSError:
        person = ""
    if not (TEAM.fullmatch(person) and len(person) <= 40):
        raise ValueError(t("state.bot_no_team", relay, bot["env"]))
    team_hash = (bot_entry(data, relay) or {}).get("team_hash")
    if not isinstance(team_hash, str) or not re.fullmatch(r"[0-9a-f]{16}", team_hash):
        raise ValueError(t("state.bot_no_team_hash", relay, record_path(), relay))
    return render_prompt("routine", lang, team_name=person, bot_selector=relay, team_hash=team_hash)


def load(warn=True):
    path = record_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        if warn:
            print(t("state.unreadable_unknown", path, exc), file=sys.stderr)
        return {}
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        if warn:
            print(t("state.format_unknown", path), file=sys.stderr)
        return {}
    return data


def save(data):
    data = dict(data, schema=SCHEMA)
    path = record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".deployed.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def entry(data, field):
    value = data.get(field)
    if (isinstance(value, dict) and value.get("state") in STATES and isinstance(value.get("hash"), str)
            and re.fullmatch(r"[0-9a-f]{64}", value["hash"])):
        return value
    return None


def remote_status(data, field, lang):
    value = entry(data, field)
    if value is None:
        return "unknown"
    try:
        current = digest(field, lang)
    except NoFirstBot:
        return "unknown"
    if value["hash"] != current:
        return "changed"
    return "unchanged" if value["state"] == "confirmed" else "pending"


def same_file(installed, expected):
    try:
        return installed.read_bytes() == expected
    except OSError:
        return False


def plist_bytes(python, bot):
    relay = str(lib_dir() / "agent_relay.py")
    if bot["kind"] == bots.LEGACY:
        return launch_agent_bytes(home(), python, relay)
    return launch_agent_bytes(home(), python, relay, label=bot["label"], env=bot["env"], state=bot["state_dir"],
                              log=bot["log"], launchd_log=bot["launchd_log"])


def code_current():
    return (same_file(lib_dir() / "agent_relay.py", (ROOT / "agent_relay.py").read_bytes())
            and not (lib_dir() / "rollback").exists())


def cohort(roster):
    return [b for b in roster if b["stage"] == "relay" and not b["removing"]]


def lenient_roster():
    try:
        return bots.roster(home())
    except ValueError:
        return None


def relay_status(python, roster=None):
    members = cohort(roster if roster is not None else lenient_roster() or [])
    if not members:
        members = [bots.paths(home(), None, bots.LEGACY)]
    ok = code_current() and all(same_file(Path(b["plist"]), plist_bytes(python, b)) for b in members)
    return "unchanged" if ok else "changed"


def probe(python, code):
    try:
        done = subprocess.run([python, "-c", code], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.returncode


def developer_tools():
    try:
        done = subprocess.run(["/usr/bin/xcode-select", "-p"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def python_usable(python):
    if not isinstance(python, str) or not os.path.isabs(python) or any(ord(c) < 32 for c in python):
        return False
    if python != python.strip():
        return False
    if not os.path.isfile(python) or not os.access(python, os.X_OK):
        return False
    if sys.platform == "darwin" and os.path.realpath(python) == "/usr/bin/python3" and not developer_tools():
        return False
    return probe(python, PYTHON_CHECK) == 0


def python_in_venv(python):
    path = Path(python)
    if (path.parent / "pyvenv.cfg").is_file() or (path.parent.parent / "pyvenv.cfg").is_file():
        return True
    return probe(python, "import sys; sys.exit(sys.prefix != sys.base_prefix)") == 1


def python_in_temp(python):
    roots = list(TEMP_ROOTS)
    tmp = os.environ.get("TMPDIR", "").rstrip("/")
    if tmp:
        roots += [tmp, os.path.realpath(tmp)]
    return any(candidate == root or candidate.startswith(root + "/")
               for candidate in (python, os.path.realpath(python)) for root in roots)


def python_notes(python):
    notes = []
    if python_in_venv(python):
        notes.append("venv")
    if python_in_temp(python):
        notes.append("temp")
    return notes


def pick_python():
    given = os.environ.get("AGENT_MESH_PYTHON_CANDIDATES")
    names = PYTHON_PICKS if given is None else [name for name in given.split(":") if name]
    for name in names:
        if python_usable(name) and not python_notes(name):
            return name
    return None


def relay_members(roster=None):
    members = cohort(roster if roster is not None else lenient_roster() or [])
    return members or [bots.paths(home(), None, bots.LEGACY)]


def plist_python(bot):
    try:
        with open(bot["plist"], "rb") as stream:
            arguments = plistlib.load(stream).get("ProgramArguments")
    except Exception:
        return None
    if isinstance(arguments, list) and arguments and isinstance(arguments[0], str) and arguments[0]:
        return arguments[0]
    return None


def recorded_python(data):
    value = data.get("relay_python")
    return value if isinstance(value, str) and value else None


def resolve_python(data, roster=None):
    recorded = recorded_python(data)
    if recorded and python_usable(recorded):
        return recorded, "recorded", None
    found = next((p for p in map(plist_python, relay_members(roster)) if p), None)
    if found and python_usable(found):
        return found, "plist", None
    picked = pick_python()
    return picked, "picked" if picked else "none", recorded or found


def python_mixed(python, roster=None):
    found = [plist_python(bot) for bot in relay_members(roster)]
    return any(p is not None and p != python for p in found)


def has_webhook(env):
    try:
        cfg = relay_names.parse_env(Path(env).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return False
    return cfg.get("WEBHOOK_URL", "").startswith("https://") and len(cfg.get("WEBHOOK_KEY", "")) >= 8


def bot_prompt_status(data, name, lang):
    value = (bot_entry(data, name) or {}).get("prompt")
    if not (isinstance(value, dict) and value.get("state") in STATES and isinstance(value.get("hash"), str)):
        return "unknown"
    try:
        current = digest("prompt", lang, name)
    except ValueError:
        return "unknown"
    if value["hash"] != current:
        return "changed"
    return "unchanged" if value["state"] == "confirmed" else "pending"


def bot_lines(data, roster, python, lang):
    removing = removing_of(data)
    for b in roster:
        if b["kind"] == bots.LEGACY:
            prompt = remote_status(data, "prompt", lang)
        else:
            prompt = bot_prompt_status(data, b["name"], lang)
        relay = "-"
        if b["stage"] == "relay":
            relay = "unchanged" if code_current() and same_file(Path(b["plist"]), plist_bytes(python, b)) else "changed"
        step = removing.get("step") if b["removing"] and isinstance(removing.get("step"), str) else "-"
        yield (f"bot {b['name']} kind {b['kind']} stage {b['stage']} webhook "
               f"{'ok' if has_webhook(b['env']) else 'missing'} relay {relay} prompt {prompt} cloud {b['cloud']} "
               f"removing {step if b['removing'] else '-'} record {b['record']}")


def dsh_status():
    plugin = home() / ".dsh/plugins/reason-network-bridge.mjs"
    if not plugin.exists():
        return "absent"
    return "unchanged" if same_file(plugin, (ROOT / "dsh/reason-network-bridge.mjs").read_bytes()) else "changed"


def valid_participant(value):
    return (isinstance(value, dict) and isinstance(value.get("name"), str) and isinstance(value.get("label"), str)
            and isinstance(value.get("plist"), str) and isinstance(value.get("was_loaded"), bool))


def valid_txn(txn):
    if not isinstance(txn, dict):
        return False
    participants = txn.get("participants")
    loaded = isinstance(txn.get("relay_was_loaded"), bool) if participants is None else (
        isinstance(participants, list) and all(valid_participant(p) for p in participants))
    return (loaded and txn.get("stage") in STAGES
            and isinstance(txn.get("need"), list) and all(part in NEEDS for part in txn["need"])
            and txn.get("lang") in ("en", "zh-CN") and isinstance(txn.get("commit"), str)
            and isinstance(txn.get("target"), dict))


def txn_participants(txn, data=None):
    if txn.get("participants") is not None:
        return [dict(p) for p in txn["participants"]]
    legacy = bots.paths(home(), None, bots.LEGACY)
    name = recorded_name(data or {}) or relay_names.from_env(env_path()) or relay_names.DEFAULT
    return [{"name": name, "label": legacy["label"], "plist": legacy["plist"], "was_loaded": txn["relay_was_loaded"]}]


def bots_of(data):
    value = data.get("bots")
    return value if isinstance(value, dict) else {}


def bot_entry(data, name):
    value = bots_of(data).get(name)
    return value if isinstance(value, dict) else None


def removing_of(data):
    return bots.removing_of(data)


def removed_bots(data):
    value = data.get("removed_bots")
    return [n for n in value if isinstance(n, str)] if isinstance(value, list) else []


def legacy_removed(data):
    return data.get("legacy_removed") is True


def txn_of(data, warn=False):
    txn = data.get("txn")
    if txn is None or valid_txn(txn):
        return txn
    if warn:
        print(t("state.txn_damaged"), file=sys.stderr)
    return None


def engine_txn_stage():
    path = engine_txn_path()
    if not path.exists():
        return "-"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        stage = data.get("stage")
    except (OSError, ValueError, AttributeError):
        return "damaged"
    if data.get("phase") == "committed":
        return "committed"
    return stage if isinstance(stage, str) and re.fullmatch(r"[a-z_]+", stage) else "damaged"


def daemon_start():
    eng = rn_engine()
    pid = eng.daemon_pid(home() / ".reason/network")
    return pid, (eng.started(pid) if pid else None)


def config_record(data):
    value = data.get("engine_config")
    if value is None:
        return None
    return value if isinstance(value, dict) else {}


def config_loaded(record):
    pid, start = daemon_start()
    if pid is None:
        return True
    written = record.get("written_s")
    if not isinstance(written, int) or isinstance(written, bool) or start is None:
        return False
    return int(start) >= written


def config_check():
    return engine_config.check(home() / ".reason/network/config.toml", CONFIG_KEYS)


def config_status(data):
    result = config_check()
    if result == engine_config.UNSUPPORTED:
        return "unsupported"
    if result != engine_config.OFF:
        return "repair"
    record = config_record(data)
    return "ok" if record is None or config_loaded(record) else "reload"


def cmd_status(args):
    data = load()
    roster = lenient_roster()
    python = args.python or resolve_python(data, roster)[0] or sys.executable
    print(f"relay {relay_status(python, roster)}")
    print(f"dsh {dsh_status()}")
    for field in REMOTE:
        print(f"{field} {remote_status(data, field, args.lang)}")
    txn = txn_of(data, warn=True)
    print(f"txn {txn['stage'] if txn else '-'}")
    state, have = engine_status()
    print(f"engine {state} {have} {pinned()}")
    print(f"etxn {engine_txn_stage()}")
    recorded = entry(data, "cloud_engine")
    print(f"cloud_engine_version {recorded.get('version') or '-' if recorded else '-'}")
    print(f"cloud_release {cloud_release(data) or '-'}")
    request = request_of(data)
    print(f"request {request['req'] + ' ' + request['mode'] if request else '-'}")
    print(f"config {config_status(data)}")
    try:
        status = name_status(data)
        current = relay_name()
    except NoFirstBot:
        status, current = "unknown", "-"
    print(f"name {status}" if status.startswith("changed ") else f"name {status} {current}")
    if roster is None:
        print("bots unreadable")
        return
    for line in bot_lines(data, roster, python, args.lang):
        print(line)


def known_entry(data, name):
    value = bot_entry(data, name)
    if value is None:
        raise ValueError(t("state.bot_unrecorded", name, record_path()))
    return value


def mark_bot_prompt(data, args):
    item = known_entry(data, args.relay_name)
    value = {"hash": digest("prompt", args.lang, args.relay_name), "state": args.state}
    old = item.get("prompt") if isinstance(item.get("prompt"), dict) else {}
    if args.expect and args.expect != value["hash"]:
        raise SystemExit(t("state.checkout_moved", "prompt"))
    if args.if_pending and not (old.get("state") == "pending" and old.get("hash") == value["hash"]):
        raise SystemExit(t("state.record_moved", "prompt"))
    item["prompt"] = value
    data["lang"] = args.lang
    save(data)


def cmd_mark(args):
    data = load_strict()
    if added_bot(args.relay_name, data):
        if args.field == "prompt":
            return mark_bot_prompt(data, args)
        args.relay_name = None
    if args.field == "prompt":
        status = name_status(data, args.relay_name)
    else:
        status = legacy_name_status(data, args.relay_name)
    if status is not None and status.startswith("changed "):
        _, recorded, current = status.split()
        raise SystemExit(t("state.renamed_not_recorded", recorded, current))
    value = {"hash": digest(args.field, args.lang, args.relay_name), "state": args.state}
    if args.field == "cloud_engine":
        value["version"] = pinned()
    old = entry(data, args.field)
    if args.expect and args.expect != value["hash"]:
        raise SystemExit(t("state.checkout_moved", args.field))
    if args.if_pending and not (old and old["state"] == "pending" and old["hash"] == value["hash"]):
        raise SystemExit(t("state.record_moved", args.field))
    data[args.field] = value
    if status == "unknown":
        data["relay_name"] = relay_name(args.relay_name)
    if args.field == "prompt":
        data["lang"] = args.lang
    stamp_release(data)
    save(data)


def version():
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def cloud_release(data):
    value = data.get("cloud")
    release_ = value.get("release") if isinstance(value, dict) else None
    return release_ if isinstance(release_, str) and re.fullmatch(r"\d+\.\d+\.\d+", release_) else None


def stamp_release(data):
    if all(remote_status(data, field, "en") == "unchanged" for field in CLOUD_PARTS):
        data["cloud"]["release"] = version()


def load_strict(plain=False):
    path = record_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise ValueError(t("state.unreadable_plain" if plain else "state.unreadable_strict", path, exc))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError(t("state.format_plain" if plain else "state.format_strict", path))
    return data


def cmd_set_grok_name(args):
    why = relay_names.grok_problem(args.grok_name)
    if why:
        raise ValueError(why)
    data = load_strict()
    if added_bot(args.relay_name, data):
        item = known_entry(data, args.relay_name)
        if not args.check:
            item["grok_name"] = args.grok_name
            save(data)
        return
    status = name_status(data, args.relay_name)
    if status.startswith("changed "):
        _, recorded, current = status.split()
        raise ValueError(t("state.renamed", recorded, current))
    if args.check:
        return
    data["grok_name"] = args.grok_name
    if status == "unknown":
        data["relay_name"] = relay_name(args.relay_name)
    save(data)


def utf8_stdout():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def cmd_label(args):
    data = load(warn=False)
    if added_bot(args.relay_name, data):
        utf8_stdout()
        print(bot_label(args.person, args.relay_name, bots.grok_of(bot_entry(data, args.relay_name))))
        return
    name = relay_names.from_env(env_path()) or recorded_name(data) or args.relay_name
    if not name:
        raise SystemExit(3)
    utf8_stdout()
    print(bot_label(args.person, name, recorded_grok(data)))


def cmd_recorded_name(_args):
    try:
        data = load_strict(plain=True)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(4)
    name = recorded_name(data)
    if not name:
        raise SystemExit(3)
    print(name)


def cmd_check_name(args):
    print(name_status(load(warn=False), args.relay_name))


def cmd_show_prompt(args):
    sys.stdout.write(prompt_text(args.lang, args.relay_name))


def cmd_lang(_args):
    lang = load(warn=False).get("lang")
    print(lang if lang in ("en", "zh-CN") else "")


def parse_participants(text):
    try:
        value = json.loads(text)
    except ValueError:
        value = None
    if not (isinstance(value, list) and all(valid_participant(p) for p in value)):
        raise ValueError(t("state.bad_participants"))
    return [{k: p[k] for k in ("name", "label", "plist", "was_loaded")} for p in value]


def print_participants(participants):
    for p in participants:
        print(f"{p['name']}\t{p['label']}\t{p['plist']}\t{1 if p['was_loaded'] else 0}")


def prompt_bots(data):
    return [b["name"] for b in bots.roster(home(), data)
            if not b["removing"] and (b["kind"] == bots.LEGACY or b["cloud"] == "confirmed")]


def txn_target(data, need, lang, names):
    target = {part: digest(part, lang) for part in need if part != "prompt"}
    if "prompt" in need and not data.get("bots") and not data.get("legacy_removed"):
        target["prompt"] = digest("prompt", lang)
    elif "prompt" in need:
        names = prompt_bots(data) if names is None else [n for n in names.split(",") if n]
        target["prompt"] = {name: digest("prompt", lang, name) for name in names}
    return target


def cmd_txn_start(args):
    data = load_strict()
    need = [part for part in args.need.split(",") if part]
    txn = {"stage": "stopping", "need": need, "lang": args.lang, "commit": args.commit,
           "target": txn_target(data, need, args.lang, args.prompt_bots)}
    if args.participants is not None:
        txn["participants"] = parse_participants(args.participants)
    elif args.was is not None:
        txn["relay_was_loaded"] = args.was == "1"
    else:
        raise ValueError(t("state.bad_participants"))
    data["txn"] = txn
    save(data)


def live_participants(participants):
    live = {b["label"] for b in cohort(bots.roster(home()))}
    return [p for p in participants if p["label"] in live]


def cmd_txn_participants(_args):
    data = load(warn=False)
    txn = txn_of(data)
    if txn:
        print_participants(live_participants(txn_participants(txn, data)))


def cmd_etxn_participants(_args):
    path = engine_txn_path()
    try:
        extra = json.loads(path.read_text(encoding="utf-8")).get("extra") or {}
    except (OSError, ValueError, AttributeError):
        extra = {}
    if not isinstance(extra, dict):
        extra = {}
    if isinstance(extra.get("participants"), str):
        print_participants(live_participants(parse_participants(extra["participants"])))
        return
    was = extra.get("relay_was_loaded", "1") != "0"
    print_participants(live_participants(txn_participants({"relay_was_loaded": was}, load(warn=False))))


def cmd_txn_get(args):
    data = load(warn=False)
    txn = txn_of(data)
    if not txn:
        return
    value = txn.get(args.key)
    if args.key == "relay_was_loaded" and value is None:
        value = any(p["was_loaded"] for p in txn_participants(txn, data))
    if isinstance(value, bool):
        print("1" if value else "0")
    elif isinstance(value, list):
        print(",".join(value))
    elif value is not None:
        print(value)


def cmd_txn_set(args):
    data = load_strict()
    txn = txn_of(data)
    if not txn:
        raise SystemExit(t("state.no_txn"))
    if args.key == "need":
        txn["need"] = [part for part in args.value.split(",") if part]
        txn["target"] = txn_target(data, txn["need"], txn.get("lang", "en"), args.prompt_bots)
    else:
        txn[args.key] = args.value
    save(data)


ENGINE_LINE = re.compile(r"engine: reason-network (\S+) running \(daemon pid \d+, started ")
BUILD_LINE = re.compile(r"engine: pinned build ([0-9a-f]{64}) at box/bin/engine/reason-network")
DEFAULT_REPO_URL = "https://github.com/xiaozhenliu/agent-mesh.git"


def release_tag():
    return "v" + version()


def git(*args, cwd=None):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="true", LC_ALL="C")
    return subprocess.run(("git",) + args, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=180, check=False)


def release(url):
    tag = release_tag()
    with tempfile.TemporaryDirectory(prefix="agent-mesh-release.") as tmp:
        try:
            steps = (("init", "-q", "--bare", tmp),
                     ("-C", tmp, "fetch", "-q", "--depth", "1", "--no-tags", url, f"refs/tags/{tag}:refs/tags/{tag}"))
            for step in steps:
                done = git(*step)
                if done.returncode != 0:
                    why = done.stderr.decode("utf-8", "replace").strip().splitlines()
                    raise ValueError(t("release.fetch_failed", tag, url, why[-1] if why else "git failed"))
            done = git("-C", tmp, "rev-parse", "--verify", "-q", f"refs/tags/{tag}^{{commit}}")
            commit = done.stdout.decode().strip()
            if done.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", commit):
                raise ValueError(t("release.not_commit", tag, url))
            sums = []
            for name in CLOUD_FILES + (UPDATE_SCRIPT,):
                done = git("-C", tmp, "cat-file", "blob", f"{commit}:{name}")
                if done.returncode != 0:
                    raise ValueError(t("release.missing_file", tag, name))
                want = sha(done.stdout)
                if want != sha((ROOT / name).read_bytes()):
                    raise ValueError(t("release.differs", name, tag))
                if name in CLOUD_FILES:
                    sums.append((want, name))
        except (OSError, subprocess.SubprocessError) as error:
            raise ValueError(t("release.check_failed", tag, url, error))
    return tag, commit, sums


def cmd_release(args):
    tag, commit, sums = release(args.repo_url)
    print(f"tag {tag}")
    print(f"commit {commit}")
    for digest_, name in sums:
        print(f"sum {digest_}  {name}")


LEAD = r"^[ \t>`*_-]*"
TAIL = r"[ \t`*_]*$"


def reply_problems(text, commit, tag=None, engine=None):
    problems = []
    if re.search(LEAD + r"STOP:", text, re.M):
        problems.append(t("reply.stop"))
    result = LEAD + r"agent-mesh-result "
    if tag and not re.search(result + r"DONE " + re.escape(tag) + TAIL, text, re.M):
        problems.append(t("reply.no_done", tag))
    commits = {c.lower() for c in re.findall(result + r"commit ([0-9a-fA-F]{40})" + TAIL, text, re.M)}
    if not commits:
        problems.append(t("reply.no_commit"))
    elif commits != {commit.lower()}:
        others = ", ".join(sorted(other[:12] for other in commits - {commit.lower()}))
        problems.append(t("reply.other_commit", others, commit.lower()[:12]))
    for name in CLOUD_FILES:
        want = sha((ROOT / name).read_bytes())
        pattern = result + r"sum ([0-9a-fA-F]{64})[ \t]+\*?(?:\./)?" + re.escape(name) + TAIL
        found = {f.lower() for f in re.findall(pattern, text, re.M)}
        if not found:
            problems.append(t("reply.no_sum", name))
        elif found != {want}:
            problems.append(t("reply.sum_differs", name))
    if engine:
        found_engine = set(re.findall(LEAD + ENGINE_LINE.pattern, text, re.M))
        found_builds = set(re.findall(LEAD + BUILD_LINE.pattern + TAIL, text, re.M))
        if not found_engine:
            problems.append(t("reply.no_engine"))
        elif found_engine != {engine}:
            problems.append(t("reply.other_engine", ", ".join(sorted(found_engine)), engine))
        allowed = set(rn_engine().PINNED_BUILDS.get(engine, {}).values())
        if not found_builds:
            problems.append(t("reply.no_build"))
        elif not found_builds <= allowed:
            problems.append(t("reply.unpinned_build", engine))
        if not re.search(LEAD + r"engine: launcher ok" + TAIL, text, re.M):
            problems.append(t("reply.no_launcher"))
    return problems


def print_problems(problems):
    for problem in problems:
        print(t("reply.mismatch", problem))


def cmd_cloud_reply(args):
    engine = pinned() if args.engine else None
    problems = reply_problems(sys.stdin.read(), args.commit, args.tag, engine)
    if problems:
        print_problems(problems)
        raise SystemExit(1)
    print(t("reply.match_engine", engine) if engine else t("reply.match"))


def bot_address():
    try:
        text = env_path().read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise ValueError(t("request.no_env", env_path(), error.strerror or error))
    person = relay_names.parse_env(text).get("PERSON", "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", person):
        raise ValueError(t("request.no_person", env_path()))
    return f"{person}/{relay_name()}"


def request_address(request):
    value = request.get("address")
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}/[a-z0-9]+(?:-[a-z0-9]+)*", value):
        return value
    return bot_address()


def request_of(data):
    value = data.get("request")
    if (isinstance(value, dict) and isinstance(value.get("req"), str) and re.fullmatch(r"[0-9a-f]{16}", value["req"])
            and value.get("mode") in ("line", "block") and isinstance(value.get("sent_ms"), int)
            and isinstance(value.get("tag"), str) and isinstance(value.get("commit"), str)
            and isinstance(value.get("target"), dict)):
        return value
    return None


def find_reply(req, sender, floor_ms):
    marker = re.compile(LEAD + r"agent-mesh-result request " + re.escape(req) + TAIL, re.M)
    for body in reply_bodies(req, sender, floor_ms):
        if marker.search(body):
            return body
    return None


def reply_bodies(req, sender, floor_ms):
    path = relay_names.default_db()
    if not path.exists():
        raise LookupError(t("request.no_db", path))
    try:
        db = relay_names.open_ro(path)
        try:
            cols = {r[1] for r in db.execute("PRAGMA table_info(messages)")}
            sql = "SELECT body FROM messages WHERE sender = ? AND sent_at >= ? AND body LIKE ?"
            if "held" in cols:
                sql += " AND COALESCE(held, 0) = 0"
            rows = db.execute(sql + " ORDER BY sent_at, id", (sender, floor_ms, f"%{req}%")).fetchall()
        finally:
            db.close()
    except sqlite3.Error as error:
        raise LookupError(t("request.db_error", path, error))
    return [body for (body,) in rows if isinstance(body, str)]


def cmd_bot_address(args):
    if args.relay_name is None:
        print(bot_address())
        return
    data = load(warn=False)
    bot = added_bot(args.relay_name, data)
    print(f"{bot_person(bot)}/{args.relay_name}" if bot is not None else bot_address())


def cmd_request_new(args):
    data = load_strict()
    req = secrets.token_hex(8)
    data["request"] = {"req": req, "tag": args.tag, "commit": args.commit, "mode": args.mode,
                       "sent_ms": int(time.time() * 1000) - 2000,
                       "target": {field: digest(field, "en") for field in CLOUD_PARTS}}
    if args.address:
        data["request"]["address"] = args.address
    save(data)
    print(req)


def drop_request(data):
    if "request" in data:
        data.pop("request")
        save(data)


def cmd_request_check(args):
    try:
        check_request(args)
    except ValueError as error:
        print(str(error))
        raise SystemExit(4)


def check_request(args):
    data = load_strict()
    request = request_of(data)
    if request is None or (request["tag"], request["commit"]) != (args.tag, args.commit):
        drop_request(data)
        raise SystemExit(3)
    if any(request["target"].get(field) != digest(field, "en") for field in CLOUD_PARTS):
        drop_request(data)
        print(t("request.checkout_moved"))
        raise SystemExit(3)
    try:
        text = find_reply(request["req"], request_address(request), request["sent_ms"])
    except LookupError as error:
        print(str(error))
        raise SystemExit(4)
    if text is None:
        raise SystemExit(3)
    stops = [line.strip() for line in text.splitlines() if re.match(LEAD + r"STOP:", line)]
    problems = reply_problems(text, args.commit, args.tag, pinned())
    if problems:
        drop_request(data)
        for line in stops:
            print(f"  {line}")
        print_problems(problems)
        raise SystemExit(1)
    for field in CLOUD_PARTS:
        data[field] = {"hash": digest(field, "en"), "state": "confirmed"}
    data["cloud_engine"]["version"] = pinned()
    if legacy_name_status(data) == "unknown":
        data["relay_name"] = relay_name()
    data.pop("request")
    stamp_release(data)
    save(data)
    print(t("reply.match_engine", pinned()))


def added_bot_strict(data, name):
    bot = added_bot(name, data)
    if bot is None:
        raise ValueError(t("state.not_added_bot", name))
    return bot


def bot_person(bot):
    try:
        text = Path(bot["env"]).read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    person = relay_names.parse_env(text).get("PERSON", "")
    if not (TEAM.fullmatch(person) and len(person) <= 40):
        raise ValueError(t("state.bot_no_team", bot["name"], bot["env"]))
    return person


def cmd_add_record(args):
    if not re.fullmatch(r"[0-9a-f]{16}", args.team_hash):
        raise ValueError(t("state.bot_no_team_hash", args.relay_name, record_path(), args.relay_name))
    why = relay_names.problem(args.relay_name) or (args.grok_name is not None and relay_names.grok_problem(args.grok_name))
    if why:
        raise ValueError(why)
    data = load_strict()
    if any(b["name"] == args.relay_name for b in bots.roster(home(), data)) or args.relay_name in removed_bots(data):
        raise ValueError(t("names.add_is_bot", args.relay_name))
    item = {"team_hash": args.team_hash, "stage": "configured", "prompt": {"state": "pending"},
            "cloud": {"state": "pending"}, "added_ms": int(time.time() * 1000)}
    if args.grok_name is not None:
        item["grok_name"] = args.grok_name
    data.setdefault("bots", {})[args.relay_name] = item
    save(data)


def cmd_bot_get(args):
    data = load_strict()
    bot = added_bot_strict(data, args.relay_name)
    item = bot_entry(data, args.relay_name) or {}
    cloud = item.get("cloud") if isinstance(item.get("cloud"), dict) else {}
    req = cloud.get("req") if isinstance(cloud.get("req"), str) and re.fullmatch(r"[0-9a-f]{16}", cloud["req"]) else "-"
    team_hash = item.get("team_hash")
    print(f"stage {bot['stage']}")
    print(f"cloud {bot['cloud']}")
    print(f"req {req}")
    print(f"team_hash {team_hash if isinstance(team_hash, str) and re.fullmatch(r'[0-9a-f]{16}', team_hash) else '-'}")
    print(f"record {bot['record']}")
    print(f"removing {1 if bot['removing'] else 0}")


def cmd_bot_stage(args):
    data = load_strict()
    added_bot_strict(data, args.relay_name)
    known_entry(data, args.relay_name)["stage"] = args.stage
    save(data)


def cmd_add_request(args):
    data = load_strict()
    added_bot_strict(data, args.relay_name)
    item = known_entry(data, args.relay_name)
    req = secrets.token_hex(8)
    item["cloud"] = {"state": "pending", "req": req, "sent_ms": int(time.time() * 1000) - 2000}
    save(data)
    print(req)


def add_marker(req, name, team_hash):
    return LEAD + r"agent-mesh-result add-bot " + re.escape(req) + r" ok " + re.escape(name) + " " + team_hash + TAIL


def cmd_add_reply(args):
    if args.check:
        try:
            data = load_strict()
            bot = added_bot_strict(data, args.relay_name)
        except ValueError as error:
            print(str(error))
            raise SystemExit(4)
    else:
        data = load_strict()
        bot = added_bot_strict(data, args.relay_name)
    item = known_entry(data, args.relay_name)
    cloud = item.get("cloud") if isinstance(item.get("cloud"), dict) else {}
    if cloud.get("state") == "confirmed":
        return
    req, sent, team_hash = cloud.get("req"), cloud.get("sent_ms"), item.get("team_hash")
    if not (isinstance(req, str) and re.fullmatch(r"[0-9a-f]{16}", req) and isinstance(sent, int)
            and isinstance(team_hash, str) and re.fullmatch(r"[0-9a-f]{16}", team_hash)):
        raise SystemExit(3)
    sender = f"{bot_person(bot)}/{args.relay_name}"
    try:
        bodies = reply_bodies(req, sender, sent)
    except LookupError as error:
        print(str(error))
        raise SystemExit(4)
    ok = re.compile(add_marker(req, args.relay_name, team_hash), re.M)
    other = re.compile(LEAD + r"(STOP:.*|agent-mesh-result add-bot " + re.escape(req) + r" .*)$", re.M)
    if not any(ok.search(body) for body in bodies):
        lines = [m.group(0).strip() for body in bodies for m in other.finditer(body)]
        if not lines:
            raise SystemExit(3)
        print(t("state.add_reply_mismatch", sender))
        for line in lines:
            print(f"  {line}")
        raise SystemExit(1)
    if args.check:
        return
    item["cloud"] = {"state": "confirmed", "req": req, "sent_ms": sent}
    prompt = item.get("prompt") if isinstance(item.get("prompt"), dict) else {}
    if prompt.get("state") == "pending" and prompt.get("hash") == digest("prompt", args.lang, args.relay_name):
        item["prompt"] = {"hash": prompt["hash"], "state": "confirmed"}
    save(data)
    print(t("state.add_confirmed", sender))


WAKE_TEXT = ("agent-mesh: the Mac installer just added this Bot ({address}). If its cloud registration is not done "
             "yet, do step 0 of your routine prompt now; once it is done, step 0 does nothing. There is no mail to "
             "fetch for this notice.")


def relay_module():
    spec = importlib.util.spec_from_file_location("agent_relay_for_wake", ROOT / "agent_relay.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cmd_wake_bot(args):
    data = load_strict()
    bot = added_bot_strict(data, args.relay_name)
    relay = relay_module()
    relay.ENV_FILE = Path(bot["env"])
    try:
        cfg = relay.load_env()
    except SystemExit as error:
        raise ValueError(t("state.wake_env", args.relay_name, error.code))
    me = f"{cfg['PERSON']}/{cfg['LISTEN_NAME']}"
    if cfg["LISTEN_NAME"] != args.relay_name:
        raise ValueError(t("state.env_name_differs", bot["env"], cfg["LISTEN_NAME"], args.relay_name))
    view = types.SimpleNamespace(me=me, cloud=f"{cfg['PERSON']}/{cfg['CLOUD_NAME']}",
                                 inbox=f"{cfg['PERSON']}/{cfg['INBOX_NAME']}")
    item = relay.notice_item(f"relay-add-bot-{int(time.time())}", WAKE_TEXT.format(address=me))
    body = json.dumps(relay.Relay.payload(view, [item]), ensure_ascii=False).encode()
    request = urllib.request.Request(cfg["WEBHOOK_URL"], data=body, method="POST",
                                     headers=relay.webhook_headers(cfg["WEBHOOK_KEY"]))
    try:
        with relay.webhook_open(request, timeout=15) as resp:
            code = resp.status
    except urllib.error.HTTPError as error:
        code = error.code
        if error.fp is not None:
            error.close()
    except Exception as error:
        code = type(error).__name__
    if not (isinstance(code, int) and 200 <= code < 300):
        raise ValueError(t("state.wake_failed", me, code))
    print(t("state.woke", me))


def held_lock():
    fd = os.environ.get("AGENT_MESH_LOCK_FD")
    if fd is None:
        return
    import mesh_lock
    if not fd.isdigit() or mesh_lock.inherit(int(fd), str(home() / ".local/lib/agent-mesh.lock")) != mesh_lock.HELD:
        raise SystemExit(5)


def need_lock():
    if os.environ.get("AGENT_MESH_LOCK_FD") is None:
        raise ValueError(t("state.needs_lock"))
    held_lock()


def print_python(python, source, stale=None, mixed=False):
    if python:
        print(f"python {python}")
    print(f"source {source}")
    if stale:
        print(f"stale {stale}")
    for note in python_notes(python) if python else []:
        print(f"note {note}")
    if mixed:
        print("note mixed")


def set_python(data, python):
    if data.get("relay_python") != python:
        data["relay_python"] = python
        save(data)


def cmd_relay_python(args):
    if args.python is not None:
        if not python_usable(args.python):
            raise ValueError(t("state.python_unusable", args.python))
        print_python(args.python, "given")
        return
    if args.set is not None:
        need_lock()
        if not python_usable(args.set):
            raise ValueError(t("state.python_unusable", args.set))
        set_python(load_strict(), args.set)
        return
    if args.record:
        need_lock()
        data = load_strict()
    else:
        data = load(warn=False)
    roster = lenient_roster()
    python, source, stale = resolve_python(data, roster)
    if args.record and source == "plist":
        set_python(data, python)
    print_python(python, source, stale, bool(python) and python_mixed(python, roster))
    if not python:
        raise SystemExit(3)


def cmd_config_pending(_args):
    need_lock()
    data = load_strict()
    data["engine_config"] = {"state": "pending"}
    save(data)


def cmd_config_written(_args):
    need_lock()
    data = load_strict()
    if config_check() != engine_config.OFF:
        raise ValueError(t("state.config_not_written", home() / ".reason/network/config.toml"))
    written = int(time.time()) + 2
    data["engine_config"] = {"state": "pending", "written_s": written}
    save(data)
    time.sleep(max(0.0, written - time.time()))


def cmd_config_verified(_args):
    need_lock()
    data = load_strict()
    record = config_record(data)
    path = home() / ".reason/network/config.toml"
    if record is None:
        if config_check() == engine_config.OFF:
            engine_config.drop_backup(path)
        return
    if config_check() != engine_config.OFF or not config_loaded(record):
        raise SystemExit(1)
    data.pop("engine_config")
    save(data)
    engine_config.drop_backup(path)


def cmd_config_state(_args):
    print(config_status(load(warn=False)))


def relay_backlog(state_dir):
    try:
        data = json.loads((Path(state_dir) / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(data, dict):
        return 0
    spool, pending = data.get("spool"), data.get("pending")
    return (len(spool) if isinstance(spool, list) else 0) + (len(pending) if isinstance(pending, dict) else 0)


def cmd_relay_pending(_args):
    print(sum(relay_backlog(bot["state_dir"]) for bot in relay_members()))


def removing_target(data, args):
    removing = removing_of(data)
    if removing is not None and removing["name"] != args.relay_name:
        raise ValueError(t("state.removing_other", removing["name"], args.relay_name))
    return removing


def cmd_removing_set(args):
    held_lock()
    data = load_strict()
    removing_target(data, args)
    if not any(b["name"] == args.relay_name and b["kind"] == args.kind for b in bots.roster(home(), data)):
        raise ValueError(t("state.not_a_bot", args.relay_name))
    data["removing"] = {"name": args.relay_name, "kind": args.kind, "step": args.step}
    save(data)


def cmd_removing_clear(args):
    held_lock()
    data = load_strict()
    if removing_target(data, args) is not None:
        data.pop("removing", None)
        save(data)


def cmd_removing_done(args):
    held_lock()
    data = load_strict()
    removing = removing_target(data, args)
    if removing is None:
        raise ValueError(t("state.not_removing", args.relay_name))
    if removing["kind"] == bots.LEGACY:
        data["legacy_removed"] = True
        for key in ("relay_name", "grok_name", "prompt"):
            data.pop(key, None)
    else:
        bots_of(data).pop(args.relay_name, None)
    gone = removed_bots(data)
    if args.relay_name not in gone:
        gone.append(args.relay_name)
    data["removed_bots"] = gone
    data.pop("removing", None)
    save(data)


def cmd_set_engine(args):
    data = load_strict()
    data["engine"] = {"version": args.version}
    save(data)


def cmd_txn_clear(_args):
    data = load_strict()
    if "txn" in data:
        data.pop("txn")
        save(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status")
    p.add_argument("--lang", required=True, choices=("en", "zh-CN"))
    p.add_argument("--python")
    p.set_defaults(run=cmd_status)
    p = sub.add_parser("relay-python")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--record", action="store_true")
    group.add_argument("--set")
    group.add_argument("--python")
    p.set_defaults(run=cmd_relay_python)
    for name, run in (("config-pending", cmd_config_pending), ("config-written", cmd_config_written),
                      ("config-verified", cmd_config_verified), ("config-state", cmd_config_state),
                      ("relay-pending", cmd_relay_pending)):
        sub.add_parser(name).set_defaults(run=run)
    p = sub.add_parser("digest")
    p.add_argument("field", choices=REMOTE)
    p.add_argument("--lang", default="en", choices=("en", "zh-CN"))
    p.add_argument("--relay-name")
    p.set_defaults(run=lambda a: print(digest(a.field, a.lang, a.relay_name)))
    sub.add_parser("pinned").set_defaults(run=lambda a: print(pinned()))
    p = sub.add_parser("set-engine")
    p.add_argument("version")
    p.set_defaults(run=cmd_set_engine)
    p = sub.add_parser("mark")
    p.add_argument("field", choices=REMOTE)
    p.add_argument("state", choices=STATES)
    p.add_argument("--lang", default="en", choices=("en", "zh-CN"))
    p.add_argument("--expect")
    p.add_argument("--if-pending", action="store_true")
    p.add_argument("--relay-name")
    p.set_defaults(run=cmd_mark)
    p = sub.add_parser("check-name")
    p.add_argument("--relay-name")
    p.set_defaults(run=cmd_check_name)
    p = sub.add_parser("set-grok-name")
    p.add_argument("grok_name")
    p.add_argument("--relay-name")
    p.add_argument("--check", action="store_true")
    p.set_defaults(run=cmd_set_grok_name)
    p = sub.add_parser("label")
    p.add_argument("--person", default="")
    p.add_argument("--relay-name")
    p.set_defaults(run=cmd_label)
    sub.add_parser("recorded-name").set_defaults(run=cmd_recorded_name)
    p = sub.add_parser("show-prompt")
    p.add_argument("--lang", default="en", choices=("en", "zh-CN"))
    p.add_argument("--relay-name")
    p.set_defaults(run=cmd_show_prompt)
    sub.add_parser("lang").set_defaults(run=cmd_lang)
    p = sub.add_parser("txn-start")
    p.add_argument("--lang", required=True, choices=("en", "zh-CN"))
    p.add_argument("--need", required=True)
    p.add_argument("--was", choices=("0", "1"))
    p.add_argument("--participants")
    p.add_argument("--prompt-bots")
    p.add_argument("--commit", required=True)
    p.set_defaults(run=cmd_txn_start)
    sub.add_parser("txn-participants").set_defaults(run=cmd_txn_participants)
    sub.add_parser("etxn-participants").set_defaults(run=cmd_etxn_participants)
    p = sub.add_parser("txn-get")
    p.add_argument("key", choices=TXN_KEYS)
    p.set_defaults(run=cmd_txn_get)
    p = sub.add_parser("txn-set")
    p.add_argument("key", choices=("stage", "need"))
    p.add_argument("value")
    p.add_argument("--prompt-bots")
    p.set_defaults(run=cmd_txn_set)
    sub.add_parser("txn-clear").set_defaults(run=cmd_txn_clear)
    p = sub.add_parser("cloud-reply")
    p.add_argument("--commit", required=True)
    p.add_argument("--tag", required=True)
    p.add_argument("--engine", action="store_true")
    p.set_defaults(run=cmd_cloud_reply)
    p = sub.add_parser("release")
    p.add_argument("--repo-url", default=DEFAULT_REPO_URL)
    p.set_defaults(run=cmd_release)
    p = sub.add_parser("bot-address")
    p.add_argument("--relay-name")
    p.set_defaults(run=cmd_bot_address)
    p = sub.add_parser("request-new")
    p.add_argument("--tag", required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--mode", required=True, choices=("line", "block"))
    p.add_argument("--address")
    p.set_defaults(run=cmd_request_new)
    p = sub.add_parser("request-check")
    p.add_argument("--tag", required=True)
    p.add_argument("--commit", required=True)
    p.set_defaults(run=cmd_request_check)
    p = sub.add_parser("add-record")
    p.add_argument("--relay-name", required=True)
    p.add_argument("--team-hash", required=True)
    p.add_argument("--grok-name")
    p.set_defaults(run=cmd_add_record)
    for name, run in (("bot-get", cmd_bot_get), ("add-request", cmd_add_request), ("wake-bot", cmd_wake_bot)):
        p = sub.add_parser(name)
        p.add_argument("--relay-name", required=True)
        p.set_defaults(run=run)
    p = sub.add_parser("bot-stage")
    p.add_argument("--relay-name", required=True)
    p.add_argument("--stage", required=True, choices=bots.STAGES)
    p.set_defaults(run=cmd_bot_stage)
    p = sub.add_parser("add-reply")
    p.add_argument("--relay-name", required=True)
    p.add_argument("--lang", default="en", choices=("en", "zh-CN"))
    p.add_argument("--check", action="store_true")
    p.set_defaults(run=cmd_add_reply)
    p = sub.add_parser("removing-set")
    p.add_argument("--relay-name", required=True)
    p.add_argument("--kind", required=True, choices=bots.KINDS)
    p.add_argument("--step", required=True, choices=("started", "stopped", "routine", "cloud"))
    p.set_defaults(run=cmd_removing_set)
    for name, run in (("removing-clear", cmd_removing_clear), ("removing-done", cmd_removing_done)):
        p = sub.add_parser(name)
        p.add_argument("--relay-name", required=True)
        p.set_defaults(run=run)
    args = parser.parse_args(argv)
    utf8_stdout()
    try:
        args.run(args)
    except ValueError as error:
        raise SystemExit(str(error))


if __name__ == "__main__":
    main()
