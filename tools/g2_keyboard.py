#!/usr/bin/env python3
"""Experimental keyboard command check on an allowlisted platform.

    sudo python3 tools/g2_keyboard.py --experimental [--allow-raw]

Sets the single-zone backlight to orange at full brightness for ~6s so you
can see it, then requests factory blue (0,0,200) as best-effort cleanup.
That is NOT restoration of the previous state; live keyboard readback is not
documented. Cleanup can fail, and cannot run after SIGKILL or power loss.
Keep stdout/stderr as experiment evidence. No hardware safety is implied.
"""
import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "daemon"))

from gigactld import keyboard  # noqa: E402
from gigactld.ec import EcError, EmbeddedController  # noqa: E402
from gigactld.ownership import OwnerLease  # noqa: E402

FACTORY_BLUE = (0, 0, 200)
TEST_COLOR = (255, 102, 0)  # orange — unmistakably not the factory blue


def exercise(ec) -> int:
    reason = ec.unwritable_reason()
    if reason:
        raise EcError(reason)
    ok = True
    print(">>> requesting ORANGE @100% — record visual behaviour <<<", flush=True)
    try:
        keyboard.set_enabled(ec, True)
        keyboard.set_color(ec, *TEST_COLOR)
        keyboard.set_brightness(ec, 100)
        time.sleep(6)
    except Exception as exc:
        ok = False
        print(f"g2_keyboard: experiment aborted: {exc}", file=sys.stderr, flush=True)
    finally:
        # Cleanup steps are independent even if the first command fails.
        for label, action in (
            ("factory blue", lambda: keyboard.set_color(ec, *FACTORY_BLUE)),
            ("brightness 100%", lambda: keyboard.set_brightness(ec, 100)),
        ):
            try:
                action()
                print(f"cleanup {label}: command acknowledged; previous state unknown", flush=True)
            except Exception as exc:
                ok = False
                print(f"cleanup {label} FAILED: {exc}", file=sys.stderr, flush=True)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experimental", action="store_true", help="explicitly authorize this live EC write experiment")
    parser.add_argument("--allow-raw", action="store_true", help="also opt in to raw I/O if native ec_sys is absent")
    args = parser.parse_args(argv)
    if not args.experimental:
        parser.error("live EC writes require --experimental; no automatic experiment is run")
    try:
        with OwnerLease():
            ec = EmbeddedController(allow_raw=args.allow_raw)
            print(f"EXPERIMENTAL: {ec.platform_info!r}; backend={ec.backend.name}; "
                  "userspace ownership does not exclude kernel EC access", flush=True)
            return exercise(ec)
    except (EcError, OSError) as exc:
        print(f"g2_keyboard: {exc}", file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        print("g2_keyboard: interrupted; any cleanup was best effort", file=sys.stderr, flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
