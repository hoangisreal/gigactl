"""EC access layer — the ec_sys backend and the flock transaction are TDD'd here
against a temp file. The /dev/port backend needs real hardware and is not unit-tested."""
import fcntl

import pytest

from gigactld.ec import (EcNotWritable, EcSysBackend,
                         EmbeddedController)


def _fill(path, mutate=None):
    data = bytearray(range(256))  # register N holds value N by default
    if mutate:
        mutate(data)
    path.write_bytes(bytes(data))
    return path


def test_ecsys_reads_byte_at_offset(tmp_path):
    io = _fill(tmp_path / "io")
    b = EcSysBackend(str(io))
    assert b.read(0x07) == 0x07
    assert b.read(0xCE) == 0xCE


def test_ecsys_writes_only_target_byte(tmp_path):
    io = _fill(tmp_path / "io")
    b = EcSysBackend(str(io))
    b.write(0x10, 0xAB)
    raw = (tmp_path / "io").read_bytes()
    assert raw[0x10] == 0xAB
    assert raw[0x11] == 0x11  # neighbour untouched


def test_ecsys_available_reflects_path(tmp_path):
    io = _fill(tmp_path / "io")
    assert EcSysBackend.available(str(io)) is True
    assert EcSysBackend.available(str(tmp_path / "nope")) is False


def test_controller_read_u8_via_backend(tmp_path):
    io = _fill(tmp_path / "io")
    ec = EmbeddedController(backend=EcSysBackend(str(io)),
                            lock_path=str(tmp_path / "lock"))
    with ec.transaction():
        assert ec.read_u8(0x0A) == 0x0A


def test_controller_read_u16_be_is_high_byte_first(tmp_path):
    def setup(d):
        d[0xD0], d[0xD1] = 0x03, 0x6C  # 0x036C = 876

    io = _fill(tmp_path / "io", setup)
    ec = EmbeddedController(backend=EcSysBackend(str(io)),
                            lock_path=str(tmp_path / "lock"))
    assert ec.read_u16_be(0xD0, 0xD1) == 0x036C


def test_transaction_takes_an_exclusive_lock(tmp_path):
    io = _fill(tmp_path / "io")
    lock = tmp_path / "lock"
    ec = EmbeddedController(backend=EcSysBackend(str(io)), lock_path=str(lock))
    with ec.transaction():
        # while the transaction holds LOCK_EX, a non-blocking grab must fail
        with open(lock) as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                got = True
                fcntl.flock(probe, fcntl.LOCK_UN)
            except BlockingIOError:
                got = False
    assert got is False


# --- a read-only ec_sys (#25) -----------------------------------------------
#
# The module exposes the debugfs file whether or not it was loaded with
# write_support=1; the kernel gates writes alone, returning EINVAL before it
# looks at anything else. That is why the machine in #25 reported perfect
# temperatures and RPM while every command failed with "[Errno 22] Invalid
# argument" — a message that blames the daemon for a kernel refusal.

def _backend(tmp_path, write_support):
    io = _fill(tmp_path / "io")
    param = tmp_path / "write_support"
    param.write_text(write_support)
    return EcSysBackend(str(io), str(param)), io


def test_ecsys_still_reads_when_the_module_is_read_only(tmp_path):
    """Telemetry must survive: reads are never gated, and a daemon that gave up
    here would lose the temperatures it can still report perfectly well."""
    b, _io = _backend(tmp_path, "N\n")
    assert b.read(0x07) == 0x07


def test_ecsys_refuses_to_write_when_the_module_is_read_only(tmp_path):
    b, io = _backend(tmp_path, "N\n")
    with pytest.raises(EcNotWritable) as exc:
        b.write(0x10, 0xAB)
    assert io.read_bytes()[0x10] == 0x10, "must not have written"
    assert "modprobe" in str(exc.value), "the message has to say how to fix it"


def test_ecsys_writes_when_the_module_has_write_support(tmp_path):
    b, io = _backend(tmp_path, "Y\n")
    b.write(0x10, 0xAB)
    assert io.read_bytes()[0x10] == 0xAB


def test_ecsys_reports_why_it_cannot_write_without_attempting_one(tmp_path):
    """The daemon states the problem at startup, so it must be able to ask
    without poking the Embedded Controller to find out."""
    readonly, io = _backend(tmp_path, "N\n")
    assert "modprobe" in (readonly.unwritable_reason() or "")
    assert io.read_bytes() == bytes(range(256)), "asking must not write"
    writable, _ = _backend(tmp_path, "Y\n")
    assert writable.unwritable_reason() is None


def test_ecsys_notices_write_support_arriving_without_a_restart(tmp_path):
    """Reloading the module fixes the machine; the daemon should agree without
    being restarted, so the status can clear on its own."""
    b, _io = _backend(tmp_path, "N\n")
    assert b.unwritable_reason() is not None
    (tmp_path / "write_support").write_text("Y\n")
    assert b.unwritable_reason() is None


def test_ecsys_assumes_writable_when_the_parameter_is_missing(tmp_path):
    """No parameter file means this is not a kernel whose gate we understand;
    guessing 'broken' would refuse writes that would have worked."""
    io = _fill(tmp_path / "io")
    b = EcSysBackend(str(io), str(tmp_path / "absent"))
    assert b.unwritable_reason() is None
    b.write(0x10, 0xAB)
    assert io.read_bytes()[0x10] == 0xAB
