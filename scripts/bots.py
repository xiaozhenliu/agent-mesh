#!/usr/bin/env python3
"""The Bots on this Mac: the first Bot on the original paths, added Bots under agent-relay-bots/<slot>/.

The slot of an added Bot is the name it was added with and stays its key for paths, labels and RN_BOT; its address
name can change later. The first Bot's slot is "-".

  bots.py list                         one line per Bot: name, kind, stage, cloud, removing (0|1), record (ok|missing),
                                       address, separated by tabs; exit 4 if the deployment record cannot be read
  bots.py list --paths                 the same, with the label, plist and registration marker of each Bot before the
                                       address
  bots.py paths NAME                   KEY<TAB>value lines with the paths and stage of the Bot NAME (its slot or its
                                       current address name); exit 3 if there is no such Bot here, 4 if the deployment
                                       record cannot be read
  bots.py paths [NAME] --kind KIND     the same, and for a Bot that is not here yet the paths it would get (stage
                                       new); without NAME, --kind legacy selects the first Bot
  bots.py paths ... --lenient          for read-only callers: an unreadable record counts as empty instead of exit 4
"""
import json
import os
import sys
from pathlib import Path

import relay_names
from messages import t

SCHEMA = 1
LEGACY = "legacy"
ADDED = "added"
KINDS = (LEGACY, ADDED)
STAGES = ("configured", "relay")
CLOUD_STATES = ("pending", "confirmed")
LEGACY_LABEL = "com.local.agent-relay"
KEYS = (("KIND", "kind"), ("CONF_DIR", "conf_dir"), ("ENV_FILE", "env"), ("LABEL", "label"), ("PLIST", "plist"),
        ("STATE_DIR", "state_dir"), ("MARKER", "marker"), ("LOG", "log"), ("LAUNCHD_LOG", "launchd_log"),
        ("STAGE", "stage"), ("SLOT", "slot"), ("ADDRESS", "address"))


def record_path(home):
    return Path(home) / ".local/lib/agent-mesh/deployed.json"


def added_root(home):
    return Path(home) / ".config/agent-relay-bots"


def paths(home, name, kind):
    home = Path(home)
    logs = home / "Library/Logs"
    if kind == LEGACY:
        conf = home / ".config/agent-relay"
        label = LEGACY_LABEL
        state = home / "Library/Application Support/agent-relay"
        log, launchd_log = logs / "agent-relay.log", logs / "agent-relay.launchd.log"
    elif kind == ADDED:
        conf = added_root(home) / name
        label = f"{LEGACY_LABEL}.{name}"
        state = home / "Library/Application Support/agent-relay-bots" / name
        log, launchd_log = logs / f"agent-relay-{name}.log", logs / f"agent-relay-{name}.launchd.log"
    else:
        raise ValueError(f"unknown kind: {kind}")
    return {"name": name, "kind": kind, "slot": "-" if kind == LEGACY else name, "address": name,
            "conf_dir": str(conf), "env": str(conf / "env"), "label": label,
            "plist": str(home / "Library/LaunchAgents" / f"{label}.plist"), "state_dir": str(state),
            "marker": str(state / "registered"), "log": str(log), "launchd_log": str(launchd_log)}


def load_record(home):
    path = record_path(home)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as error:
        raise ValueError(t("state.unreadable_plain", path, error))
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError(t("state.format_plain", path))
    bots = data.get("bots", {})
    if not isinstance(bots, dict) or not all(isinstance(v, dict) for v in bots.values()):
        raise ValueError(t("state.format_plain", path))
    return data


def valid_name(name):
    return relay_names.problem(name) is None


def grok_of(entry):
    value = entry.get("grok_name") if isinstance(entry, dict) else None
    return value if relay_names.grok_problem(value) is None else None


def added_on_disk(home):
    root = added_root(home)
    try:
        names = sorted(p.name for p in root.iterdir())
    except OSError:
        return []
    return [n for n in names if valid_name(n) and (root / n / "env").is_file()]


def legacy_suppressed(home, data):
    if data.get("legacy_removed") is True:
        return True
    return not data and not Path(paths(home, None, LEGACY)["env"]).exists() and bool(added_on_disk(home))


def removing_of(data):
    value = data.get("removing")
    if isinstance(value, dict) and isinstance(value.get("name"), str) and value.get("kind") in KINDS:
        return value
    return None


def address_of(entry, slot):
    value = entry.get("name") if isinstance(entry, dict) else None
    return value if isinstance(value, str) and valid_name(value) else slot


