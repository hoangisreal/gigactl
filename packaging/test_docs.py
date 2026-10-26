"""Where the documentation makes a claim the code also makes.

Most of what README.md and docs/PROTOCOL.md say is prose, reviewed by reading.
A few statements are joins, though: the same fact written in two places, free to
drift apart silently. Those are worth holding together here.

Run: ``python3 -m pytest packaging -q``
"""
from __future__ import annotations

import re

from conftest import text

MODEL_SOURCE = "gui/gigactl_gui/model.py"


def _verified_models() -> set[str]:
    """The machines the code treats as proven, read from the code."""
    match = re.search(r'VERIFIED_MODELS = \(([^)]+)\)', text(MODEL_SOURCE))
    assert match, f"{MODEL_SOURCE} should declare VERIFIED_MODELS"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def _support_rows() -> list[str]:
    """The rows of README's hardware support table."""
    table = text("README.md").split("## Supported hardware", 1)[1].split("\n## ", 1)[0]
    return [line for line in table.splitlines()
            if line.startswith("|") and "---" not in line][1:]


def test_the_readme_verifies_exactly_the_machine_the_code_verifies():
    """PRODUCT.md #3: never claim "verified" for a machine we have not run on.
    Two users have now reported on hardware nobody here owns (#24, #25), so the
    difference between "we proved this" and "someone told us" is the whole
    honesty of the table — and it is one edit away from being lost.
    """
    verified = _verified_models()
    claimed = [row for row in _support_rows() if "Verified" in row]
    # The README may claim exactly the machines the code verifies — no
    # more (PRODUCT.md #3), and no fewer (a proven machine left at
    # "expected" undersells what was measured).
    assert len(claimed) == len(verified), \
        f"README claims {len(claimed)} verified rows, code verifies {verified}"
    for model in verified:
        assert any(model in row for row in claimed), \
            f"README does not verify {model!r}, the code does"
