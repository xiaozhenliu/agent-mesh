import _thread
import atexit
import copy
import functools
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path, PurePath

SCALARS = (str, int, float, bool, type(None))
CONTAINERS = (list, tuple, set, frozenset, dict)
MISSING = object()


class State:
    pid = None
    root = None
    serial = 0
    owner = None
    base = None
    started = None
    store = None
    saved = {}
    depth = 0


def root():
    if State.pid != os.getpid():
        temp = tempfile.TemporaryDirectory(prefix="am.")
        atexit.register(temp.cleanup)
        State.pid, State.root, State.serial, State.owner, State.base = os.getpid(), Path(temp.name), 0, None, None
        State.store, State.saved, State.depth = None, {}, 0
    return State.root


def drop():
    if State.store is not None:
        State.store.cleanup()
    State.store, State.saved = None, {}


def abandon():
    drop()
    State.serial += 1
    State.base = root() / f"t{State.serial}"


def claim(test):
    """Returns the directory of an InstallHarness test: one path shared by the tests of a class, so that a fixture's
    files can be replayed there, or a new path inside test.temp when setUp runs again within one test."""
    keep = Path(test.temp.name)
    if "base" in vars(test):
        base = keep / "t"
        base.mkdir()
        return base
    root()
    if State.owner is not type(test):
        State.owner = type(test)
        abandon()
    elif os.path.lexists(State.base):
        abandon()
    State.base.mkdir()
    State.started = time.time()
    test.addCleanup(release, State.base, keep, State.started)
    return State.base


def release(base, keep, started):
    """Moves the test's tree into its own temporary folder, which removes it; when a thread or process of the test
    may still write there, the later tests of the class also move to a new path."""
    if base != State.base:
        return
    if not alone() or leftovers(base, started) != []:
        abandon()
    try:
        os.rename(base, Path(tempfile.mkdtemp(dir=keep)) / "t")
    except OSError:
        if base == State.base:
            abandon()


def seconds(text):
    days, _, clock = text.rpartition("-")
    parts = clock.split(":")
    if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts) or days and not days.isdigit():
        raise ValueError(text)
    total = 0
    for part in parts:
        total = total * 60 + int(part)
    return int(days or 0) * 86400 + total


def processes():
    """(pid, parent pid, seconds since start, command line) of every process, or None when ps cannot list them."""
    ps = shutil.which("ps", path=os.defpath) or shutil.which("ps")
    if not ps:
        return None
    try:
        proc = subprocess.Popen([ps, "-ww", "-axo", "pid=,ppid=,etime=,command="], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, errors="replace")
    except OSError:
        return None
    with proc:
        try:
            out = proc.communicate(timeout=10)[0]
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return None
    if proc.returncode:
        return None
    rows = []
    for line in out.splitlines():
        fields = line.split(None, 3)
        try:
            pid, ppid, age = int(fields[0]), int(fields[1]), seconds(fields[2])
        except (IndexError, ValueError):
            return None
        if pid != proc.pid:
            rows.append((pid, ppid, age, fields[3] if len(fields) > 3 else ""))
    return rows


def descends(pid, parents, ancestor):
    seen = set()
    while pid in parents and pid not in seen:
        if pid == ancestor:
            return True
        seen.add(pid)
        pid = parents[pid]
    return False


