"""Guarded Embedded Controller transport.

``ec_sys`` is the default native interface. Missing native access fails closed;
raw ``/dev/port`` access requires explicit opt-in and can race the kernel EC
driver. Even a raw *read* sends command/address bytes to I/O ports.

The advisory transaction lock only serializes cooperating userspace callers.
It does not synchronize with kernel/ACPI users or make raw port access safe.
Process-lifetime ownership is a separate lease in ``ownership.py``.
"""
from __future__ import annotations

import errno
import fcntl
import math
import os
import stat
import time
from contextlib import contextmanager
from typing import Callable, Iterator, Protocol

from .platform import PlatformError, PlatformInfo, require_supported_platform

EC_SYS_IO = "/sys/kernel/debug/ec/ec0/io"
# The kernel publishes whether ec_sys will accept writes, so the daemon can ask
# instead of discovering it by having a write refused (#25).
EC_SYS_WRITE_SUPPORT = "/sys/module/ec_sys/parameters/write_support"
LOCK_PATH = "/run/lock/gigactl-ec.lock"

READ_ONLY_REASON = (
    "the kernel's EC interface is read-only: the ec_sys module was loaded "
    "without write support. Reload it with "
    "'sudo modprobe -r ec_sys && sudo modprobe ec_sys write_support=1', "
    "or configure write_support=1 and reboot. Raw access is not an automatic fallback."
)


class EcError(Exception):
    """An EC read/write failed or timed out."""


class EcNotWritable(EcError):
    """The EC can be read but not written, and we know why before trying.

    Distinct from a failed write: nothing was attempted, the cause is known,
    and the message says what to do about it.
    """


class EcBackend(Protocol):
    name: str

    def read(self, offset: int) -> int: ...
    def write(self, offset: int, value: int) -> None: ...
    def unwritable_reason(self) -> str | None: ...


def _validate_u8(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
        raise ValueError(f"{label} must be an integer byte (0..255), got {value!r}")
    return value


def _validate_timeout(timeout_s: float) -> None:
    if isinstance(timeout_s, bool) or not math.isfinite(timeout_s) or timeout_s < 0:
        raise ValueError("timeout_s must be finite and nonnegative")


def _acquire_flock(fd: int, timeout_s: float, label: str) -> None:
    """Bound contention without ever issuing a blocking flock operation."""
    _validate_timeout(timeout_s)
    deadline = time.monotonic() + timeout_s
    contended = False
    while True:
        if contended and time.monotonic() >= deadline:
            raise EcError(
                f"{label} lock timeout after {timeout_s:g}s; "
                "another cooperating EC user may hold it"
            )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EACCES, errno.EINTR):
                raise
            contended = True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EcError(
                    f"{label} lock timeout after {timeout_s:g}s; "
                    "another cooperating EC user may hold it"
                ) from exc
            time.sleep(min(0.01, remaining))


@contextmanager
def _open_io(path: str, flags: int):
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        stream = os.fdopen(fd, "r+b" if flags & os.O_RDWR else "rb", buffering=0)
    except BaseException:
        os.close(fd)
        raise
    with stream:
        yield stream


def _read_byte(stream, offset: int) -> int:
    stream.seek(offset)
    data = stream.read(1)
    if data is None or len(data) != 1:
        raise EcError(f"short read at {offset:#04x}")
    return data[0]


def _write_byte(stream, offset: int, value: int) -> None:
    stream.seek(offset)
    if stream.write(bytes([value])) != 1:
        raise EcError(f"short write at {offset:#04x}")


def _open_lock_file(path: str) -> int:
    """No truncation, symlink following, FIFO blocking, or permissive lock fd."""
    path = os.path.abspath(path)
    parent, name = os.path.split(path)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        fd = os.open(name, os.O_CREAT | os.O_RDWR | os.O_NONBLOCK
                     | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid():
            raise EcError(f"unsafe EC lock file: {path}")
        os.fchmod(fd, 0o600)
        return fd
    except BaseException:
        os.close(fd)
        raise


class EcSysBackend:
    """Byte access via the ``ec_sys`` debugfs file."""
    name = "ec_sys"

    def __init__(self, path: str = EC_SYS_IO,
                 write_support_path: str = EC_SYS_WRITE_SUPPORT) -> None:
        self.path = path
        self.write_support_path = write_support_path

    @staticmethod
    def available(path: str = EC_SYS_IO) -> bool:
        """Whether this backend can be used at all — which means reads.

        Deliberately not a writability check: reads work regardless, and a
        machine whose module lacks write support still deserves its
        temperatures. Whether writes will land is ``unwritable_reason``.
        """
        return os.path.exists(path)

    def unwritable_reason(self) -> str | None:
        """Why a write would be refused, or None if one should land.

        Read fresh every time rather than cached at startup, so reloading the
        module fixes the machine without restarting the daemon. An absent
        parameter means this is not a gate we recognise; the native driver
        remains the write authority. An unsafe or unreadable setting refuses
        writes rather than pretending that write support was enabled.
        """
        try:
            with _open_io(self.write_support_path, os.O_RDONLY) as stream:
                data = stream.read(16)
                if data is None:
                    return "cannot safely read ec_sys write_support: setting is not ready"
                setting = data.decode("ascii").strip()
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError) as exc:
            return f"cannot safely read ec_sys write_support: {exc}"
        return None if setting.upper() in ("Y", "1") else READ_ONLY_REASON

    def read(self, offset: int) -> int:
        _validate_u8(offset, "offset")
        with _open_io(self.path, os.O_RDONLY) as stream:
            return _read_byte(stream, offset)

    def write(self, offset: int, value: int) -> None:
        _validate_u8(offset, "offset")
        _validate_u8(value, "value")
        reason = self.unwritable_reason()
        if reason:
            # Refuse here rather than let the kernel answer EINVAL: the errno
            # alone told the user in #25 nothing they could act on.
            raise EcNotWritable(reason)
        with _open_io(self.path, os.O_RDWR) as stream:
            _write_byte(stream, offset, value)


