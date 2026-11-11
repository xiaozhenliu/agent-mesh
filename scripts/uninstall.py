#!/usr/bin/env python3
"""Tiered uninstaller for the Mac side of agent-mesh; uninstall.sh runs it.

  uninstall.py                          what is installed; in a terminal, then offers the levels
  uninstall.py --plan [--level L]       what is installed and what level L would do; never asks, never removes
  uninstall.py --level program [--yes]  remove the relay program, keep identity, settings and key
  uninstall.py --level identity         also leave the team on both computers and remove keys and identity
  uninstall.py --level all              also remove logs, the engine and the cloud's data
  uninstall.py --residual --level identity|all
                                        no identity left on this Mac (e.g. after an older --all)
  uninstall.py --cloud-prompt --level identity|all [--residual]
                                        only the cloud step: prompt, PROOF check, confirmation code
  --lang en|zh-CN                       language of the printed cloud prompt
"""
import argparse
import hashlib
import hmac
import json
import os
import plistlib
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import relay_names
from render_prompt import ROOT, render_prompt

LABEL = "com.local.agent-relay"
LEVELS = ("program", "identity", "all")
DEEP = ("identity", "all")
KNOWN_RELAYS = {"02b6a5097a93e66e": "3c810b5 to v0.1.1", "1467396a0de8baa4": "v0.2.0 to v0.4.1",
                "b034a2293d05274b": "an untagged commit"}
TEMP6 = r"[A-Za-z0-9]{6}"
RC_FILES = (".zshrc", ".zprofile", ".zshenv", ".bash_profile", ".bashrc", ".profile", ".config/fish/config.fish")
RULE = "-" * 64


class Stop(Exception):
    pass


def valid_name(name):
    return isinstance(name, str) and bool(relay_names.NAME.fullmatch(name)) and len(name) <= 40


class Paths:
    def __init__(self, home):
        h = Path(home)
        self.home = h
        self.agents = h / "Library/LaunchAgents"
        self.plist = self.agents / f"{LABEL}.plist"
        self.lib = h / ".local/lib/agent-mesh"
        self.relay = self.lib / "agent_relay.py"
        self.record = self.lib / "deployed.json"
        self.lock = self.lib / "update.lock"
        self.rollback = self.lib / "rollback"
        self.rollback_tmp = self.lib / "rollback.tmp"
        self.conf = h / ".config/agent-relay"
        self.env = self.conf / "env"
        self.key = self.conf / "outbound.key"
        self.state = h / "Library/Application Support/agent-relay"
        self.logs = h / "Library/Logs"
        self.rn_bin = h / ".local/bin/reason-network"
        self.rn_home = h / ".reason/network"
        self.machine_key = self.rn_home / "machine.key"
        self.config_toml = self.rn_home / "config.toml"
        self.dsh = h / ".dsh"
        self.patch = self.dsh / "cordis.patch.yml"
        self.dsh_tmp = self.dsh / "cordis.patch.yml.reason-network-bridge.tmp"
        self.plugin = self.dsh / "plugins/reason-network-bridge.mjs"
        self.dsh_backup = self.dsh / "backup-reason-network-bridge"
        self.dsh_logs = self.dsh / "logs"

    def unsafe(self, path):
        path = Path(path)
        try:
            rel = path.relative_to(self.home)
        except ValueError:
            return f"{path} is outside the home folder"
        cur = self.home
        for part in rel.parts:
            cur = cur / part
            try:
                st = os.lstat(cur)
            except FileNotFoundError:
                return None
            except OSError as error:
                return f"cannot check {cur}: {error.strerror or error}"
            if stat.S_ISLNK(st.st_mode):
                return f"{cur} is a symlink"
            if st.st_uid != os.getuid():
                return f"{cur} belongs to another user"
        real_home = os.path.realpath(self.home)
        real = os.path.realpath(path)
        if real != real_home and not real.startswith(real_home + os.sep):
            return f"{path} resolves outside the home folder"
        return None

    def temp_files(self):
        found = []
        for folder, pattern in ((self.agents, rf"\.{re.escape(LABEL)}\.{TEMP6}"),
                                (self.lib, rf"\.agent_relay\.py\.{TEMP6}")):
            try:
                names = sorted(os.listdir(folder))
            except OSError:
                continue
            for name in names:
                p = folder / name
                if re.fullmatch(pattern, name) and p.is_file() and not p.is_symlink():
                    found.append(p)
        return found

    def log_files(self):
        return self.matching(self.logs, r"agent-relay\.log(\.[0-9]+)?|agent-relay\.launchd\.log(\.[0-9]+)?")

    def dsh_log_files(self):
        return self.matching(self.dsh_logs, r"reason-network-bridge\.log(\.[0-9]+)?")

    @staticmethod
    def matching(folder, pattern):
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return []
        return [folder / n for n in names
                if re.fullmatch(pattern, n) and (folder / n).is_file() and not (folder / n).is_symlink()]


def run(cmd, env=None, timeout=120):
    try:
        r = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    except FileNotFoundError:
        return 127, f"{cmd[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]}: no answer within {timeout} s"
    except OSError as error:
        return 126, f"{cmd[0]}: {error}"
    return r.returncode, r.stdout.decode("utf-8", errors="replace")


def loaded_plist(out):
    m = re.search(r"^\s*path = (.+?)\s*$", out, re.M)
    return m.group(1) if m else None


def short(text, limit=300):
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "..."


def tag(key, kind, challenge):
    msg = "agent-mesh-uninstall-v1\n" + kind + "\n" + challenge
    return hmac.new(key.encode(), msg.encode(), hashlib.sha256).hexdigest()[:16]


