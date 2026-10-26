"""The Clevo EC command mailbox — shared by every control module.

A command is written parameter-first, command byte (the *doorbell*) last, all
inside one advisory userspace EC transaction. FCMD must be idle before any
parameters are written, and its documented clear acknowledgement is awaited.
Only the parameters a command needs are written; no extra-byte protocol is
inferred from unverified reports. The lock cannot exclude kernel/ACPI users.
See ``docs/PROTOCOL.md``.
"""
from __future__ import annotations

import time

from .ec import EcError, _validate_timeout, _validate_u8

# mailbox registers
FCMD = 0xF8   # command / doorbell — written LAST, triggers execution
FDAT = 0xF9   # sub-command
FBUF = 0xFA   # parameter 1
FBF1 = 0xFB   # parameter 2
FBF2 = 0xFC   # parameter 3
FBF3 = 0xFD   # parameter 4
_PARAM_REGS = (FBUF, FBF1, FBF2, FBF3)


def _wait_clear(ec, timeout_s: float, phase: str) -> None:
    deadline = time.monotonic() + timeout_s
    while True:
        value = ec.read_u8(FCMD)
        if time.monotonic() >= deadline:
            raise EcError(f"EC mailbox timeout waiting for FCMD {phase}")
        if value == 0:
            return
        time.sleep(min(0.005, max(0, deadline - time.monotonic())))


def command(ec, sub: int, params=(), *, doorbell: int, timeout_s: float = 0.5) -> None:
    """Issue one mailbox command atomically: FDAT=sub, params into FBUF.., then
    the doorbell into FCMD, then await its clear acknowledgement.

    A timeout after the doorbell means completion is unknown, not that no
    command ran. Callers must not retry blindly or overwrite a busy mailbox.
    """
    params = tuple(params)
    if len(params) > len(_PARAM_REGS):
        raise ValueError(f"too many mailbox params: {len(params)}")
    _validate_u8(sub, "sub-command")
    _validate_u8(doorbell, "doorbell")
    if doorbell == 0:
        raise ValueError("doorbell must be a nonzero command byte")
    for value in params:
        _validate_u8(value, "parameter")
    _validate_timeout(timeout_s)
    if timeout_s == 0:
        raise ValueError("mailbox timeout_s must be positive")
    with ec.transaction():
        _wait_clear(ec, timeout_s, "idle before parameters")
        ec.write_u8(FDAT, sub)
        for reg, val in zip(_PARAM_REGS, params):
            ec.write_u8(reg, val)
        ec.write_u8(FCMD, doorbell)
        _wait_clear(ec, timeout_s, "clear acknowledgement after doorbell")


def pct_to_byte(pct: int) -> int:
    """Scale a 0-100 percentage to a 0-255 EC byte.

    Out-of-range input raises instead of being clamped into range: silently
    masking an invalid duty would hide the caller bug that produced it.
    """
    if not isinstance(pct, int) or isinstance(pct, bool) or not 0 <= pct <= 100:
        raise ValueError(f"percentage out of range: {pct!r}")
    return round(pct * 255 / 100)
