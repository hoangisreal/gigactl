"""sd_notify transport only: fake sockets and local, temporary Unix endpoints."""
import os
import socket
import sys
import uuid

import pytest

from gigactld import watchdog


@pytest.fixture(autouse=True)
def clean_watchdog_environment(monkeypatch):
    for key in ("WATCHDOG_USEC", "WATCHDOG_PID", "NOTIFY_SOCKET"):
        monkeypatch.delenv(key, raising=False)


def _enabled(monkeypatch, address="/run/fake-notify"):
    monkeypatch.setenv("WATCHDOG_USEC", "15000000")
    monkeypatch.setenv("WATCHDOG_PID", str(os.getpid()))
    monkeypatch.setenv("NOTIFY_SOCKET", address)
    return watchdog.Watchdog.from_environment()


def test_disabled_does_not_open_a_socket(monkeypatch):
    def forbidden(*_args):
        pytest.fail("a disabled watchdog opened a socket")
    monkeypatch.setattr(watchdog.socket, "socket", forbidden)
    obj = watchdog.Watchdog.from_environment()
    assert obj.interval_s == 0
    assert obj.ping() is False


def test_interval_is_half_the_systemd_deadline(monkeypatch):
    assert _enabled(monkeypatch).interval_s == 7.5


def test_watchdog_pid_is_optional(monkeypatch):
    _enabled(monkeypatch)
    monkeypatch.delenv("WATCHDOG_PID")
    assert watchdog.Watchdog.from_environment().interval_s == 7.5


@pytest.mark.parametrize("value", ["", "0", "-1", "+1", "15.0", " 15", "15 ",
                                 "abc", "١٥", str(2**64), "9" * 5000])
def test_malformed_usec_disables(monkeypatch, value):
    _enabled(monkeypatch)
    monkeypatch.setenv("WATCHDOG_USEC", value)
    assert watchdog.Watchdog.from_environment().interval_s == 0


@pytest.mark.parametrize("value", ["", "0", "-1", "+1", "1.5", " 1", "abc", str(2**31)])
def test_malformed_pid_disables(monkeypatch, value):
    _enabled(monkeypatch)
    monkeypatch.setenv("WATCHDOG_PID", value)
    assert watchdog.Watchdog.from_environment().interval_s == 0


def test_other_process_pid_disables_recovery_heartbeats(monkeypatch):
    _enabled(monkeypatch)
    monkeypatch.setenv("WATCHDOG_PID", str(os.getpid() + 1))
    assert watchdog.Watchdog.from_environment().interval_s == 0


@pytest.mark.parametrize("address", ["", "@", "/", "relative/path", "unix:/tmp/notify"])
def test_missing_or_invalid_notify_socket_disables(monkeypatch, address):
    assert _enabled(monkeypatch, address).interval_s == 0


class FakeSocket:
    def __init__(self, fail=None, short=False):
        self.fail = fail
        self.short = short
        self.closed = False
        self.messages = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def setblocking(self, enabled):
        assert enabled is False  # a full notify queue must never stall the loop

    def sendto(self, data, address):
        self.messages.append((data, address))
        if self.fail:
            raise self.fail
        return len(data) - int(self.short)


@pytest.mark.parametrize("address,expected", [("/run/fake-notify", "/run/fake-notify"),
                                               ("@fake-notify", "\0fake-notify")])
def test_only_watchdog_datagram_is_sent_and_socket_is_closed(monkeypatch, address, expected):
    obj = _enabled(monkeypatch, address)
    sock = FakeSocket()

    def factory(family, kind):
        assert family == socket.AF_UNIX and kind == socket.SOCK_DGRAM
        return sock

    monkeypatch.setattr(watchdog.socket, "socket", factory)
    assert obj.ping() is True
    assert sock.messages == [(b"WATCHDOG=1", expected)]
    assert sock.closed


@pytest.mark.parametrize("error", [BlockingIOError(), FileNotFoundError(),
                                 PermissionError(), ConnectionRefusedError()])
def test_transport_failure_is_nonfatal_and_closes_socket(monkeypatch, error):
    obj, sock = _enabled(monkeypatch), FakeSocket(fail=error)
    monkeypatch.setattr(watchdog.socket, "socket", lambda *_args: sock)
    assert obj.ping() is False
    assert sock.closed


def test_socket_creation_failure_is_nonfatal(monkeypatch):
    obj = _enabled(monkeypatch)

    def failed(*_args):
        raise OSError("fake descriptor exhaustion")

    monkeypatch.setattr(watchdog.socket, "socket", failed)
    assert obj.ping() is False


def test_short_datagram_is_not_reported_as_delivered(monkeypatch):
    obj, sock = _enabled(monkeypatch), FakeSocket(short=True)
    monkeypatch.setattr(watchdog.socket, "socket", lambda *_args: sock)
    assert obj.ping() is False


def test_temporary_filesystem_endpoint_receives_heartbeat(monkeypatch, tmp_path):
    path = str(tmp_path / "notify")
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
        receiver.bind(path)
        receiver.settimeout(1)
        assert _enabled(monkeypatch, path).ping() is True
        assert receiver.recv(64) == b"WATCHDOG=1"


@pytest.mark.skipif(sys.platform != "linux", reason="abstract Unix names are Linux-specific")
def test_temporary_abstract_endpoint_receives_heartbeat(monkeypatch):
    name = f"gigactl-test-{uuid.uuid4().hex}"
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
        receiver.bind("\0" + name)
        receiver.settimeout(1)
        assert _enabled(monkeypatch, "@" + name).ping() is True
        assert receiver.recv(64) == b"WATCHDOG=1"
