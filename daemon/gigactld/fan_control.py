"""Fan I/O reconciliation. A requested duty is not an observed duty.

Commands are serialized by the EC gateway. Two physical fan writes cannot be
atomic: the caller must enter recovery on *any* apply/check exception. Firmware
handback is attempted independently per fan and failed requests remain pending.
"""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable

from . import fans, mailbox
from .ec import EcError


@dataclass
class _Target:
    percent: int
    raw: int
    started: float
    progress_at: float
    observed: int
    written_at: float
    verified: bool = False


class FanControl:
    # Downward EC ramps can take ~25 seconds. Lack of progress is a separate,
    # shorter deadline; retries never extend either deadline.
    CONVERGENCE_S = 30.0
    NO_PROGRESS_S = 6.0
    RETRY_S = 2.0

    def __init__(self, ec, clock: Callable[[], float] = time.monotonic):
        self.ec = ec
        self.clock = clock
        self.targets: dict[int, _Target] = {}
        self.pending_auto: set[int] = set()
        self.restore_errors: dict[int, str] = {}

    @property
    def pending_duties(self) -> tuple[int, ...]:
        return tuple(f for f, target in self.targets.items() if not target.verified)

    def apply(self, duties: dict[int, int]) -> None:
        """Issue changed targets and observe them without blocking the event loop.

        Success means the command was accepted by the transport. Until readback
        converges, pending_duties explicitly distinguishes that from application.
        """
        if self.pending_auto:
            raise EcError("firmware handback is unresolved; cannot take control")
        if any(f not in fans.FANS or type(p) is not int or not 0 <= p <= 100
               for f, p in duties.items()):
            raise ValueError("invalid fan target")
        now = self.clock()
        for fan, percent in duties.items():
            previous = self.targets.get(fan)
            if previous is not None and previous.percent == percent:
                continue
            observed = fans.read_duty(self.ec, fan)
            # Remember intent before the command: a partial write must not be
            # mistaken for an untouched fan if the transport then raises.
            self.targets[fan] = _Target(percent, mailbox.pct_to_byte(percent),
                                        now, now, observed, now)
            fans.set_duty(self.ec, fan, percent)
        self.check()

    def check(self) -> None:
        """Detect silent rejection, lost manual control, and stalled ramps."""
        now = self.clock()
        for fan, target in self.targets.items():
            observed = fans.read_duty(self.ec, fan)
            if not 0 <= observed <= 255:
                raise EcError(f"invalid duty readback for fan {fan}")
            if fans.duty_matches(target.raw, observed):
                target.verified = True
                target.observed = observed
                target.progress_at = now
                continue
            if target.verified:
                # Firmware reset or another controller changed the duty. Start
                # a new bounded verification, never silently trust our cache.
                target.verified = False
                target.started = target.progress_at = now
                target.observed = observed
                fans.set_duty(self.ec, fan, target.percent)
                target.written_at = now
            elif abs(observed - target.raw) < abs(target.observed - target.raw):
                target.progress_at = now
            target.observed = observed
            if (now - target.started >= self.CONVERGENCE_S or
                    now - target.progress_at >= self.NO_PROGRESS_S):
                raise EcError(f"fan {fan} duty {target.percent}% was not verified")
            if now - target.written_at >= self.RETRY_S:
                fans.set_duty(self.ec, fan, target.percent)
                target.written_at = now

    def restore_all(self) -> bool:
        """Request auto for both fans; do not infer ownership from duty values."""
        self.targets.clear()
        self.pending_auto.update(fans.FANS)
        return self.retry_restore()

    def release(self, fan: int) -> None:
        """Return a temporary emergency-only override to firmware."""
        fans.restore_auto(self.ec, fan)
        self.targets.pop(fan, None)

    def retry_restore(self, *, deadline: float | None = None) -> bool:
        for fan in sorted(self.pending_auto):
            if deadline is not None and self.clock() >= deadline:
                break
            try:
                fans.restore_auto(self.ec, fan)
            except Exception as exc:
                self.restore_errors[fan] = str(exc)
            else:
                self.pending_auto.discard(fan)
                self.restore_errors.pop(fan, None)
        return not self.pending_auto

    def handback_status(self) -> str:
        if self.pending_auto:
            names = ", ".join(str(f) for f in sorted(self.pending_auto))
            return f"firmware-auto handback unconfirmed for fan(s) {names}"
        return "firmware-auto commands acknowledged (physical ownership not observable)"
