"""Polkit authorization against a fake Gio bus, never the system bus."""
import pytest

from gigactld import authz

Gio, GLib = authz.Gio, authz.GLib


class FakeBus:
    def __init__(self, allowed=True):
        self.reply = GLib.Variant("((bba{ss}))", ((allowed, False, {}),))
        self.calls = []
        self.error = None
        self.start_error = None

    def call(self, *args):
        self.calls.append(args)
        if self.start_error:
            raise self.start_error
        self.callback = args[9]

    def complete(self):
        self.callback(self, object(), None)

    def call_finish(self, _result):
        if self.error:
            raise self.error
        return self.reply

    def call_sync(self, *args):
        self.calls.append(args)
        return self.call_finish(None)


@pytest.fixture(autouse=True)
def no_real_bus(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("test attempted to connect to the real system bus")
    monkeypatch.setattr(Gio, "bus_get_sync", forbidden)
    monkeypatch.setattr(Gio, "bus_get", forbidden)


def _io_error(code):
    return GLib.Error.new_literal(Gio.io_error_quark(), "fake bus error", code)


def test_async_success_uses_unique_subject_interaction_and_explicit_deadline():
    bus, outcomes = FakeBus(), []
    authz.Authorizer(bus).check_async(":1.25", authz.ACTION_CONTROL_FANS, outcomes.append)
    assert outcomes == []  # dispatch must not wait for an interactive response
    (name, path, interface, method, params, reply_type, flags, timeout_ms,
     cancellable, _callback, _data), = bus.calls
    assert (name, path, interface, method) == (
        "org.freedesktop.PolicyKit1", "/org/freedesktop/PolicyKit1/Authority",
        "org.freedesktop.PolicyKit1.Authority", "CheckAuthorization")
    assert params.unpack() == (("system-bus-name", {"name": ":1.25"}),
                               authz.ACTION_CONTROL_FANS, {}, 1, "")
    assert reply_type.dup_string() == "((bba{ss}))"
    assert flags == Gio.DBusCallFlags.NONE
    assert timeout_ms == 5000
    assert cancellable is None
    bus.complete()
    assert outcomes == [True]


def test_async_denial_and_challenge_are_not_grants():
    bus, outcomes = FakeBus(False), []
    bus.reply = GLib.Variant("((bba{ss}))", ((False, True, {}),))
    authz.Authorizer(bus).check_async(":1.25", authz.ACTION_CONTROL_CPU, outcomes.append)
    bus.complete()
    assert outcomes == [False]


@pytest.mark.parametrize("code", [Gio.IOErrorEnum.FAILED, Gio.IOErrorEnum.TIMED_OUT,
                                 Gio.IOErrorEnum.CANCELLED])
def test_async_error_or_timeout_denies_once_even_if_a_late_reply_arrives(code):
    bus, outcomes = FakeBus(), []
    bus.error = _io_error(code)
    authz.Authorizer(bus).check_async(":1.25", authz.ACTION_CONTROL_FANS, outcomes.append)
    bus.complete()
    bus.error = None
    bus.complete()
    assert outcomes == [False]


def test_async_dispatch_error_is_a_denial():
    bus, outcomes = FakeBus(), []
    bus.start_error = _io_error(Gio.IOErrorEnum.CLOSED)
    authz.Authorizer(bus).check_async(":1.25", authz.ACTION_CONTROL_FANS, outcomes.append)
    assert outcomes == [False]


class MalformedReply:
    def __init__(self, value):
        self.value = value

    def get_type_string(self):
        return "((bba{ss}))"

    def unpack(self):
        return self.value


@pytest.mark.parametrize("reply", [
    None, GLib.Variant("(b)", (True,)), MalformedReply(()),
    MalformedReply(((1, False, {}),)),
    MalformedReply((("yes", False, {}),)),
    MalformedReply(((True, "no", {}),)),
    MalformedReply(((True, False, []),)),
    MalformedReply(((True, False, {"key": 1}),)),
    MalformedReply(((True, False, {}), (True, False, {}))),
])
def test_async_malformed_reply_fails_closed(reply):
    bus, outcomes = FakeBus(), []
    bus.reply = reply
    authz.Authorizer(bus).check_async(":1.25", authz.ACTION_CONTROL_FANS, outcomes.append)
    bus.complete()
    assert outcomes == [False]


def test_missing_connection_is_acquired_asynchronously(monkeypatch):
    pending, bus, outcomes = [], FakeBus(), []

    def bus_get(bus_type, cancellable, callback, user_data):
        assert bus_type == Gio.BusType.SYSTEM and cancellable is None
        pending.append((callback, user_data))

    monkeypatch.setattr(Gio, "bus_get", bus_get)
    monkeypatch.setattr(Gio, "bus_get_finish", lambda _result: bus)
    authorizer = authz.Authorizer()
    authorizer.check_async(":1.25", authz.ACTION_CONTROL_FANS, outcomes.append)
    assert outcomes == [] and not bus.calls
    callback, user_data = pending.pop()
    callback(None, object(), user_data)
    assert outcomes == []
    bus.complete()
    assert outcomes == [True]
    # The cached bus is reused; no second connection request.
    authorizer.check_async(":1.25", authz.ACTION_CONTROL_FANS, outcomes.append)
    assert not pending
    bus.complete()
    assert outcomes == [True, True]


def test_bus_acquisition_failure_denies(monkeypatch):
    outcomes = []

    def failed(_result):
        raise _io_error(Gio.IOErrorEnum.FAILED)

    monkeypatch.setattr(Gio, "bus_get_finish", failed)
    monkeypatch.setattr(Gio, "bus_get", lambda _type, _cancel, cb, data: cb(None, None, data))
    authz.Authorizer().check_async(":1.25", authz.ACTION_CONTROL_FANS, outcomes.append)
    assert outcomes == [False]


@pytest.mark.parametrize("sender,action", [("org.example.App", authz.ACTION_CONTROL_FANS),
                                         ("", authz.ACTION_CONTROL_FANS),
                                         (":", authz.ACTION_CONTROL_FANS), (":1.25", "")])
def test_invalid_subject_or_action_is_not_sent(sender, action):
    bus, outcomes = FakeBus(), []
    authz.Authorizer(bus).check_async(sender, action, outcomes.append)
    assert outcomes == [False]
    assert not bus.calls


@pytest.mark.parametrize("allowed", [True, False])
def test_compatibility_check_has_the_same_bounded_call(allowed):
    bus = FakeBus(allowed)
    assert authz.Authorizer(bus).check(":1.25", authz.ACTION_CONTROL_FANS) is allowed
    assert bus.calls[0][7] == 5000


def test_compatibility_check_denies_errors_and_malformed_replies():
    bus = FakeBus()
    bus.error = _io_error(Gio.IOErrorEnum.TIMED_OUT)
    assert not authz.Authorizer(bus).check(":1.25", authz.ACTION_CONTROL_FANS)
    bus.error, bus.reply = None, None
    assert not authz.Authorizer(bus).check(":1.25", authz.ACTION_CONTROL_FANS)


def test_compatibility_check_without_a_bus_denies_instead_of_connecting_synchronously():
    assert not authz.Authorizer().check(":1.25", authz.ACTION_CONTROL_FANS)