def leftovers(base, started):
    """Processes that may still change files under base: those naming it, as given or resolved, and those started
    since the test began that descend from this process, or that are in its session and in its process group or a
    group whose leader has exited, without hanging below the process that started this one (a parallel test run or a
    pipeline); None when ps cannot tell. A session can hold unrelated processes: on macOS, launch agents and the
    services launchd starts on demand all share one session, each in a group of its own."""
    rows = processes()
    if rows is None:
        return None
    me = os.getpid()
    parents = {pid: ppid for pid, ppid, _, _ in rows}
    if me not in parents:
        return None
    mine, pid = set(), me
    while pid not in mine:
        mine.add(pid)
        pid = parents.get(pid, pid)
    starter = parents.get(me) if parents.get(me, 0) > 1 else None
    spellings = sorted({str(base), os.path.realpath(base)}, key=len, reverse=True)
    named = re.compile("(?:" + "|".join(map(re.escape, spellings)) + r")(?![\w.-])")
    try:
        session, group = os.getsid(0), os.getpgid(0)
    except OSError:
        return None
    window = time.time() - started + 1
    found = []
    for pid, ppid, age, command in rows:
        if pid in mine:
            continue
        if named.search(command):
            found.append(pid)
        elif age <= window:
            if descends(ppid, parents, me):
                found.append(pid)
                continue
            if starter is not None and descends(ppid, parents, starter):
                continue
            try:
                if os.getsid(pid) != session:
                    continue
                leader = os.getpgid(pid)
            except ProcessLookupError:
                continue
            except OSError:
                return None
            if leader == group or leader not in parents:
                found.append(pid)
    return found


def alone():
    """Whether this is the only Python thread, counting threads started with _thread, whether or not they run Python
    code at the moment."""
    return threading.active_count() == 1 and len(sys._current_frames()) == 1 and _thread._count() == 0


def quiet():
    return alone() and leftovers(State.base, State.started) == []


def walk_error(error):
    raise error


def within(path, home):
    return path == home or path.startswith(home + os.sep)


def listing(base):
    """Kind, mode, device, inode and mtime of every entry under base by relative path, with the target of each
    link; None when the tree holds something a replay cannot reproduce (hard links, fifos, sockets, devices, links
    to folders outside it). Opens no file, so it releases no lock this process holds."""
    home = os.path.realpath(base)
    info = os.lstat(base)
    entries = {".": (stat.S_IFDIR, stat.S_IMODE(info.st_mode), info.st_dev, info.st_ino, info.st_mtime_ns, None)}
    for top, dirs, files in os.walk(base, onerror=walk_error):
        for name in dirs + files:
            path = os.path.join(top, name)
            info = os.lstat(path)
            kind, data = stat.S_IFMT(info.st_mode), None
            if kind == stat.S_IFLNK:
                data = os.readlink(path)
                if not within(os.path.realpath(path), home) and os.path.isdir(path):
                    return None
            elif kind != stat.S_IFDIR and (kind != stat.S_IFREG or info.st_nlink != 1):
                return None
            entries[os.path.relpath(path, base)] = (kind, stat.S_IMODE(info.st_mode), info.st_dev, info.st_ino,
                                                    info.st_mtime_ns, data)
    return entries


def contents(base, entries):
    """The entries with the SHA-256 of each regular file, read in blocks."""
    found = {}
    for rel, (kind, mode, dev, ino, mtime, data) in entries.items():
        if kind == stat.S_IFREG:
            total = hashlib.sha256()
            with open(os.path.join(base, rel), "rb") as stream:
                for block in iter(functools.partial(stream.read, 1 << 20), b""):
                    total.update(block)
            data = total.hexdigest()
        found[rel] = (kind, mode, dev, ino, mtime, data)
    return found


def scan(base):
    entries = listing(base)
    return None if entries is None else contents(base, entries)


def sound(entries):
    return all(mode & 0o700 == 0o700 for kind, mode, *_ in entries.values() if kind == stat.S_IFDIR)


def opened(entries):
    """Whether this process holds a descriptor on anything in the tree (a lock, an open log), or cannot tell."""
    inodes = {(dev, ino) for _, _, dev, ino, _, _ in entries.values()}
    try:
        names = os.listdir("/dev/fd")
    except OSError:
        return True
    for name in names:
        try:
            info = os.fstat(int(name))
        except (OSError, ValueError):
            continue
        if (info.st_dev, info.st_ino) in inodes:
            return True
    return False


def digest(base, entries):
    """Hash of the tree's paths, kinds, modes, contents and link targets, plus the file a link outside it points to."""
    home = os.path.realpath(base)
    total = hashlib.sha256()
    for rel in sorted(entries):
        kind, mode, _, _, _, data = entries[rel]
        total.update(os.fsencode(rel) + f"\0{kind:o}\0{mode:o}\0".encode())
        if kind == stat.S_IFREG:
            total.update(data.encode())
        elif kind == stat.S_IFLNK:
            total.update(os.fsencode(data) + b"\0" + repr(target(os.path.join(base, rel), home)).encode())
        total.update(b"\0")
    return total.hexdigest()


