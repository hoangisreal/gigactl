"""Reconciliation clocks and EC registers are in memory, never live devices."""
import pytest

from conftest import FakeEc
from gigactld import ec, fans, mailbox
from gigactld.fan_control import FanControl


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def ramping_ec():
    device = FakeEc()
    original = device.write_u8

    def write(off, value):
        if off == mailbox.FCMD and device.regs.get(mailbox.FDAT) in fans.FANS:
            device.writes.append((off, value))
            device.regs[off] = 0  # accepted, duty will ramp separately
        else:
            original(off, value)

    device.write_u8 = write
    return device


def test_slow_downward_ramp_is_pending_until_observed_not_falsely_rejected():
    clock = Clock()
    device = ramping_ec()
    device.regs[0xCE] = 255
    control = FanControl(device, clock)
    control.apply({1: 20})
    assert control.pending_duties == (1,)
    for raw in range(233, 50, -22):
        clock.now += 2
        device.regs[0xCE] = raw
        control.check()
    device.regs[0xCE] = 51
    clock.now += 2
    control.check()
    assert not control.pending_duties


def test_slow_upward_ramp_is_pending_until_observed_not_falsely_rejected():
    clock = Clock()
    device = ramping_ec()
    device.regs[0xCE] = 0
    control = FanControl(device, clock)
    control.apply({1: 80})
    assert control.pending_duties == (1,)
    for raw in range(30, 204, 30):
        clock.now += 2
        device.regs[0xCE] = raw
        control.check()
        assert control.pending_duties == (1,)
    device.regs[0xCE] = 204
    clock.now += 2
    control.check()
    assert not control.pending_duties


def test_two_fan_asymmetric_ramp_reconciles_each_fan_independently():
    clock = Clock()
    device = ramping_ec()
    device.regs[0xCE] = 0
    device.regs[0xCF] = 0
    control = FanControl(device, clock)
    control.apply({1: 50, 2: 90})  # 128 and 230
    assert set(control.pending_duties) == {1, 2}

    # Tick 1: fan 1 and 2 start ramping, neither within tolerance
    clock.now += 2
    device.regs[0xCE] = 80
    device.regs[0xCF] = 60
    control.check()
    assert set(control.pending_duties) == {1, 2}

    # Tick 2: fan 1 reaches target, fan 2 continues ramping
    clock.now += 2
    device.regs[0xCE] = 128
    device.regs[0xCF] = 150
    control.check()
    assert control.pending_duties == (2,)

    # Tick 3: fan 2 reaches target
    clock.now += 2
    device.regs[0xCF] = 230
    control.check()
    assert not control.pending_duties


def test_ramp_target_change_mid_ramp_updates_deadline_and_raw_target():
    clock = Clock()
    device = ramping_ec()
    device.regs[0xCE] = 50
    control = FanControl(device, clock)
    control.apply({1: 80})  # raw 204
    assert control.targets[1].raw == 204

    clock.now += 2
    device.regs[0xCE] = 100
    control.check()
    assert control.pending_duties == (1,)

    # Target changes to 40% (raw 102) before reaching 204
    control.apply({1: 40})
    assert control.targets[1].raw == 102
    assert control.targets[1].started == clock.now

    clock.now += 2
    device.regs[0xCE] = 102
    control.check()
    assert not control.pending_duties


def test_small_progress_cannot_extend_absolute_convergence_deadline_forever():
    clock = Clock()
    device = ramping_ec()
    device.regs[0xCE] = 255
    control = FanControl(device, clock)
    control.apply({1: 0})
    for i in range(1, 15):
        clock.now = i * 2
        device.regs[0xCE] = 255 - i
        control.check()
    clock.now = 30
    with pytest.raises(ec.EcError, match="not verified"):
        control.check()


def test_retries_do_not_reset_no_progress_deadline():
    clock = Clock()
    device = ramping_ec()
    control = FanControl(device, clock)
    control.apply({1: 100})
    for i in (2, 4):
        clock.now = i
        control.check()
    clock.now = 6
    with pytest.raises(ec.EcError, match="not verified"):
        control.check()


def test_invalid_batch_has_zero_writes():
    device = FakeEc()
    control = FanControl(device)
    with pytest.raises(ValueError):
        control.apply({1: 70, 2: 101})
    assert not device.writes
