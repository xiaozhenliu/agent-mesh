"""Installation checks with fake tools and a disposable home; never contact real services."""
import base64
import hashlib
import importlib.util
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


def rn_engine():
    spec = importlib.util.spec_from_file_location("rn_engine", ROOT / "box/rn-engine.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
                     "cmp", "ps", "gzip"):
            found = shutil.which(name)
            if found:
                (self.bin / name).symlink_to(found)
        (self.bin / "python3").symlink_to(sys.executable)
        self.env = {"PATH": str(self.bin), "HOME": str(self.home),
                    "MOCK_OS": "Darwin", "MOCK_ARCH": "arm64", "MOCK_VERSION": "0.12.17", "AGENT_MESH_REGISTER_WAIT": "2",
                    "CALL_LOG": str(self.base / "calls")}
        self.tool("uname", 'if [ "$1" = -s ]; then echo "$MOCK_OS"; else echo "$MOCK_ARCH"; fi')
        self.tool("id", "echo 501")
        self.tool("launchctl", 'echo "launchctl $*" >> "$CALL_LOG"; exit 0')
        self.tool("plutil", "exit 0")
        self.tool("curl", 'echo "curl $*" >> "$CALL_LOG"; case "$*" in *https://aps1-1*|*.compute.reasonmachines.com/*) printf 200 ;; *) exit 73 ;; esac')
        self.git = self.base / "git"
        self.git.mkdir()
        (self.git / "release-commit").write_text("a" * 40 + "\n")
        self.env.update(GIT_STATE=str(self.git), RELEASE_ROOT=str(ROOT))
        self.env["RELEASE_GIT_STUB"] = str(self.tool("release-git", RELEASE_GIT))
        self.tool("git", RELEASE_GIT)

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
                                    'team) [ ! -f "$MOCK_WHO/team" ] || cat "$MOCK_WHO/team" ;; '
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
            self.assertIn("URL=https://github.com/xiaozhenliu/agent-mesh.git\n", result.stdout)
            self.assertIn(str(self.home / ".config/agent-relay/outbound.key"), result.stdout)
            self.assertNotIn("<mac-home>", result.stdout)
        self.assertNotIn("unexpected git", self.calls())
        self.assertNotIn("curl", self.calls())
        self.assertNotIn("bootstrap", self.calls())

    def test_cloud_prompt_is_pinned_to_the_published_release(self):
        self.engine()
        tag = "v" + (ROOT / "VERSION").read_text().strip()
        cases = (("release-fail", "could not be fetched"),
                 ("release/box/rn-fetch.py", "box/rn-fetch.py in this checkout differs from the release"))
        for path, why in cases:
            with self.subTest(why):
                (self.git / path).parent.mkdir(parents=True, exist_ok=True)
                (self.git / path).write_text("other\n")
                result = self.run_script("install.sh", "--cloud-prompt")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(why, result.stderr)
                self.assertIn("./install.sh --cloud-prompt", result.stderr)
                self.assertEqual(result.stdout.count("AGENT_MESH_SETUP"), 0)
                self.assertIsNone(self.record())
                (self.git / path).unlink()
        result = self.run_script("install.sh", "--cloud-prompt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"git fetch https://github.com/xiaozhenliu/agent-mesh.git refs/tags/{tag}:refs/tags/{tag}",
                      self.calls())
        self.assertIn(f"TAG='{tag}'; COMMIT='{'a' * 40}'", result.stdout)
        for name in ("box/rn-cloud.sh", "box/rn-fetch.py", "box/rn-cloud-setup.sh", "box/rn-engine.py"):
            digest = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            self.assertIn(f"{digest}  {name}", result.stdout)
        for placeholder in ("<release-tag>", "<release-commit>", "<cloud-checksums>", "<repo-url>"):
            self.assertNotIn(placeholder, result.stdout)

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
      */com.local.agent-relay) [ -f "$S/loaded" ] || exit 113; [ ! -s "$S/loaded" ] || echo "	path = $(cat "$S/loaded")"; [ -f "$S/dead" ] || {{ echo "state = running"; [ -f "$S/nopid" ] || echo "	pid = $(cat "$S/pid")"; }} ;;
    esac ;;
  bootout) rm -f "$S/loaded" ;;
  bootstrap)
    rm -f "$S/dead"
    echo "$$" > "$S/pid"
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
        self.assertIn("URL=https://example.invalid/fork.git\n", result.stdout)

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
        self.assertIn("never downgrades or replaces a newer or unrecognised engine", result.stderr)
        self.assertEqual(engine.read_bytes(), before)
        self.env["MOCK_VERSION"] = "0.10.7"
        result = self.run_script("install.sh", "--name", "demo")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("this release requires 0.12.17. Run ./update.sh", result.stderr)
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
            result = self.run_script("install.sh", "--name", "demo", "--relay-name", "nova")
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("could not download Reason Network v0.12.17; nothing was installed", result.stderr)
            self.assertIn(f"reason-network-{target}.tar.gz", self.calls())
            self.assertNotIn(".sha256", self.calls())

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
        self.assertIn("could not download", self.run_script("install.sh", "--name", "x" * 40, "--relay-name", "nova").stderr)
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
        shutil.copy(ROOT / "box/rn-engine.py", self.box / "rn-engine.py")
        self.mock = self.base / "mock"
        self.mock.mkdir()
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as tar:
            item = tarfile.TarInfo("reason-network")
            item.mode, item.size = 0o755, len(CLOUD_ENGINE)
            tar.addfile(item, io.BytesIO(CLOUD_ENGINE.encode()))
        (self.mock / "e.tgz").write_bytes(archive.getvalue())
        setup = (ROOT / "box/rn-cloud-setup.sh").read_text()
        line = next(l for l in setup.splitlines() if "/x86_64-unknown-linux-musl) echo " in l)
        fake = hashlib.sha256(archive.getvalue()).hexdigest()
        (self.box / "rn-cloud-setup.sh").write_text(setup.replace(line, line.rsplit(" echo ", 1)[0] + f" echo {fake} ;;"))
        for name in ("cut", "gzip"):
            found = shutil.which(name)
            if found and not (self.bin / name).exists():
                (self.bin / name).symlink_to(found)
        self.tool("curl", 'echo "curl $*" >> "$CALL_LOG"; out=""; prev=""\n'
                          'for a in "$@"; do [ "$prev" != -o ] || out="$a"; prev="$a"; done\n'
                          'case "$*" in *.sha256*) echo "fakesum  e.tgz" > "$out" ;; *) cat "$MOCK_BOX/e.tgz" > "$out" ;; esac')
        self.tool("sha256sum", 'python3 -c "import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], \'rb\').read()).hexdigest() + \'  \' + sys.argv[1])" "$1"')
        real_sed, real_mv = shutil.which("sed"), shutil.which("mv")
        self.tool("sed", f'[ "$1" != -i ] || exit 0\nexec "{real_sed}" "$@"')
        self.tool("mv", f'eval "last=\\${{$#}}"\n'
                        f'if [ -f "$MOCK_BOX/fail-mv" ] && [ "$last" = "$RN_CLOUD_BASE/person" ]; then exit 1; fi\n'
                        f'exec "{real_mv}" "$@"')
        self.builds = self.mock / "builds.json"
        self.pin_builds({"0.12.17": CLOUD_ENGINE.encode()})
        self.env.update(MOCK_OS="Linux", MOCK_ARCH="x86_64", RN_CLOUD_BASE=str(self.box), MOCK_BOX=str(self.mock),
                        RN_PINNED_BUILDS_FILE=str(self.builds))

    def pin_builds(self, builds):
        target = rn_engine().linux_target()
        self.builds.write_text(json.dumps({v: {target: hashlib.sha256(data).hexdigest()} for v, data in builds.items()}))

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
        return {f.name: f.read_text() for f in self.box.iterdir()
                if f.is_file() and f.name not in ("rn-cloud-setup.sh", "rn-engine.py")}

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
        self.assertEqual(moves, ["reason-network", "relay", "person"])
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

    def test_cloud_setup_rerun_reads_verified_names(self):
        self.cloud_box()
        self.joined_before(me="demo (verified)  cloud")
        result = self.setup(invite(), "demo", "alice")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already joined this team as demo/cloud; the invite is not used", result.stdout)
        self.assertNotIn("rn join", self.calls())

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
        (box / "rn-engine.py").symlink_to(ROOT / "box/rn-engine.py")
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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def mark(self, repo, field, state, lang="en"):
        subprocess.run([sys.executable, str(repo / "scripts/deploy_state.py"), "mark", field, state, "--lang", lang],
                       env=self.env, check=True)

    def repo(self):
        repo = self.base / "repo"
        shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns("__pycache__", ".git"))
        (repo / "check.sh").write_text("#!/bin/bash\necho 'RESULT ok'\n")
        (self.git / "head").write_text("a" * 40 + "\n")
        (self.git / "upstream").write_text("a" * 40 + "\n")
        self.env.update(GIT_STATE=str(self.git), RELEASE_ROOT=str(repo), PYTHONDONTWRITEBYTECODE="1")
        self.tool("git", GIT_STUB)
        return repo

    def installed(self, record=True, relay=None):
        self.configured(relay)
        repo = self.repo()
        result = self.run_script("install.sh", "--restart-relay", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        if record:
            for field in ("cloud", "cloud_setup", "prompt", "cloud_engine"):
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
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=os.setpgrp)
            seen, deadline = b"", time.time() + 90
            while wait_for.encode() not in seen and time.time() < deadline:
                ready, _, _ = select.select([proc.stderr], [], [], 0.5)
                if ready:
                    seen += os.read(proc.stderr.fileno(), 4096)
            if wait_for.encode() not in seen:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate(timeout=30)
                self.fail(f"update.sh never printed {wait_for!r}; stderr so far:\n{seen.decode()}")
            time.sleep(1)
            os.killpg(proc.pid, sig)
            out, err = proc.communicate(timeout=60)
            return proc.returncode, out.decode(), (seen + err).decode()
        finally:
            os.close(slave)
            os.close(master)

    def sha(self, path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def release_tag(self, repo):
        return "v" + (repo / "VERSION").read_text().strip()

    def cloud_reply(self, repo, commit="a" * 40, fetch_sum=None, engine=True, tag=None):
        lines = ["Done. Output:", "```", "origin: https://github.com/example/agent-mesh.git"]
        if engine:
            build = next(iter(rn_engine().PINNED_BUILDS["0.12.17"].values()))
            lines += ["engine: reason-network 0.12.17 running (daemon pid 4321, started 2026-11-12T08:00:00Z, "
                      "binary replaced 2026-11-12T07:59:58Z)",
                      f"engine: pinned build {build} at box/bin/engine/reason-network", "engine: launcher ok"]
        r = "agent-mesh-result"
        lines += [f"{r} commit {commit}",
                  f"{r} sum {self.sha(repo / 'box/rn-cloud.sh')}  box/rn-cloud.sh",
                  f"{r} sum {fetch_sum or self.sha(repo / 'box/rn-fetch.py')}  box/rn-fetch.py",
                  f"{r} sum {self.sha(repo / 'box/rn-cloud-setup.sh')}  box/rn-cloud-setup.sh",
                  f"{r} sum {self.sha(repo / 'box/rn-engine.py')}  box/rn-engine.py",
                  f"{r} DONE {tag or self.release_tag(repo)}", "```"]
        return lines + ["."]

    def cloud_reply_check(self, repo, text, *extra):
        return subprocess.run([sys.executable, str(repo / "scripts/deploy_state.py"), "cloud-reply",
                               "--commit", "a" * 40, "--tag", self.release_tag(repo), *extra], input=text,
                              env=self.env, capture_output=True, text=True)

    def test_restart_relay_needs_the_relay_pid_from_launchd(self):
        repo = self.installed()
        relay, _ = self.relay_files()
        old_relay = relay.read_bytes()
        with open(repo / "agent_relay.py", "a") as stream:
            stream.write("\n")
        (self.state / "nopid").write_text("")
        result = self.run_script("install.sh", "--restart-relay", root=repo)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("the relay service has no running process", result.stdout + result.stderr)
        self.assertIn("previous relay restored", result.stdout)
        self.assertEqual(relay.read_bytes(), old_relay)
        (self.state / "nopid").unlink()
        result = self.run_script("install.sh", "--restart-relay", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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

    def test_restart_relay_rolls_back_when_the_new_relay_registers_under_another_name(self):
        repo = self.installed()
        relay, plist = self.relay_files()
        old_relay, old_plist = relay.read_bytes(), plist.read_bytes()
        with open(repo / "agent_relay.py", "a") as stream:
            stream.write("\n")
        self.env.update(MOCK_REGISTER="bot-2", AGENT_MESH_REGISTER_WAIT="1")
        result = self.run_script("install.sh", "--restart-relay", root=repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("the new relay registered as demo/bot-2 instead of demo/bot", result.stdout)
        self.assertIn("previous relay restored", result.stdout)
        self.assertNotIn("relay up (log:", result.stdout)
        self.assertEqual((relay.read_bytes(), plist.read_bytes()), (old_relay, old_plist))
        self.assertTrue(self.loaded())
        self.assertEqual(self.started()[-1], hashlib.sha256(old_relay).hexdigest())
        self.assertFalse((relay.parent / "rollback").exists())

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
        result = self.run_tty(repo, "--no-pull", answers=["y", "y", "y", *self.cloud_reply(repo), "y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("match: commit, checksums and engine", result.stdout)
        self.assertIn("bash -s <<'AGENT_MESH_UPDATE'", result.stdout)
        self.assertNotIn("pull --ff-only", result.stdout)
        self.assertIn(f"TAG={self.release_tag(repo)}; COMMIT={'a' * 40}", result.stdout)
        self.assertRegex(result.stdout, r"cloud release\s+ok")
        self.assertIn("rn-cloud-setup.sh --upgrade-engine", result.stdout)
        data = self.record()
        self.assertEqual(data["cloud_engine"]["version"], "0.12.17")
        for field in ("cloud", "cloud_setup", "prompt", "cloud_engine"):
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
        result = self.run_tty(repo, "--no-pull", answers=["y", "y", "n"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertTrue(self.loaded())
        self.assertEqual(self.started()[-1], self.sha(relay))
        self.assertEqual(self.record()["txn"]["stage"], "relay_stopped")
        self.assertIn("stopped at 'relay_stopped'", result.stdout)
        result = self.run_tty(repo, "--no-pull", answers=["y", *self.cloud_reply(repo), "y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("continuing the update that stopped at 'relay_stopped'", result.stdout)
        self.assertNotIn("txn", self.record())

    def paused_after_cloud(self):
        repo = self.installed(record=False)
        result = self.run_tty(repo, "--no-pull", answers=["y", "y", "y", *self.cloud_reply(repo), "n"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("The routine is still paused: resume it in Grok Bot", result.stdout)
        self.assertTrue(self.loaded())
        data = self.record()
        self.assertEqual((data["txn"]["stage"], data["cloud"]["state"]), ("cloud_done", "confirmed"))
        self.assertEqual(data["cloud_engine"]["state"], "confirmed")
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
        result = self.run_tty(repo, "--no-pull", answers=["y", *self.cloud_reply(repo, engine=False), "y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Send this to Grok Bot", result.stdout)
        self.assertEqual(self.record()["cloud"]["hash"],
                         subprocess.run([sys.executable, str(repo / "scripts/deploy_state.py"), "digest", "cloud"],
                                        env=self.env, capture_output=True, text=True).stdout.strip())

    def test_update_rerun_with_a_rejected_cloud_reply_keeps_the_routine_paused(self):
        repo = self.paused_after_cloud()
        with open(repo / "box/rn-cloud.sh", "a") as stream:
            stream.write("\n")
        result = self.run_tty(repo, "--no-pull", answers=["y", "y", "."])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("mismatch:", result.stdout)
        self.assertIn("Keep it paused until ./update.sh records the cloud step", result.stdout)
        self.assertNotIn("resume it in Grok Bot", result.stdout)
        data = self.record()
        self.assertEqual(data["txn"]["stage"], "routine_paused")
        result = self.run_tty(repo, "--no-pull", answers=["y", *self.cloud_reply(repo, engine=False), "y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("txn", self.record())

    def test_update_cloud_step_needs_the_pasted_reply_to_match(self):
        cases = {
            "bare yes": ["y", "."],
            "empty answer": ["."],
            "wrong commit": None,
            "wrong checksum": None,
        }
        for label, answer in cases.items():
            with self.subTest(label):
                self.setUp()
                repo = self.installed(record=False)
                if label == "wrong commit":
                    answer = self.cloud_reply(repo, commit="b" * 40)
                elif label == "wrong checksum":
                    answer = self.cloud_reply(repo, fetch_sum="c" * 64)
                result = self.run_tty(repo, "--no-pull", answers=["y", "y", "y", *answer, "y", "y"])
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("mismatch:", result.stdout)
                self.assertIn("The routine is still paused. Keep it paused until ./update.sh records the cloud step",
                              result.stdout)
                self.assertNotIn("resume it in Grok Bot", result.stdout)
                self.assertTrue(self.loaded())
                data = self.record()
                self.assertNotEqual(data.get("cloud", {}).get("state"), "confirmed")
                self.assertEqual(data["txn"]["stage"], "routine_paused")
                result = self.run_tty(repo, "--no-pull", answers=["y", *self.cloud_reply(repo), "y", "y"])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("Send this to Grok Bot", result.stdout)
                self.assertEqual(self.record()["cloud"]["state"], "confirmed")

    def test_update_checks_the_release_before_changing_anything(self):
        cases = {
            "not published": ("release-fail", "could not be fetched"),
            "differs": ("release/box/rn-cloud.sh", "box/rn-cloud.sh in this checkout differs from the release"),
        }
        for label, (path, why) in cases.items():
            with self.subTest(label):
                self.setUp()
                repo = self.installed(record=False)
                (self.git / path).parent.mkdir(parents=True, exist_ok=True)
                (self.git / path).write_text("other\n")
                path_ = self.home / ".local/lib/agent-mesh/deployed.json"
                before = path_.read_bytes() if path_.exists() else None
                plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
                self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
                self.assertRegex(plan.stdout, r"cloud release\s+failed")
                self.assertIn(why, plan.stdout)
                result = self.run_tty(repo, "--no-pull", answers=["y", "y", "y"])
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(why, result.stderr)
                self.assertNotIn("Send this to Grok Bot", result.stdout)
                self.assertEqual(self.service_changes(), [])
                self.assertEqual(path_.read_bytes() if path_.exists() else None, before)
                self.assertTrue(self.loaded())

    def test_update_without_cloud_changes_does_not_check_the_release(self):
        repo = self.installed()
        (self.git / "release-fail").write_text("")
        with open(repo / "agent_relay.py", "a") as stream:
            stream.write("\n")
        plan = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(plan.returncode, 0, plan.stdout + plan.stderr)
        self.assertNotIn("cloud release", plan.stdout)
        result = self.run_tty(repo, "--no-pull", answers=["y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("git init", self.calls())
        self.assertNotIn("Send this to Grok Bot", result.stdout)

    def test_update_release_points_elsewhere_and_the_reply_must_show_it(self):
        repo = self.installed(record=False)
        (self.git / "release-commit").write_text("b" * 40 + "\n")
        result = self.run_tty(repo, "--no-pull", answers=["y", "y", "y", *self.cloud_reply(repo), "y", "y"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"TAG={self.release_tag(repo)}; COMMIT={'b' * 40}", result.stdout)
        self.assertIn("mismatch: the reply shows commit aaaaaaaaaaaa, not bbbbbbbbbbbb", result.stdout)
        result = self.run_tty(repo, "--no-pull", answers=["y", *self.cloud_reply(repo, commit="b" * 40), "y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.record()["cloud"]["state"], "confirmed")

    def test_update_interrupted_while_pasting_keeps_the_cloud_unconfirmed(self):
        for sig, code in ((signal.SIGINT, 130), (signal.SIGTERM, 143)):
            with self.subTest(sig=sig):
                self.setUp()
                repo = self.installed(record=False)
                partial = self.cloud_reply(repo)[:4]
                rc, out, err = self.run_tty_until(repo, "--no-pull", answers=["y", "y", "y", *partial],
                                                  wait_for="Paste Grok Bot's reply", sig=sig)
                self.assertEqual(rc, code, out + err)
                self.assertIn("The routine is still paused", out)
                self.assertTrue(self.loaded())
                data = self.record()
                self.assertNotEqual(data.get("cloud", {}).get("state"), "confirmed")
                self.assertEqual(data["txn"]["stage"], "routine_paused")
                self.assertFalse((self.home / ".local/lib/agent-mesh/update.lock").exists())
                result = self.run_tty(repo, "--no-pull", answers=["y", *self.cloud_reply(repo), "y", "y"])
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.record()["cloud"]["state"], "confirmed")

    def test_cloud_reply_reads_only_the_result_lines(self):
        repo = self.installed()
        r = "agent-mesh-result"
        cloud, fetch = self.sha(repo / "box/rn-cloud.sh"), self.sha(repo / "box/rn-fetch.py")
        tag = self.release_tag(repo)
        c, a = f"{r} sum {cloud}  box/rn-cloud.sh\n", f"{r} commit {'a' * 40}\n"
        f = f"{r} sum {fetch}  box/rn-fetch.py\n"
        rest = (f"{r} sum {self.sha(repo / 'box/rn-cloud-setup.sh')}  box/rn-cloud-setup.sh\n"
                f"{r} sum {self.sha(repo / 'box/rn-engine.py')}  box/rn-engine.py\n{r} DONE {tag}\n")
        good = [
            a + c + f + rest,
            f"Previously HEAD was {'b' * 40}.\n{r} commit {'A' * 40}\n{r} sum {cloud.upper()} *box/rn-cloud.sh\n"
            f"{r} sum {fetch}  ./box/rn-fetch.py\n{rest}",
            f"`{a.strip()}`\n> {c}- {f}**{r} DONE {tag}**\n{rest}",
            a + c + f + rest + "I would have printed STOP: here, but it is not at the start of a line\n",
            a + c + f + rest + f"commit {'c' * 40}\n{'d' * 64}  box/rn-fetch.py\nDONE v0.0.1\n",
        ]
        bad = [
            a + f"{r} commit {'b' * 40}\n" + c + f + rest,
            a + c + rest,
            f"{r} commit {'a' * 39}\n" + c + f + rest,
            a + c + f + f"{r} sum {'d' * 64}  box/rn-fetch.py\n" + rest,
            a + c.replace("rn-cloud.sh", "rn-cloud.sh.bak") + f + rest,
            a + c + f.replace("rn-fetch.py", "rn-fetch.py~") + rest,
            a + c + f + rest.replace(tag, "v0.0.1"),
            a + c + f + rest.replace(tag, tag + ".1"),
            a + c + f + rest + "STOP: the engine step failed\n",
            a + c + f + rest + "> **STOP: no clone**\n",
            a + c + f + rest.replace(f"{r} DONE {tag}", f"The block failed before {r} DONE {tag}"),
            a + c + f + rest.replace(f"{r} DONE {tag}", f"DONE {tag}"),
            (a + c + f + rest).replace(f"{r} ", ""),
        ]
        for text in good:
            with self.subTest(text=text):
                result = self.cloud_reply_check(repo, text)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for text in bad:
            with self.subTest(text=text):
                result = self.cloud_reply_check(repo, text)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("mismatch:", result.stdout)

    def test_cloud_reply_with_the_engine_needs_a_pinned_build_and_the_launcher(self):
        repo = self.installed()
        good = "\n".join(self.cloud_reply(repo)[:-1])
        build = next(iter(rn_engine().PINNED_BUILDS["0.12.17"].values()))
        result = self.cloud_reply_check(repo, good, "--engine")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        cases = {
            "no build line": (good.replace(f"engine: pinned build {build} at box/bin/engine/reason-network\n", ""),
                              "no line 'engine: pinned build"),
            "unpinned build": (good.replace(build, "e" * 64), "not a pinned Reason Network 0.12.17 build"),
            "two builds": (good + f"\nengine: pinned build {'e' * 64} at box/bin/engine/reason-network",
                           "not a pinned Reason Network 0.12.17 build"),
            "old path": (good.replace("box/bin/engine/reason-network", "box/bin/reason-network"),
                         "no line 'engine: pinned build"),
            "no launcher": (good.replace("engine: launcher ok\n", ""), "no line 'engine: launcher ok'"),
        }
        for label, (text, why) in cases.items():
            with self.subTest(label):
                result = self.cloud_reply_check(repo, text, "--engine")
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(why, result.stdout)

    def test_update_terminated_while_waiting_restores_the_relay(self):
        repo = self.installed(record=False)
        code, out, err = self.run_tty_until(repo, "--no-pull", answers=["y", "y"], wait_for="Pause the routine")
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

    def test_update_refuses_cloud_files_that_differ_from_the_release(self):
        repo = self.installed()
        before = self.record()
        (self.git / "release/box").mkdir(parents=True)
        shutil.copy(repo / "box/rn-cloud.sh", self.git / "release/box/rn-cloud.sh")
        with open(repo / "box/rn-cloud.sh", "a") as stream:
            stream.write("\n")
        result = self.run_tty(repo, "--no-pull")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("box/rn-cloud.sh in this checkout differs from the release", result.stderr)
        self.assertEqual(self.service_changes(), [])
        self.assertEqual(self.record(), before)
        (self.git / "release/box/rn-cloud.sh").unlink()
        result = self.run_tty(repo, "--no-pull", answers=["y", "n"])
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(self.record()["cloud"], before["cloud"])
        self.assertTrue(self.loaded())

    def test_update_asks_once_about_steps_the_installer_printed(self):
        repo = self.installed(record=False)
        for field in ("cloud", "cloud_setup", "prompt", "cloud_engine"):
            self.mark(repo, field, "pending")
        result = self.run_tty(repo, "--no-pull", answers=["y", "y"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = self.record()
        for field in ("cloud", "cloud_setup", "prompt", "cloud_engine"):
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

    def test_update_stops_when_the_engine_is_newer_or_unknown(self):
        repo = self.installed()
        for version in ("0.13.0", "garbage"):
            self.env["MOCK_VERSION"] = version
            result = self.run_script("update.sh", "--no-pull", root=repo)
            self.assertEqual(result.returncode, 1)
            self.assertIn("never downgrades or replaces a newer or unrecognised engine", result.stderr)
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
        for register, said in (("alice-2", "registered as demo/alice-2 instead of demo/alice"),
                               ("-", "registered as nothing (the new relay reported no registration within 2 s) "
                                     "instead of demo/alice")):
            db = self.sessions([])
            con = sqlite3.connect(db)
            con.execute("DELETE FROM sessions")
            con.commit()
            con.close()
            self.env["MOCK_REGISTER"] = register
            result = self.webhook()
            self.assertNotEqual(result.returncode, 0, register)
            self.assertIn(said, result.stderr, register)
        self.env["MOCK_REGISTER"] = ""
        self.sessions([("alice", "cli:alice", 1)])
        result = self.webhook()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("relay registered as demo/alice", result.stdout)
        db.unlink()
        self.env["MOCK_NO_DB"] = "1"
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
        result = self.run_script("install.sh", "--name", "demo", "--yes", "--relay-name", "bob")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("installed with the Bot address name 'alice'", result.stderr)
        self.assertIn("not 'bob'", result.stderr)
        self.assertIn("--relay-name alice or without --relay-name", result.stderr)
        self.assertFalse(env.exists())
        result = self.run_script("install.sh", "--name", "demo", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("LISTEN_NAME=alice", env.read_text())
        self.assertEqual(self.record()["relay_name"], "alice")

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

    def test_new_install_without_an_address_name_stops_with_a_suggestion(self):
        self.fresh()
        (self.home / ".reason/network/config.toml").unlink()
        for extra, said in (((), "What is your Grok Bot called?"),
                            (("--grok-name", "Zoë Bot"), "the suggested address is: --relay-name zoe-bot"),
                            (("--grok-name", "小助手"), "has no Latin letters or digits")):
            result = self.run_script("install.sh", "--name", "demo", "--yes", *extra)
            self.assertEqual(result.returncode, 1, extra)
            self.assertIn("a new installation needs the Bot's address name", result.stderr, extra)
            self.assertIn(said, result.stderr, extra)
            self.assertIn("--relay-name <address> --grok-name", result.stderr, extra)
        self.assertNotIn("curl", self.calls())
        self.assertEqual(self.rn_calls(), [])
        self.assertFalse((self.home / ".local/lib").exists())
        self.assertFalse(self.env_file().exists())

    def test_existing_installations_keep_their_address_without_relay_name(self):
        self.fresh()
        result = self.run_script("install.sh", "--name", "demo", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("LISTEN_NAME=bot", self.env_file().read_text())
        self.assertEqual(self.record()["relay_name"], "bot")
        self.assertNotIn("grok_name", self.record())
        self.assertIn("<team-name>/bot-cloud", result.stdout)
        for relay in (None, "alice"):
            shutil.rmtree(self.home / ".local/lib/agent-mesh")
            shutil.rmtree(self.home / ".config")
            shutil.rmtree(self.home / ".reason")
            self.configured(relay)
            want = relay or "bot"
            for args in (("--cloud-prompt",), ("--name", "demo", "--yes")):
                result = self.run_script("install.sh", *args)
                self.assertEqual(result.returncode, 0, (relay, args, result.stdout + result.stderr))
                self.assertIn(f"Bot address: demo/{want} (cloud sessions", result.stdout, (relay, args))
                self.assertEqual(self.record()["relay_name"], want, (relay, args))

    def test_grok_name_is_stored_as_given_and_shown_next_to_the_address(self):
        self.fresh()
        cfg = self.home / ".reason/network/config.toml"
        cfg.unlink()
        real = self.home / ".local/bin/reason-network"
        real.rename(self.base / "real-rn")
        self.tool("reason-network", f'case "$1" in team) [ -f "{cfg}" ] || exit 1 ;; '
                                    f'init) printf \'person = "demo"\\n\' >> "{cfg}" ;; esac\n'
                                    f'exec "{self.base / "real-rn"}" "$@"', real)
        grok = '小助手 "Bot" it\'s'
        result = self.run_script("install.sh", "--name", "demo", "--relay-name", "xiaozhushou", "--grok-name", grok,
                                 "--yes", "--lang", "zh-CN")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = self.record()
        self.assertEqual((data["relay_name"], data["grok_name"]), ("xiaozhushou", grok))
        self.assertNotIn(grok, self.env_file().read_text())
        self.assertEqual(self.state_cmd("show-prompt", "--lang", "zh-CN").stdout,
                         render_prompt("routine", "zh-CN", relay_name="xiaozhushou"))
        templates = self.home / ".local/lib/agent-mesh/templates"
        en = (templates / "AGENTS.reason-network.md").read_text()
        zh = (templates / "AGENTS.reason-network.zh-CN.md").read_text()
        self.assertIn(f"- This team's Grok Bot address: `demo/xiaozhushou` (called “{grok}” in Grok).\n", en)
        self.assertIn(f"- 本团队 Grok Bot 的地址：`demo/xiaozhushou`（在 Grok 里叫「{grok}」）。\n", zh)
        for text, name in ((en, "AGENTS.reason-network.md"), (zh, "AGENTS.reason-network.zh-CN.md")):
            source = (ROOT / "templates" / name).read_text().splitlines()
            lines = text.splitlines()
            self.assertEqual(len(lines), len(source), name)
            self.assertEqual([i for i, (a, b) in enumerate(zip(lines, source)) if a != b],
                             [i for i, line in enumerate(source) if "<relay-name>" in line], name)
        result = self.run_script("install.sh", "--cloud-prompt")
        self.assertIn(f"Bot address: demo/xiaozhushou → Grok: {grok} (cloud sessions", result.stdout)
        self.assertIn(f"PASS  Bot address: demo/xiaozhushou → Grok: {grok}\n", self.run_script("check.sh").stdout)

    def test_grok_name_on_its_own_only_labels_an_existing_installation(self):
        result = self.run_script("install.sh", "--grok-name", "Nova")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no installation to label yet", result.stderr)
        self.assertIsNone(self.record())
        key = self.configured()
        before = self.env_file().read_bytes()
        calls = self.base / "calls"
        if calls.exists():
            calls.unlink()
        for bad, said in (("", "is empty"), (" Nova", "starts or ends with a space"), ("a\nb", "contains a line break"),
                          ("x" * 65, "is longer than 64")):
            result = self.run_script("install.sh", "--grok-name", bad)
            self.assertNotEqual(result.returncode, 0, bad)
            self.assertIn(f"--grok-name: the Bot's name in Grok {said}", result.stderr, repr(bad))
            self.assertIsNone(self.record(), bad)
        result = self.run_script("install.sh", "--rotate-key", "--grok-name", "Nova")
        self.assertEqual(result.returncode, 2)
        self.assertIsNone(self.record())
        result = self.run_script("install.sh", "--grok-name", "Nova")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Bot address: demo/bot → Grok: Nova", result.stdout)
        self.assertIn("reply_convention.py refresh", result.stdout)
        self.assertEqual(self.record(), {"schema": 1, "relay_name": "bot", "grok_name": "Nova"})
        result = self.run_script("install.sh", "--grok-name", "Nova 2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.record()["grok_name"], "Nova 2")
        self.assertIn("`demo/bot` (called “Nova 2” in Grok)",
                      (self.home / ".local/lib/agent-mesh/templates/AGENTS.reason-network.md").read_text())
        self.assertEqual(self.env_file().read_bytes(), before)
        self.assertEqual(key.read_text(), "a" * 64 + "\n")
        self.assertEqual(self.service_changes(), [])
        self.assertEqual(self.rn_calls(), [])
        path = self.home / ".local/lib/agent-mesh/deployed.json"
        path.write_text("{")
        result = self.run_script("install.sh", "--grok-name", "Nova 3")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("is unreadable", result.stderr)
        self.assertEqual(path.read_text(), "{")

    def test_reply_convention_refresh_replaces_only_the_block(self):
        self.configured(relay="alice")
        self.write_record({"schema": 1, "relay_name": "alice", "grok_name": "Alice"})
        script = ROOT / "scripts/reply_convention.py"

        def refresh(path):
            return subprocess.run([sys.executable, str(script), "refresh", str(path)], env=self.env,
                                  capture_output=True, text=True)

        agents = self.base / "AGENTS.md"
        template = (ROOT / "templates/AGENTS.reason-network.zh-CN.md").read_text()
        before, after = "# My rules\r\n\r\nKeep it short.\r\n\r\n", "\r\n## Other\r\nUser notes stay.\r\n"
        original = before + template.replace("\n", "\r\n") + after
        agents.write_bytes(original.encode())
        agents.chmod(0o640)
        result = refresh(agents)
        self.assertEqual(result.returncode, 0, result.stderr)
        placeholder = [line for line in template.splitlines() if "<relay-name>" in line]
        self.assertEqual(len(placeholder), 1)
        want = original.replace(placeholder[0], "- 本团队 Grok Bot 的地址：`demo/alice`（在 Grok 里叫「Alice」）。")
        self.assertEqual(agents.read_bytes().decode(), want)
        self.assertEqual(agents.stat().st_mode & 0o777, 0o640)
        self.assertIn("already shows", refresh(agents).stdout)
        for content in ("# no block\n", template + template,
                        template.replace("<!-- /agent-mesh:bot-address -->\n", ""),
                        template.replace("lang=zh-CN", "lang=fr")):
            agents.write_text(content)
            result = refresh(agents)
            self.assertEqual(result.returncode, 1, content[:40])
            self.assertIn("nothing was changed", result.stderr)
            self.assertEqual(agents.read_text(), content)
        result = subprocess.run([sys.executable, str(script), "render", "--lang", "en"], env=self.env,
                                capture_output=True, text=True)
        self.assertIn("`demo/alice` (called “Alice” in Grok).", result.stdout)

    def test_update_refreshes_the_filled_in_templates_but_plan_does_not(self):
        repo = self.installed(relay="alice")
        templates = self.home / ".local/lib/agent-mesh/templates"
        shutil.rmtree(templates, ignore_errors=True)
        result = self.run_script("update.sh", "--no-pull", "--plan", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(templates.exists())
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("reply convention with this Bot's address", result.stdout)
        self.assertIn("`demo/alice`.", (templates / "AGENTS.reason-network.md").read_text())
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertNotIn("reply convention with this Bot's address", result.stdout)
        self.assertIn("already up to date", result.stdout)
        self.assertEqual(self.service_changes(), [])

    def test_grok_name_with_webhook_or_cloud_prompt_needs_an_installation(self):
        for args in (("--webhook",), ("--cloud-prompt",)):
            result = self.run_script("install.sh", *args, "--grok-name", "Nova")
            self.assertNotEqual(result.returncode, 0, args)
            self.assertIsNone(self.record(), args)
        self.engine()
        for args, said in ((("--webhook",), "team or signing key missing"),
                           (("--cloud-prompt",), "no installation to label yet")):
            result = self.run_script("install.sh", *args, "--grok-name", "Nova")
            self.assertNotEqual(result.returncode, 0, args)
            self.assertIn(said, result.stderr, args)
            self.assertIsNone(self.record(), args)
        result = self.run_script("install.sh", "--name", "demo", "--relay-name", "nova", "--grok-name", "Nova",
                                 "--cloud-prompt")
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(self.record())

    def test_webhook_with_grok_name_checks_its_prerequisites_before_recording(self):
        key = self.configured(relay="alice")
        self.write_record({"schema": 1, "relay_name": "alice", "grok_name": "Old"})
        key.unlink()
        result = self.run_script("install.sh", "--webhook", "--grok-name", "New")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("team or signing key missing", result.stderr)
        self.assertEqual(self.record()["grok_name"], "Old")

    def test_full_install_with_grok_name_refuses_an_unreadable_record_before_any_change(self):
        self.fresh()
        path = self.home / ".local/lib/agent-mesh/deployed.json"
        for content in ("{", json.dumps({"schema": 99, "relay_name": "bot"})):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            result = self.run_script("install.sh", "--name", "demo", "--relay-name", "nova", "--grok-name", "Nova",
                                     "--yes")
            self.assertNotEqual(result.returncode, 0, content)
            self.assertIn("--grok-name:", result.stderr, content)
            self.assertEqual(path.read_text(), content)
            self.assertFalse(self.env_file().exists(), content)
            self.assertEqual(self.rn_calls(), [], content)
            self.assertNotIn("curl", self.calls(), content)

    def test_grok_names_starting_with_a_hyphen_are_stored(self):
        self.configured()
        for name in ("-Nova", "--help", "-- x"):
            result = self.run_script("install.sh", "--grok-name", name)
            self.assertEqual(result.returncode, 0, (name, result.stdout + result.stderr))
            self.assertEqual(self.record()["grok_name"], name)
            self.assertIn(f"Bot address: demo/bot → Grok: {name}", result.stdout)

    def test_cloud_prompt_with_grok_name_refreshes_the_filled_in_templates(self):
        self.configured(relay="alice")
        self.write_record({"schema": 1, "relay_name": "alice", "grok_name": "Old"})
        result = self.run_script("install.sh", "--cloud-prompt", "--grok-name", "New")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.record()["grok_name"], "New")
        self.assertIn("Bot address: demo/alice → Grok: New (cloud sessions", result.stdout)
        text = (self.home / ".local/lib/agent-mesh/templates/AGENTS.reason-network.md").read_text()
        self.assertIn("`demo/alice` (called “New” in Grok).", text)

    def test_check_reports_an_unreadable_record_instead_of_no_address(self):
        self.engine()
        path = self.home / ".local/lib/agent-mesh/deployed.json"
        path.parent.mkdir(parents=True)
        path.write_text("{")
        out = self.run_script("check.sh").stdout
        self.assertIn("FAIL  Bot address", out)
        self.assertIn("is unreadable", out)
        self.assertNotIn("no address name chosen yet", out)
        self.assertIn("SKIP  relay registration (the Bot address name is not known)", out)

    def test_reply_convention_refresh_keeps_the_file_and_refuses_what_it_cannot_change(self):
        self.configured(relay="alice")
        script = ROOT / "scripts/reply_convention.py"

        def refresh(path):
            return subprocess.run([sys.executable, str(script), "refresh", str(path)], env=self.env,
                                  capture_output=True, text=True)

        template = (ROOT / "templates/AGENTS.reason-network.md").read_text()
        agents = self.base / "AGENTS.md"
        agents.write_text(template)
        link = self.base / "hardlink.md"
        os.link(agents, link)
        inode = agents.stat().st_ino
        result = refresh(agents)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(agents.stat().st_ino, inode)
        self.assertIn("`demo/alice`.", link.read_text())
        agents.write_text(template)
        agents.chmod(0o444)
        result = refresh(agents)
        if os.geteuid() != 0:
            self.assertEqual(result.returncode, 1)
            self.assertEqual(agents.read_text(), template)
        agents.chmod(0o644)
        bad = template.encode() + b"\xff\n"
        agents.write_bytes(bad)
        result = refresh(agents)
        self.assertEqual(result.returncode, 1)
        self.assertIn("is not UTF-8 text; nothing was changed", result.stderr)
        self.assertEqual(agents.read_bytes(), bad)
        agents.write_text(template)
        symlink = self.base / "symlink.md"
        symlink.symlink_to(agents)
        result = refresh(symlink)
        self.assertEqual(result.returncode, 1)
        self.assertIn("is not a regular file; nothing was changed", result.stderr)
        self.assertEqual(agents.read_text(), template)
        result = refresh(self.base)
        self.assertEqual(result.returncode, 1)

    def test_update_still_finishes_when_the_filled_in_templates_cannot_be_written(self):
        repo = self.installed(relay="alice")
        templates = self.home / ".local/lib/agent-mesh/templates"
        shutil.rmtree(templates, ignore_errors=True)
        templates.write_text("not a directory")
        result = self.run_script("update.sh", "--no-pull", root=repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("note: could not refresh the reply convention", result.stdout)
        self.assertIn("already up to date", result.stdout)
        self.assertEqual(templates.read_text(), "not a directory")

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

    def fake_daemon(self):
        home = self.home / ".reason/network"
        home.mkdir(parents=True, exist_ok=True)
        code = ("import fcntl, os, sys, time\n"
                "f = open(sys.argv[1], 'a+')\n"
                "fcntl.flock(f, fcntl.LOCK_EX)\n"
                "f.truncate(0); f.write(str(os.getpid())); f.flush()\n"
                "print('locked', flush=True)\n"
                "time.sleep(300)\n")
        proc = subprocess.Popen([sys.executable, "-c", code, str(home / "daemon.lock")], stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (proc.kill(), proc.wait(), proc.stdout.close()))
        self.assertEqual(proc.stdout.readline().strip(), "locked")
        return proc

    def write_marker(self, name, pid=None):
        marker = self.home / "Library/Application Support/agent-relay/registered"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"name": name, "pid": pid or os.getpid(), "at": int(time.time())}))
        return marker

    def test_check_reports_the_relay_name_and_its_registration(self):
        self.configured(relay="alice")
        (self.state / "loaded").write_text("")
        (self.state / "pid").write_text(str(os.getpid()))
        text = self.who / "text"
        now = int(time.time() * 1000)

        def check():
            return self.run_script("check.sh").stdout

        out = check()
        self.assertIn("PASS  Bot address: demo/alice (no Grok name recorded;", out)
        self.assertIn("FAIL  relay not registered on the running engine: no engine daemon is running", out)
        self.fake_daemon()
        self.assertIn("SKIP  relay registration (the engine daemon or the relay restarted", check())
        time.sleep(1.1)
        marker = self.write_marker("demo/alice")
        text.write_text("  alice  cli  ~/work  idle\n  repo-claude  claude-code  ~/repo  idle\n")
        out = check()
        self.assertIn("PASS  relay listening as demo/alice (registered on the running engine daemon)", out)
        self.assertIn("PASS  1 agent session(s) on the roster", out)
        self.write_marker("demo/alice-2")
        self.assertIn("FAIL  relay not registered on the running engine: the relay registered as demo/alice-2, "
                      "not demo/alice", check())
        marker.write_text("{")
        self.assertIn("is unreadable", check())
        self.write_marker("demo/alice")
        db = self.sessions([("alice", "cli:alice", now)])
        self.assertIn("PASS  relay listening as demo/alice", check())
        for who in ("  alice  cli  ~/work  idle\n", ""):
            text.write_text(who)
            self.sessions([("alice-2", "cli:alice", now + 1)])
            self.assertIn("FAIL  relay registered under another name: demo/alice-2", check(), who)
        db.unlink()
        self.assertIn("PASS  relay listening as demo/alice", check())
        (self.state / "dead").write_text("")
        self.assertIn("FAIL  relay not registered on the running engine: the relay service has no running process", check())
        (self.state / "dead").unlink()
        self.write_marker("demo/alice", pid=os.getppid())
        self.assertNotIn("PASS  relay listening", check())
        self.write_marker("demo/alice")
        env = self.env_file()
        env.write_text(env.read_text() + "LISTEN_NAME=x\n")
        out = check()
        self.assertIn("FAIL  relay names in env are not derived from one name", out)
        self.assertNotIn("0123456789", out)
        env.unlink()
        out = check()
        self.assertIn("SKIP  Bot address (no address name chosen yet", out)
        self.assertIn("SKIP  relay registration (the Bot address name is not known)", out)
        self.write_record({"schema": 1, "relay_name": "alice", "grok_name": "Alice"})
        out = check()
        self.assertIn("PASS  Bot address: demo/alice → Grok: Alice", out)
        self.assertIn("relay listening as demo/alice", out)
        env.write_text(f"RN_BIN={self.home}/.local/bin/reason-network\n")
        self.write_record({"schema": 1, "relay_name": "bot", "grok_name": "Nova"})
        self.assertIn("PASS  Bot address: demo/bot → Grok: Nova", check())

    def test_check_reads_team_lists_with_and_without_verified_names(self):
        self.configured()
        team = self.who / "team"
        head = "team demo · relay: Reason's (the default)\n"
        for v in ("", " (verified)"):
            with self.subTest(v=v):
                team.write_text(f"{head}  demo{v}  mac    this machine\n  demo{v}  cloud  direct 4ms\n")
                out = self.run_script("check.sh").stdout
                self.assertIn("PASS  cloud machine joined", out)
                self.assertIn("PASS  team holds only this machine and cloud", out)
                team.write_text(f"{head}  demo{v}  mac    this machine\n  demo{v}  cloud  direct 4ms\n"
                                f"  eve{v}   pc     offline 1h\n")
                self.assertIn("FAIL  unexpected team members: eve/pc", self.run_script("check.sh").stdout)
                team.write_text(f"{head}  demo{v}  mac    this machine\n")
                self.assertIn("SKIP  cloud machine", self.run_script("check.sh").stdout)

    def test_check_probes_the_relay_the_engine_actually_uses(self):
        self.configured()
        self.env["MOCK_VERSION"] = "0.10.7"
        out = self.run_script("check.sh").stdout
        self.assertIn("PASS  relay reachable: n0 public relays (Reason Network 0.10.7)", out)
        self.env["MOCK_VERSION"] = "0.12.17"
        self.tool("curl", 'echo "curl $*" >> "$CALL_LOG"; case "$*" in *https://aps1-1*) printf 200 ;; *) exit 73 ;; esac')
        log = self.home / ".reason/network/daemon.log"
        log.write_text("[1] net: on relay https://relay-sfo1.compute.reasonmachines.com/\n")
        out = self.run_script("check.sh").stdout
        self.assertIn("FAIL  relay not reachable (home relay from daemon.log (Reason Network 0.12.17)): "
                      "https://relay-sfo1.compute.reasonmachines.com/", out)
        self.assertIn("iroh.link and reasonmachines.com", out)
        log.write_text("[1] net: on relay https://aps1-1.relay.n0.iroh.link/\n")
        self.assertIn("PASS  relay reachable: home relay from daemon.log", self.run_script("check.sh").stdout)
        log.unlink()
        out = self.run_script("check.sh").stdout
        self.assertIn("SKIP  relay reachability unknown (unknown: Reason Network 0.12.17 has not logged", out)
        self.assertNotIn("relay not reachable", out)
        (self.who / "team").write_text("team demo · relay: https://aps1-1.relay.n0.iroh.link/\n"
                                       "  demo  mac  this machine\n")
        self.assertIn("PASS  relay reachable: the team's relay", self.run_script("check.sh").stdout)

    def test_install_probes_the_pinned_default_before_the_engine_exists(self):
        result = self.run_script("install.sh", "--name", "demo", "--relay-name", "nova", "--yes")
        self.assertIn("ok (default relays of Reason Network 0.12.17 (not installed yet))", result.stdout)
        self.assertIn("curl -sS -o /dev/null -m 10 -w %{http_code} https://relay-sfo1.compute.reasonmachines.com/",
                      self.calls())

    def test_check_judges_the_latest_webhook_post(self):
        self.configured()
        log = self.home / "Library/Logs/agent-relay.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        up = "2026-11-10 09:00:00,000 INFO agent-relay up"

        def post(code):
            return f"2026-11-10 09:00:01,000 INFO POST 120 chars 120 bytes sha256={'e' * 64} code={code} items=[m1]"

        failed = "2026-11-10 09:00:01,001 WARNING webhook POST failed: error URLError; 1 item(s) kept in spool"
        cases = [
            ("failure then success", [post("error URLError"), failed, post(200), "INFO woke Grok Bot with 1 item(s)"],
             "PASS  latest webhook POST succeeded (code=200; recovered after an earlier failure)", False),
            ("failure only", [post("error URLError"), failed], "FAIL  latest webhook POST failed (code=error URLError)",
             True),
            ("success only", [post(200)], "PASS  latest webhook POST succeeded (code=200)", False),
            ("redirect", [post(302), failed.replace("error URLError", "302")],
             "FAIL  latest webhook POST failed (code=302)", True),
            ("no POST lines", [], None, False),
            ("success then failure", [post(200), post("error URLError"), failed],
             "FAIL  latest webhook POST failed (code=error URLError)", True),
            ("success then 500", [post(200), post(500)], "FAIL  latest webhook POST failed (code=500)", True),
            ("success then redirect", [post(200), post(302)], "FAIL  latest webhook POST failed (code=302)", True),
            ("204", [post(204)], "PASS  latest webhook POST succeeded (code=204)", False),
            ("recovered long ago", [post("error URLError"), failed, post(200)] + ["INFO note"] * 60,
             "recovered after an earlier failure", False),
            ("success twice", [post("error URLError"), post(200), post(200)],
             "PASS  latest webhook POST succeeded (code=200)\n", False),
            ("only a failure warning", [failed], "FAIL  recent webhook POST failures", True),
        ]
        for label, lines, expected, fails in cases:
            with self.subTest(label):
                log.write_text("\n".join([up, *lines]) + "\n")
                out = self.run_script("check.sh").stdout
                if expected:
                    self.assertIn(expected, out)
                self.assertEqual("webhook POST failed" in out or "webhook POST failures" in out, fails, out)


TEAM_ID = "5f0c2a9be14d4c7aa3d1e2f30415a6b7"
OTHER_TEAM_ID = "0123456789abcdef0123456789abcdef"


def invite(team=TEAM_ID, cut=None):
    data = bytes.fromhex(team) + bytes(range(16)) + bytes([4]) + b"demo" + bytes([0, 0, 0]) + bytes(range(32))
    ticket = "tincan1" + base64.urlsafe_b64encode(data[:cut]).decode().rstrip("=")
    return f"join my Reason Network team as demo: https://reason.network/join/#{ticket}"


CLOUD_ENGINE = """#!/bin/sh
[ "$1" != --version ] || { echo "reason-network ${MOCK_CLOUD_VERSION:-0.12.17}"; exit 0; }
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

REGISTER = f'''import json, os, re, sqlite3, subprocess, sys, time
from pathlib import Path
home = Path(os.environ["HOME"])
name = os.environ.get("MOCK_REGISTER", "")
if name == "-":
    sys.exit(0)
skip = Path(os.environ["MOCK_LAUNCHD"]) / "noreg-when"
if skip.exists():
    out = subprocess.run([str(home / ".local/bin/reason-network"), "--version"], capture_output=True, text=True).stdout
    if skip.read_text().strip() in out.split():
        sys.exit(0)
listen = "bot"
env = home / ".config/agent-relay/env"
if env.exists():
    for line in env.read_text().splitlines():
        if line.startswith("LISTEN_NAME="):
            listen = line.split("=", 1)[1]
db = home / ".reason/network/network.db"
db.parent.mkdir(parents=True, exist_ok=True)
if not os.environ.get("MOCK_NO_DB"):
    con = sqlite3.connect(db)
    con.executescript("""{SESSIONS}""")
    con.execute("INSERT OR IGNORE INTO sessions VALUES (?, ?, 'cli', '/', NULL, NULL, NULL, 'pull', ?)",
                (name or listen, "cli:" + listen, int(time.time() * 1000)))
    con.commit()
if os.environ.get("MOCK_NO_MARKER"):
    sys.exit(0)
person = re.search(r'^person = "(.*)"', (home / ".reason/network/config.toml").read_text(), re.M).group(1)
marker = home / "Library/Application Support/agent-relay/registered"
marker.parent.mkdir(parents=True, exist_ok=True)
pid = int((Path(os.environ["MOCK_LAUNCHD"]) / "pid").read_text())
marker.write_text(json.dumps({{"name": f"{{person}}/{{name or listen}}", "pid": pid, "at": int(time.time())}}))
'''

RELEASE_GIT = r'''[ "$1" = -C ] && shift 2
S="$GIT_STATE"
case "$*" in
  "init -q --bare "*) [ $# = 4 ] ;;
  "fetch -q --depth 1 --no-tags "*) echo "git fetch $6 $7" >> "$CALL_LOG"
    [ ! -f "$S/release-fail" ] || { echo "fatal: couldn't find remote ref $7" >&2; exit 128; } ;;
  "rev-parse --verify -q refs/tags/"*) cat "$S/release-commit" ;;
  "cat-file blob "*) f="${3#*:}"; if [ -f "$S/release/$f" ]; then cat "$S/release/$f"; else cat "$RELEASE_ROOT/$f"; fi ;;
  *) echo "unexpected git call $*" >> "$CALL_LOG"; exit 99 ;;
esac'''

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
  "init -q --bare "*|"fetch -q --depth 1 --no-tags "*|"rev-parse --verify -q refs/tags/"*|"cat-file blob "*)
    exec /bin/sh "$RELEASE_GIT_STUB" "$@" ;;
  *) echo "unexpected git $*" >> "$CALL_LOG"; exit 99 ;;
esac'''

if __name__ == "__main__":
    unittest.main()
