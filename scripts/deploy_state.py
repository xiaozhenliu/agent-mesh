#!/usr/bin/env python3
"""Deployment record for update.sh: what this Mac installed locally and last confirmed on the cloud side."""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import relay_names
from messages import t
from render_prompt import ROOT, render_prompt
from write_launch_agent import launch_agent_bytes

SCHEMA = 1
REMOTE = ("cloud", "cloud_setup", "prompt", "cloud_engine")
CLOUD_PARTS = ("cloud", "cloud_setup", "cloud_engine")
UPDATE_SCRIPT = "box/rn-cloud-update.sh"
NEEDS = ("cloud", "prompt", "cloud_engine")
STATES = ("pending", "confirmed")
TXN_KEYS = ("stage", "relay_was_loaded", "need", "lang", "commit", "target")
STAGES = ("stopping", "relay_stopped", "routine_paused", "cloud_done", "prompt_done", "routine_resumed")


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
        return fallback or relay_names.DEFAULT
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


def digest(field, lang, relay=None):
    if field == "cloud":
        names = ("rn-cloud.sh", "rn-fetch.py", "rn-cloud-update.sh")
        parts = [sha((ROOT / "box" / name).read_bytes()) for name in names]
        return sha("\n".join(parts).encode())
    if field == "cloud_setup":
        return sha((ROOT / "box/rn-cloud-setup.sh").read_bytes())
    if field == "prompt":
        return sha(prompt_text(lang, relay).encode("utf-8"))
    if field == "cloud_engine":
        return sha(f"reason-network {pinned()}\n{sha((ROOT / 'box/rn-engine.py').read_bytes())}".encode())
    raise ValueError(f"unknown field: {field}")


def prompt_text(lang, relay=None):
    return render_prompt("routine", lang, relay_name=relay_name(relay))


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
    if value["hash"] != digest(field, lang):
        return "changed"
    return "unchanged" if value["state"] == "confirmed" else "pending"


def same_file(installed, expected):
    try:
        return installed.read_bytes() == expected
    except OSError:
        return False


def relay_status(python):
    relay = lib_dir() / "agent_relay.py"
    plist = home() / "Library/LaunchAgents/com.local.agent-relay.plist"
    expected_plist = launch_agent_bytes(home(), python, str(relay))
    ok = same_file(relay, (ROOT / "agent_relay.py").read_bytes()) and same_file(plist, expected_plist)
    return "unchanged" if ok and not (lib_dir() / "rollback").exists() else "changed"


def dsh_status():
    plugin = home() / ".dsh/plugins/reason-network-bridge.mjs"
    if not plugin.exists():
        return "absent"
    return "unchanged" if same_file(plugin, (ROOT / "dsh/reason-network-bridge.mjs").read_bytes()) else "changed"


def valid_txn(txn):
    return (isinstance(txn, dict) and txn.get("stage") in STAGES and isinstance(txn.get("relay_was_loaded"), bool)
            and isinstance(txn.get("need"), list) and all(part in NEEDS for part in txn["need"])
            and txn.get("lang") in ("en", "zh-CN") and isinstance(txn.get("commit"), str)
            and isinstance(txn.get("target"), dict))


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
        stage = json.loads(path.read_text(encoding="utf-8")).get("stage")
    except (OSError, ValueError, AttributeError):
        return "damaged"
    return stage if isinstance(stage, str) and re.fullmatch(r"[a-z_]+", stage) else "damaged"


def cmd_status(args):
    data = load()
    print(f"relay {relay_status(args.python)}")
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
    status = name_status(data)
    print(f"name {status}" if status.startswith("changed ") else f"name {status} {relay_name()}")


def cmd_mark(args):
    data = load(warn=False)
    status = name_status(data, args.relay_name)
    if status.startswith("changed "):
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


def cmd_txn_start(args):
    data = load(warn=False)
    need = [part for part in args.need.split(",") if part]
    data["txn"] = {"stage": "stopping", "relay_was_loaded": args.was == "1", "need": need, "lang": args.lang,
                   "commit": args.commit, "target": {part: digest(part, args.lang) for part in need}}
    save(data)


