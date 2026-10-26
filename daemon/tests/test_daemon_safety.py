"""M1 fault injection: only fake EC, fake authorization and temporary state."""
import json

import pytest
from gi.repository import GLib

from conftest import Invocation, RecordingConn
from gigactld import curve, ec, fans, mailbox, safety, state
from gigactld.service import INTERFACE


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds=2):
        self.now += seconds


def call(d, method, signature="(s)", values=("performance",)):
    invocation = Invocation()
    params = GLib.Variant(signature, values) if signature else None
    d._method_call(None, ":1.fake", None, INTERFACE, method, params, invocation)
    return invocation


def sleep_signal(d, sleeping):
    d._on_prepare_for_sleep(None, None, None, None, None,
                            GLib.Variant("(b)", (sleeping,)))


def duties(d):
    return tuple(d.ec.regs.get(fans.DUTY_REG[f], 0) for f in fans.FANS)


@pytest.mark.parametrize("cpu,gpu,want", [(45, 40, 0), (70, 40, 30),
                                         (45, 70, 30), (95, 40, 100),
                                         (45, 87, 100), (70, 0, 30)])
def test_thermal_policy_is_applied_to_manual_dbus_requests(make_daemon, cpu, gpu, want):
    d = make_daemon()
    d.ec.regs.update({0x07: cpu, 0x0A: gpu})
    inv = call(d, "SetFanDuty", "(uu)", (0, 0))
    assert inv.returned and inv.error is None
    assert duties(d) == (want * 255 // 100,) * 2


def test_manual_override_remains_thermally_supervised_after_request(make_daemon):
    d = make_daemon()
    assert call(d, "SetFanDuty", "(uu)", (0, 0)).returned
    d.ec.regs[0x07] = 96
    d._tick()
    assert duties(d) == (255, 255)
    d.ec.regs[0x07] = 45
    d._tick()
    assert duties(d) == (0, 0)


def test_emergency_override_of_other_fan_is_released_after_cooling(make_daemon):
    d = make_daemon()
    call(d, "SetFanDuty", "(uu)", (1, 60))
    assert d.ec.modes == {1: "manual", 2: "firmware"}
    d.ec.regs[0x0A] = 90
    d._tick()
    assert duties(d) == (255, 255)
    d.ec.regs[0x0A] = 40
    d._tick()
    assert d.ec.modes == {1: "manual", 2: "firmware"}


def test_one_fan_manual_request_releases_other_previously_curve_controlled_fan(make_daemon):
    d = make_daemon()
    assert call(d, "SetProfile").returned
    assert d.ec.modes == {1: "manual", 2: "manual"}
    assert call(d, "SetFanDuty", "(uu)", (1, 60)).returned
    assert d.ec.modes == {1: "manual", 2: "firmware"}


def test_custom_zero_curve_cannot_defeat_emergency(make_daemon):
    d = make_daemon()
    d.ec.regs[0x07] = 100
    points = [(40, 0), (55, 0), (70, 0), (85, 0), (100, 0)]
    inv = call(d, "SetCurve", "(sa(uu)b)", ("cpu", points, True))
    assert inv.returned
    assert duties(d) == (255, 255)


@pytest.mark.parametrize("write_number", range(1, 7))
def test_every_partial_two_fan_write_boundary_recovers_both(make_daemon, write_number):
    d = make_daemon()
    original = d.ec.write_u8
    calls = 0

    def fault(off, value):
        nonlocal calls
        calls += 1
        if calls == write_number:
            raise ec.EcError("injected write timeout")
        original(off, value)

    d.ec.write_u8 = fault
    inv = call(d, "SetFanDuty", "(uu)", (0, 60))
    assert inv.error and not inv.returned
    assert d.engine.profile == curve.FIRMWARE
    assert d.ec.modes == {1: "firmware", 2: "firmware"}
    assert not d.control.pending_auto
    assert d._failsafe_tripped


@pytest.mark.parametrize("read_number", range(1, 17))
def test_sample_command_and_readback_exceptions_never_strand_partial_manual(
        make_daemon, read_number):
    d = make_daemon()
    original = d.ec.read_u8
    reads = 0

    def fault(off):
        nonlocal reads
        reads += 1
        if reads == read_number:
            raise ec.EcError("injected read timeout")
        return original(off)

    d.ec.read_u8 = fault
    inv = call(d, "SetFanDuty", "(uu)", (0, 60))
    assert inv.error and not inv.returned
    assert d.ec.modes == {1: "firmware", 2: "firmware"}
    assert d.engine.profile == curve.FIRMWARE


def test_first_fan_failed_handback_does_not_prevent_second_and_is_retried(
        make_daemon, monkeypatch):
    d = make_daemon()
    failed = True
    attempted = []
    original = fans.restore_auto

    def restore(ec_, fan):
        attempted.append(fan)
        if fan == 1 and failed:
            raise ec.EcError("synthetic restore failure")
        original(ec_, fan)

    monkeypatch.setattr(fans, "restore_auto", restore)
    d._enter_recovery("test failure")
    assert attempted == [1, 2]
    assert d.control.pending_auto == {1}
    assert "unconfirmed" in d.ec_status()
    assert "handed back" not in d.ec_status()
    d._tick()
    assert attempted == [1, 2, 1]
    failed = False
    d._tick()
    assert attempted == [1, 2, 1, 1]
    assert not d.control.pending_auto
    assert "acknowledged" in d.ec_status()
    assert d._failsafe_tripped  # transport recovery is not user re-arming


@pytest.mark.parametrize("method,signature,values", [
    ("SetProfile", "(s)", ("performance",)),
    ("SetCurve", "(sa(uu)b)", ("cpu", [(40, 40), (95, 100)], True)),
    ("SetFanDuty", "(uu)", (0, 60)),
])
def test_all_fan_entrypoints_rearm_and_can_trip_again(make_daemon, method, signature, values):
    d = make_daemon()
    d._enter_recovery("first failure")
    assert call(d, method, signature, values).returned
    assert not d._failsafe_tripped
    d.ec.regs[0x07] = 0  # successful I/O but invalid thermal data
    d._tick()
    assert d._failsafe_tripped
    assert d.engine.profile == curve.FIRMWARE


def test_silent_doorbell_rejection_is_not_a_verified_profile(make_daemon):
    clock = Clock()
    d = make_daemon(clock=clock)
    original = d.ec.write_u8

    def ignore_manual(off, value):
        # Acknowledge consumption, but silently ignore only fan duty commands.
        if off == mailbox.FCMD and value == fans.DOORBELL and d.ec.regs.get(mailbox.FDAT) != 255:
            d.ec.writes.append((off, value))
            d.ec.regs[off] = 0
        else:
            original(off, value)

    d.ec.write_u8 = ignore_manual
    assert call(d, "SetProfile", values=("max",)).returned
    assert d.control.pending_duties == (1, 2)
    assert d.control_state() == "verifying"
    for _ in range(3):
        clock.advance()
        d._tick()
    assert d._failsafe_tripped
    assert d.engine.profile == curve.FIRMWARE
    assert "not verified" in d.ec_status()


def test_cached_target_is_reconciled_after_external_reset(make_daemon):
    d = make_daemon()
    call(d, "SetProfile", values=("max",))
    d.ec.regs.update({0xCE: 0, 0xCF: 0})
    d._tick()  # unchanged temperatures must not suppress verification
    assert duties(d) == (255, 255)


@pytest.mark.parametrize("cpu,gpu", [(0, 40), (255, 40), (45, 255)])
def test_invalid_thermal_data_enters_recovery_without_low_fan_writes(make_daemon, cpu, gpu):
    d = make_daemon()
    call(d, "SetFanDuty", "(uu)", (0, 60))
    d.ec.writes.clear()
    d.ec.regs.update({0x07: cpu, 0x0A: gpu})
    d._tick()
    assert d._failsafe_tripped
    assert d.ec.modes == {1: "firmware", 2: "firmware"}
    assert d._get_property(None, None, None, INTERFACE, "Telemetry") is None


def test_unknown_gpu_uses_cpu_for_cooling_not_a_fake_zero_degree_sample(make_daemon):
    d = make_daemon()
    d.ec.regs.update({0x07: 80, 0x0A: 0})
    assert call(d, "SetProfile", values=("balanced",)).returned
    assert duties(d)[0] == duties(d)[1]
    assert "unavailable or asleep" in d._get_property(None, None, None, INTERFACE,
                                                     "TelemetryStatus").get_string()


def test_stale_sample_does_not_wait_for_three_more_errors(make_daemon):
    clock = Clock()
    d = make_daemon(clock=clock)
    call(d, "SetProfile")
    clock.advance(7)
    d.ec.read_u8 = lambda off: (_ for _ in ()).throw(ec.EcError("sensor timeout"))
    d._tick()
    assert d._failsafe_tripped
    assert d.control.pending_auto == {1, 2}


def test_property_reads_never_initiate_ec_transactions_and_expire(make_daemon):
    clock = Clock()
    d = make_daemon(clock=clock)
    d._sample()
    d.ec.read_u8 = lambda off: pytest.fail("getter performed hardware I/O")
    assert d._get_property(None, None, None, INTERFACE, "Telemetry") is not None
    clock.advance(6)
    assert d._get_property(None, None, None, INTERFACE, "Telemetry") is None


def test_suspend_hands_back_and_resume_reapplies_from_fresh_sensor_data(make_daemon):
    d = make_daemon()
    call(d, "SetProfile")
    sleep_signal(d, True)
    assert d.ec.modes == {1: "firmware", 2: "firmware"}
    before = list(d.ec.writes)
    d._tick()
    assert d.ec.writes == before
    assert call(d, "SetProfile").error[0].endswith("Unavailable")
    d.ec.regs.update({0xCE: 0, 0xCF: 0, 0x07: 75})
    sleep_signal(d, False)
    assert d.ec.modes == {1: "manual", 2: "manual"}
    assert d.engine.profile == "performance"
    assert len(d.ec.writes) > len(before)


def test_manual_override_does_not_resume_after_suspend(make_daemon):
    d = make_daemon()
    call(d, "SetFanDuty", "(uu)", (0, 60))
    sleep_signal(d, True)
    sleep_signal(d, False)
    assert d.engine.profile == curve.FIRMWARE
    assert d.ec.modes == {1: "firmware", 2: "firmware"}


def test_failed_suspend_handback_latches_recovery_and_blocks_resume(make_daemon):
    d = make_daemon()
    call(d, "SetProfile")
    assert d.ec.modes == {1: "manual", 2: "manual"}
    # The suspend handback itself fails: writes no longer land.
    d.ec.write_error = ec.EcError("injected suspend handback failure")
    sleep_signal(d, True)
    assert d._failsafe_tripped
    assert d.control.pending_auto == {1, 2}
    assert state.load(d.state_path)["recovery_latched"] is True
    # Resume must not resurrect the pre-suspend profile over a failed handback.
    d.ec.write_error = None
    sleep_signal(d, False)
    assert d.engine.profile == curve.FIRMWARE
    assert d._failsafe_tripped
    # The pending handback is still retried on later ticks, and the latch
    # clears only through an explicit new fan request.
    d._tick()
    assert not d.control.pending_auto
    assert call(d, "SetProfile").returned
    assert not d._failsafe_tripped


@pytest.mark.parametrize("change", ["keyboard", "turbo"])
def test_unrelated_save_does_not_persist_transient_manual_policy(make_daemon, change):
    d = make_daemon()
    call(d, "SetProfile", values=("balanced",))
    call(d, "SetFanDuty", "(uu)", (0, 60))
    if change == "keyboard":
        call(d, "SetKeyboardColor", "(uuu)", (10, 20, 30))
    else:
        call(d, "SetTurboBoost", "(b)", (True,))
    assert state.load(d.state_path)["profile"] == "balanced"


@pytest.mark.parametrize("data", [[], 42, {"linked": "false"},
                                 {"turbo_enabled": "false"},
                                 {"keyboard": {"r": "bad"}},
                                 {"keyboard": {"enabled": "false"}}])
def test_malformed_saved_state_cannot_write_any_optional_domain(make_daemon, data):
    d = make_daemon()
    with open(d.state_path, "w") as f:
        json.dump(data, f)
    d._restore_saved_state()
    assert not d.ec.writes
    assert d.engine.profile == curve.FIRMWARE


def test_safety_latch_survives_restart_without_reapplying_profile(make_daemon):
    d = make_daemon()
    call(d, "SetProfile")
    d._enter_recovery("simulated controller failure")
    restarted = make_daemon()
    restarted.state_path = d.state_path
    restarted._restore_saved_state()
    restarted.ec.modes = {1: "manual", 2: "manual"}  # old handback might have failed
    assert restarted.control.pending_auto == {1, 2}
    restarted._tick()
    assert restarted.engine.profile == curve.FIRMWARE
    assert restarted._failsafe_tripped
    assert restarted.ec.modes == {1: "firmware", 2: "firmware"}
    assert not any(off == mailbox.FDAT and value in fans.FANS
                   for off, value in restarted.ec.writes)


class DeferredAuthorizer:
    def __init__(self):
        self.callbacks = []

    def check_async(self, sender, action, callback):
        self.callbacks.append(callback)


def test_slow_authorization_cannot_resurrect_superseded_curve_after_escape(make_daemon):
    d = make_daemon()
    d.authorizer = DeferredAuthorizer()
    old = call(d, "SetProfile")
    escape = call(d, "RestoreFirmware", signature=None)
    assert not d.ec.writes  # no synchronous authorization or hardware access
    d.authorizer.callbacks[1](True)
    after_escape = list(d.ec.writes)
    d.authorizer.callbacks[0](True)
    assert escape.returned
    assert old.error[0].endswith("Superseded")
    assert d.ec.writes == after_escape
    assert d.engine.profile == curve.FIRMWARE


def test_authorization_completion_rechecks_ownership(make_daemon):
    d = make_daemon()
    d.authorizer = DeferredAuthorizer()
    inv = call(d, "SetProfile")
    d._owns_name = False
    d.authorizer.callbacks[0](True)
    assert inv.error[0].endswith("Unavailable")
    assert not d.ec.writes


def test_denied_authorization_never_touches_hardware(make_daemon):
    d = make_daemon()
    d.authorizer = DeferredAuthorizer()
    inv = call(d, "SetFanDuty", "(uu)", (0, 100))
    d.authorizer.callbacks[0](False)
    assert inv.error[0].endswith("NotAuthorized")
    assert not d.ec.writes


def test_losing_dbus_name_before_acquisition_has_zero_writes(make_daemon):
    d = make_daemon(connected=False)
    conn = RecordingConn()
    conn.register_object = lambda *args: 1
    d._on_bus_acquired(conn, "fake")
    assert not d.ec.writes
    d._on_name_lost(conn, "fake")
    assert not d.ec.writes


def test_control_loop_is_scheduled_before_state_restore(make_daemon, monkeypatch):
    d = make_daemon(connected=False)
    events = []
    monkeypatch.setattr(GLib, "timeout_add_seconds", lambda *args: events.append("timer"))
    monkeypatch.setattr(d, "_restore_saved_state", lambda: events.append("restore"))
    monkeypatch.setattr(d, "_tick", lambda: events.append("tick"))
    d._on_name_acquired(RecordingConn(), "fake")
    assert events == ["timer", "restore", "tick"]


def test_external_turbo_change_is_announced_without_reassertion(make_daemon):
    d = make_daemon()
    d._sync_turbo()
    with open(d.turbo.path, "w") as f:
        f.write("0")
    d._sync_turbo()
    assert d._conn.properties_changed()[1]["TurboBoost"] is True
    assert d._turbo_choice is None
