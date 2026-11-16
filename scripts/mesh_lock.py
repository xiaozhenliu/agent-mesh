#!/usr/bin/env python3
"""The management lock of install.sh, update.sh and uninstall.sh: an flock on a stable file, held by the shell.

  mesh_lock.py acquire --fd N --path P [--wait SECONDS] [--legacy-dir DIR]
      lock the open file N (the shell opened P on it), wait up to SECONDS (default 0), check that N is still the
      file at P, refuse while DIR/pid is a running older update.sh and otherwise remove DIR, then write our pid
  mesh_lock.py inherit --fd N --path P
      check that N is the file at P and that this open file holds the lock (a child of the command that took it)

Exit status: 0 held; 3 another command holds it; 5 P was replaced while waiting (open it again and retry);
6 an older update.sh is running; 2 usage or an error.
"""
import argparse
import errno
import fcntl
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from messages import t

HELD, BUSY, MOVED, LEGACY = 0, 3, 5, 6


def same_file(fd, path):
    try:
        a, b = os.fstat(fd), os.stat(path)
    except OSError:
        return False
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def try_lock(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        if error.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
            return False
        raise
    return True


def holder(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            line = stream.readline().strip()
    except OSError:
        return "?"
    return line or "?"


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def runs_update(pid):
    try:
        done = subprocess.run(["ps", "-p", str(pid), "-o", "command="], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return True
    return done.returncode != 0 or "update.sh" in done.stdout.decode("utf-8", "replace")


def legacy_update(folder):
    folder = Path(folder)
    if not folder.is_dir() or folder.is_symlink():
        return None
    try:
        pid = int((folder / "pid").read_text().strip())
    except (OSError, ValueError):
        pid = None
    if pid and pid > 0 and pid not in (os.getpid(), os.getppid()) and alive(pid) and runs_update(pid):
        return pid
    print(t("lock.legacy_stale", folder))
    shutil.rmtree(folder, ignore_errors=True)
    return None


def acquire(fd, path, wait=0.0, legacy=None):
    deadline = time.monotonic() + max(0.0, wait)
    while not try_lock(fd):
        if time.monotonic() >= deadline:
            print(t("lock.busy", holder(path), path), file=sys.stderr)
            return BUSY
        time.sleep(0.1)
    if not same_file(fd, path):
        return MOVED
    if legacy:
        pid = legacy_update(legacy)
        if pid:
            print(t("lock.legacy_running", pid, legacy), file=sys.stderr)
            return LEGACY
    os.ftruncate(fd, 0)
    os.write(fd, f"pid {os.getppid()}\n".encode())
    return HELD


def inherit(fd, path):
    if same_file(fd, path) and try_lock(fd):
        return HELD
    print(t("lock.not_inherited", fd, path), file=sys.stderr)
    return BUSY


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("acquire", "inherit"):
        p = sub.add_parser(name)
        p.add_argument("--fd", type=int, required=True)
        p.add_argument("--path", required=True)
        if name == "acquire":
            p.add_argument("--wait", type=float, default=0.0)
            p.add_argument("--legacy-dir")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "acquire":
            return acquire(args.fd, args.path, args.wait, args.legacy_dir)
        return inherit(args.fd, args.path)
    except OSError as error:
        print(t("lock.failed", args.path, error.strerror or error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
