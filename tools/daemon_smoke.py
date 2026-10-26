#!/usr/bin/env python3
"""Experimental foreground launcher using the guarded daemon entrypoint.

    sudo python3 tools/daemon_smoke.py --experimental [--allow-raw]

The former private _tick() smoke path bypassed lifecycle supervision. This
wrapper instead runs the canonical entrypoint: platform guard, lifetime owner
lease, bus-name ownership, watchdog and independent best-effort fan cleanup
remain in one place. Normal startup can restore the daemon's saved settings;
this wrapper does not select a test profile or start a workload. Stop with
SIGINT/SIGTERM; retain stdout/stderr as evidence. This is
not a compatibility test or a guarantee of cleanup after transport failure,
SIGKILL, or power loss. Raw I/O can still race kernel/ACPI EC access.
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "daemon"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experimental", action="store_true", help="explicitly authorize this live EC write experiment")
    parser.add_argument("--allow-raw", action="store_true", help="also opt in to raw I/O if native ec_sys is absent")
    args = parser.parse_args(argv)
    if not args.experimental:
        parser.error("live EC writes require --experimental; no automatic experiment is run")
    # Deferred import keeps help, opt-in refusal and tests free of D-Bus setup.
    from gigactld.__main__ import main as daemon_main

    forwarded = ["--interval", "2"]
    if args.allow_raw:
        forwarded.append("--allow-raw-ec")
    print("EXPERIMENTAL: launching supervised foreground daemon; no automatic test profile or workload", flush=True)
    return daemon_main(forwarded)


if __name__ == "__main__":
    raise SystemExit(main())
