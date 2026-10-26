"""Fail-closed DMI allowlist for the privileged EC transport.

This is an evidence boundary, not a claim that every firmware configuration on
an allowed machine is safe. G6 KF BIOS coverage was not recorded; G5 GE coverage
is restricted to FB06. Similar model names are deliberately not accepted.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DMI_PATH = Path("/sys/class/dmi/id")


class PlatformError(RuntimeError):
    """The machine's identity is missing or outside the evidence allowlist."""


@dataclass(frozen=True)
class PlatformInfo:
    vendor: str
    model: str
    bios: str


def read_platform(dmi_path: str | Path = DMI_PATH) -> PlatformInfo:
    """Read DMI identity without opening any EC interface.

    ``dmi_path`` is an explicit filesystem injection for tests. Unreadable,
    empty, or malformed identity fields fail closed, including BIOS on G6 KF.
    """
    root = Path(dmi_path)
    fields = []
    for name in ("sys_vendor", "product_name", "bios_version"):
        try:
            value = (root / name).read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError) as exc:
            raise PlatformError(f"cannot read DMI {name}: {exc}") from exc
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise PlatformError(f"missing or malformed DMI {name}")
        fields.append(value)
    return PlatformInfo(*fields)


def require_supported_platform(info: PlatformInfo | None = None) -> PlatformInfo:
    """Accept only GIGABYTE G5 GE/FB06 or the documented G6 KF model.

    An explicit ``info`` permits tests to avoid the host's DMI. It still goes
    through the same allowlist; it is not a general model-guard bypass.
    """
    info = read_platform() if info is None else info
    if not isinstance(info, PlatformInfo) or any(
        not isinstance(value, str) or not value.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        for value in (info.vendor, info.model, info.bios)
    ):
        raise PlatformError("missing or malformed platform identity")
    if info.vendor == "GIGABYTE":
        if info.model == "G5 GE" and info.bios == "FB06":
            return info
        if info.model == "G6 KF":
            # The original model evidence does not specify a BIOS version.
            return info
    raise PlatformError(
        f"unsupported EC platform: vendor={info.vendor!r}, model={info.model!r}, "
        f"BIOS={info.bios!r}; allowed: GIGABYTE G5 GE BIOS FB06, or GIGABYTE "
        "G6 KF (BIOS coverage unspecified)"
    )
