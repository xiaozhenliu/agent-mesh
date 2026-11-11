#!/usr/bin/env python3
import base64
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "relay_targets.py"
N0 = "https://aps1-1.relay.n0.iroh.link/"
SFO = "https://relay-sfo1.compute.reasonmachines.com/"


def signed_list(urls):
    part = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    claims = {"iss": "x", "aud": "reason-network", "iat": 1, "exp": 2, "relays": [{"url": u} for u in urls]}
    return json.dumps({"relays": urls, "signed": f"{part({'alg': 'EdDSA'})}.{part(claims)}.sig"})


class Targets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.home = self.base / "network"
        self.home.mkdir()
        self.rn = self.base / "reason-network"

    def tearDown(self):
        self.tmp.cleanup()

    def engine(self, version, team=None):
        team_cmd = f"printf '%s\\n' {shlex.quote(team)}" if team is not None else "echo 'error: not in a team'; exit 1"
        self.rn.write_text(f"#!/bin/sh\ncase \"$1\" in\n  --version) echo 'reason-network {version}' ;;\n"
                           f"  team) {team_cmd} ;;\n  *) exit 0 ;;\nesac\n")
        self.rn.chmod(0o755)

    def run_it(self, pinned="0.10.7"):
        r = subprocess.run([sys.executable, str(SCRIPT), "--rn", str(self.rn), "--home", str(self.home),
                            "--pinned", pinned], capture_output=True, text=True, timeout=30)
        lines = r.stdout.splitlines()
        return r.returncode, lines[0] if lines else "", lines[1:]

    def test_no_engine_uses_the_pinned_defaults(self):
        code, source, urls = self.run_it("0.10.7")
        self.assertEqual((code, urls), (0, [N0]))
        self.assertIn("not installed yet", source)
        code, _, urls = self.run_it("0.12.17")
        self.assertEqual(code, 0)
        self.assertEqual(len(urls), 4)
        self.assertTrue(all(u.endswith(".compute.reasonmachines.com/") for u in urls))
        code, source, urls = self.run_it("0.13.0")
        self.assertEqual((code, urls), (3, []))
        self.assertTrue(source.startswith("unknown:"))

    def test_an_explicit_team_relay_wins_on_any_version(self):
        for version in ("0.10.7", "0.12.17", "0.13.0"):
            with self.subTest(version=version):
                self.engine(version, "team t1 · relay: https://relay.example.org./\n  demo  mac  this machine")
                code, source, urls = self.run_it()
                self.assertEqual((code, source, urls), (0, "the team's relay", ["https://relay.example.org./"]))

    def test_default_label_is_not_trusted_and_the_version_decides(self):
        for label in ("n0 public relays", "Reason's (the default)"):
            with self.subTest(label=label):
                self.engine("0.10.7", f"team t1 · relay: {label}\n  demo  mac  this machine")
                self.assertEqual(self.run_it()[2], [N0])
                self.engine("0.12.17", f"team t1 · relay: {label}\n  demo  mac  this machine")
                (self.home / "daemon.log").write_text(f"[1] net: on relay {SFO}\n")
                self.assertEqual(self.run_it()[2], [SFO])

    def test_engine_012_reads_the_last_logged_relay(self):
        self.engine("0.12.17", "team t1 · relay: Reason's (the default)\n  demo  mac  this machine")
        sin = "https://relay-sin1.compute.reasonmachines.com/"
        (self.home / "daemon.log").write_text(f"[1] net: on relay {SFO}\n[2] net: relay unreachable: x\n"
                                              f"[3] net: on relay {sin}\n[4] net: connected to ab (out)\n")
        (self.home / "relays.json").write_text(signed_list([SFO]))
        code, source, urls = self.run_it()
        self.assertEqual((code, urls), (0, [sin]))
        self.assertIn("daemon.log", source)
        with open(self.home / "daemon.log", "a") as f:
            f.write("[5] net: connected to ab (out)\n" * 60000)
        self.assertEqual(self.run_it()[2], [sin])

    def test_engine_012_falls_back_to_relays_json_then_unknown(self):
        self.engine("0.12.17")
        (self.home / "daemon.log").write_text("[1] net: connected to ab (out)\n")
        listed = [SFO, "https://relay-fra1.compute.reasonmachines.com/"]
        (self.home / "relays.json").write_text(signed_list(listed))
        code, source, urls = self.run_it()
        self.assertEqual((code, urls), (0, listed))
        self.assertIn("relays.json", source)
        for bad in ("{", '{"signed": "abc"}', '{"signed": "a.!!!.c"}', signed_list([])):
            with self.subTest(bad=bad[:20]):
                (self.home / "relays.json").write_text(bad)
                code, source, urls = self.run_it()
                self.assertEqual((code, urls), (3, []))
                self.assertTrue(source.startswith("unknown:"), source)
        (self.home / "relays.json").unlink()
        self.assertEqual(self.run_it()[0], 3)

    def test_other_or_unreadable_versions_are_unknown(self):
        for version in ("0.11.0", "0.13.2", "dev"):
            with self.subTest(version=version):
                self.engine(version, "team t1 · relay: Reason's (the default)")
                code, source, urls = self.run_it()
                self.assertEqual((code, urls), (3, []))
                self.assertTrue(source.startswith("unknown:"), source)


if __name__ == "__main__":
    unittest.main()