def target(link, home):
    path = os.path.realpath(link)
    if within(path, home):
        return None
    try:
        info = os.stat(path)
    except OSError:
        return "missing"
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_mode


def changes(before, after):
    """The fixture's effect on the tree as replay steps; None when a replay could not reproduce it."""
    if before["."][3] != after["."][3]:
        return None
    ops = {"gone": [], "made": [], "rewrite": [], "modes": [], "touch": []}
    moved = []
    for rel in sorted(set(before) | set(after)):
        old, new = before.get(rel), after.get(rel)
        under = any(rel.startswith(prefix + os.sep) for prefix in moved)
        kept = (old is not None and new is not None and old[0] == new[0] and old[3] == new[3]
                and (old[0] != stat.S_IFLNK or old[5] == new[5]))
        if kept and under:
            return None
        if kept:
            if new[0] == stat.S_IFLNK:
                continue
            if new[0] == stat.S_IFREG and new[5] != old[5]:
                if not old[1] & 0o200:
                    return None
                ops["rewrite"].append((rel, new[5]))
            elif new[4] != old[4]:
                ops["touch"].append(rel)
            if new[1] != old[1]:
                ops["modes"].append((rel, new[1]))
            continue
        if old is not None and not under:
            ops["gone"].append(rel)
        if old is not None and old[0] == stat.S_IFDIR:
            moved.append(rel)
        if new is not None:
            ops["made"].append((rel, new[0], new[1], new[5]))
    return ops


def overwrite(path, source):
    with open(source, "rb") as reader, open(path, "wb") as writer:
        shutil.copyfileobj(reader, writer, 1 << 20)


def rewind(base, before):
    """Puts back the mtime of every folder and file of before that a replay and its undo changed."""
    for rel, (kind, _, _, _, mtime, _) in before.items():
        if kind == stat.S_IFLNK:
            continue
        path = os.path.join(base, rel)
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            continue
        if info.st_mtime_ns != mtime:
            os.utime(path, ns=(info.st_atime_ns, mtime))


def undo_all(base, undo, before):
    for step in reversed(undo):
        step()
    rewind(base, before)


def replay(base, ops, work, before):
    """Repeats the steps on base and returns how to undo them; on an error undoes the steps taken, puts back the
    mtimes of before and raises."""
    stage, trash = work / "stage", work / "trash"
    stage.mkdir()
    trash.mkdir()
    staged = {}
    for number, (rel, kind, mode, data) in enumerate(ops["made"]):
        if kind == stat.S_IFREG:
            staged[rel] = stage / str(number)
            shutil.copyfile(data, staged[rel])
    undo = []
    try:
        for number, rel in enumerate(ops["gone"]):
            os.rename(base / rel, trash / str(number))
            undo.append(functools.partial(os.rename, trash / str(number), base / rel))
        for rel, kind, mode, data in ops["made"]:
            path = base / rel
            if kind == stat.S_IFDIR:
                os.mkdir(path)
                undo.append(functools.partial(os.rmdir, path))
                os.chmod(path, mode)
            elif kind == stat.S_IFLNK:
                os.symlink(data, path)
                undo.append(functools.partial(os.unlink, path))
            else:
                os.rename(staged[rel], path)
                undo.append(functools.partial(os.unlink, path))
                os.chmod(path, mode)
        for number, (rel, data) in enumerate(ops["rewrite"]):
            path, old = base / rel, trash / f"was{number}"
            shutil.copyfile(path, old)
            undo.append(functools.partial(overwrite, path, old))
            overwrite(path, data)
        for rel, mode in ops["modes"]:
            path = base / rel
            undo.append(functools.partial(os.chmod, path, stat.S_IMODE(os.lstat(path).st_mode)))
            os.chmod(path, mode)
        for rel in ops["touch"]:
            path = base / rel
            info = os.lstat(path)
            undo.append(functools.partial(os.utime, path, ns=(info.st_atime_ns, info.st_mtime_ns)))
            os.utime(path)
    except BaseException:
        undo_all(base, undo, before)
        raise
    return undo


