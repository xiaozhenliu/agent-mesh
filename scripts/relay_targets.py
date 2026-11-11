#!/usr/bin/env python3
"""Which relay URLs this Mac's Reason Network engine uses, so check.sh and install.sh probe the right ones.

  relay_targets.py --rn PATH --home DIR --pinned VERSION

Prints the source on the first line and one URL per following line; exit 0. When the relay cannot be told,
prints "unknown: <reason>" and exits 3. Order of evidence:
  1. the team's own relay (`reason-network team` header shows a URL);
  2. no engine installed yet: the default relays of the pinned VERSION;
  3. engine 0.10.x: n0's public relays;
  4. engine 0.12.x: the last "net: on relay <url>" in daemon.log, else the relay list in relays.json.
The `team` header text for the default relays is drawn by the client binary, not the daemon, so it is not used.
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

N0 = ["https://aps1-1.relay.n0.iroh.link/"]
REASON = ["https://relay-sfo1.compute.reasonmachines.com/", "https://relay-iad1.compute.reasonmachines.com/",
          "https://relay-fra1.compute.reasonmachines.com/", "https://relay-sin1.compute.reasonmachines.com/"]
URL = re.compile(r"https?://[^\s/]+(?:/[^\s]*)?")
ON_RELAY = re.compile(r"\bnet: on relay (https?://\S+)")
HEADER = re.compile(r"^team \S+ · relay: (\S.*)$")


def run(rn, *args):
    try:
        r = subprocess.run([rn, *args], capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def series(version):
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.\d+", version or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def defaults(sv):
    if sv == (0, 10):
        return N0
    if sv == (0, 12):
        return REASON
    return None


def team_relay(rn):
    out = run(rn, "team")
    if not out:
        return None
    m = HEADER.match(out.splitlines()[0].strip())
    if m and URL.fullmatch(m.group(1).strip()):
        return m.group(1).strip()
    return None


def last_on_relay(home):
    found = None
    try:
        with open(home / "daemon.log", "rb") as f:
            for raw in f:
                if b"net: on relay " in raw:
                    m = ON_RELAY.search(raw.decode("utf-8", "replace"))
                    found = m.group(1) if m else found
    except OSError:
        return None
    return found


def listed_relays(home):
    try:
        signed = json.loads((home / "relays.json").read_text())["signed"]
        payload = signed.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        urls = [e["url"] for e in claims["relays"]]
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        return None
    urls = [u for u in urls if isinstance(u, str) and URL.fullmatch(u)]
    return urls or None


def targets(rn, home, pinned):
    installed = os.access(rn, os.X_OK) and Path(rn).is_file()
    if not installed:
        urls = defaults(series(pinned))
        if urls is None:
            return "unknown: no engine installed and no known default relays for " + pinned, []
        return f"default relays of Reason Network {pinned} (not installed yet)", urls
    explicit = team_relay(rn)
    if explicit:
        return "the team's relay", [explicit]
    out = run(rn, "--version") or ""
    version = out.split()[1] if len(out.split()) >= 2 else ""
    sv = series(version)
    if sv == (0, 10):
        return f"n0 public relays (Reason Network {version})", N0
    if sv == (0, 12):
        url = last_on_relay(home)
        if url:
            return f"home relay from daemon.log (Reason Network {version})", [url]
        urls = listed_relays(home)
        if urls:
            return f"Reason's relay list in relays.json (Reason Network {version})", urls
        return f"unknown: Reason Network {version} has not logged a relay yet and has no relays.json", []
    return f"unknown: Reason Network version {version or 'unreadable'}", []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rn", required=True)
    ap.add_argument("--home", required=True)
    ap.add_argument("--pinned", required=True)
    a = ap.parse_args()
    source, urls = targets(a.rn, Path(a.home), a.pinned)
    print(source)
    for u in urls:
        print(u)
    return 0 if urls else 3


if __name__ == "__main__":
    sys.exit(main())
