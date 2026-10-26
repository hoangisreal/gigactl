"""polkit authorization, called straight over the system bus (no polkit GIR
dependency — just python3-gi).

The daemon runs as root; it must *not* trust callers blindly. For each
control request it asks polkit whether the calling bus peer is authorized for
the action, and polkit applies the policy in the shipped ``.policy`` file
(``allow_active=yes``; ``allow_inactive`` and ``allow_any`` both require
``auth_admin``).
"""
from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

ACTION_CONTROL_FANS = "io.github.smairio.gigactl.control-fans"
ACTION_CONTROL_KEYBOARD = "io.github.smairio.gigactl.control-keyboard"
ACTION_CONTROL_CPU = "io.github.smairio.gigactl.control-cpu"

_PK_NAME = "org.freedesktop.PolicyKit1"
_PK_PATH = "/org/freedesktop/PolicyKit1/Authority"
_PK_IFACE = "org.freedesktop.PolicyKit1.Authority"
_ALLOW_INTERACTION = 1  # let a remote admin authenticate; active users aren't prompted
_TIMEOUT_MS = 5000
_REPLY_TYPE = "((bba{ss}))"


class Authorizer:
    """Checks caller authorization via polkit. Fails closed."""

    def __init__(self, bus: Gio.DBusConnection | None = None) -> None:
        self._bus = bus

    @staticmethod
    def _parameters(sender: str, action_id: str) -> GLib.Variant:
        # Never accept a well-known name: it could change owners while polkit
        # is checking it. Gio supplies the caller's unique name to the service.
        if not isinstance(sender, str) or not sender.startswith(":") or len(sender) < 2:
            raise ValueError("expected a unique system-bus sender")
        if not isinstance(action_id, str) or not action_id:
            raise ValueError("expected a polkit action ID")
        # Build the whole argument tuple in one Variant. The subject is a plain
        # Python tuple ("(sa{sv})"); only the a{sv} *values* are wrapped Variants.
        subject = ("system-bus-name", {"name": GLib.Variant("s", sender)})
        return GLib.Variant(
            "((sa{sv})sa{ss}us)",
            (subject, action_id, {}, _ALLOW_INTERACTION, ""),
        )

    @staticmethod
    def _allowed(reply: GLib.Variant) -> bool:
        """Only a well-formed, explicitly authorized polkit reply grants access."""
        if reply.get_type_string() != _REPLY_TYPE:
            raise ValueError("unexpected polkit reply type")
        ((authorized, challenge, details),) = reply.unpack()
        if (type(authorized) is not bool or type(challenge) is not bool
                or not isinstance(details, dict)
                or not all(isinstance(k, str) and isinstance(v, str)
                           for k, v in details.items())):
            raise ValueError("malformed polkit reply")
        return authorized

    @staticmethod
    def _failed(exc: Exception) -> None:
        print(f"gigactld: polkit check failed, denying: {exc}", flush=True)

    def check_async(self, sender: str, action_id: str,
                    callback: Callable[[bool], None]) -> None:
        """Check without blocking the GLib control loop; call back exactly once.

        Both bus acquisition and CheckAuthorization use Gio's async API. The
        latter has a five-second deadline, including interactive authentication.
        Errors, timeouts and malformed replies deny access. No worker thread or
        nested main loop is used, so a stalled polkit cannot hide a stalled EC
        control loop from systemd's watchdog.
        """
        completed = False

        def finish(allowed: bool) -> None:
            nonlocal completed
            if not completed:
                completed = True
                callback(allowed)

        def checked(connection, result, *_user_data) -> None:
            try:
                allowed = self._allowed(connection.call_finish(result))
            except Exception as exc:
                self._failed(exc)
                allowed = False
            finish(allowed)

        def dispatch(connection) -> None:
            try:
                connection.call(
                    _PK_NAME, _PK_PATH, _PK_IFACE, "CheckAuthorization",
                    params, GLib.VariantType(_REPLY_TYPE),
                    Gio.DBusCallFlags.NONE, _TIMEOUT_MS, None, checked, None,
                )
            except Exception as exc:
                self._failed(exc)
                finish(False)

        def connected(_source, result, *_user_data) -> None:
            try:
                connection = Gio.bus_get_finish(result)
            except Exception as exc:
                self._failed(exc)
                finish(False)
                return
            self._bus = connection
            dispatch(connection)

        try:
            params = self._parameters(sender, action_id)
        except Exception as exc:
            self._failed(exc)
            finish(False)
            return
        if self._bus is not None:
            dispatch(self._bus)
        else:
            try:
                Gio.bus_get(Gio.BusType.SYSTEM, None, connected, None)
            except Exception as exc:
                self._failed(exc)
                finish(False)

    def check(self, sender: str, action_id: str) -> bool:
        """Bounded compatibility API on an existing bus, never for dispatch.

        Synchronous bus acquisition has no caller-supplied timeout. Without an
        existing connection, deny instead of risking an unbounded bus_get_sync.
        The normal service path must always use ``check_async``.
        """
        try:
            params = self._parameters(sender, action_id)
            if self._bus is None:
                raise ValueError("synchronous authorization needs an existing bus; use check_async")
            reply = self._bus.call_sync(
                _PK_NAME, _PK_PATH, _PK_IFACE, "CheckAuthorization",
                params, GLib.VariantType(_REPLY_TYPE),
                Gio.DBusCallFlags.NONE, _TIMEOUT_MS, None,
            )
            return self._allowed(reply)
        except Exception as exc:
            self._failed(exc)
            return False