class DevPortBackend:
    """Experimental raw ACPI EC access, never an automatic fallback.

    Unit-tested against an emulated EC port file (tests/test_ec.py); timing
    on real port I/O remains unverified. The protocol is the standard
    ACPI 12.x handshake: poll the status port's IBF/OBF flags around each
    command/data byte.
    """
    name = "dev_port"

    CMD = 0x66   # status/command port
    DATA = 0x62  # data port
    RD_EC = 0x80
    WR_EC = 0x81
    OBF = 0x01   # output buffer full (EC -> host ready)
    IBF = 0x02   # input buffer full (host -> EC pending)

    def __init__(self, path: str = "/dev/port", *, timeout_s: float = 0.25) -> None:
        _validate_timeout(timeout_s)
        if timeout_s == 0:
            raise ValueError("raw EC timeout_s must be positive")
        self.path = path
        self.timeout_s = timeout_s

    def unwritable_reason(self) -> str | None:
        """Nothing to consult: port I/O has no equivalent switch to read, so a
        write is attempted and fails on its own terms if it cannot land."""
        return None

    def _inb(self, f, port: int) -> int:
        return _read_byte(f, port)

    def _outb(self, f, port: int, value: int) -> None:
        _write_byte(f, port, _validate_u8(value, "port byte"))

    @staticmethod
    def _check_deadline(deadline: float) -> None:
        if time.monotonic() >= deadline:
            raise EcError("raw EC transaction timeout")

    def _wait(self, f, flag: int, want_set: bool, deadline: float) -> None:
        while True:
            self._check_deadline(deadline)
            status = self._inb(f, self.CMD)
            self._check_deadline(deadline)
            if bool(status & flag) is want_set:
                return
            time.sleep(min(0.001, max(0, deadline - time.monotonic())))

    def read(self, offset: int) -> int:
        _validate_u8(offset, "offset")
        deadline = time.monotonic() + self.timeout_s
        with _open_io(self.path, os.O_RDWR) as f:
            self._wait(f, self.IBF, False, deadline)
            self._outb(f, self.CMD, self.RD_EC)
            self._wait(f, self.IBF, False, deadline)
            self._outb(f, self.DATA, offset)
            self._wait(f, self.OBF, True, deadline)
            value = self._inb(f, self.DATA)
            self._check_deadline(deadline)
            return value

    def write(self, offset: int, value: int) -> None:
        _validate_u8(offset, "offset")
        _validate_u8(value, "value")
        deadline = time.monotonic() + self.timeout_s
        with _open_io(self.path, os.O_RDWR) as f:
            self._wait(f, self.IBF, False, deadline)
            self._outb(f, self.CMD, self.WR_EC)
            self._wait(f, self.IBF, False, deadline)
            self._outb(f, self.DATA, offset)
            self._wait(f, self.IBF, False, deadline)
            self._outb(f, self.DATA, value)
            self._wait(f, self.IBF, False, deadline)


def default_backend(*, allow_raw: bool = False) -> EcBackend:
    if EcSysBackend.available():
        return EcSysBackend()
    if allow_raw:
        return DevPortBackend()
    raise EcError(
        f"native EC backend unavailable: {EC_SYS_IO} is absent; "
        "raw /dev/port access is disabled by default and requires explicit "
        "allow_raw=True opt-in (it can race kernel/ACPI EC access)"
    )


class EmbeddedController:
    """The single EC gateway. All reads/writes go through one instance."""

    def __init__(self, backend: EcBackend | None = None,
                 lock_path: str = LOCK_PATH, *, allow_raw: bool = False,
                 platform_info: PlatformInfo | None = None,
                 platform_guard: Callable[[PlatformInfo | None], PlatformInfo] | None = None,
                 lock_timeout_s: float = 2.0) -> None:
        # An injected backend is NOT a platform exemption, regardless of name.
        # Tests explicitly inject identity or a guard before any transport use.
        try:
            guard = require_supported_platform if platform_guard is None else platform_guard
            self.platform_info = guard(platform_info)
        except PlatformError as exc:
            raise EcError(f"EC platform guard refused access: {exc}") from exc
        _validate_timeout(lock_timeout_s)
        self.backend = backend if backend is not None else default_backend(allow_raw=allow_raw)
        self.lock_path = lock_path
        self.lock_timeout_s = lock_timeout_s

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Bounded advisory serialization of cooperating userspace callers only."""
        fd = _open_lock_file(self.lock_path)
        try:
            _acquire_flock(fd, self.lock_timeout_s, "EC mailbox")
            yield
        finally:
            os.close(fd)

    def read_u8(self, offset: int) -> int:
        _validate_u8(offset, "offset")
        return _validate_u8(self.backend.read(offset), "backend result")

    def read_u16_be(self, hi_offset: int, lo_offset: int) -> int:
        """Read a big-endian 16-bit value (high byte at ``hi_offset``)."""
        _validate_u8(hi_offset, "high offset")
        _validate_u8(lo_offset, "low offset")
        return (self.read_u8(hi_offset) << 8) | self.read_u8(lo_offset)

    def write_u8(self, offset: int, value: int) -> None:
        _validate_u8(offset, "offset")
        _validate_u8(value, "value")
        self.backend.write(offset, value)

    def unwritable_reason(self) -> str | None:
        """Why writes would be refused right now, or None. Asking costs nothing
        and touches no hardware, so callers may ask as often as they like."""
        return self.backend.unwritable_reason()
