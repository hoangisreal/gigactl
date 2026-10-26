"""D-Bus system-bus service.

The daemon owns its well-known name, broadcasts a ``Telemetry`` signal every
poll interval, runs the automatic fan-curve engine on that same tick, and
exposes fan-control methods (all gated by polkit).
"""
from __future__ import annotations

import copy
import signal
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from . import __version__  # noqa: E402
from . import authz, curve, ec as ec_mod, fans, keyboard, safety, state, turbo  # noqa: E402
from .fan_control import FanControl  # noqa: E402
from .telemetry import InvalidTelemetry, read_telemetry  # noqa: E402

_ERR = "io.github.smairio.gigactl.Error"

BUS_NAME = "io.github.smairio.gigactl"
OBJECT_PATH = "/io/github/smairio/gigactl"
INTERFACE = "io.github.smairio.gigactl.Control"

INTROSPECTION_XML = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="SetFanDuty">
      <arg name="fan"     type="u" direction="in"/>
      <arg name="percent" type="u" direction="in"/>
    </method>
    <method name="RestoreFirmware"/>
    <method name="SetProfile">
      <arg name="name" type="s" direction="in"/>
    </method>
    <method name="SetCurve">
      <arg name="which"  type="s"     direction="in"/>
      <arg name="points" type="a(uu)" direction="in"/>
      <arg name="linked" type="b"     direction="in"/>
    </method>
    <method name="SetKeyboardColor">
      <arg name="r" type="u" direction="in"/>
      <arg name="g" type="u" direction="in"/>
      <arg name="b" type="u" direction="in"/>
    </method>
    <method name="SetKeyboardBrightness">
      <arg name="percent" type="u" direction="in"/>
    </method>
    <method name="SetKeyboardEnabled">
      <arg name="on" type="b" direction="in"/>
    </method>
    <method name="SetTurboBoost">
      <arg name="enabled" type="b" direction="in"/>
    </method>
    <signal name="Telemetry">
      <arg name="cpu_temp"  type="u"/>
      <arg name="gpu_temp"  type="u"/>
      <arg name="fan1_rpm"      type="u"/>
      <arg name="fan2_rpm"      type="u"/>
      <arg name="fan1_duty_pct" type="u"/>
      <arg name="fan2_duty_pct" type="u"/>
    </signal>
    <property name="DaemonVersion" type="s" access="read"/>
    <!-- Empty when the Embedded Controller is doing what it is told. Otherwise
         one plain sentence naming what is wrong and what to do about it, so a
         client can show it without having to attempt a write to find out. A
         machine whose EC refuses writes still reports temperatures perfectly
         (#25), and nothing else on this interface reveals the difference. -->
    <property name="EcStatus" type="s" access="read"/>
    <property name="ActiveProfile" type="s" access="read"/>
    <!-- The same six numbers as the Telemetry signal, read on demand. The signal
         is for anything that stays running; this is for one-shot readers such as
         `gfan status`, which cannot wait out a poll interval. Deliberately never
         announced via PropertiesChanged — a client that wants a stream wants the
         signal, not a property notification every two seconds. -->
    <property name="Telemetry" type="(uuuuuu)" access="read"/>
    <!-- (enabled, r, g, b, brightness_pct) — what the GUI shows as selected -->
    <property name="KeyboardState" type="(buuuu)" access="read"/>
    <!-- (linked, cpu_points, gpu_points) — the shape the curve editor loads.
         Empty point lists mean the engine is driving nothing (firmware/manual). -->
    <property name="Curves" type="(ba(uu)a(uu))" access="read"/>
    <!-- Live intel_pstate state: TurboBoost reads the sysfs knob every
         time (tuned/PowerDevil can move it behind us); TurboAvailable is
         False where the knob does not exist at all. -->
    <property name="TurboBoost" type="b" access="read"/>
    <property name="TurboAvailable" type="b" access="read"/>
    <property name="FanControlState" type="s" access="read"/>
    <property name="TelemetryStatus" type="s" access="read"/>
  </interface>
</node>
"""


def _keyboard_variant(kbd: keyboard.KeyboardState | None) -> GLib.Variant:
    """Wire shape for the ``KeyboardState`` property. The D-Bus adapter lives
    here rather than on ``KeyboardState`` so the hardware module stays free of
    GLib; when nothing has been set yet we report the firmware default the EC
    itself boots with, which is honest rather than a guess."""
    k = kbd or keyboard.KeyboardState()
    return GLib.Variant("(buuuu)", (k.enabled, k.r, k.g, k.b, k.brightness_pct))


def _curves_variant(engine: curve.ProfileEngine) -> GLib.Variant:
    """Wire shape for the ``Curves`` property: the curves the engine is actually
    driving, so opening the editor on a named profile shows that profile's shape
    rather than an invented one. Firmware/manual report empty lists, because in
    those states the engine drives nothing."""
    cpu = [tuple(p) for p in engine.curve_for(curve.CPU_FAN)]
    gpu = [tuple(p) for p in engine.curve_for(curve.GPU_FAN)]
    return GLib.Variant("(ba(uu)a(uu))", (engine.linked, cpu, gpu))


class Daemon:
    # Ticks are 2s apart; 3 consecutive failures (~6s) with no successful
    # drive means the fans may be stranded at a manual duty — trip the
    # failsafe and hand them back to firmware (docs/PLAN.md T2.1).
    FAILSAFE_AFTER_TICKS = 3

    MAX_SAMPLE_AGE_S = 6.0

    def __init__(self, ec, interval_s: int = 2,
                 authorizer: authz.Authorizer | None = None,
                 clock=time.monotonic) -> None:
        if not 1 <= interval_s <= 3:
            raise ValueError("poll interval must be between 1 and 3 seconds")
        self.ec = ec
        self.clock = clock
        self.control = FanControl(ec, clock=clock)
        self.interval_s = interval_s
        self.authorizer = authorizer or authz.Authorizer()
        self._conn: Gio.DBusConnection | None = None
        self._node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        self._loop = GLib.MainLoop()
        self.engine = curve.ProfileEngine(hysteresis=curve.HYSTERESIS_C)
        # Lazily created on first keyboard set (see _remember_keyboard): while
        # it's None the daemon writes no keyboard section and re-applies nothing,
        # so a fan-only user's backlight is never touched.
        self.keyboard: keyboard.KeyboardState | None = None
        self.turbo = turbo.TurboControl()
        # The turbo choice the user made through us (None = never): only a
        # made choice is persisted and re-applied at boot, so a machine
        # managed by tuned/systemd alone is never fought by the daemon.
        self._turbo_choice: bool | None = None
        self.state_path = state.DEFAULT_PATH
        # Why the engine's last attempt to drive the fans came to nothing, and
        # the status last announced — kept so a condition repeating every two
        # seconds is reported once rather than every tick.
        self._engine_fault: str | None = None
        self._last_ec_status = ""
        self._tick_failures = 0
        self._failsafe_tripped = False
        self._recovery_reason = ""
        self._manual_targets: dict[int, int] = {}
        self._durable_fan_state: dict | None = None
        self._owns_name = False
        self._started = False
        self._suspended = False
        self._suspend_profile: dict | None = None
        self._fan_generation = 0
        self._telemetry = None
        self._sampled_at: float | None = None
        self._watchdog = None
        self._last_control_state = ""
        self._last_telemetry_status = ""
        self._last_turbo = None

    # --- lifecycle -----------------------------------------------------------
    def run(self) -> None:
        from .watchdog import Watchdog

        self._watchdog = Watchdog.from_environment()
        Gio.bus_own_name(
            Gio.BusType.SYSTEM,
            BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired,
            self._on_name_acquired,
            self._on_name_lost,
        )
        # GLib-level signal handling so systemd's SIGTERM (and Ctrl-C) quit the
        # loop promptly — Python's own signal handlers can stall under GLib.
        for sig in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self._on_signal)
        self._loop.run()

    def _on_signal(self) -> bool:
        # Clean-stop failsafe: hand the fans back to firmware so a stop never
        # leaves them frozen. (Crash is covered by `gigactld --restore-firmware`
        # in the unit's ExecStopPost.)
        print("gigactld: shutting down; handing fans back to firmware", flush=True)
        if self._owns_name:
            self._owns_name = False
            self._fan_generation += 1
            self.control.restore_all()
            print(f"gigactld: {self.control.handback_status()}", flush=True)
        self._loop.quit()
        return GLib.SOURCE_REMOVE

    def _on_bus_acquired(self, conn: Gio.DBusConnection, name: str) -> None:
        self._conn = conn
        # Acquiring a connection is NOT acquiring the well-known name. Do not
        # restore state or touch hardware until _on_name_acquired.
        try:
            conn.register_object(
                OBJECT_PATH,
                self._node.interfaces[0],
                self._method_call,   # method_call
                self._get_property,  # get_property
                None,                # set_property
            )
        except Exception as exc:  # pragma: no cover - environment dependent
            print(f"gigactld: object registration failed ({exc})", flush=True)
            self._loop.quit()

    def _restore_saved_state(self) -> None:
        data = state.load(self.state_path)
        if not data:
            return
        try:
            state.validate(data)
            state.restore(self.engine, data)
            if data.get("recovery_latched", False):
                self.engine.set_profile(curve.FIRMWARE)
                self._failsafe_tripped = True
                self._recovery_reason = "saved safety latch; explicit profile selection required"
                self._engine_fault = self._recovery_reason
                # A previous process may have died before handback completed.
                # Never infer physical firmware ownership from the JSON latch.
                self.control.pending_auto.update(fans.FANS)
            self._durable_fan_state = state.snapshot(self.engine)
            print(f"gigactld: restored profile '{self.engine.profile}'", flush=True)
        except Exception as exc:
            self.engine.set_profile(curve.FIRMWARE)
            print(f"gigactld: could not restore saved profile: {exc}", flush=True)
            return  # validate the ENTIRE snapshot before any optional writes
        # Keyboard is optional: only re-apply if we actually saved one, so a
        # fresh install doesn't force the firmware default onto the backlight.
        kd = data.get("keyboard")
        if kd:
            self.keyboard = keyboard.KeyboardState.from_dict(kd)
            self._apply_keyboard("boot")
        # Turbo likewise: only re-apply a choice made through the daemon.
        if "turbo_enabled" in data:
            self._turbo_choice = data["turbo_enabled"]
            try:
                self.turbo.set_enabled(self._turbo_choice)
                print(f"gigactld: turbo {'enabled' if self._turbo_choice else 'disabled'} (restored)", flush=True)
            except Exception as exc:
                print(f"gigactld: could not restore turbo choice: {exc}", flush=True)

    def _apply_keyboard(self, why: str) -> None:
        """Re-drive the backlight to the remembered state, if the user has set
        one. Best-effort and self-contained — it runs from a signal callback, so
        an exception must not escape into the GLib loop."""
        if self.keyboard is None:
            return
        try:
            self.keyboard.apply(self.ec)
            print(f"gigactld: keyboard re-applied ({why})", flush=True)
        except Exception as exc:
            print(f"gigactld: keyboard apply failed ({why}): {exc}", flush=True)

    def _subscribe_sleep(self, conn: Gio.DBusConnection) -> None:
        """Listen for logind's PrepareForSleep so we can re-apply the backlight
        on resume (the EC drops it across suspend). Receiving a broadcast signal
        needs no extra D-Bus policy."""
        try:
            conn.signal_subscribe(
                "org.freedesktop.login1",           # sender
                "org.freedesktop.login1.Manager",   # interface
                "PrepareForSleep",                  # signal
                "/org/freedesktop/login1",          # path
                None,                               # arg0 filter
                Gio.DBusSignalFlags.NONE,
                self._on_prepare_for_sleep,
            )
        except Exception as exc:  # pragma: no cover - environment dependent
            print(f"gigactld: could not subscribe to logind sleep signal ({exc})", flush=True)

    def _on_prepare_for_sleep(self, conn, sender, path, iface, signal_name, params):
        if not self._owns_name:
            return
        (going_to_sleep,) = params.unpack()
        self._fan_generation += 1  # invalidate pending authorization callbacks
        if going_to_sleep:
            self._suspended = True
            self._suspend_profile = (state.snapshot(self.engine)
                                     if self.engine.profile != curve.MANUAL else None)
            self._manual_targets.clear()
            if self.engine.profile != curve.FIRMWARE or self.control.targets:
                self.engine.set_profile(curve.FIRMWARE)
                if not self.control.restore_all():
                    self._enter_recovery("suspend handback failed", restore=False)
            self._telemetry = None
            self._sampled_at = None
            return
        self._suspended = False
        self.control.targets.clear()
        self._telemetry = None
        self._sampled_at = None
        if not self._failsafe_tripped and self._suspend_profile:
            state.restore(self.engine, self._suspend_profile)
        self._suspend_profile = None
        self.engine.retry_next_tick()
        if self.engine.profile != curve.FIRMWARE and not self._failsafe_tripped:
            try:
                self._apply_now()  # fresh sample; cached pre-sleep targets are invalid
            except Exception as exc:
                self._enter_recovery(f"resume reconciliation failed: {exc}")
        self._apply_keyboard("resume")
        self._notify_fan_state()

    def _on_name_acquired(self, conn, name: str) -> None:
        if conn is not None:
            self._conn = conn
        self._owns_name = True
        if not self._started:
            self._started = True
            # Schedule safety before restoring potentially malformed state.
            GLib.timeout_add_seconds(self.interval_s, self._tick)
            if self._watchdog is not None and self._watchdog.interval_s:
                GLib.timeout_add_seconds(max(1, int(self._watchdog.interval_s)),
                                         self._heartbeat)
            self._subscribe_sleep(self._conn)
            self._restore_saved_state()
            self._tick()
        print(f"gigactld: owning {name} (EC backend: {self.ec.backend.name})", flush=True)
        # Say it here as well as on the property: a user asking "why is nothing
        # happening" reaches for systemctl status long before they think to
        # inspect a D-Bus property.
        status = self.ec_status()
        if status:
            print(f"gigactld: {status}", flush=True)

    def _on_name_lost(self, conn, name: str) -> None:
        print(f"gigactld: lost name {name}; is another instance running?", flush=True)
        if self._owns_name:
            self._owns_name = False
            self._fan_generation += 1
            self.control.restore_all()
        self._loop.quit()

    def _heartbeat(self) -> bool:
        # Deliberately runs on the SAME event loop as control/authorization.
        # A separate heartbeat thread would conceal a hung controller.
        if self._owns_name and self._watchdog is not None:
            self._watchdog.ping()
        return GLib.SOURCE_CONTINUE

    # --- D-Bus surface -------------------------------------------------------
    def _get_property(self, conn, sender, path, iface, prop):
        if prop == "DaemonVersion":
            return GLib.Variant("s", __version__)
        if prop == "ActiveProfile":
            return GLib.Variant("s", self.engine.profile)
        if prop == "EcStatus":
            return GLib.Variant("s", self.ec_status())
        if prop == "Telemetry":
            if not self._sample_fresh():
                return None
            t = self._telemetry
            return GLib.Variant("(uuuuuu)",
                                (t.cpu_temp, t.gpu_temp, t.fan1_rpm, t.fan2_rpm,
                                 t.fan1_duty_pct, t.fan2_duty_pct))
        if prop == "KeyboardState":
            return _keyboard_variant(self.keyboard)
        if prop == "Curves":
            return _curves_variant(self.engine)
        if prop == "TurboAvailable":
            return GLib.Variant("b", self.turbo.available())
        if prop == "TurboBoost":
            try:
                return GLib.Variant("b", self.turbo.enabled())
            except turbo.TurboUnavailable:
                return GLib.Variant("b", False)
        if prop == "FanControlState":
            return GLib.Variant("s", self.control_state())
        if prop == "TelemetryStatus":
            return GLib.Variant("s", self.telemetry_status())
        return None

    def ec_status(self) -> str:
        """Why the EC is not doing as it is told, or "" when it is.

        Asked fresh rather than remembered, so a machine that is fixed while the
        daemon runs stops complaining on its own.
        """
        # A read-only EC outranks a generic write failure: both are true at
        # once when the module lacks write support, and only one of them tells
        # the user what to do about it.
        return (self.ec.unwritable_reason() or self._engine_fault or
                ("fan target awaiting readback verification"
                 if self.control.pending_duties else ""))

    def control_state(self) -> str:
        if self._suspended:
            return "suspended"
        if self.control.pending_auto:
            return "recovery-unconfirmed"
        if self._failsafe_tripped:
            return "safety-latched"
        if self.control.pending_duties:
            return "verifying"
        return "firmware-requested" if self.engine.profile == curve.FIRMWARE else "software-controlled"

    def telemetry_status(self) -> str:
        if not self._sample_fresh():
            return "unavailable or stale"
        if not self._telemetry.gpu_available:
            return "CPU valid; GPU sensor unavailable or asleep"
        return "valid"

    def _sync_ec_status(self) -> None:
        """Log and announce the EC status, but only when it changes.

        A fault that persists is not news on every tick; a transition is. The
        announcement matters as much as the log — a window is often open while
        this happens, and should not need restarting to notice.
        """
        status = self.ec_status()
        changed = {}
        if status != self._last_ec_status:
            self._last_ec_status = status
            print(f"gigactld: {status}" if status
                  else "gigactld: the EC is accepting writes again", flush=True)
            changed["EcStatus"] = GLib.Variant("s", status)
        phase = self.control_state()
        if phase != self._last_control_state:
            self._last_control_state = phase
            changed["FanControlState"] = GLib.Variant("s", phase)
        telemetry_status = self.telemetry_status()
        if telemetry_status != self._last_telemetry_status:
            self._last_telemetry_status = telemetry_status
            changed["TelemetryStatus"] = GLib.Variant("s", telemetry_status)
        if changed:
            self._notify_properties(**changed)

    def _notify_properties(self, **changed: GLib.Variant) -> None:
        """Emit ``PropertiesChanged`` so a running GUI or tray reflects state it
        did not set itself — another client, or a manual duty override flipping
        the profile to 'manual'. (The boot restore needs no emit: it runs before
        the bus name is acquired, so nobody is listening yet.) Best-effort: a
        failed emit must never break the call that triggered it."""
        if self._conn is None:
            return
        try:
            self._conn.emit_signal(
                None, OBJECT_PATH,
                "org.freedesktop.DBus.Properties", "PropertiesChanged",
                GLib.Variant("(sa{sv}as)", (INTERFACE, changed, [])),
            )
        except Exception as exc:
            print(f"gigactld: could not emit PropertiesChanged: {exc}", flush=True)

    def _notify_fan_state(self) -> None:
        """Profile and curves always change together — one emission covers both."""
        self._notify_properties(
            ActiveProfile=GLib.Variant("s", self.engine.profile),
            Curves=_curves_variant(self.engine),
            FanControlState=GLib.Variant("s", self.control_state()))

    def _notify_keyboard(self) -> None:
        self._notify_properties(KeyboardState=_keyboard_variant(self.keyboard))

    def _method_call(self, conn, sender, path, iface, method, params, invocation):
        actions = {
            "SetFanDuty": authz.ACTION_CONTROL_FANS,
            "RestoreFirmware": authz.ACTION_CONTROL_FANS,
            "SetProfile": authz.ACTION_CONTROL_FANS,
            "SetCurve": authz.ACTION_CONTROL_FANS,
            "SetKeyboardColor": authz.ACTION_CONTROL_KEYBOARD,
            "SetKeyboardBrightness": authz.ACTION_CONTROL_KEYBOARD,
            "SetKeyboardEnabled": authz.ACTION_CONTROL_KEYBOARD,
            "SetTurboBoost": authz.ACTION_CONTROL_CPU,
        }
        if method not in actions:
            invocation.return_dbus_error(f"{_ERR}.UnknownMethod", method)
            return
        if not self._owns_name or self._suspended:
            invocation.return_dbus_error(f"{_ERR}.Unavailable", "controller not active")
            return
        fan_request = actions[method] == authz.ACTION_CONTROL_FANS
        if fan_request:
            self._fan_generation += 1
        generation = self._fan_generation

        def authorized(allowed):
            if not allowed:
                invocation.return_dbus_error(f"{_ERR}.NotAuthorized", "authorization refused")
            elif not self._owns_name or self._suspended:
                invocation.return_dbus_error(f"{_ERR}.Unavailable", "controller not active")
            elif fan_request and generation != self._fan_generation:
                invocation.return_dbus_error(f"{_ERR}.Superseded", "newer fan request superseded this one")
            else:
                self._dispatch(method, sender, params, invocation)

        try:
            if hasattr(self.authorizer, "check_async"):
                self.authorizer.check_async(sender, actions[method], authorized)
            else:
                # Narrow injection seam for synchronous in-memory authorizers.
                # Production Authorizer always takes the nonblocking path.
                authorized(self.authorizer.check(sender, actions[method]))
        except Exception as exc:
            invocation.return_dbus_error(f"{_ERR}.NotAuthorized", f"authorization failed: {exc}")

    def _dispatch(self, method, sender, params, invocation):
        """Private handlers run only after asynchronous boundary authorization."""
        try:
            if method == "SetFanDuty":
                self._set_fan_duty(sender, params, invocation)
            elif method == "RestoreFirmware":
                self._restore_firmware(sender, invocation)
            elif method == "SetProfile":
                self._set_profile(sender, params, invocation)
            elif method == "SetCurve":
                self._set_curve(sender, params, invocation)
            elif method == "SetKeyboardColor":
                self._set_keyboard_color(sender, params, invocation)
            elif method == "SetKeyboardBrightness":
                self._set_keyboard_brightness(sender, params, invocation)
            elif method == "SetKeyboardEnabled":
                self._set_keyboard_enabled(sender, params, invocation)
            elif method == "SetTurboBoost":
                self._set_turbo_boost(sender, params, invocation)
            else:
                invocation.return_dbus_error(f"{_ERR}.UnknownMethod", method)
        except ec_mod.EcNotWritable as exc:
            # A known cause with a known remedy — worth its own error name so a
            # client can recognise it, and its own message so the user is not
            # handed a bare errno as they were in #25.
            invocation.return_dbus_error(f"{_ERR}.EcNotWritable", str(exc))
        except Exception as exc:  # never let an exception escape into GLib
            invocation.return_dbus_error(f"{_ERR}.Failed", str(exc))

    def _set_fan_duty(self, sender, params, invocation) -> None:
        fan, percent = params.unpack()
        try:
            targets = fans.expand(fan)
        except ValueError as exc:
            invocation.return_dbus_error(f"{_ERR}.InvalidArgs", str(exc))
            return
        if not 0 <= percent <= 100:
            invocation.return_dbus_error(f"{_ERR}.InvalidArgs", f"bad percent {percent}")
            return
        try:
            t = self._sample()
            self._rearm()
            if self.engine.profile != curve.MANUAL:
                self._durable_fan_state = state.snapshot(self.engine)
                self._manual_targets.clear()
            self._manual_targets.update({f: percent for f in targets})
            self.engine.set_manual()
            self._run_engine(t.cpu_temp, t.gpu_temp)
        except Exception as exc:
            self._enter_recovery(f"manual fan request failed: {exc}")
            raise
        self._notify_fan_state()
        invocation.return_value(None)

    def _restore_firmware(self, sender, invocation) -> None:
        self._set_profile(sender, GLib.Variant("(s)", (curve.FIRMWARE,)), invocation)

    def _sample_fresh(self) -> bool:
        return (not self._suspended and self._telemetry is not None and
                self._sampled_at is not None and
                self.clock() - self._sampled_at < self.MAX_SAMPLE_AGE_S)

    def _sample(self):
        try:
            t = read_telemetry(self.ec)
            t.validate()
        except Exception:
            self._telemetry = None
            raise
        self._telemetry = t
        self._sampled_at = self.clock()
        return t

    # --- telemetry loop ------------------------------------------------------
    def _tick(self) -> bool:
        if not self._owns_name or self._suspended:
            return True
        if self._failsafe_tripped and self.control.pending_auto:
            self.control.retry_restore()
            self._engine_fault = f"{self._recovery_reason}; {self.control.handback_status()}"
        try:
            t = self._sample()
            if not self._failsafe_tripped:
                self._run_engine(t.cpu_temp, t.gpu_temp, quiet=True)
            self._tick_failures = 0
            try:
                self._conn.emit_signal(
                    None, OBJECT_PATH, INTERFACE, "Telemetry",
                    GLib.Variant("(uuuuuu)",
                                 (t.cpu_temp, t.gpu_temp, t.fan1_rpm, t.fan2_rpm,
                                  t.fan1_duty_pct, t.fan2_duty_pct)))
            except Exception as exc:
                # A presentation failure is not an EC failure.
                print(f"gigactld: telemetry delivery failed: {exc}", flush=True)
        except InvalidTelemetry as exc:
            self._enter_recovery(str(exc))
        except Exception as exc:
            print(f"gigactld: tick failed: {exc}", flush=True)
            self._note_tick_failure(str(exc))
        self._sync_turbo()
        self._sync_ec_status()
        return True  # keep the timeout running

    def _note_tick_failure(self, reason="telemetry failure") -> None:
        self._tick_failures += 1
        stale = (self._sampled_at is not None and
                 self.clock() - self._sampled_at >= self.MAX_SAMPLE_AGE_S)
        if self._failsafe_tripped:
            return
        if self._tick_failures >= self.FAILSAFE_AFTER_TICKS or stale:
            self._enter_recovery(f"repeated or stale telemetry: {reason}")

    def _enter_recovery(self, reason: str, *, restore=True) -> None:
        first_trip = not self._failsafe_tripped
        self._failsafe_tripped = True
        self._fan_generation += 1
        self._manual_targets.clear()
        self.engine.set_profile(curve.FIRMWARE)
        if restore and first_trip:
            self.control.restore_all()
        self._recovery_reason = f"failsafe: {reason}"
        self._engine_fault = f"{self._recovery_reason}; {self.control.handback_status()}"
        self._persist()
        self._notify_fan_state()

    def _rearm(self) -> None:
        if self.control.pending_auto and not self.control.retry_restore():
            raise ec_mod.EcError(self.control.handback_status())
        self._failsafe_tripped = False
        self._tick_failures = 0
        self._engine_fault = None
        self._recovery_reason = ""
        # Retain knowledge of already-controlled fans until they are explicitly
        # reconciled/released. Clearing it here could strand the other fan when
        # switching a two-fan curve to a one-fan manual request.

    def _run_engine(self, cpu_temp: int, gpu_temp: int, *,
                    quiet: bool = False) -> None:
        """Drive each fan the engine has an opinion about.

        ``quiet`` is for the periodic tick, which must not die on a failure it
        will meet again in two seconds — the fault is recorded and reported
        through ``EcStatus`` instead. A caller acting on a user's request wants
        the exception, so that the request can fail out loud rather than report
        success it did not achieve.
        """
        if self.engine.profile == curve.FIRMWARE:
            return
        profile = self.engine.profile
        try:
            if profile == curve.MANUAL:
                duties = {f: safety.safe_duty(p, cpu_temp, gpu_temp)
                          for f, p in self._manual_targets.items()}
                emergency = (cpu_temp >= safety.CPU_EMERGENCY_C or
                             gpu_temp >= safety.GPU_EMERGENCY_C)
                if emergency:
                    duties = {f: 100 for f in fans.FANS}
                else:
                    for f in set(self.control.targets) - set(duties):
                        self.control.release(f)
            else:
                duties = {}
                for fan in fans.FANS:
                    # A missing GPU sample cannot justify low cooling. Use the
                    # valid CPU sample rather than treating unavailable as 0C.
                    src = cpu_temp if fan == fans.CPU_FAN or not gpu_temp else gpu_temp
                    self.engine.decide(fan, src, cpu_temp)
                    requested = self.engine.requested_duty(fan)
                    duties[fan] = safety.safe_duty(requested, cpu_temp, gpu_temp)
            self.control.apply(duties)
            self._engine_fault = None
        except Exception as exc:
            self.engine.retry_next_tick()
            self._enter_recovery(f"profile '{profile}' could not be applied: {exc}")
            if not quiet:
                raise

    def _apply_now(self) -> None:
        t = self._sample()
        self._run_engine(t.cpu_temp, t.gpu_temp)

    def _persist(self) -> None:
        try:
            data = state.snapshot(self.engine, self.keyboard, self._turbo_choice)
            if self.engine.profile == curve.MANUAL:
                durable = self._durable_fan_state or {"profile": curve.FIRMWARE,
                                                     "linked": True, "cpu": [], "gpu": []}
                data.update({k: durable[k] for k in ("profile", "linked", "cpu", "gpu")})
            data["recovery_latched"] = self._failsafe_tripped
            state.save(self.state_path, data)
        except Exception as exc:
            print(f"gigactld: could not persist state: {exc}", flush=True)

    # --- profile / curve methods --------------------------------------------
    def _set_profile(self, sender, params, invocation) -> None:
        (name,) = params.unpack()
        candidate = copy.deepcopy(self.engine)
        try:
            candidate.set_profile(name)
        except ValueError as exc:
            invocation.return_dbus_error(f"{_ERR}.InvalidArgs", str(exc))
            return
        try:
            self._rearm()
            self.engine = candidate
            self._manual_targets.clear()
            if name == curve.FIRMWARE:
                if not self.control.restore_all():
                    raise ec_mod.EcError(self.control.handback_status())
            else:
                self._apply_now()
        except Exception as exc:
            self._enter_recovery(f"profile '{name}' request failed: {exc}")
            raise
        self._durable_fan_state = state.snapshot(self.engine)
        self._persist()
        self._notify_fan_state()
        invocation.return_value(None)

    def _set_curve(self, sender, params, invocation) -> None:
        which, points, linked = params.unpack()
        candidate = copy.deepcopy(self.engine)
        try:
            candidate.set_one_curve(which, points, linked)
        except ValueError as exc:
            invocation.return_dbus_error(f"{_ERR}.InvalidArgs", str(exc))
            return
        try:
            self._rearm()
            self.engine = candidate
            self._manual_targets.clear()
            self._apply_now()
        except Exception as exc:
            self._enter_recovery(f"custom curve request failed: {exc}")
            raise
        self._durable_fan_state = state.snapshot(self.engine)
        self._persist()
        self._notify_fan_state()
        invocation.return_value(None)

    # --- CPU methods ---------------------------------------------------------
    def _set_turbo_boost(self, sender, params, invocation) -> None:
        (enabled,) = params.unpack()
        try:
            self.turbo.set_enabled(enabled)
        except turbo.TurboUnavailable as exc:
            invocation.return_dbus_error(f"{_ERR}.TurboUnavailable", str(exc))
            return
        self._turbo_choice = enabled
        self._persist()
        self._notify_properties(TurboBoost=GLib.Variant("b", enabled))
        invocation.return_value(None)

    # --- keyboard methods ----------------------------------------------------
    def _remember_keyboard(self) -> keyboard.KeyboardState:
        """Keyboard state is created lazily on first use, so a user who only
        ever touches the fans never gets a keyboard section written — and thus
        never has the firmware default re-applied at boot."""
        if self.keyboard is None:
            self.keyboard = keyboard.KeyboardState()
        return self.keyboard

    def _set_keyboard_color(self, sender, params, invocation) -> None:
        r, g, b = params.unpack()
        if not all(0 <= c <= 255 for c in (r, g, b)):
            invocation.return_dbus_error(f"{_ERR}.InvalidArgs",
                                         f"colour components must be 0-255: {(r, g, b)}")
            return
        keyboard.set_color(self.ec, r, g, b)
        self._remember_keyboard().record_colour(r, g, b)
        self._persist()
        self._notify_keyboard()
        invocation.return_value(None)

    def _set_keyboard_brightness(self, sender, params, invocation) -> None:
        (percent,) = params.unpack()
        if not 0 <= percent <= 100:
            invocation.return_dbus_error(f"{_ERR}.InvalidArgs", f"bad percent {percent}")
            return
        keyboard.set_brightness(self.ec, percent)
        self._remember_keyboard().record_brightness(percent)
        self._persist()
        self._notify_keyboard()
        invocation.return_value(None)

    def _set_keyboard_enabled(self, sender, params, invocation) -> None:
        (on,) = params.unpack()
        keyboard.set_enabled(self.ec, on)
        self._remember_keyboard().record_enabled(on)
        self._persist()
        self._notify_keyboard()
        invocation.return_value(None)

    def stop(self) -> None:
        self._loop.quit()

    def _sync_turbo(self) -> None:
        """Observe external policy changes; never fight TuneD by reasserting."""
        try:
            current = (self.turbo.available(), self.turbo.enabled())
        except turbo.TurboUnavailable:
            current = (False, False)
        if current != self._last_turbo:
            self._last_turbo = current
            self._notify_properties(TurboAvailable=GLib.Variant("b", current[0]),
                                    TurboBoost=GLib.Variant("b", current[1]))
