"""Entrypoint/recovery simulation. Never create a real EC or /run lock."""
from contextlib import contextmanager

import pytest

from conftest import FakeEc
from gigactld import __main__ as entry, ec, fans
from gigactld.platform import PlatformError


def fake_entry(monkeypatch, device):
    monkeypatch.setattr(entry, "require_supported_platform", lambda: None)
    monkeypatch.setattr(entry, "EmbeddedController", lambda **kwargs: device)
    monkeypatch.setattr(entry.time, "sleep", lambda seconds: None)
    events = []

    @contextmanager
    def lease():
        events.append("acquire")
        try:
            yield
        finally:
            events.append("release")

    monkeypatch.setattr(entry, "OwnerLease", lease)
    return events


@pytest.mark.parametrize("arguments", [[], ["--restore-firmware"]])
def test_unsupported_platform_refuses_before_lease_or_backend(monkeypatch, arguments):
    def refuse():
        raise PlatformError("unverified platform")

    monkeypatch.setattr(entry, "require_supported_platform", refuse)
    monkeypatch.setattr(entry, "OwnerLease", lambda: pytest.fail("lease acquired"))
    monkeypatch.setattr(entry, "EmbeddedController", lambda **kwargs: pytest.fail("EC created"))
    assert entry.main(arguments) == 1


def test_stop_post_is_independent_of_daemon_handlers(monkeypatch):
    device = FakeEc()
    device.modes = {1: "manual", 2: "manual"}  # crashed process no longer runs
    events = fake_entry(monkeypatch, device)
    monkeypatch.setattr(entry, "Daemon", lambda *a, **k: pytest.fail("daemon constructed"))
    assert entry.main(["--restore-firmware"]) == 0
    assert device.modes == {1: "firmware", 2: "firmware"}
    assert events == ["acquire", "release"]


def test_python_crash_attempts_recovery_before_releasing_owner_lease(monkeypatch):
    device = FakeEc()
    device.modes = {1: "manual", 2: "manual"}
    events = fake_entry(monkeypatch, device)

    class CrashingDaemon:
        def __init__(self, *args, **kwargs):
            self._started = True
            self._owns_name = True

        def run(self):
            raise ec.EcError("simulated controller crash")

    monkeypatch.setattr(entry, "Daemon", CrashingDaemon)
    assert entry.main([]) == 1
    assert device.modes == {1: "firmware", 2: "firmware"}
    assert events == ["acquire", "release"]


def test_recovery_attempts_other_fan_and_retries_only_failed_fan(monkeypatch):
    attempts = []

    def restore(device, fan):
        attempts.append(fan)
        if fan == 1:
            raise ec.EcError("permanent EC timeout")

    monkeypatch.setattr(fans, "restore_auto", restore)
    monkeypatch.setattr(entry.time, "sleep", lambda seconds: None)
    assert entry.restore_firmware(FakeEc()) is False
    assert attempts == [1, 2, 1, 1]


def test_argument_errors_cannot_construct_controller(monkeypatch):
    monkeypatch.setattr(entry, "EmbeddedController", lambda **kwargs: pytest.fail("EC created"))
    with pytest.raises(SystemExit):
        entry.main(["--interval", "0"])