def cmd_txn_get(args):
    txn = txn_of(load(warn=False))
    if not txn:
        return
    value = txn.get(args.key)
    if isinstance(value, bool):
        print("1" if value else "0")
    elif isinstance(value, list):
        print(",".join(value))
    elif value is not None:
        print(value)


def cmd_txn_set(args):
    data = load(warn=False)
    txn = txn_of(data)
    if not txn:
        raise SystemExit(t("state.no_txn"))
    if args.key == "need":
        txn["need"] = [part for part in args.value.split(",") if part]
        txn["target"] = {part: digest(part, txn.get("lang", "en")) for part in txn["need"]}
    else:
        txn[args.key] = args.value
    save(data)


CLOUD_FILES = ("box/rn-cloud.sh", "box/rn-fetch.py", "box/rn-cloud-setup.sh", "box/rn-engine.py")
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


def request_of(data):
    value = data.get("request")
    if (isinstance(value, dict) and isinstance(value.get("req"), str) and re.fullmatch(r"[0-9a-f]{16}", value["req"])
            and value.get("mode") in ("line", "block") and isinstance(value.get("sent_ms"), int)
            and isinstance(value.get("tag"), str) and isinstance(value.get("commit"), str)
            and isinstance(value.get("target"), dict)):
        return value
    return None


def find_reply(req, sender, floor_ms):
    path = relay_names.default_db()
    if not path.exists():
        raise LookupError(t("request.no_db", path))
    marker = re.compile(LEAD + r"agent-mesh-result request " + re.escape(req) + TAIL, re.M)
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
    for (body,) in rows:
        if isinstance(body, str) and marker.search(body):
            return body
    return None


def cmd_bot_address(_args):
    print(bot_address())


def cmd_request_new(args):
    data = load_strict()
    req = secrets.token_hex(8)
    data["request"] = {"req": req, "tag": args.tag, "commit": args.commit, "mode": args.mode,
                       "sent_ms": int(time.time() * 1000) - 2000,
                       "target": {field: digest(field, "en") for field in CLOUD_PARTS}}
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
        text = find_reply(request["req"], bot_address(), request["sent_ms"])
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
    if name_status(data) == "unknown":
        data["relay_name"] = relay_name()
    data.pop("request")
    stamp_release(data)
    save(data)
    print(t("reply.match_engine", pinned()))


def cmd_set_engine(args):
    data = load(warn=False)
    data["engine"] = {"version": args.version}
    save(data)


def cmd_txn_clear(_args):
    data = load(warn=False)
    if "txn" in data:
        data.pop("txn")
        save(data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status")
    p.add_argument("--lang", required=True, choices=("en", "zh-CN"))
    p.add_argument("--python", required=True)
    p.set_defaults(run=cmd_status)
    p = sub.add_parser("digest")
    p.add_argument("field", choices=REMOTE)
    p.add_argument("--lang", default="en", choices=("en", "zh-CN"))
    p.set_defaults(run=lambda a: print(digest(a.field, a.lang)))
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
    p.add_argument("--was", required=True, choices=("0", "1"))
    p.add_argument("--commit", required=True)
    p.set_defaults(run=cmd_txn_start)
    p = sub.add_parser("txn-get")
    p.add_argument("key", choices=TXN_KEYS)
    p.set_defaults(run=cmd_txn_get)
    p = sub.add_parser("txn-set")
    p.add_argument("key", choices=("stage", "need"))
    p.add_argument("value")
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
    sub.add_parser("bot-address").set_defaults(run=cmd_bot_address)
    p = sub.add_parser("request-new")
    p.add_argument("--tag", required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--mode", required=True, choices=("line", "block"))
    p.set_defaults(run=cmd_request_new)
    p = sub.add_parser("request-check")
    p.add_argument("--tag", required=True)
    p.add_argument("--commit", required=True)
    p.set_defaults(run=cmd_request_check)
    args = parser.parse_args(argv)
    utf8_stdout()
    try:
        args.run(args)
    except ValueError as error:
        raise SystemExit(str(error))


if __name__ == "__main__":
    main()
