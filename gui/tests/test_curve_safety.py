"""Exercise actual page/window handlers with fake timers, widgets and bus calls.

No GTK objects, windows, main loop, bus connection, clients or hardware are
started. The lightweight harnesses bind production handlers to recording fakes.
"""
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from gi.repository import Gio, GLib

from gigactl_gui import curves, tray_menu
from gigactl_gui.app import GigactlApp
from gigactl_gui.client import DaemonClient, Telemetry
from gigactl_gui.curve_page import CurvePage
from gigactl_gui.curve_view import CurveView
from gigactl_gui.model import Support
from gigactl_gui.window import OverviewWindow


class Timers:
    def __init__(self):
        self.callbacks = {}
        self.pending = set()

    def timeout_add(self, _delay, callback, *args):
        source = len(self.callbacks) + 1
        self.callbacks[source] = (callback, args)
        self.pending.add(source)
        return source

    def source_remove(self, source):
        self.pending.discard(source)
        return True

    def fire(self, source, *, stale=False):
        assert stale or source in self.pending
        self.pending.discard(source)
        callback, args = self.callbacks[source]
        assert callback(*args) == GLib.SOURCE_REMOVE


@pytest.fixture
def timers(monkeypatch):
    fake = Timers()
    monkeypatch.setattr(GLib, "timeout_add", fake.timeout_add)
    monkeypatch.setattr(GLib, "source_remove", fake.source_remove)
    return fake


@dataclass
class Request:
    method: str
    params: object
    cancellable: object
    callback: object
    data: object


class Proxy:
    def __init__(self):
        self.owner = ":fake.1"
        self.props = {}
        self.calls = []

    def get_name_owner(self):
        return self.owner

    def get_cached_property(self, name):
        return self.props.get(name)

    def call(self, method, params, flags, _timeout, cancellable, callback, data):
        assert flags == Gio.DBusCallFlags.NO_AUTO_START
        self.calls.append(Request(method, params, cancellable, callback, data))

    def call_finish(self, result):
        if isinstance(result, Exception):
            raise result

    def finish(self, index, error=None):
        request = self.calls[index]
        request.callback(self, error, request.data)

    @property
    def methods(self):
        return [call.method for call in self.calls]


def client_proxy():
    errors = []
    client = DaemonClient(on_error=errors.append)
    proxy = Proxy()
    client._proxy = proxy
    client._notify_availability()
    return client, proxy, errors


class Widget:
    def __init__(self):
        self.active = True
        self.text = ""
        self.sensitive = True

    def get_active(self):
        return self.active

    def set_active(self, active):
        self.active = active

    def set_sensitive(self, sensitive):
        self.sensitive = sensitive

    def set_visible(self, visible):
        self.visible = visible

    def set_text(self, text):
        self.text = text

    set_markup = set_text

    def add_css_class(self, _name):
        pass

    remove_css_class = add_css_class


class View(Widget):
    cancel_editing = CurveView.cancel_editing
    _on_drag_update = CurveView._on_drag_update

    def __init__(self):
        super().__init__()
        self._dragging = None
        self._drag_origin = None

    def set_points(self, points):
        self.points = list(points)

    def set_ghost(self, points):
        self.ghost = points

    def set_now(self, *temps):
        self.now = temps


class Page:
    # Reuse production handlers without constructing a NavigationPage/display.
    _queue_send = CurvePage._queue_send
    _send_now = CurvePage._send_now
    cancel_pending = CurvePage.cancel_pending
    set_active = CurvePage.set_active
    set_controls_sensitive = CurvePage.set_controls_sensitive
    _on_curve_edited = CurvePage._on_curve_edited
    _on_firmware_clicked = CurvePage._on_firmware_clicked
    _on_link_toggled = CurvePage._on_link_toggled
    _on_fan_toggled = CurvePage._on_fan_toggled
    sync_from_daemon = CurvePage.sync_from_daemon
    on_telemetry = CurvePage.on_telemetry
    _refresh_view = CurvePage._refresh_view
    _other = CurvePage._other
    _push_now = CurvePage._push_now
    _update_annotation = CurvePage._update_annotation

    def __init__(self, client):
        self._client = client
        self._active = self._usable = False
        self._linked = True
        self._syncing = self._dirty = self._daemon_has_curve = False
        self._editing = "cpu"
        self._curves = {fan: list(curves.DEFAULT_SEED) for fan in ("cpu", "gpu")}
        self._temps = {fan: None for fan in ("cpu", "gpu")}
        self._send_source = self._send_generation = 0
        self._pending = None
        self._controls = []
        self._view = View()
        self._annotation = Widget()
        self._fan_switcher = Widget()
        self._link_buttons = {True: Widget(), False: Widget()}


class Signals:
    def __init__(self):
        self.handlers = {}

    def connect(self, signal, callback):
        self.handlers[signal] = callback

    def emit(self, signal):
        self.handlers[signal](self, None)


