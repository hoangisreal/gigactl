#!/usr/bin/env python3
"""Experimental fan measurement on an allowlisted platform only.

    sudo python3 tools/g2_measure.py --experimental --fan 1 [--allow-raw]

The chosen fan is requested at 80% for approximately five seconds. A baseline
above 80% on EITHER fan, missing/invalid temperatures, or a hot baseline aborts
before writing. Sampling aborts on invalid or high temperatures. Cleanup of
both fans is best effort, independent per fan, and cannot survive SIGKILL,
power loss, or an inaccessible EC. Acknowledgement is not proof of cooling.

This does not validate a new model or make raw I/O safe. Keep stdout/stderr as
evidence and record physical fan placement separately; no workload is started.
"""
import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "daemon"))

from gigactld import fans, telemetry  # noqa: E402
from gigactld.ec import EcError, EmbeddedController  # noqa: E402
from gigactld.mailbox import pct_to_byte  # noqa: E402
from gigactld.ownership import OwnerLease  # noqa: E402

TEST_DUTY_PCT = 80
MAX_START_TEMP_C = 75
THERMAL_ABORT_TEMP_C = 80
SAMPLES = 5


def sample(ec, label):
    t = telemetry.read_telemetry(ec)
    print(f"{label}: cpu={t.cpu_temp}C gpu={t.gpu_temp}C "
          f"dut1={t.fan1_duty_pct}% dut2={t.fan2_duty_pct}% "
          f"rpm1={t.fan1_rpm} rpm2={t.fan2_rpm}", flush=True)
    return t


def check_temperatures(t, limit: int) -> None:
    for label, value in (("CPU", t.cpu_temp), ("GPU", t.gpu_temp)):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 125:
            raise EcError(f"invalid/missing {label} temperature: {value!r}")
        if value >= limit:
            raise EcError(f"thermal abort: {label} {value}C >= {limit}C")


def restore_fans(ec) -> bool:
    ok = True
    for fan in fans.FANS:
        try:
            fans.restore_auto(ec, fan)
            print(f"fan {fan}: firmware-auto request acknowledged (not a physical verification)", flush=True)
        except Exception as exc:
            ok = False
            print(f"fan {fan}: firmware-auto cleanup FAILED: {exc}", file=sys.stderr, flush=True)
    return ok


def measure(ec, fan: int) -> int:
    reason = ec.unwritable_reason()
    if reason:
        raise EcError(reason)
    start = sample(ec, "before")
    check_temperatures(start, MAX_START_TEMP_C)
    with ec.transaction():
        duties = {number: ec.read_u8(fans.DUTY_REG[number]) for number in fans.FANS}
    print(f"baseline raw duties: {duties}", flush=True)
    if any(value > pct_to_byte(TEST_DUTY_PCT) for value in duties.values()):
        raise EcError("refusing: baseline duty above 80%; this request could reduce cooling")
    print(f">>> requesting fan {fan} at {TEST_DUTY_PCT}% — record the physical side <<<", flush=True)
    ok = True
    try:
        # Include partial/failed command attempts in the cleanup scope.
        fans.set_duty(ec, fan, TEST_DUTY_PCT)
        for i in range(SAMPLES):
            time.sleep(1)
            check_temperatures(sample(ec, f"  t+{i + 1}s"), THERMAL_ABORT_TEMP_C)
    except Exception as exc:
        ok = False
        print(f"g2_measure: experiment aborted: {exc}", file=sys.stderr, flush=True)
    finally:
        cleanup_ok = restore_fans(ec)
    if not ok or not cleanup_ok:
        return 1
    time.sleep(2)
    check_temperatures(sample(ec, "after"), THERMAL_ABORT_TEMP_C)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fan", type=int, choices=(1, 2), required=True)
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
            return measure(ec, args.fan)
    except (EcError, OSError) as exc:
        print(f"g2_measure: {exc}", file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        print("g2_measure: interrupted; any cleanup was best effort", file=sys.stderr, flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