class Roster:
    def __init__(self, status, team=None, me=None, rows=(), message=""):
        self.status = status
        self.team = team
        self.me = me
        self.rows = list(rows)
        self.message = message

    @classmethod
    def parse(cls, code, out):
        if code != 0:
            if "not in a team" in out:
                return cls("left", message=short(out))
            return cls("unknown", message=short(out))
        lines = out.splitlines()
        head = lines[0].split() if lines else []
        if len(head) < 2 or head[0] != "team":
            return cls("unknown", message="unexpected output: " + short(out))
        me, rows = None, []
        for line in lines[1:]:
            parts = line.split()
            if len(parts) >= 4 and parts[1] == "(verified)":
                del parts[1]
            if len(parts) < 3:
                continue
            person, machine, status = parts[0], parts[1], " ".join(parts[2:])
            if status == "this machine":
                me = (person, machine)
            else:
                rows.append((person, machine, status))
        if me is None:
            return cls("unknown", message="the team list has no 'this machine' row")
        return cls("in", team=head[1], me=me, rows=rows)

    @staticmethod
    def online(row):
        return not row[2].startswith("offline")


class Inventory:
    def __init__(self, paths, here):
        self.p = paths
        self.here = Path(here)
        self.stops = []
        self.notes = []
        self.skips = {}
        self.foreign = []
        self.collect()

    def stop(self, scope, reason):
        self.stops.append((scope, reason))

    def stops_for(self, level):
        return [reason for scope, reason in self.stops if scope == "all" or level in DEEP]

    def rn_env(self):
        env = dict(os.environ)
        env.update(REASON_NETWORK_HOME=str(self.p.rn_home), REASON_NETWORK_NO_PROMPT="1")
        return env

    def rn(self, *args, timeout=120):
        if not self.critical_ok or "rn-bin" in self.skips:
            return 1, "not run: the Reason Network folders are unsafe (see the STOP lines)"
        return run([str(self.p.rn_bin), *args], env=self.rn_env(), timeout=timeout)

    def collect(self):
        p = self.p
        self.critical_ok = True
        for path in (p.rn_home, p.conf):
            why = p.unsafe(path)
            if why:
                self.critical_ok = False
                self.stop("deep", why + "; levels identity and all stop, and Reason Network is not queried")
        for key, path in (("service", p.plist), ("program", p.lib), ("state", p.state), ("rn-bin", p.rn_bin),
                          ("logs", p.logs), ("dsh", p.dsh)):
            why = p.unsafe(path)
            if why:
                self.skips[key] = why
        self.read_plist()
        self.read_launchd()
        self.scan_foreign()
        self.read_env()
        self.read_record()
        self.read_engine()
        self.resolve_team()
        self.read_queue()
        self.read_lock()
        self.read_dsh()
        self.read_version()
        self.rc_lines = self.read_rc_lines()

    def read_plist(self):
        p = self.p
        self.plist_present = p.plist.exists() or p.plist.is_symlink()
        if not self.plist_present or "service" in self.skips:
            return
        problem = None
        try:
            with open(p.plist, "rb") as stream:
                data = plistlib.load(stream)
        except Exception as error:
            data, problem = None, f"cannot be read ({type(error).__name__})"
        if problem is None:
            args = data.get("ProgramArguments") if isinstance(data, dict) else None
            env = data.get("EnvironmentVariables", {}) if isinstance(data, dict) else None
            if not isinstance(data, dict) or data.get("Label") != LABEL:
                problem = "has another label"
            elif not isinstance(args, list) or len(args) < 2 or args[-1] != str(p.relay):
                problem = "does not start the relay installed in ~/.local/lib/agent-mesh"
            elif not isinstance(env, dict):
                problem = "has unexpected environment settings"
            elif any(str(k).startswith("AGENT_RELAY_") for k in env):
                problem = "overrides AGENT_RELAY_* settings"
            elif env.get("REASON_NETWORK_HOME", str(p.rn_home)) != str(p.rn_home):
                problem = "uses another REASON_NETWORK_HOME"
        if problem:
            self.stop("all", f"{p.plist} {problem}: it is not the login service install.sh writes; "
                             "review it by hand, nothing is removed")

    def read_launchd(self):
        self.loaded, self.running = False, False
        self.launchd_note = ""
        self.loaded_path = None
        code, out = run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], timeout=30)
        if code == 0:
            self.loaded = True
            self.running = "state = running" in out
            self.loaded_path = loaded_plist(out)
            if not self.service_owned(out):
                self.stop("all", f"the loaded {LABEL} service comes from {self.loaded_path or 'an unknown plist'}, "
                                 f"not from {self.p.plist}; it belongs to another home or was loaded by hand, "
                                 "so it is not stopped")
        elif code != 113 and "Could not find service" not in out and "could not find service" not in out:
            self.launchd_note = short(out, 120)
            self.loaded = None
            self.stop("all", f"cannot check whether {LABEL} is loaded: {self.launchd_note}")

    def service_owned(self, out):
        path = loaded_plist(out)
        if path:
            return os.path.realpath(path) == os.path.realpath(self.p.plist)
        return bool(re.search(r"(^|\s)" + re.escape(str(self.p.relay)) + r"(\s|$)", out))

    def scan_foreign(self):
        p = self.p
        scripts = set()
        try:
            names = sorted(os.listdir(p.agents))
        except FileNotFoundError:
            names = []
        except OSError as error:
            names = []
            self.stop("deep", f"cannot list {p.agents} to look for other relays: {error.strerror or error}")
        for name in names:
            if not name.endswith(".plist") or name == f"{LABEL}.plist":
                continue
            try:
                with open(p.agents / name, "rb") as stream:
                    data = plistlib.load(stream)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            label = str(data.get("Label") or name[:-6])
            args = data.get("ProgramArguments") if isinstance(data.get("ProgramArguments"), list) else []
            env = data.get("EnvironmentVariables") if isinstance(data.get("EnvironmentVariables"), dict) else {}
            program = str(data.get("Program") or "")
            words = [str(a) for a in args] + [program]
            hit = any(re.search(r"[A-Za-z0-9]_relay\.py$", w) or w.endswith("agent_relay.py") for w in words)
            hit = hit or any("agent-relay" in str(v) or ".reason/network" in str(v) for v in env.values())
            hit = hit or bool(re.match(r"com\.local\.[A-Za-z0-9._-]*relay", label))
            if hit:
                scripts.update(w for w in words if w.endswith("_relay.py"))
                self.foreign.append((label, f"login service {p.agents / name}"))
        code, out = run(["ps", "-axo", "pid=,command="], timeout=30)
        if code != 0:
            self.stop("deep", "cannot list processes to look for other relays: " + short(out, 120))
            return
        for line in out.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2 or not parts[0].isdigit() or int(parts[0]) in (os.getpid(), os.getppid()):
                continue
            cmd = parts[1]
            if not re.search(r"[A-Za-z0-9]_relay\.py(\s|$)", cmd):
                continue
            if any(re.search(r"(^|\s)" + re.escape(s) + r"(\s|$)", cmd) for s in [str(p.relay), *scripts]):
                continue
            script = re.search(r"([^\s/]*[A-Za-z0-9]_relay\.py)(\s|$)", cmd).group(1)
            program = os.path.basename(cmd.split(None, 1)[0])
            self.foreign.append((f"pid {parts[0]}", f"process {short(program, 40)} running {short(script, 60)} "
                                                   "(arguments not shown)"))

    def read_env(self):
        p = self.p
        self.env_present = p.env.is_file()
        self.cfg = {}
        self.relay_name, self.name_default = None, False
        self.webhook = False
        self.key_source = None
        self.custom_key = None
        if self.env_present and self.critical_ok:
            try:
                self.cfg = relay_names.parse_env(p.env.read_text(encoding="utf-8", errors="replace"))
            except OSError as error:
                self.stop("deep", f"cannot read {p.env}: {error.strerror or error}")
            try:
                self.relay_name = relay_names.from_env(p.env)
                self.name_default = not any(k in self.cfg for k in relay_names.KEYS)
            except ValueError as error:
                self.stop("deep", f"{error}; the Bot address name is unclear, so levels identity and all stop")
        self.webhook = (self.cfg.get("WEBHOOK_URL", "").startswith("https://")
                        and len(self.cfg.get("WEBHOOK_KEY", "")) >= 8)
        self.cloud_machine = self.cfg.get("CLOUD_MACHINE") or relay_names.CLOUD_MACHINE
        if not valid_name(self.cloud_machine):
            self.stop("deep", "CLOUD_MACHINE in the relay env is not a valid machine name")
        if self.cfg.get("OUTBOUND_KEY"):
            self.key_source = "inline"
        else:
            kf = self.cfg.get("OUTBOUND_KEY_FILE")
            path = Path(os.path.expanduser(kf)) if kf else p.key
            real = os.path.realpath(path)
            if kf and not (os.path.isabs(path) and real.startswith(os.path.realpath(p.conf) + os.sep)):
                self.custom_key = path
            self.key_source = path
        rn_bin = self.cfg.get("RN_BIN")
        if rn_bin and os.path.expanduser(rn_bin) != str(p.rn_bin):
            self.notes.append(f"the relay env names another engine (RN_BIN); only {p.rn_bin} is used here")

    def mac_key(self):
        if self.key_source == "inline":
            return self.cfg["OUTBOUND_KEY"].strip() or None
        try:
            text = Path(self.key_source).read_text(encoding="utf-8").strip()
        except (OSError, TypeError, UnicodeDecodeError):
            return None
        return text or None

    def key_present(self):
        return bool(self.mac_key())

    def read_record(self):
        p = self.p
        self.record_present = p.record.is_file()
        self.record_lang = None
        if not self.record_present:
            return
        try:
            data = json.loads(p.record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.notes.append(f"{p.record} cannot be read")
            return
        if not isinstance(data, dict):
            return
        if data.get("lang") in ("en", "zh-CN"):
            self.record_lang = data["lang"]
        recorded = data.get("relay_name")
        if isinstance(recorded, str) and relay_names.problem(recorded) is None:
            if self.relay_name is None and not self.env_present:
                self.relay_name = recorded
            elif self.relay_name is not None and recorded != self.relay_name:
                self.stop("deep", f"the relay env gives the Bot address name {self.relay_name}, but this Mac was "
                                  f"installed with {recorded} (deployed.json); levels identity and all stop")

    def read_engine(self):
        p = self.p
        self.identity = p.machine_key.is_file() and not p.machine_key.is_symlink()
        self.engine = p.rn_bin.is_file() and os.access(p.rn_bin, os.X_OK) and "rn-bin" not in self.skips
        self.config_person = None
        try:
            for line in p.config_toml.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.fullmatch(r'person = "(.*)"', line.strip())
                if m:
                    self.config_person = m.group(1)
                    break
        except OSError:
            pass
        if not self.identity:
            self.roster = Roster("none", message="no Reason Network identity on this Mac")
            return
        if not (self.critical_ok and self.engine):
            self.roster = Roster("unknown", message="the engine is missing or its folders are unsafe")
            return
        self.roster = self.query_roster()

    def query_roster(self):
        code, out = self.rn("team", timeout=60)
        return Roster.parse(code, out)

    def resolve_team(self):
        r = self.roster
        values = {}
        if r.status == "in":
            values["team list"] = r.team
            values["this machine's person"] = r.me[0]
        if self.config_person:
            values["config.toml"] = self.config_person
        if self.cfg.get("PERSON"):
            values["relay env"] = self.cfg["PERSON"]
        self.team = None
        self.team_sources = sorted(values)
        if len(set(values.values())) > 1:
            listed = ", ".join(f"{k} {v}" for k, v in sorted(values.items()))
            self.stop("deep", f"the team name differs between sources ({listed}); levels identity and all stop")
        elif values:
            self.team = next(iter(values.values()))
            if not valid_name(self.team):
                self.stop("deep", f"the team name {self.team[:60]!r} is not a valid name")
        self.mac_machine = r.me[1] if r.status == "in" else None
        self.same_name = self.mac_machine == self.cloud_machine
        self.cloud_rows, self.own_other, self.others = self.classify(r)

    def classify(self, r):
        if r.status != "in":
            return [], [], []
        cloud, own, others = [], [], []
        for row in r.rows:
            if row[0] == r.me[0] and row[1] == self.cloud_machine:
                cloud.append(row)
            elif row[0] == r.me[0]:
                own.append(row)
            else:
                others.append(row)
        return cloud, own, others

    def queue(self):
        path = self.p.state / "state.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "unreadable"
        if not isinstance(data, dict):
            return "unreadable"
        spool = data.get("spool") if isinstance(data.get("spool"), list) else []
        pending = data.get("pending") if isinstance(data.get("pending"), dict) else {}
        sends = data.get("sends") if isinstance(data.get("sends"), dict) else {}
        alerts = data.get("alerts") if isinstance(data.get("alerts"), list) else []
        states = [r.get("state") for r in sends.values() if isinstance(r, dict)]
        return {"spool": len(spool), "pending": len(pending), "sending": states.count("sending"),
                "unknown": states.count("unknown"), "alerts": len(alerts)}

    def read_queue(self):
        self.queue_counts = self.queue()

    def read_lock(self):
        lock = self.p.lock
        self.lock_state = None
        if not lock.is_dir():
            return
        try:
            pid = int((lock / "pid").read_text().strip())
        except (OSError, ValueError):
            pid = None
        alive = False
        if pid:
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            except PermissionError:
                alive = True
        if alive:
            code, out = run(["ps", "-p", str(pid), "-o", "command="], timeout=30)
            if code != 0 or "update.sh" in out:
                self.lock_state = "live"
                self.stop("all", f"./update.sh is running (pid {pid}, {lock}); let it finish first")
                return
        self.lock_state = "stale"
        self.notes.append(f"an earlier ./update.sh left {lock}; levels identity and all remove it")

    def read_dsh(self):
        p = self.p
        try:
            self.dsh_block = "reason-network-bridge" in p.patch.read_text(encoding="utf-8", errors="replace")
        except OSError:
            self.dsh_block = False
        self.dsh_plugin = p.plugin.is_file()
        self.dsh_backup = p.dsh_backup.exists()
        self.dsh_logs = p.dsh_log_files()
        if self.dsh_block or self.dsh_plugin:
            if "dsh" in self.skips:
                self.stop("deep", "dsh: " + self.skips["dsh"])
            for path in (p.patch, p.dsh_tmp, p.plugin):
                why = p.unsafe(path)
                if not why and path == p.dsh_tmp and (path.exists() or path.is_symlink()) and not path.is_file():
                    why = f"{path} is not a regular file"
                if why:
                    self.stop("deep", f"{why}; dsh/mac-uninstall.sh would write through it, so levels identity "
                                      "and all stop; remove it by hand")

    def read_version(self):
        p = self.p
        self.version = "no relay program"
        if p.relay.is_file() and "program" not in self.skips:
            data = p.relay.read_bytes()
            digest = hashlib.sha256(data).hexdigest()[:16]
            mine = (self.here / "agent_relay.py")
            if mine.is_file() and mine.read_bytes() == data:
                known = "same as this checkout"
            else:
                known = KNOWN_RELAYS.get(digest, "unknown version, same layout")
            self.version = f"relay sha {digest} ({known})"
        if self.record_present:
            self.version += "; deployed.json present (v0.4.0 or newer)"
        if p.rollback.exists() or p.rollback_tmp.exists():
            self.notes.append("an earlier relay switch did not finish (rollback/ exists); it is removed, "
                              "not restored")

    def read_rc_lines(self):
        found = []
        for name in RC_FILES:
            path = self.p.home / name
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for number, line in enumerate(lines, 1):
                if ".local/bin" in line and not line.lstrip().startswith("#"):
                    found.append(f"{path}:{number}")
        return found

    def names_line(self):
        name = self.relay_name or relay_names.DEFAULT
        tail = " (default)" if (self.relay_name is None or self.name_default) else ""
        cloud, inbox = relay_names.derived(name)[1:]
        return f"{name}, {cloud}, {inbox}{tail}; cloud machine \"{self.cloud_machine}\""

    def show(self):
        r = self.roster
        print("agent-mesh uninstall: what is on this Mac (nothing removed yet)")
        line = lambda k, v: print(f"  {k:<14}{v}")
        line("version", self.version)
        if self.team:
            line("team/person", f"{self.team}  (from {', '.join(self.team_sources)})")
        else:
            line("team/person", "unknown")
        if r.status == "in":
            line("this machine", f"{r.me[0]}/{r.me[1]}")
            if self.cloud_rows:
                states = ", ".join(row[2] for row in self.cloud_rows)
                line("cloud", f"{r.me[0]}/{self.cloud_machine}  ({len(self.cloud_rows)} member(s): {states})")
            else:
                line("cloud", "not in the team")
        elif r.status == "left":
            line("team", "this Mac already left its team")
        elif r.status == "unknown":
            line("team", "could not be listed: " + r.message)
        else:
            line("team", r.message)
        line("relay names", self.names_line())
        if self.loaded is None:
            service = "unknown (" + self.launchd_note + ")"
        else:
            service = ("loaded, running" if self.running else "loaded, not running") if self.loaded else "not loaded"
        key = "signing key present" if self.key_present() else "no signing key"
        line("relay", f"{service}; webhook {'configured' if self.webhook else 'not configured'}; {key}")
        q = self.queue_counts
        if isinstance(q, dict):
            line("queue", ", ".join(f"{k} {v}" for k, v in q.items()))
        else:
            line("queue", "none" if q is None else "cannot be read")
        line("dsh bridge", "present" if (self.dsh_block or self.dsh_plugin) else "none")
        members = [f"{row[0]}/{row[1]} ({row[2]})" for row in self.own_other + self.others]
        line("other members", ", ".join(members) or "none")
        line("foreign", "; ".join(f"{fid}: {what}" for fid, what in self.foreign) or "none")
        if self.custom_key:
            line("custom key", f"{self.custom_key} (reported only, never removed)")
        for note in self.notes:
            line("note", note)
        for key, why in sorted(self.skips.items()):
            line("skipped", f"{key}: {why}")


class Uninstaller:
    def __init__(self, inv, level, lang, residual=False, yes=False, interactive=False):
        self.inv = inv
        self.p = inv.p
        self.level = level
        self.lang = lang
        self.residual = residual
        self.yes = yes
        self.interactive = interactive
        self.results = []
        self.team = inv.team
        self.cloud_status = None

    def ask(self, prompt):
        if not self.interactive:
            return None
        try:
            return input(prompt).strip()
        except EOFError:
            print()
            return None

    def result(self, label, state, note="", credential=False):
        self.results.append((label, state, note, credential))
        print(f"  {label}: {state}" + (f" ({note})" if note else ""))

    def deep(self):
        return self.level in DEEP

    def plan_rows(self):
        inv, p, deep, everything = self.inv, self.p, self.deep(), self.level == "all"
        keep, remove = "keep", "remove"
        rows = [
            ("relay login service and plist", "present" if inv.plist_present or inv.loaded else "absent",
             "stop and remove"),
            ("relay program, temporary files, rollback/", "present" if p.relay.exists() or p.temp_files()
             or p.rollback.exists() or p.rollback_tmp.exists() else "absent", remove),
            ("deployment record, update lock (~/.local/lib/agent-mesh)",
             "present" if p.lib.exists() else "absent", remove if deep else keep),
            ("relay settings and signing key (~/.config/agent-relay)", "present" if p.conf.exists() else "absent",
             remove if deep else keep),
            ("relay state: queue, replay protection, kept copies", "present" if p.state.exists() else "absent",
             remove if deep else keep),
            ("relay logs", "present" if p.log_files() else "absent", remove if everything else keep),
            ("dsh bridge", "present" if inv.dsh_block or inv.dsh_plugin else "absent",
             "remove (dsh/mac-uninstall.sh --no-restore)" if deep else keep),
            ("dsh bridge backups and log", "present" if inv.dsh_backup or inv.dsh_logs else "absent",
             remove if everything else keep),
            ("cloud: team membership, identity, key, names", "Bot", "Bot leaves and removes them" if deep else keep),
            ("cloud: inbox/, threads/, /workspace/agent-mesh", "Bot",
             "Bot removes them (after listing your files)" if everything else keep),
            ("this Mac's team membership", inv.roster.status, "leave" if deep else keep),
            ("agent wiring and engine service", "present" if inv.engine else "absent",
             "setup --remove" if deep else keep),
            ("Reason Network identity and history (~/.reason/network)",
             "present" if p.rn_home.exists() else "absent", remove if deep else keep),
            ("Reason Network engine (~/.local/bin/reason-network)", "present" if p.rn_bin.exists() else "absent",
             remove if everything else keep),
            ("Grok routine (webhook URL and key)", "Grok UI", "you delete it" if deep else "you pause it"),
        ]
        if self.residual:
            rows = [r for r in rows if not r[0].startswith(("this Mac's team", "agent wiring"))]
        return rows

    def show_plan(self):
        title = {"program": "program: remove the relay, keep identity, settings and key",
                 "identity": "identity: also leave the team on both computers and remove keys and identity",
                 "all": "all: also remove logs, the engine and the cloud's data"}[self.level]
        print(f"\n== plan for level {title}" + (" (residual)" if self.residual else ""))
        for what, now, action in self.plan_rows():
            print(f"  {what:<58} {now:<9} {action}")
        if self.level == "all":
            print("  Only listed, never removed: this folder, PATH lines, reply convention templates, "
                  "an external inbox folder, the Bot's long-term memory.")
        stops = self.inv.stops_for(self.level)
        for reason in stops:
            print(f"  STOP: {reason}")
        if self.deep():
            for fid, what in self.inv.foreign:
                print(f"  STOP unless you type I accept {fid}: another deployment ({what}) shares this Mac; it is "
                      "never changed, but removing the shared identity breaks it")
        return not stops

    def confirm_main(self):
        if self.level == "program":
            if self.yes:
                return True
            return self.ask("Remove the relay program now? Type y: ") == "y"
        phrase = self.team if self.level == "identity" else f"delete {self.team}"
        return self.ask(f"Type {phrase} to go on (anything else stops): ") == phrase

    def confirm_foreign(self):
        for fid, what in self.inv.foreign:
            print(f"Another deployment uses this Mac: {fid} ({what}). It is never changed, but it may share "
                  f"~/.reason/network, which level {self.level} removes.")
            if self.ask(f"Type I accept {fid} to go on anyway (anything else stops): ") != f"I accept {fid}":
                return False
        return True

    def queue_total(self, q):
        return sum(q.values()) if isinstance(q, dict) else 0

    def confirm_queue(self, q, again=False):
        if not isinstance(q, dict) or not self.queue_total(q):
            return True
        counts = ", ".join(f"{k} {v}" for k, v in q.items())
        if self.level == "program":
            print(f"queue {counts}: kept for a reinstall; 'sending' entries become 'unknown' and are not sent again.")
            if not again or self.yes:
                return True
            return self.ask("The queue grew while the relay stopped. Go on? Type y: ") == "y"
        print(f"queue {counts}: these are lost with level {self.level}.")
        return self.ask("Type yes to lose them: ") == "yes"

    def stop_relay(self):
        p = self.p
        was = self.inv.plist_present or self.inv.loaded
        service = f"gui/{os.getuid()}/{LABEL}"
        code, out = run(["launchctl", "print", service], timeout=30)
        if code == 0:
            if not self.inv.service_owned(out):
                self.result("relay login service", "skipped", "the loaded service is not this home's")
                raise Stop(f"the loaded {LABEL} service does not come from {p.plist}; it was not stopped")
            run(["launchctl", "bootout", service], timeout=60)
            for _ in range(20):
                code, out = run(["launchctl", "print", service], timeout=30)
                if code != 0:
                    break
                time.sleep(0.5)
            else:
                self.result("relay login service", "failed", "launchctl bootout did not unload it")
                raise Stop("the relay is still loaded; nothing else was changed")
        if "service" in self.inv.skips:
            self.result("relay login service", "skipped", "unloaded, but the plist was kept: "
                        + self.inv.skips["service"])
            return
        self.remove_paths("relay login service", [p.plist] + [t for t in p.temp_files() if t.parent == p.agents],
                          absent="absent" if not was else None)

    def remove_tree(self, path):
        why = self.p.unsafe(path)
        if why:
            return why
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            os.unlink(path)
        return None

    def remove_paths(self, label, paths, credential=False, skip_key=None, absent=None):
        if skip_key and skip_key in self.inv.skips:
            self.result(label, "skipped", self.inv.skips[skip_key], credential)
            return
        present = [path for path in paths if path.exists() or path.is_symlink()]
        if not present:
            self.result(label, "absent" if absent is None else absent, credential=credential)
            return
        for path in present:
            try:
                why = self.remove_tree(path)
            except OSError as error:
                why = f"{path}: {error.strerror or error}"
            if why:
                self.result(label, "skipped", why, credential)
                return
        self.result(label, "done", credential=credential)

    def program_paths(self):
        p = self.p
        return [p.relay, p.rollback, p.rollback_tmp] + [t for t in p.temp_files() if t.parent == p.lib]

    def cloud_prompt(self, challenge, mode):
        return render_prompt("cloud-uninstall", self.lang, team_name=self.team,
                             mac_machine=self.inv.mac_machine or "mac",
                             relay_name=self.inv.relay_name or relay_names.DEFAULT,
                             level=self.level, mode=mode, challenge=challenge)

    def cloud_step(self):
        mode = "residual" if self.residual else "normal"
        challenge = secrets.token_hex(8)
        text = self.cloud_prompt(challenge, mode)
        print("\n[GROK-UI] User: first pause the routine in Grok Bot and make sure no run is in progress.")
        print("[GROK-UI] User: then send this to Grok Bot as one message. The Bot runs it on [BOT-CLOUD], its cloud "
              "computer, and answers with a check result and a PROOF line:")
        show = lambda: print(f"{RULE}\n{text}{RULE}")
        show()
        key = None if self.residual else self.inv.mac_key()
        while True:
            answer = self.ask("[LOCAL] Type the PROOF value from the Bot, 'none' if it printed none, 'prompt' to "
                              "show the prompt again, or 'skip': ")
            if answer is None or answer == "skip":
                return "skipped", "skipped by the user"
            if answer == "prompt":
                show()
                continue
            if answer == "none":
                print("No proof: the Bot may run apply-no-proof only after you confirm in the chat that this is your "
                      "own deployment and may be removed.")
                break
            if re.fullmatch(r"[0-9a-f]{16}", answer):
                if key is None:
                    print("This Mac has no signing key to check the PROOF with; answer none to go on without proof, "
                          "or skip.")
                    continue
                if not hmac.compare_digest(answer, tag(key, "proof", challenge)):
                    print("The PROOF does not match this Mac's signing key: that cloud belongs to another deployment, "
                          "or the key was replaced without copying it to the cloud. The cloud step stops here.")
                    return "skipped", "PROOF did not match"
                print(f"PROOF matches. [GROK-UI] User: send the Bot this confirmation code and say you agree: "
                      f"{tag(key, 'ack', challenge)}")
                break
            print("Not understood.")
        while True:
            answer = self.ask("[LOCAL] Type done if the Bot reported DONE or KEPT, incomplete if it reported "
                              "INCOMPLETE, or skip if it could not finish: ")
            if answer == "done":
                return "done", "reported by the Bot"
            if answer == "incomplete":
                return "incomplete", "the Bot reported INCOMPLETE: its files are gone, but it did not leave the team"
            if answer is None or answer == "skip":
                return "skipped", "the Bot did not finish"
            if answer == "prompt":
                show()

    def cloud_member_check(self):
        inv = self.inv
        label = "cloud team membership"
        if self.residual:
            done = self.cloud_status == "done"
            note = ("reported by the Bot; this Mac has no team identity to check it" if done else
                    f"the Bot reported INCOMPLETE; a member still in the team must run reason-network remove "
                    f"{self.team}/{inv.cloud_machine}" if self.cloud_status == "incomplete"
                    else "the Bot did not finish")
            self.result(label, "done" if done else "skipped", note, True)
            return
        roster = inv.query_roster()
        if roster.status != "in":
            self.result(label, "skipped", "the team list is unknown: " + roster.message, True)
            return
        cloud, own, others = inv.classify(roster)
        if not cloud:
            self.result(label, "done", "the cloud is no longer in the team", True)
            return
        if inv.same_name:
            self.result(label, "skipped", f"this Mac is also named {inv.cloud_machine}, so it cannot remove the "
                                          "cloud; only the cloud itself can leave", True)
            return
        rest = own + others
        if rest and not any(Roster.online(row) for row in rest):
            self.result(label, "skipped", "the other members are all offline, so a removal could not be passed "
                                          "on", True)
            return
        target = f"{roster.me[0]}/{inv.cloud_machine}"
        print(f"The cloud {target} is still in the team ({len(cloud)} row(s): "
              f"{', '.join(row[2] for row in cloud)}). This Mac can remove it: the team link changes and that "
              "cloud's engine is cut off.")
        if len(cloud) > 1 and self.ask(f"Are all {len(cloud)} rows your own cloud? Type y: ") != "y":
            self.result(label, "skipped", "not confirmed", True)
            return
        if self.ask(f"Remove {target} from the team now? Type y: ") != "y":
            self.result(label, "skipped", "not confirmed", True)
            return
        code, out = inv.rn("remove", target)
        if code != 0:
            self.result(label, "skipped", "remove failed: " + short(out), True)
            return
        after = inv.query_roster()
        left = inv.classify(after)[0] if after.status == "in" else None
        if left == []:
            self.result(label, "done", f"removed with reason-network remove {target}", True)
        else:
            self.result(label, "skipped", "the cloud still shows in the team after remove", True)

    def dsh_step(self):
        inv = self.inv
        if "dsh" in inv.skips:
            raise Stop("dsh: " + inv.skips["dsh"] + "; Reason Network was left in place")
        if not (inv.dsh_block or inv.dsh_plugin):
            self.result("dsh bridge", "absent")
            return
        code, out = run(["/bin/bash", str(inv.here / "dsh/mac-uninstall.sh"), "--no-restore"], timeout=120)
        for line in out.splitlines():
            print("    " + line)
        if code != 0:
            self.result("dsh bridge", "failed", "dsh/mac-uninstall.sh --no-restore failed")
            raise Stop(f"the dsh bridge was not removed, so Reason Network was left in place; fix {self.p.patch} "
                       f"and run ./uninstall.sh --level {self.level} again")
        self.result("dsh bridge", "done")

    def leave_step(self):
        inv = self.inv
        label = "this Mac's team membership"
        roster = inv.query_roster() if inv.roster.status != "none" else inv.roster
        if roster.status == "left":
            self.result(label, "absent", "already left", True)
            return
        if roster.status == "none":
            self.result(label, "absent", roster.message, True)
            return
        if roster.status != "in":
            self.result(label, "skipped", "the team list is unknown: " + roster.message, True)
            return
        code, out = inv.rn("leave")
        if code == 0:
            self.result(label, "done", credential=True)
            return
        rest = [row for row in roster.rows]
        if rest and not any(Roster.online(row) for row in rest):
            print("leave was refused: the other members are all offline, so none of them can hear that this Mac "
                  "left. They need to remove it on their side (reason-network remove " f"{roster.me[0]}/"
                  f"{roster.me[1]}).")
            if self.ask("Type force to leave anyway: ") == "force":
                code, out = inv.rn("leave", "--force")
                if code == 0:
                    self.result(label, "done", "with --force; nobody confirmed hearing it", True)
                    return
        self.result(label, "failed", "leave: " + short(out), True)
        raise Stop("this Mac did not leave the team, so its identity was kept; run this level again when the "
                   "other members are online")

    def setup_step(self):
        inv = self.inv
        if not inv.engine:
            self.result("agent wiring and engine service", "absent", "no engine")
            return True
        code, out = inv.rn("setup", "--remove")
        for line in out.splitlines():
            print("    " + line)
        if code == 0:
            self.result("agent wiring and engine service", "done")
            return True
        self.result("agent wiring and engine service", "failed",
                    f"run {self.p.rn_bin} setup --remove by hand; the engine is kept for it")
        return False

    def engine_stop(self):
        inv = self.inv
        if inv.engine and self.p.machine_key.is_file():
            code, out = inv.rn("stop", timeout=60)
            if code != 0:
                print("    reason-network stop said: " + short(out))

    def routine_step(self):
        print("\n[GROK-UI] User: delete the routine in Grok Bot. Pausing is not enough: a paused routine keeps its "
              "webhook URL and key valid.")
        answer = self.ask("Type deleted when it is gone, or later: ")
        if answer == "deleted":
            self.result("Grok routine", "user-confirmed", "deleted, as you said", True)
        else:
            self.result("Grok routine", "user-pending", "its webhook key stays valid until you delete it", True)

    def manual_items(self):
        print("\nLeft for you to handle (nothing below is changed):")
        print(f"  this code folder: {self.inv.here}")
        for where in self.inv.rc_lines:
            print(f"  PATH line with ~/.local/bin: {where}")
        print("  reply convention: look for 'Reason Network reply convention' in the AGENTS.md / CLAUDE.md of your "
              "repositories")
        print("  an external inbox folder on [BOT-CLOUD] (RN_INBOX_DIR), if you set one")
        print("  anything the Bot keeps in its long-term memory about this setup")

    def run_program(self):
        if not self.confirm_queue(self.inv.queue_counts):
            raise Stop("stopped; nothing was changed")
        before = self.inv.queue_counts
        self.stop_relay()
        after = self.inv.queue()
        if self.grew(before, after) and not self.confirm_queue(after, again=True):
            raise Stop("stopped after the relay was stopped; run ./install.sh --restart-relay to start it again")
        self.remove_paths("relay program, temporary files, rollback/", self.program_paths(), skip_key="program")
        print("\n[GROK-UI] User: pause the routine in Grok Bot.")
        print("To reinstall later: git pull, then ./install.sh --restart-relay in this folder, then resume the "
              "routine. Do not run the full install again.")

    @staticmethod
    def grew(before, after):
        if not isinstance(after, dict):
            return False
        before = before if isinstance(before, dict) else {}
        return any(after[k] > before.get(k, 0) for k in after)

    def run_deep(self):
        inv, p = self.inv, self.p
        before = inv.queue_counts
        self.stop_relay()
        after = inv.queue()
        if self.grew(before, after):
            print("The queue grew while the relay stopped.")
            if not self.confirm_queue(after, again=True):
                raise Stop("stopped after the relay was stopped; nothing else was changed")
        status, note = self.cloud_step()
        self.cloud_status = status
        removed = status in ("done", "incomplete")
        self.result("cloud identity files", "done" if removed else "skipped", note, True)
        if self.level == "all":
            self.result("cloud inbox, threads and clone", "done" if removed else "skipped", note)
        self.cloud_member_check()
        self.dsh_step()
        setup_ok = True
        if not self.residual:
            self.leave_step()
            setup_ok = self.setup_step()
        self.engine_stop()
        self.remove_paths("relay program, temporary files, rollback/", self.program_paths(), skip_key="program")
        self.remove_paths("deployment record and update lock", [p.lib], skip_key="program")
        self.remove_paths("relay settings and signing key", [p.conf], credential=True)
        if inv.custom_key:
            self.result("signing key outside ~/.config/agent-relay", "skipped",
                        f"{inv.custom_key} is never removed automatically; delete it yourself", True)
        self.remove_paths("relay state (queue, replay protection, kept copies)", [p.state], credential=True,
                          skip_key="state")
        self.remove_paths("Reason Network identity and history", [p.rn_home], credential=True)
        if self.level == "all":
            self.remove_paths("relay logs", p.log_files(), skip_key="logs")
            if setup_ok:
                self.remove_paths("Reason Network engine", [p.rn_bin], skip_key="rn-bin")
            else:
                self.result("Reason Network engine", "skipped", "kept for setup --remove")
            self.remove_paths("dsh bridge backups and log", [p.dsh_backup] + p.dsh_log_files(), skip_key="dsh")
            self.manual_items()
        self.routine_step()

    def summary(self, final=True):
        print(f"\n== result (level {self.level})")
        for label, state, note, _ in self.results:
            print(f"  {label:<52} {state}" + (f" ({note})" if note else ""))
        if not final:
            print(f"Stopped before the end; run ./uninstall.sh --level {self.level} again to go on.")
            return 1
        if not self.deep():
            bad = [r for r in self.results if r[1] in ("failed", "skipped")]
            print("level program complete" if not bad else "level program not complete; see the lines above")
            return 0 if not bad else 1
        open_items = [r for r in self.results if r[3] and r[1] not in ("done", "absent", "user-confirmed")]
        others = [r for r in self.results if not r[3] and r[1] in ("failed", "skipped")]
        if open_items:
            print(f"This Mac is cleaned up, but level {self.level} is not complete yet. Still open:")
            for label, state, note, _ in open_items:
                print(f"  - {label}: {state}" + (f" ({note})" if note else ""))
            print(f"Run ./uninstall.sh --cloud-prompt --level {self.level} for the cloud step again, or delete the "
                  "routine in Grok Bot, as listed.")
        else:
            print(f"level {self.level} complete")
        if others:
            print("Not done, but not needed for completion: " + ", ".join(r[0] for r in others))
        print("Reopen your agent sessions.")
        return 1 if open_items else 0


def residual_team(uninstaller):
    answer = uninstaller.ask("[LOCAL] This Mac has no team identity left. Type the team name you installed with: ")
    if answer is None or not valid_name(answer):
        raise Stop("not a valid team name (lowercase letters and digits joined by single '-', at most 40)")
    return answer


def choose_level(ask):
    print("\nChoose a level:\n  1  program   remove the relay; keep the team, settings, key and the cloud\n"
          "  2  identity  also leave the team on both computers, remove keys and identity\n"
          "  3  all       also remove logs, the engine and the cloud's data\n  q  quit")
    return {"1": "program", "2": "identity", "3": "all"}.get(ask("Level [1/2/3/q]: ") or "")


def parse(argv):
    parser = argparse.ArgumentParser(prog="uninstall.sh", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--level", choices=LEVELS)
    parser.add_argument("--all", action="store_true", help="same as --level all")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--residual", action="store_true")
    parser.add_argument("--cloud-prompt", action="store_true")
    parser.add_argument("--yes", "-y", action="store_true")
    parser.add_argument("--lang", choices=("en", "zh-CN"))
    args = parser.parse_args(argv)
    if args.all:
        if args.level not in (None, "all"):
            parser.error("--all is the same as --level all")
        args.level = "all"
    if args.yes and args.level != "program":
        parser.error("--yes works only with --level program; levels identity and all need your own terminal")
    if (args.residual or args.cloud_prompt) and args.level not in DEEP:
        parser.error("--residual and --cloud-prompt need --level identity or --level all")
    return args


def main(argv=None):
    args = parse(sys.argv[1:] if argv is None else argv)
    here = ROOT
    paths = Paths(os.environ["HOME"])
    interactive = sys.stdin.isatty()
    if not args.plan and args.level and (args.level in DEEP or not args.yes) and not interactive:
        inv = Inventory(paths, here)
        inv.show()
        Uninstaller(inv, args.level, "en", residual=args.residual).show_plan()
        print(f"\nThis is not a terminal, so nothing was removed. Level {args.level} asks for your confirmation: "
              f"run ./uninstall.sh --level {args.level}{' --residual' if args.residual else ''} in your own terminal"
              + (" (or add --yes)." if args.level == "program" else "."))
        return 3
    inv = Inventory(paths, here)
    lang = args.lang or os.environ.get("AGENT_MESH_LANG") or inv.record_lang or "en"
    if lang not in ("en", "zh-CN"):
        lang = "en"
    inv.show()
    if args.plan:
        ok = True
        for level in ([args.level] if args.level else LEVELS):
            ok = Uninstaller(inv, level, lang, residual=args.residual).show_plan() and ok
        print("\nNothing was removed (--plan).")
        return 0
    level = args.level
    if level is None:
        if not interactive:
            print("\nNothing was removed. Choose a level: ./uninstall.sh --plan shows what each level does.")
            return 0
        u = Uninstaller(inv, "program", lang, interactive=True)
        level = choose_level(u.ask)
        if level is None:
            print("Nothing was removed.")
            return 0
    u = Uninstaller(inv, level, lang, residual=args.residual, yes=args.yes, interactive=interactive)
    try:
        return execute(u, args)
    except Stop as stop:
        print(f"\nSTOP: {stop}")
        if u.results:
            u.summary(final=False)
        return 1


def execute(u, args):
    inv = u.inv
    if args.residual:
        if inv.identity:
            raise Stop("this Mac still has a Reason Network identity; run without --residual")
    elif u.deep() and inv.roster.status == "none" and not inv.identity:
        raise Stop("this Mac has no Reason Network identity, so the team name cannot be checked here; "
                   f"run ./uninstall.sh --residual --level {u.level}")
    elif u.deep() and not inv.team:
        raise Stop("the team name could not be found on this Mac; nothing was removed")
    if args.cloud_prompt:
        stops = inv.stops_for(u.level)
        for reason in stops:
            print(f"  STOP: {reason}")
        if stops:
            raise Stop("nothing was done; resolve the STOP lines above first")
        if inv.foreign and not u.confirm_foreign():
            raise Stop("stopped; nothing was changed")
        if args.residual:
            u.team = residual_team(u)
        status, note = u.cloud_step()
        u.cloud_status = status
        u.result("cloud identity files", "done" if status in ("done", "incomplete") else "skipped", note, True)
        u.cloud_member_check()
        print(f"\n== result (cloud step only, level {u.level})")
        for label, state, note, _ in u.results:
            print(f"  {label:<52} {state}" + (f" ({note})" if note else ""))
        still = [r for r in u.results if r[3] and r[1] not in ("done", "absent", "user-confirmed")]
        print("Only the cloud step ran; nothing on this Mac was removed. "
              + ("The cloud step is not complete." if still else "The cloud step is complete."))
        return 1 if still else 0
    ok = u.show_plan()
    if not ok:
        raise Stop("nothing was removed; resolve the STOP lines above first")
    if args.residual:
        u.team = residual_team(u)
    if u.deep() and inv.foreign and not u.confirm_foreign():
        raise Stop("stopped; nothing was changed")
    if not u.confirm_main():
        raise Stop("not confirmed; nothing was changed")
    if u.deep() and not u.confirm_queue(inv.queue_counts):
        raise Stop("stopped; nothing was changed")
    print()
    if u.deep():
        u.run_deep()
    else:
        u.run_program()
    return u.summary()


if __name__ == "__main__":
    sys.exit(main())
