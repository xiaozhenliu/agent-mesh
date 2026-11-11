"""Installation checks with fake tools and a disposable home; never contact real services."""
import base64
import hashlib
import io
import json
import os
import plistlib
import pty
import select
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

from render_prompt import ROOT, render_prompt
from test_render_prompt import ROUTINE_V041
from write_launch_agent import write_launch_agent


class Installation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.home = self.base / "custom homes" / "Alex's & Team"
        self.home.mkdir(parents=True)
        self.bin = self.base / "tools"
        self.bin.mkdir()
        for name in ("dirname", "sed", "head", "awk", "tar", "shasum", "openssl", "mkdir",
                     "chmod", "cp", "date", "grep", "cat", "install", "sleep", "tail", "wc", "mktemp", "rm", "mv",
                     "cmp", "ps"):
            found = shutil.which(name)
            if found:
                (self.bin / name).symlink_to(found)
        (self.bin / "python3").symlink_to(sys.executable)
        self.env = {"PATH": str(self.bin), "HOME": str(self.home),
                    "MOCK_OS": "Darwin", "MOCK_ARCH": "arm64", "MOCK_VERSION": "0.10.7",
                    "CALL_LOG": str(self.base / "calls")}
        self.tool("uname", 'if [ "$1" = -s ]; then echo "$MOCK_OS"; else echo "$MOCK_ARCH"; fi')
        self.tool("id", "echo 501")
        self.tool("launchctl", 'echo "launchctl $*" >> "$CALL_LOG"; exit 0')
        self.tool("plutil", "exit 0")
        self.tool("curl", 'echo "curl $*" >> "$CALL_LOG"; case "$*" in *https://aps1-1*) printf 200 ;; *) exit 73 ;; esac')
        self.tool("git", 'echo "unexpected git call" >> "$CALL_LOG"; exit 99')

    def tool(self, name, body, path=None):
        path = path or self.bin / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or path.exists():
            path.unlink()
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)
        return path

    def engine(self):
        path = self.home / ".local/bin/reason-network"
        self.who = self.base / "who"
        self.who.mkdir(exist_ok=True)
        (self.who / "all.json").write_text(json.dumps(EMPTY_DIRECTORY))
        self.env["MOCK_WHO"] = str(self.who)
        self.tool("reason-network", '[ "$1" = --version ] || echo "rn $*" >> "$CALL_LOG"\n'
                                    'case "$1" in --version) echo "reason-network $MOCK_VERSION" ;; '
                                    'invite) echo "For a cloud agent of yours send it: fake-invite" ;; '
                                    'who) [ ! -f "$MOCK_WHO/fail" ] || exit 1; '
                                    'if [ "$*" = "who --all --json" ]; then cat "$MOCK_WHO/all.json"; '
                                    'elif [ -f "$MOCK_WHO/text" ]; then cat "$MOCK_WHO/text"; fi ;; '
                                    '*) exit 0 ;; esac', path)
        cfg = self.home / ".reason/network/config.toml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text('person = "demo"\n')
        return path

    def run_script(self, script="install.sh", *args, root=ROOT, timeout=10):
        return subprocess.run(["/bin/bash", str(root / script), *args], env=self.env,
                              input="", capture_output=True, text=True, timeout=timeout)

    def calls(self):
        path = self.base / "calls"
        return path.read_text() if path.exists() else ""

    def test_cloud_prompt_uses_public_https_and_actual_home_without_git(self):
        self.engine()
        for lang in ("en", "zh-CN"):
            result = self.run_script("install.sh", "--cloud-prompt", "--lang", lang)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("git clone https://github.com/xiaozhenliu/agent-mesh.git", result.stdout)
            self.assertIn(str(self.home / ".config/agent-relay/outbound.key"), result.stdout)
            self.assertNotIn("<mac-home>", result.stdout)
        self.assertNotIn("unexpected git", self.calls())
        self.assertNotIn("curl", self.calls())
        self.assertNotIn("bootstrap", self.calls())

    def configured(self, relay=None):
        """An installed engine plus a configured relay: env with a webhook, and the current key."""
        self.engine()
        conf = self.home / ".config/agent-relay"
        conf.mkdir(parents=True)
        (conf / "env").write_text(f"RN_BIN={self.home}/.local/bin/reason-network\nPERSON=demo\n"
                                  "WEBHOOK_URL=https://example.invalid/hook\nWEBHOOK_KEY=0123456789\n"
                                  "OUTBOUND_KEY_FILE=~/.config/agent-relay/outbound.key\n")
        if relay:
            self.set_names(relay)
        key = conf / "outbound.key"
        key.write_text("a" * 64 + "\n")
        key.chmod(0o600)
        self.launchd()
        return key

    def launchd(self):
        """A launchctl that remembers whether the relay is loaded and which relay file each bootstrap started."""
        self.state = self.base / "launchd"
        self.state.mkdir(exist_ok=True)
        log = self.home / "Library/Logs/agent-relay.log"
        self.env["MOCK_LAUNCHD"] = str(self.state)
        (self.state / "register.py").write_text(REGISTER)
        self.tool("launchctl", f"""echo "launchctl $*" >> "$CALL_LOG"
S="$MOCK_LAUNCHD"
case "$1" in
  print)
    case "$2" in
      */com.local.agent-relay) [ -f "$S/loaded" ] || exit 113; [ ! -s "$S/loaded" ] || echo "	path = $(cat "$S/loaded")"; [ -f "$S/dead" ] || echo "state = running" ;;
    esac ;;
  bootout) rm -f "$S/loaded" ;;
  bootstrap)
    rm -f "$S/dead"
    if [ -f "$S/fail-bootstrap" ]; then rm -f "$S/fail-bootstrap"; exit 5; fi
    if [ -f "$S/hang" ]; then rm -f "$S/hang"; : > "$S/hanging"; while [ ! -f "$S/go" ]; do /bin/sleep 0.1; done; fi
    printf '%s' "$3" > "$S/loaded"
    python3 -c 'import hashlib, plistlib, sys; p = plistlib.load(open(sys.argv[1], "rb"))["ProgramArguments"][1]; print(hashlib.sha256(open(p, "rb").read()).hexdigest())' "$3" >> "$S/started"
    if [ -f "$S/die" ]; then rm -f "$S/die"; : > "$S/dead"; fi
    if [ -f "$S/no-up" ]; then rm -f "$S/no-up"; else echo "agent-relay up" >> "{log}"; python3 "$S/register.py"; fi ;;
esac
exit 0""")
        self.tool("sleep", "exit 0")

    def started(self):
        path = self.state / "started"
        return path.read_text().split() if path.exists() else []

    def loaded(self):
        return (self.state / "loaded").exists()

    def test_rotate_key_replaces_key_restarts_relay_and_explains_cloud_copy(self):
        key = self.configured()
        result = self.run_script("install.sh", "--rotate-key", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        new = key.read_text()
        self.assertRegex(new, r"^[0-9a-f]{64}\n$")
        self.assertNotEqual(new, "a" * 64 + "\n")
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(key.parent.glob("outbound.key.new.*")), [])
        self.assertIn("bootstrap", self.calls())
        self.assertIn("CopyToBox", result.stdout)
        self.assertIn("/workspace/agent-mesh/box/outbound.key", result.stdout)
        self.assertIn("./install.sh --webhook", result.stdout)
        self.assertNotIn(new.strip(), result.stdout + result.stderr)

    def test_rotate_key_without_terminal_or_relay_changes_nothing(self):
        key = self.configured()
        result = self.run_script("install.sh", "--rotate-key")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(key.read_text(), "a" * 64 + "\n")
        self.assertNotIn("bootstrap", self.calls())
        (key.parent / "env").unlink()
        result = self.run_script("install.sh", "--rotate-key", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("relay is not configured", result.stderr)
        self.assertEqual(key.read_text(), "a" * 64 + "\n")

    def test_rotate_key_refuses_when_the_relay_loads_another_key(self):
        key = self.configured()
        env = key.parent / "env"
        base = env.read_text()
        for extra, said in (("OUTBOUND_KEY=" + "b" * 64 + "\n", "sets OUTBOUND_KEY inline"),
                            ("OUTBOUND_KEY_FILE=/elsewhere/outbound.key\n", "does not load the key")):
            env.write_text(base + extra)
            result = self.run_script("install.sh", "--rotate-key", "--yes")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(said, result.stderr)
            self.assertEqual(key.read_text(), "a" * 64 + "\n")
            self.assertNotIn("bootstrap", self.calls())

    def test_purge_copies_removes_kept_bodies_only(self):
        state = self.home / "Library/Application Support/agent-relay"
        result = self.run_script("install.sh", "--purge-copies")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("removed 0 kept message copies", result.stdout)
        copies = state / "fwd-copies"
        copies.mkdir(parents=True)
        for name in ("01A.json", "01B.json", "01C.tmp"):
            (copies / name).write_text("{}")
        (state / "state.json").write_text("{}")
        result = self.run_script("install.sh", "--purge-copies")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("removed 3 kept message copies", result.stdout)
        self.assertEqual(list(copies.iterdir()), [])
        self.assertTrue((state / "state.json").exists())
        self.assertNotIn("curl", self.calls())
        elsewhere = self.base / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "keep.json").write_text("{}")
        copies.rmdir()
        copies.symlink_to(elsewhere, target_is_directory=True)
        result = self.run_script("install.sh", "--purge-copies")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", result.stderr)
        self.assertTrue((elsewhere / "keep.json").exists())

    def test_explicit_public_fork(self):
        self.engine()
        result = self.run_script("install.sh", "--cloud-prompt", "--repo-url", "https://example.invalid/fork.git")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("git clone https://example.invalid/fork.git", result.stdout)

    def test_ssh_repository_override_is_rejected(self):
        result = self.run_script("install.sh", "--cloud-prompt", "--repo-url", "git@example.invalid:repo.git")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("public HTTPS", result.stderr)

    def test_existing_other_version_is_not_overwritten(self):
        engine = self.engine()
        before = engine.read_bytes()
        self.env["MOCK_VERSION"] = "9.9.9"
        result = self.run_script("install.sh", "--name", "demo")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("has not been overwritten", result.stderr)
        self.assertEqual(engine.read_bytes(), before)
        self.assertNotIn("curl", self.calls())

    def test_other_path_installation_is_not_duplicated(self):
        self.tool("reason-network", "exit 0")
        result = self.run_script("install.sh", "--name", "demo")
        self.assertIn("already exists at", result.stderr)
        self.assertNotIn("curl", self.calls())

    def test_custom_network_home_stops_before_changes(self):
        self.env["REASON_NETWORK_HOME"] = str(self.base / "another-profile")
        result = self.run_script("install.sh", "--name", "demo")
        self.assertIn("custom REASON_NETWORK_HOME", result.stderr)
        self.assertNotIn("curl", self.calls())

    def test_missing_dependency_stops_before_download(self):
        (self.bin / "curl").unlink()
        result = self.run_script("install.sh", "--name", "demo")
        self.assertIn("missing curl", result.stderr)
        self.assertFalse((self.home / ".local").exists())

    def test_missing_gui_login_is_explained_before_changes(self):
        self.tool("launchctl", "exit 1")
        result = self.run_script("install.sh", "--name", "demo")
        self.assertIn("no macOS GUI login session", result.stderr)
        self.assertNotIn("curl", self.calls())

    def test_root_and_unsupported_local_system_are_rejected(self):
        self.env["MOCK_OS"] = "Linux"
        result = self.run_script("install.sh", "--name", "demo")
        self.assertIn("requires macOS", result.stderr)
        self.env["MOCK_OS"] = "Darwin"
        self.tool("id", "echo 0")
        result = self.run_script("install.sh", "--name", "demo")
        self.assertIn("without sudo", result.stderr)
        self.assertNotIn("curl", self.calls())

    def test_incomplete_python_is_explained(self):
        (self.bin / "python3").unlink()
        self.tool("python3", 'case "$*" in *sys.executable*) echo "$0" ;; *) exit 1 ;; esac')
        result = self.run_script("install.sh", "--name", "demo")
        self.assertIn("Python 3.9+ with ssl and sqlite3", result.stderr)
        self.assertNotIn("curl", self.calls())

    def test_both_mac_architectures_select_their_own_release(self):
        for arch, target in (("arm64", "aarch64-apple-darwin"), ("x86_64", "x86_64-apple-darwin")):
            self.env["MOCK_ARCH"] = arch
            result = self.run_script("install.sh", "--name", "demo")
            self.assertEqual(result.returncode, 73, result.stderr)
            self.assertIn(f"reason-network-{target}.tar.gz", self.calls())

    def test_unsupported_cloud_os_and_cpu_stop_before_writes(self):
        for system, arch, error in (("Darwin", "arm64", "requires Linux"), ("Linux", "riscv64", "unsupported cloud CPU")):
            self.env.update(MOCK_OS=system, MOCK_ARCH=arch)
            result = self.run_script("box/rn-cloud-setup.sh", "fake-invite", "demo")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(error, result.stderr)
            self.assertNotIn("curl", self.calls())

    def test_names_the_engine_would_change_are_refused_before_downloads(self):
        for args in (("--name", "a--b"), ("--name", "demo", "--machine", "m--1"), ("--name", "x" * 41),
                     ("--name", "demo-")):
            result = self.run_script("install.sh", *args)
            self.assertNotEqual(result.returncode, 0, args)
            self.assertIn("none repeated", result.stderr, args)
        self.assertEqual(self.run_script("install.sh", "--name", "x" * 40).returncode, 73)
        self.tool("sha256sum", "exit 0")
        self.env.update(MOCK_OS="Linux", MOCK_ARCH="x86_64")
        (self.base / "calls").unlink()
        for person in ("a--b", "x" * 41, "-a"):
            result = self.run_script("box/rn-cloud-setup.sh", "fake-invite", person)
            self.assertNotEqual(result.returncode, 0, person)
            self.assertIn("invalid team/person name", result.stderr, person)
        self.assertNotIn("curl", self.calls())

    def cloud_box(self):
        """A cloud directory with this checkout's rn-cloud-setup.sh, a fake download and a fake engine."""
        self.box = self.base / "workspace" / "box"
        self.box.mkdir(parents=True)
        shutil.copy(ROOT / "box/rn-cloud-setup.sh", self.box / "rn-cloud-setup.sh")
        self.mock = self.base / "mock"
        self.mock.mkdir()
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as tar:
            item = tarfile.TarInfo("reason-network")
            item.mode, item.size = 0o755, len(CLOUD_ENGINE)
            tar.addfile(item, io.BytesIO(CLOUD_ENGINE.encode()))
        (self.mock / "e.tgz").write_bytes(archive.getvalue())
        for name in ("cut", "gzip"):
            found = shutil.which(name)
            if found and not (self.bin / name).exists():
                (self.bin / name).symlink_to(found)
        self.tool("curl", 'echo "curl $*" >> "$CALL_LOG"; out=""; prev=""\n'
                          'for a in "$@"; do [ "$prev" != -o ] || out="$a"; prev="$a"; done\n'
                          'case "$*" in *.sha256*) echo "fakesum  e.tgz" > "$out" ;; *) cat "$MOCK_BOX/e.tgz" > "$out" ;; esac')
        self.tool("sha256sum", 'echo "fakesum  $1"')
        real_sed, real_mv = shutil.which("sed"), shutil.which("mv")
        self.tool("sed", f'[ "$1" != -i ] || exit 0\nexec "{real_sed}" "$@"')
        self.tool("mv", f'eval "last=\\${{$#}}"\n'
                        f'if [ -f "$MOCK_BOX/fail-mv" ] && [ "$last" = "$RN_CLOUD_BASE/person" ]; then exit 1; fi\n'
                        f'exec "{real_mv}" "$@"')
        self.env.update(MOCK_OS="Linux", MOCK_ARCH="x86_64", RN_CLOUD_BASE=str(self.box), MOCK_BOX=str(self.mock))

    def cloud_state(self, team=None, rows=()):
        (self.box / "state").mkdir(exist_ok=True)
        db = sqlite3.connect(self.box / "state" / "network.db")
        db.executescript(SESSIONS)
        if team is not None:
            db.execute("INSERT OR REPLACE INTO kv VALUES ('team', ?)", (team,))
        for name, key in rows:
            db.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?, 'cli', '/', NULL, NULL, NULL, 'pull', 1)",
                       (name, key))
        db.commit()
        db.close()

    def joined_before(self, team=None, me="demo  cloud"):
        self.cloud_state(json.dumps({"id": team or TEAM_ID, "name": "demo"}))
        (self.mock / "joined").write_text("")
        (self.mock / "team").write_text(f"team demo · relay: n0 public relays\n  demo  mac    direct 4ms\n"
                                        f"  {me}  this machine\n")

    def setup(self, *args):
        return subprocess.run(["/bin/bash", str(self.box / "rn-cloud-setup.sh"), *args], env=self.env, input="",
                              capture_output=True, text=True, timeout=30)

    def names_files(self):
        return {f.name: f.read_text() for f in self.box.iterdir() if f.is_file() and f.name != "rn-cloud-setup.sh"}

    def test_cloud_setup_writes_the_relay_name_then_the_team_name(self):
        self.cloud_box()
        result = self.setup(invite(), "demo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.names_files(), {"relay": "bot\n", "person": "demo\n"})
        self.assertIn("Bot address: demo/bot (this cloud uses bot-cloud and bot-inbox)", result.stdout)
        moves = [l for l in self.calls().splitlines() if "rn join" in l]
        self.assertEqual(len(moves), 1)
        shutil.rmtree(self.box / "state")
        (self.mock / "joined").unlink()
        for f in ("relay", "person"):
            (self.box / f).unlink()
        result = self.setup(invite(), "demo", "alice")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.names_files(), {"relay": "alice\n", "person": "demo\n"})
        self.assertIn("Bot address: demo/alice", result.stdout)

    def test_cloud_setup_order_relay_before_person(self):
        self.cloud_box()
        self.tool("mv", 'echo "mv ${3:-$2}" >> "$CALL_LOG"\n' f'exec "{shutil.which("mv")}" "$@"')
        result = self.setup(invite(), "demo", "alice")
        self.assertEqual(result.returncode, 0, result.stderr)
        moves = [l.rsplit("/", 1)[-1] for l in self.calls().splitlines() if l.startswith("mv ")]
        self.assertEqual(moves, ["relay", "person"])
        self.assertEqual([f.name for f in self.box.iterdir() if f.name.startswith(".")], [])

    def test_cloud_setup_rerun_in_the_same_team_skips_join(self):
        self.cloud_box()
        for link in (invite(), invite().rsplit("#", 1)[1], "  " + invite() + "\n"):
            with self.subTest(link=link[:20]):
                self.joined_before()
                (self.base / "calls").unlink(missing_ok=True)
                result = self.setup(link, "demo", "alice")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("already joined this team as demo/cloud; the invite is not used", result.stdout)
                self.assertNotIn("rn join", self.calls())
                self.assertEqual(self.names_files()["relay"], "alice\n")

    def test_cloud_setup_in_a_team_with_the_same_name_but_another_id_stops(self):
        self.cloud_box()
        self.joined_before(team=OTHER_TEAM_ID)
        result = self.setup(invite(), "demo", "alice")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("rn join", self.calls())
        self.assertIn("different team", result.stderr)
        self.assertIn("already in team 'demo'", result.stderr)
        self.assertEqual(self.names_files(), {})

    def test_cloud_setup_joined_as_someone_else_stops_without_join(self):
        self.cloud_box()
        for me in ("demo  laptop", "eve   cloud"):
            with self.subTest(me=me):
                self.joined_before(me=me)
                result = self.setup(invite(), "demo")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("review the existing deployment", result.stderr)
                self.assertNotIn("rn join", self.calls())
                self.assertEqual(self.names_files(), {})

    def test_cloud_setup_that_cannot_confirm_the_team_lets_the_engine_decide(self):
        self.cloud_box()
        cases = (("short code", "apple-river-stone-cloud", None),
                 ("bad base64", invite().replace("#tincan1", "#tincan1!!"), None),
                 ("cut off", invite(cut=10), None),
                 ("no id", invite(), json.dumps({"name": "demo"})),
                 ("bad json", invite(), "{"),
                 ("no team rows", invite(), "team-output"))
        for label, link, team in cases:
            with self.subTest(label):
                self.joined_before()
                if team == "team-output":
                    (self.mock / "team").write_text("something else\n")
                elif team is not None:
                    self.cloud_state(team)
                (self.base / "calls").unlink(missing_ok=True)
                result = self.setup(link, "demo", "alice")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("rn join", self.calls())
                self.assertIn("could not confirm", result.stderr)
                self.assertIn("already in team 'demo'", result.stderr)
                self.assertEqual(self.names_files(), {})

    def test_cloud_setup_with_an_unreadable_database_lets_the_engine_decide(self):
        self.cloud_box()
        self.joined_before()
        (self.box / "state" / "network.db").write_bytes(b"not a database" * 100)
        result = self.setup(invite(), "demo")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not confirm", result.stderr)

    def test_cloud_setup_never_renames(self):
        self.cloud_box()
        (self.box / "person").write_text("demo\n")
        (self.box / "relay").write_text("alice\n")
        result = self.setup(invite(), "demo", "bob")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("uses the Bot address name alice; renaming is not supported", result.stderr)
        self.assertNotIn("curl", self.calls())
        self.assertEqual(self.names_files(), {"relay": "alice\n", "person": "demo\n"})

    def test_cloud_setup_on_an_older_setup_needs_proof_for_a_custom_name(self):
        self.cloud_box()
        (self.box / "person").write_text("demo\n")
        for rows in ((), (("alice-inbox", "cli:someone-else"),)):
            with self.subTest(rows=rows):
                self.cloud_state(rows=rows)
                result = self.setup(invite(), "demo", "alice")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("cannot be proven", result.stderr)
                self.assertNotIn("curl", self.calls())
                self.assertNotIn("relay", self.names_files())
        self.joined_before()
        result = self.setup(invite(), "demo", "bot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.names_files()["relay"], "bot\n")
        (self.box / "relay").unlink()
        self.cloud_state(rows=(("alice-inbox", "cli:alice-inbox"),))
        result = self.setup(invite(), "demo", "alice")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.names_files()["relay"], "alice\n")

    def test_cloud_setup_repairs_a_damaged_relay_file_only_with_proof(self):
        self.cloud_box()
        for text in ("", "Bot\n", "a--b\n"):
            with self.subTest(text=text):
                for f in self.box.glob("relay*"):
                    f.unlink()
                (self.box / "person").write_text("demo\n")
                (self.box / "relay").write_text(text)
                shutil.rmtree(self.box / "state", ignore_errors=True)
                self.joined_before()
                result = self.setup(invite(), "demo", "alice")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("cannot be proven", result.stderr)
                self.assertNotIn("uses the Bot address name", result.stderr)
                self.assertIn("box/relay is damaged", result.stderr)
                self.assertEqual((self.box / "relay").read_text(), text)
                self.assertEqual(list(self.box.glob("relay.damaged-*")), [])
                self.cloud_state(rows=(("alice-inbox", "cli:alice-inbox"),))
                result = self.setup(invite(), "demo", "alice")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((self.box / "relay").read_text(), "alice\n")
                (backup,) = self.box.glob("relay.damaged-*")
                self.assertEqual(backup.read_text(), text)
        for f in self.box.glob("relay*"):
            f.unlink()
        (self.box / "relay").write_text("\n")
        result = self.setup(invite(), "demo", "bot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.box / "relay").read_text(), "bot\n")
        self.assertEqual(len(list(self.box.glob("relay.damaged-*"))), 1)

    def test_cloud_setup_interrupted_between_the_two_files_can_run_again(self):
        self.cloud_box()
        (self.mock / "fail-mv").write_text("")
        result = self.setup(invite(), "demo", "alice")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not write", result.stderr)
        self.assertEqual(self.names_files(), {"relay": "alice\n"})
        self.assertEqual([f.name for f in self.box.iterdir() if f.name.startswith(".")], [])
        (self.mock / "fail-mv").unlink()
        (self.mock / "team").write_text("team demo · relay: n0 public relays\n  demo  cloud  this machine\n")
        self.cloud_state(json.dumps({"id": TEAM_ID, "name": "demo"}))
        result = self.setup(invite(), "demo", "alice")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.names_files(), {"relay": "alice\n", "person": "demo\n"})

    def test_cloud_setup_invalid_relay_names_stop_before_any_download(self):
        self.cloud_box()
        for name in ("Bot", "a--b", "terminal", "terminal-2", "x" * 35, "a/b"):
            with self.subTest(name=name):
                result = self.setup(invite(), "demo", name)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid Bot address name", result.stderr)
        self.assertNotIn("curl", self.calls())
        self.assertEqual(self.names_files(), {})

    def test_cloud_runtime_on_mac_fails_before_accessing_cloud_paths(self):
        result = self.run_script("box/rn-cloud.sh", "who")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Bot's Linux cloud computer", result.stderr)
        self.assertNotIn("No such file", result.stderr)

    def test_cloud_purge_stops_engine_first_and_checks_days(self):
        box = self.base / "box"
        (box / "state").mkdir(parents=True)
        (box / "person").write_text("demo\n")
        (box / "outbound.key").write_text("k" * 64)
        (box / "rn-fetch.py").symlink_to(ROOT / "box/rn-fetch.py")
        self.tool("reason-network", 'echo "rn $*" >> "$CALL_LOG"', box / "bin/reason-network")
        db = sqlite3.connect(box / "state/network.db")
        db.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, sender TEXT, recipient TEXT, body TEXT, sent_at INTEGER)")
        db.execute("INSERT INTO messages VALUES ('m1', 'demo/terminal', 'demo/bot-inbox', 'old text', 1)")
        db.commit()
        db.close()
        self.env.update(MOCK_OS="Linux", MOCK_ARCH="x86_64", RN_CLOUD_BASE=str(box),
                        RN_INBOX_DIR=str(self.base / "inbox"), RN_THREADS_DIR=str(self.base / "threads"))
        for bad in ((), ("x",), ("-1",)):
            result = self.run_script("box/rn-cloud.sh", "purge", *bad)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("usage: rn-cloud.sh purge <days>", result.stderr)
        self.assertNotIn("rn stop", self.calls())
        result = self.run_script("box/rn-cloud.sh", "purge", "0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"messages": 1', result.stdout)
        self.assertEqual(self.calls().strip(), "rn stop")

    def test_launch_agent_keeps_selected_python_and_escapes_paths(self):
        destination = self.base / "agent.plist"
        python = str(self.home / "Python & Tools/bin/python3")
        relay = str(self.home / ".local/lib/agent-mesh/agent_relay.py")
        write_launch_agent(self.home, python, relay, destination)
        with destination.open("rb") as stream:
            data = plistlib.load(stream)
        self.assertEqual(data["ProgramArguments"], [python, relay])
        self.assertEqual(data["EnvironmentVariables"]["REASON_NETWORK_HOME"], str(self.home / ".reason/network"))

    def test_dsh_user_app_location_and_reinstall(self):
        self.engine()
        app = self.home / "Applications/DeepSeek Harness.app/Contents/MacOS/DeepSeek Harness"
        self.tool("app", "echo 'ℹ pass 7'", app)
        patch = self.home / ".dsh/cordis.patch.yml"
        patch.parent.mkdir(parents=True)
        patch.write_text("# === reason-network MCP BEGIN ===\nold\n# === reason-network MCP END ===\n")
        for attempt in range(3):
            if attempt == 2:
                custom = self.home / "My Apps/DeepSeek Harness.app"
                custom.parent.mkdir()
                shutil.move(str(app.parents[2]), custom)
                self.env["DSH_APP"] = str(custom)
            result = self.run_script("dsh/mac-install.sh", "demo")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(patch.read_text().count("id: reason-network-bridge"), 1)
            self.assertIn("Alex''s & Team", patch.read_text())


    def dsh_installed(self):
        self.engine()
        app = self.home / "Applications/DeepSeek Harness.app/Contents/MacOS/DeepSeek Harness"
        self.tool("app", "echo 'ℹ pass 7'", app)
        patch = self.home / ".dsh/cordis.patch.yml"
        patch.parent.mkdir(parents=True)
        own = "# === reason-network MCP BEGIN ===\nold\n# === reason-network MCP END ===\n"
        patch.write_text("- before: 1\n" + own + "- after: 2\n")
        result = self.run_script("dsh/mac-install.sh", "demo")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        plugin = self.home / ".dsh/plugins/reason-network-bridge.mjs"
        self.assertTrue(plugin.is_file())
        patch.write_text(patch.read_text() + "- added later: 3\n")
        return patch, own, plugin

    def test_dsh_uninstall_puts_back_only_reason_networks_block(self):
        patch, own, plugin = self.dsh_installed()
        saved = self.home / ".dsh/backup-reason-network-bridge/reason-network-block.yml"
        self.assertEqual(saved.read_text(), own)
        result = self.run_script("dsh/mac-uninstall.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(patch.read_text(), "- before: 1\n" + own + "- after: 2\n- added later: 3\n")
        self.assertFalse(plugin.exists())
        self.assertIn("own MCP block is back", result.stdout)
        again = self.run_script("dsh/mac-uninstall.sh")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("nothing to change", again.stdout)

    def test_dsh_uninstall_falls_back_to_backups_then_to_setup_hint(self):
        patch, own, _ = self.dsh_installed()
        backups = self.home / ".dsh/backup-reason-network-bridge"
        (backups / "reason-network-block.yml").unlink()
        newer = own.replace("old", "newer")
        (backups / "cordis.patch.yml.29991231235959.zzzzzz").write_text(newer)
        result = self.run_script("dsh/mac-uninstall.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(own, patch.read_text())
        self.assertNotIn("newer", patch.read_text())
        self.run_script("dsh/mac-install.sh", "demo")
        for path in backups.iterdir():
            path.unlink()
        result = self.run_script("dsh/mac-uninstall.sh")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("reason-network", patch.read_text())
        self.assertIn("reason-network setup", result.stdout)

    def test_dsh_uninstall_refuses_ambiguous_markers_and_changes_nothing(self):
        patch, _, plugin = self.dsh_installed()
        good = patch.read_text()
        self.assertEqual(self.run_script("dsh/mac-uninstall.sh", "--bogus").returncode, 2)
        begin = "# === reason-network-bridge BEGIN (replaces reason-network's MCP block) ===\n"
        end = "# === reason-network-bridge END ===\n"
        for broken in (good.replace(end, ""), good.replace(begin, ""),
                       begin + "- keep: 0\n" + good, good.replace(begin, end + begin)):
            patch.write_text(broken)
            result = self.run_script("dsh/mac-uninstall.sh")
            self.assertNotEqual(result.returncode, 0, broken)
            self.assertIn("edit the file by hand", result.stderr)
            self.assertEqual(patch.read_text(), broken)
            self.assertTrue(plugin.exists())

    def test_dsh_uninstall_keeps_lines_after_a_block_saved_without_newline(self):
        self.engine()
        app = self.home / "Applications/DeepSeek Harness.app/Contents/MacOS/DeepSeek Harness"
        self.tool("app", "echo 'ℹ pass 7'", app)
        patch = self.home / ".dsh/cordis.patch.yml"
        patch.parent.mkdir(parents=True)
        patch.write_text("- before: 1\n# === reason-network MCP BEGIN ===\nold\n# === reason-network MCP END ===")
        self.assertEqual(self.run_script("dsh/mac-install.sh", "demo").returncode, 0)
        patch.write_text(patch.read_text() + "- unrelated: 2\n")
        self.assertEqual(self.run_script("dsh/mac-uninstall.sh").returncode, 0)
        self.assertEqual(patch.read_text(), "- before: 1\n# === reason-network MCP BEGIN ===\nold\n"
                                             "# === reason-network MCP END ===\n- unrelated: 2\n")

    def test_legacy_uninstall_all_yes_is_refused_and_changes_nothing(self):
        patch, _, plugin = self.dsh_installed()
        before = patch.read_text()
        result = self.run_script("uninstall.sh", "--all", "-y")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertEqual(patch.read_text(), before)
        self.assertTrue(plugin.exists())
        self.assertTrue((self.home / ".reason/network/config.toml").exists())

    def relay_files(self):
        return (self.home / ".local/lib/agent-mesh/agent_relay.py",
                self.home / "Library/LaunchAgents/com.local.agent-relay.plist")

    def record(self):
        path = self.home / ".local/lib/agent-mesh/deployed.json"
        return json.loads(path.read_text()) if path.exists() else None

    def write_record(self, data):
        path = self.home / ".local/lib/agent-mesh/deployed.json"
        path.write_text(json.dumps(data))

    def mark(self, repo, field, state, lang="en"):
        subprocess.run([sys.executable, str(repo / "scripts/deploy_state.py"), "mark", field, state, "--lang", lang],
                       env=self.env, check=True)

    def repo(self):
        repo = self.base / "repo"
        shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns("__pycache__", ".git"))
        (repo / "check.sh").write_text("#!/bin/bash\necho 'RESULT ok'\n")
        self.git = self.base / "git"
        self.git.mkdir()
        (self.git / "head").write_text("a" * 40 + "\n")
        (self.git / "upstream").write_text("a" * 40 + "\n")
        self.env.update(GIT_STATE=str(self.git), PYTHONDONTWRITEBYTECODE="1")
        self.tool("git", GIT_STUB)
        return repo

    def installed(self, record=True, relay=None):
        self.configured(relay)
        repo = self.repo()
        result = self.run_script("install.sh", "--restart-relay", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        if record:
            for field in ("cloud", "cloud_setup", "prompt"):
                self.mark(repo, field, "confirmed")
        (self.base / "calls").unlink()
        return repo

    def call_lines(self):
        return self.calls().splitlines()

    def service_changes(self):
        return [line for line in self.call_lines() if line.startswith(("launchctl bootout", "launchctl bootstrap"))]

    def run_tty(self, root, *args, answers=(), script="update.sh"):
        master, slave = pty.openpty()
        try:
            os.write(master, "".join(answer + "\n" for answer in answers).encode() + b"\x04")
            return subprocess.run(["/bin/bash", str(root / script), *args], env=self.env, stdin=slave,
                                  capture_output=True, text=True, timeout=60)
        finally:
            os.close(slave)
            os.close(master)

    def run_tty_until(self, root, *args, answers=(), wait_for="", sig=signal.SIGTERM):
        master, slave = pty.openpty()
        try:
            os.write(master, "".join(answer + "\n" for answer in answers).encode())
            proc = subprocess.Popen(["/bin/bash", str(root / "update.sh"), *args], env=self.env, stdin=slave,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            seen, deadline = b"", time.time() + 30
            while wait_for.encode() not in seen and time.time() < deadline:
                ready, _, _ = select.select([proc.stderr], [], [], 0.5)
                if ready:
                    seen += os.read(proc.stderr.fileno(), 4096)
            proc.send_signal(sig)
            out, err = proc.communicate(timeout=30)
            return proc.returncode, out.decode(), (seen + err).decode()
        finally:
            os.close(slave)
            os.close(master)

    def sha(self, path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def test_restart_relay_rolls_back_each_failed_start(self):
        repo = self.installed()
        relay, plist = self.relay_files()
        old_relay, old_plist = relay.read_bytes(), plist.read_bytes()
        old_hash = hashlib.sha256(old_relay).hexdigest()
        with open(repo / "agent_relay.py", "a") as stream:
            stream.write("\n")
        for flag in ("no-up", "fail-bootstrap", "die"):
            (self.state / flag).write_text("")
            result = self.run_script("install.sh", "--restart-relay", root=repo)
            self.assertNotEqual(result.returncode, 0, flag)
            self.assertIn("previous relay restored", result.stdout, flag + result.stdout)
            self.assertEqual(relay.read_bytes(), old_relay, flag)
            self.assertEqual(plist.read_bytes(), old_plist, flag)
            self.assertTrue(self.loaded(), flag)
            self.assertEqual(self.started()[-1], old_hash, flag)
            self.assertFalse((relay.parent / "rollback").exists(), flag)
            lines = self.service_changes()
            self.assertEqual(lines[-2].split()[1], "bootout", flag)
        result = self.run_script("install.sh", "--restart-relay", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(relay.read_bytes(), (repo / "agent_relay.py").read_bytes())
        self.assertEqual(self.started()[-1], self.sha(repo / "agent_relay.py"))

    def test_restart_relay_without_a_backup_changes_nothing(self):
        repo = self.installed()
        relay, plist = self.relay_files()
        old = relay.read_bytes()
        with open(repo / "agent_relay.py", "a") as stream:
            stream.write("\n")
        relay.parent.chmod(0o555)
        try:
            result = self.run_script("install.sh", "--restart-relay", root=repo)
        finally:
            relay.parent.chmod(0o755)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not save a copy of the current relay; nothing changed", result.stdout)
        self.assertEqual(relay.read_bytes(), old)
        self.assertEqual(self.service_changes(), [])

    def test_restart_relay_finishes_or_refuses_a_leftover_switch(self):
        repo = self.installed()
        relay, plist = self.relay_files()
        good = relay.read_bytes()
        rollback = relay.parent / "rollback"

        def leftover(tamper=False):
            rollback.mkdir()
            (rollback / "loaded").write_text("1\n")
            (rollback / "agent_relay.py").write_bytes(good)
            (rollback / "agent.plist").write_bytes(plist.read_bytes())
            sums = "".join(f"{self.sha(rollback / name)}  {name}\n" for name in ("loaded", "agent_relay.py", "agent.plist"))
            (rollback / "SHA256SUMS").write_text(sums)
            if tamper:
                (rollback / "agent_relay.py").write_bytes(b"tampered")
            relay.write_bytes(b"half-switched")
            (self.state / "loaded").unlink(missing_ok=True)

        leftover(tamper=True)
        (relay.parent / "rollback.tmp").mkdir()
        result = self.run_script("install.sh", "--restart-relay", root=repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed verification", result.stdout)
        self.assertEqual(relay.read_bytes(), b"half-switched")
        self.assertTrue(rollback.exists())
        for path in rollback.iterdir():
            path.unlink()
        rollback.rmdir()
        leftover()
        result = self.run_script("install.sh", "--restart-relay", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("did not finish; restoring the previous relay first", result.stdout)
        self.assertEqual(self.started()[-2], hashlib.sha256(good).hexdigest())
        self.assertEqual(relay.read_bytes(), (repo / "agent_relay.py").read_bytes())
        self.assertFalse(rollback.exists())
        self.assertFalse((relay.parent / "rollback.tmp").exists())

    def test_restart_relay_interrupted_mid_switch_restores_the_previous_relay(self):
        repo = self.installed()
        relay, plist = self.relay_files()
        old_relay, old_plist = relay.read_bytes(), plist.read_bytes()
        with open(repo / "agent_relay.py", "a") as stream:
            stream.write("\n")
        (self.state / "hang").write_text("")
        proc = subprocess.Popen(["/bin/bash", str(repo / "install.sh"), "--restart-relay"], env=self.env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.time() + 30
        while not (self.state / "hanging").exists() and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue((self.state / "hanging").exists())
        self.assertFalse(self.loaded())
        proc.send_signal(signal.SIGTERM)
        (self.state / "go").write_text("")
        out, err = proc.communicate(timeout=60)
        self.assertEqual(proc.returncode, 143, out + err)
        self.assertIn("interrupted; restoring the previous relay", out)
        self.assertEqual((relay.read_bytes(), plist.read_bytes()), (old_relay, old_plist))
        self.assertTrue(self.loaded())
        self.assertEqual(self.started()[-1], hashlib.sha256(old_relay).hexdigest())
        self.assertFalse((relay.parent / "rollback").exists())

    def test_update_finishes_a_switch_killed_after_the_files_moved(self):
        repo = self.installed()
        relay, plist = self.relay_files()
        rollback = relay.parent / "rollback"
        rollback.mkdir()
        (rollback / "loaded").write_text("1\n")
        (rollback / "agent_relay.py").write_bytes(relay.read_bytes())
        (rollback / "agent.plist").write_bytes(plist.read_bytes())
        (rollback / "SHA256SUMS").write_text(
            "".join(f"{self.sha(rollback / name)}  {name}\n" for name in ("loaded", "agent_relay.py", "agent.plist")))
        (self.state / "loaded").unlink()
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("already up to date", result.stdout)
        self.assertTrue(self.loaded())
        self.assertFalse(rollback.exists())

    def test_deploy_state_refuses_confirmations_that_no_longer_match(self):
        repo = self.installed(record=False)
        state = [sys.executable, str(repo / "scripts/deploy_state.py")]
        run = lambda *args: subprocess.run(state + list(args), env=self.env, capture_output=True, text=True)
        self.assertNotEqual(run("mark", "prompt", "confirmed", "--expect", "0" * 64).returncode, 0)
        self.assertNotEqual(run("mark", "cloud", "confirmed", "--if-pending").returncode, 0)
        self.assertIsNone(self.record())
        self.assertEqual(run("mark", "cloud", "pending").returncode, 0)
        with open(repo / "box/rn-fetch.py", "a") as stream:
            stream.write("\n")
        self.assertNotEqual(run("mark", "cloud", "confirmed", "--if-pending").returncode, 0)
        self.assertEqual(self.record()["cloud"]["state"], "pending")
        shown = run("digest", "cloud").stdout.strip()
        self.assertEqual(run("mark", "cloud", "confirmed", "--expect", shown).returncode, 0)
        self.assertEqual(self.record()["cloud"], {"hash": shown, "state": "confirmed"})

    def test_update_ignores_a_damaged_unfinished_update_record(self):
        repo = self.installed()
        data = self.record()
        data["txn"] = {"stage": "routine_paused", "need": ["prompt"], "lang": "en", "commit": "x", "target": {}}
        self.write_record(data)
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("already up to date", result.stdout)
        self.assertIn("damaged and was ignored", result.stderr)
        self.assertEqual(self.service_changes(), [])

    def test_installer_records_cloud_and_prompt_as_pending_only(self):
        self.configured()
        self.assertEqual(self.run_script("install.sh", "--cloud-prompt").returncode, 0)
        data = self.record()
        self.assertEqual((data["cloud"]["state"], data["cloud_setup"]["state"]), ("pending", "pending"))
        self.assertNotIn("prompt", data)
        result = self.run_script("install.sh", "--webhook", "--lang", "zh-CN")
        self.assertEqual(result.returncode, 0, result.stderr)
        data = self.record()
        self.assertEqual((data["prompt"]["state"], data["lang"]), ("pending", "zh-CN"))
        self.mark(ROOT, "cloud", "confirmed")
        self.mark(ROOT, "prompt", "confirmed", "zh-CN")
        self.assertEqual(self.run_script("install.sh", "--cloud-prompt").returncode, 0)
        self.assertEqual(self.run_script("install.sh", "--webhook", "--lang", "zh-CN").returncode, 0)
        data = self.record()
        self.assertEqual((data["cloud"]["state"], data["prompt"]["state"]), ("pending", "pending"))
        result = self.run_script("uninstall.sh", "--level", "program", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(any(path.exists() for path in self.relay_files()))
        self.assertEqual(self.record()["prompt"]["state"], "pending")

    def test_update_reports_up_to_date_without_touching_anything(self):
        repo = self.installed()
        for args in (("--no-pull",), ("--no-pull", "--plan")):
            result = self.run_script("update.sh", *args, root=repo)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("already up to date" if len(args) == 1 else "== plan", result.stdout)
        self.assertEqual(self.service_changes(), [])
        self.assertNotIn("rn setup", self.calls())
        self.assertNotIn("rn invite", self.calls())

    def test_update_relay_only_runs_unattended(self):
        repo = self.installed()
        with open(repo / "agent_relay.py", "a") as stream:
            stream.write("\n")
        result = self.run_script("update.sh", "--no-pull", root=repo, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        relay, _ = self.relay_files()
        self.assertEqual(relay.read_bytes(), (repo / "agent_relay.py").read_bytes())
        self.assertNotIn("[GROK-UI]", result.stdout)
        self.assertRegex(result.stdout, r"relay\s+updated")
        self.assertIn("RESULT ok", result.stdout)
        self.assertNotIn("rn setup", self.calls())
        self.assertNotIn("rn invite", self.calls())

    def test_update_compares_generated_output(self):
        repo = self.installed()
        generator = repo / "scripts/write_launch_agent.py"
        original = generator.read_text()
        generator.write_text(original.replace('"ThrottleInterval": 30', '"ThrottleInterval": 31'))
        self.assertRegex(self.run_script("update.sh", "--no-pull", "--plan", root=repo).stdout, r"relay\s+changed")
        generator.write_text(original)
        prompt = repo / "prompts/routine.md"
        text = prompt.read_text()
        prompt.write_text(text + "\nA note outside the prompt block.\n")
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("already up to date", result.stdout)
        fence = text.index("```\n") + 4
        prompt.write_text(text[:fence] + "Changed. " + text[fence:])
        self.assertRegex(self.run_script("update.sh", "--no-pull", "--plan", root=repo).stdout, r"routine prompt\s+changed")

    def test_update_plan_and_non_terminal_never_change_the_deployment(self):
        repo = self.installed(record=False)
        result = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"cloud scripts\s+unknown")
        self.assertIsNone(self.record())
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIsNone(self.record())
        self.mark(repo, "cloud_setup", "pending")
        data = self.record()
        data["txn"] = {"stage": "routine_paused", "relay_was_loaded": True, "need": ["prompt"], "lang": "en",
                       "commit": "a" * 40, "target": {}}
        self.write_record(data)
        path = self.home / ".local/lib/agent-mesh/deployed.json"
        before = path.read_bytes()
        for args in (("--no-pull", "--plan"), ("--no-pull",)):
            result = self.run_script("update.sh", *args, root=repo)
            self.assertEqual(result.returncode, 0 if "--plan" in args else 3, result.stdout + result.stderr)
            self.assertIn("unfinished update stopped at 'routine_paused'", result.stdout)
            self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.service_changes(), [])
        path.write_text("{not json")
        result = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("unreadable", result.stderr)

    def test_update_guided_flow_runs_in_order_and_records_each_step(self):
        repo = self.installed(record=False)
        result = self.run_tty(repo, "--no-pull", answers=["y"] * 5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = self.record()
        for field in ("cloud", "cloud_setup", "prompt"):
            self.assertEqual(data[field]["state"], "confirmed", field)
        self.assertNotIn("txn", data)
        lines = self.service_changes()
        self.assertEqual(lines[0].split()[1], "bootout")
        self.assertEqual(lines[-1].split()[1], "bootstrap")
        self.assertIn("a" * 40, result.stdout)
        self.assertIn(self.sha(repo / "box/rn-cloud.sh"), result.stdout)
        self.assertTrue(self.loaded())

    def test_update_stop_restores_the_relay_and_rerun_continues(self):
        repo = self.installed(record=False)
        relay, _ = self.relay_files()
        result = self.run_tty(repo, "--no-pull", answers=["y", "n"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(self.loaded())
        self.assertEqual(self.started()[-1], self.sha(relay))
        self.assertEqual(self.record()["txn"]["stage"], "relay_stopped")
        self.assertIn("stopped at 'relay_stopped'", result.stdout)
        result = self.run_tty(repo, "--no-pull", answers=["y"] * 4)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("continuing the update that stopped at 'relay_stopped'", result.stdout)
        self.assertNotIn("txn", self.record())

    def paused_after_cloud(self):
        repo = self.installed(record=False)
        result = self.run_tty(repo, "--no-pull", answers=["y", "y", "y", "n"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("The routine is still paused", result.stdout)
        self.assertTrue(self.loaded())
        data = self.record()
        self.assertEqual((data["txn"]["stage"], data["cloud"]["state"]), ("cloud_done", "confirmed"))
        (self.base / "calls").unlink()
        return repo

    def test_update_rerun_stops_the_restored_relay_before_continuing(self):
        repo = self.paused_after_cloud()
        result = self.run_tty(repo, "--no-pull", answers=["y", "y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.service_changes()[0].split()[1], "bootout")
        self.assertIn("still paused", result.stderr)
        self.assertNotIn("Send this to Grok Bot", result.stdout)
        self.assertNotIn("txn", self.record())

    def test_update_rerun_redoes_a_step_whose_target_moved(self):
        repo = self.paused_after_cloud()
        with open(repo / "box/rn-cloud.sh", "a") as stream:
            stream.write("\n")
        result = self.run_tty(repo, "--no-pull", answers=["y"] * 4)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Send this to Grok Bot", result.stdout)
        self.assertEqual(self.record()["cloud"]["hash"],
                         subprocess.run([sys.executable, str(repo / "scripts/deploy_state.py"), "digest", "cloud"],
                                        env=self.env, capture_output=True, text=True).stdout.strip())

    def test_update_terminated_while_waiting_restores_the_relay(self):
        repo = self.installed(record=False)
        code, out, err = self.run_tty_until(repo, "--no-pull", answers=["y"], wait_for="Pause the routine")
        self.assertEqual(code, 143, out + err)
        self.assertTrue(self.loaded())
        self.assertEqual(self.record()["txn"]["stage"], "relay_stopped")
        self.assertFalse((self.home / ".local/lib/agent-mesh/update.lock").exists())

    def test_update_after_a_kill_reclaims_the_lock_and_continues(self):
        repo = self.installed()
        data = self.record()
        data["txn"] = {"stage": "routine_paused", "relay_was_loaded": True, "need": ["prompt"], "lang": "en",
                       "commit": "a" * 40, "target": {}}
        self.write_record(data)
        (self.state / "loaded").unlink()
        lock = self.home / ".local/lib/agent-mesh/update.lock"
        lock.mkdir()
        (lock / "pid").write_text("999999\n")
        result = self.run_tty(repo, "--no-pull", answers=["y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("removing the lock", result.stdout)
        self.assertTrue(self.loaded())
        self.assertNotIn("txn", self.record())
        self.assertFalse(lock.exists())
        lock.mkdir()
        holder = subprocess.Popen(["/bin/sh", "-c", "sleep 30; :", "update.sh"])
        try:
            (lock / "pid").write_text(f"{holder.pid}\n")
            for ps in ("present", "missing"):
                if ps == "missing":
                    (self.bin / "ps").unlink(missing_ok=True)
                result = self.run_script("update.sh", "--no-pull", root=repo)
                self.assertEqual(result.returncode, 1, ps)
                self.assertIn("another ./update.sh is running", result.stderr, ps)
                self.assertTrue(lock.exists(), ps)
        finally:
            holder.kill()
            holder.wait()

    def test_update_refuses_cloud_changes_the_cloud_cannot_pull(self):
        repo = self.installed()
        before = self.record()
        with open(repo / "box/rn-cloud.sh", "a") as stream:
            stream.write("\n")
        (self.git / "boxdirty").write_text(" M box/rn-cloud.sh\n")
        result = self.run_tty(repo, "--no-pull")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("box/ has uncommitted changes", result.stderr)
        (self.git / "boxdirty").unlink()
        (self.git / "upstream").write_text("b" * 40 + "\n")
        result = self.run_tty(repo, "--no-pull")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("not at its upstream commit", result.stderr)
        self.assertEqual(self.service_changes(), [])
        self.assertEqual(self.record(), before)
        (self.git / "upstream").write_text("a" * 40 + "\n")
        result = self.run_tty(repo, "--no-pull", answers=["y", "n"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.record()["cloud"], before["cloud"])
        self.assertTrue(self.loaded())

    def test_update_asks_once_about_steps_the_installer_printed(self):
        repo = self.installed(record=False)
        for field in ("cloud", "cloud_setup", "prompt"):
            self.mark(repo, field, "pending")
        result = self.run_tty(repo, "--no-pull", answers=["y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = self.record()
        for field in ("cloud", "cloud_setup", "prompt"):
            self.assertEqual(data[field]["state"], "confirmed", field)
        self.assertEqual(self.service_changes(), [])
        data["prompt"]["state"] = "pending"
        self.write_record(data)
        result = self.run_tty(repo, "--no-pull", answers=["n", "y", "y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Replace the prompt saved in the routine", result.stdout)
        self.assertEqual(self.record()["prompt"]["state"], "confirmed")

    def test_update_pulls_once_and_hands_over_to_the_new_script(self):
        repo = self.installed()
        (self.git / "dirty").write_text(" M install.sh\n")
        result = self.run_script("update.sh", root=repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn("uncommitted changes", result.stderr)
        self.assertNotIn("git pull", self.calls())
        (self.git / "dirty").unlink()
        (self.git / "pull-fail").write_text("")
        result = self.run_script("update.sh", root=repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn("git pull --ff-only failed", result.stderr)
        (self.git / "pull-fail").unlink()
        newer = self.base / "newer-update.sh"
        newer.write_text('#!/bin/bash\nfor a in "$@"; do echo "NEW-SCRIPT [$a]"; done\n')
        (self.git / "pull.sh").write_text(f"cp '{newer}' '{repo}/update.sh'\n")
        (self.git / "upstream").write_text("b" * 40 + "\n")
        (self.base / "calls").unlink()
        result = self.run_script("update.sh", "--lang", "zh-CN", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("NEW-SCRIPT [--no-pull]\nNEW-SCRIPT [--lang]\nNEW-SCRIPT [zh-CN]", result.stdout)
        self.assertEqual(self.calls().count("git pull"), 1)
        self.assertFalse((self.home / ".local/lib/agent-mesh/update.lock").exists())

    def test_update_stops_when_the_engine_version_differs(self):
        repo = self.installed()
        self.env["MOCK_VERSION"] = "0.12.17"
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 1)
        self.assertIn("this release requires 0.10.7", result.stderr)
        self.assertEqual(self.service_changes(), [])

    def test_update_replaces_only_the_dsh_plugin_file(self):
        repo = self.installed()
        app = self.home / "Applications/DeepSeek Harness.app/Contents/MacOS/DeepSeek Harness"
        self.tool("app", "echo 'ℹ pass 7'", app)
        patch = self.home / ".dsh/cordis.patch.yml"
        patch.parent.mkdir(parents=True)
        patch.write_text("# === reason-network MCP BEGIN ===\nold\n# === reason-network MCP END ===\n")
        self.assertEqual(self.run_script("dsh/mac-install.sh", "demo", root=repo).returncode, 0)
        patch.write_text(patch.read_text().replace("        allowPeople:", "        pollMs: 2000\n        allowPeople:"))
        before = patch.read_bytes()
        with open(repo / "dsh/reason-network-bridge.mjs", "a") as stream:
            stream.write("\n")
        result = self.run_script("update.sh", "--no-pull", root=repo, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        plugin = self.home / ".dsh/plugins/reason-network-bridge.mjs"
        self.assertEqual(plugin.read_bytes(), (repo / "dsh/reason-network-bridge.mjs").read_bytes())
        self.assertEqual(patch.read_bytes(), before)
        self.assertRegex(result.stdout, r"dsh plugin\s+updated")
        self.assertEqual(self.service_changes(), [])

    def test_dsh_installer_keeps_the_previous_plugin_when_it_fails(self):
        patch, _, plugin = self.dsh_installed()
        plugin.write_bytes(b"previous plugin")
        app = self.home / "Applications/DeepSeek Harness.app/Contents/MacOS/DeepSeek Harness"
        self.tool("app", "echo 'ℹ fail 1'; exit 1", app)
        result = self.run_script("dsh/mac-install.sh", "--plugin-only")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(plugin.read_bytes(), b"previous plugin")
        self.tool("app", "echo 'ℹ pass 7'", app)
        begin = "# === reason-network-bridge BEGIN (replaces reason-network's MCP block) ===\n"
        broken = begin + "- extra: 0\n" + patch.read_text()
        patch.write_text(broken)
        result = self.run_script("dsh/mac-install.sh", "demo")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(plugin.read_bytes(), b"previous plugin")
        self.assertEqual(patch.read_text(), broken)
        result = self.run_script("dsh/mac-install.sh", "--plugin-only")
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not hold exactly one bridge block", result.stdout)
        self.assertEqual(plugin.read_bytes(), b"previous plugin")
        self.assertEqual(list(plugin.parent.glob(".reason-network-bridge.mjs.*")), [])


    def env_file(self):
        return self.home / ".config/agent-relay/env"

    def set_names(self, relay, extra=""):
        env = self.env_file()
        keep = [line for line in env.read_text().splitlines()
                if not line.startswith(("LISTEN_NAME=", "CLOUD_NAME=", "INBOX_NAME="))]
        names = [f"LISTEN_NAME={relay}", f"CLOUD_NAME={relay}-cloud", f"INBOX_NAME={relay}-inbox"]
        env.write_text("\n".join(keep + names) + "\n" + extra)

    def fresh(self):
        self.engine()
        self.launchd()
        self.env["AGENT_MESH_REGISTER_WAIT"] = "2"
        real_sed = shutil.which("sed")
        if subprocess.run([real_sed, "--version"], capture_output=True).returncode == 0:
            self.tool("sed", f'if [ "$1" = -i ] && [ "$2" = "" ]; then shift 2; exec "{real_sed}" -i "$@"; fi\n'
                             f'exec "{real_sed}" "$@"')

    def sessions(self, rows):
        path = self.home / ".reason/network/network.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        db.executescript(SESSIONS)
        for name, key, last_seen in rows:
            db.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?, 'cli', '/', NULL, NULL, NULL, 'pull', ?)",
                       (name, key, last_seen))
        db.commit()
        db.close()
        return path

    def webhook(self, root=ROOT):
        master, slave = pty.openpty()
        try:
            proc = subprocess.Popen(["/bin/bash", str(root / "install.sh"), "--webhook"], env=self.env, stdin=slave,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            os.write(master, b"https://example.invalid/hook\n")
            out_fd, err_fd = proc.stdout.fileno(), proc.stderr.fileno()
            got = {out_fd: b"", err_fd: b""}
            open_fds, sent, deadline = set(got), False, time.time() + 60
            while open_fds and time.time() < deadline:
                ready, _, _ = select.select(list(open_fds) + [master], [], [], 0.2)
                for fd in ready:
                    try:
                        chunk = os.read(fd, 4096)
                    except OSError:
                        chunk = b""
                    if fd == master:
                        continue
                    if not chunk:
                        open_fds.discard(fd)
                    got[fd] += chunk
                if not sent and b"(not echoed): " in got[err_fd]:
                    os.write(master, b"0123456789\n\x04")
                    sent = True
            proc.wait(timeout=10)
            proc.stdout.close()
            proc.stderr.close()
            return subprocess.CompletedProcess(proc.args, proc.returncode, got[out_fd].decode(), got[err_fd].decode())
        finally:
            os.close(slave)
            os.close(master)

    def rn_calls(self):
        return [line for line in self.call_lines() if line.startswith("rn ")]

    def state_cmd(self, *args, root=ROOT):
        return subprocess.run([sys.executable, str(root / "scripts/deploy_state.py"), *args], env=self.env,
                              capture_output=True, text=True)

    def test_relay_name_fresh_install_seeds_the_env_and_records_the_name(self):
        self.fresh()
        result = self.run_script("install.sh", "--name", "demo", "--relay-name", "alice", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        env = self.env_file()
        lines = env.read_text().splitlines()
        for line in ("LISTEN_NAME=alice", "CLOUD_NAME=alice-cloud", "INBOX_NAME=alice-inbox"):
            self.assertEqual(lines.count(line), 1, line)
        self.assertFalse([line for line in lines if line.startswith(("WEBHOOK_URL=", "WEBHOOK_KEY="))])
        self.assertEqual(env.stat().st_mode & 0o777, 0o600)
        self.assertIn("<team-name>/alice-cloud", result.stdout)
        self.assertIn("<team-name>/alice ", result.stdout)
        self.assertNotIn("<relay-name>", result.stdout)
        self.assertIn("rn who --all --json", self.rn_calls())
        data = self.record()
        self.assertEqual(data["relay_name"], "alice")
        self.assertEqual(data["prompt"]["hash"], self.state_cmd("digest", "prompt").stdout.strip())
        self.assertEqual(self.state_cmd("show-prompt").stdout, render_prompt("routine", "en", relay_name="alice"))

        result = self.webhook()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("relay registered as demo/alice", result.stdout)
        lines = env.read_text().splitlines()
        for line in ("LISTEN_NAME=alice", "CLOUD_NAME=alice-cloud", "INBOX_NAME=alice-inbox",
                     "WEBHOOK_URL=https://example.invalid/hook"):
            self.assertEqual(lines.count(line), 1, line)
        for register, said in (("alice-2", "registered as alice-2 instead of demo/alice"),
                               ("-", "registered as nothing instead of demo/alice")):
            db = self.sessions([])
            con = sqlite3.connect(db)
            con.execute("DELETE FROM sessions")
            con.commit()
            con.close()
            self.env["MOCK_REGISTER"] = register
            result = self.webhook()
            self.assertNotEqual(result.returncode, 0, register)
            self.assertIn(said, result.stderr, register)
        self.sessions([("alice", "cli:alice", 1)])
        result = self.webhook()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("relay registered as demo/alice", result.stdout)
        db.unlink()
        result = self.webhook()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("registered as nothing (no engine database", result.stderr)

    def test_relay_name_cannot_be_changed_even_before_the_install_finished(self):
        self.fresh()
        self.assertEqual(self.run_script("install.sh", "--name", "demo", "--relay-name", "alice", "--yes").returncode, 0)
        env, record = self.env_file(), self.home / ".local/lib/agent-mesh/deployed.json"
        before = (env.read_bytes(), record.read_bytes())
        (self.base / "calls").unlink()
        for args in (("--name", "demo", "--relay-name", "bob", "--yes"), ("--webhook", "--relay-name", "bob"),
                     ("--cloud-prompt", "--relay-name", "bob")):
            result = self.run_script("install.sh", *args)
            self.assertNotEqual(result.returncode, 0, args)
            self.assertIn("Renaming is not supported", result.stderr, args)
            self.assertEqual((env.read_bytes(), record.read_bytes()), before, args)
            self.assertEqual(self.rn_calls(), [], args)
        result = self.run_script("install.sh", "--cloud-prompt", "--relay-name", "alice")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Bot address: demo/alice (cloud sessions alice-cloud, alice-inbox)", result.stdout)
        self.set_names("bob")
        result = self.run_script("install.sh", "--webhook")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("installed with the Bot address name 'alice', but", result.stderr)
        self.assertIn("now gives 'bob'", result.stderr)
        env.unlink()
        for args in ((), ("--relay-name", "bob")):
            result = self.run_script("install.sh", "--name", "demo", "--yes", *args)
            self.assertNotEqual(result.returncode, 0, args)
            self.assertIn("is gone", result.stderr, args)
            self.assertIn("--relay-name alice", result.stderr, args)
            self.assertFalse(env.exists(), args)

    def test_relay_name_on_an_install_without_name_keys_is_bot(self):
        self.configured()
        result = self.run_script("install.sh", "--cloud-prompt", "--relay-name", "bot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rn-cloud-setup.sh '<invite-link>' demo bot.", result.stdout)
        self.assertIn("Bot address: demo/bot (cloud sessions bot-cloud, bot-inbox)", result.stdout)
        self.assertNotIn("<relay-name>", result.stdout)
        result = self.run_script("install.sh", "--cloud-prompt", "--relay-name", "alice")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("uses the Bot address name 'bot'. Renaming is not supported", result.stderr)
        self.assertEqual(self.record()["relay_name"], "bot")

    def test_invalid_relay_names_stop_before_any_download(self):
        for name in ("Bot", "a--b", "terminal", "terminal-3", "x" * 35, "a_b"):
            result = self.run_script("install.sh", "--name", "demo", "--relay-name", name)
            self.assertNotEqual(result.returncode, 0, name)
            self.assertIn("--relay-name: the Bot address name", result.stderr, name)
        self.assertNotIn("curl", self.calls())

    def test_relay_name_taken_in_the_team_stops_before_setup(self):
        self.fresh()
        row = {"address": "demo/alice-inbox", "name": "alice-inbox", "person": "demo", "machine": "laptop",
               "agent": "cli", "cwd": "~", "repo": None, "branch": None, "status": "offline", "since": 1,
               "doing": None, "tier": "pull", "unread": 0, "is_me": False}
        listing = self.who / "all.json"
        args = ("install.sh", "--name", "demo", "--relay-name", "alice", "--yes")
        for content, said in ((json.dumps(dict(EMPTY_DIRECTORY, sessions=[row])),
                               "demo/alice-inbox (machine laptop, offline) already exists"),
                              ("not json", "could not check the session names"),
                              ('{"sessions": [{}]}', "could not check the session names")):
            listing.write_text(content)
            result = self.run_script(*args)
            self.assertNotEqual(result.returncode, 0, content)
            self.assertIn(said, result.stderr, content)
            self.assertIn("rn who --all --json", self.rn_calls(), content)
            self.assertFalse([line for line in self.rn_calls() if line.startswith("rn setup")], content)
            self.assertFalse(self.env_file().exists(), content)
        listing.write_text(json.dumps(EMPTY_DIRECTORY))
        (self.who / "fail").write_text("")
        result = self.run_script(*args)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("who --all --json failed", result.stderr)
        (self.who / "fail").unlink()
        self.sessions([("alice-2", "cli:alice", 1000)])
        result = self.run_script(*args)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already registered the relay key for alice as alice-2", result.stderr)
        self.assertFalse([line for line in self.rn_calls() if line.startswith("rn setup")])
        result = self.run_script("install.sh", "--name", "demo", "--relay-name", "bob", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_relay_names_not_derived_from_one_name_stop_without_showing_secrets(self):
        repo = self.installed()
        env = self.env_file()
        env.write_text(env.read_text() + "LISTEN_NAME=x\n")
        for script, args, root in (("install.sh", ("--webhook",), ROOT), ("update.sh", ("--no-pull", "--plan"), repo)):
            result = self.run_script(script, *args, root=root)
            out = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0, script)
            self.assertIn("not derived from one Bot address name: x, bot-cloud, bot-inbox", out, script)
            self.assertNotIn("0123456789", out, script)
            self.assertNotIn("example.invalid", out, script)
        self.assertEqual(self.service_changes(), [])

    def test_env_writes_replace_the_whole_file_or_nothing(self):
        self.fresh()
        real = (self.bin / "mv").resolve()
        self.tool("mv", f'case "$*" in */agent-relay/env) [ ! -f "$MOCK_LAUNCHD/fail-mv" ] || exit 1 ;; esac\n'
                        f'exec "{real}" "$@"')
        conf = self.env_file().parent
        args = ("install.sh", "--name", "demo", "--relay-name", "alice", "--yes")
        (self.state / "fail-mv").write_text("")
        result = self.run_script(*args)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not write", result.stderr)
        self.assertFalse(self.env_file().exists())
        self.assertEqual(list(conf.glob(".env.*")), [])
        (conf / ".env.Ab12Cd").write_text("leftover")
        (self.state / "fail-mv").unlink()
        result = self.run_script(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("LISTEN_NAME=alice", self.env_file().read_text())
        self.assertEqual(list(conf.glob(".env.*")), [])
        before = self.env_file().read_bytes()
        (self.state / "fail-mv").write_text("")
        result = self.webhook()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not write", result.stderr)
        self.assertEqual(self.env_file().read_bytes(), before)
        self.assertEqual(list(conf.glob(".env.*")), [])
        (self.state / "fail-mv").unlink()
        result = self.webhook()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("WEBHOOK_URL=https://example.invalid/hook", self.env_file().read_text())

    def test_update_prints_the_prompt_that_deploy_state_hashes(self):
        repo = self.installed(relay="alice")
        data = self.record()
        self.assertEqual(data["relay_name"], "alice")
        data["prompt"]["state"] = "pending"
        self.write_record(data)
        result = self.run_tty(repo, "--no-pull", answers=["n", "y", "y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        shown = self.state_cmd("show-prompt", root=repo).stdout
        self.assertIn("<team-name>/alice-cloud", shown)
        self.assertIn("this one (en):\n" + "-" * 64 + "\n" + shown + "\n" + "-" * 64 + "\n", result.stdout)
        self.assertEqual(self.record()["prompt"]["hash"], self.state_cmd("digest", "prompt", root=repo).stdout.strip())

    def test_update_accepts_records_from_before_the_name_was_recorded(self):
        repo = self.installed()
        old = self.record()
        old.pop("relay_name")
        old["prompt"]["hash"] = ROUTINE_V041["en"]
        self.write_record(old)
        result = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"routine prompt\s+unchanged")
        self.assertRegex(result.stdout, r"address name\s+unknown\s+bot")
        self.assertEqual(self.state_cmd("status", "--lang", "en", "--python", sys.executable, root=repo).stdout.splitlines()[-1], "name unknown bot")
        self.set_names("alice")
        result = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"address name\s+unknown\s+alice")
        self.mark(repo, "cloud", "confirmed")
        self.assertEqual(self.record()["relay_name"], "alice")
        self.assertRegex(self.run_script("update.sh", "--no-pull", "--plan", root=repo).stdout,
                         r"address name\s+ok\s+alice")
        self.set_names("bob")
        before = self.record()
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("now gives 'bob'. Renaming is not supported", result.stderr)
        self.assertIn("nothing was changed", result.stderr)
        result = self.run_script("install.sh", "--webhook", root=repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("now gives 'bob'", result.stderr)
        result = self.state_cmd("mark", "prompt", "pending", root=repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("changed from alice", result.stderr)
        self.assertEqual(self.record(), before)
        self.assertEqual(self.service_changes(), [])

    def test_check_reports_the_relay_name_and_its_registration(self):
        self.configured(relay="alice")
        text = self.who / "text"
        now = int(time.time() * 1000)

        def check():
            return self.run_script("check.sh").stdout

        out = check()
        self.assertIn("PASS  relay name: alice", out)
        self.assertIn("SKIP  relay session (the engine has no session database yet)", out)
        text.write_text("  alice  cli  ~/work  idle\n  repo-claude  claude-code  ~/repo  idle\n")
        db = self.sessions([("alice", "cli:alice", now)])
        self.assertIn("PASS  relay listening as demo/alice", check())
        text.write_text("  repo-claude  claude-code  ~/repo  idle\n")
        self.assertIn("SKIP  relay session (appears a few seconds", check())
        for who in ("  alice  cli  ~/work  idle\n", ""):
            text.write_text(who)
            self.sessions([("alice-2", "cli:alice", now + 1)])
            self.assertIn("FAIL  relay registered under another name: demo/alice-2", check(), who)
        db.unlink()
        self.sessions([("bob", "cli:bob", now)])
        self.assertIn("SKIP  relay session (appears a few seconds", check())
        env = self.env_file()
        env.write_text(env.read_text() + "LISTEN_NAME=x\n")
        out = check()
        self.assertIn("FAIL  relay names in env are not derived from one name", out)
        self.assertNotIn("0123456789", out)
        env.unlink()
        self.assertIn("PASS  relay name: bot (default)", check())


TEAM_ID = "5f0c2a9be14d4c7aa3d1e2f30415a6b7"
OTHER_TEAM_ID = "0123456789abcdef0123456789abcdef"


def invite(team=TEAM_ID, cut=None):
    data = bytes.fromhex(team) + bytes(range(16)) + bytes([4]) + b"demo" + bytes([0, 0, 0]) + bytes(range(32))
    ticket = "tincan1" + base64.urlsafe_b64encode(data[:cut]).decode().rstrip("=")
    return f"join my Reason Network team as demo: https://reason.network/join/#{ticket}"


CLOUD_ENGINE = """#!/bin/sh
echo "rn $*" >> "$CALL_LOG"
case "$1" in
  join) if [ -f "$MOCK_BOX/joined" ]; then echo "Error: already in team 'demo' (with demo/mac); leave it first"; exit 1; fi
        : > "$MOCK_BOX/joined"; echo "joined team demo as demo/cloud" ;;
  team) [ ! -f "$MOCK_BOX/team" ] || cat "$MOCK_BOX/team" ;;
  doctor) echo "peers: 1 connected" ;;
esac
exit 0
"""


EMPTY_DIRECTORY = {"team": "demo", "person": "demo", "machine": "mac", "machines": [], "sessions": []}

SESSIONS = """CREATE TABLE IF NOT EXISTS sessions (name TEXT PRIMARY KEY, key TEXT NOT NULL, agent TEXT NOT NULL,
cwd TEXT NOT NULL, repo TEXT, branch TEXT, doing TEXT, tier TEXT NOT NULL, last_seen INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);"""

REGISTER = f'''import os, sqlite3, sys, time
from pathlib import Path
home = Path(os.environ["HOME"])
name = os.environ.get("MOCK_REGISTER", "")
if name == "-":
    sys.exit(0)
listen = "bot"
env = home / ".config/agent-relay/env"
if env.exists():
    for line in env.read_text().splitlines():
        if line.startswith("LISTEN_NAME="):
            listen = line.split("=", 1)[1]
db = home / ".reason/network/network.db"
db.parent.mkdir(parents=True, exist_ok=True)
con = sqlite3.connect(db)
con.executescript("""{SESSIONS}""")
con.execute("INSERT OR IGNORE INTO sessions VALUES (?, ?, 'cli', '/', NULL, NULL, NULL, 'pull', ?)",
            (name or listen, "cli:" + listen, int(time.time() * 1000)))
con.commit()
'''

GIT_STUB = r'''[ "$1" = -C ] && shift 2
echo "git $*" >> "$CALL_LOG"
S="$GIT_STATE"
case "$*" in
  "rev-parse --is-inside-work-tree") echo true ;;
  "rev-parse --abbrev-ref @{u}") echo origin/main ;;
  "rev-parse @{u}") cat "$S/upstream" ;;
  "rev-parse HEAD") cat "$S/head" ;;
  "rev-parse --short HEAD") head -c 7 "$S/head"; echo ;;
  "status --porcelain --untracked-files=no") [ ! -f "$S/dirty" ] || cat "$S/dirty" ;;
  "status --porcelain -- box") [ ! -f "$S/boxdirty" ] || cat "$S/boxdirty" ;;
  "pull --ff-only") [ ! -f "$S/pull-fail" ] || exit 1; [ ! -f "$S/pull.sh" ] || /bin/sh "$S/pull.sh"; cp "$S/upstream" "$S/head" ;;
  *) echo "unexpected git $*" >> "$CALL_LOG"; exit 99 ;;
esac'''

if __name__ == "__main__":
    unittest.main()
