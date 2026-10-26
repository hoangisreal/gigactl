"""Persist the active profile so it survives a reboot (the EC resets to
firmware defaults on power-cycle; the daemon re-applies this at boot)."""
from __future__ import annotations

import json
import os
import tempfile

from . import curve

DEFAULT_PATH = "/var/lib/gigactl/state.json"


def snapshot(engine, kbd=None, turbo_enabled=None) -> dict:
    """Serialise the fan profile, plus the keyboard state when one is given.
    The keyboard rides in the same file so both restore from one atomic write;
    each domain owns its own dict shape (``kbd.to_dict()``) so this module never
    has to know the keyboard's fields."""
    data = {
        "profile": engine.profile,
        "linked": engine.linked,
        "cpu": [list(p) for p in engine.curve_for(curve.CPU_FAN)],
        "gpu": [list(p) for p in engine.curve_for(curve.GPU_FAN)],
    }
    if kbd is not None:
        data["keyboard"] = kbd.to_dict()
    if turbo_enabled is not None:
        data["turbo_enabled"] = turbo_enabled
    return data


def save(path: str, data: dict) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=parent or ".",
                                         prefix=".gigactl-state-", delete=False) as f:
            tmp = f.name
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # atomic, no predictable temporary symlink
        tmp = None
    finally:
        if tmp is not None:
            os.unlink(tmp)


def load(path: str):
    try:
        with open(path) as f:
            # A settings file is not an unbounded input stream.
            text = f.read(65_537)
        if len(text) > 65_536:
            return None
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def validate(data: dict) -> None:
    """Validate the full snapshot before boot restoration can touch hardware."""
    if not isinstance(data, dict):
        raise ValueError("saved state must be an object")
    if not isinstance(data.get("profile", curve.FIRMWARE), str):
        raise ValueError("invalid saved profile")
    for key in ("linked", "turbo_enabled", "recovery_latched"):
        if key in data and type(data[key]) is not bool:
            raise ValueError(f"{key} must be a boolean")
    for key in ("cpu", "gpu"):
        points = data.get(key, [])
        if not isinstance(points, list) or len(points) > 64:
            raise ValueError(f"invalid {key} curve")
        for point in points:
            if (not isinstance(point, (list, tuple)) or len(point) != 2 or
                    any(type(v) is not int or not 0 <= v <= 100 for v in point)):
                raise ValueError(f"invalid {key} curve point")
    if data.get("profile") == curve.CUSTOM and not data.get("cpu"):
        raise ValueError("saved custom curve is empty")
    if "keyboard" in data:
        kd = data["keyboard"]
        if not isinstance(kd, dict):
            raise ValueError("invalid saved keyboard")
        if "enabled" in kd and type(kd["enabled"]) is not bool:
            raise ValueError("keyboard enabled must be a boolean")
        for key, maximum in (("r", 255), ("g", 255), ("b", 255), ("brightness", 100)):
            if key in kd and (type(kd[key]) is not int or not 0 <= kd[key] <= maximum):
                raise ValueError(f"invalid keyboard {key}")


def restore(engine, data: dict) -> None:
    """Apply a loaded snapshot to an engine. Manual/unknown profiles fall back
    to firmware — 'manual' is a transient override that shouldn't have been
    saved, and firmware is the safe default."""
    validate(data)
    profile = data.get("profile", curve.FIRMWARE)
    if profile == curve.CUSTOM:
        cpu = [tuple(p) for p in data.get("cpu", [])]
        gpu = [tuple(p) for p in data.get("gpu", [])]
        engine.set_custom(cpu, gpu or None, linked=data.get("linked", True))
    elif profile in curve.PROFILES or profile == curve.FIRMWARE:
        engine.set_profile(profile)
    else:
        engine.set_profile(curve.FIRMWARE)
