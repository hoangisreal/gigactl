"""What the top banner says — one honest sentence about whether this machine
can be controlled.

Two things spoil that independently. The hardware may not be one we support, in
which case writes are refused on purpose (PRODUCT.md #3, DESIGN.md). Or the
Embedded Controller may be refusing every write even though the machine is
fine — the failure reported in #25, where the app rendered a fully functional
Overview whose every control was a dead end, and the user found out one failed
click at a time.

Both can be true at once, so the order matters and is decided here rather than
in the window: this is the part worth testing, and it needs no GTK to test.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

from .model import Model, Support


class Tone(enum.Enum):
    """How the banner reads.

    Distinct members even where two of them are styled alike: an EC that will
    not accept writes and a machine we refuse to write to look equally alarming
    and say completely different things, and an enum that collapsed them would
    make "which of these is it" unanswerable. (Giving both the same *value*
    would do exactly that — Python would make one an alias of the other.)
    """
    VERIFIED = "verified"
    EXPECTED = "expected"
    PROBLEM = "problem"
    UNSUPPORTED = "unsupported"

    @property
    def css_class(self) -> str:
        """The stylesheet class in ``app.py``. A blocked EC borrows the error
        styling: nothing on the screen will do anything until it is fixed."""
        return "unsupported" if self is Tone.PROBLEM else self.value


_ICONS = {
    Tone.VERIFIED: "emblem-ok-symbolic",
    Tone.EXPECTED: "dialog-warning-symbolic",
    Tone.PROBLEM: "dialog-error-symbolic",
    Tone.UNSUPPORTED: "dialog-error-symbolic",
}


@dataclass(frozen=True)
class Banner:
    tone: Tone
    icon: str
    text: str


def banner(model: Model, ec_status: str = "") -> Banner:
    """The banner for this machine and this EC state.

    An unsupported machine outranks everything: refusing to write to unknown
    hardware is a safety decision, and explaining how to make writes land would
    invite precisely what the guard exists to prevent.

    Otherwise an EC that will not accept writes is the thing to say, because it
    is the thing to act on now — it outranks "expected to work", which is
    background by comparison. The daemon's own sentence is passed through
    unchanged: it was written for a beginner and it names the fix, so rewording
    it here could only lose the instruction.
    """
    if model.support is Support.UNSUPPORTED:
        return Banner(Tone.UNSUPPORTED, _ICONS[Tone.UNSUPPORTED],
                      f"{model.banner} — controls are switched off to keep it safe.")
    if ec_status:
        return Banner(Tone.PROBLEM, _ICONS[Tone.PROBLEM],
                      f"Nothing is reaching the hardware: {ec_status}")
    tone = Tone.VERIFIED if model.support is Support.VERIFIED else Tone.EXPECTED
    return Banner(tone, _ICONS[tone], model.banner)
