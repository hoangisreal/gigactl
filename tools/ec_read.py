#!/usr/bin/env python3
"""Read-only EC register dump for the Phase-0 probe on Fedora.

Fedora's stock kernel sets CONFIG_ACPI_EC=y with CONFIG_ACPI_EC_DEBUGFS
unset, so the ec_sys debugfs file gigactl prefers on Ubuntu never exists
there. The only stock-kernel EC path is /dev/port, driven here through the
daemon's own DevPortBackend — read methods only; this tool never writes.

Usage (root):  sudo python3 tools/ec_read.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "daemon"))

from gigactld.ec import DevPortBackend, EcError  # noqa: E402

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


def main() -> int:
    backend = DevPortBackend()
    for samples in range(3):
        values = {}
        try:
            for offset, label in REGISTERS:
                values[label] = backend.read(offset)
        except (EcError, OSError) as exc:
            print(f"ec_read: read failed on sample {samples}: {exc}")
            return 1
        tach1 = (values["tach1_hi"] << 8) | values["tach1_lo"]
        tach2 = (values["tach2_hi"] << 8) | values["tach2_lo"]
        rpm1 = round(2_156_220 / tach1) if tach1 else 0
        rpm2 = round(2_156_220 / tach2) if tach2 else 0
        print(
            f"sample {samples}: cpu={values['cpu_temp']}C "
            f"gpu={values['gpu_temp_a']}/{values['gpu_temp_b']}C "
            f"dut1={values['dut1']} dut2={values['dut2']} "
            f"rpm1~{rpm1} rpm2~{rpm2}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
