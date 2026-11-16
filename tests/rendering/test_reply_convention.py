#!/usr/bin/env python3
import json
import os
import signal
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import reply_convention


class OverwriteInPlace(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "AGENTS.md"
        self.old = ("# Rules\n" + "old line\n" * 50).encode()
        self.new = ("# Rules\n" + "new line, a little longer\n" * 50).encode()
        self.path.write_bytes(self.old)
        self.backup = reply_convention.backup_path(self.path)
        self.real_put = reply_convention.put

    def failing_put(self, error, fail_calls):
        calls = []

        def put(stream, data):
            calls.append(data)
            if len(calls) in fail_calls:
                stream.seek(0)
                stream.write(data[:20])
                raise error
            self.real_put(stream, data)
        return put

    def test_success_keeps_the_inode_and_removes_the_backup(self):
        inode = self.path.stat().st_ino
        reply_convention.overwrite_in_place(self.path, self.old, self.new)
        self.assertEqual(self.path.read_bytes(), self.new)
        self.assertEqual(self.path.stat().st_ino, inode)
        self.assertFalse(os.path.lexists(self.backup))

    def test_a_failed_write_puts_the_original_back(self):
        for error in (OSError(28, "No space left on device"), KeyboardInterrupt()):
            self.path.write_bytes(self.old)
            with mock.patch.object(reply_convention, "put", self.failing_put(error, {1})):
                with self.assertRaises(OSError) as caught:
                    reply_convention.overwrite_in_place(self.path, self.old, self.new)
            self.assertIn("the original was put back", str(caught.exception))
            self.assertEqual(self.path.read_bytes(), self.old, error)
            self.assertFalse(os.path.lexists(self.backup), error)

    def test_a_failed_restore_leaves_the_original_in_the_backup(self):
        with mock.patch.object(reply_convention, "put", self.failing_put(OSError(5, "I/O error"), {1, 2})):
            with self.assertRaises(OSError) as caught:
                reply_convention.overwrite_in_place(self.path, self.old, self.new)
        self.assertIn(f"the original is in {self.backup}", str(caught.exception))
        self.assertEqual(self.backup.read_bytes(), self.old)
        self.assertEqual(self.backup.stat().st_mode & 0o777, 0o600)

    def test_a_backup_that_cannot_be_made_durable_stops_before_the_file_changes(self):
        with mock.patch.object(reply_convention, "fsync_dir", side_effect=OSError(5, "I/O error")):
            with self.assertRaises(OSError):
                reply_convention.overwrite_in_place(self.path, self.old, self.new)
        self.assertEqual(self.path.read_bytes(), self.old)
        self.assertFalse(os.path.lexists(self.backup))

    def test_signals_are_held_during_the_write_and_restored_after(self):
        seen = []

        def put(stream, data):
            seen.append(signal.getsignal(signal.SIGINT))
            self.real_put(stream, data)
        before = signal.getsignal(signal.SIGINT)
        with mock.patch.object(reply_convention, "put", put):
            reply_convention.overwrite_in_place(self.path, self.old, self.new)
        self.assertEqual(seen, [signal.SIG_IGN])
        self.assertEqual(signal.getsignal(signal.SIGINT), before)

    def test_refresh_refuses_while_a_backup_from_an_interrupted_refresh_exists(self):
        self.backup.write_bytes(self.old)
        with self.assertRaises(SystemExit) as caught:
            reply_convention.main(["refresh", str(self.path)])
        self.assertIn("left from an interrupted refresh", str(caught.exception))
        self.assertIn("nothing was changed", str(caught.exception))
        self.assertEqual(self.path.read_bytes(), self.old)


    def test_refresh_messages_follow_the_language(self):
        self.backup.write_bytes(self.old)
        with mock.patch.dict(os.environ, {"AGENT_MESH_LANG": "zh-CN"}):
            with self.assertRaises(SystemExit) as caught:
                reply_convention.main(["refresh", str(self.path)])
        self.assertIn("是上次中断的刷新留下的", str(caught.exception))
        self.assertIn("没有做任何改动", str(caught.exception))
        with mock.patch.dict(os.environ, {"AGENT_MESH_LANG": "zh-CN"}):
            with self.assertRaises(SystemExit) as caught:
                reply_convention.main(["refresh", str(self.path.parent)])
        self.assertIn("不是普通文件；没有做任何改动", str(caught.exception))
        self.assertEqual(self.path.read_bytes(), self.old)


class SeveralBots(unittest.TestCase):
    TEXT = ("# Rules\nbefore\n<!-- agent-mesh:bot-address lang={lang} -->\n- old line\n"
            "<!-- /agent-mesh:bot-address -->\nafter\n")

    def block(self, text):
        start = text.index("-->\n") + 4
        return text[start:text.index("<!-- /agent-mesh:bot-address -->")]

    def check(self, lang, entries, want):
        text = reply_convention.fill(self.TEXT.format(lang=lang), "demo", entries)
        self.assertTrue(text.startswith(f"# Rules\nbefore\n<!-- agent-mesh:bot-address lang={lang} -->\n"))
        self.assertTrue(text.endswith("<!-- /agent-mesh:bot-address -->\nafter\n"))
        self.assertEqual(self.block(text), want)
        again = reply_convention.fill(text, "demo", entries)
        self.assertEqual(again, text)
        return text

    def test_the_block_follows_added_renamed_and_removed_bots(self):
        one = self.check("en", [("alpha", None)], "- This team's Grok Bot address: `demo/alpha`.\n")
        two = self.check("en", [("alpha", None), ("beta", "Beta")],
                         "- This team's Grok Bot addresses:\n  - `demo/alpha`\n  - `demo/beta` (called “Beta” in Grok)\n")
        renamed = reply_convention.fill(two, "demo", [("alpha", "A"), ("beta", "Beta Two")])
        self.assertEqual(self.block(renamed), "- This team's Grok Bot addresses:\n  - `demo/alpha` (called “A” in Grok)\n"
                                              "  - `demo/beta` (called “Beta Two” in Grok)\n")
        self.assertEqual(reply_convention.fill(two, "demo", [("alpha", None)]), one)
        first_gone = reply_convention.fill(two, "demo", [("beta", "Beta")])
        self.assertEqual(self.block(first_gone), "- This team's Grok Bot address: `demo/beta` (called “Beta” in Grok).\n")
        self.check("zh-CN", [("alpha", None), ("beta", "小助手")],
                   "- 本团队 Grok Bot 的地址：\n  - `demo/alpha`\n  - `demo/beta`（在 Grok 里叫「小助手」）\n")
        self.check("zh-CN", [("beta", "小助手")], "- 本团队 Grok Bot 的地址：`demo/beta`（在 Grok 里叫「小助手」）。\n")

    def test_the_addresses_are_the_bots_that_can_receive_mail(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        home = Path(temp.name)
        env = home / ".config/agent-relay/env"
        env.parent.mkdir(parents=True)
        env.write_text("RN_BIN=/x\nPERSON=demo\nLISTEN_NAME=alpha\nCLOUD_NAME=alpha-cloud\nINBOX_NAME=alpha-inbox\n")
        record = home / ".local/lib/agent-mesh/deployed.json"
        record.parent.mkdir(parents=True)
        bots = {"beta": {"grok_name": "Beta", "stage": "relay", "cloud": {"state": "confirmed"}, "added_ms": 1},
                "gamma": {"stage": "configured", "cloud": {"state": "pending"}, "added_ms": 2},
                "delta": {"stage": "relay", "cloud": {"state": "confirmed"}, "added_ms": 3}}
        data = {"schema": 1, "relay_name": "alpha", "bots": bots}
        record.write_text(json.dumps(data))
        self.assertEqual(reply_convention.addresses(home), [("alpha", None), ("beta", "Beta"), ("delta", None)])
        data["removing"] = {"name": "delta", "kind": "added", "step": "stopped"}
        record.write_text(json.dumps(data))
        self.assertEqual(reply_convention.addresses(home), [("alpha", None), ("beta", "Beta")])
        env.unlink()
        data.update(legacy_removed=True)
        data.pop("relay_name")
        record.write_text(json.dumps(data))
        self.assertEqual(reply_convention.addresses(home), [("beta", "Beta")])
