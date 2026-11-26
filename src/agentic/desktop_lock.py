"""Cross-process exclusion for server-owned engineering desktop operations."""

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import tempfile


DESKTOP_LOCK_PATH = Path(tempfile.gettempdir()) / "civil-steel-standard-agentic-desktop.lock"


class DesktopBusyError(RuntimeError):
    """Another cooperating process is using the engineering desktop."""


@contextmanager
def desktop_lock():
    with open(DESKTOP_LOCK_PATH, "a+b") as lock_file:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                raise DesktopBusyError("Another engineering operation is using the desktop tools.") from exc
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise DesktopBusyError("Another engineering operation is using the desktop tools.") from exc
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
