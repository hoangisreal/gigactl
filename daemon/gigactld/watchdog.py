"""Minimal systemd watchdog transport, driven by the daemon's GLib loop.

This module deliberately has no timer or background thread. Only the control
loop may call ``ping``: independently generated heartbeats would conceal a hung
loop. Type=dbus readiness remains the acquisition of the service's bus name.
"""
from __future__ import annotations

import os
import re
import socket
from dataclasses import dataclass


def _positive_decimal(value: str, maximum: int) -> int:
    if not re.fullmatch(r"[0-9]+", value):
        raise ValueError("expected a positive decimal integer")
    number = int(value)
    if not 0 < number <= maximum:
        raise ValueError("watchdog value outside range")
    return number


@dataclass(frozen=True)
class Watchdog:
    """A disabled watchdog has interval_s=0 and ping() is a harmless no-op."""

    interval_s: float = 0.0
    _address: str | None = None

    @classmethod
    def from_environment(cls) -> Watchdog:
        """Read sd_watchdog_enabled's environment contract, failing closed.

        WATCHDOG_PID, when present, must identify this process. Invalid or
        incomplete configuration disables notifications, including those from a
        short-lived recovery process inheriting the main daemon's environment.
        """
        try:
            usec = _positive_decimal(os.environ.get("WATCHDOG_USEC", ""), 2**64 - 1)
            if "WATCHDOG_PID" in os.environ:
                pid = _positive_decimal(os.environ["WATCHDOG_PID"], 2**31 - 1)
                if pid != os.getpid():
                    return cls()
            address = os.environ.get("NOTIFY_SOCKET", "")
            if (len(address) < 2 or address[0] not in ("/", "@")
                    or "\0" in address):
                return cls()
            if address.startswith("@"):
                address = "\0" + address[1:]
            # Ping halfway through the allowed period, not at its deadline.
            return cls(interval_s=usec / 2_000_000, _address=address)
        except (ValueError, OverflowError):
            return cls()

    def ping(self) -> bool:
        """Send WATCHDOG=1 without blocking; False on disabled/failed delivery."""
        if self.interval_s <= 0 or self._address is None:
            return False
        message = b"WATCHDOG=1"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                sock.setblocking(False)
                return sock.sendto(message, self._address) == len(message)
        except (OSError, ValueError):
            return False
