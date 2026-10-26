"""Process-lifetime ownership, separate from the short EC mailbox lock.

Every hardware entrypoint must acquire this lease *before* constructing its EC
and hold it until all cleanup attempts finish. This is advisory userspace
ownership, not a kernel EC lock and not a hardware watchdog.
"""
from __future__ import annotations

import os
import stat

from .ec import EcError, _acquire_flock, _open_lock_file, _validate_timeout

OWNER_LOCK_PATH = "/run/gigactl/owner.lock"
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _check_root_directory(fd: int, path: str) -> None:
    info = os.fstat(fd)
    if info.st_uid != 0 or info.st_mode & 0o022:
        raise EcError(f"unsafe owner lease parent {path}: must be root-owned and not writable by others")


def _open_run_lock(path: str) -> int:
    """Walk /run with directory fds; never follow a replaceable parent symlink."""
    parts = path.split("/")[2:]
    parent_fd = os.open("/run", _DIRECTORY_FLAGS)
    try:
        _check_root_directory(parent_fd, "/run")
        for part in parts[:-1]:
            try:
                os.mkdir(part, mode=0o700, dir_fd=parent_fd)
            except FileExistsError:
                pass
            next_fd = os.open(part, _DIRECTORY_FLAGS, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = next_fd
            _check_root_directory(parent_fd, path)
        fd = os.open(parts[-1], os.O_CREAT | os.O_RDWR | os.O_NONBLOCK
                     | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1:
            raise EcError(f"unsafe owner lease file: {path}")
        os.fchmod(fd, 0o600)
        return fd
    except BaseException:
        os.close(fd)
        raise


class OwnerLease:
    """A bounded, close-on-exec flock lease; close/process exit releases it.

    ``path`` overrides outside /run are intended for isolated tests. The real
    /run hierarchy must be root-owned, non-writable by others, and symlink-free.
    The file is never unlinked: unlinking a held lock would split ownership.
    """

    def __init__(self, path: str = OWNER_LOCK_PATH, timeout_s: float = 2) -> None:
        _validate_timeout(timeout_s)
        self.path = os.path.abspath(path)
        self.timeout_s = timeout_s
        self._fd: int | None = None

    def acquire(self) -> OwnerLease:
        if self._fd is not None:
            return self
        fd = (_open_run_lock(self.path) if self.path.startswith("/run/")
              else _open_lock_file(self.path))
        try:
            _acquire_flock(fd, self.timeout_s, "EC owner lease")
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        return self

    def release(self) -> None:
        if self._fd is not None:
            fd, self._fd = self._fd, None
            os.close(fd)

    def __enter__(self) -> OwnerLease:
        return self.acquire()

    def __exit__(self, *_exc) -> None:
        self.release()
