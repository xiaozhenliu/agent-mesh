#!/usr/bin/env python3
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


if __name__ == "__main__":
    unittest.main()