class Navigation(Signals):
    def __init__(self, page):
        super().__init__()
        self.curve_page = page
        self.overview = object()
        self.visible_page = self.overview

    def get_visible_page(self):
        return self.visible_page

    def push_by_tag(self, tag):
        assert tag == "curve"
        self.visible_page = self.curve_page
        self.emit("notify::visible-page")

    def back(self):
        self.visible_page = self.overview
        self.emit("notify::visible-page")


class Window(Signals):
    _wire_curve_lifetime = OverviewWindow._wire_curve_lifetime
    _on_curve_visibility_changed = OverviewWindow._on_curve_visibility_changed
    _on_close = OverviewWindow._on_close
    cancel_curve_edits = OverviewWindow.cancel_curve_edits
    set_fan_profile = OverviewWindow.set_fan_profile
    _on_profile_toggled = OverviewWindow._on_profile_toggled
    _on_custom_clicked = OverviewWindow._on_custom_clicked
    _on_availability = OverviewWindow._on_availability
    _update_sensitivity = OverviewWindow._update_sensitivity
    _sync_from_daemon = OverviewWindow._sync_from_daemon

    def __init__(self, client, page):
        super().__init__()
        self._client = client
        self._curve_page = page
        self._nav = Navigation(page)
        self._visible = True
        self._syncing = False
        self._last_profile = "custom"
        self._brightness_source = self._resync_source = 0
        self._pending_brightness = None
        self._controls = []
        self._model = SimpleNamespace(support=Support.VERIFIED)
        self._now_label = Widget()
        self._custom_button = Widget()
        self._profile_buttons = {name: Widget() for name in ("quiet", "balanced", "firmware")}
        self._wire_curve_lifetime()
        self._update_sensitivity()

    def get_visible(self):
        return self._visible

    def hide(self):
        self._visible = False
        self.emit("notify::visible")

    def show(self):
        self._visible = True
        self.emit("notify::visible")

    def _show_waiting(self):
        pass

    def _update_banner(self):
        pass


def editor():
    client, proxy, errors = client_proxy()
    page = Page(client)
    window = Window(client, page)
    window._on_custom_clicked(None)
    assert page._active and page._usable
    return client, proxy, page, window, errors


def test_debounce_captures_points_and_stale_timer_cannot_send_or_clear_newer_edit(timers):
    _client, proxy, page, _window, _errors = editor()
    first = list(curves.DEFAULT_SEED)
    page._on_curve_edited(first)
    old_timer = page._send_source
    second = [(40, 35), (55, 45), (70, 65), (85, 85), (95, 100)]
    expected = list(second)
    page._on_curve_edited(second)
    new_timer = page._send_source
    second[0] = (40, 99)
    timers.fire(old_timer, stale=True)
    assert proxy.calls == [] and page._send_source == new_timer
    timers.fire(new_timer)
    assert proxy.methods == ["SetCurve"]
    assert proxy.calls[0].params.unpack() == ("cpu", expected, True)


def test_firmware_escape_cancels_timer_queued_curve_and_ongoing_drag(timers):
    _client, proxy, page, _window, _errors = editor()
    page._on_curve_edited(list(curves.DEFAULT_SEED))
    old_timer = page._send_source
    page._view._dragging = 1
    page._view._drag_origin = (20, 20)
    page._on_firmware_clicked(None)
    timers.fire(old_timer, stale=True)
    page._view._on_drag_update(None, 10, -10)
    page._on_link_toggled(Widget(), False)
    assert proxy.methods == ["RestoreFirmware"]
    assert page._pending is None and not page._dirty
    assert not timers.pending


@pytest.mark.parametrize("exit_path", ["back", "close", "hide", "disconnect"])
def test_lifecycle_wiring_cancels_pending_and_rejects_late_edits(timers, exit_path):
    client, proxy, page, window, _errors = editor()
    page._on_curve_edited(list(curves.DEFAULT_SEED))
    old_timer = page._send_source
    if exit_path == "back":
        window._nav.back()
    elif exit_path == "close":
        assert window._on_close(None) is False
    elif exit_path == "hide":
        window.hide()
    else:
        proxy.owner = None
        client._on_availability = window._on_availability
        client._notify_availability()
    timers.fire(old_timer, stale=True)
    page._on_curve_edited(list(curves.DEFAULT_SEED))
    assert proxy.calls == [] and not timers.pending


def test_owner_replacement_cancels_page_debounce_without_a_vanished_notification(timers):
    client, proxy, page, window, _errors = editor()
    page._on_curve_edited(list(curves.DEFAULT_SEED))
    old_timer = page._send_source
    client._on_availability = window._on_availability
    proxy.owner = ":fake.2"
    client._notify_availability()
    timers.fire(old_timer, stale=True)
    assert proxy.calls == [] and not timers.pending


@pytest.mark.parametrize("origin", ["overview", "tray", "external"])
def test_preset_callers_cancel_debounce_before_new_profile(timers, origin):
    _client, proxy, page, window, _errors = editor()
    page._on_curve_edited(list(curves.DEFAULT_SEED))
    old_timer = page._send_source
    if origin == "overview":
        window._on_profile_toggled(Widget(), "balanced")
    elif origin == "tray":
        tray_client, tray_proxy, _ = client_proxy()
        app = SimpleNamespace(_tray_client=tray_client, _window=window)
        GigactlApp._on_tray_action(app, "profile:firmware")
        assert tray_proxy.calls == [], "tray must use the same serialized fan channel"
    else:
        proxy.props["ActiveProfile"] = GLib.Variant("s", "firmware")
        window._sync_from_daemon()
    timers.fire(old_timer, stale=True)
    assert "SetCurve" not in proxy.methods and not timers.pending
    assert not page._dirty


