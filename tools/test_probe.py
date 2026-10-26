"""Safety contract for tools/probe.sh: it must stay read-only.

The probe is the Phase-0 tool run on unverified hardware. If a future edit
adds a write (EC doorbell, sysfs store, modprobe, dd of=...), these tests
fail before the script ever touches a machine.
"""
import pathlib
import re

PROBE = pathlib.Path(__file__).with_name("probe.sh").read_text()

FORBIDDEN = [
    r"\bdd\b[^#\n]*\bof=",          # dd writing to a device/file
    r">\s*/sys/",                   # shell redirection into sysfs
    r">\s*/dev/port",               # raw port I/O target
    r"\bmodprobe\b",                # loading modules changes system state
    r"\bec_probe\b[^#\n]*\bwrite\b",
    r"0xC1|0xCA|0xC4",              # EC doorbell commands are writes
]


def test_no_write_operations():
    for pattern in FORBIDDEN:
        assert not re.search(pattern, PROBE), f"probe.sh must stay read-only, found: {pattern}"


def test_reads_documented_ec_offsets_only():
    for off in ("07", "0A", "0B", "CE", "CF", "D0", "D3"):
        assert off in PROBE
    assert "docs/PROTOCOL.md" in PROBE


def test_g2_measure_always_restores_and_refuses_hot():
    tool = pathlib.Path(__file__).with_name("g2_measure.py").read_text()
    assert "finally:" in tool and "restore_auto" in tool, \
        "the controlled write must hand the fan back whatever happens"
    assert "MAX_START_TEMP_C" in tool, "must refuse to run on a hot machine"
    assert "TEST_DUTY_PCT = 80" in tool, "distinctive, cooling-increasing duty"


def test_daemon_smoke_always_restores_firmware():
    tool = pathlib.Path(__file__).with_name("daemon_smoke.py").read_text()
    assert "finally:" in tool and "restore_auto" in tool
    assert 'set_profile("firmware")' in tool


def test_ec_read_tool_never_writes():
    tool = pathlib.Path(__file__).with_name("ec_read.py").read_text()
    assert ".write(" not in tool, "ec_read.py must only call backend.read"
    assert "DevPortBackend" in tool
    for off in ("0x07", "0x0A", "0xCE", "0xCF", "0xD0", "0xD3"):
        assert off in tool
