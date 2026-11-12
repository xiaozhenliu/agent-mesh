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
The address comes from the relay env (or the deployment record), the team name from the engine config, and the
Bot's name in Grok from the deployment record (install.sh --grok-name).
"""
import argparse
import os
import re
import signal
import sys
import tempfile
from pathlib import Path

import deploy_state
import relay_names
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


def values():
    data = deploy_state.load(warn=False)
    name = relay_names.from_env(deploy_state.env_path()) or deploy_state.recorded_name(data)
    team = person()
    if not name or not team:
        raise ValueError("this Mac has no Bot address yet (no team or no address name); run ./install.sh first")
    return team, name, deploy_state.recorded_grok(data)


def block_line(lang, team, name, grok):
    address = f"`{team}/{name}`"
    if lang == "en":
        return f"- This team's Grok Bot address: {address}" + (f" (called “{grok}” in Grok)." if grok else ".")
    return f"- 本团队 Grok Bot 的地址：{address}" + (f"（在 Grok 里叫「{grok}」）。" if grok else "。")


def find_block(lines):
    starts = [i for i, line in enumerate(lines) if START.match(line.rstrip("\r\n"))]
    ends = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == END]
    if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
        return None
    return starts[0], ends[0], START.match(lines[starts[0]].rstrip("\r\n")).group(1)


def fill(text, team, name, grok):
    lines = text.splitlines(keepends=True)
    found = find_block(lines)
    if found is None:
        raise ValueError("no single, complete agent-mesh block (from '<!-- agent-mesh:bot-address lang=... -->' "
                         f"to '{END}')")
    start, end, lang = found
    newline = "\r\n" if lines[start].endswith("\r\n") else "\n"
    return "".join(lines[:start + 1] + [block_line(lang, team, name, grok) + newline] + lines[end:])


def rendered(lang, team, name, grok):
    return fill(template_path(lang).read_text(encoding="utf-8"), team, name, grok)


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
                    raise OSError(f"could not write {path} ({error}) and could not put the original back; the "
                                  f"original is in {backup}")
                os.unlink(backup)
                raise OSError(f"could not write {path} ({error}); the original was put back")
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    os.unlink(backup)


def cmd_install(_args):
    team, name, grok = values()
    for lang in LANGS:
        path = installed_dir() / template_path(lang).name
        if write_if_changed(path, rendered(lang, team, name, grok)):
            print(f"reply convention with this Bot's address: {path}")


def cmd_render(args):
    sys.stdout.write(rendered(args.lang, *values()))


def cmd_refresh(args):
    path = Path(args.file)
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{path} is not a regular file; nothing was changed")
    backup = backup_path(path)
    if os.path.lexists(backup):
        raise ValueError(f"{backup} is left from an interrupted refresh; compare it with {path}, keep the right text "
                         "in the file and remove the backup, then run again; nothing was changed")
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{path} is not UTF-8 text; nothing was changed")
    try:
        new = fill(text, *values())
    except ValueError as error:
        raise ValueError(f"{path}: {error}; nothing was changed. If it holds an older copy of the reply convention, "
                         f"replace that section with {installed_dir()}/{template_path('en').name} (or the zh-CN "
                         "file)")
    data = new.encode("utf-8")
    if data == raw:
        print(f"{path} already shows this Bot's address")
        return
    overwrite_in_place(path, raw, data)
    print(f"updated the Bot address in {path}")


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
