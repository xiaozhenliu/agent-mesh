"""The release check and the cloud bash blocks against real git repositories in a temporary directory."""
import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from render_prompt import ROOT, render_prompt
from test_render_prompt import FIRST_UPDATE_PREFIX

CLOUD_FILES = ("box/rn-cloud.sh", "box/rn-fetch.py", "box/rn-cloud-setup.sh", "box/rn-engine.py")
TAG = "v" + (ROOT / "VERSION").read_text().strip()
UPDATE = "box/rn-cloud-update.sh"
REQ = "0123456789abcdef"
ENGINE_STEP = 'bash "$A/box/rn-cloud-setup.sh" --upgrade-engine </dev/null'


def rn_engine():
    spec = importlib.util.spec_from_file_location("rn_engine", ROOT / "box/rn-engine.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stubbed_update_script():
    build = next(iter(rn_engine().PINNED_BUILDS["0.12.17"].values()))
    lines = ["engine: reason-network 0.12.17 running (daemon pid 4321, started 2026-11-12T08:00:00Z)",
             f"engine: pinned build {build} at box/bin/engine/reason-network", "engine: launcher ok"]
    stub = "{ " + "; ".join(f"echo '{line}'" for line in lines) + "; [ ! -f \"$A/.git/engine-fails\" ]; } </dev/null"
    text = (ROOT / UPDATE).read_text()
    assert text.count(ENGINE_STEP) == 1
    return text.replace(ENGINE_STEP, stub)


@unittest.skipUnless(shutil.which("git") and shutil.which("sha256sum"), "needs git and sha256sum")
class ReleaseBlocks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(os.path.realpath(self.temp.name))
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "RN_", "REASON_NETWORK_"))}
        self.env.update(HOME=str(self.base), GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x",
                        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x", LC_ALL="C")
        self.src = self.base / "src"
        self.bare = self.base / "remote.git"
        self.url = self.bare.as_uri()
        self.git("init", "-q", "-b", "main", str(self.src))
        self.write_files(old=True)
        self.git("-C", str(self.src), "add", "-A")
        self.git("-C", str(self.src), "commit", "-q", "-m", "old")
        self.git("-C", str(self.src), "tag", "v0.0.1")
        self.old = self.rev(self.src, "HEAD")
        self.write_files(old=False)
        self.git("-C", str(self.src), "commit", "-q", "-am", "release")
        self.git("-C", str(self.src), "tag", "-a", "-m", "release", TAG)
        self.commit = self.rev(self.src, "HEAD")
        self.git("clone", "-q", "--bare", str(self.src), str(self.bare))
        self.sums = "".join(f"{hashlib.sha256((ROOT / n).read_bytes()).hexdigest()}  {n}\n" for n in CLOUD_FILES)
        self.ws = self.base / "workspace"
        self.ws.mkdir()
        self.clone = self.ws / "agent-mesh"

    def git(self, *args, cwd=None, check=True):
        return subprocess.run(["git", *args], cwd=cwd, env=self.env, capture_output=True, text=True, check=check,
                              timeout=60)

    def rev(self, repo, ref):
        return self.git("-C", str(repo), "rev-parse", ref).stdout.strip()

    def write_files(self, old):
        for name in CLOUD_FILES:
            path = self.src / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / name).read_bytes() + (b"\n# old\n" if old else b""))
        (self.src / UPDATE).write_text((ROOT / UPDATE).read_text() if old else stubbed_update_script())
        (self.src / UPDATE).chmod(0o755)
        (self.src / "README.md").write_text("old\n" if old else "release\n")
        (self.src / "VERSION").write_text("0.0.1\n" if old else TAG[1:] + "\n")

    def retag(self, update_text=None, version=None, tag=TAG):
        if update_text is not None:
            (self.src / UPDATE).write_text(update_text)
        if version is not None:
            (self.src / "VERSION").write_text(version + "\n")
        self.git("-C", str(self.src), "commit", "-q", "-am", f"retag {tag}")
        self.git("-C", str(self.src), "tag", "-f", "-a", "-m", "release", tag)
        self.git("-C", str(self.src), "push", "-q", "-f", str(self.bare), f"refs/tags/{tag}")
        commit = self.rev(self.src, "HEAD")
        if tag == TAG:
            self.commit = commit
        return commit

    def existing_clone(self, branch=False):
        self.git("clone", "-q", self.url, str(self.clone))
        if branch:
            self.git("-C", str(self.clone), "reset", "-q", "--hard", "v0.0.1")
        else:
            self.git("-C", str(self.clone), "checkout", "-q", "--detach", "v0.0.1")

    def tampered(self, i):
        lines = self.sums.splitlines(True)
        lines[i] = "0" * 64 + lines[i][64:]
        return "".join(lines)

    def head(self):
        return self.rev(self.clone, "HEAD")

    def run_block(self, text):
        text = text.replace("A=/workspace/agent-mesh;", f"A={self.clone};")
        text = text.replace("/workspace/.agent-mesh-setup.XXXXXX", f"{self.ws}/.agent-mesh-setup.XXXXXX")
        return subprocess.run(["bash", "-c", text], cwd=self.base, env=self.env, capture_output=True, text=True,
                              timeout=120)

    def update_block(self, commit=None, sums=None, message=False, call="cloud_step"):
        harness = f'''set -u
. "{ROOT}/scripts/msg.sh"
state() {{ :; }}
read_reply() {{ REPLY_TEXT=""; }}
die() {{ exit 0; }}
REL_TAG={TAG}; REL_COMMIT={commit or self.commit}; S_EPIN=0.12.17
REL_SUMS="$(cat "$1")"$'\\n'
eval "$(cat "$2")"
new_req() {{ echo {REQ}; }}
{call}
'''
        sums_file = self.base / "sums"
        sums_file.write_text(self.sums if sums is None else sums)
        script = (ROOT / "update.sh").read_text()
        func = self.base / "cloud_step.sh"
        parts = []
        for name, end in (("cloud_block", "prompt_text"), ("bootstrap_message", "cloud_auto")):
            start = script.index(f"\n{name}() {{") + 1
            parts.append(script[start:script.index(f"\n{end}() {{", start) + 1])
        func.write_text("".join(parts))
        out = subprocess.run(["bash", "-c", harness, "harness", str(sums_file), str(func)],
                             env=self.env, capture_output=True, text=True, timeout=60, check=True).stdout
        if message:
            return out
        start = out.index("bash -s <<'AGENT_MESH_UPDATE'")
        return out[start:out.index("\nAGENT_MESH_UPDATE\n", start) + len("\nAGENT_MESH_UPDATE\n")]

    def setup_block(self, url=None, commit=None, sums=None):
        text = render_prompt("cloud-setup", team_name="demo", mac_home="/Users/demo", repo_url=url or self.url,
                             release_tag=TAG, release_commit=commit or self.commit,
                             cloud_checksums=self.sums if sums is None else sums)
        start = text.index("bash -s <<'AGENT_MESH_SETUP'")
        return text[start:text.index("\nAGENT_MESH_SETUP\n", start) + len("\nAGENT_MESH_SETUP\n")]

    def release(self, url=None):
        return subprocess.run([sys.executable, str(ROOT / "scripts/deploy_state.py"), "release", "--repo-url",
                               url or self.url], env=self.env, capture_output=True, text=True, timeout=120)

    def staging(self):
        return sorted(p.name for p in self.ws.iterdir() if p.name.startswith(".agent-mesh-setup."))

    def test_release_check_reads_the_tag_commit_and_checksums(self):
        self.retag((ROOT / UPDATE).read_text())
        result = self.release()
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[:2], [f"tag {TAG}", f"commit {self.commit}"])
        self.assertEqual(lines[2:], ["sum " + line for line in self.sums.splitlines()])

    def test_release_check_fails_when_only_the_update_script_differs(self):
        result = self.release()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn(f"{UPDATE} in this checkout differs from the release {TAG}", result.stderr)
        self.assertEqual(result.stdout, "")
        self.retag((ROOT / UPDATE).read_text())
        self.assertEqual(self.release().returncode, 0)

    def test_release_check_fails_for_a_missing_tag_or_different_files(self):
        self.retag((ROOT / UPDATE).read_text())
        self.git("-C", str(self.bare), "tag", "-d", TAG)
        result = self.release()
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"the release {TAG} could not be fetched from {self.url}", result.stderr)
        (self.src / "box/rn-fetch.py").write_text("changed\n")
        self.git("-C", str(self.src), "commit", "-q", "-am", "other")
        self.git("-C", str(self.src), "push", "-q", str(self.bare), f"HEAD:refs/tags/{TAG}")
        result = self.release()
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"box/rn-fetch.py in this checkout differs from the release {TAG}", result.stderr)
        result = self.release(url=(self.base / "missing.git").as_uri())
        self.assertEqual(result.returncode, 1)
        self.assertIn("could not be fetched", result.stderr)

    def test_update_block_moves_a_clean_clone_on_a_branch_to_the_release(self):
        self.existing_clone(branch=True)
        self.assertEqual(self.git("-C", str(self.clone), "symbolic-ref", "--short", "HEAD").stdout.strip(), "main")
        result = self.run_block(self.update_block())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], f"agent-mesh-result DONE {TAG}")
        self.assertEqual(self.head(), self.commit)
        self.assertNotEqual(self.git("-C", str(self.clone), "symbolic-ref", "-q", "HEAD", check=False).returncode, 0)

    def test_update_block_moves_a_clean_clone_to_the_release(self):
        self.existing_clone()
        result = self.run_block(self.update_block())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotRegex(result.stdout, r"(?m)^STOP:")
        self.assertIn(f"agent-mesh-result commit {self.commit}\n", result.stdout)
        self.assertEqual(result.stdout.splitlines()[-1], f"agent-mesh-result DONE {TAG}")
        for line in self.sums.splitlines():
            self.assertIn(f"agent-mesh-result sum {line}\n", result.stdout)
        self.assertEqual(self.head(), self.commit)
        self.assertEqual(self.git("-C", str(self.clone), "status", "--porcelain").stdout, "")
        again = self.run_block(self.update_block())
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn(f"DONE {TAG}", again.stdout)

    def reply_check(self, text):
        return subprocess.run([sys.executable, str(ROOT / "scripts/deploy_state.py"), "cloud-reply", "--commit",
                               self.commit, "--tag", TAG], input=text, env=self.env, capture_output=True, text=True,
                              timeout=60)

    def test_only_the_executed_block_makes_a_reply_that_is_recorded(self):
        self.existing_clone()
        message = self.update_block(message=True)
        self.assertNotIn("agent-mesh-result commit", message)
        self.assertNotIn(f"agent-mesh-result DONE {TAG}", message)
        echoed = [message, f"Sure, here it is:\n{message}\nDONE {TAG}\n",
                  message + f"The command failed before agent-mesh-result DONE {TAG}\n"]
        for text in echoed:
            with self.subTest(text=text[-80:]):
                result = self.reply_check(text)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("mismatch:", result.stdout)
        self.assertEqual(self.head(), self.old)
        ran = self.run_block(self.update_block())
        self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
        for text in (ran.stdout, f"Done. Previously at {self.old}.\n```\n{ran.stdout}```\n" + message):
            result = self.reply_check(text)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = self.reply_check(ran.stdout.replace(f"agent-mesh-result DONE {TAG}", f"DONE {TAG}"))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def assert_stopped(self, result, why, head=None):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"(?m)^STOP: .*" + re.escape(why))
        self.assertNotIn(f"DONE {TAG}", result.stdout)
        if head is not None:
            self.assertEqual(self.head(), head)

    def test_update_block_stops_before_changing_anything(self):
        cases = {
            "local changes": (lambda: (self.clone / "README.md").write_text("mine\n"), {}, "has local changes"),
            "untracked file": (lambda: (self.clone / "box/extra").write_text("x\n"), {}, "has local changes"),
            "tag missing": (lambda: self.git("-C", str(self.bare), "tag", "-d", TAG), {}, f"could not fetch {TAG}"),
            "other local tag": (lambda: self.git("-C", str(self.clone), "tag", "-f", TAG, "v0.0.1"), {},
                                f"could not fetch {TAG}, or a different tag"),
            "other commit": (lambda: None, lambda: {"commit": self.old}, None),
            **{f"wrong checksum {i}": (lambda: None, lambda i=i: {"sums": self.tampered(i)},
                                       f"{CLOUD_FILES[i]} in") for i in range(4)},
            "short list": (lambda: None, lambda: {"sums": "".join(self.sums.splitlines(True)[:3])},
                           "the checksum list is incomplete"),
        }
        for label, (prepare, kwargs, why) in cases.items():
            with self.subTest(label):
                self.setUp()
                self.existing_clone()
                before = self.head()
                prepare()
                result = self.run_block(self.update_block(**(kwargs() if callable(kwargs) else kwargs)))
                self.assert_stopped(result, why or f"points to {self.commit}, not {self.old}", before)

    def test_update_block_without_a_clone_or_with_a_non_git_directory_stops(self):
        result = self.run_block(self.update_block())
        self.assert_stopped(result, "no clone at")
        self.clone.mkdir()
        result = self.run_block(self.update_block())
        self.assert_stopped(result, "is not a git clone")

    def test_update_block_restores_the_clone_when_the_checked_out_files_differ(self):
        self.existing_clone()
        self.git("-C", str(self.clone), "config", "core.autocrlf", "true")
        result = self.run_block(self.update_block())
        self.assert_stopped(result, f"the files after checking out {TAG} do not match it", self.old)
        self.assertIn(f"restored the clone to {self.old}", result.stdout)
        self.assertEqual(self.git("-C", str(self.clone), "status", "--porcelain").stdout, "")

    def test_update_block_reports_a_clone_it_could_not_restore(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores directory permissions")
        self.existing_clone()
        box = self.clone / "box"
        box.chmod(0o555)
        self.addCleanup(box.chmod, 0o755)
        result = self.run_block(self.update_block())
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"(?m)^STOP: ")
        self.assertIn("do not run any script in", result.stdout)
        self.assertNotIn(f"DONE {TAG}", result.stdout)

    def test_update_block_with_the_engine_step_stops_when_the_step_fails(self):
        self.retag((ROOT / UPDATE).read_text())
        self.existing_clone()
        block = self.update_block()
        self.assertIn('exec bash "$A/box/rn-cloud-update.sh" --after-checkout "$TAG" "$COMMIT" "$REQ"', block)
        tools = self.base / "tools"
        tools.mkdir()
        (tools / "uname").write_text("#!/bin/sh\necho Darwin\n")
        (tools / "uname").chmod(0o755)
        self.env["PATH"] = f"{tools}{os.pathsep}{self.env['PATH']}"
        result = self.run_block(block)
        self.assert_stopped(result, "the engine step failed", self.commit)
        self.assertIn("requires Linux", result.stdout + result.stderr)

    def run_update(self, *args, script=None):
        script = script or self.clone / UPDATE
        return subprocess.run(["bash", str(script), *args], cwd=self.base, env=self.env, capture_output=True,
                              text=True, timeout=120)

    def engine_reply_check(self, text):
        return subprocess.run([sys.executable, str(ROOT / "scripts/deploy_state.py"), "cloud-reply", "--commit",
                               self.commit, "--tag", TAG, "--engine"], input=text, env=self.env,
                              capture_output=True, text=True, timeout=60)

    def newer_clone(self):
        self.write_files(old=False)
        newer = self.retag(version="99.0.0", tag="v99.0.0")
        self.git("clone", "-q", self.url, str(self.clone))
        self.git("-C", str(self.clone), "checkout", "-q", "--detach", newer)
        return newer

    def test_update_block_reports_the_request_first_and_never_downgrades(self):
        self.existing_clone()
        (self.clone / "README.md").write_text("mine\n")
        result = self.run_block(self.update_block())
        self.assertEqual(result.stdout.splitlines()[0], f"agent-mesh-result request {REQ}")
        self.assert_stopped(result, "has local changes", self.old)
        shutil.rmtree(self.clone)
        newer = self.newer_clone()
        result = self.run_block(self.update_block())
        self.assertEqual(result.stdout.splitlines()[0], f"agent-mesh-result request {REQ}")
        self.assert_stopped(result, f"this clone is at 99.0.0, newer than {TAG}", newer)
        self.assertNotIn("origin:", result.stdout)

    def test_first_update_message_explains_and_carries_the_block(self):
        text = self.update_block(message=True, call=f"bootstrap_message {REQ}")
        self.assertTrue(text.startswith(FIRST_UPDATE_PREFIX))
        for lang in ("en", "zh-CN"):
            self.assertIn(FIRST_UPDATE_PREFIX, render_prompt("routine", lang))
        self.assertIn(f"REQ={REQ}", text)
        self.assertTrue(text.rstrip("\n").endswith("AGENT_MESH_UPDATE"))

    def test_update_script_runs_the_new_checkouts_copy_and_its_reply_is_recorded(self):
        self.existing_clone()
        self.assertEqual((self.clone / UPDATE).read_text(), (ROOT / UPDATE).read_text())
        result = self.run_update(TAG, self.commit, REQ)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], f"agent-mesh-result request {REQ}")
        self.assertEqual(lines[-1], f"agent-mesh-result DONE {TAG}")
        self.assertIn("engine: launcher ok", lines)
        self.assertEqual(self.head(), self.commit)
        self.assertEqual(self.engine_reply_check(result.stdout).returncode, 0)
        again = self.run_update(TAG, self.commit, REQ)
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(self.engine_reply_check(again.stdout).returncode, 0)

    def test_update_script_stops_before_changing_anything(self):
        cases = {
            "bad tag": (lambda: None, ("1.2.3", None, REQ), "the tag must look like v1.2.3"),
            "bad commit": (lambda: None, (TAG, "ABC", REQ), "the commit must be 40 lowercase hex"),
            "uppercase commit": (lambda: None, (TAG, "upper", REQ), "the commit must be 40 lowercase hex"),
            "bad request": (lambda: None, (TAG, None, "xyz; rm"), "the request id must be 8 to 32"),
            "local changes": (lambda: (self.clone / "README.md").write_text("mine\n"), (TAG, None, REQ),
                              "has local changes"),
            "other commit": (lambda: None, (TAG, "old", REQ), "points to"),
            "other local tag": (lambda: self.git("-C", str(self.clone), "tag", "-f", TAG, "v0.0.1"), (TAG, None, REQ),
                                f"could not fetch {TAG}, or a different tag"),
            "tag missing": (lambda: self.git("-C", str(self.bare), "tag", "-d", TAG), (TAG, None, REQ),
                            f"could not fetch {TAG}"),
        }
        for label, (prepare, args, why) in cases.items():
            with self.subTest(label):
                self.setUp()
                self.existing_clone()
                prepare()
                tag, commit, req = args
                commit = {None: self.commit, "old": self.old, "upper": self.commit.upper()}.get(commit, commit)
                result = self.run_update(tag, commit, req)
                self.assert_stopped(result, why, self.old)
                self.assertEqual("agent-mesh-result request" in result.stdout, label != "bad request")
        self.setUp()
        self.existing_clone()
        (self.clone / "box/extra").write_text("x\n")
        result = self.run_update(TAG, self.commit, REQ)
        self.assertIn("changed files in", result.stdout)
        self.assertIn("?? box/extra", result.stdout)
        self.assertEqual(self.run_update(TAG, self.commit).returncode, 2)

    def test_update_script_never_downgrades(self):
        newer = self.newer_clone()
        result = self.run_update(TAG, self.commit, REQ)
        self.assert_stopped(result, "newer than", newer)

    def test_update_script_keeps_the_new_checkout_when_the_engine_step_fails(self):
        self.existing_clone()
        (self.clone / ".git/engine-fails").write_text("")
        result = self.run_update(TAG, self.commit, REQ)
        self.assert_stopped(result, f"the engine step failed (see the lines above); the clone stays at {TAG}",
                            self.commit)
        self.assertEqual(self.engine_reply_check(result.stdout).returncode, 1)
        (self.clone / ".git/engine-fails").unlink()
        result = self.run_update(TAG, self.commit, REQ)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_setup_block_clones_the_release_into_a_new_directory(self):
        result = self.run_block(self.setup_block())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], f"READY {TAG}")
        self.assertEqual(self.head(), self.commit)
        self.assertEqual(self.staging(), [])
        self.assertEqual((self.clone / "box/rn-cloud.sh").read_bytes(), (ROOT / "box/rn-cloud.sh").read_bytes())
        again = self.run_block(self.setup_block())
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("origin: ", again.stdout)
        self.assertEqual(again.stdout.splitlines()[-1], f"READY {TAG}")

    def test_setup_block_failures_leave_nothing_and_a_retry_works(self):
        cases = {
            "clone fails": ({"url": (self.base / "missing.git").as_uri()}, "git clone"),
            "other commit": ({"commit": self.old}, f"points to {self.commit}, not {self.old}"),
            "wrong checksum": ({"sums": self.sums.replace(self.sums[:64], "0" * 64)}, "box/rn-cloud.sh in"),
        }
        for label, (kwargs, why) in cases.items():
            with self.subTest(label):
                result = self.run_block(self.setup_block(**kwargs))
                self.assert_stopped(result, why)
                self.assertIn("nothing was kept", result.stdout)
                self.assertFalse(os.path.lexists(self.clone))
                self.assertEqual(self.staging(), [])
        self.assertNotIn(f"READY {TAG}", result.stdout)
        self.git("-C", str(self.bare), "tag", "-d", TAG)
        result = self.run_block(self.setup_block())
        self.assert_stopped(result, f"could not fetch {TAG}")
        self.assertFalse(os.path.lexists(self.clone))
        self.assertEqual(self.staging(), [])
        self.git("-C", str(self.src), "push", "-q", str(self.bare), f"refs/tags/{TAG}")
        result = self.run_block(self.setup_block())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], f"READY {TAG}")
        self.assertEqual(self.head(), self.commit)
        self.assertEqual(self.staging(), [])

    def test_setup_block_on_an_existing_clone_follows_the_update_rules(self):
        self.existing_clone()
        (self.clone / "README.md").write_text("mine\n")
        result = self.run_block(self.setup_block())
        self.assert_stopped(result, "has local changes", self.old)
        self.git("-C", str(self.clone), "checkout", "-q", "--", "README.md")
        self.git("-C", str(self.clone), "config", "core.autocrlf", "true")
        result = self.run_block(self.setup_block())
        self.assert_stopped(result, "do not match it", self.old)
        self.assertIn(f"restored {self.clone} to {self.old}", result.stdout)
        self.clone.rename(self.base / "aside")
        self.clone.mkdir()
        result = self.run_block(self.setup_block())
        self.assert_stopped(result, "exists but is not a git clone")

    def test_an_unfilled_setup_block_stops_at_once(self):
        text = render_prompt("cloud-setup", team_name="demo", repo_url=self.url)
        start = text.index("bash -s <<'AGENT_MESH_SETUP'")
        block = text[start:text.index("\nAGENT_MESH_SETUP\n", start) + len("\nAGENT_MESH_SETUP\n")]
        result = self.run_block(block)
        self.assert_stopped(result, "this block was not filled in")
        self.assertFalse(os.path.lexists(self.clone))


if __name__ == "__main__":
    unittest.main()
