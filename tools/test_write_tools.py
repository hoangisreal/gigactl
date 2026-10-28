"""The tools that write the EC ask the guard first, and touch nothing when it
refuses.

Each test swaps in a fake EC as well as a refusing guard, so even a tool that
ignored the guard could not reach real hardware from here.
"""
from __future__ import annotations

import importlib
import pathlib
import re
import sys

import pytest

import guard

TOOLS = pathlib.Path(__file__).resolve().parent

# Every tool that writes the EC, with the arguments it needs to get going.
WRITERS = {
    "fan_check": ["--fan", "1"],
    "keyboard_check": [],
    "daemon_smoke": [],
}


@pytest.mark.parametrize("name", sorted(WRITERS))
def test_a_tool_touches_nothing_when_the_guard_refuses(name, monkeypatch,
                                                         fake_ec, capsys):
    if name == "daemon_smoke":
        pytest.importorskip("gi")  # it runs the daemon's own Daemon class
    tool = importlib.import_module(name)
    monkeypatch.setattr(tool, "EmbeddedController", lambda *a, **k: fake_ec)
    monkeypatch.setattr(guard, "refusal",
                        lambda **_: "the gigactl daemon is running")
    monkeypatch.setattr(sys, "argv", [f"{name}.py", *WRITERS[name]])

    assert tool.main() == 1
    assert fake_ec.reads == [] and fake_ec.writes == []
    assert "the gigactl daemon is running" in capsys.readouterr().err


def test_every_tool_that_writes_the_ec_is_held_to_the_guard():
    """A new tool that writes the EC has to join WRITERS above, and so be
    tested to ask first. ec_read.py only reads, and must not need to."""
    writes = re.compile(r"\b(set_duty|restore_auto|set_color|set_brightness|"
                        r"set_enabled|apply|write_u8|Daemon)\(")
    found = {script.stem for script in TOOLS.glob("*.py")
             if not script.name.startswith("test_")
             and script.name not in ("conftest.py", "guard.py")
             and writes.search(script.read_text())}
    assert found == set(WRITERS)
