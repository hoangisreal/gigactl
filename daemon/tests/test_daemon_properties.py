"""The D-Bus property surface: what the GUI reads back to show current state,
and the change notifications that keep it in sync. No bus, no hardware.

Fakes come from ``daemon/conftest.py``.
"""
from conftest import Invocation

import gi

gi.require_version("Gio", "2.0")
from gi.repository import GLib  # noqa: E402

from gigactld import keyboard as kb  # noqa: E402
from gigactld.service import INTERFACE  # noqa: E402


def _get(daemon, prop):
    return daemon._get_property(None, None, None, INTERFACE, prop)


def test_active_profile_property_reflects_engine(make_daemon):
    d = make_daemon()
    d.engine.set_profile("performance")
    assert _get(d, "ActiveProfile").get_string() == "performance"


def test_keyboard_state_property_reports_remembered_state(make_daemon):
    d = make_daemon()
    d.keyboard = kb.KeyboardState(enabled=True, r=255, g=128, b=0, brightness_pct=60)
    assert _get(d, "KeyboardState").unpack() == (True, 255, 128, 0, 60)


def test_keyboard_state_property_falls_back_to_firmware_default(make_daemon):
    d = make_daemon()  # nothing set yet (lazy None)
    assert d.keyboard is None
    # honest fallback: the firmware default the EC itself boots with
    assert _get(d, "KeyboardState").unpack() == (True, 0, 0, 255, 100)


def test_telemetry_property_reports_the_same_reading_as_the_signal(make_daemon):
    """The signal is for streaming; the property is for one-shot readers — the
    CLI asks once and exits, and cannot wait out a poll interval. Both must
    describe the same hardware."""
    d = make_daemon()
    d.ec.regs.update({0x07: 71, 0x0A: 64, 0xCE: 128, 0xCF: 64})
    d._sample()  # getters use the bounded control-loop cache, never initiate I/O
    assert _get(d, "Telemetry").unpack() == (71, 64, 0, 0, 50, 25)


def test_telemetry_property_survives_an_unreadable_ec(make_daemon):
    """A raising getter would take the GLib dispatcher down; None makes GDBus
    answer the caller with an error instead."""
    d = make_daemon()

    def boom(_off):
        raise OSError("EC gone")

    d.ec.read_u8 = boom
    assert _get(d, "Telemetry") is None


def test_unknown_property_is_none(make_daemon):
    assert _get(make_daemon(), "NoSuchProperty") is None


def test_set_profile_notifies_active_profile_changed(make_daemon):
    d = make_daemon()
    d._set_profile(None, GLib.Variant("(s)", ("quiet",)), Invocation())
    iface, props, _invalidated = d._conn.properties_changed()
    assert iface == INTERFACE
    assert props["ActiveProfile"] == "quiet"


def test_set_keyboard_color_notifies_keyboard_state_changed(make_daemon):
    d = make_daemon()
    d._set_keyboard_color(None, GLib.Variant("(uuu)", (255, 0, 0)), Invocation())
    _iface, props, _inv = d._conn.properties_changed()
    assert props["KeyboardState"] == (True, 255, 0, 0, 100)


def test_restore_firmware_notifies_profile_changed(make_daemon):
    d = make_daemon()
    d.engine.set_profile("max")
    d._restore_firmware(None, Invocation())
    _iface, props, _inv = d._conn.properties_changed()
    assert props["ActiveProfile"] == "firmware"


def test_manual_duty_notifies_profile_changed(make_daemon):
    """A direct duty override moves the engine to 'manual'; a running GUI must
    hear about it, since it did not make that change itself."""
    d = make_daemon()
    d._set_fan_duty(None, GLib.Variant("(uu)", (0, 60)), Invocation())
    _iface, props, _inv = d._conn.properties_changed()
    assert props["ActiveProfile"] == "manual"


def test_notify_without_connection_is_safe(make_daemon):
    d = make_daemon(connected=False)  # _conn stays None
    d._notify_properties(ActiveProfile=GLib.Variant("s", "quiet"))  # must not raise


# --- Curves: what the curve editor loads and follows ------------------------

def test_curves_property_reports_the_active_profile_shape(make_daemon):
    from gigactld import curve
    d = make_daemon()
    d.engine.set_profile("balanced")
    linked, cpu, gpu = _get(d, "Curves").unpack()
    assert linked is True
    assert [tuple(p) for p in cpu] == curve.PROFILES["balanced"]
    assert cpu == gpu  # linked profiles drive both fans identically