def test_tray_quit_cancels_window_edits_before_quitting(timers):
    client, proxy, page, window, _errors = editor()
    page._on_curve_edited(list(curves.DEFAULT_SEED))
    old_timer = page._send_source
    seen = []
    app = SimpleNamespace(_tray_client=client, _window=window, quit=lambda: seen.append("quit"))
    GigactlApp._on_tray_action(app, tray_menu.ACTION_QUIT)
    timers.fire(old_timer, stale=True)
    assert seen == ["quit"] and proxy.calls == []


def test_reopening_editor_allows_only_new_explicit_edits(timers):
    _client, proxy, page, window, _errors = editor()
    page._on_curve_edited(list(curves.DEFAULT_SEED))
    old_timer = page._send_source
    window._nav.back()
    window._on_custom_clicked(None)
    timers.fire(old_timer, stale=True)
    assert proxy.calls == []
    page._on_curve_edited(list(curves.DEFAULT_SEED))
    timers.fire(page._send_source)
    assert proxy.methods == ["SetCurve"]


def test_escape_drops_superseded_requests_and_preempts_dispatched_curve_immediately(timers):
    client, proxy, page, _window, errors = editor()
    page._on_curve_edited(list(curves.DEFAULT_SEED))
    timers.fire(page._send_source)
    client.set_curve("cpu", [(40, 20)], True)  # queued behind the dispatched call
    page._on_curve_edited(list(curves.DEFAULT_SEED))
    last_timer = page._send_source
    page._on_firmware_clicked(None)
    assert proxy.methods == ["SetCurve", "RestoreFirmware"]
    assert proxy.calls[0].cancellable.is_cancelled()
    proxy.finish(0, GLib.Error("superseded curve failure"))
    timers.fire(last_timer, stale=True)
    assert proxy.methods == ["SetCurve", "RestoreFirmware"]
    assert errors == []
    proxy.finish(1, GLib.Error("firmware restoration failed"))
    assert errors, "a real firmware failure must still reach the user"


def test_newest_preset_preempts_older_fan_requests_and_stale_errors_are_ignored():
    client, proxy, errors = client_proxy()
    client.set_curve("cpu", [(40, 10)], True)
    client.set_curve("cpu", [(40, 20)], True)
    client.set_profile("quiet")
    client.set_profile("firmware")
    assert proxy.methods == ["SetCurve", "SetProfile", "SetProfile"]
    assert proxy.calls[0].cancellable.is_cancelled()
    assert proxy.calls[1].cancellable.is_cancelled()
    proxy.finish(1, GLib.Error("superseded preset"))
    proxy.finish(0, GLib.Error("superseded curve"))
    assert errors == []
    proxy.finish(2)
    assert not client._fan_pending and client._fan_inflight is None


def test_client_coalesces_superseded_curves_but_preserves_independent_split_fan_edits():
    client, proxy, _errors = client_proxy()
    client.set_curve("cpu", [(40, 10)], False)
    client.set_curve("cpu", [(40, 20)], False)
    client.set_curve("gpu", [(40, 30)], False)
    client.set_curve("cpu", [(40, 40)], False)
    assert proxy.methods == ["SetCurve"]
    proxy.finish(0)
    assert proxy.calls[1].params.unpack() == ("gpu", [(40, 30)], False)
    proxy.finish(1)
    assert proxy.calls[2].params.unpack() == ("cpu", [(40, 40)], False)


@pytest.mark.parametrize("action", ["back", "close", "hide", "disconnect", "replacement", "stop"])
def test_queued_client_curve_never_sends_after_lifecycle_cancel(timers, action):
    client, proxy, page, window, errors = editor()
    client.set_curve("cpu", [(40, 10)], True)
    client.set_curve("cpu", [(40, 20)], True)
    if action == "back":
        window._nav.back()
    elif action == "close":
        window._on_close(None)
    elif action == "hide":
        window.hide()
    elif action == "stop":
        client.stop()
    else:
        proxy.owner = None if action == "disconnect" else ":fake.2"
        client._notify_availability()
    proxy.finish(0, GLib.Error("obsolete completion"))
    assert proxy.methods == ["SetCurve"] and errors == []
    if action not in ("back", "close", "hide"):
        assert proxy.calls[0].cancellable.is_cancelled()


def test_both_sensor_telemetry_reaches_marker_and_emergency_annotation():
    _client, _proxy, page, _window, _errors = editor()
    page._curves["cpu"] = [(40, 0), (55, 0), (70, 0), (85, 0), (95, 0)]
    page.on_telemetry(Telemetry(60, 87, 1000, 1000, 30, 30))
    assert page._view.now == (60, 60, 87)
    assert "100%" in page._annotation.text
