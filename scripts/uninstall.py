#!/usr/bin/env python3
"""Tiered uninstaller for the Mac side of agent-mesh; uninstall.sh runs it.

  uninstall.py                          what is installed; in a terminal, then offers the levels
  uninstall.py --plan [--level L]       what is installed and what level L would do; never asks, never removes
  uninstall.py --level program [--yes]  remove the relay program of every Bot, keep identity, settings and key
  uninstall.py --level bot --relay-name NAME
                                        remove one Bot (its relay, settings, state, logs, record entry and cloud
                                        data); the other Bots, the team and the shared key stay
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

import bots
import mesh_lock
import relay_names
from messages import pad, t
from render_prompt import ROOT, render_prompt

LABEL = bots.LEGACY_LABEL
LOCK_FD = 9
LEVELS = ("program", "bot", "identity", "all")
PLAN_LEVELS = ("program", "identity", "all")
DEEP = ("identity", "all")
BOT_STEPS = ("started", "stopped", "routine", "cloud")
ADDED_PLIST = re.compile(r"com\.local\.agent-relay\.([a-z0-9]+(?:-[a-z0-9]+)*)\.plist")
KNOWN_RELAYS = {"02b6a5097a93e66e": "uninstall.known_v011", "1467396a0de8baa4": "uninstall.known_v041",
                "b034a2293d05274b": "uninstall.known_untagged"}
TEMP6 = r"[A-Za-z0-9]{6}"
RC_FILES = (".zshrc", ".zprofile", ".zshenv", ".bash_profile", ".bashrc", ".profile", ".config/fish/config.fish")
RULE = "-" * 64
LANGS = ("en", "zh-CN")
TEAM_SOURCES = {"team list": "uninstall.source_team_list", "this machine's person": "uninstall.source_person",
                "config.toml": "uninstall.source_config", "relay env": "uninstall.source_env"}


class Stop(Exception):
    pass


def valid_name(name):
    return isinstance(name, str) and bool(relay_names.NAME.fullmatch(name)) and len(name) <= 40


def state_text(state):
    return t("uninstall.state_" + state.replace("-", "_"))


def with_note(text, note):
    return t("uninstall.with_note", text, note) if note else text


def source_text(source):
    return t(TEAM_SOURCES[source])


class BotPaths:
    def __init__(self, home, name, kind):
        d = bots.paths(home, name, kind)
        self.name, self.kind, self.label = name, kind, d["label"]
        self.conf, self.env, self.plist = Path(d["conf_dir"]), Path(d["env"]), Path(d["plist"])
        self.state, self.marker = Path(d["state_dir"]), Path(d["marker"])
        self.log, self.launchd_log = Path(d["log"]), Path(d["launchd_log"])

    def log_files(self, logs):
        stem = "agent-relay" if self.kind == bots.LEGACY else "agent-relay-" + re.escape(self.name)
        return Paths.matching(logs, rf"{stem}\.log(\.[0-9]+)?|{stem}\.launchd\.log(\.[0-9]+)?")

    def temp_plists(self):
        if self.kind == bots.LEGACY:
            return []
        return Paths.matching(self.plist.parent, rf"\.{re.escape(self.label)}\.{TEMP6}")


class Paths:
    def __init__(self, home):
        h = Path(home)
        self.home = h
        self.agents = h / "Library/LaunchAgents"
        self.legacy = BotPaths(h, None, bots.LEGACY)
        self.plist = self.legacy.plist
        self.lib = h / ".local/lib/agent-mesh"
        self.relay = self.lib / "agent_relay.py"
        self.record = self.lib / "deployed.json"
        self.lock = self.lib / "update.lock"
        self.mesh_lock = h / ".local/lib/agent-mesh.lock"
        self.rollback = self.lib / "rollback"
        self.rollback_tmp = self.lib / "rollback.tmp"
        self.conf = h / ".config/agent-relay"
        self.env = self.legacy.env
        self.key = self.conf / "outbound.key"
        self.state = self.legacy.state
        self.bots_conf = bots.added_root(h)
        self.bots_state = h / "Library/Application Support/agent-relay-bots"
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
            return t("uninstall.outside_home", path)
        cur = self.home
        for part in rel.parts:
            cur = cur / part
            try:
                st = os.lstat(cur)
            except FileNotFoundError:
                return None
            except OSError as error:
                return t("uninstall.cannot_check", cur, error.strerror or error)
            if stat.S_ISLNK(st.st_mode):
                return t("uninstall.is_symlink", cur)
            if st.st_uid != os.getuid():
                return t("uninstall.other_owner", cur)
        real_home = os.path.realpath(self.home)
        real = os.path.realpath(path)
        if real != real_home and not real.startswith(real_home + os.sep):
            return t("uninstall.resolves_outside", path)
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
        stem = r"agent-relay(-[a-z0-9]+(-[a-z0-9]+)*)?"
        return self.matching(self.logs, rf"{stem}\.log(\.[0-9]+)?|{stem}\.launchd\.log(\.[0-9]+)?")

    def env_temps(self):
        return self.matching(self.conf, rf"\.env\.{TEMP6}")

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
        return 127, t("uninstall.cmd_not_found", cmd[0])
    except subprocess.TimeoutExpired:
        return 124, t("uninstall.cmd_timeout", cmd[0], timeout)
    except OSError as error:
        return 126, f"{cmd[0]}: {error}"
    return r.returncode, r.stdout.decode("utf-8", errors="replace")


def run_locked(cmd, timeout=120):
    env = dict(os.environ, AGENT_MESH_LOCK_FD=str(LOCK_FD))
    try:
        r = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
                           pass_fds=(LOCK_FD,))
    except (OSError, subprocess.TimeoutExpired) as error:
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
            return cls("unknown", message=t("uninstall.unexpected_output", short(out)))
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
            return cls("unknown", message=t("uninstall.no_this_machine"))
        return cls("in", team=head[1], me=me, rows=rows)

    @staticmethod
    def online(row):
        return not row[2].startswith("offline")


class Member:
    def __init__(self, paths, stage="relay", cloud="-", removing=False, record="ok", grok=None, orphan=False):
        self.p = paths
        self.kind = paths.kind
        self.name = paths.name
        self.stage, self.cloud, self.removing, self.record, self.grok = stage, cloud, removing, record, grok
        self.orphan = orphan
        self.listed = True
        self.plist_present, self.loaded, self.running = False, False, False
        self.loaded_path, self.launchd_note, self.skip = None, "", None

    def label(self, team):
        name = self.name or relay_names.DEFAULT
        address = f"{team}/{name}" if team else name
        return f"{address} → Grok: {self.grok}" if self.grok else address

    def confirmed(self):
        return self.kind == bots.LEGACY or self.cloud == "confirmed"


class Inventory:
    def __init__(self, paths, here, holds_lock=False):
        self.p = paths
        self.here = Path(here)
        self.holds_lock = holds_lock
        self.stops = []
        self.notes = []
        self.skips = {}
        self.foreign = []
        self.collect()

    def stop(self, scope, reason):
        self.stops.append((scope, reason))

    def stops_for(self, level, target=None):
        def applies(scope):
            if isinstance(scope, tuple):
                return level != "bot" or scope[1] == target
            return scope == "all" or (scope == "deep" and level in DEEP)
        return [reason for scope, reason in self.stops if applies(scope)]

    def bots(self):
        return [m for m in self.members if m.listed and not m.orphan]

    def member(self, name):
        return next((m for m in self.bots() if m.name == name), None)

    def label_of(self, m):
        return m.label(self.team)

    def rn_env(self):
        env = dict(os.environ)
        env.update(REASON_NETWORK_HOME=str(self.p.rn_home), REASON_NETWORK_NO_PROMPT="1")
        return env

    def rn(self, *args, timeout=120):
        if not self.critical_ok or "rn-bin" in self.skips:
            return 1, t("uninstall.rn_not_run")
        return run([str(self.p.rn_bin), *args], env=self.rn_env(), timeout=timeout)

    def collect(self):
        p = self.p
        self.critical_ok = True
        for path in (p.rn_home, p.conf):
            why = p.unsafe(path)
            if why:
                self.critical_ok = False
                self.stop("deep", t("uninstall.rn_unsafe", why))
        for key, path in (("service", p.plist), ("program", p.lib), ("state", p.state), ("rn-bin", p.rn_bin),
                          ("logs", p.logs), ("dsh", p.dsh)):
            why = p.unsafe(path)
            if why:
                self.skips[key] = why
        self.read_members()
        for m in self.members:
            self.read_plist(m)
            self.read_launchd(m)
        legacy = self.members[0]
        self.plist_present, self.loaded, self.running = legacy.plist_present, legacy.loaded, legacy.running
        self.loaded_path, self.launchd_note = legacy.loaded_path, legacy.launchd_note
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

    def read_members(self):
        p = self.p
        self.data, self.record_bad = {}, None
        try:
            self.data = bots.load_record(p.home)
        except ValueError as error:
            self.record_bad = str(error)
        data = self.data
        self.removing = bots.removing_of(data)
        self.legacy_removed = data.get("legacy_removed") is True
        legacy = Member(p.legacy, grok=bots.grok_of(data))
        try:
            legacy.name = bots.legacy_name(p.home, data)
            legacy.listed = legacy.name is not None
        except ValueError:
            legacy.listed = True
        legacy.removing = bool(self.removing and self.removing["kind"] == bots.LEGACY
                               and self.removing["name"] == legacy.name)
        self.members = [legacy]
        why = p.unsafe(p.bots_conf) or p.unsafe(p.bots_state)
        if why:
            self.bots_unsafe = why
            self.stop("all", t("uninstall.bots_unsafe", why))
            self.multi = True
            return
        self.bots_unsafe = None
        for entry in bots.added_entries(p.home, data, self.removing):
            self.members.append(Member(BotPaths(p.home, entry["name"], bots.ADDED), entry["stage"], entry["cloud"],
                                       entry["removing"], entry["record"], entry["grok_name"]))
        known = {m.p.plist.name for m in self.members}
        try:
            names = sorted(os.listdir(p.agents))
        except OSError:
            names = []
        for fname in names:
            match = ADDED_PLIST.fullmatch(fname)
            if not match or fname in known or not bots.valid_name(match.group(1)):
                continue
            bp = BotPaths(p.home, match.group(1), bots.ADDED)
            if self.plist_problem(bp) is None:
                self.members.append(Member(bp, record="missing", orphan=True))
        self.multi = len(self.members) > 1 or self.legacy_removed

    def plist_problem(self, bp):
        p = self.p
        try:
            with open(bp.plist, "rb") as stream:
                data = plistlib.load(stream)
        except Exception as error:
            return t("uninstall.plist_unreadable", type(error).__name__)
        args = data.get("ProgramArguments") if isinstance(data, dict) else None
        env = data.get("EnvironmentVariables", {}) if isinstance(data, dict) else None
        if not isinstance(data, dict) or data.get("Label") != bp.label:
            return t("uninstall.plist_label")
        if not isinstance(args, list) or len(args) < 2 or args[-1] != str(p.relay):
            return t("uninstall.plist_program")
        if not isinstance(env, dict):
            return t("uninstall.plist_env")
        overrides = {str(k): v for k, v in env.items() if str(k).startswith("AGENT_RELAY_")}
        expected = {} if bp.kind == bots.LEGACY else {"AGENT_RELAY_ENV": str(bp.env),
                                                       "AGENT_RELAY_STATE": str(bp.state),
                                                       "AGENT_RELAY_LOG": str(bp.log)}
        if overrides != expected:
            return t("uninstall.plist_overrides")
        if env.get("REASON_NETWORK_HOME", str(p.rn_home)) != str(p.rn_home):
            return t("uninstall.plist_rn_home")
        return None

    def read_plist(self, m):
        m.plist_present = m.p.plist.exists() or m.p.plist.is_symlink()
        if not m.plist_present or "service" in self.skips or m.orphan:
            return
        problem = self.plist_problem(m.p)
        if problem:
            self.stop(("member", m.name), t("uninstall.plist_foreign", m.p.plist, problem))

    def read_launchd(self, m):
        m.loaded, m.running = False, False
        m.launchd_note, m.loaded_path = "", None
        code, out = run(["launchctl", "print", f"gui/{os.getuid()}/{m.p.label}"], timeout=30)
        if code == 0:
            m.loaded = True
            m.running = "state = running" in out
            m.loaded_path = loaded_plist(out)
            if not self.service_owned(out, m):
                self.stop(("member", m.name), t("uninstall.service_foreign", m.p.label,
                                                m.loaded_path or t("uninstall.unknown_plist"), m.p.plist))
        elif code != 113 and "Could not find service" not in out and "could not find service" not in out:
            m.launchd_note = short(out, 120)
            m.loaded = None
            self.stop(("member", m.name), t("uninstall.launchd_unknown", m.p.label, m.launchd_note))

    def service_owned(self, out, m=None):
        m = m or self.members[0]
        path = loaded_plist(out)
        if path:
            return os.path.realpath(path) == os.path.realpath(m.p.plist)
        if m.kind != bots.LEGACY:
            return False
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
            self.stop("deep", t("uninstall.agents_unlisted", p.agents, error.strerror or error))
        ours = {m.p.plist.name for m in self.members}
        for name in names:
            if not name.endswith(".plist") or name in ours:
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
                self.foreign.append((label, t("uninstall.foreign_service", p.agents / name)))
        code, out = run(["ps", "-axo", "pid=,command="], timeout=30)
        if code != 0:
            self.stop("deep", t("uninstall.ps_failed", short(out, 120)))
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
            self.foreign.append((f"pid {parts[0]}", t("uninstall.foreign_process", short(program, 40),
                                                      short(script, 60))))

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
                self.stop("deep", t("uninstall.cannot_read", p.env, error.strerror or error))
            try:
                self.relay_name = relay_names.from_env(p.env)
                self.name_default = not any(k in self.cfg for k in relay_names.KEYS)
            except ValueError as error:
                self.stop("deep", t("uninstall.name_unclear", error))
        for m in self.members:
            m.cfg = self.cfg if m.kind == bots.LEGACY else {}
            if m.kind != bots.LEGACY and self.critical_ok:
                try:
                    m.cfg = relay_names.parse_env(m.p.env.read_text(encoding="utf-8", errors="replace"))
                except OSError:
                    m.cfg = {}
            m.webhook = (m.cfg.get("WEBHOOK_URL", "").startswith("https://")
                         and len(m.cfg.get("WEBHOOK_KEY", "")) >= 8)
        if not self.env_present:
            first = next((m for m in self.members[1:] if m.cfg and not m.orphan), None)
            if first is not None:
                self.cfg = first.cfg
        self.webhook = (self.cfg.get("WEBHOOK_URL", "").startswith("https://")
                        and len(self.cfg.get("WEBHOOK_KEY", "")) >= 8)
        self.cloud_machine = self.cfg.get("CLOUD_MACHINE") or relay_names.CLOUD_MACHINE
        if not valid_name(self.cloud_machine):
            self.stop("deep", t("uninstall.cloud_machine_invalid"))
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
            self.notes.append(t("uninstall.other_rn_bin", p.rn_bin))

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
            self.notes.append(t("uninstall.record_unreadable", p.record))
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
                self.stop("deep", t("uninstall.name_mismatch", self.relay_name, recorded))

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
            self.roster = Roster("none", message=t("uninstall.no_identity"))
            return
        if not (self.critical_ok and self.engine):
            self.roster = Roster("unknown", message=t("uninstall.engine_unusable"))
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
            listed = t("uninstall.list_separator").join(f"{source_text(k)} {v}" for k, v in sorted(values.items()))
            self.stop("deep", t("uninstall.team_differs", listed))
        elif values:
            self.team = next(iter(values.values()))
            if not valid_name(self.team):
                self.stop("deep", t("uninstall.team_invalid", repr(self.team[:60])))
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

    def queue(self, m=None):
        path = (m.p.state if m is not None else self.p.state) / "state.json"
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
        for m in self.members:
            m.queue = self.queue(m)
        self.queue_counts = self.merged(self.members)

    def snapshot(self, members=None):
        return {id(m): self.queue(m) for m in (self.members if members is None else members)}

    def merged(self, members, snap=None):
        counts = [snap[id(m)] if snap is not None else m.queue for m in members]
        if not self.multi:
            return counts[0]
        found = [q for q in counts if isinstance(q, dict)]
        if not found:
            return "unreadable" if "unreadable" in counts else None
        return {k: sum(q[k] for q in found) for k in found[0]}

    def read_lock(self):
        self.read_mesh_lock()
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
                self.stop("all", t("uninstall.update_running", pid, lock))
                return
        self.lock_state = "stale"
        self.notes.append(t("uninstall.stale_lock", lock))

    def read_mesh_lock(self):
        if self.holds_lock:
            return
        try:
            fd = os.open(self.p.mesh_lock, os.O_RDONLY)
        except OSError:
            return
        try:
            if not mesh_lock.try_lock(fd):
                self.stop("all", t("lock.busy", mesh_lock.holder(self.p.mesh_lock), self.p.mesh_lock))
        except OSError:
            pass
        finally:
            os.close(fd)

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
                    why = t("uninstall.not_regular", path)
                if why:
                    self.stop("deep", t("uninstall.dsh_unsafe", why))

    def read_version(self):
        p = self.p
        self.version = t("uninstall.no_relay_program")
        if p.relay.is_file() and "program" not in self.skips:
            data = p.relay.read_bytes()
            digest = hashlib.sha256(data).hexdigest()[:16]
            mine = (self.here / "agent_relay.py")
            if mine.is_file() and mine.read_bytes() == data:
                known = t("uninstall.same_as_checkout")
            else:
                known = t(KNOWN_RELAYS.get(digest, "uninstall.unknown_layout"))
            self.version = t("uninstall.relay_sha", digest, known)
        if self.record_present:
            self.version = t("uninstall.with_record", self.version)
        if (p.lib / "engine-upgrade.json").exists():
            self.notes.append(t("uninstall.engine_upgrade_unfinished"))
        if p.rollback.exists() or p.rollback_tmp.exists():
            self.notes.append(t("uninstall.rollback_left"))

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

    @staticmethod
    def service_text(m):
        if m.loaded is None:
            return t("uninstall.service_unknown", m.launchd_note)
        if m.loaded:
            return t("uninstall.service_running" if m.running else "uninstall.service_stopped")
        return t("uninstall.service_unloaded")

    @staticmethod
    def queue_text(q):
        if isinstance(q, dict):
            return ", ".join(f"{k} {v}" for k, v in q.items())
        return t("uninstall.none") if q is None else t("uninstall.unreadable")

    def show_bots(self, line, key):
        listed = self.bots()
        line("bots", t("uninstall.bots_count", len(listed), key, self.cloud_machine))
        if self.bots_unsafe:
            line("note", t("uninstall.bots_unsafe", self.bots_unsafe))
        for m in self.members:
            if not (m.listed or m.plist_present or m.loaded):
                continue
            if m.orphan:
                kind = t("uninstall.bot_orphan")
            elif m.kind == bots.LEGACY:
                kind = t("uninstall.kind_legacy")
            else:
                kind = t("uninstall.kind_added", m.stage, m.cloud)
            if m.removing:
                kind = t("uninstall.with_note", kind, t("uninstall.bot_removing", self.removing.get("step", "-")))
            if m.record == "missing" and not m.orphan:
                kind = t("uninstall.with_note", kind, t("uninstall.record_missing"))
            webhook = t("uninstall.webhook_set" if getattr(m, "webhook", False) else "uninstall.webhook_unset")
            print("    " + t("uninstall.bot_line", self.label_of(m), kind, self.service_text(m), webhook,
                              self.queue_text(m.queue)))
        if self.record_bad:
            line("note", self.record_bad)

    def names_line(self):
        name = self.relay_name or relay_names.DEFAULT
        default = self.relay_name is None or self.name_default
        cloud, inbox = relay_names.derived(name)[1:]
        return t("uninstall.names_default" if default else "uninstall.names", name, cloud, inbox, self.cloud_machine)

    def show(self):
        r = self.roster
        print(t("uninstall.inventory_title"))
        line = lambda k, v: print(f"  {pad(t('uninstall.field_' + k), 14)}{v}")
        none = t("uninstall.none")
        sep = t("uninstall.list_separator")
        line("version", self.version)
        if self.team:
            line("team_person", t("uninstall.team_from", self.team, sep.join(map(source_text, self.team_sources))))
        else:
            line("team_person", t("uninstall.unknown"))
        if r.status == "in":
            line("this_machine", f"{r.me[0]}/{r.me[1]}")
            if self.cloud_rows:
                states = sep.join(row[2] for row in self.cloud_rows)
                line("cloud", t("uninstall.cloud_members", f"{r.me[0]}/{self.cloud_machine}", len(self.cloud_rows),
                                states))
            else:
                line("cloud", t("uninstall.cloud_absent"))
        elif r.status == "left":
            line("team", t("uninstall.team_left"))
        elif r.status == "unknown":
            line("team", t("uninstall.team_unlisted", r.message))
        else:
            line("team", r.message)
        key = t("uninstall.key_present" if self.key_present() else "uninstall.key_absent")
        if self.multi:
            self.show_bots(line, key)
        else:
            line("relay_names", self.names_line())
            webhook = t("uninstall.webhook_set" if self.webhook else "uninstall.webhook_unset")
            line("relay", t("uninstall.relay_status", self.service_text(self.members[0]), webhook, key))
            line("queue", self.queue_text(self.queue_counts))
        line("dsh_bridge", t("uninstall.present") if (self.dsh_block or self.dsh_plugin) else none)
        members = [f"{row[0]}/{row[1]} ({row[2]})" for row in self.own_other + self.others]
        line("other_members", sep.join(members) or none)
        line("foreign", "; ".join(f"{fid}: {what}" for fid, what in self.foreign) or none)
        if self.custom_key:
            line("custom_key", t("uninstall.custom_key", self.custom_key))
        for note in self.notes:
            line("note", note)
        for key, why in sorted(self.skips.items()):
            line("skipped", f"{key}: {why}")


class Uninstaller:
    def __init__(self, inv, level, lang, residual=False, yes=False, interactive=False, name=None):
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
        self.name = name
        self.target, self.executor = None, None
        self.finished = True

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
        print("  " + t("uninstall.result_line", label, with_note(state_text(state), note)))

    def deep(self):
        return self.level in DEEP

    def plan_rows(self):
        if self.level == "bot":
            return self.bot_plan_rows()
        inv, p, deep, everything = self.inv, self.p, self.deep(), self.level == "all"
        keep, remove = "keep", "remove"
        have = lambda present: "present" if present else "absent"
        multi = "_multi" if inv.multi else ""
        rows = [
            ("service" + multi, have(any(m.plist_present or m.loaded for m in inv.members)), "stop_remove"),
            ("program", have(p.relay.exists() or p.temp_files() or p.rollback.exists() or p.rollback_tmp.exists()),
             remove),
            ("record", have(p.lib.exists()), remove if deep else keep),
            ("settings" + multi, have(p.conf.exists() or p.bots_conf.exists()), remove if deep else keep),
            ("state" + multi, have(p.state.exists() or p.bots_state.exists()), remove if deep else keep),
            ("logs", have(p.log_files()), remove if everything else keep),
            ("dsh", have(inv.dsh_block or inv.dsh_plugin), "remove_dsh" if deep else keep),
            ("dsh_backups", have(inv.dsh_backup or inv.dsh_logs), remove if everything else keep),
            ("cloud_identity", "bot", "bot_leaves" if deep else keep),
            ("cloud_data", "bot", "bot_removes" if everything else keep),
            ("team", "roster_" + inv.roster.status, "leave" if deep else keep),
            ("wiring", have(inv.engine), "setup_remove" if deep else keep),
            ("identity", have(p.rn_home.exists()), remove if deep else keep),
            ("engine", have(p.rn_bin.exists()), remove if everything else keep),
            ("routine" + multi, "grok_ui", "you_delete" if deep else "you_pause"),
        ]
        if everything:
            rows.append(("mesh_lock", have(p.mesh_lock.exists()), remove))
        if self.residual:
            rows = [r for r in rows if r[0] not in ("team", "wiring")]
        return [(t("uninstall.plan_" + what), t("uninstall.now_" + now), t("uninstall.action_" + action))
                for what, now, action in rows]

    def bot_plan_rows(self):
        m, p = self.target, self.p
        have = lambda present: "present" if present else "absent"
        if m is None:
            return []
        if m.kind == bots.LEGACY:
            settings, logs = [m.p.env] + p.env_temps(), m.p.log_files(p.logs)
        else:
            settings, logs = [m.p.conf], m.p.log_files(p.logs)
        rows = [
            ("bot_service", have(m.plist_present or m.loaded), "stop_remove"),
            ("bot_routine", "grok_ui", "you_delete"),
            ("bot_cloud", "bot", "bot_removes_bot"),
            ("bot_settings_" + m.kind, have(any(x.exists() for x in settings)), "remove"),
            ("bot_state", have(m.p.state.exists()), "remove"),
            ("bot_logs", have(logs), "remove"),
            ("bot_record", "present" if not self.inv.record_bad else "absent", "bot_record"),
            ("bot_shared", "present", "keep"),
        ]
        return [(t("uninstall.plan_" + what), t("uninstall.now_" + now), t("uninstall.action_" + action))
                for what, now, action in rows]

    def show_plan(self):
        if self.level == "bot":
            return self.show_bot_plan()
        title = t("uninstall.level_" + self.level)
        print("\n" + t("uninstall.plan_header_residual" if self.residual else "uninstall.plan_header", title))
        for what, now, action in self.plan_rows():
            print(f"  {pad(what, 58)} {pad(now, 9)} {action}")
        if self.level == "all":
            print("  " + t("uninstall.plan_listed_only"))
        if self.inv.multi and self.inv.bots():
            print("  " + t("uninstall.plan_bots", t("uninstall.list_separator").join(
                self.inv.label_of(m) for m in self.inv.bots())))
        stops = self.inv.stops_for(self.level)
        for reason in stops:
            print(f"  STOP: {reason}")
        if self.deep():
            for fid, what in self.inv.foreign:
                print("  " + t("uninstall.plan_foreign", fid, what))
        return not stops

    def bot_step(self):
        m = self.target
        if m is None or not m.removing:
            return None
        step = self.inv.removing.get("step")
        return step if step in BOT_STEPS else "started"

    def bot_checks(self):
        inv = self.inv
        stops = list(inv.stops_for("bot", self.name))
        if inv.record_bad:
            stops.append(t("uninstall.bot_record_bad", inv.record_bad))
        self.target = m = inv.member(self.name)
        if m is None:
            names = t("uninstall.list_separator").join(b.name or relay_names.DEFAULT for b in inv.bots())
            stops.append(t("uninstall.bot_unknown", self.name, names or t("uninstall.none")))
            return stops
        if inv.removing and not m.removing:
            stops.append(t("uninstall.bot_other_removing", inv.removing["name"],
                           f"./uninstall.sh --level bot --relay-name {inv.removing['name']}"))
        rest = [b for b in inv.bots() if b is not m and not b.removing]
        if not rest:
            stops.append(t("uninstall.bot_last", self.name))
        step = self.bot_step()
        self.executor = next((b for b in rest if b.confirmed() and b.stage == "relay"), None)
        if self.executor is None and rest and step != "cloud":
            stops.append(t("uninstall.bot_no_executor", self.name))
        if step in (None, "started"):
            stops += self.queue_stops(m.queue)
        if step is None:
            stops += self.unfinished_stops()
        if not inv.team:
            stops.append(t("uninstall.team_not_found"))
        return stops

    def unfinished_stops(self):
        p, data = self.p, self.inv.data
        stops = []
        if (p.lib / "engine-upgrade.json").exists():
            stops.append(t("uninstall.bot_engine_unfinished", p.lib / "engine-upgrade.json"))
        elif p.rollback.exists() or p.rollback_tmp.exists():
            stops.append(t("uninstall.bot_rollback_unfinished", p.rollback))
        if isinstance(data, dict) and data.get("txn") is not None:
            stops.append(t("uninstall.bot_update_unfinished"))
        return stops

    def queue_stops(self, q):
        if q == "unreadable":
            return [t("uninstall.bot_queue_unreadable", self.target.p.state / "state.json")]
        if isinstance(q, dict) and (q["spool"] or q["pending"]):
            return [t("uninstall.bot_queue_busy", self.inv.label_of(self.target), q["spool"], q["pending"])]
        return []

    def show_bot_plan(self):
        stops = self.bot_checks()
        m = self.target
        label = self.inv.label_of(m) if m else self.name
        print("\n" + t("uninstall.plan_header", t("uninstall.level_bot_named", label)))
        for what, now, action in self.plan_rows():
            print(f"  {pad(what, 58)} {pad(now, 9)} {action}")
        if m is not None:
            if self.executor is not None:
                print("  " + t("uninstall.plan_executor", self.inv.label_of(self.executor)))
            step = self.bot_step()
            if step:
                print("  " + t("uninstall.plan_resume", step))
        for reason in stops:
            print(f"  STOP: {reason}")
        return not stops

    def confirm_main(self):
        if self.level == "bot":
            return self.ask(t("uninstall.ask_phrase", self.name)) == self.name
        if self.level == "program":
            if self.yes:
                return True
            return self.ask(t("uninstall.ask_program")) == "y"
        if self.inv.multi:
            print(t("uninstall.confirm_bots", self.level))
            for m in self.inv.bots():
                print("  - " + self.inv.label_of(m))
        phrase = self.team if self.level == "identity" else f"delete {self.team}"
        return self.ask(t("uninstall.ask_phrase", phrase)) == phrase

    def confirm_foreign(self):
        for fid, what in self.inv.foreign:
            print(t("uninstall.foreign_warning", fid, what, self.level))
            if self.ask(t("uninstall.ask_accept", f"I accept {fid}")) != f"I accept {fid}":
                return False
        return True

    def queue_total(self, q):
        return sum(q.values()) if isinstance(q, dict) else 0

    def confirm_queue(self, q, again=False):
        if not isinstance(q, dict) or not self.queue_total(q):
            return True
        counts = ", ".join(f"{k} {v}" for k, v in q.items())
        if self.level == "program":
            print(t("uninstall.queue_kept", counts))
            if not again or self.yes:
                return True
            return self.ask(t("uninstall.ask_queue_grew")) == "y"
        print(t("uninstall.queue_lost", counts, self.level))
        return self.ask(t("uninstall.ask_lose")) == "yes"

    def service_label(self, m):
        if not self.inv.multi:
            return t("uninstall.label_service")
        return t("uninstall.label_service_of", self.inv.label_of(m))

    def bootout(self, m, label):
        service = f"gui/{os.getuid()}/{m.p.label}"
        code, out = run(["launchctl", "print", service], timeout=30)
        if code != 0:
            return
        if not self.inv.service_owned(out, m):
            self.result(label, "skipped", t("uninstall.service_not_ours"))
            raise Stop(t("uninstall.service_not_stopped", m.p.label, m.p.plist))
        run(["launchctl", "bootout", service], timeout=60)
        for _ in range(20):
            code, out = run(["launchctl", "print", service], timeout=30)
            if code != 0:
                return
            time.sleep(0.5)
        self.result(label, "failed", t("uninstall.bootout_failed"))
        raise Stop(t("uninstall.still_loaded"))

    def remove_plist(self, m, label, was):
        p = self.p
        if "service" in self.inv.skips:
            self.result(label, "skipped", t("uninstall.plist_kept", self.inv.skips["service"]))
            return False
        temps = [x for x in p.temp_files() if x.parent == p.agents] if m.kind == bots.LEGACY else m.p.temp_plists()
        before = len(self.results)
        self.remove_paths(label, [m.p.plist] + temps, absent="absent" if not was else None)
        return self.results[before][1] in ("done", "absent")

    def stop_member(self, m):
        label = self.service_label(m)
        was = m.plist_present or m.loaded
        self.bootout(m, label)
        self.remove_plist(m, label, was)

    def stop_relay(self):
        for m in self.inv.members:
            if not self.inv.multi or m.listed or m.plist_present or m.loaded:
                self.stop_member(m)

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

    def prompt_relay_name(self):
        if self.level == "bot":
            return self.target.name
        if self.inv.relay_name or not self.inv.multi:
            return self.inv.relay_name or relay_names.DEFAULT
        legacy = self.inv.members[0]
        if legacy.listed and legacy.name:
            return legacy.name
        added = [m for m in self.inv.bots() if m.kind == bots.ADDED]
        return added[0].name if added else relay_names.DEFAULT

    def prompt_selector(self):
        if self.level == "bot":
            m = self.executor
            return m.name if m is not None and m.kind == bots.ADDED else None
        name = self.prompt_relay_name()
        return name if any(m.kind == bots.ADDED and m.name == name for m in self.inv.bots()) else None

    def cloud_prompt(self, challenge, mode):
        return render_prompt("cloud-uninstall", self.lang, team_name=self.team,
                             mac_machine=self.inv.mac_machine or "mac",
                             relay_name=self.prompt_relay_name(),
                             level=self.level, mode=mode, challenge=challenge,
                             bot_selector=self.prompt_selector())

    def cloud_step(self):
        mode = "residual" if self.residual else "normal"
        challenge = secrets.token_hex(8)
        text = self.cloud_prompt(challenge, mode)
        if self.level == "bot":
            print("\n" + t("uninstall.cloud_send_bot", self.inv.label_of(self.executor), self.target.name))
        else:
            print("\n" + t("uninstall.cloud_pause"))
            print(t("uninstall.cloud_send"))
        show = lambda: print(f"{RULE}\n{text}{RULE}")
        show()
        key = None if self.residual else self.inv.mac_key()
        while True:
            answer = self.ask(t("uninstall.ask_proof"))
            if answer is None or answer == "skip":
                return "skipped", t("uninstall.skipped_by_user")
            if answer == "prompt":
                show()
                continue
            if answer == "none":
                print(t("uninstall.no_proof"))
                break
            if re.fullmatch(r"[0-9a-f]{16}", answer):
                if key is None:
                    print(t("uninstall.no_key_for_proof"))
                    continue
                if not hmac.compare_digest(answer, tag(key, "proof", challenge)):
                    print(t("uninstall.proof_mismatch"))
                    return "skipped", t("uninstall.proof_mismatch_note")
                print(t("uninstall.proof_ok", tag(key, "ack", challenge)))
                break
            print(t("uninstall.not_understood"))
        while True:
            answer = self.ask(t("uninstall.ask_done_bot" if self.level == "bot" else "uninstall.ask_done"))
            if answer == "done":
                return "done", t("uninstall.reported_by_bot")
            if answer == "incomplete" and self.level != "bot":
                return "incomplete", t("uninstall.bot_incomplete")
            if answer is None or answer == "skip":
                return "skipped", t("uninstall.bot_unfinished")
            if answer == "prompt":
                show()

    def cloud_member_check(self):
        inv = self.inv
        label = t("uninstall.label_cloud_member")
        if self.residual:
            done = self.cloud_status == "done"
            note = (t("uninstall.residual_reported") if done else
                    t("uninstall.residual_incomplete", f"{self.team}/{inv.cloud_machine}")
                    if self.cloud_status == "incomplete" else t("uninstall.bot_unfinished"))
            self.result(label, "done" if done else "skipped", note, True)
            return
        roster = inv.query_roster()
        if roster.status != "in":
            self.result(label, "skipped", t("uninstall.team_list_unknown", roster.message), True)
            return
        cloud, own, others = inv.classify(roster)
        if not cloud:
            self.result(label, "done", t("uninstall.cloud_gone"), True)
            return
        if inv.same_name:
            self.result(label, "skipped", t("uninstall.same_name", inv.cloud_machine), True)
            return
        rest = own + others
        if rest and not any(Roster.online(row) for row in rest):
            self.result(label, "skipped", t("uninstall.all_offline"), True)
            return
        target = f"{roster.me[0]}/{inv.cloud_machine}"
        print(t("uninstall.cloud_still_in", target, len(cloud), t("uninstall.list_separator").join(row[2] for row in cloud)))
        if len(cloud) > 1 and self.ask(t("uninstall.ask_all_rows", len(cloud))) != "y":
            self.result(label, "skipped", t("uninstall.not_confirmed"), True)
            return
        if self.ask(t("uninstall.ask_remove_cloud", target)) != "y":
            self.result(label, "skipped", t("uninstall.not_confirmed"), True)
            return
        code, out = inv.rn("remove", target)
        if code != 0:
            self.result(label, "skipped", t("uninstall.remove_failed", short(out)), True)
            return
        after = inv.query_roster()
        left = inv.classify(after)[0] if after.status == "in" else None
        if left == []:
            self.result(label, "done", t("uninstall.removed_with", f"reason-network remove {target}"), True)
        else:
            self.result(label, "skipped", t("uninstall.cloud_still_listed"), True)

    def dsh_step(self):
        inv = self.inv
        if "dsh" in inv.skips:
            raise Stop(t("uninstall.dsh_skipped", inv.skips["dsh"]))
        if not (inv.dsh_block or inv.dsh_plugin):
            self.result(t("uninstall.label_dsh"), "absent")
            return
        code, out = run(["/bin/bash", str(inv.here / "dsh/mac-uninstall.sh"), "--no-restore"], timeout=120)
        for line in out.splitlines():
            print("    " + line)
        if code != 0:
            self.result(t("uninstall.label_dsh"), "failed", t("uninstall.dsh_failed"))
            raise Stop(t("uninstall.dsh_not_removed", self.p.patch, f"./uninstall.sh --level {self.level}"))
        self.result(t("uninstall.label_dsh"), "done")

    def leave_step(self):
        inv = self.inv
        label = t("uninstall.label_mac_member")
        roster = inv.query_roster() if inv.roster.status != "none" else inv.roster
        if roster.status == "left":
            self.result(label, "absent", t("uninstall.already_left"), True)
            return
        if roster.status == "none":
            self.result(label, "absent", roster.message, True)
            return
        if roster.status != "in":
            self.result(label, "skipped", t("uninstall.team_list_unknown", roster.message), True)
            return
        code, out = inv.rn("leave")
        if code == 0:
            self.result(label, "done", credential=True)
            return
        rest = [row for row in roster.rows]
        if rest and not any(Roster.online(row) for row in rest):
            print(t("uninstall.leave_refused", f"reason-network remove {roster.me[0]}/{roster.me[1]}"))
            if self.ask(t("uninstall.ask_force")) == "force":
                code, out = inv.rn("leave", "--force")
                if code == 0:
                    self.result(label, "done", t("uninstall.left_forced"), True)
                    return
        self.result(label, "failed", "leave: " + short(out), True)
        raise Stop(t("uninstall.leave_failed"))

    def setup_step(self):
        inv = self.inv
        if not inv.engine:
            self.result(t("uninstall.label_wiring"), "absent", t("uninstall.no_engine"))
            return True
        code, out = inv.rn("setup", "--remove")
        for line in out.splitlines():
            print("    " + line)
        if code == 0:
            self.result(t("uninstall.label_wiring"), "done")
            return True
        self.result(t("uninstall.label_wiring"), "failed", t("uninstall.setup_failed", f"{self.p.rn_bin} setup --remove"))
        return False

    def engine_stop(self):
        inv = self.inv
        if inv.engine and self.p.machine_key.is_file():
            code, out = inv.rn("stop", timeout=60)
            if code != 0:
                print("    " + t("uninstall.stop_said", short(out)))

    def list_bots(self):
        if self.inv.multi:
            for m in self.inv.bots():
                print("  - " + self.inv.label_of(m))

    def routine_step(self):
        print("\n" + t("uninstall.routine_delete"))
        self.list_bots()
        answer = self.ask(t("uninstall.ask_deleted"))
        if answer == "deleted":
            self.result(t("uninstall.label_routine"), "user-confirmed", t("uninstall.routine_deleted"), True)
        else:
            self.result(t("uninstall.label_routine"), "user-pending", t("uninstall.routine_pending"), True)

    def manual_items(self):
        print("\n" + t("uninstall.manual_title"))
        print("  " + t("uninstall.manual_folder", self.inv.here))
        for where in self.inv.rc_lines:
            print("  " + t("uninstall.manual_path", where))
        print("  " + t("uninstall.manual_convention"))
        print("  " + t("uninstall.manual_inbox"))
        print("  " + t("uninstall.manual_memory"))

    def queue_now(self):
        inv = self.inv
        return inv.merged(inv.members, inv.snapshot())

    def run_program(self):
        if not self.confirm_queue(self.inv.queue_counts):
            raise Stop(t("uninstall.stopped_unchanged"))
        before = self.inv.queue_counts
        self.stop_relay()
        after = self.queue_now()
        if self.grew(before, after) and not self.confirm_queue(after, again=True):
            raise Stop(t("uninstall.stopped_after_relay_restart"))
        self.remove_paths(t("uninstall.label_program"), self.program_paths(), skip_key="program")
        print("\n" + t("uninstall.routine_pause"))
        self.list_bots()
        print(t("uninstall.reinstall_hint"))

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
        after = self.queue_now()
        if self.grew(before, after):
            print(t("uninstall.queue_grew"))
            if not self.confirm_queue(after, again=True):
                raise Stop(t("uninstall.stopped_after_relay"))
        status, note = self.cloud_step()
        self.cloud_status = status
        removed = status in ("done", "incomplete")
        self.result(t("uninstall.label_cloud_files"), "done" if removed else "skipped", note, True)
        if self.level == "all":
            self.result(t("uninstall.label_cloud_data"), "done" if removed else "skipped", note)
        self.cloud_member_check()
        self.dsh_step()
        setup_ok = True
        if not self.residual:
            self.leave_step()
            setup_ok = self.setup_step()
        self.engine_stop()
        self.remove_paths(t("uninstall.label_program"), self.program_paths(), skip_key="program")
        self.remove_paths(t("uninstall.label_record"), [p.lib], skip_key="program")
        self.remove_paths(t("uninstall.label_settings"), [p.conf], credential=True)
        if inv.multi or p.bots_conf.exists():
            self.remove_paths(t("uninstall.label_bots_settings"), [p.bots_conf], credential=True)
        if inv.custom_key:
            self.result(t("uninstall.label_custom_key"), "skipped", t("uninstall.custom_key_kept", inv.custom_key), True)
        self.remove_paths(t("uninstall.label_state"), [p.state], credential=True, skip_key="state")
        if inv.multi or p.bots_state.exists():
            self.remove_paths(t("uninstall.label_bots_state"), [p.bots_state], credential=True, skip_key="state")
        self.remove_paths(t("uninstall.label_identity"), [p.rn_home], credential=True)
        if self.level == "all":
            self.remove_paths(t("uninstall.label_logs"), p.log_files(), skip_key="logs")
            if setup_ok:
                self.remove_paths(t("uninstall.label_engine"), [p.rn_bin], skip_key="rn-bin")
            else:
                self.result(t("uninstall.label_engine"), "skipped", t("uninstall.engine_kept"))
            self.remove_paths(t("uninstall.label_dsh_backups"), [p.dsh_backup] + p.dsh_log_files(), skip_key="dsh")
            self.manual_items()
        self.routine_step()
        if self.level == "all":
            self.remove_paths(t("uninstall.label_mesh_lock"), [p.mesh_lock])

    def state_cmd(self, *args):
        code, out = run_locked([sys.executable, str(self.inv.here / "scripts/deploy_state.py"), *args])
        if code != 0:
            raise Stop(t("uninstall.bot_record_failed", short(out)))

    def set_step(self, step):
        m = self.target
        self.state_cmd("removing-set", "--relay-name", m.name, "--kind", m.kind, "--step", step)

    def abandon(self, m):
        if m.p.plist.is_file():
            run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(m.p.plist)], timeout=60)
        self.state_cmd("removing-clear", "--relay-name", m.name)

    def bot_mac_paths(self):
        m, p = self.target, self.p
        if m.kind == bots.LEGACY:
            return [m.p.env] + p.env_temps()
        return [m.p.conf]

    def run_bot(self):
        m, inv = self.target, self.inv
        label = inv.label_of(m)
        step = self.bot_step()
        at = BOT_STEPS.index(step) if step else -1
        if step is None:
            self.set_step("started")
            at = 0
        if at < BOT_STEPS.index("stopped"):
            service = self.service_label(m)
            was = m.plist_present or m.loaded
            self.bootout(m, service)
            stops = self.queue_stops(inv.queue(m))
            if stops:
                self.abandon(m)
                raise Stop(stops[0] + " " + t("uninstall.bot_restarted"))
            if not self.remove_plist(m, service, was):
                raise Stop(t("uninstall.bot_not_finished", f"./uninstall.sh --level bot --relay-name {m.name}"))
            self.set_step("stopped")
        if at < BOT_STEPS.index("routine"):
            print("\n" + t("uninstall.routine_delete_bot", label))
            answer = self.ask(t("uninstall.ask_deleted_bot"))
            if answer not in ("deleted", "paused"):
                self.result(t("uninstall.label_routine"), "user-pending", t("uninstall.routine_pending"), True)
                raise Stop(t("uninstall.bot_not_finished", f"./uninstall.sh --level bot --relay-name {m.name}"))
            self.result(t("uninstall.label_routine"), "user-confirmed", t("uninstall.routine_" + answer), True)
            self.set_step("routine")
        if at < BOT_STEPS.index("cloud"):
            status, note = self.cloud_step()
            self.result(t("uninstall.label_cloud_bot"), "done" if status == "done" else "skipped", note, True)
            if status != "done":
                self.finished = False
                return
            self.set_step("cloud")
        settings = t("uninstall.label_bot_settings")
        self.remove_paths(settings, self.bot_mac_paths(), credential=True)
        if self.results[-1][1] not in ("done", "absent"):
            raise Stop(t("uninstall.bot_not_finished", f"./uninstall.sh --level bot --relay-name {m.name}"))
        self.remove_paths(t("uninstall.label_state"), [m.p.state], credential=True, skip_key="state")
        state_ok = self.results[-1][1] in ("done", "absent")
        self.remove_paths(t("uninstall.label_logs"), m.p.log_files(self.p.logs), skip_key="logs")
        if not state_ok or self.results[-1][1] not in ("done", "absent"):
            raise Stop(t("uninstall.bot_not_finished", f"./uninstall.sh --level bot --relay-name {m.name}"))
        self.state_cmd("removing-done", "--relay-name", m.name)
        self.result(t("uninstall.label_bot_record"), "done")
        code, out = run_locked([sys.executable, str(inv.here / "scripts/reply_convention.py"), "install"])
        self.result(t("uninstall.label_convention"), "done" if code == 0 else "skipped",
                    "" if code == 0 else short(out))
        print("\n" + t("uninstall.bot_sessions_kept", f"{self.team}/{m.name}"))
        print(t("uninstall.bot_refresh_convention"))

    def summary(self, final=True):
        print("\n" + t("uninstall.result_header", self.level))
        self.result_rows(self.results)
        if self.level == "bot":
            again = f"./uninstall.sh --level bot --relay-name {self.name}"
            if not final or not self.finished:
                print(t("uninstall.bot_incomplete_summary", self.name, again))
                return 1
            print(t("uninstall.bot_complete", self.name))
            return 0
        if not final:
            print(t("uninstall.stopped_early", f"./uninstall.sh --level {self.level}"))
            return 1
        if not self.deep():
            bad = [r for r in self.results if r[1] in ("failed", "skipped")]
            print(t("uninstall.program_complete" if not bad else "uninstall.program_incomplete"))
            return 0 if not bad else 1
        open_items = [r for r in self.results if r[3] and r[1] not in ("done", "absent", "user-confirmed")]
        others = [r for r in self.results if not r[3] and r[1] in ("failed", "skipped")]
        if open_items:
            print(t("uninstall.level_open", self.level))
            for label, state, note, _ in open_items:
                print("  - " + t("uninstall.result_line", label, with_note(state_text(state), note)))
            print(t("uninstall.level_open_hint", f"./uninstall.sh --cloud-prompt --level {self.level}"))
        else:
            print(t("uninstall.level_complete", self.level))
        if others:
            print(t("uninstall.not_needed", t("uninstall.list_separator").join(r[0] for r in others)))
        print(t("uninstall.reopen_sessions"))
        return 1 if open_items else 0

    @staticmethod
    def result_rows(results):
        for label, state, note, _ in results:
            print(f"  {pad(label, 52)} {with_note(state_text(state), note)}")


def residual_team(uninstaller):
    answer = uninstaller.ask(t("uninstall.ask_team"))
    if answer is None or not valid_name(answer):
        raise Stop(t("uninstall.team_name_invalid"))
    return answer


def choose_level(ask, multi=False):
    print("\n" + t("uninstall.choose_level"))
    if multi:
        print(t("uninstall.choose_bot_hint"))
    return {"1": "program", "2": "identity", "3": "all"}.get(ask(t("uninstall.ask_level")) or "")


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, t("uninstall.bad_option", message) + "\n")


def parse(argv):
    parser = Parser(prog="uninstall.sh", add_help=False)
    parser.add_argument("--level", choices=LEVELS)
    parser.add_argument("--all", action="store_true", help="same as --level all")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--residual", action="store_true")
    parser.add_argument("--cloud-prompt", action="store_true")
    parser.add_argument("--yes", "-y", action="store_true")
    parser.add_argument("--lang", choices=("en", "zh-CN"))
    parser.add_argument("--relay-name")
    args = parser.parse_args(argv)
    if args.all:
        if args.level not in (None, "all"):
            parser.error(t("uninstall.error_all"))
        args.level = "all"
    if args.yes and args.level != "program":
        parser.error(t("uninstall.error_yes"))
    if (args.residual or args.cloud_prompt) and args.level not in DEEP:
        parser.error(t("uninstall.error_residual"))
    if args.level == "bot" and not args.relay_name:
        parser.error(t("uninstall.error_bot_name"))
    if args.relay_name is not None and args.level != "bot":
        parser.error(t("uninstall.error_relay_name"))
    if args.relay_name is not None and relay_names.problem(args.relay_name):
        parser.error(relay_names.problem(args.relay_name))
    return args


def command_of(args):
    extra = f" --relay-name {args.relay_name}" if args.level == "bot" else ""
    return f"./uninstall.sh --level {args.level}{extra}{' --residual' if args.residual else ''}"


def recorded_lang(paths):
    try:
        data = json.loads(paths.record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    lang = data.get("lang") if isinstance(data, dict) else None
    return lang if lang in LANGS else None


def resolve_lang(args, paths):
    env = os.environ.get("AGENT_MESH_LANG")
    return args.lang or recorded_lang(paths) or (env if env in LANGS else "en")


def utf8_output():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def main(argv=None):
    utf8_output()
    args = parse(sys.argv[1:] if argv is None else argv)
    here = ROOT
    paths = Paths(os.environ["HOME"])
    interactive = sys.stdin.isatty()
    lang = resolve_lang(args, paths)
    os.environ["AGENT_MESH_LANG"] = lang
    changes = not args.plan and (args.level is not None or interactive)
    if changes and mesh_lock.inherit(LOCK_FD, str(paths.mesh_lock)) != mesh_lock.HELD:
        return 1
    inv = Inventory(paths, here, holds_lock=changes)
    inv.show()
    if not args.plan and args.level and (args.level in DEEP or not args.yes) and not interactive:
        Uninstaller(inv, args.level, lang, residual=args.residual, name=args.relay_name).show_plan()
        print("\n" + t("uninstall.no_tty_program" if args.level == "program" else "uninstall.no_tty", args.level,
                       command_of(args)))
        return 3
    if args.plan:
        ok = True
        for level in ([args.level] if args.level else PLAN_LEVELS):
            ok = Uninstaller(inv, level, lang, residual=args.residual, name=args.relay_name).show_plan() and ok
        if not args.level and inv.multi:
            print("\n" + t("uninstall.plan_bot_hint"))
        print("\n" + t("uninstall.plan_nothing_removed"))
        return 0
    level = args.level
    if level is None:
        if not interactive:
            print("\n" + t("uninstall.choose_hint"))
            return 0
        u = Uninstaller(inv, "program", lang, interactive=True)
        level = choose_level(u.ask, inv.multi)
        if level is None:
            print(t("uninstall.nothing_removed"))
            return 0
    u = Uninstaller(inv, level, lang, residual=args.residual, yes=args.yes, interactive=interactive,
                    name=args.relay_name)
    try:
        return execute(u, args)
    except Stop as stop:
        print(f"\nSTOP: {stop}")
        if u.results:
            u.summary(final=False)
        return 1


def execute(u, args):
    inv = u.inv
    if u.level == "bot":
        if not u.show_plan():
            raise Stop(t("uninstall.nothing_removed_stops"))
        if not u.confirm_main():
            raise Stop(t("uninstall.not_confirmed_unchanged"))
        print()
        u.run_bot()
        return u.summary()
    if args.residual:
        if inv.identity:
            raise Stop(t("uninstall.residual_has_identity"))
    elif u.deep() and inv.roster.status == "none" and not inv.identity:
        raise Stop(t("uninstall.no_identity_use_residual", f"./uninstall.sh --residual --level {u.level}"))
    elif u.deep() and not inv.team:
        raise Stop(t("uninstall.team_not_found"))
    if args.cloud_prompt:
        stops = inv.stops_for(u.level)
        for reason in stops:
            print(f"  STOP: {reason}")
        if stops:
            raise Stop(t("uninstall.nothing_done"))
        if inv.foreign and not u.confirm_foreign():
            raise Stop(t("uninstall.stopped_unchanged"))
        if args.residual:
            u.team = residual_team(u)
        status, note = u.cloud_step()
        u.cloud_status = status
        u.result(t("uninstall.label_cloud_files"), "done" if status in ("done", "incomplete") else "skipped", note,
                 True)
        u.cloud_member_check()
        print("\n" + t("uninstall.cloud_result_header", u.level))
        u.result_rows(u.results)
        still = [r for r in u.results if r[3] and r[1] not in ("done", "absent", "user-confirmed")]
        print(t("uninstall.cloud_only_incomplete" if still else "uninstall.cloud_only_complete"))
        return 1 if still else 0
    ok = u.show_plan()
    if not ok:
        raise Stop(t("uninstall.nothing_removed_stops"))
    if args.residual:
        u.team = residual_team(u)
    if u.deep() and inv.foreign and not u.confirm_foreign():
        raise Stop(t("uninstall.stopped_unchanged"))
    if not u.confirm_main():
        raise Stop(t("uninstall.not_confirmed_unchanged"))
    if u.deep() and not u.confirm_queue(inv.queue_counts):
        raise Stop(t("uninstall.stopped_unchanged"))
    print()
    if u.deep():
        u.run_deep()
    else:
        u.run_program()
    return u.summary()


if __name__ == "__main__":
    sys.exit(main())
