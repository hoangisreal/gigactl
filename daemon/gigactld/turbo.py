"""Intel Turbo Boost switch, via intel_pstate's ``no_turbo`` sysfs knob.

This machine's owner already disables turbo with an early boot service
writing ``1`` to ``no_turbo`` plus tuned profiles carrying ``boost=0``;
this module gives the daemon the same knob so the GUI can own it at
runtime. Note the boundary: tuned/PowerDevil profile switches can rewrite
``boost`` behind us, so the daemon reports the *live* sysfs value rather
than what it last wrote, and re-asserts only when the user toggles.
"""
from __future__ import annotations

NO_TURBO_PATH = "/sys/devices/system/cpu/intel_pstate/no_turbo"


class TurboUnavailable(Exception):
    """The knob does not exist (not intel_pstate, or HWP hides it)."""


class TurboControl:
    def __init__(self, path: str = NO_TURBO_PATH) -> None:
        self.path = path

    def available(self) -> bool:
        try:
            with open(self.path) as f:
                return f.read().strip() in ("0", "1")
        except OSError:
            return False

    def enabled(self) -> bool:
        """Live state: True when turbo is allowed (no_turbo == 0)."""
        try:
            with open(self.path) as f:
                return f.read().strip() == "0"
        except OSError as exc:
            raise TurboUnavailable(str(exc)) from exc

    def set_enabled(self, on: bool) -> None:
        try:
            with open(self.path, "w") as f:
                f.write("0" if on else "1")
        except OSError as exc:
            raise TurboUnavailable(str(exc)) from exc
