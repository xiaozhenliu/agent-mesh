#!/usr/bin/env python3
"""The reply convention templates with this Mac's Bot address filled in.

  reply_convention.py install          write both filled-in templates to ~/.local/lib/agent-mesh/templates/ (only
                                       when they differ) and print the files it wrote
  reply_convention.py render --lang L  print the filled-in template
  reply_convention.py refresh FILE     replace only the lines between the agent-mesh block markers in FILE (an
                                       AGENTS.md that holds a copy of the template), writing the same file so its
                                       owner, group, mode and ACLs stay (a backup of the original sits next to it,
                                       hidden, until the write is done); exit 1 and leave FILE unchanged if it has no
                                       single, complete block
The addresses come from the Bots on this Mac (scripts/bots.py: the first Bot from its env or the deployment record,
and every added Bot whose cloud registration is confirmed; a Bot being removed is left out), the team name from the
engine config, and each Bot's name in Grok from the deployment record (install.sh --grok-name). With one Bot the
block is one line; with several, one line per Bot in the order of the roster.
"""
import argparse
import os
import re
import signal
import sys
import tempfile
from pathlib import Path

import bots
import deploy_state
from messages import t
from render_prompt import ROOT

LANGS = ("en", "zh-CN")
START = re.compile(r"^<!-- agent-mesh:bot-address lang=(en|zh-CN) -->$")
END = "<!-- /agent-mesh:bot-address -->"


def template_path(lang):
    suffix = "" if lang == "en" else f".{lang}"
    return ROOT / "templates" / f"AGENTS.reason-network{suffix}.md"


def installed_dir():
    return deploy_state.lib_dir() / "templates"


def person():
    cfg = deploy_state.home() / ".reason/network/config.toml"
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(r'^person = "([^"]*)"', text, re.M)
    return m.group(1) if m else ""


def addresses(home):
    try:
        data = bots.load_record(home)
    except ValueError:
        data = {}
    return [(b["name"], b["grok_name"]) for b in bots.roster(home, data)
            if not b["removing"] and (b["kind"] == bots.LEGACY or b["cloud"] == "confirmed")]


def values():
    entries = addresses(deploy_state.home())
    team = person()
    if not entries or not team:
        raise ValueError(t("reply.no_address"))
    return team, entries


def block_line(lang, team, name, grok):
    address = f"`{team}/{name}`"
    if lang == "en":
        return f"- This team's Grok Bot address: {address}" + (f" (called “{grok}” in Grok)." if grok else ".")
    return f"- 本团队 Grok Bot 的地址：{address}" + (f"（在 Grok 里叫「{grok}」）。" if grok else "。")


def block_lines(lang, team, entries):
    if len(entries) == 1:
        return [block_line(lang, team, *entries[0])]
    head = "- This team's Grok Bot addresses:" if lang == "en" else "- 本团队 Grok Bot 的地址："
    out = [head]
    for name, grok in entries:
        address = f"`{team}/{name}`"
        if lang == "en":
            out.append(f"  - {address}" + (f" (called “{grok}” in Grok)" if grok else ""))
        else:
            out.append(f"  - {address}" + (f"（在 Grok 里叫「{grok}」）" if grok else ""))
    return out


def find_block(lines):
    starts = [i for i, line in enumerate(lines) if START.match(line.rstrip("\r\n"))]
    ends = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == END]
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        return None
    return starts[0], ends[0], START.match(lines[starts[0]].rstrip("\r\n")).group(1)


def fill(text, team, entries):
    lines = text.splitlines(keepends=True)
    found = find_block(lines)
    if found is None:
        raise ValueError(t("reply.no_block", END))
    start, end, lang = found
    newline = "\r\n" if lines[start].endswith("\r\n") else "\n"
    return "".join(lines[:start + 1] + [line + newline for line in block_lines(lang, team, entries)] + lines[end:])


def rendered(lang, team, entries):
    return fill(template_path(lang).read_text(encoding="utf-8"), team, entries)


def write_new(path, data, mode):
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def fsync_dir(path):
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_if_changed(path, text, mode=0o644):
    data = text.encode("utf-8")
    try:
        if path.read_bytes() == data:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    write_new(path, data, mode)
    return True


def backup_path(path):
    return path.with_name(f".{path.name}.agent-mesh-backup")


def put(stream, data):
    stream.seek(0)
    view = memoryview(data)
    while view:
        view = view[stream.write(view):]
    stream.truncate()
    os.fsync(stream.fileno())


def overwrite_in_place(path, old, new):
    backup = backup_path(path)
    with open(path, "r+b", buffering=0) as stream:
        try:
            write_new(backup, old, 0o600)
            fsync_dir(backup.parent)
        except BaseException:
            if os.path.lexists(backup):
                os.unlink(backup)
            raise
        previous = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        try:
            try:
                put(stream, new)
            except BaseException as error:
                try:
                    put(stream, old)
                except BaseException:
                    raise OSError(t("reply.write_lost", path, error, backup))
                os.unlink(backup)
                raise OSError(t("reply.write_restored", path, error))
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    os.unlink(backup)


def cmd_install(_args):
    team, entries = values()
    for lang in LANGS:
        path = installed_dir() / template_path(lang).name
        if write_if_changed(path, rendered(lang, team, entries)):
            print(t("reply.installed", path))


def cmd_render(args):
    sys.stdout.write(rendered(args.lang, *values()))


def cmd_refresh(args):
    path = Path(args.file)
    if path.is_symlink() or not path.is_file():
        raise ValueError(t("reply.not_regular", path))
    backup = backup_path(path)
    if os.path.lexists(backup):
        raise ValueError(t("reply.backup_left", backup, path))
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(t("reply.not_utf8", path))
    try:
        new = fill(text, *values())
    except ValueError as error:
        raise ValueError(t("reply.refresh_failed", path, error, f"{installed_dir()}/{template_path('en').name}"))
    data = new.encode("utf-8")
    if data == raw:
        print(t("reply.up_to_date", path))
        return
    overwrite_in_place(path, raw, data)
    print(t("reply.updated", path))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("install").set_defaults(run=cmd_install)
    p = sub.add_parser("render")
    p.add_argument("--lang", default="en", choices=LANGS)
    p.set_defaults(run=cmd_render)
    p = sub.add_parser("refresh")
    p.add_argument("file")
    p.set_defaults(run=cmd_refresh)
    args = parser.parse_args(argv)
    deploy_state.utf8_stdout()
    try:
        args.run(args)
    except (ValueError, OSError) as error:
        raise SystemExit(f"reply_convention: {error}")


if __name__ == "__main__":
    main()
