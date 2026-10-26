"""Entry point: `gigactld` / `python3 -m gigactld`."""
from __future__ import annotations

import argparse
import os
import sys
import time

from .ec import EmbeddedController, EcError
from .fan_control import FanControl
from .ownership import OwnerLease
from .platform import PlatformError, require_supported_platform
from .service import Daemon


def restore_firmware(ec) -> bool:
    """Bounded, independent recovery attempts; never claim physical ownership."""
    control = FanControl(ec)
    deadline = time.monotonic() + 5.0
    restored = control.restore_all()
    for _ in range(2):
        if restored or time.monotonic() >= deadline:
            break
        time.sleep(0.1)
        restored = control.retry_restore(deadline=deadline)
    print(f"gigactld: {control.handback_status()}", flush=True)
    return restored


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GigaControl privileged controller")
    parser.add_argument("--interval", type=int, choices=(1, 2, 3), default=2)
    parser.add_argument("--restore-firmware", action="store_true")
    parser.add_argument("--allow-raw-ec", action="store_true",
                        help="EXPERIMENTAL: permit raw ports without kernel arbitration")
    args = parser.parse_args(argv)
    try:
        # Check before backend construction AND in the EC gateway. A GUI model
        # banner is not a privileged hardware guard.
        require_supported_platform()
        with OwnerLease():
            allow_raw = args.allow_raw_ec or os.environ.get("GIGACTL_ALLOW_RAW_EC") == "1"
            ec = EmbeddedController(allow_raw=allow_raw)
            if args.restore_firmware:
                return 0 if restore_firmware(ec) else 1
            daemon = Daemon(ec, interval_s=args.interval)
            try:
                daemon.run()
            finally:
                if daemon._started and daemon._owns_name:
                    restore_firmware(ec)
    except (EcError, PlatformError, OSError) as exc:
        print(f"gigactld: controller unavailable: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
