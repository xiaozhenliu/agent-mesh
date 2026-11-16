#!/usr/bin/env python3
"""The engine settings this project turns off, checked the way the engine reads the whole file.

  engine_config.py check --path CONFIG KEY...
  engine_config.py clean --path CONFIG KEY...

check is read-only. Exit 0: every KEY is set to false before the first table of CONFIG and no table sets
auto_update, lan or a KEY to anything but a quoted string (a person or a folder of that name). Exit 1: a KEY is missing there, set more than once or not false, CONFIG does not exist,
or a table holds the line "auto_update = false" or "lan = false" exactly as installers up to v0.8.0 appended it;
./update.sh corrects these. Exit 2: CONFIG cannot be read, its top level uses syntax this line-based check does not
cover (quoted keys, multi-line values), or a table, a dotted key or an inline table names one of these keys in any
other way; that is never reported as off.

clean removes the appended lines from the tables and keeps the file as it was next to it, as CONFIG.agent-mesh.bak.
It prints the number of removed lines and that path. Exit 2 as for check; nothing is changed.
"""
import argparse
import os
import re
import sys
from pathlib import Path

BARE_KEY = re.compile(r"([A-Za-z0-9_-]+)[ \t]*=[ \t]*")
ASSIGNMENT = re.compile(r"([A-Za-z0-9_-]+(?:[ \t]*\.[ \t]*[A-Za-z0-9_-]+)*)[ \t]*=[ \t]*(.*)")
OFF, NOT_OFF, UNSUPPORTED = 0, 1, 2
NO, STRING, NAMED = "no", "string", "named"
MANAGED = ("auto_update", "lan")
BACKUP_SUFFIX = ".agent-mesh.bak"


def scan(text, depth):
    out, quote, i = [], None, 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\" and quote == '"':
                out.append(text[i:i + 2])
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
        elif c == "#":
            break
        out.append(c)
        i += 1
    if quote or depth < 0:
        return None, depth
    return "".join(out).strip(), depth


def pieces(line):
    out, i, start = [], 0, 0
    while i < len(line):
        c = line[i]
        if c == "#":
            break
        if c in "\"'":
            if i > start:
                out.append((False, line[start:i]))
            j = i + 1
            while j < len(line) and line[j] != c:
                j += 2 if c == '"' and line[j] == "\\" else 1
            if j >= len(line):
                return None
            out.append((True, line[i + 1:j]))
            i = start = j + 1
            continue
        i += 1
    if i > start:
        out.append((False, line[start:i]))
    return out


def key_word(keys):
    names = "|".join(re.escape(key) for key in keys)
    return re.compile(rf"(?<![A-Za-z0-9_-])(?:{names})(?![A-Za-z0-9_-])")


def mentions(text, word):
    parts = pieces(text)
    return parts is None or any(word.search(part) for quoted, part in parts if not quoted)


def string_entry(parts, keys):
    parts = list(parts)
    while parts and not parts[-1][0] and not parts[-1][1].strip():
        parts.pop()
    flags = [quoted for quoted, text in parts]
    if flags == [False, True]:
        found = BARE_KEY.fullmatch(parts[0][1])
        return bool(found) and found.group(1) in keys
    if flags == [True, False, True]:
        return parts[0][1] in keys and parts[1][1].strip() == "="
    return False


def names_key(raw, keys, word):
    line = raw.strip()
    if not line or line.startswith("#"):
        return NO
    if '"""' in line or "'''" in line:
        return None
    parts = pieces(line)
    if parts is None:
        return None
    bare = "".join(text for quoted, text in parts if not quoted)
    if sum(bare.count(c) for c in "[{") != sum(bare.count(c) for c in "]}"):
        return None
    named = any(word.search(text) for quoted, text in parts if not quoted)
    if not named:
        side = parts
        if not line.startswith("["):
            side = []
            for quoted, text in parts:
                if not quoted and "=" in text:
                    break
                side.append((quoted, text))
        named = any(quoted and text in keys for quoted, text in side)
    if not named:
        return NO
    return STRING if string_entry(parts, keys) else NAMED


def analyse(text, keys):
    lines = text.split("\n")
    names = list(MANAGED) + [key for key in keys if key not in MANAGED]
    word = key_word(names)
    values, depth, first = {}, 0, len(lines)
    for index, raw in enumerate(lines):
        line = raw.strip()
        if line.startswith("["):
            if depth:
                return UNSUPPORTED, []
            first = index
            break
        if not line or line.startswith("#"):
            continue
        if '"""' in line or "'''" in line:
            return UNSUPPORTED, []
        if depth:
            value, depth = scan(line, depth)
            if value is None or mentions(line, word):
                return UNSUPPORTED, []
            continue
        found = ASSIGNMENT.fullmatch(line)
        if not found:
            return UNSUPPORTED, []
        value, depth = scan(found.group(2), 0)
        if not value or mentions(found.group(2), word):
            return UNSUPPORTED, []
        if found.group(1) not in names and word.search(found.group(1)):
            return UNSUPPORTED, []
        values.setdefault(found.group(1), []).append(value if depth == 0 else None)
    if depth:
        return UNSUPPORTED, []
    result = OFF if all(values.get(key) == ["false"] for key in keys) else NOT_OFF
    legacy = []
    if not word.search("\n".join(lines[first:])):
        return result, legacy
    appended = [f"{key} = false" for key in names]
    for index in range(first, len(lines)):
        named = names_key(lines[index], names, word)
        if named is None:
            return UNSUPPORTED, []
        if named == NAMED:
            if lines[index] not in appended:
                return UNSUPPORTED, []
            legacy.append(index)
    return (NOT_OFF if legacy else result), legacy


def read(path):
    try:
        data = Path(path).read_bytes()
    except FileNotFoundError:
        return None, ""
    return data, data.decode("utf-8")


def check(path, keys):
    try:
        data, text = read(path)
    except (OSError, ValueError):
        return UNSUPPORTED
    if data is None:
        return NOT_OFF
    return analyse(text, keys)[0]


def backup_path(path):
    path = Path(path)
    return path.with_name(path.name + BACKUP_SUFFIX)


def put(path, data, mode):
    tmp = path.with_name(f".{path.name}.new.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def clean(path, keys):
    path = Path(path)
    try:
        data, text = read(path)
    except (OSError, ValueError):
        return UNSUPPORTED, 0
    if data is None:
        return OFF, 0
    result, legacy = analyse(text, keys)
    if result == UNSUPPORTED:
        return UNSUPPORTED, 0
    if not legacy:
        return OFF, 0
    mode = path.stat().st_mode & 0o777
    backup = backup_path(path)
    if not os.path.lexists(backup):
        put(backup, data, mode)
    drop = set(legacy)
    kept = [line for index, line in enumerate(text.split("\n")) if index not in drop]
    put(path, "\n".join(kept).encode("utf-8"), mode)
    return OFF, len(legacy)


def drop_backup(path):
    try:
        os.unlink(backup_path(path))
    except OSError:
        pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("check", "clean"):
        p = sub.add_parser(name)
        p.add_argument("--path", required=True)
        p.add_argument("keys", nargs="+")
    args = parser.parse_args(argv)
    if args.cmd == "check":
        return check(args.path, args.keys)
    try:
        result, removed = clean(args.path, args.keys)
    except OSError as error:
        print(f"engine_config: {error}", file=sys.stderr)
        return NOT_OFF
    if result == UNSUPPORTED:
        return UNSUPPORTED
    print(f"{removed} {backup_path(args.path)}")
    return OFF


if __name__ == "__main__":
    sys.exit(main())
