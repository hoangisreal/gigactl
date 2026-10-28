"""Where the documentation makes a claim the code also makes.

Most of what README.md and docs/PROTOCOL.md say is prose, reviewed by reading.
A few statements are joins, though: the same fact written in two places, free to
drift apart silently. Those are worth holding together here.

Run: ``python3 -m pytest packaging -q``
"""
from __future__ import annotations

import re

from conftest import TEST_WORKFLOW, apt_installed, text

MODEL_SOURCE = "gui/gigactl_gui/model.py"


def _verified_model() -> str:
    """The one machine the code treats as proven, read from the code."""
    match = re.search(r'VERIFIED_MODEL = "([^"]+)"', text(MODEL_SOURCE))
    assert match, f"{MODEL_SOURCE} should declare VERIFIED_MODEL"
    return match.group(1)


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
    verified = _verified_model()
    claimed = [row for row in _support_rows() if "Verified" in row]
    assert len(claimed) == 1, f"exactly one row may claim verification: {claimed}"
    assert verified in claimed[0], \
        f"README verifies {claimed[0]!r}, the code verifies {verified!r}"


def _contributing_apt_packages() -> set[str]:
    """The packages CONTRIBUTING.md tells a newcomer to apt install."""
    joined = re.sub(r"\\\n", " ", text("CONTRIBUTING.md"))
    lines = [line for line in joined.splitlines()
             if re.search(r"\bapt(-get)? install\b", line)]
    assert len(lines) == 1, f"expected one apt install line, got {lines}"
    args = re.split(r"\bapt(?:-get)? install\b", lines[0], maxsplit=1)[1].split()
    return {arg for arg in args if not arg.startswith("-")}


def test_contributing_installs_everything_ci_installs():
    """CI's package list is the one proven to run the tests. If CONTRIBUTING.md
    falls behind it, a newcomer's first run fails in a way CI never shows."""
    missing = apt_installed(TEST_WORKFLOW) - _contributing_apt_packages()
    assert not missing, f"CONTRIBUTING.md's install line lacks {sorted(missing)}"
