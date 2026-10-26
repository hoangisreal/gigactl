#!/usr/bin/env python3
"""All-core CPU burner for the sustained benchmark (stdlib only).

Usage: python3 tools/cpu_burn.py [seconds]
Spawns one spinning process per logical CPU and stops them all at the
deadline (or on SIGINT/SIGTERM). This is a soak load, not a benchmark
score — bench_sustained.sh samples temperature/frequency under it.
"""
import multiprocessing
import sys
import time


def spin(deadline):
    while time.time() < deadline:
        pass


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 120
    deadline = time.time() + seconds
    procs = [multiprocessing.Process(target=spin, args=(deadline,))
             for _ in range(multiprocessing.cpu_count())]
    for p in procs:
        p.start()
    try:
        for p in procs:
            p.join()
    finally:
        for p in procs:
            if p.is_alive():
                p.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
