#!/usr/bin/env python3
"""Guarded telemetry dump with no intentional EC register configuration writes.

Native ec_sys access is required by default. Experimental raw reads require
``--allow-raw``: those reads DO send command/address bytes to I/O ports and can
race the kernel EC driver. Both paths require the model guard and owner lease.

Usage (root): sudo python3 tools/ec_read.py [--allow-raw]
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "daemon"))

from gigactld.ec import EmbeddedController, EcError  # noqa: E402
from gigactld.ownership import OwnerLease  # noqa: E402
from gigactld.telemetry import rpm_from_period  # noqa: E402

REGISTERS = [
    (0x07, "cpu_temp"),
    (0x0A, "gpu_temp_a"),
    (0x0B, "gpu_temp_b"),
    (0xCE, "dut1"),
    (0xCF, "dut2"),
    (0xD0, "tach1_hi"),
    (0xD1, "tach1_lo"),
    (0xD2, "tach2_hi"),
    (0xD3, "tach2_lo"),
]


def read_samples(ec) -> None:
    for samples in range(3):
        values = {}
        with ec.transaction():
            for offset, label in REGISTERS:
                values[label] = ec.read_u8(offset)
        tach1 = (values["tach1_hi"] << 8) | values["tach1_lo"]
        tach2 = (values["tach2_hi"] << 8) | values["tach2_lo"]
        rpm1 = rpm_from_period(tach1)
        rpm2 = rpm_from_period(tach2)
        print(
            f"sample {samples}: cpu={values['cpu_temp']}C "
            f"gpu={values['gpu_temp_a']}/{values['gpu_temp_b']}C "
            f"dut1={values['dut1']} dut2={values['dut2']} "
            f"tach1={tach1:#06x} tach2={tach2:#06x} rpm1~{rpm1} rpm2~{rpm2}", flush=True,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-raw", action="store_true",
                        help="experimental raw I/O if ec_sys is absent; may race kernel EC access")
    args = parser.parse_args(argv)
    try:
        with OwnerLease():
            ec = EmbeddedController(allow_raw=args.allow_raw)
            print(f"platform: {ec.platform_info!r}", flush=True)
            print(f"EC backend: {ec.backend.name}; no intentional EC register configuration writes", flush=True)
            if ec.backend.name == "dev_port":
                print("WARNING: raw reads issue I/O command/address writes; the lease does not exclude kernel access",
                      flush=True)
            read_samples(ec)
    except (EcError, OSError) as exc:
        print(f"ec_read: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
