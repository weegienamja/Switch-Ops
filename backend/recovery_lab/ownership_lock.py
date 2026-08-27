"""Kernel-held locks, so "is the owner still running?" has a real answer.

Two different questions need locking, and neither is served by a lock file whose
mere existence means "taken". A harness that dies abruptly -- which is precisely
what the crash experiment does on purpose -- would leave such a file behind and
block every later run until somebody deleted it by hand. Worse, a reconciler
that learned to ignore stale lock files would have learned to ignore live ones.

So both locks here are byte-range locks the operating system holds on an open
file descriptor. The kernel releases them when the process ends, however it
ends: normal exit, unhandled exception, ``os._exit``, or being killed. Nothing
is inferred from a file existing.

* The **journal lock** is held for the few microseconds of one read-modify-write
  so two processes cannot lose each other's records.
* The **operation lock** is held for as long as an operation owns an address.
  A later process can acquire it only if the owning process is gone, which is
  how crash reconciliation proves it is not racing a live run.
"""
from __future__ import annotations

import os
import sys
import hashlib
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

#: One byte is enough: the lock is advisory between cooperating processes and
#: the range only has to be the same on both sides.
_LOCK_BYTES = 1


class LockUnavailable(RuntimeError):
    """Somebody else holds this lock right now, and is still alive."""


class LockingUnsupported(RuntimeError):
    """No usable file-locking primitive on this platform."""


def _lock_fd(fd: int, *, blocking: bool) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    if sys.platform == "win32":
        import msvcrt

        mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        try:
            msvcrt.locking(fd, mode, _LOCK_BYTES)
        except OSError as error:  # PermissionError when held elsewhere
            raise LockUnavailable(str(error)) from error
        return
    try:
        import fcntl
    except ImportError as error:  # pragma: no cover - neither Windows nor POSIX
        raise LockingUnsupported(
            "This platform provides neither msvcrt nor fcntl locking, so "
            "ownership cannot be established safely."
        ) from error
    flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
    try:
        fcntl.flock(fd, flags)
    except OSError as error:
        raise LockUnavailable(str(error)) from error


def _unlock_fd(fd: int) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    if sys.platform == "win32":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, _LOCK_BYTES)
        except OSError:
            # Already released, or the handle is going away anyway. The kernel
            # is the authority here, not this call.
            pass
        return
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


def _open_lock_file(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    return os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)


@contextmanager
def exclusive(path: Path, *, blocking: bool = True) -> Iterator[None]:
    """Hold an exclusive lock on `path` for the duration of the block."""
    fd = _open_lock_file(path)
    try:
        _lock_fd(fd, blocking=blocking)
        try:
            yield
        finally:
            _unlock_fd(fd)
    finally:
        os.close(fd)


class OperationLock:
    """A lock held for as long as one operation owns an address.

    Deliberately not a context manager. The crash experiment must be able to
    take this lock and then die without releasing it, and a ``with`` block would
    invite a ``finally`` that defeats the point. Release is either explicit or
    performed by the kernel when the process ends.
    """

    def __init__(self, directory: Path, operation_id: str) -> None:
        if not operation_id:
            raise ValueError("operation_id must not be empty")
        # Journal content is data, not a pathname. Hashing prevents a malformed
        # or hand-edited operation id from escaping the lock directory while
        # still giving every process a deterministic name for the same id.
        name = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()
        self.path = Path(directory) / f"operation-{name}.lock"
        self._fd: int | None = None

    def acquire(self) -> None:
        """Claim the operation. Raises LockUnavailable if somebody holds it."""
        if self._fd is not None:
            raise RuntimeError("This OperationLock is already acquired.")
        fd = _open_lock_file(self.path)
        try:
            _lock_fd(fd, blocking=False)
        except Exception:
            os.close(fd)
            raise
        self._fd = fd

    def release(self) -> None:
        if self._fd is None:
            return
        _unlock_fd(self._fd)
        os.close(self._fd)
        self._fd = None


def owner_process_is_gone(directory: Path, operation_id: str) -> bool:
    """Can this process take the operation lock, i.e. is the owner dead?

    A missing lock file counts as gone: an operation that never took a lock, or
    whose lock file was cleaned up, is not evidence of a live process. Ownership
    of the *address* is proved separately and far more strictly; this answers
    only "am I racing somebody".
    """
    probe = OperationLock(directory, operation_id)
    try:
        probe.acquire()
    except LockUnavailable:
        return False
    probe.release()
    return True


def owner_process_is_gone_read_only(directory: Path, operation_id: str) -> bool:
    """Probe liveness without creating a lock file or directory.

    `crash-status` promises not to persist anything. Phase A necessarily made
    the operation lock before writing intent, so an absent lock path is enough
    to answer "no cooperating owner currently holds it" without creating a
    marker merely to ask the question.
    """
    probe = OperationLock(directory, operation_id)
    try:
        fd = os.open(str(probe.path), os.O_RDWR)
    except FileNotFoundError:
        return True
    try:
        try:
            _lock_fd(fd, blocking=False)
        except LockUnavailable:
            return False
        _unlock_fd(fd)
        return True
    finally:
        os.close(fd)
