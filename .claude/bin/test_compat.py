"""Tests of compat.py, the flow's operating-system layer. Both branches (Windows and POSIX) run on any system by
patching compat.IS_WINDOWS; the lock is also tested for real on the system that runs the suite.

Run: python3 -m unittest discover -s .claude/bin -p test_compat.py
"""
import errno
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compat  # noqa: E402
import testutil  # noqa: E402


class TempDir(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="compat-")).resolve()
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)


class LockedTests(TempDir):
    def test_no_write_is_lost_between_threads(self):
        path = self.dir / "counter.json"
        path.write_text("[]", encoding="utf-8")

        def append(n):
            with open(path, "r+", encoding="utf-8") as fh, compat.locked(fh):  # one handle per thread
                fh.seek(0)
                data = json.loads(fh.read() or "[]")
                data.append(n)
                fh.seek(0)
                fh.truncate()
                fh.write(json.dumps(data))

        threads = [threading.Thread(target=lambda i=i: [append(i * 100 + k) for k in range(20)]) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(json.loads(path.read_text(encoding="utf-8"))), 80)

    def test_windows_branch_locks_byte_zero_and_retries(self):
        msvcrt = mock.Mock(LK_LOCK=1, LK_UNLCK=0)
        msvcrt.locking.side_effect = [OSError(errno.EDEADLK, "busy"), None, None]
        fh = mock.Mock()
        fh.fileno.return_value = 7
        with mock.patch.object(compat, "IS_WINDOWS", True), mock.patch.dict(sys.modules, {"msvcrt": msvcrt}), \
                mock.patch.object(compat.time, "sleep"):
            with compat.locked(fh):
                self.assertEqual(msvcrt.locking.call_count, 2)
        self.assertEqual(msvcrt.locking.call_args_list[-1], mock.call(7, 0, 1))
        calls = [c[0] for c in fh.method_calls]
        self.assertLess(calls.index("flush"), len(calls) - 1, "flushed before unlocking")
        self.assertEqual(fh.seek.call_args_list[0], mock.call(0))


    def test_windows_branch_fails_on_errors_other_than_contention(self):
        # Only "someone else holds it" is worth waiting for; anything else would spin forever.
        msvcrt = mock.Mock(LK_LOCK=1, LK_UNLCK=0)
        # a third attempt means it is retrying: stop there instead of hanging the suite
        msvcrt.locking.side_effect = [OSError(errno.EBADF, "bad descriptor"), OSError(errno.EBADF, "bad descriptor"),
                                      RuntimeError("it retried a non-contention error")]
        fh = mock.Mock()
        fh.fileno.return_value = 7
        with mock.patch.object(compat, "IS_WINDOWS", True), mock.patch.dict(sys.modules, {"msvcrt": msvcrt}), \
                mock.patch.object(compat.time, "sleep") as sleep:
            with self.assertRaises(OSError):
                with compat.locked(fh):
                    pass
        self.assertEqual(msvcrt.locking.call_count, 1)
        sleep.assert_not_called()


class ProcessTests(unittest.TestCase):
    def test_group_kwargs_posix(self):
        with mock.patch.object(compat, "IS_WINDOWS", False):
            self.assertEqual(compat.group_kwargs(), {"start_new_session": True})

    def test_group_kwargs_windows(self):
        with mock.patch.object(compat, "IS_WINDOWS", True), \
                mock.patch.object(subprocess, "CREATE_NEW_PROCESS_GROUP", 512, create=True):
            self.assertEqual(compat.group_kwargs(), {"creationflags": 512})

    def test_kill_tree_posix_uses_the_group(self):
        with mock.patch.object(compat, "IS_WINDOWS", False), mock.patch("os.killpg", create=True) as killpg:
            compat.kill_tree(123)
        self.assertEqual(killpg.call_args[0][0], 123)

    def test_kill_tree_windows_uses_taskkill(self):
        with mock.patch.object(compat, "IS_WINDOWS", True), mock.patch.object(compat.subprocess, "run") as run:
            compat.kill_tree(123)
        self.assertEqual(run.call_args[0][0], ["taskkill", "/T", "/F", "/PID", "123"])

    def test_kill_tree_never_raises(self):
        with mock.patch.object(compat, "IS_WINDOWS", False), \
                mock.patch("os.killpg", create=True, side_effect=ProcessLookupError):
            compat.kill_tree(123)
        with mock.patch.object(compat, "IS_WINDOWS", True), \
                mock.patch.object(compat.subprocess, "run", side_effect=FileNotFoundError):
            compat.kill_tree(123)


class ResolveTests(TempDir):
    def test_finds_a_program_on_path(self):
        prog = testutil.fake_exe(self.dir, "faketool", "print('hi')\n")
        with mock.patch.dict(os.environ, {"PATH": f"{self.dir}{os.pathsep}{os.environ['PATH']}"}):
            argv = compat.resolve(["faketool", "--x"])
        self.assertEqual(Path(argv[0]).resolve(), prog.resolve())
        self.assertEqual(argv[1:], ["--x"])
        out = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(out.stdout.strip(), "hi")

    def test_unknown_program_is_left_alone(self):
        argv = ["no-such-program-xyz", "a"]
        self.assertEqual(compat.resolve(argv), argv)
        self.assertIsNot(compat.resolve(argv), argv)

    def test_empty(self):
        self.assertEqual(compat.resolve([]), [])


class TextTests(TempDir):
    def test_content_hash_ignores_crlf(self):
        lf, crlf = self.dir / "lf", self.dir / "crlf"
        lf.write_bytes(b"a\nb\n")
        crlf.write_bytes(b"a\r\nb\r\n")
        self.assertEqual(compat.content_hash(lf), compat.content_hash(crlf))
        other = self.dir / "other"
        other.write_bytes(b"a\nc\n")
        self.assertNotEqual(compat.content_hash(lf), compat.content_hash(other))

    def test_read_stdin_text_is_utf8(self):
        fake = mock.Mock(buffer=io.BytesIO("ñ · €".encode("utf-8")))
        with mock.patch.object(sys, "stdin", fake):
            self.assertEqual(compat.read_stdin_text(), "ñ · €")

    def test_utf8_stdio_reconfigures(self):
        out = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
        with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", out):
            compat.utf8_stdio()
            self.assertEqual(sys.stdout.encoding, "utf-8")


class TestutilTests(TempDir):
    def test_claude_command_uses_forward_slashes(self):
        value = testutil.claude_command(self.dir / "fake.py")
        self.assertNotIn("\\", value)
        self.assertTrue(value.endswith("fake.py") or value.endswith("fake.py'"))


if __name__ == "__main__":
    unittest.main()
