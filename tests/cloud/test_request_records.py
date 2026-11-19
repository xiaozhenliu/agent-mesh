"""rn-routine.py request records in one process: runs cut off at each step, same-second ordering, the 30-day
window, Mac clock skew, request id collisions and the two request id generators."""
import contextlib, io, json, re, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.cloud.support import HERE, load

ROOT = HERE.parent
DAY = 86400
WINDOW = 30 * DAY


class Crash(Exception):
    pass


class Records(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        (self.d / ".git").mkdir()
        (self.d / "box").mkdir()
        self.m = load("rn_routine_records", HERE / "rn-routine.py")
        self.m.BASE, self.m.ROOT = self.d / "box", self.d
        self.now = 1_800_000_000.0
        self.m.time = SimpleNamespace(time=lambda: self.now, sleep=lambda s: None)
        self.sel = SimpleNamespace(slot="-", name="bot", inbox_dir=self.d / "inbox")
        self.runs, self.sends, self.crash = [], [], None
        real_execute, real_save = self.m.execute, self.m.save_record

        def execute(parsed, *args):
            if self.crash == "before-step":
                self.crash = None
                raise Crash()
            self.runs.append(parsed.req)
            return real_execute(parsed, *args)

        def save_record(req, rec):
            phase = {"done": "done", "running": None}.get(rec.get("state"))
            if rec.get("replied"):
                phase = "replied"
            elif rec.get("texts"):
                phase = "text"
            if phase and phase == self.crash:
                self.crash = None
                raise Crash()
            real_save(req, rec)

        def send(to, mid, text):
            self.sends.append((mid, text))
            if self.crash == "send":
                self.crash = None
                raise Crash()
            return True, ""

        self.m.execute, self.m.save_record, self.m.send = execute, save_record, send
        self.m.mark_done = lambda sel, mid: None

    def req(self, at, tail="aaaaaaaa"):
        return f"{int(at):08x}{tail}"

    def handle(self, req, mid, lang="zh-CN", crash=None):
        self.crash = crash
        parsed = self.m.parse_request(f"agent-mesh stub {req}" + (f"\nlang={lang}" if lang else ""))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                ok, sent = self.m.handle(self.sel, mid, "demo/mac", parsed, None, None)
            except Crash:
                return None, out.getvalue()
        return (ok, sent), out.getvalue()

    def record(self, req):
        p = self.d / ".git" / "agent-mesh" / "requests" / f"{req}.json"
        return json.loads(p.read_text()) if p.exists() else None

    def lang(self):
        p = self.d / "box" / "lang"
        return p.read_text().strip() if p.exists() else None

    def test_a_run_cut_off_at_each_step(self):
        cases = {"before-step": (1, 1, "running"), "done": (2, 1, "running"), "text": (1, 1, "done"),
                 "send": (1, 2, "done"), "replied": (1, 2, "done")}
        for n, (phase, (runs, sends, left)) in enumerate(cases.items()):
            with self.subTest(phase):
                self.runs, self.sends = [], []
                r = self.req(self.now - 60, f"{n:08x}")
                res, _ = self.handle(r, "m1", crash=phase)
                self.assertIsNone(res)
                self.assertEqual(self.record(r)["state"], left)
                res, out = self.handle(r, "m1")
                self.assertEqual(res, (True, True))
                self.assertEqual(len(self.runs), runs)
                self.assertEqual(len(self.sends), sends)
                self.assertEqual(len({t for _, t in self.sends}), 1)
                self.assertTrue(out.endswith("agent-mesh-result DONE stub\n"))
                rec = self.record(r)
                self.assertEqual((rec["state"], rec["replied"]), ("done", ["m1"]))
                self.assertEqual(self.lang(), "zh-CN")
                res, out = self.handle(r, "m1")
                self.assertEqual(res, (True, True))
                self.assertEqual(len(self.sends), sends)
                self.assertEqual(out, self.sends[-1][1])

    def test_a_run_cut_off_at_each_step_and_replayed_after_a_later_request(self):
        cases = {"before-step": (["b", "a"], 2, False), "done": (["a", "b", "a"], 2, False),
                 "text": (["a", "b"], 2, False), "send": (["a", "b"], 3, True), "replied": (["a", "b"], 3, True)}
        for n, (phase, (runs, sends, same)) in enumerate(cases.items()):
            with self.subTest(phase):
                shutil.rmtree(self.d / ".git" / "agent-mesh", True)
                (self.d / "box" / "lang").unlink(missing_ok=True)
                self.runs, self.sends = [], []
                self.now += 100
                a, b = self.req(self.now - 60, f"{n:08x}"), self.req(self.now, f"{n + 16:08x}")
                self.assertIsNone(self.handle(a, "ma", "zh-CN", crash=phase)[0])
                first = [t for m, t in self.sends if m == "ma"]
                self.assertEqual(self.handle(b, "mb", "en")[0], (True, True))
                self.assertEqual(self.lang(), "en")
                res, out = self.handle(a, "ma", "zh-CN")
                self.assertEqual(res, (True, True))
                self.assertEqual(self.runs, [{"a": a, "b": b}[k] for k in runs])
                self.assertEqual(len(self.sends), sends)
                self.assertEqual(self.lang(), "en")
                self.assertTrue(out.endswith("agent-mesh-result DONE stub\n"), out)
                self.assertEqual(out, self.sends[-1][1])
                if same:
                    self.assertEqual(out, first[0])
                    self.assertIn("agent-mesh-result lang zh-CN\n", out)
                else:
                    self.assertIn("agent-mesh-result lang en\n", out)
                if phase in ("before-step", "done"):
                    self.assertIn("box/lang stays en: a request made later already set the language\n", out)
                ra, rb = self.record(a), self.record(b)
                self.assertEqual((ra["state"], ra["replied"], rb["state"], rb["replied"]), ("done", ["ma"], "done", ["mb"]))
                self.assertEqual(ra["texts"]["ma"], out)

    def test_a_reply_sent_again_is_the_text_first_sent(self):
        r = self.req(self.now - 60)
        self.assertIsNone(self.handle(r, "m1", "zh-CN", crash="replied")[0])
        self.assertEqual(self.handle(self.req(self.now, "bbbbbbbb"), "m2", "en")[0], (True, True))
        self.assertEqual(self.lang(), "en")
        self.assertEqual(self.handle(r, "m1", "zh-CN")[0], (True, True))
        first, again = self.sends[0], self.sends[-1]
        self.assertEqual((first, again[0]), (again, "m1"))
        self.assertIn("agent-mesh-result lang zh-CN\n", again[1])
        self.assertEqual(self.runs, [r, self.req(self.now, "bbbbbbbb")])

    def test_same_second_crash_in_both_orders(self):
        for first, second, final in (("aaaaaaaa", "bbbbbbbb", "en"), ("bbbbbbbb", "aaaaaaaa", "zh-CN")):
            with self.subTest(first=first):
                shutil.rmtree(self.d / ".git" / "agent-mesh", True)
                (self.d / "box" / "lang").unlink(missing_ok=True)
                self.now += 10
                a, b = self.req(self.now, first), self.req(self.now, second)
                self.assertIsNone(self.handle(a, "ma", "zh-CN", crash="done")[0])
                self.assertEqual((self.record(a)["state"], self.lang()), ("running", "zh-CN"))
                res, out = self.handle(b, "mb", "en")
                self.assertEqual(res, (True, True))
                if final == "zh-CN":
                    self.assertIn("box/lang stays zh-CN: a request made later already set the language\n", out)
                self.assertIn(f"agent-mesh-result lang {self.lang()}\n", out)
                res, out = self.handle(a, "ma", "zh-CN")
                self.assertEqual(res, (True, True))
                self.assertEqual(self.lang(), final)
                self.assertIn(f"agent-mesh-result lang {final}\n", out)

    def test_copies_across_the_window_run_once(self):
        start = self.now
        r = self.req(start)
        self.assertEqual(self.handle(r, "m1")[0], (True, True))
        self.now = start + 29 * DAY
        res, out = self.handle(r, "m2")
        self.assertEqual(res, (True, True))
        self.assertTrue(out.endswith("agent-mesh-result DONE stub\n"))
        self.now = start + 31 * DAY
        for mid in ("m1", "m3"):
            res, out = self.handle(r, mid)
            self.assertEqual(res, (False, True))
            self.assertIn("was made more than 30 days ago by the Mac's clock; nothing was run", out)
            self.assertNotIn("DONE", out)
            self.assertIsNone(self.record(r))
        self.assertEqual(self.runs, [r])

    def test_the_exact_window_edge(self):
        made = int(self.now)
        r = self.req(made)
        self.assertEqual(self.handle(r, "m1")[0], (True, True))
        self.now = made + WINDOW
        self.assertEqual(self.handle(r, "m2")[0], (True, True))
        self.assertEqual(self.record(r)["replied"], ["m1", "m2"])
        self.now = made + WINDOW + 0.01
        res, out = self.handle(r, "m3")
        self.assertEqual(res, (False, True))
        self.assertIsNone(self.record(r))
        self.assertEqual(self.runs, [r])

    def test_mac_clock_behind_or_ahead(self):
        res, out = self.handle(self.req(self.now - 31 * DAY), "m1")
        self.assertEqual(res, (False, True))
        self.assertIn("by the Mac's clock", out)
        self.assertIsNone(self.lang())
        ahead = self.req(self.now + 40 * DAY)
        self.assertEqual(self.handle(ahead, "m2", "zh-CN")[0], (True, True))
        self.assertEqual(self.lang(), "zh-CN")
        res, out = self.handle(self.req(self.now, "bbbbbbbb"), "m3", "en")
        self.assertEqual(res, (True, True))
        self.assertIn("box/lang stays zh-CN", out)
        self.now += 35 * DAY
        self.handle(self.req(self.now, "cccccccc"), "m4", None)
        self.assertEqual(self.record(ahead)["state"], "done")
        self.now += 6 * DAY
        self.assertEqual(self.handle(self.req(self.now, "dddddddd"), "m5", "en")[0], (True, True))
        self.assertEqual(self.lang(), "en")

    def test_request_id_collisions(self):
        r = self.req(self.now - 60)
        self.assertEqual(self.handle(r, "m1", "zh-CN")[0], (True, True))
        res, out = self.handle(r, "m2", "zh-CN")
        self.assertEqual(res, (True, True))
        self.assertTrue(out.endswith("agent-mesh-result DONE stub\n"))
        before = self.record(r)
        res, out = self.handle(r, "m3", "en")
        self.assertEqual(res, (False, True))
        self.assertIn(f"STOP: the request id {r} was already used with other values; nothing was run", out)
        self.assertEqual(self.record(r)["replied"], before["replied"])
        self.assertEqual(self.lang(), "zh-CN")
        self.assertEqual(self.runs, [r])


class Generators(unittest.TestCase):
    def test_python_and_shell_make_the_same_id(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        self.addCleanup(sys.path.remove, str(ROOT / "scripts"))
        import deploy_state
        line = [l for l in (ROOT / "update.sh").read_text().splitlines() if l.startswith("new_req() {")]
        self.assertEqual(len(line), 1)
        m = re.fullmatch(r"""new_req\(\) \{ "\$PYTHON" -c '([^']*)'; \}""", line[0])
        self.assertIsNotNone(m)
        fixed = "import secrets, time; time.time = lambda: 1800000000.9; secrets.token_hex = lambda n: 'ab' * n\n"
        shell = subprocess.run([sys.executable, "-I", "-c", fixed + m[1]], capture_output=True, text=True, timeout=30)
        with mock.patch.object(deploy_state.time, "time", return_value=1800000000.9), \
                mock.patch.object(deploy_state.secrets, "token_hex", side_effect=lambda n: "ab" * n):
            py = deploy_state.new_req()
        self.assertEqual(shell.stdout.strip(), py)
        self.assertEqual(py, "6b49d200abababab")
        cloud = load("rn_routine_ids", HERE / "rn-routine.py")
        self.assertRegex(py, rf"\A{cloud.REQ}\Z")
        self.assertEqual(cloud.made(py), 1800000000)
        self.assertRegex(deploy_state.new_req(), r"\A[0-9a-f]{16}\Z")


if __name__ == "__main__":
    unittest.main()
