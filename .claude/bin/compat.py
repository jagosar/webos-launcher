"""The operating-system layer of the flow: the only module that knows whether it runs on Windows or on POSIX.

`agentic.py` and `upgrade.py` use it for file locks, process groups and killing, finding executables (`.cmd` files on
Windows), UTF-8 standard streams and line-ending-independent content hashes. Every function decides on IS_WINDOWS at
call time, so tests can patch it and check both branches on any system.
"""
import contextlib
import errno
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

IS_WINDOWS = os.name == "nt"
LOCK_RETRY_S = 0.05  # msvcrt's LK_LOCK gives up after ~10 s with OSError: keep trying while another process holds it
# The errors that mean "someone else holds the lock". Any other OSError is real and must not be retried forever.
LOCK_BUSY_ERRNOS = {errno.EDEADLK, getattr(errno, "EDEADLOCK", errno.EDEADLK), errno.EACCES}


@contextlib.contextmanager
def locked(fh):
    """Exclusive lock of an open file for the block. The data is flushed before the lock is released."""
    if IS_WINDOWS:
        import msvcrt
        fh.seek(0)
        while True:
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                break
            except OSError as e:
                if e.errno not in LOCK_BUSY_ERRNOS:
                    raise
                time.sleep(LOCK_RETRY_S)
        try:
            yield fh
        finally:
            fh.flush()
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield fh
        finally:
            fh.flush()
            fcntl.flock(fh, fcntl.LOCK_UN)


def group_kwargs() -> dict:
    """Popen arguments that start the child in its own process group, so kill_tree can end it with its children."""
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def kill_tree(pid: int) -> None:
    """Ends the process and its children. Never raises: the process may already be gone."""
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        else:
            import signal
            os.killpg(pid, signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        pass


def resolve(argv: list[str]) -> list[str]:
    """A copy of argv with the program replaced by its full path when it is on PATH (on Windows, `npm` becomes
    `npm.cmd` through PATHEXT). Unchanged otherwise, so the caller still gets FileNotFoundError."""
    if not argv:
        return list(argv)
    found = shutil.which(argv[0])
    return [found or argv[0], *argv[1:]]


def utf8_stdio() -> None:
    """Standard output and error in UTF-8, whatever the console's encoding."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def read_stdin_text() -> str:
    """Standard input decoded as UTF-8 (hooks and the workflow send UTF-8 JSON)."""
    return sys.stdin.buffer.read().decode("utf-8")


def content_hash(path) -> str:
    """sha256 of the file with CRLF turned into LF: a checkout with Windows line endings counts as the same content."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
