"""Shared EC mailbox helper — command sequencing and the pct scaler."""
from contextlib import contextmanager

import pytest

from gigactld import mailbox
from gigactld.ec import EcError


class RecordingEc:
    def __init__(self):
        self.writes = []
        self.events = []
        self.active = False

    def read_u8(self, off):
        assert self.active
        assert off == mailbox.FCMD
        self.events.append(("read", off))
        return 0  # emulate immediate firmware consumption/clear acknowledgement

    def write_u8(self, off, val):
        assert self.active
        self.events.append(("write", off))
        self.writes.append((off, val))

    @contextmanager
    def transaction(self):
        self.active = True
        try:
            yield
        finally:
            self.active = False


def test_command_writes_sub_params_then_doorbell():
    ec = RecordingEc()
    mailbox.command(ec, 0x03, (10, 20, 30), doorbell=0xCA)
    assert ec.writes == [
        (mailbox.FDAT, 0x03),
        (mailbox.FBUF, 10),
        (mailbox.FBF1, 20),
        (mailbox.FBF2, 30),
        (mailbox.FCMD, 0xCA),
    ]
    assert ec.events[0] == ("read", mailbox.FCMD)
    assert ec.events[-1] == ("read", mailbox.FCMD)


def test_command_writes_only_the_params_given():
    ec = RecordingEc()
    mailbox.command(ec, 0x0C, (0x3F,), doorbell=0xC4)
    assert ec.writes == [
        (mailbox.FDAT, 0x0C),
        (mailbox.FBUF, 0x3F),
        (mailbox.FCMD, 0xC4),
    ]


def test_command_rejects_too_many_params():
    ec = RecordingEc()
    with pytest.raises(ValueError):
        mailbox.command(ec, 0, (1, 2, 3, 4, 5), doorbell=0xC1)
    assert ec.events == []


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(mailbox.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(mailbox.time, "sleep", lambda duration: now.__setitem__(0, now[0] + duration))
    return now


def test_busy_mailbox_is_never_overwritten(clock):
    class BusyEc(RecordingEc):
        def read_u8(self, off):
            super().read_u8(off)
            return 0xC1

    ec = BusyEc()
    with pytest.raises(EcError, match="idle before parameters"):
        mailbox.command(ec, 1, (204,), doorbell=0xC1, timeout_s=0.02)
    assert ec.writes == []
    assert ec.active is False
    assert clock[0] == pytest.approx(0.02)


def test_waits_for_idle_then_for_clear_acknowledgement(clock):
    class DelayedEc(RecordingEc):
        def __init__(self):
            super().__init__()
            self.values = iter([0xC4, 0xC4, 0, 0xC1, 0xC1, 0])

        def read_u8(self, off):
            super().read_u8(off)
            if len(self.events) <= 3:
                assert self.writes == []
            return next(self.values)

    ec = DelayedEc()
    mailbox.command(ec, 1, (204,), doorbell=0xC1, timeout_s=0.05)
    assert len([event for event in ec.events if event[0] == "read"]) == 6
    assert ec.writes == [(mailbox.FDAT, 1), (mailbox.FBUF, 204), (mailbox.FCMD, 0xC1)]


def test_missing_ack_is_an_error_not_success_or_implicit_retry(clock):
    class UnacknowledgedEc(RecordingEc):
        def read_u8(self, off):
            super().read_u8(off)
            return 0xC1 if self.writes else 0

    ec = UnacknowledgedEc()
    with pytest.raises(EcError, match="acknowledgement"):
        mailbox.command(ec, 1, (204,), doorbell=0xC1, timeout_s=0.02)
    assert ec.writes == [(mailbox.FDAT, 1), (mailbox.FBUF, 204), (mailbox.FCMD, 0xC1)]
    assert ec.active is False


@pytest.mark.parametrize("kwargs", [
    {"sub": -1, "params": (), "doorbell": 0xC1},
    {"sub": 1, "params": (256,), "doorbell": 0xC1},
    {"sub": 1, "params": (True,), "doorbell": 0xC1},
    {"sub": 1, "params": (10, -1), "doorbell": 0xC1},
    {"sub": 1, "params": (), "doorbell": 0},
    {"sub": 1, "params": (), "doorbell": 0x1C1},
    {"sub": 1, "params": (), "doorbell": 0xC1, "timeout_s": float("nan")},
])
def test_invalid_commands_fail_before_any_mailbox_io(kwargs):
    ec = RecordingEc()
    with pytest.raises(ValueError):
        mailbox.command(ec, **kwargs)
    assert ec.events == []


def test_pct_to_byte():
    assert mailbox.pct_to_byte(0) == 0
    assert mailbox.pct_to_byte(100) == 255
    assert mailbox.pct_to_byte(70) == 178


@pytest.mark.parametrize("bad", [-5, 150, True, 70.0, "70"])
def test_pct_to_byte_rejects_out_of_range_instead_of_clamping(bad):
    with pytest.raises(ValueError):
        mailbox.pct_to_byte(bad)
