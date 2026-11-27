import fcntl, hashlib, json, os, shutil, sqlite3, subprocess, sys, tempfile, time, unittest
from pathlib import Path

from tests.cloud.bot_support import FAKE_ENGINE, SESSIONS, TEAM_HASH, pin_file
from tests.cloud.support import HERE, KEY, SCHEMA, rr
from tests.helpers.stub_runner import StubRunner, last_line

ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "scripts"))
from render_prompt import CLOUD_FILES, prompt_block, render_prompt  # noqa: E402

UPDATE = "box/rn-cloud-update.sh"
VERSION = (ROOT / "VERSION").read_text().strip()
TAG = f"v{VERSION}"
NEXT = "v99.0.0"
ENGINE_STEP = 'bash "$A/box/rn-cloud-setup.sh" --upgrade-engine </dev/null'
SEND_ENGINE = FAKE_ENGINE + r'''elif args[:1] == ["send"]:
    with open(os.environ["FAKE_SENT"], "a") as fh:
        fh.write(json.dumps({"to": args[1], "envelope": sys.stdin.read()}) + "\n")
    print("→ queued")
'''
INSTALL = list(CLOUD_FILES) + [UPDATE, "box/rn-cloud-uninstall.sh", ".gitignore"]


def stubbed(text):
    assert text.count(ENGINE_STEP) == 1
    return text.replace(ENGINE_STEP, "echo 'engine: ok' </dev/null")


def req(made, tail):
    return f"{made:08x}{tail}"


NOW = int(time.time())
R, RX, RA, RB, RC, RE, RF = (req(NOW, tail) for tail in ("89abcdef", "89abcdee", "aaaaaaaa", "bbbbbbbb", "cccccccc",
                                                         "eeeeeeee", "ffffffff"))