def item(home, name, kind, stage, cloud, record, grok, removing, address=None):
    out = paths(home, name, kind)
    out.update(stage=stage, cloud=cloud, record=record, grok_name=grok, address=address or name,
               removing=bool(removing and removing["name"] == name and removing["kind"] == kind))
    return out


def legacy_name(home, data):
    env = Path(paths(home, None, LEGACY)["env"])
    name = relay_names.from_env(env)
    if name is not None:
        return name
    recorded = data.get("relay_name")
    if isinstance(recorded, str) and valid_name(recorded) and data.get("legacy_removed") is not True:
        return recorded
    return None


def roster(home, data=None):
    if data is None:
        data = load_record(home)
    removing = removing_of(data)
    out = []
    name = legacy_name(home, data)
    if name is not None:
        recorded = data.get("relay_name")
        out.append(item(home, name, LEGACY, "relay", "-", "ok" if recorded == name else "missing",
                        grok_of(data), removing))
    return out + added_entries(home, data, removing, {name} if name else set())


def added_entries(home, data, removing=None, seen=()):
    out, seen = [], set(seen)
    entries = []
    for bot, entry in data.get("bots", {}).items():
        if not valid_name(bot) or bot in seen:
            continue
        added_ms = entry.get("added_ms")
        entries.append((added_ms if isinstance(added_ms, int) and not isinstance(added_ms, bool) else 0, bot, entry))
    for _, bot, entry in sorted(entries, key=lambda e: (e[0], e[1])):
        stage = entry.get("stage") if entry.get("stage") in STAGES else "configured"
        cloud = entry.get("cloud") if isinstance(entry.get("cloud"), dict) else {}
        state = cloud.get("state") if cloud.get("state") in CLOUD_STATES else "pending"
        out.append(item(home, bot, ADDED, stage, state, "ok", grok_of(entry), removing, address_of(entry, bot)))
        seen.add(bot)
    gone = data.get("removed_bots") if isinstance(data.get("removed_bots"), list) else []
    for bot in added_on_disk(home):
        if bot in seen or bot in gone:
            continue
        stage = "relay" if Path(paths(home, bot, ADDED)["plist"]).exists() else "configured"
        out.append(item(home, bot, ADDED, stage, "pending", "missing", None, removing))
    return out


def match(bots, name):
    for bot in bots:
        if bot["name"] == name:
            return bot
    for bot in bots:
        if bot["address"] == name:
            return bot
    return None


def find(home, name, data=None):
    return match(roster(home, data), name)


def print_paths(bot):
    for key, field in KEYS:
        print(f"{key}\t{bot[field] if bot[field] is not None else ''}")


def cmd_paths(home, args):
    kind = None
    lenient = "--lenient" in args
    args = [a for a in args if a != "--lenient"]
    if "--kind" in args:
        at = args.index("--kind")
        if at + 1 >= len(args) or args[at + 1] not in KINDS:
            return usage()
        kind = args[at + 1]
        args = args[:at] + args[at + 2:]
    if len(args) > 1 or (not args and kind != LEGACY):
        return usage()
    name = args[0] if args else None
    if name is not None and not valid_name(name):
        print(relay_names.problem(name), file=sys.stderr)
        return 3
    try:
        data = load_record(home)
    except ValueError:
        if not lenient:
            raise
        data = {}
    bots = roster(home, data)
    found = match(bots, name) if name is not None else next((b for b in bots if b["kind"] == LEGACY), None)
    if found is not None:
        print_paths(found)
        return 0
    if kind is None or (kind == ADDED and name is None) or (kind == LEGACY and any(b["kind"] == LEGACY for b in bots)):
        return 3
    print_paths(dict(paths(home, name, kind), stage="new"))
    return 0


def usage():
    print(__doc__, file=sys.stderr)
    return 2


def main(argv, home):
    args = list(argv)
    cmd = args.pop(0) if args else ""
    try:
        if cmd == "list" and args in ([], ["--paths"]):
            for bot in roster(home):
                cols = [bot["name"], bot["kind"], bot["stage"], bot["cloud"], "1" if bot["removing"] else "0",
                        bot["record"]]
                if args:
                    cols += [bot["label"], bot["plist"], bot["marker"]]
                print("\t".join(cols + [bot["address"]]))
            return 0
        if cmd == "paths":
            return cmd_paths(home, args)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 4
    return usage()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], os.environ["HOME"]))
