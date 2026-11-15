import json, os, subprocess, sys, time, unittest
from pathlib import Path

from tests.cloud.support import (
    HERE,
    KEY,
    FetchFixture,
)


def fetch_after_barrier(barrier, env, mid, queue):
    """One of two real processes that start `fetch` at the same moment (module level so spawn can import it)."""
    barrier.wait()
    p = subprocess.run([sys.executable, str(HERE / "rn-fetch.py"), "fetch", "--timeout", "5", mid], env=env,
                       capture_output=True, text=True, timeout=60)
    queue.put((p.returncode, p.stdout))


class Claims(FetchFixture, unittest.TestCase):
    """Idempotent consumption (claim markers, one-time migration) and the orphan scan."""

    def setUp(self):
        super().setUp()
        self.addCleanup(self.db.close)
        self.env.update(RN_REFWD="0", RN_THREADS_DIR=str(self.d / "threads"))  # no engine: never ask the relay
        self.inbox = self.d / "inbox"

    def recent(self, mid, text="hello", age=0, key=KEY):
        """An RN-FWD row sent `age` seconds ago (put() rows date from 1970, outside the orphan window)."""
        self.n += 1
        self.row(f"01RC{self.n:022d}", self.fwd(mid, text, key=key), int((time.time() - age) * 1000))

    def fetch(self, *ids, timeout="1"):
        rc, res = self.run_fetch("fetch", "--timeout", timeout, *ids)
        by = {r["id"]: r for r in res}
        self.assertEqual(len(by), len(res), res)                      # never two results for one id
        return rc, by

    def mark(self, name, *ids):
        return subprocess.run([sys.executable, str(HERE / "rn-fetch.py"), name, *ids], env=self.env,
                              capture_output=True, text=True, timeout=60)

    def age(self, mid, *names, by=4000):
        t = time.time() - by
        for name in names:
            os.utime(self.inbox / mid / name, (t, t))

    def old_scan(self, *ids):
        """What the previous version left behind: inbox folders written by scan(), no markers, no migration."""
        driver = ("import importlib.util, sys\n"
                  "spec = importlib.util.spec_from_file_location('rnf', sys.argv[1]); m = importlib.util.module_from_spec(spec)\n"
                  "spec.loader.exec_module(m)\n"
                  "m.scan(sys.argv[3:], open(sys.argv[2]).read().strip(), 'demo', 'bot-inbox')\n")
        subprocess.run([sys.executable, "-c", driver, str(HERE / "rn-fetch.py"), str(self.d / "key"), *ids],
                       env=self.env, check=True, capture_output=True, timeout=60)

    def crash_before_settle(self, *args):
        """Runs main() but dies after store() and before the claim is written."""
        driver = ("import importlib.util, sys\n"
                  "spec = importlib.util.spec_from_file_location('rnf', sys.argv[1]); m = importlib.util.module_from_spec(spec)\n"
                  "spec.loader.exec_module(m)\n"
                  "def boom(res): raise SystemExit(9)\n"
                  "m.settle = boom\n"
                  "sys.exit(m.main(sys.argv[2:]))\n")
        p = subprocess.run([sys.executable, "-c", driver, str(HERE / "rn-fetch.py"), *args], env=self.env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual((p.returncode, p.stdout), (9, ""))

    def test_claim_arrivals_in_different_polls(self):
        self.put(self.fwd("A", "first"))
        p = subprocess.Popen([sys.executable, str(HERE / "rn-fetch.py"), "fetch", "--timeout", "20", "A", "B"],
                             env=self.env, stdout=subprocess.PIPE, text=True)
        time.sleep(3)                                                  # polls at 0, 2, 4 s: B lands before the third
        self.put(self.fwd("B", "second"))
        out, _ = p.communicate(timeout=60)
        by = {r["id"]: r for r in map(json.loads, out.splitlines())}
        self.assertEqual(p.returncode, 0, out)
        for mid in "AB":
            self.assertEqual((by[mid]["status"], by[mid]["claim"]), ("complete", "new"))
            self.assertTrue((self.inbox / mid / "claimed").exists())

    def test_claim_one_missing_until_timeout(self):
        self.put(self.fwd("A", "here"))
        rc, by = self.fetch("A", "B", timeout="2")
        self.assertEqual(rc, 4)
        self.assertEqual((by["A"]["status"], by["A"]["claim"], by["B"]["status"]), ("complete", "new", "missing"))
        claim = json.loads((self.inbox / "A" / "claimed").read_text())
        self.assertEqual(claim["id"], "A")
        self.assertTrue(claim["token"])

    def test_second_run_sees_the_claim(self):
        self.put(self.fwd("A", "here"))
        self.assertEqual(self.fetch("A")[1]["A"]["status"], "complete")
        rc, by = self.fetch("A")
        self.assertEqual((rc, by["A"]["status"]), (0, "claimed"))
        self.assertNotIn("full", by["A"])
        self.assertIn("claimed_at", by["A"])

    def test_two_concurrent_processes_claim_once(self):
        import multiprocessing
        self.put(self.fwd("A", "here"))
        ctx = multiprocessing.get_context("spawn")
        barrier, queue = ctx.Barrier(2), ctx.Queue()
        procs = [ctx.Process(target=fetch_after_barrier, args=(barrier, self.env, "A", queue)) for _ in range(2)]
        for p in procs:
            p.start()
        got = [queue.get(timeout=90) for _ in procs]
        for p in procs:
            p.join(30)
        self.assertEqual([rc for rc, _ in got], [0, 0])
        self.assertEqual(sorted(json.loads(o)["status"] for _, o in got), ["claimed", "complete"])

    def test_expired_claim_is_reclaimed(self):
        self.put(self.fwd("A", "here"))
        self.fetch("A")
        self.age("A", "claimed")
        rc, by = self.fetch("A")
        self.assertEqual((rc, by["A"]["status"], by["A"]["claim"]), (0, "complete", "reclaimed"))
        self.assertIn("full", by["A"])
        self.assertEqual(self.fetch("A")[1]["A"]["status"], "claimed")    # the new claim holds again

    def test_empty_claimed_file_still_counts(self):
        self.put(self.fwd("A", "here"))
        (self.inbox / "A").mkdir(parents=True)
        (self.inbox / "A" / "claimed").write_text("")
        self.assertEqual(self.fetch("A")[1]["A"]["status"], "claimed")
        self.age("A", "claimed")
        self.assertEqual(self.fetch("A")[1]["A"]["claim"], "reclaimed")

    def test_done_makes_it_handled(self):
        self.put(self.fwd("A", "here"))
        self.fetch("A")
        p = self.mark("done", "A")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads((self.inbox / "A" / "done").read_text())["id"], "A")
        rc, by = self.fetch("A")
        self.assertEqual((rc, by["A"]["status"]), (0, "handled"))
        self.assertNotIn("full", by["A"])

    def test_unfinished_reply_becomes_uncertain_after_the_lease(self):
        self.put(self.fwd("A", "here"))
        self.fetch("A")
        self.assertEqual(self.mark("replying", "A").returncode, 0)
        self.assertEqual(self.fetch("A")[1]["A"]["status"], "claimed")
        self.age("A", "claimed", "replying")
        rc, by = self.fetch("A")
        self.assertEqual((rc, by["A"]["status"]), (0, "uncertain"))
        self.assertEqual(Path(by["A"]["full"]).read_text(), "here")
        self.assertIn("replying_at", by["A"])

    def test_unfinished_work_is_uncertain_not_reclaimed(self):
        self.recent("A", "work")
        self.fetch("A")
        self.assertEqual(self.mark("working", "A").returncode, 0)    # then the turn dies: no replying, no done
        rc, by = self.fetch("A")
        self.assertEqual(by["A"]["status"], "claimed")
        self.assertNotIn("A", self.fetch("X")[1])                     # held within the lease: no orphan either
        self.age("A", "claimed", "working")
        rc, by = self.fetch("X")                                       # orphan scan
        self.assertEqual((by["A"]["status"], by["A"]["orphan"]), ("uncertain", True))
        self.assertIn("working_at", by["A"])
        rc, by = self.fetch("A")                                       # explicit doorbell
        self.assertEqual(by["A"]["status"], "uncertain")
        self.assertNotIn("claim", by["A"])

    def test_crash_after_claim_before_working_is_reclaimed(self):
        self.recent("A", "work")
        self.fetch("A")
        self.age("A", "claimed")
        rc, by = self.fetch("X")
        self.assertEqual((by["A"]["status"], by["A"]["claim"], by["A"]["orphan"]), ("complete", "reclaimed", True))

    def test_legacy_claim_then_crash_is_reclaimed(self):
        self.put(self.fwd("A", "old"))
        self.old_scan("A")
        rc, by = self.fetch("A")                                       # first run: migration marks A legacy
        self.assertEqual((by["A"]["status"], by["A"]["claim"]), ("complete", "legacy"))
        self.age("A", "claimed")                                       # the turn died before working
        rc, by = self.fetch("A")
        self.assertEqual((by["A"]["status"], by["A"]["claim"]), ("complete", "reclaimed"))

    def test_interrupted_migration_resumes_without_relabelling_claims(self):
        self.recent("P", "p")
        self.recent("Q", "q")
        self.old_scan("P", "Q")
        driver = ("import importlib.util, sys\n"
                  "spec = importlib.util.spec_from_file_location('rnf', sys.argv[1]); m = importlib.util.module_from_spec(spec)\n"
                  "spec.loader.exec_module(m)\n"
                  "real, calls = m.put_marker, []\n"
                  "def flaky(p, c):\n"
                  "    calls.append(p)\n"
                  "    if len(calls) == 2: raise SystemExit(9)\n"
                  "    real(p, c)\n"
                  "m.put_marker = flaky\n"
                  "m.migrate()\n")
        p = subprocess.run([sys.executable, "-c", driver, str(HERE / "rn-fetch.py")], env=self.env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 9, p.stderr)
        self.assertFalse((self.inbox / "_claims_v1").exists())
        self.assertEqual(sorted(d.name for d in self.inbox.iterdir() if (d / "legacy").exists()), ["P"])
        (self.inbox / "N").mkdir()                                     # stored and claimed by the new version
        (self.inbox / "N" / "claimed").write_text(json.dumps({"id": "N", "token": "t", "ts": 1}))
        self.fetch("X")
        self.assertTrue((self.inbox / "_claims_v1").exists())
        self.assertTrue((self.inbox / "P" / "legacy").exists())
        self.assertTrue((self.inbox / "Q" / "legacy").exists())
        self.assertFalse((self.inbox / "N" / "legacy").exists())

    def test_crash_after_store_before_claim_is_handed_out_next_time(self):
        self.recent("A", "a")
        self.crash_before_settle("fetch", "--timeout", "1", "A")
        self.assertTrue((self.inbox / "A" / "full.txt").exists())
        rc, by = self.fetch("X")                                       # as an orphan
        self.assertEqual((by["A"]["status"], by["A"]["claim"], by["A"]["orphan"]), ("complete", "new", True))
        self.recent("B", "b")
        self.crash_before_settle("fetch", "--timeout", "1", "B")
        rc, by = self.fetch("B")                                       # by its doorbell
        self.assertEqual((by["B"]["status"], by["B"]["claim"]), ("complete", "new"))

    def test_scan_then_orphan_scan_hands_it_out_once(self):
        self.fetch("X")                                                # already upgraded
        self.recent("B", "b")
        rc, res = self.run_fetch("scan")                               # diagnostic: stores, claims nothing
        self.assertEqual([r["id"] for r in res], ["B"])
        self.assertFalse((self.inbox / "B" / "claimed").exists())
        rc, by = self.fetch("X")
        self.assertEqual((by["B"]["status"], by["B"]["orphan"]), ("complete", True))
        self.assertNotIn("B", self.fetch("X")[1])

    def test_migration_marks_old_folders_legacy_once(self):
        self.recent("A", "a")
        self.recent("B", "b")
        self.old_scan()
        rc, by = self.fetch("X")
        self.assertEqual(sorted(by), ["X"])                            # old folders are not orphans
        for mid in "AB":
            self.assertTrue((self.inbox / mid / "legacy").exists())
            self.assertFalse((self.inbox / mid / "done").exists())
        self.assertTrue((self.inbox / "_claims_v1").exists())
        self.recent("C", "c")
        self.old_scan("C")                                             # unmarked folder after the migration
        rc, by = self.fetch("X")
        self.assertFalse((self.inbox / "C" / "legacy").exists())
        self.assertEqual((by["C"]["status"], by["C"]["claim"], by["C"]["orphan"]), ("complete", "new", True))

    def test_old_scan_then_upgrade_doorbell_is_legacy_not_handled(self):
        self.recent("B", "b")
        self.old_scan("B")
        rc, by = self.fetch("B")
        self.assertEqual((rc, by["B"]["status"], by["B"]["claim"]), (0, "complete", "legacy"))

    def test_old_stored_unhandled_without_doorbell_stays_until_its_doorbell(self):
        self.recent("B", "b")
        self.old_scan("B")
        self.assertNotIn("B", self.fetch("X")[1])                      # known limit: not an orphan
        rc, by = self.fetch("B")
        self.assertEqual((by["B"]["status"], by["B"]["claim"]), ("complete", "legacy"))

    def test_orphan_reported_once_then_claimed_then_handled(self):
        self.recent("B", "lost doorbell")
        rc, by = self.fetch("X")
        self.assertEqual(rc, 4)                                        # X is missing; the orphan does not change that
        self.assertEqual((by["B"]["status"], by["B"]["orphan"]), ("complete", True))
        self.assertEqual(Path(by["B"]["full"]).read_text(), "lost doorbell")
        self.assertNotIn("B", self.fetch("X")[1])
        self.assertEqual(self.fetch("B")[1]["B"]["status"], "claimed")
        self.assertEqual(self.mark("done", "B").returncode, 0)
        self.age("B", "claimed", "done")
        self.assertNotIn("B", self.fetch("X")[1])
        self.assertEqual(self.fetch("B")[1]["B"]["status"], "handled")

    def test_orphan_window(self):
        self.recent("OLD", "too old", age=8 * 86400)
        self.recent("NEW", "inside", age=6 * 86400)
        rc, by = self.fetch("X")
        self.assertEqual(sorted(by), ["NEW", "X"])
        self.env["RN_ORPHAN_DAYS"] = "9"
        self.assertIn("OLD", self.fetch("X")[1])
        self.env["RN_ORPHANS"] = "0"
        self.recent("OFF", "not scanned")
        self.assertEqual(sorted(self.fetch("X")[1]), ["X"])

    def test_bad_rows_reported_once_by_the_orphan_scan(self):
        self.recent("M", "forged", key="f" * 64)
        self.n += 1
        self.row("01RCPLAIN", "plain text to the inbox", int(time.time() * 1000))
        rc, by = self.fetch("X")
        self.assertEqual(rc, 5)
        self.assertIn("bad signature", by["M"]["reason"])
        self.assertEqual((by["rn:01RCPLAIN"]["status"], by["rn:01RCPLAIN"]["orphan"]), ("mismatch", True))
        self.assertEqual(len(list((self.inbox / "_mismatch").iterdir())), 2)
        rc, by = self.fetch("X")
        self.assertEqual((rc, sorted(by)), (4, ["X"]))
        for _ in range(2):                                             # asked for by id: every time
            rc, by = self.fetch("M")
            self.assertEqual((rc, by["M"]["status"]), (5, "mismatch"))
            self.assertNotIn("orphan", by["M"])

    def test_bad_rows_reported_once_by_fetch_and_wait_without_ids(self):
        self.recent("M", "forged", key="f" * 64)
        self.recent("M", "forged again", key="f" * 64)                # two bad rows, one id
        self.row("01RCPLAIN", "plain text to the inbox", int(time.time() * 1000))
        rc, res = self.run_fetch("fetch", "--timeout", "1")
        self.assertEqual(rc, 5)
        self.assertEqual(sorted(r["id"] for r in res), ["M", "rn:01RCPLAIN"])
        for cmd in ("fetch", "wait"):
            rc, res = self.run_fetch(cmd, "--timeout", "1")
            self.assertEqual((rc, res), (0, []), cmd)
        rc, by = self.fetch("X")                                       # nor through the orphan scan
        self.assertEqual((rc, sorted(by)), (4, ["X"]))
        rc, res = self.run_fetch("scan")                               # the diagnostic scan still shows them
        self.assertEqual((rc, sorted(r["id"] for r in res)), (5, ["M", "rn:01RCPLAIN"]))
        for _ in range(2):                                             # asked for by id: every time
            rc, by = self.fetch("M")
            self.assertEqual((rc, by["M"]["status"]), (5, "mismatch"))

    def test_bad_row_reported_by_the_orphan_scan_stays_quiet_without_ids(self):
        self.recent("N", "forged", key="f" * 64)
        rc, by = self.fetch("X")
        self.assertEqual((rc, by["N"]["status"]), (5, "mismatch"))
        for cmd in ("fetch", "wait"):
            rc, res = self.run_fetch(cmd, "--timeout", "1")
            self.assertEqual((rc, res), (0, []), cmd)
        rc, by = self.fetch("N")
        self.assertEqual((rc, by["N"]["status"]), (5, "mismatch"))

    def test_doorbell_ids_are_not_also_orphans(self):
        self.recent("A", "a")
        self.recent("B", "b")                                          # no doorbell: an orphan
        rc, res = self.run_fetch("fetch", "--timeout", "1", "A")
        self.assertEqual([r["id"] for r in res], ["A", "B"])          # A once, from its doorbell
        self.assertNotIn("orphan", res[0])
        self.assertEqual((res[0]["claim"], res[1]["orphan"]), ("new", True))

    def test_id_collision(self):
        self.put(self.fwd("A", "a"))
        (self.inbox / "A").mkdir(parents=True)
        (self.inbox / "A" / "claimed").write_text(json.dumps({"id": "other", "token": "t", "ts": 1}))
        rc, by = self.fetch("A")
        self.assertEqual((rc, by["A"]["status"]), (5, "mismatch"))
        self.assertIn("id collision", by["A"]["reason"])
        p = self.mark("done", "A")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("id collision", p.stderr)
        self.assertFalse((self.inbox / "A" / "done").exists())

    def test_purge_removes_markers_and_remembered_bad_rows(self):
        self.recent("A", "a")
        self.recent("M", "forged", key="f" * 64)
        self.fetch("X")
        self.assertEqual(self.mark("done", "A").returncode, 0)
        rc, res = self.run_fetch("purge", "0")
        self.assertEqual(res[0]["purged"], {"messages": 2, "inbox": 1, "stray": 0, "threads": 0, "mismatch": 1})
        self.assertEqual(sorted(p.name for p in self.inbox.iterdir()), ["_claims_v1", "_lock", "_mismatch"])
        self.assertEqual(list((self.inbox / "_mismatch").iterdir()), [])
        rc, by = self.fetch("X")
        self.assertEqual((rc, sorted(by)), (4, ["X"]))

    def test_exit_codes_ignore_handled_claimed_uncertain(self):
        for mid in "HCU":
            self.put(self.fwd(mid, mid))
        self.fetch("H", "C", "U")
        self.mark("done", "H")
        self.mark("replying", "U")
        self.age("U", "claimed", "replying")
        rc, by = self.fetch("H", "C", "U")
        self.assertEqual(rc, 0)
        self.assertEqual([by[m]["status"] for m in "HCU"], ["handled", "claimed", "uncertain"])
        rc, by = self.fetch("H", "C", "U", "MISS")
        self.assertEqual((rc, by["MISS"]["status"]), (4, "missing"))
