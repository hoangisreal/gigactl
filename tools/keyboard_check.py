#!/usr/bin/env python3
"""Check the keyboard backlight: master enable, one colour, brightness.

    sudo python3 tools/keyboard_check.py

Sets the single-zone backlight to orange at full brightness for ~6s so you
can see it, then returns it to the factory blue (0,0,200). The EC forgets
LED state at reboot regardless; nothing here persists beyond the session
unless the daemon later re-applies it.
"""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "daemon"))

import guard  # noqa: E402
from gigactld import keyboard  # noqa: E402
from gigactld.ec import DevPortBackend, EmbeddedController  # noqa: E402

FACTORY_BLUE = (0, 0, 200)
TEST_COLOR = (255, 102, 0)  # orange — unmistakably not the factory blue


def main() -> int:
    reason = guard.refusal()
    if reason:
        print(f"keyboard_check: {reason}", file=sys.stderr)
        return 1
    ec = EmbeddedController(backend=DevPortBackend())
    print(">>> enabling keyboard control, setting ORANGE @100% — look at the keys <<<", flush=True)
    keyboard.set_enabled(ec, True)
    keyboard.set_color(ec, *TEST_COLOR)
    keyboard.set_brightness(ec, 100)
    time.sleep(6)
    print(">>> back to factory blue <<<", flush=True)
    keyboard.set_color(ec, *FACTORY_BLUE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
