"""Embedded Controller access layer.

Two backends, tried in order of preference:

* ``ec_sys``   — read/write bytes through ``/sys/kernel/debug/ec/ec0/io`` (the
  kernel serialises each access). Preferred; requires the ``ec_sys`` module
  (loaded with ``write_support=1`` for writes).
* ``/dev/port`` — the raw ACPI EC handshake on ports 0x62/0x66, used when
  ``ec_sys`` is unavailable.

Loaded without ``write_support=1``, ``ec_sys`` serves every read and refuses
every write — the kernel answers ``EINVAL`` before looking at anything else.
That is a *read-only EC*, not an absent one: the machine in #25 reported correct
temperatures and RPM throughout while no command it was given had any effect. So
this layer keeps serving reads, refuses writes up front with
:class:`EcNotWritable`, and offers :meth:`unwritable_reason` for callers that
want to say so without provoking a failure. It never silently switches to
``/dev/port`` to get around it: that backend races the kernel's own EC driver
(see ``daemon/data/gigactl.service``), and quietly moving a misconfigured
machine onto it would trade a clear problem for an obscure one.

Every logical operation runs inside :meth:`EmbeddedController.transaction`,
which holds an flock so a multi-write doorbell sequence stays atomic. That lock
is the serialization point every EC writer is meant to take; once the CLI is
reworked to share it (ticket #17), the daemon and a direct-write fallback can no
longer interleave. Today only the daemon takes it.
"""
from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from typing import Iterator, Protocol

EC_SYS_IO = "/sys/kernel/debug/ec/ec0/io"
# The kernel publishes whether ec_sys will accept writes, so the daemon can ask
# instead of discovering it by having a write refused (#25).
EC_SYS_WRITE_SUPPORT = "/sys/module/ec_sys/parameters/write_support"
LOCK_PATH = "/run/lock/gigactl-ec.lock"

READ_ONLY_REASON = (
    "the kernel's EC interface is read-only: the ec_sys module was loaded "
    "without write support. Reload it with "
    "'sudo modprobe -r ec_sys && sudo modprobe ec_sys', or reboot."
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
        module fixes the machine without restarting the daemon. An unreadable
        or absent parameter means this is not a gate we recognise, and claiming
        the EC is broken would refuse writes that would have worked.
        """
        try:
            with open(self.write_support_path) as f:
                setting = f.read().strip()
        except OSError:
            return None
        return None if setting.upper() in ("Y", "1") else READ_ONLY_REASON

    def read(self, offset: int) -> int:
        with open(self.path, "rb", buffering=0) as f:
            f.seek(offset)
            b = f.read(1)
        if len(b) != 1:
            raise EcError(f"short read at {offset:#04x}")
        return b[0]

    def write(self, offset: int, value: int) -> None:
        reason = self.unwritable_reason()
        if reason:
            # Refuse here rather than let the kernel answer EINVAL: the errno
            # alone told the user in #25 nothing they could act on.
            raise EcNotWritable(reason)
        with open(self.path, "r+b", buffering=0) as f:
            f.seek(offset)
            f.write(bytes([value & 0xFF]))


class DevPortBackend:
    """Raw ACPI EC access on I/O ports via ``/dev/port`` (fallback).

    Not unit-tested — it needs real port I/O. The protocol is the standard
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
    _SPINS = 100_000

    def __init__(self, path: str = "/dev/port") -> None:
        self.path = path

    def unwritable_reason(self) -> str | None:
        """Nothing to consult: port I/O has no equivalent switch to read, so a
        write is attempted and fails on its own terms if it cannot land."""
        return None

    def _inb(self, f, port: int) -> int:
        f.seek(port)
        return f.read(1)[0]

    def _outb(self, f, port: int, value: int) -> None:
        f.seek(port)
        f.write(bytes([value & 0xFF]))
        f.flush()

    def _wait(self, f, flag: int, want_set: bool) -> None:
        for _ in range(self._SPINS):
            status = self._inb(f, self.CMD)
            if bool(status & flag) is want_set:
                return
        raise EcError("EC status timeout")

    def read(self, offset: int) -> int:
        with open(self.path, "r+b", buffering=0) as f:
            self._wait(f, self.IBF, False)
            self._outb(f, self.CMD, self.RD_EC)
            self._wait(f, self.IBF, False)
            self._outb(f, self.DATA, offset)
            self._wait(f, self.OBF, True)
            return self._inb(f, self.DATA)

    def write(self, offset: int, value: int) -> None:
        with open(self.path, "r+b", buffering=0) as f:
            self._wait(f, self.IBF, False)
            self._outb(f, self.CMD, self.WR_EC)
            self._wait(f, self.IBF, False)
            self._outb(f, self.DATA, offset)
            self._wait(f, self.IBF, False)
            self._outb(f, self.DATA, value)


def default_backend() -> EcBackend:
    if EcSysBackend.available():
        return EcSysBackend()
    return DevPortBackend()


class EmbeddedController:
    """The single EC gateway. All reads/writes go through one instance."""

    def __init__(self, backend: EcBackend | None = None,
                 lock_path: str = LOCK_PATH) -> None:
        self.backend = backend or default_backend()
        self.lock_path = lock_path

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Hold an exclusive advisory lock for the duration of a transaction."""
        parent = os.path.dirname(self.lock_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def read_u8(self, offset: int) -> int:
        return self.backend.read(offset)

    def read_u16_be(self, hi_offset: int, lo_offset: int) -> int:
        """Read a big-endian 16-bit value (high byte at ``hi_offset``)."""
        return (self.backend.read(hi_offset) << 8) | self.backend.read(lo_offset)

    def write_u8(self, offset: int, value: int) -> None:
        self.backend.write(offset, value)

    def unwritable_reason(self) -> str | None:
        """Why writes would be refused right now, or None. Asking costs nothing
        and touches no hardware, so callers may ask as often as they like."""
        return self.backend.unwritable_reason()
