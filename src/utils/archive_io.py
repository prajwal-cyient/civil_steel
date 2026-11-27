"""Windows-safe archive paths and bounded cross-process checkpoint locking."""
from contextlib import contextmanager
import os
from pathlib import Path
import threading
import time

_lock = threading.RLock()


def io_path(path):
    path = Path(path).absolute()
    value = str(path)
    if os.name == 'nt' and not value.startswith('\\\\?\\'):
        value = '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value
    return Path(value)


def retry_replace(source, destination):
    for attempt in range(5):
        try:
            source.replace(destination)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(.025 * (2 ** attempt))


@contextmanager
def checkpoint_lock(folder):
    """Serialize API/worker checkpoints; a timeout is an explicit archive warning."""
    with _lock:
        path = io_path(folder / '.archive.lock')
        with path.open('a+b') as stream:
            if not stream.tell():
                stream.write(b'0')
                stream.flush()
            deadline = time.monotonic() + 2
            while True:
                try:
                    stream.seek(0)
                    if os.name == 'nt':
                        import msvcrt
                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Archive checkpoint lock remained busy')
                    time.sleep(.025)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
