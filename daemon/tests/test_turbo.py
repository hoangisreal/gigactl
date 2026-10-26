"""Turbo Boost knob: sysfs reads/writes against a temp file, plus the
daemon surface (property, authorized method, persistence choice)."""
import pytest

from gigactld import turbo as turbo_mod


def _knob(tmp_path, value="1"):
    p = tmp_path / "no_turbo"
    p.write_text(value)
    return turbo_mod.TurboControl(str(p))


def test_enabled_reflects_no_turbo(tmp_path):
    assert _knob(tmp_path, "1").enabled() is False
    assert _knob(tmp_path, "0").enabled() is True
    assert _knob(tmp_path, "1").available() is True


def test_set_enabled_writes_the_inverse(tmp_path):
    knob = _knob(tmp_path, "1")
    knob.set_enabled(True)
    assert knob.enabled() is True
    knob.set_enabled(False)
    assert knob.enabled() is False


def test_missing_knob_is_unavailable(tmp_path):
    knob = turbo_mod.TurboControl(str(tmp_path / "absent-dir" / "no_turbo"))
    assert knob.available() is False
    with pytest.raises(turbo_mod.TurboUnavailable):
        knob.enabled()
    with pytest.raises(turbo_mod.TurboUnavailable):
        knob.set_enabled(True)


def test_daemon_property_reports_live_state(make_daemon, tmp_path):
    from gigactld.service import INTERFACE
    d = make_daemon()
    d.turbo = _knob(tmp_path, "1")
    get = lambda p: d._get_property(None, None, None, INTERFACE, p)
    assert get("TurboAvailable").get_boolean() is True
    assert get("TurboBoost").get_boolean() is False


def test_daemon_set_turbo_persists_choice(make_daemon, tmp_path):
    from conftest import Invocation

    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import GLib

    from gigactld.service import INTERFACE
    d = make_daemon()
    knob_path = tmp_path / "no_turbo"
    knob_path.write_text("1")
    d.turbo = turbo_mod.TurboControl(str(knob_path))
    inv = Invocation()
    d._method_call(None, ":1.9", None, INTERFACE, "SetTurboBoost",
                   GLib.Variant("(b)", (True,)), inv)
    assert inv.error is None and inv.returned
    assert knob_path.read_text() == "0"
    import json
    saved = json.loads(open(d.state_path).read())
    assert saved["turbo_enabled"] is True


def test_daemon_restore_applies_saved_turbo(make_daemon, tmp_path):
    import json
    d = make_daemon()
    knob_path = tmp_path / "no_turbo"
    knob_path.write_text("0")
    d.turbo = turbo_mod.TurboControl(str(knob_path))
    open(d.state_path, "w").write(json.dumps(
        {"profile": "firmware", "linked": True, "cpu": [], "gpu": [],
         "turbo_enabled": False}))
    d._restore_saved_state()
    assert knob_path.read_text() == "1"