def plain(value, seen, depth=0):
    """Whether value is data a key can describe: scalars, paths and containers of them, with no mutable container
    reachable twice."""
    kind = type(value)
    if kind in SCALARS or isinstance(value, PurePath):
        return True
    if depth > 20 or kind not in CONTAINERS:
        return False
    if kind in (list, set, dict):
        if id(value) in seen:
            return False
        seen.add(id(value))
    items = [part for pair in value.items() for part in pair] if kind is dict else list(value)
    return all(plain(item, seen, depth + 1) for item in items)


def canonical(value):
    kind = type(value)
    if kind is dict:
        return "dict{" + ",".join(f"{canonical(k)}:{canonical(v)}" for k, v in value.items()) + "}"
    if kind in (set, frozenset):
        return kind.__name__ + "{" + ",".join(sorted(canonical(item) for item in value)) + "}"
    if kind in (list, tuple):
        return kind.__name__ + "(" + ",".join(canonical(item) for item in value) + ")"
    if isinstance(value, PurePath):
        return kind.__name__ + "(" + repr(str(value)) + ")"
    return kind.__name__ + ":" + repr(value)


def inside(value, home):
    """Whether every path in value resolves, links followed, to home or below it."""
    if isinstance(value, PurePath) or type(value) is str and os.path.isabs(value):
        return within(os.path.realpath(value), home)
    if type(value) is dict:
        return all(inside(key, home) and inside(item, home) for key, item in value.items())
    if type(value) in CONTAINERS:
        return all(inside(item, home) for item in value)
    return True


def keepable(result, seen):
    if type(result) is subprocess.CompletedProcess:
        return plain(vars(result), seen)
    return plain(result, seen)


def code(test, fixture):
    """The class attributes along the MRO and the globals of the modules involved, which the key records with pin()."""
    found = [(cls.__qualname__, vars(cls)) for cls in type(test).__mro__]
    names = {fixture.__module__} | {cls.__module__ for cls in type(test).__mro__}
    found += [(name, vars(sys.modules[name])) for name in sorted(names) if name in sys.modules]
    return [(owner, name, value) for owner, space in found for name, value in list(space.items())]


def pin(owner, name, value):
    """What the key records of a class attribute or global: its identity, and the content of plain data, which a
    patch can change in place."""
    return owner, name, id(value), canonical(value) if type(value) in CONTAINERS and plain(value, set()) else None


def surroundings():
    mask = os.umask(0o022)
    os.umask(mask)
    return os.getcwd(), mask, canonical(dict(os.environ))


def store():
    if State.store is None:
        State.store = tempfile.TemporaryDirectory(dir=root())
    return State.store.name


def save(base, key, before, changed, result, pins):
    try:
        listed = listing(base)
        if listed is None or not sound(listed) or opened(listed):
            return
        after = contents(base, listed)
        ops = changes(before, after)
        if ops is None:
            return
        expected = digest(base, after)
        entry = Path(tempfile.mkdtemp(dir=store()))
        try:
            stored = {}
            for rel, data in [(rel, data) for rel, kind, _, data in ops["made"] if kind == stat.S_IFREG] + ops["rewrite"]:
                if data not in stored:
                    stored[data] = entry / data
                    shutil.copyfile(base / rel, stored[data])
        except BaseException:
            shutil.rmtree(entry, ignore_errors=True)
            raise
    except OSError:
        return
    ops["made"] = [(rel, kind, mode, stored[data] if kind == stat.S_IFREG else data)
                   for rel, kind, mode, data in ops["made"]]
    ops["rewrite"] = [(rel, stored[data]) for rel, data in ops["rewrite"]]
    State.saved[key] = (ops, expected, copy.deepcopy(changed), copy.deepcopy(result), pins)


