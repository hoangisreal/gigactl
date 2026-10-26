"""Helper safety contracts: static checks and explicitly injected EC fakes only.

The real probe, daemon, EC devices and /run lease are never executed/opened.
Raw reads are not called "no writes": they issue I/O command/address bytes.
"""
from contextlib import nullcontext
import importlib.util
import pathlib
import re
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

PROBE = pathlib.Path(__file__).with_name("probe.sh").read_text()
TOOLS = pathlib.Path(__file__).parent

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


def load_tool(name):
    spec = importlib.util.spec_from_file_location(f"test_tool_{name}", TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EmulatedEc:
    """Command acknowledgement and telemetry, with no real transport."""

    backend = SimpleNamespace(name="emulated")
    platform_info = SimpleNamespace(vendor="GIGABYTE", model="G5 GE", bios="FB06")

    def __init__(self):
        self.regs = {0x07: 45, 0x0A: 40, 0xCE: 80, 0xCF: 64}
        self.writes = []

    def transaction(self):
        return nullcontext()

    def unwritable_reason(self):
        return None

    def read_u8(self, off):
        return self.regs.get(off, 0)

    def read_u16_be(self, hi, lo):
        return self.read_u8(hi) << 8 | self.read_u8(lo)

    def write_u8(self, off, value):
        self.writes.append((off, value))
        self.regs[off] = value
        if off == 0xF8:
            self.regs[off] = 0


def test_reads_documented_ec_offsets_only():
    ec_read = load_tool("ec_read")
    assert [offset for offset, _ in ec_read.REGISTERS] == [
        0x07, 0x0A, 0x0B, 0xCE, 0xCF, 0xD0, 0xD1, 0xD2, 0xD3,
    ]
    assert "docs/PROTOCOL.md" in PROBE


def test_default_probe_has_no_direct_ec_or_automatic_raw_fallback():
    assert "EC_ARGS=()" in PROBE
    assert '--allow-raw) EC_ARGS=(--allow-raw)' in PROBE
    assert '"$SCRIPT_DIR/ec_read.py" "${EC_ARGS[@]}"' in PROBE
    assert "dd if=" not in PROBE
    assert "/sys/kernel/debug/ec/ec0/io" not in PROBE
    assert "no intentional EC register configuration writes" in PROBE
    assert "no writes were performed" not in PROBE
    assert "os.O_EXCL" in PROBE and "os.O_NOFOLLOW" in PROBE


def test_probe_shell_syntax_only():
    # -n parses, but does not execute any probe commands.
    result = subprocess.run(["bash", "-n", str(TOOLS / "probe.sh")],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr


def test_evidence_capture_is_exclusive_without_running_probe(tmp_path):
    # Exercise only the embedded output-fd launcher with an inert temporary
    # shell script. None of probe.sh's system/EC inspection commands execute.
    launcher = PROBE.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    stub = tmp_path / "inert.sh"
    stub.write_text("printf 'fake evidence\\n'\n")
    output = tmp_path / "evidence.txt"
    command = [sys.executable, "-c", launcher, str(output), str(stub)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert output.read_text() == "fake evidence\n"
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    result = subprocess.run(command, capture_output=True, text=True, timeout=5)
    assert result.returncode == 2
    assert output.read_text() == "fake evidence\n"
    link = tmp_path / "link"
    link.symlink_to(output)
    result = subprocess.run([sys.executable, "-c", launcher, str(link), str(stub)],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 2
    assert output.read_text() == "fake evidence\n"


def test_ec_read_tool_never_writes():
    tool = pathlib.Path(__file__).with_name("ec_read.py").read_text()
    assert ".write(" not in tool and "write_u8(" not in tool
    assert "EmbeddedController" in tool and "OwnerLease" in tool
    assert "DevPortBackend(" not in tool
    for off in ("0x07", "0x0A", "0xCE", "0xCF", "0xD0", "0xD3"):
        assert off in tool


@pytest.mark.parametrize("name,args", [
    ("g2_measure", ["--fan", "1"]),
    ("g2_keyboard", []),
    ("daemon_smoke", []),
])
def test_write_helpers_require_explicit_experiment_before_hardware(monkeypatch, name, args):
    tool = load_tool(name)

    def forbidden(*a, **kw):
        pytest.fail("hardware/ownership construction without experiment opt-in")

    monkeypatch.setattr(tool, "OwnerLease", forbidden, raising=False)
    monkeypatch.setattr(tool, "EmbeddedController", forbidden, raising=False)
    with pytest.raises(SystemExit) as exc:
        tool.main(args)
    assert exc.value.code == 2


@pytest.mark.parametrize("name,runner,args", [
    ("ec_read", "read_samples", []),
    ("g2_measure", "measure", ["--experimental", "--fan", "1"]),
    ("g2_keyboard", "exercise", ["--experimental"]),
])
@pytest.mark.parametrize("raw", [False, True])
def test_helpers_hold_lease_across_controller_and_operation(monkeypatch, name, runner, args, raw):
    tool = load_tool(name)
    events = []
    fake = EmulatedEc()

    class Lease:
        def __enter__(self):
            events.append("acquire")

        def __exit__(self, *exc):
            events.append("release")

    def controller(**kwargs):
        assert events == ["acquire"]
        assert kwargs == {"allow_raw": raw}, "tools must not inject a platform exemption"
        events.append("controller")
        return fake

    def operation(ec, *a):
        assert ec is fake
        assert events == ["acquire", "controller"]
        events.append("operation-and-cleanup")
        return 0

    monkeypatch.setattr(tool, "OwnerLease", Lease)
    monkeypatch.setattr(tool, "EmbeddedController", controller)
    monkeypatch.setattr(tool, runner, operation)
    assert tool.main(args + (["--allow-raw"] if raw else [])) == 0
    assert events == ["acquire", "controller", "operation-and-cleanup", "release"]


@pytest.mark.parametrize("name,args", [
    ("ec_read", []),
    ("g2_measure", ["--experimental", "--fan", "1"]),
    ("g2_keyboard", ["--experimental"]),
])
def test_platform_refusal_is_not_bypassed(monkeypatch, tmp_path, name, args):
    tool = load_tool(name)
    from gigactld.ec import EmbeddedController
    from gigactld.ownership import OwnerLease
    from gigactld.platform import PlatformError

    def denied(info):
        raise PlatformError("injected unsupported machine")

    monkeypatch.setattr(tool, "OwnerLease", lambda: OwnerLease(str(tmp_path / "owner.lock")))
    monkeypatch.setattr(tool, "EmbeddedController", EmbeddedController)
    monkeypatch.setattr("gigactld.ec.require_supported_platform", denied)
    assert tool.main(args) == 1
    with OwnerLease(str(tmp_path / "owner.lock"), timeout_s=0):
        pass  # also verifies the failure released ownership


@pytest.mark.parametrize("raw_duties", [(205, 64), (80, 255)])
def test_g2_rejects_baseline_above_eighty_percent_without_writes(raw_duties):
    tool = load_tool("g2_measure")
    from gigactld.ec import EcError

    fake = EmulatedEc()
    fake.regs[0xCE], fake.regs[0xCF] = raw_duties
    with pytest.raises(EcError, match="baseline duty above 80%"):
        tool.measure(fake, 1)
    assert fake.writes == []


@pytest.mark.parametrize("temperature", [0, -1, 75, 126, 255, None, True])
@pytest.mark.parametrize("offset", [0x07, 0x0A])
def test_g2_rejects_invalid_or_hot_baseline_without_writes(temperature, offset):
    tool = load_tool("g2_measure")
    from gigactld.ec import EcError

    fake = EmulatedEc()
    fake.regs[offset] = temperature
    with pytest.raises(EcError, match="temperature|thermal abort"):
        tool.measure(fake, 1)
    assert fake.writes == []


def test_g2_thermal_abort_attempts_both_fan_handbacks(monkeypatch, capsys):
    tool = load_tool("g2_measure")
    fake = EmulatedEc()
    samples = iter([SimpleNamespace(cpu_temp=45, gpu_temp=40),
                    SimpleNamespace(cpu_temp=80, gpu_temp=40)])
    monkeypatch.setattr(tool, "sample", lambda ec, label: next(samples))
    monkeypatch.setattr(tool.time, "sleep", lambda seconds: None)
    assert tool.measure(fake, 1) == 1
    assert fake.writes == [
        (0xF9, 1), (0xFA, 204), (0xF8, 0xC1),
        (0xF9, 255), (0xFA, 1), (0xF8, 0xC1),
        (0xF9, 255), (0xFA, 2), (0xF8, 0xC1),
    ]
    assert "thermal abort" in capsys.readouterr().err


def test_g2_cleanup_failure_does_not_skip_other_fan_or_hide_original_error(monkeypatch, capsys):
    tool = load_tool("g2_measure")
    from gigactld.ec import EcError

    attempted = []

    def fail_write(*a):
        raise EcError("initial command failed")

    def restore(ec, fan):
        attempted.append(fan)
        if fan == 1:
            raise EcError("fan one cleanup failed")

    monkeypatch.setattr(tool.fans, "set_duty", fail_write)
    monkeypatch.setattr(tool.fans, "restore_auto", restore)
    assert tool.measure(EmulatedEc(), 1) == 1
    assert attempted == [1, 2]
    errors = capsys.readouterr().err
    assert "initial command failed" in errors and "fan one cleanup failed" in errors


def test_keyboard_cleanup_steps_are_independent_and_do_not_claim_previous_state(monkeypatch, capsys):
    tool = load_tool("g2_keyboard")
    from gigactld.ec import EcError

    brightness = []

    def fail_enable(*a):
        raise EcError("enable failed")

    def fail_color(*a):
        raise EcError("colour cleanup failed")

    monkeypatch.setattr(tool.keyboard, "set_enabled", fail_enable)
    monkeypatch.setattr(tool.keyboard, "set_color", fail_color)
    monkeypatch.setattr(tool.keyboard, "set_brightness", lambda ec, level: brightness.append(level))
    assert tool.exercise(EmulatedEc()) == 1
    assert brightness == [100]
    output = capsys.readouterr()
    assert "enable failed" in output.err and "colour cleanup failed" in output.err
    assert "previous state unknown" in output.out


def test_read_samples_performs_no_register_configuration_writes():
    tool = load_tool("ec_read")
    fake = EmulatedEc()
    tool.read_samples(fake)
    assert fake.writes == []


@pytest.mark.parametrize("raw", [False, True])
def test_daemon_smoke_routes_only_to_supervised_entrypoint(monkeypatch, raw):
    tool = load_tool("daemon_smoke")
    forwarded = []
    # No real service import or launch; this stub is the only callable entrypoint.
    monkeypatch.setitem(sys.modules, "gigactld.__main__", SimpleNamespace(
        main=lambda argv: forwarded.append(argv) or 17))
    assert tool.main(["--experimental"] + (["--allow-raw"] if raw else [])) == 17
    assert forwarded == [["--interval", "2"] + (["--allow-raw-ec"] if raw else [])]
