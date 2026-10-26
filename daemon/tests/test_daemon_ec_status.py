"""What the daemon says when the fans are not doing as they are told.

The failure this covers is the quiet one from #25: the curve engine tried to
drive the fans every two seconds, every attempt raised, the tick caught it and
printed to the journal — and `gfan status` went on announcing "profile max" on
a machine holding 27% duty. The user was told something was happening that had
entirely failed to happen.

Fakes come from ``daemon/conftest.py``.
"""
from conftest import Invocation  # noqa: F401  (kept for symmetry with siblings)

from gigactld import ec  # noqa: E402
from gigactld.service import INTERFACE  # noqa: E402


def _get(daemon, prop):
    return daemon._get_property(None, None, None, INTERFACE, prop)


def _driving(make_daemon, profile="max"):
    """A daemon whose engine is actually trying to drive the fans."""
    d = make_daemon()
    d.engine.set_profile(profile)
    return d


def test_a_failing_engine_says_the_profile_is_not_being_applied(make_daemon):
    d = _driving(make_daemon)
    d.ec.write_error = OSError("EC busy")
    d._tick()
    status = _get(d, "EcStatus").get_string()
    assert status, "a profile that cannot be applied must not look healthy"
    assert "max" in status, f"say which profile is not happening: {status!r}"


def test_the_chosen_profile_is_still_reported_while_it_fails(make_daemon):
    """The profile is what the user asked for; whether it is happening is a
    separate question, and collapsing the two would lose one of them."""
    d = _driving(make_daemon)
    d.ec.write_error = OSError("EC busy")
    d._tick()
    assert _get(d, "ActiveProfile").get_string() == "max"


def test_status_clears_once_the_fans_accept_writes_again(make_daemon):
    d = _driving(make_daemon)
    d.ec.write_error = OSError("EC busy")
    d._tick()
    assert _get(d, "EcStatus").get_string() != ""
    d.ec.write_error = None
    d._tick()
    assert _get(d, "EcStatus").get_string() == ""


def test_a_sustained_fault_is_logged_once_not_every_tick(make_daemon, capsys):
    """The tick runs every two seconds; a persistent fault that logged each time
    would bury the journal and tell the reader nothing new."""
    d = _driving(make_daemon)
    d.ec.write_error = OSError("EC busy")
    for _ in range(5):
        d._tick()
    assert capsys.readouterr().out.count("EC busy") == 1


def test_recovery_is_logged_too(make_daemon, capsys):
    """A warning that never retracts is worthless once the machine is fixed."""
    d = _driving(make_daemon)
    d.ec.write_error = OSError("EC busy")
    d._tick()
    d.ec.write_error = None
    capsys.readouterr()
    d._tick()
    assert capsys.readouterr().out.strip(), "recovery should say so"


def test_telemetry_keeps_flowing_while_writes_fail(make_daemon):
    """Reads and writes fail independently — losing the temperatures as well
    would take away the one thing still working."""
    d = _driving(make_daemon)
    d.ec.write_error = OSError("EC busy")
    d.ec.regs.update({0x07: 88, 0x0A: 70})
    d._tick()
    signals = [e for e in d._conn.emissions if e[1] == "Telemetry"]
    assert signals and signals[-1][2][:2] == (88, 70)


def test_a_status_change_is_announced_so_a_running_client_notices(make_daemon):
    """The window is open while this happens; it must not need a restart."""
    d = _driving(make_daemon)
    d.ec.write_error = OSError("EC busy")
    d._tick()
    changed = d._conn.properties_changed()
    assert changed is not None and "EcStatus" in changed[1]


def test_sustained_tick_failure_hands_fans_back_to_firmware(make_daemon):
    """T2.1 watchdog: if telemetry reads keep failing, the fans may be stuck
    at the last manual duty with nothing driving them. After the threshold
    the daemon must hand both fans back to firmware and stop claiming the
    profile."""
    d = _driving(make_daemon)

    def dead_read(off):
        raise OSError("EC gone")
    d.ec.read_u8 = dead_read
    for _ in range(d.FAILSAFE_AFTER_TICKS - 1):
        d._tick()
    assert d.engine.profile == "max", "one or two bad ticks must not trip the failsafe"
    d._tick()
    assert d.engine.profile == "firmware"
    auto = [w for w in d.ec.writes if w == (0xF9, 0xFF)]
    assert len(auto) == 2, f"both fans handed back, writes were {d.ec.writes}"
    assert "firmware" in _get(d, "EcStatus").get_string()


def test_sustained_write_failure_trips_failsafe_without_spamming(make_daemon):
    """The write-failure path reaches the watchdog through EcStatus, and a
    restore attempt that also fails must not stop the profile being dropped."""
    d = _driving(make_daemon)
    d.ec.write_error = OSError("EC busy")
    for _ in range(d.FAILSAFE_AFTER_TICKS):
        d._tick()
    assert d.engine.profile == "firmware"
    writes_after_trip = len(d.ec.writes)
    d._tick()
    assert len(d.ec.writes) == writes_after_trip, "failsafe trips once, not every tick"


def test_failsafe_retries_the_handback_until_the_ec_accepts_it(make_daemon):
    """Tripping while the EC refuses writes must not strand the fans at a
    manual duty forever: once writes land again, a later tick retries the
    firmware handback — and then stops retrying."""
    d = _driving(make_daemon)
    d.ec.write_error = OSError("EC busy")
    for _ in range(d.FAILSAFE_AFTER_TICKS):
        d._tick()
    assert d.engine.profile == "firmware"
    assert not [w for w in d.ec.writes if w == (0xF9, 0xFF)]
    d.ec.write_error = None
    d._tick()
    auto = [w for w in d.ec.writes if w == (0xF9, 0xFF)]
    assert len(auto) == 2, f"handback retried once the EC recovered: {d.ec.writes}"
    writes = len(d.ec.writes)
    d._tick()
    assert len(d.ec.writes) == writes, "landed handback is not repeated"


def test_a_read_only_ec_outranks_a_generic_write_failure(make_daemon):
    """Both are true at once, and only one of them tells the user what to do."""
    d = _driving(make_daemon)
    d.ec.unwritable = ec.READ_ONLY_REASON
    d._tick()
    assert "modprobe" in _get(d, "EcStatus").get_string()


def test_choosing_a_profile_fails_out_loud_when_it_cannot_be_applied(make_daemon):
    """The periodic tick swallows a write failure on purpose, because it will
    meet the same one in two seconds. A user's own request must not: reporting
    success for a profile that never reached the hardware is the #25 bug in a
    different place."""
    from conftest import Invocation

    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import GLib

    d = make_daemon()
    d.ec.unwritable = ec.READ_ONLY_REASON
    inv = Invocation()
    d._method_call(None, ":1.5", None, INTERFACE, "SetProfile",
                   GLib.Variant("(s)", ("performance",)), inv)
    assert inv.error is not None, "SetProfile must not claim success"
    assert inv.error[0].endswith("EcNotWritable")
