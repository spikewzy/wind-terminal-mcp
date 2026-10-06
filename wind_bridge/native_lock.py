"""Serialize native Wind requests with an OS-owned lock released on process exit."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import os
import time

from .common import Problem


def _try_lock(file):
    if os.name == "nt":
        import msvcrt
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(file):
    if os.name == "nt":
        import msvcrt
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(file, fcntl.LOCK_UN)


@contextmanager
def native_lock(path, timeout=5):
    with path.open("a+b", buffering=0) as file:
        # Windows byte-range locking requires a stable byte, including on new files.
        if os.fstat(file.fileno()).st_size == 0:
            file.write(b"\0")
        deadline = time.monotonic() + timeout
        while True:
            try:
                _try_lock(file)
                break
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                if time.monotonic() >= deadline:
                    raise Problem("WIND_BUSY", "Another local Wind MCP query is in progress") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(file)