def test_curves_property_is_empty_on_firmware(make_daemon):
    d = make_daemon()  # firmware by default: the engine drives nothing
    linked, cpu, gpu = _get(d, "Curves").unpack()
    assert cpu == [] and gpu == []


def test_curves_property_exposes_split_curves(make_daemon):
    d = make_daemon()
    d.engine.set_custom(cpu_points=[(40, 20), (95, 90)],
                        gpu_points=[(40, 50), (95, 100)], linked=False)
    linked, cpu, gpu = _get(d, "Curves").unpack()
    assert linked is False
    assert cpu != gpu


def test_set_curve_notifies_curves_and_profile(make_daemon):
    d = make_daemon()
    points = [(40, 25), (55, 35), (70, 55), (85, 80), (95, 100)]
    d._set_curve(None, GLib.Variant("(sa(uu)b)", ("cpu", points, True)), Invocation())
    _iface, props, _inv = d._conn.properties_changed()
    assert props["ActiveProfile"] == "custom"
    linked, cpu, _gpu = props["Curves"]
    assert linked is True
    assert [tuple(p) for p in cpu] == points


# --- EcStatus: is anything we are told actually happening? (#25) -------------

def test_ec_status_is_empty_when_writes_land(make_daemon):
    """Empty means healthy, so a client can show the property verbatim without
    first deciding whether it is a message or a state."""
    assert _get(make_daemon(), "EcStatus").get_string() == ""


def test_ec_status_reports_a_read_only_ec(make_daemon):
    from gigactld import ec

    d = make_daemon()
    d.ec.unwritable = ec.READ_ONLY_REASON
    assert "modprobe" in _get(d, "EcStatus").get_string()


def test_ec_status_clears_once_the_ec_accepts_writes_again(make_daemon):
    from gigactld import ec

    d = make_daemon()
    d.ec.unwritable = ec.READ_ONLY_REASON
    assert _get(d, "EcStatus").get_string() != ""
    d.ec.unwritable = None
    assert _get(d, "EcStatus").get_string() == ""


def test_setting_a_duty_on_a_read_only_ec_names_the_cause_and_the_fix(make_daemon):
    """#25 got '[Errno 22] Invalid argument', which blames the daemon for a
    kernel refusal and offers nothing to act on."""
    from gigactld import ec

    d = make_daemon()
    d.ec.unwritable = ec.READ_ONLY_REASON
    inv = Invocation()
    d._method_call(None, ":1.5", None, INTERFACE, "SetFanDuty",
                   GLib.Variant("(uu)", (0, 70)), inv)
    assert inv.error is not None, "the call must fail, not silently do nothing"
    name, message = inv.error
    assert name.endswith("EcNotWritable"), name
    assert "modprobe" in message
    assert "Errno" not in message


def test_setting_a_keyboard_colour_on_a_read_only_ec_says_the_same_thing(make_daemon):
    from gigactld import ec

    d = make_daemon()
    d.ec.unwritable = ec.READ_ONLY_REASON
    inv = Invocation()
    d._method_call(None, ":1.5", None, INTERFACE, "SetKeyboardColor",
                   GLib.Variant("(uuu)", (255, 0, 0)), inv)
    assert inv.error is not None
    assert inv.error[0].endswith("EcNotWritable")
    assert "modprobe" in inv.error[1]


def test_a_read_only_ec_still_serves_telemetry(make_daemon):
    """The reason to keep the daemon running at all: the temperatures in #25
    were correct throughout."""
    from gigactld import ec

    d = make_daemon()
    d.ec.unwritable = ec.READ_ONLY_REASON
    d.ec.regs.update({0x07: 71, 0x0A: 64})
    d._sample()
    assert _get(d, "Telemetry").unpack()[:2] == (71, 64)


def test_startup_announces_a_read_only_ec(make_daemon, capsys):
    """Nobody may be watching the bus, but the journal is always there — and
    'why is nothing happening' is answered fastest by systemctl status."""
    from gigactld import ec

    d = make_daemon()
    d.ec.unwritable = ec.READ_ONLY_REASON
    d._on_name_acquired(None, "io.github.smairio.gigactl")
    out = capsys.readouterr().out
    assert "modprobe" in out


def test_startup_is_quiet_about_a_healthy_ec(make_daemon, capsys):
    d = make_daemon()
    d._on_name_acquired(None, "io.github.smairio.gigactl")
    assert "modprobe" not in capsys.readouterr().out