@unittest.skipUnless(shutil.which("git") and shutil.which("sha256sum"), "needs git and sha256sum")
class CloudClone(unittest.TestCase):
    def setUp(self):
        self.d = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.genv = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "RN_", "REASON_NETWORK_",
                                                                                "FAKE_"))}
        self.genv.update(HOME=str(self.d), GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x",
                         GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@x", LC_ALL="C")
        self.src, self.bare = self.d / "src", self.d / "remote.git"
        self.git("init", "-q", "-b", "main", str(self.src))
        self.commits = {}
        self.release(TAG, self.files())
        self.git("clone", "-q", "--bare", str(self.src), str(self.bare))
        self.root = self.d / "workspace" / "agent-mesh"
        self.git("clone", "-q", self.bare.as_uri(), str(self.root))
        self.git("-C", str(self.root), "checkout", "-q", "--detach", TAG)
        self.box = self.root / "box"
        (self.box / "state").mkdir()
        (self.box / "person").write_text("demo\n")
        (self.box / "outbound.key").write_text(KEY + "\n")
        engine = self.box / "bin" / "engine" / "reason-network"
        engine.parent.mkdir(parents=True)
        engine.write_text(SEND_ENGINE.replace("#!/usr/bin/env python3", "#!" + sys.executable, 1))
        engine.chmod(0o755)
        pin_file(self.d / "builds.json", engine)
        db = sqlite3.connect(self.box / "state" / "network.db")
        db.executescript(SCHEMA + SESSIONS + "CREATE TABLE outbox (id TEXT);")
        db.close()
        tools = self.d / "tools"
        tools.mkdir()
        for name, script in (("uname", 'if [ "${1:-}" = -m ]; then echo x86_64; else echo Linux; fi'),
                             ("sleep", "exit 0"), ("timeout", 'shift; exec "$@"')):
            (tools / name).write_text("#!/bin/sh\n" + script + "\n")
            (tools / name).chmod(0o755)
        (tools / "python3").symlink_to(sys.executable)
        self.sent_log = self.d / "sent.log"
        self.env = dict(self.genv, PATH=f"{tools}{os.pathsep}{os.environ['PATH']}", RN_CLOUD_BASE=str(self.box),
                        FAKE_LOG=str(self.d / "rn.log"), FAKE_SENT=str(self.sent_log), RN_GOT="0", REFWD="0",
                        RN_PINNED_BUILDS_FILE=str(self.d / "builds.json"))
        self.n = 0

    def git(self, *args, check=True):
        return subprocess.run(["git", *args], env=self.genv, capture_output=True, text=True, check=check, timeout=60)

    def files(self, **changes):
        out = {name: (ROOT / name).read_bytes() for name in INSTALL}
        out["VERSION"] = (VERSION + "\n").encode()
        out.update(changes)
        return out

    def release(self, tag, files):
        for name, data in files.items():
            path = self.src / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if data is None:
                if path.exists():
                    path.unlink()
                continue
            path.write_bytes(data)
        self.git("-C", str(self.src), "add", "-A")
        self.git("-C", str(self.src), "commit", "-q", "--allow-empty", "-m", tag)
        self.git("-C", str(self.src), "tag", "-a", "-m", tag, tag)
        self.commits[tag] = self.git("-C", str(self.src), "rev-parse", "HEAD").stdout.strip()
        if self.bare.exists():
            self.git("-C", str(self.src), "push", "-q", str(self.bare), f"refs/tags/{tag}")

    def next_release(self, **changes):
        files = self.files(VERSION=b"99.0.0\n", **changes)
        files[UPDATE] = stubbed((ROOT / UPDATE).read_text()).encode()
        self.release(NEXT, files)
        return self.commits[NEXT]

    def message(self, text, frm="demo/mac", inbox="bot-inbox", mid=None):
        self.n += 1
        mid = mid or f"m{self.n:04d}abc"
        body = rr.fwd_body(KEY, mid, frm, "codex", "01JTHREAD", text)
        db = sqlite3.connect(self.box / "state" / "network.db")
        db.execute("INSERT INTO messages (id, sender, recipient, body, wake, sent_at) VALUES (?,?,?,?,1,?)",
                   (f"01RN{self.n:022d}", "demo/mac", f"demo/{inbox}", body.strip(), int(time.time() * 1000)))
        db.commit()
        db.close()
        return mid

    def cloud(self, *args, env=None, body=""):
        return subprocess.run(["bash", str(self.box / "rn-cloud.sh"), *args], env=dict(self.env, **(env or {})),
                              input=body, capture_output=True, text=True, timeout=300)

    def fetch(self, *ids, env=None):
        p = self.cloud("fetch", *ids, env=env)
        return {r["id"]: r for r in (json.loads(l) for l in p.stdout.splitlines() if l.startswith("{"))}

    def sent(self):
        if not self.sent_log.exists():
            return []
        out = []
        for line in self.sent_log.read_text().splitlines():
            env = json.loads(line)["envelope"]
            head, _, body = env.partition("\n---\n")
            fields = dict(l.split(": ", 1) for l in head.split("\n")[1:] if ": " in l)
            out.append((fields.get("to"), fields.get("reply_to"), body))
        return out

    def head(self):
        return self.git("-C", str(self.root), "rev-parse", "HEAD").stdout.strip()

    def tree_state(self):
        st = self.git("-C", str(self.root), "status", "--porcelain").stdout
        return self.head(), st, (self.box / "lang").exists()


class Instructions(CloudClone):
    def test_first_bot_rules(self):
        p = self.cloud("instructions", "1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        lines = p.stdout.rstrip("\n").split("\n")
        self.assertEqual(lines[0], f"agent-mesh-instructions BEGIN 1 {TAG} en -")
        self.assertEqual(lines[-1], "agent-mesh-instructions OK 1")
        self.assertIn("R1. Retrieve bodies. Copy each id from messages[] and run "
                      "/workspace/agent-mesh/box/rn-cloud.sh fetch <id...>", p.stdout)
        self.assertIn("demo/bot-cloud", p.stdout)
        self.assertIn("/workspace/agent-mesh/threads/<from", p.stdout)
        self.assertNotRegex(p.stdout, r"<(team-name|relay-name|rn-cloud|threads-dir)>")
        rec = json.loads((self.root / ".git" / "agent-mesh" / "stub").read_text())
        self.assertEqual(rec["stub"], 1)
        self.assertEqual(self.git("-C", str(self.root), "status", "--porcelain").stdout, "")

    def test_language_and_name(self):
        (self.box / "lang").write_text("zh-CN\n")
        (self.box / "relay").write_text("robot\n")
        p = self.cloud("instructions", "1")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertTrue(p.stdout.startswith(f"agent-mesh-instructions BEGIN 1 {TAG} zh-CN -\n你是 Grok Bot"))
        self.assertIn("demo/robot-cloud", p.stdout)
        self.assertEqual(self.git("-C", str(self.root), "status", "--porcelain").stdout, "")

    def rules(self, p):
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        end = "agent-mesh-instructions OK 1\n"
        self.assertTrue(p.stdout.endswith(end), p.stdout[-200:])
        return p.stdout[p.stdout.index("\n") + 1:-len(end)]

    def test_rules_match_the_complete_prompt_byte_for_byte(self):
        for lang in ("en", "zh-CN"):
            with self.subTest(lang=lang):
                (self.box / "lang").write_text(lang + "\n")
                body = self.rules(self.cloud("instructions", "1"))
                full = render_prompt("routine", lang, team_name="demo", relay_name="bot", full=True)
                self.assertTrue(full.startswith(body))
                self.assertTrue(full[len(body):].startswith("F1. "))
                body = self.rules(self.cloud("instructions", "1", "demo", TEAM_HASH, env={"RN_BOT": "helper"}))
                full = render_prompt("routine", lang, full=True, bot_selector="helper", team_name="demo",
                                     team_hash=TEAM_HASH)
                step0 = render_prompt("cloud-add-bot", lang, team_name="demo", relay_name="helper",
                                      team_hash=TEAM_HASH)
                self.assertEqual(full.count(step0), 1)
                full = full.replace(step0, "")
                self.assertTrue(full.startswith(body))
                self.assertTrue(full[len(body):].startswith("F1. "))

    def test_every_copy_of_the_cloud_file_list_agrees(self):
        import deploy_state
        import render_prompt as module
        self.assertEqual(module.routine_module().CLOUD_FILES, CLOUD_FILES)
        text = (ROOT / UPDATE).read_text()
        start = text.index('sums="$(sha256sum ') + len('sums="$(sha256sum ')
        listed = text[start:text.index(')"', start)].replace("\\\n", " ").split()
        self.assertEqual(tuple(listed), CLOUD_FILES)
        self.assertEqual(set(deploy_state.CLOUD_DIGEST),
                         set(CLOUD_FILES) - {"box/rn-cloud-setup.sh", "box/rn-engine.py"} | {UPDATE})
        for lang in ("", ".zh-CN"):
            setup = (ROOT / f"prompts/cloud-setup{lang}.md").read_text()
            self.assertEqual(setup.count(f'[ "$n" = {len(CLOUD_FILES)} ]'), 1, lang)

    def assert_stop(self, p, text):
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn("agent-mesh-instructions OK", p.stdout)
        self.assertIn("STOP: ", p.stdout)
        self.assertIn(text, p.stdout)

    def test_refusals(self):
        self.assert_stop(self.cloud("instructions", "2"), "stub version 2 is not supported")
        self.assert_stop(self.cloud("instructions"), "stub version (none)")
        self.assert_stop(self.cloud("instructions", "1", "demo", TEAM_HASH, "reclaim"), "the first Bot's stub passes neither")
        self.assert_stop(self.cloud("instructions", "1", "demo", TEAM_HASH), "the first Bot's stub passes neither")
        (self.box / "lang").write_text("fr\n")
        self.assert_stop(self.cloud("instructions", "1"), "box/lang must hold en or zh-CN")
        (self.box / "lang").unlink()
        (self.box / "relay").write_text("Bad Name\n")
        self.assert_stop(self.cloud("instructions", "1"), "box/relay is damaged")
        (self.box / "relay").unlink()
        (self.box / "relay.removed").write_text("x\n")
        self.assert_stop(self.cloud("instructions", "1"), "the first Bot was removed")

    def test_changed_files_or_other_commit(self):
        for name in ("prompts/routine-body.md", "box/rn-routine.py", UPDATE):
            path = self.root / name
            keep = path.read_bytes()
            path.write_bytes(keep + b"\n")
            self.assert_stop(self.cloud("instructions", "1"), f"were changed in this clone: {name}")
            path.write_bytes(keep)
        self.git("-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@x", "commit", "-q",
                 "--allow-empty", "-m", "local")
        self.assert_stop(self.cloud("instructions", "1"), "not at the commit of release")

    def test_added_bot_registers_on_first_run(self):
        env = {"RN_BOT": "helper"}
        self.assert_stop(self.cloud("instructions", "1", env=env), "passes its team name and team hash")
        self.assert_stop(self.cloud("instructions", "1", "demo", "0" * 16, env=env), "registering this Bot")
        self.assertFalse((self.root / "bots" / "helper" / ".registered").exists())
        p = self.cloud("instructions", "1", "demo", TEAM_HASH, env=env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue((self.root / "bots" / "helper" / ".registered").is_file())
        self.assertTrue(p.stdout.startswith(f"agent-mesh-instructions BEGIN 1 {TAG} en helper\n"))
        self.assertNotIn("Bot address:", p.stdout)
        self.assertIn("RN_BOT=helper /workspace/agent-mesh/box/rn-cloud.sh fetch <id...>", p.stdout)
        self.assertIn("/workspace/agent-mesh/bots/helper/threads/<from", p.stdout)
        self.assertIn("demo/helper-cloud", p.stdout)
        self.assertTrue((self.root / ".git" / "agent-mesh" / "bots" / "helper" / "stub").is_file())
        self.assertFalse((self.root / ".git" / "agent-mesh" / "stub").exists())
        self.assert_stop(self.cloud("instructions", "1", "other", TEAM_HASH, env=env), "box/person is demo, not other")
        self.assert_stop(self.cloud("instructions", "1", "demo", "1" * 16, env=env), "claimed for another team")
        p = self.cloud("who", env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotEqual(self.cloud("who", env={"RN_BOT": "other"}).returncode, 0)


class Requests(CloudClone):
    def request(self, mid, env=None):
        return self.cloud("request", mid, env=env)

    def test_ordinary_messages_change_nothing(self):
        cases = {
            "other person": (f"agent-mesh stub {R}", "eve/mac"),
            "plain text": ("hello there", "demo/mac"),
            "bad request id": ("agent-mesh stub 0123", "demo/mac"),
            "extra key": (f"agent-mesh stub {R}\nmode=x", "demo/mac"),
            "bad language": (f"agent-mesh stub {R}\nlang=fr", "demo/mac"),
            "add-bot key": (f"agent-mesh add-bot helper {R}\nlang=en", "demo/mac"),
            "carriage return": (f"agent-mesh stub {R}\r", "demo/mac"),
        }
        for name, (text, frm) in cases.items():
            with self.subTest(name):
                mid = self.message(text, frm=frm)
                self.assertEqual(self.fetch(mid)[mid]["status"], "complete")
                before = self.tree_state()
                p = self.request(mid)
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                self.assertEqual(p.stdout, "agent-mesh-request ORDINARY\n")
                self.assertEqual(self.tree_state(), before)
                self.assertFalse((self.root / ".git" / "agent-mesh" / "requests").exists())
                self.assertEqual(self.sent(), [])
                self.assertTrue((self.root / "inbox" / mid / "claimed").exists())
                self.assertFalse((self.root / "inbox" / mid / "done").exists())

    def test_changed_body_is_ordinary(self):
        mid = self.message(f"agent-mesh stub {R}")
        self.fetch(mid)
        (self.root / "inbox" / mid / "full.txt").write_text(f"agent-mesh stub {RX}")
        self.assertEqual(self.request(mid).stdout, "agent-mesh-request ORDINARY\n")
        self.assertEqual(self.request("../x").stdout, "agent-mesh-request ORDINARY\n")
        self.assertEqual(self.request("nosuch").stdout, "agent-mesh-request ORDINARY\n")

    def test_stub_request_records_language_and_is_idempotent(self):
        self.assertEqual(self.cloud("instructions", "1").returncode, 0)
        mid = self.message(f"agent-mesh stub {R}\nlang=zh-CN")
        self.fetch(mid)
        p = self.request(mid)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(last_line(p.stdout), f"agent-mesh-request DONE {mid}")
        self.assertEqual((self.box / "lang").read_text(), "zh-CN\n")
        sent = self.sent()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][:2], ("demo/mac", mid))
        self.assertEqual(sent[0][2].split("\n"), [f"agent-mesh-result request {R}",
                                                  "agent-mesh-result lang zh-CN", "agent-mesh-result stub 1",
                                                  "agent-mesh-result DONE stub"])
        self.assertTrue((self.root / "inbox" / mid / "done").exists())
        p = self.request(mid)
        self.assertEqual(last_line(p.stdout), f"agent-mesh-request DONE {mid}")
        self.assertEqual(len(self.sent()), 1)
        again = self.message(f"agent-mesh stub {R}\nlang=zh-CN")
        self.fetch(again)
        self.assertEqual(last_line(self.request(again).stdout), f"agent-mesh-request DONE {again}")
        self.assertEqual([s[1] for s in self.sent()], [mid, again])
        other = self.message(f"agent-mesh stub {R}\nlang=en")
        self.fetch(other)
        p = self.request(other)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("was already used with other values", p.stdout)
        self.assertEqual((self.box / "lang").read_text(), "zh-CN\n")
        self.assertIn(f"STOP: the request id {R} was already used", self.sent()[-1][2])

    def test_a_request_made_earlier_never_undoes_a_later_language(self):
        now = int(time.time())
        late = req(now - 60, "bbbbbbbb")
        early = self.message(f"agent-mesh stub {req(now - 600, 'aaaaaaaa')}\nlang=zh-CN")
        later = self.message(f"agent-mesh stub {late}\nlang=en")
        same = self.message(f"agent-mesh stub {req(now - 60, '00000001')}\nlang=zh-CN")
        self.fetch(early, later, same)
        self.assertEqual(self.request(later).returncode, 0)
        for mid in (early, same):
            p = self.request(mid)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertEqual((self.box / "lang").read_text(), "en\n")
            reply = self.sent()[-1][2].split("\n")
            self.assertIn("box/lang stays en: a request made later already set the language", reply)
            self.assertIn("agent-mesh-result lang en", reply)
        newer = self.message(f"agent-mesh stub {req(now, 'cccccccc')}\nlang=zh-CN")
        self.fetch(newer)
        self.assertEqual(self.request(newer).returncode, 0)
        self.assertEqual((self.box / "lang").read_text(), "zh-CN\n")

    def test_a_request_cut_off_part_way_runs_again(self):
        now = int(time.time())
        first, second = req(now - 60, "aaaaaaaa"), req(now - 60, "bbbbbbbb")
        records = self.root / ".git" / "agent-mesh" / "requests"
        records.mkdir(parents=True)
        cut = self.message(f"agent-mesh stub {first}\nlang=zh-CN")
        (records / f"{first}.json").write_text(json.dumps(
            {"verb": "stub", "args": [], "extras": {"lang": "zh-CN"}, "state": "running"}))
        (records / ".lang").write_text(json.dumps({"req": first, "lang": "zh-CN"}))
        (self.box / "lang").write_text("zh-CN\n")
        late = self.message(f"agent-mesh stub {second}\nlang=en")
        self.fetch(cut, late)
        self.assertEqual(self.request(late).returncode, 0)
        p = self.request(cut)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual((self.box / "lang").read_text(), "en\n")
        rec = json.loads((records / f"{first}.json").read_text())
        self.assertEqual((rec["state"], rec["replied"]), ("done", [cut]))
        self.assertEqual([s[1] for s in self.sent()], [late, cut])
        other = self.message(f"agent-mesh stub {second}\nlang=zh-CN")
        self.fetch(other)
        self.assertIn("was already used with other values", self.request(other).stdout)

    def test_old_requests_are_refused_and_old_records_deleted(self):
        now = int(time.time())
        records = self.root / ".git" / "agent-mesh" / "requests"
        records.mkdir(parents=True)
        done = {"verb": "stub", "args": [], "extras": {}, "state": "done", "lines": [], "word": "stub",
                "texts": {}, "replied": []}
        stale = records / f"{req(now - 31 * 86400, 'dddddddd')}.json"
        stale.write_text(json.dumps(done))
        recent = records / f"{req(now - 86400, 'eeeeeeee')}.json"
        recent.write_text(json.dumps(done))
        old_req = req(now - 31 * 86400, "aaaaaaaa")
        old = self.message(f"agent-mesh stub {old_req}\nlang=zh-CN")
        self.fetch(old)
        p = self.request(old)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("was made more than 30 days ago by the Mac's clock; nothing was run", self.sent()[-1][2])
        self.assertNotIn("agent-mesh-result DONE", self.sent()[-1][2])
        self.assertFalse((self.box / "lang").exists())
        self.assertFalse(stale.exists())
        self.assertTrue(recent.exists())
        self.assertFalse((records / f"{old_req}.json").exists())

    def test_stub_line_needs_a_recent_rules_record(self):
        mid = self.message(f"agent-mesh stub {RA}")
        self.fetch(mid)
        self.request(mid)
        self.assertNotIn("agent-mesh-result stub", self.sent()[-1][2])
        stub = self.root / ".git" / "agent-mesh" / "stub"
        stub.write_text(json.dumps({"stub": 1, "ts": time.time() - 3600}))
        mid = self.message(f"agent-mesh stub {RB}")
        self.fetch(mid)
        self.request(mid)
        self.assertNotIn("agent-mesh-result stub", self.sent()[-1][2])
        stub.write_text(json.dumps({"stub": 1, "ts": time.time() - 60}))
        mid = self.message(f"agent-mesh stub {RC}")
        self.fetch(mid)
        stub.write_text(json.dumps({"stub": 1, "ts": time.time() + 60}))
        self.request(mid)
        self.assertNotIn("agent-mesh-result stub", self.sent()[-1][2])

    def test_update_request(self):
        commit = self.next_release()
        mid = self.message(f"agent-mesh update {NEXT} {commit} {R}")
        self.fetch(mid)
        p = self.request(mid)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(self.head(), commit)
        reply = self.sent()[-1][2].split("\n")
        self.assertEqual(reply[0], f"agent-mesh-result request {R}")
        self.assertEqual(reply[-1], f"agent-mesh-result DONE {NEXT}")
        self.assertEqual(reply[-2], "agent-mesh-result lang en")
        sums = [l for l in reply if l.startswith("agent-mesh-result sum ")]
        self.assertEqual(sorted(l.split()[-1] for l in sums), sorted(CLOUD_FILES))
        self.assertEqual(sum(l == f"agent-mesh-result request {R}" for l in reply), 1)
        self.assertEqual(self.cloud("instructions", "1").returncode, 0)

    def test_failed_update_is_not_cached(self):
        mid = self.message(f"agent-mesh update {NEXT} {'a' * 40} {R}")
        self.fetch(mid)
        p = self.request(mid)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(f"could not fetch {NEXT}", self.sent()[-1][2])
        self.assertFalse((self.root / ".git" / "agent-mesh" / "requests" / f"{R}.json").exists())
        commit = self.next_release()
        mid = self.message(f"agent-mesh update {NEXT} {commit} {R}")
        self.fetch(mid)
        self.assertEqual(self.request(mid).returncode, 0)
        self.assertEqual(self.head(), commit)

    def test_add_bot_request(self):
        mid = self.message(f"agent-mesh add-bot helper {R}")
        self.fetch(mid)
        p = self.request(mid)
        self.assertIn("reached the Bot bot", self.sent()[-1][2])
        self.assertNotEqual(p.returncode, 0)
        env = {"RN_BOT": "helper"}
        self.assertEqual(self.cloud("instructions", "1", "demo", TEAM_HASH, env=env).returncode, 0)
        mid = self.message(f"agent-mesh add-bot helper {RA}", inbox="helper-inbox")
        self.fetch(mid, env=env)
        p = self.request(mid, env=env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        reply = self.sent()[-1][2].split("\n")
        self.assertEqual(reply[1], f"agent-mesh-result add-bot {RA} ok helper {TEAM_HASH}")
        self.assertEqual(reply[-1], "agent-mesh-result DONE add-bot")
        self.assertIn("agent-mesh-result stub 1", reply)
        self.assertTrue((self.root / "bots" / "helper" / "inbox" / mid / "done").exists())


class Delivery(CloudClone):
    def request(self, mid, env=None):
        return self.cloud("request", mid, env=env)

    def nonces(self):
        out = []
        for line in self.sent_log.read_text().splitlines():
            head = json.loads(line)["envelope"].partition("\n---\n")[0]
            out.append(dict(l.split(": ", 1) for l in head.split("\n")[1:] if ": " in l)["nonce"])
        return out

    def record(self, req=R):
        return json.loads((self.root / ".git" / "agent-mesh" / "requests" / f"{req}.json").read_text())

    def outbox(self, rows):
        db = sqlite3.connect(self.box / "state" / "network.db")
        db.execute("DELETE FROM outbox")
        db.executemany("INSERT INTO outbox VALUES (?)", [(r,) for r in rows])
        db.commit()
        db.close()

    def counting_release(self):
        files = self.files(VERSION=b"99.0.0\n")
        text = (ROOT / UPDATE).read_text()
        self.assertEqual(text.count(ENGINE_STEP), 1)
        step = ('echo run >> "$COUNT"; python3 -I -c "import os, time; g = \'${GATE:-}\'; d = time.time() + 120; '
                '[time.sleep(0.05) for _ in iter(lambda: bool(g) and not os.path.exists(g) and time.time() < d, False)]; '
                'time.sleep(float(\'${HOLD:-0}\'))" </dev/null')
        files[UPDATE] = text.replace(ENGINE_STEP, step).encode()
        self.release(NEXT, files)
        self.count = self.d / "count"
        return self.commits[NEXT]

    def start(self, mid, **env):
        return subprocess.Popen(["bash", str(self.box / "rn-cloud.sh"), "request", mid],
                                env=dict(self.env, COUNT=str(self.count), **env), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)

    def test_a_reply_the_mac_did_not_get_is_sent_again_with_the_same_text_and_nonce(self):
        mid = self.message(f"agent-mesh stub {R}\nlang=zh-CN")
        self.fetch(mid)
        p = self.request(mid, env={"FAKE_OFFLINE": "1"})
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn(f"STOP: the reply to {mid} was not handed to the Mac", p.stdout)
        self.assertNotIn("agent-mesh-request DONE", p.stdout)
        self.assertEqual(self.sent(), [])
        rec = self.record()
        self.assertEqual((rec["state"], rec["replied"]), ("done", []))
        text = rec["texts"][mid]
        self.outbox(["stuck"])
        p = self.request(mid)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("message still queued on the box", p.stdout)
        self.assertEqual(self.record()["replied"], [])
        self.outbox([])
        p = self.request(mid)
        self.assertEqual(last_line(p.stdout), f"agent-mesh-request DONE {mid}", p.stdout + p.stderr)
        sent = self.sent()
        self.assertEqual(len(sent), 2)
        self.assertEqual({s[2] for s in sent}, {text.rstrip("\n")})
        self.assertEqual(len(set(self.nonces())), 1)
        self.assertEqual(self.record()["replied"], [mid])
        self.assertEqual(last_line(self.request(mid).stdout), f"agent-mesh-request DONE {mid}")
        self.assertEqual(len(self.sent()), 2)
        self.assertEqual((self.box / "lang").read_text(), "zh-CN\n")

    def test_a_sent_reply_whose_done_mark_fails_is_not_sent_again(self):
        py = self.d / "tools" / "python3"
        py.unlink()
        py.write_text('#!/bin/sh\ncase "$*" in *"rn-fetch.py done "*) if [ -n "${FAIL_DONE:-}" ]; then '
                      'echo "rn-fetch: no space left on device" >&2; exit 1; fi ;; esac\n'
                      f'exec "{sys.executable}" "$@"\n')
        py.chmod(0o755)
        mid = self.message(f"agent-mesh stub {R}")
        self.fetch(mid)
        p = self.request(mid, env={"FAIL_DONE": "1"})
        self.assertEqual(last_line(p.stdout), f"agent-mesh-request DONE {mid}", p.stdout + p.stderr)
        self.assertIn(f"rn-cloud: sent, but could not record {mid} as handled", p.stdout)
        self.assertFalse((self.root / "inbox" / mid / "done").exists())
        self.assertTrue((self.root / "inbox" / mid / "replying").exists())
        self.assertEqual(self.record()["replied"], [mid])
        p = self.request(mid)
        self.assertEqual(last_line(p.stdout), f"agent-mesh-request DONE {mid}", p.stdout + p.stderr)
        self.assertTrue((self.root / "inbox" / mid / "done").exists())
        self.assertEqual(len(self.sent()), 1)

    def test_a_request_waits_a_bounded_time_for_another_request(self):
        mid = self.message(f"agent-mesh stub {R}")
        self.fetch(mid)
        lock = self.root / ".git" / "agent-mesh" / "requests" / ".lock"
        lock.parent.mkdir(parents=True)
        with open(lock, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            began = time.time()
            p = self.request(mid, env={"RN_REQUEST_WAIT": "2"})
            waited = time.time() - began
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        self.assertIn("STOP: another maintenance request is still running; nothing was changed", p.stdout)
        self.assertLess(waited, 30)
        self.assertEqual(self.sent(), [])
        self.assertFalse((lock.parent / f"{R}.json").exists())
        self.assertEqual(last_line(self.request(mid).stdout), f"agent-mesh-request DONE {mid}")

    def test_requests_with_one_id_run_once_even_at_the_same_time(self):
        commit = self.counting_release()
        mids = [self.message(f"agent-mesh update {NEXT} {commit} {R}") for _ in range(2)]
        self.fetch(*mids)
        procs = [self.start(mid, HOLD="2") for mid in mids]
        for mid, proc in zip(mids, procs):
            out, err = proc.communicate(timeout=300)
            self.assertEqual(last_line(out), f"agent-mesh-request DONE {mid}", out + err)
        self.assertEqual(self.count.read_text().splitlines(), ["run"])
        self.assertEqual(self.head(), commit)
        sent = self.sent()
        self.assertEqual(sorted(s[1] for s in sent), sorted(mids))
        self.assertEqual({s[2].split("\n")[-1] for s in sent}, {f"agent-mesh-result DONE {NEXT}"})

    def test_requests_with_one_id_and_other_values_never_both_run(self):
        commit = self.counting_release()
        before = self.head()
        update = self.message(f"agent-mesh update {NEXT} {commit} {R}")
        stub = self.message(f"agent-mesh stub {R}\nlang=zh-CN")
        self.fetch(update, stub)
        procs = {mid: self.start(mid, HOLD="1") for mid in (update, stub)}
        res = {mid: (proc.communicate(timeout=300), proc.returncode) for mid, proc in procs.items()}
        won = [mid for mid, (_, rc) in res.items() if rc == 0]
        self.assertEqual(len(won), 1, res)
        [lost] = [mid for mid in res if mid not in won]
        self.assertIn(f"STOP: the request id {R} was already used with other values; nothing was run",
                      res[lost][0][0])
        if won == [update]:
            self.assertEqual((self.count.read_text().splitlines(), self.head()), (["run"], commit))
            self.assertFalse((self.box / "lang").exists())
        else:
            self.assertEqual((self.count.exists(), self.head()), (False, before))
            self.assertEqual((self.box / "lang").read_text(), "zh-CN\n")

    def test_instructions_wait_for_a_running_update_and_load_the_new_release(self):
        commit = self.counting_release()
        mid = self.message(f"agent-mesh update {NEXT} {commit} {R}")
        self.fetch(mid)
        gate = self.d / "gate"
        proc = self.start(mid, GATE=str(gate))
        inst = None
        try:
            deadline = time.time() + 120
            while not self.count.exists() and time.time() < deadline and proc.poll() is None:
                time.sleep(0.05)
            self.assertTrue(self.count.exists())
            inst = subprocess.Popen(["bash", str(self.box / "rn-cloud.sh"), "instructions", "1"], env=self.env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            with self.assertRaises(subprocess.TimeoutExpired):
                inst.wait(timeout=5)
            self.assertIsNone(proc.poll())
            gate.write_text("")
            out, err = proc.communicate(timeout=300)
            self.assertEqual(last_line(out), f"agent-mesh-request DONE {mid}", out + err)
            stdout, stderr = inst.communicate(timeout=300)
        finally:
            gate.write_text("")
            for child in (proc, inst):
                if child is not None and child.poll() is None:
                    child.kill()
                    child.communicate()
        self.assertEqual(inst.returncode, 0, stdout + stderr)
        self.assertTrue(stdout.startswith(f"agent-mesh-instructions BEGIN 1 {NEXT} en -\n"), stdout[:200])
        self.assertEqual(last_line(stdout), "agent-mesh-instructions OK 1")


class UpdateCrash(CloudClone):
    SAVE = "def save_record(req, rec):\n"
    HOOK = ('    if os.environ.get("CRASH_AT") == rec.get("state") and os.environ.get("CRASH_REQ") == req:\n'
            '        os._exit(9)\n')
    FIRST = '  if [ "$after" = 0 ]; then\n'

    def files(self, **changes):
        out = super().files(**changes)
        routine = out["box/rn-routine.py"].decode()
        self.assertEqual(routine.count(self.SAVE), 1)
        out["box/rn-routine.py"] = routine.replace(self.SAVE, self.SAVE + self.HOOK).encode()
        update = out[UPDATE].decode()
        self.assertEqual((update.count(ENGINE_STEP), update.count(self.FIRST)), (1, 1))
        update = update.replace(ENGINE_STEP, 'echo "$TAG" >> "$STEPS" </dev/null')
        out[UPDATE] = update.replace(self.FIRST, self.FIRST + '    echo "$TAG" >> "$RUNS"\n').encode()
        return out

    def setUp(self):
        super().setUp()
        self.steps, self.runs = self.d / "steps", self.d / "runs"
        self.env.update(STEPS=str(self.steps), RUNS=str(self.runs))
        for tag, version in ((NEXT, b"99.0.0\n"), ("v99.1.0", b"99.1.0\n")):
            self.release(tag, self.files(VERSION=version))

    def count(self, path):
        return path.read_text().split() if path.exists() else []

    def record(self, req):
        p = self.root / ".git" / "agent-mesh" / "requests" / f"{req}.json"
        return json.loads(p.read_text()) if p.exists() else None

    def update(self, tag, req, lang, crash=False):
        mid = self.message(f"agent-mesh update {tag} {self.commits[tag]} {req}\nlang={lang}")
        self.fetch(mid)
        env = {"CRASH_AT": "done", "CRASH_REQ": req} if crash else None
        return mid, self.cloud("request", mid, env=env)

    def test_an_update_cut_off_before_done_and_replayed_after_a_later_update(self):
        old, new = req(NOW - 60, "aaaaaaaa"), req(NOW, "bbbbbbbb")
        mid, p = self.update(NEXT, old, "zh-CN", crash=True)
        self.assertEqual(p.returncode, 9, p.stdout + p.stderr)
        self.assertEqual((self.head(), self.count(self.runs), self.count(self.steps)), (self.commits[NEXT], [NEXT], [NEXT]))
        self.assertEqual((self.record(old)["state"], (self.box / "lang").read_text()), ("running", "zh-CN\n"))
        self.assertEqual(self.sent(), [])
        later, p = self.update("v99.1.0", new, "en")
        self.assertEqual(last_line(p.stdout), f"agent-mesh-request DONE {later}", p.stdout + p.stderr)
        self.assertEqual(self.head(), self.commits["v99.1.0"])
        self.assertEqual(self.count(self.steps), [NEXT, "v99.1.0"])
        reply = self.sent()[-1][2].split("\n")
        self.assertEqual(reply[-2:], ["agent-mesh-result lang en", "agent-mesh-result DONE v99.1.0"])
        done = self.record(new)
        p = self.cloud("request", mid)
        self.assertNotEqual(p.returncode, 0)
        reply = self.sent()[-1]
        self.assertEqual(reply[1], mid)
        self.assertIn("STOP: this clone is at 99.1.0, newer than v99.0.0; it is never downgraded; nothing was changed",
                      reply[2])
        self.assertTrue(reply[2].startswith(f"agent-mesh-result request {old}\n"), reply[2])
        self.assertNotIn("agent-mesh-result DONE", reply[2])
        self.assertEqual(self.head(), self.commits["v99.1.0"])
        self.assertEqual((self.root / "VERSION").read_text(), "99.1.0\n")
        self.assertEqual(self.count(self.runs), [NEXT, "v99.1.0", NEXT])
        self.assertEqual(self.count(self.steps), [NEXT, "v99.1.0"])
        self.assertEqual((self.box / "lang").read_text(), "en\n")
        self.assertIsNone(self.record(old))
        self.assertEqual(self.record(new), done)
        self.assertEqual(len(self.sent()), 2)

    def test_an_update_cut_off_before_done_and_replayed_at_once_runs_again(self):
        old = req(NOW - 60, "aaaaaaaa")
        mid, p = self.update(NEXT, old, "zh-CN", crash=True)
        self.assertEqual(p.returncode, 9, p.stdout + p.stderr)
        p = self.cloud("request", mid)
        self.assertEqual(last_line(p.stdout), f"agent-mesh-request DONE {mid}", p.stdout + p.stderr)
        self.assertEqual(self.head(), self.commits[NEXT])
        self.assertEqual((self.count(self.runs), self.count(self.steps)), ([NEXT, NEXT], [NEXT, NEXT]))
        [reply] = self.sent()
        self.assertEqual(reply[2].split("\n")[-2:], ["agent-mesh-result lang zh-CN", f"agent-mesh-result DONE {NEXT}"])
        rec = self.record(old)
        self.assertEqual((rec["state"], rec["replied"]), ("done", [mid]))
        self.assertEqual((self.box / "lang").read_text(), "zh-CN\n")


class Bootstrap(CloudClone):
    def setUp(self):
        super().setUp()
        self.old_cloud()

    def old_cloud(self):
        cloud = (ROOT / "box/rn-cloud.sh").read_text()
        line = '  instructions|request) exec python3 -I "$BASE/rn-routine.py" "$@" ;;\n'
        self.assertEqual(cloud.count(line), 1)
        files = self.files(**{"box/rn-cloud.sh": cloud.replace(line, "").encode(), "box/rn-routine.py": None,
                              "prompts/routine-body.md": None, "prompts/routine-body.zh-CN.md": None,
                              UPDATE: b"#!/bin/sh\necho usage >&2\nexit 2\n", "VERSION": b"0.8.1\n"})
        self.release("v0.8.1", files)
        self.git("-C", str(self.root), "fetch", "-q", "origin", "refs/tags/v0.8.1:refs/tags/v0.8.1")
        self.git("-C", str(self.root), "checkout", "-q", "--detach", "v0.8.1")

    def runner(self, env=None, stub=None):
        return StubRunner(stub or prompt_block("routine", "en"), self.root,
                          {**self.env, "FETCH_TIMEOUT": "0", **(env or {})})

    def test_stub_updates_an_old_cloud_and_reloads_in_the_same_run(self):
        commit = self.next_release()
        plain = self.message("please summarise the build log")
        other = self.message(f"agent-mesh update {NEXT} {commit} {RE}", frm="eve/mac")
        req = self.message(f"agent-mesh update {NEXT} {commit} {R}\nlang=zh-CN")
        run = self.runner()
        state, rules, boot = run.run([plain, other, req])
        self.assertEqual(state, "reloaded", "\n".join(f"{c}\n{o}\n{e}" for c, _, o, e in run.log))
        self.assertTrue(rules.startswith(f"agent-mesh-instructions BEGIN 1 {NEXT} zh-CN -\n"))
        self.assertEqual(self.head(), commit)
        self.assertEqual(len(self.sent()), 1)
        to, reply_to, body = self.sent()[0]
        self.assertEqual((to, reply_to), ("demo/mac", req))
        reply = body.split("\n")
        self.assertEqual(reply[0], f"agent-mesh-result request {R}")
        self.assertEqual(reply[-3:], ["agent-mesh-result lang zh-CN", "agent-mesh-result stub 1",
                                      f"agent-mesh-result DONE {NEXT}"])
        self.assertEqual(len([l for l in reply if l.startswith("agent-mesh-result sum ")]), len(CLOUD_FILES))
        self.assertIn(f'"id": "{plain}", "released": true', boot)
        self.assertIn(f'"id": "{other}", "released": true', boot)
        self.assertFalse((self.root / "inbox" / plain / "claimed").exists())
        self.assertTrue((self.root / "inbox" / req / "done").exists())
        again = self.fetch(plain, other, req)
        self.assertEqual(again[plain]["status"], "complete")
        self.assertEqual(again[other]["status"], "complete")
        self.assertEqual(again[req]["status"], "handled")
        self.assertEqual(self.git("-C", str(self.root), "status", "--porcelain").stdout, "")
        self.assertEqual(json.loads((self.root / f".git/agent-mesh/requests/{R}.json").read_text())
                         ["replied"], [req])

    def test_the_snapshot_leaves_out_claims_older_than_the_run(self):
        commit = self.next_release()
        held = self.message("held by an earlier turn")
        self.fetch(held)
        marker = self.root / "inbox" / held / "claimed"
        past = time.time() - 30
        os.utime(marker, (past, past))
        token = marker.read_bytes()
        req = self.message(f"agent-mesh update {NEXT} {commit} {R}")
        state, _, boot = self.runner().run([held, req])
        self.assertEqual(state, "reloaded", boot)
        [run] = list((self.root / ".git" / "agent-mesh" / "boot").iterdir())
        names = sorted(p.name for p in run.iterdir())
        self.assertIn(f"inbox_{req}_claimed", names)
        self.assertNotIn(f"inbox_{held}_claimed", names)
        self.assertEqual(marker.read_bytes(), token)
        self.assertNotIn(f'"id": "{held}", "released"', boot)

    def test_no_update_request_skips_without_changes(self):
        commit = self.next_release()
        plain = self.message("hello")
        other = self.message(f"agent-mesh update {NEXT} {commit} {RE}", frm="eve/mac")
        bad = self.message(f"agent-mesh update {NEXT} {commit} 0123")
        old = self.message(f"agent-mesh update v0.8.1 {self.commits['v0.8.1']} {RF}")
        before = self.head()
        state, _, boot = self.runner().run([plain, other, bad, old, "../evil", "x'y"])
        self.assertEqual(state, "skipped")
        self.assertEqual(last_line(boot), "agent-mesh-bootstrap SKIP")
        self.assertEqual(self.head(), before)
        self.assertEqual(self.sent(), [])
        self.assertFalse((self.box / "lang").exists())
        self.assertFalse((self.root / ".git" / "agent-mesh" / "requests").exists())
        self.assertTrue((self.root / "inbox" / plain / "claimed").exists())

    def test_a_failed_update_replies_without_reload(self):
        self.next_release()
        req = self.message(f"agent-mesh update {NEXT} {'b' * 40} {R}")
        plain = self.message("hello")
        state, _, boot = self.runner().run([req, plain])
        self.assertEqual(state, "answered")
        self.assertNotIn("RELOAD", boot)
        self.assertIn(f"points to {self.commits[NEXT]}", self.sent()[0][2])
        self.assertTrue((self.root / "inbox" / plain / "claimed").exists())
        self.assertEqual(self.head(), self.commits["v0.8.1"])

    def test_a_batch_with_every_kind_of_failing_candidate_runs_only_the_last_legal_request(self):
        commit = self.next_release()
        missing = self.message(f"agent-mesh update v98.0.0 {'c' * 40} {RA}")
        old = self.message(f"agent-mesh update v0.8.1 {self.commits['v0.8.1']} {RB}")
        other = self.message(f"agent-mesh update {NEXT} {commit} {RE}", frm="eve/mac")
        bad = self.message(f"agent-mesh update {NEXT} {commit} 0123")
        extra = self.message(f"agent-mesh update {NEXT} {commit} {RC}\nmode=x")
        good = self.message(f"agent-mesh update {NEXT} {commit} {R}")
        run = self.runner()
        state, rules, boot = run.run([missing, old, other, bad, extra, good])
        self.assertEqual(state, "reloaded", "\n".join(f"{c}\n{o}\n{e}" for c, _, o, e in run.log))
        self.assertTrue(rules.startswith(f"agent-mesh-instructions BEGIN 1 {NEXT} en -\n"))
        self.assertEqual(self.head(), commit)
        [(to, reply_to, body)] = self.sent()
        self.assertEqual((to, reply_to), ("demo/mac", good))
        self.assertEqual(body.split("\n")[-1], f"agent-mesh-result DONE {NEXT}")
        self.assertEqual(sorted(p.name for p in (self.root / ".git/agent-mesh/requests").glob("*.json")),
                         [f"{R}.json"])
        for mid in (missing, old, other, bad, extra):
            self.assertIn(f'"id": "{mid}", "released": true', boot)
            self.assertFalse((self.root / "inbox" / mid / "done").exists())
        self.assertEqual([l for l in boot.split("\n") if "DONE" in l or "STOP" in l],
                         [f"agent-mesh-result DONE {NEXT}"])

    def test_full_prompt_command(self):
        from tests.helpers.stub_runner import full_command
        commit = self.next_release()
        req = self.message(f"agent-mesh update {NEXT} {commit} {R}")
        self.fetch(req)
        command = full_command(prompt_block("routine-full", "en")).replace("/workspace/agent-mesh", str(self.root))
        p = subprocess.run(["bash", "-c", command + f" '{req}'"], env=self.env, capture_output=True, text=True,
                           timeout=300)
        self.assertEqual(last_line(p.stdout), "agent-mesh-bootstrap DONE", p.stdout + p.stderr)
        self.assertEqual(self.head(), commit)
        self.assertNotIn("agent-mesh-result stub", self.sent()[0][2])
        self.assertFalse((self.root / ".git" / "agent-mesh" / "stub").exists())
        plain = self.message("hi")
        self.fetch(plain)
        p = subprocess.run(["bash", "-c", command + f" '{plain}'"], env=self.env, capture_output=True, text=True,
                           timeout=300)
        self.assertEqual(last_line(p.stdout), "agent-mesh-bootstrap SKIP")


class AddedBot(CloudClone):
    def setUp(self):
        super().setUp()
        Bootstrap.old_cloud(self)
        self.inbox = self.root / "bots" / "helper" / "inbox"

    def register(self, **env):
        return subprocess.run(["bash", str(self.box / "rn-cloud-add-bot.sh"), "helper", "demo", TEAM_HASH],
                              env=dict(self.env, **env), capture_output=True, text=True, timeout=120)

    def stub(self, full=False):
        return render_prompt("routine", "en", team_name="demo", bot_selector="helper", team_hash=TEAM_HASH, full=full)

    def first_bot(self):
        mid = self.message("for the first Bot")
        self.fetch(mid)
        inbox = self.root / "inbox"
        return lambda: (sorted(str(p.relative_to(inbox)) for p in inbox.rglob("*")),
                        (inbox / mid / "claimed").read_bytes(), (self.root / ".git" / "agent-mesh" / "stub").exists())

    def test_an_added_bot_updates_an_old_cloud_and_reloads(self):
        self.assertEqual(self.register().returncode, 0)
        commit = self.next_release()
        first = self.first_bot()
        before = first()
        plain = self.message("please summarise the build log", inbox="helper-inbox")
        req = self.message(f"agent-mesh update {NEXT} {commit} {R}\nlang=zh-CN", inbox="helper-inbox")
        run = StubRunner(self.stub(), self.root, self.env)
        state, rules, boot = run.run([plain, req])
        self.assertEqual(state, "reloaded", "\n".join(f"{c}\n{o}\n{e}" for c, _, o, e in run.log))
        self.assertTrue(rules.startswith(f"agent-mesh-instructions BEGIN 1 {NEXT} zh-CN helper\n"))
        self.assertEqual(self.head(), commit)
        [(to, reply_to, body)] = self.sent()
        self.assertEqual((to, reply_to), ("demo/mac", req))
        reply = body.split("\n")
        self.assertEqual(reply[0], f"agent-mesh-result request {R}")
        self.assertEqual(reply[-3:], ["agent-mesh-result lang zh-CN", "agent-mesh-result stub 1",
                                      f"agent-mesh-result DONE {NEXT}"])
        self.assertIn(f'"id": "{plain}", "released": true', boot)
        self.assertFalse((self.inbox / plain / "claimed").exists())
        self.assertTrue((self.inbox / req / "done").exists())
        [snap] = list((self.root / ".git" / "agent-mesh" / "boot").iterdir())
        names = sorted(p.name for p in snap.iterdir())
        self.assertIn(f"bots_helper_inbox_{req}_claimed", names)
        self.assertEqual([n for n in names if n.startswith("inbox_")], [])
        self.assertTrue((self.root / ".git" / "agent-mesh" / "bots" / "helper" / "stub").is_file())
        self.assertEqual(first(), before)
        self.assertEqual(self.git("-C", str(self.root), "status", "--porcelain").stdout, "")

    def test_an_added_bots_complete_prompt_updates_without_a_stub_record(self):
        from tests.helpers.stub_runner import full_command
        self.assertEqual(self.register().returncode, 0)
        commit = self.next_release()
        first = self.first_bot()
        before = first()
        req = self.message(f"agent-mesh update {NEXT} {commit} {R}", inbox="helper-inbox")
        self.fetch(req, env={"RN_BOT": "helper"})
        command = full_command(self.stub(full=True)).replace("/workspace/agent-mesh", str(self.root))
        self.assertTrue(command.startswith("RN_BOT=helper "))
        p = subprocess.run(["bash", "-c", command + f" '{req}'"], env=self.env, capture_output=True, text=True,
                           timeout=300)
        self.assertEqual(last_line(p.stdout), "agent-mesh-bootstrap DONE", p.stdout + p.stderr)
        self.assertEqual(self.head(), commit)
        [(to, reply_to, body)] = self.sent()
        self.assertEqual((to, reply_to), ("demo/mac", req))
        self.assertNotIn("agent-mesh-result stub", body)
        self.assertFalse((self.root / ".git" / "agent-mesh" / "bots" / "helper" / "stub").exists())
        self.assertTrue((self.inbox / req / "done").exists())
        self.assertEqual(first(), before)

    def test_an_unfinished_registration_changes_nothing_until_the_new_rules_continue_it(self):
        p = self.register(FAKE_DIE_ON="helper-inbox")
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse((self.root / "bots" / "helper" / ".registered").exists())
        commit = self.next_release()
        first = self.first_bot()
        before = first()
        req = self.message(f"agent-mesh update {NEXT} {commit} {R}", inbox="helper-inbox")
        head = self.head()
        state, _, boot = StubRunner(self.stub(), self.root, self.env).run([req])
        self.assertEqual(state, "skipped", boot)
        self.assertEqual(self.head(), head)
        self.assertEqual(self.sent(), [])
        self.assertFalse((self.root / ".git" / "agent-mesh" / "requests").exists())
        self.assertEqual(first(), before)
        self.git("-C", str(self.root), "fetch", "-q", "origin", f"refs/tags/{NEXT}:refs/tags/{NEXT}")
        self.git("-C", str(self.root), "checkout", "-q", "--detach", NEXT)
        run = StubRunner(self.stub(), self.root, self.env)
        state, rules, _ = run.run([req])
        self.assertEqual(state, "loaded", "\n".join(f"{c}\n{o}\n{e}" for c, _, o, e in run.log))
        self.assertTrue(rules.startswith(f"agent-mesh-instructions BEGIN 1 {NEXT} en helper\n"))
        self.assertTrue((self.root / "bots" / "helper" / ".registered").is_file())
        self.assertEqual(first(), before)

    def test_release_in_an_added_bots_inbox_keeps_what_it_cannot_prove(self):
        self.assertEqual(self.register().returncode, 0)
        first = self.first_bot()
        before = first()
        ids = [self.message(f"m{i}", inbox="helper-inbox") for i in range(4)]
        run = self.root / ".git" / "agent-mesh" / "boot" / "run-1"
        run.mkdir(parents=True)
        (run / "start").touch()
        time.sleep(0.02)
        res = self.fetch(*ids, env={"RN_BOT": "helper"})
        (run / "fetch").write_text("".join(json.dumps(r) + "\n" for r in res.values()))
        for mid in ids:
            shutil.copy(self.inbox / mid / "claimed", run / f"bots_helper_inbox_{mid}_claimed")
        (run / "snap").touch()
        (self.inbox / ids[1] / "legacy").write_text(json.dumps({"id": ids[1]}))
        (self.inbox / ids[2] / "meta.json").write_text(json.dumps({"id": "someone-else"}))
        claimed = json.loads((self.inbox / ids[3] / "claimed").read_text())
        claimed["token"] = "f" * 16
        (self.inbox / ids[3] / "claimed").write_text(json.dumps(claimed))
        env = dict(self.env, RN_INBOX_DIR=str(self.inbox), RN_CLAIM_LEASE="3600")
        p = subprocess.run([sys.executable, str(self.box / "rn-fetch.py"), "release", str(run)], env=env,
                           capture_output=True, text=True, timeout=60)
        out = {r["id"]: r for r in map(json.loads, p.stdout.splitlines())}
        self.assertEqual(out[ids[0]], {"id": ids[0], "released": True})
        self.assertEqual(out[ids[1]]["kept"], "it has a legacy marker")
        self.assertTrue(out[ids[2]]["kept"].startswith("id collision"))
        self.assertEqual(out[ids[3]]["kept"], "another turn holds its claim")
        self.assertFalse((self.inbox / ids[0] / "claimed").exists())
        for mid in ids[1:]:
            self.assertTrue((self.inbox / mid / "claimed").exists())
        self.assertEqual(first(), before)


class Release(CloudClone):
    def snapshot(self, ids, lease=None):
        run = self.root / ".git" / "agent-mesh" / "boot" / "run-1"
        run.mkdir(parents=True)
        (run / "start").touch()
        time.sleep(0.02)
        env = {"RN_CLAIM_LEASE": str(lease)} if lease else None
        res = self.fetch(*ids, env=env)
        (run / "fetch").write_text("".join(json.dumps(r) + "\n" for r in res.values()))
        for mid in ids:
            src = self.root / "inbox" / mid / "claimed"
            if src.exists():
                shutil.copy(src, run / f"inbox_{mid}_claimed")
        (run / "snap").touch()
        return run

    def free(self, run, lease=None):
        env = dict(self.env, RN_INBOX_DIR=str(self.root / "inbox"), RN_CLAIM_LEASE=str(lease or 3600))
        p = subprocess.run([sys.executable, str(self.box / "rn-fetch.py"), "release", str(run)], env=env,
                           capture_output=True, text=True, timeout=60)
        return [json.loads(l) for l in p.stdout.splitlines()]

    def test_release_only_this_runs_claims(self):
        ids = [self.message(f"m{i}") for i in range(4)]
        run = self.snapshot(ids)
        inbox = self.root / "inbox"
        claimed = json.loads((inbox / ids[1] / "claimed").read_text())
        claimed["token"] = "f" * 16
        (inbox / ids[1] / "claimed").write_text(json.dumps(claimed))
        (inbox / ids[2] / "working").write_text(json.dumps({"id": ids[2]}))
        (run / f"inbox_{ids[3]}_claimed").unlink()
        out = {r["id"]: r for r in self.free(run)}
        self.assertEqual(out[ids[0]], {"id": ids[0], "released": True})
        self.assertEqual(out[ids[1]]["kept"], "another turn holds its claim")
        self.assertEqual(out[ids[2]]["kept"], "it has a working marker")
        self.assertEqual(out[ids[3]]["kept"], "this run took no snapshot of its claim")
        self.assertFalse((inbox / ids[0] / "claimed").exists())
        for mid in ids[1:]:
            self.assertTrue((inbox / mid / "claimed").exists())

    def test_short_lease_or_late_snapshot_releases_nothing(self):
        mid = self.message("m")
        run = self.snapshot([mid], lease=600)
        self.assertEqual(self.free(run, lease=600)[0]["released"], 0)
        os.utime(run / "snap", (time.time() + 4000, time.time() + 4000))
        self.assertEqual(self.free(run)[0]["released"], 0)
        self.assertTrue((self.root / "inbox" / mid / "claimed").exists())

    def test_release_refuses_other_folders(self):
        for bad in (str(self.root / "inbox"), ".git/agent-mesh/boot/run-1"):
            p = subprocess.run([sys.executable, str(self.box / "rn-fetch.py"), "release", bad],
                               env=dict(self.env, RN_INBOX_DIR=str(self.root / "inbox")), capture_output=True,
                               text=True, timeout=60)
            self.assertEqual(p.returncode, 2)


if __name__ == "__main__":
    unittest.main()
