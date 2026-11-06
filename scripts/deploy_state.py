#!/usr/bin/env python3
"""Deployment record for update.sh: what this Mac installed locally and last confirmed on the cloud side."""
import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from render_prompt import ROOT, render_prompt
from write_launch_agent import launch_agent_bytes

SCHEMA = 1
REMOTE = ("cloud", "cloud_setup", "prompt")
STATES = ("pending", "confirmed")
TXN_KEYS = ("stage", "relay_was_loaded", "need", "lang", "commit", "target")
STAGES = ("stopping", "relay_stopped", "routine_paused", "cloud_done", "prompt_done", "routine_resumed")


def home():
    return Path(os.environ["HOME"])


def lib_dir():
    return home() / ".local/lib/agent-mesh"


def record_path():
    return lib_dir() / "deployed.json"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(field, lang):
    if field == "cloud":
        parts = [sha((ROOT / "box" / name).read_bytes()) for name in ("rn-cloud.sh", "rn-fetch.py")]
        return sha("\n".join(parts).encode())
    if field == "cloud_setup":
        return sha((ROOT / "box/rn-cloud-setup.sh").read_bytes())
    if field == "prompt":
        return sha(render_prompt("routine", lang).encode("utf-8"))
    raise ValueError(f"unknown field: {field}")


def load(warn=True):
    path = record_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        if warn:
            print(f"update: {path} is unreadable ({exc}); treating cloud and prompt as unknown", file=sys.stderr)
        return {}
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        if warn:
            print(f"update: {path} has an unknown format; treating cloud and prompt as unknown", file=sys.stderr)
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
            and isinstance(txn.get("need"), list) and all(part in ("cloud", "prompt") for part in txn["need"])
            and txn.get("lang") in ("en", "zh-CN") and isinstance(txn.get("commit"), str)
            and isinstance(txn.get("target"), dict))


def txn_of(data, warn=False):
    txn = data.get("txn")
    if txn is None or valid_txn(txn):
        return txn
    if warn:
        print("update: the record of an unfinished update is damaged and was ignored; "
              "if the routine is still paused in Grok Bot, resume it", file=sys.stderr)
    return None


def cmd_status(args):
    data = load()
    print(f"relay {relay_status(args.python)}")
    print(f"dsh {dsh_status()}")
    for field in REMOTE:
        print(f"{field} {remote_status(data, field, args.lang)}")
    txn = txn_of(data, warn=True)
    print(f"txn {txn['stage'] if txn else '-'}")


def cmd_mark(args):
    data = load(warn=False)
    value = {"hash": digest(args.field, args.lang), "state": args.state}
    old = entry(data, args.field)
    if args.expect and args.expect != value["hash"]:
        raise SystemExit(f"update: this checkout changed after {args.field} was shown; nothing was recorded, "
                         "run ./update.sh again")
    if args.if_pending and not (old and old["state"] == "pending" and old["hash"] == value["hash"]):
        raise SystemExit(f"update: the {args.field} record changed after it was shown; nothing was recorded, "
                         "run ./update.sh again")
    data[args.field] = value
    if args.field == "prompt":
        data["lang"] = args.lang
    save(data)


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
        raise SystemExit("update: no unfinished update to change")
    if args.key == "need":
        txn["need"] = [part for part in args.value.split(",") if part]
        txn["target"] = {part: digest(part, txn.get("lang", "en")) for part in txn["need"]}
    else:
        txn[args.key] = args.value
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
    p = sub.add_parser("mark")
    p.add_argument("field", choices=REMOTE)
    p.add_argument("state", choices=STATES)
    p.add_argument("--lang", default="en", choices=("en", "zh-CN"))
    p.add_argument("--expect")
    p.add_argument("--if-pending", action="store_true")
    p.set_defaults(run=cmd_mark)
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
    args = parser.parse_args(argv)
    args.run(args)


if __name__ == "__main__":
    main()