def assign(test, changed):
    for name, value in copy.deepcopy(changed).items():
        current = vars(test).get(name)
        if type(current) is dict and type(value) is dict:
            current.clear()
            current.update(value)
        elif type(current) is list and type(value) is list:
            current[:] = value
        elif type(current) is set and type(value) is set:
            current.clear()
            current.update(value)
        else:
            setattr(test, name, value)


def restore(test, snapshot, before):
    """Replays the snapshot on the tree scanned as before and returns the first build's result; MISSING, with the
    tree as it was, when the replay fails or does not give the first build's tree."""
    ops, expected, changed, result, _ = snapshot
    try:
        work = tempfile.TemporaryDirectory(dir=root())
    except OSError:
        return MISSING
    try:
        try:
            undo = replay(test.base, ops, Path(work.name), before)
        except OSError:
            return MISSING
        try:
            entries = scan(test.base)
            good = entries is not None and digest(test.base, entries) == expected
        except OSError:
            good = False
        if not good:
            undo_all(test.base, undo, before)
            return MISSING
    finally:
        work.cleanup()
    assign(test, changed)
    return copy.deepcopy(result)


def reuse(test, fixture, args, kwargs):
    base = test.base
    attrs = {name: value for name, value in vars(test).items() if not name.startswith("_")}
    seen = set()
    data = {name: canonical(value) for name, value in attrs.items() if plain(value, seen)}
    others = {name: value for name, value in attrs.items() if name not in data}
    if (any(type(value) is not tempfile.TemporaryDirectory for value in others.values())
            or not plain((args, kwargs), seen) or not inside((args, kwargs), os.path.realpath(base)) or not quiet()):
        return fixture(test, *args, **kwargs)
    pins = code(test, fixture)
    try:
        listed = listing(base)
        before = None if listed is None or not sound(listed) or opened(listed) else contents(base, listed)
        key = None if before is None else (
            fixture.__module__, fixture.__qualname__, canonical(args), canonical(kwargs), tuple(sorted(data.items())),
            tuple(pin(*item) for item in pins), surroundings(), digest(base, before))
    except OSError:
        key = None
    if key is None:
        return fixture(test, *args, **kwargs)
    saved = State.saved.get(key)
    if saved is not None:
        result = restore(test, saved, before)
        return fixture(test, *args, **kwargs) if result is MISSING else result
    cleanups = list(test._cleanups)
    result = fixture(test, *args, **kwargs)
    now = {name: value for name, value in vars(test).items() if not name.startswith("_")}
    if (set(attrs) - set(now) or len(test._cleanups) != len(cleanups)
            or any(a is not b for a, b in zip(test._cleanups, cleanups))
            or any(now[name] is not value for name, value in others.items()) or not quiet()):
        return result
    seen, changed = set(), {}
    for name, value in now.items():
        if name in others:
            continue
        if not plain(value, seen):
            return result
        if canonical(value) != data.get(name):
            changed[name] = value
    if keepable(result, seen):
        save(base, key, before, changed, result, [value for _, _, value in pins])
    return result


def reusable(fixture):
    """Builds the fixture once per test class and starting state, then replays the files it changed when a later
    test of the class calls it the same way.

    A call is replayed only when its arguments, the test's plain attributes, the class and module code, the
    environment and every file under self.base match the first build, and no other thread or process is running.
    The replay changes the files at the same path, keeps the inode of every file the fixture left in place, checks
    the result against the first build and returns the first build's return value. Times, request ids and PIDs
    inside the replayed files come from the first build: a test that compares them with a time it computes, or that
    changes what the key cannot see (other modules, files outside self.base that the fixture reads), calls
    fixture.__wrapped__(self, ...) to build live."""
    @functools.wraps(fixture)
    def run(test, *args, **kwargs):
        if State.depth or State.owner is not type(test) or getattr(test, "base", None) != State.base:
            return fixture(test, *args, **kwargs)
        State.depth += 1
        try:
            return reuse(test, fixture, args, kwargs)
        finally:
            State.depth -= 1
    return run
