"""DMI allowlist tests use explicit identity or a temporary DMI tree only."""
import pytest

from gigactld.platform import PlatformError, PlatformInfo, read_platform, require_supported_platform


def _dmi(path, vendor="GIGABYTE", model="G5 GE", bios="FB06"):
    for name, value in (("sys_vendor", vendor), ("product_name", model), ("bios_version", bios)):
        (path / name).write_text(value + "\n")


def test_read_platform_from_injected_dmi_tree(tmp_path):
    _dmi(tmp_path)
    assert read_platform(tmp_path) == PlatformInfo("GIGABYTE", "G5 GE", "FB06")


@pytest.mark.parametrize("field", ["sys_vendor", "product_name", "bios_version"])
def test_missing_identity_fails_closed(tmp_path, field):
    _dmi(tmp_path)
    (tmp_path / field).unlink()
    with pytest.raises(PlatformError, match=field):
        read_platform(tmp_path)


@pytest.mark.parametrize("value", ["", " \n", "G5 GE\x00", "G5\nGE", "\x7f"])
def test_empty_or_malformed_identity_fails_closed(tmp_path, value):
    _dmi(tmp_path, model=value)
    with pytest.raises(PlatformError, match="product_name"):
        read_platform(tmp_path)


def test_non_utf8_identity_fails_closed(tmp_path):
    _dmi(tmp_path)
    (tmp_path / "sys_vendor").write_bytes(b"\xff")
    with pytest.raises(PlatformError, match="sys_vendor"):
        read_platform(tmp_path)


@pytest.mark.parametrize("info", [
    PlatformInfo("GIGABYTE", "G5 GE", "FB06"),
    PlatformInfo("GIGABYTE", "G6 KF", "BIOS-not-recorded-in-evidence"),
])
def test_exact_supported_models(info):
    assert require_supported_platform(info) is info


@pytest.mark.parametrize("info", [
    PlatformInfo("", "G5 GE", "FB06"),
    PlatformInfo("Other", "G5 GE", "FB06"),
    PlatformInfo("gigabyte", "G5 GE", "FB06"),
    PlatformInfo("GIGABYTE", "G5 KF", "FB06"),
    PlatformInfo("GIGABYTE", "G5 GE variant", "FB06"),
    PlatformInfo("GIGABYTE", "G5 GE", "FB05"),
    PlatformInfo("GIGABYTE", "G5 GE", "FB06 beta"),
    PlatformInfo("GIGABYTE", "G6 KF", ""),
    PlatformInfo("GIGABYTE", "G6 KF", "\x00"),
    PlatformInfo("GIGABYTE", " G5 GE ", "FB06"),
])
def test_similar_models_other_ge_firmware_and_missing_identity_are_rejected(info):
    with pytest.raises(PlatformError):
        require_supported_platform(info)


def test_default_guard_reads_identity(monkeypatch):
    info = PlatformInfo("GIGABYTE", "G5 GE", "FB06")
    monkeypatch.setattr("gigactld.platform.read_platform", lambda: info)
    assert require_supported_platform() is info
