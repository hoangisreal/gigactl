"""M1 boundary regressions: fake bus/EC only, with real temporary flock locks."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import json
import time

import pytest


def _rules(*rules):
    return {"STUB_EC_RULES": json.dumps(rules)}


def _wait_for(path):
    deadline = time.monotonic() + 5
    while not path.exists():
        assert time.monotonic() < deadline, f"fake bus never reached {path}"
        time.sleep(0.01)


def _auto_commands(sandbox):
    """Record attempted auto commands, even when a parameter/doorbell failed."""
    writes = sandbox.ec_writes
    return [writes[i + 1][1] for i, write in enumerate(writes[:-1])
            if write == (0xF9, 0xFF) and writes[i + 1][0] == 0xFA]


@pytest.mark.parametrize("tool,args", [("gfan", ["70"]), ("gfan", ["auto"]),
                                      ("gkbd", ["red"]), ("gkbd", ["apply"])])
def test_direct_writes_require_explicit_opt_in(sandbox, tool, args):
    result = sandbox.run(tool, *args, daemon=False,
                         extra_env={"GIGACTL_ALLOW_DIRECT": "0"})
    assert result.returncode != 0
    assert "GIGACTL_ALLOW_DIRECT=1" in result.output
    assert sandbox.ec_calls == [] and sandbox.sudo_calls == []
    assert not sandbox.owner_lock.exists()


@pytest.mark.parametrize("tool,args,allow", [("gfan", ["status"], "0"), ("gkbd", ["status"], "0"),
                                           ("gfan", ["70"], "1"), ("gkbd", ["red"], "1")])
def test_direct_reads_and_writes_require_the_owner_lease(sandbox, tool, args, allow):
    holder = sandbox.hold_lock(owner=True)
    try:
        result = sandbox.run(tool, *args, daemon=False,
                             extra_env={"GIGACTL_ALLOW_DIRECT": allow,
                                        "GIGACTL_LOCK_WAIT": "0.1"})
    finally:
        holder.kill()
    assert result.returncode != 0 and "owner lease" in result.output
    assert "no direct fallback" in result.output
    assert sandbox.ec_calls == []
    assert not sandbox.lock.exists(), "fd8 must precede the fd9 mailbox lock"


def test_direct_read_needs_no_write_opt_in_but_holds_both_locks(sandbox):
    result = sandbox.run("gfan", "status", daemon=False,
                         extra_env={"GIGACTL_ALLOW_DIRECT": "0"})
    assert result.returncode == 0, result.output
    assert sandbox.ec_calls and sandbox.ec_writes == []


def test_owner_lock_environment_alias_uses_the_same_temporary_lease(sandbox):
    alternate = sandbox.dir / "alternate-owner.lock"
    result = sandbox.run("gfan", "status", daemon=False, extra_env={
        "GIGACTL_OWNER_LOCK": "", "OWNER_LOCK": str(alternate),
    })
    assert result.returncode == 0, result.output
    assert alternate.exists() and not sandbox.owner_lock.exists()


@pytest.mark.parametrize("tool,args", [("gfan", ["status"]), ("gkbd", ["red"])])
def test_python_owner_lease_blocks_direct_even_before_dbus_name_is_published(sandbox, tool, args):
    # Only the filesystem lease is constructed, never an EC/backend/daemon.
    from daemon.gigactld.ownership import OWNER_LOCK_PATH, OwnerLease

    assert OWNER_LOCK_PATH == "/run/gigactl/owner.lock"
    with OwnerLease(str(sandbox.owner_lock), timeout_s=0):
        result = sandbox.run(tool, *args, daemon=False,
                             extra_env={"GIGACTL_OWNER_LOCK_WAIT": "0.1"})
        assert result.returncode != 0 and "owner lease" in result.output
        assert sandbox.ec_calls == []
    result = sandbox.run(tool, *args, daemon=False)
    assert result.returncode == 0, result.output


@pytest.mark.parametrize("tool,args", [("gfan", ["70"]), ("gfan", ["status"]),
                                      ("gkbd", ["red"])])
def test_daemon_start_while_waiting_for_owner_lease_never_touches_ec(sandbox, tool, args):
    bus_state = sandbox.dir / "bus-state"
    marker = sandbox.dir / "first-bus-reply"
    bus_state.write_text("0")
    holder = sandbox.hold_lock(owner=True)
    with ThreadPoolExecutor(max_workers=1) as workers:
        task = workers.submit(sandbox.run, tool, *args, daemon=False,
                              extra_env={"STUB_DAEMON_FILE": str(bus_state),
                                         "STUB_BUS_REPLY_MARKER": str(marker)})
        try:
            _wait_for(marker)
            assert marker.read_text() == "0"
            assert sandbox.ec_calls == []
            bus_state.write_text("1")
        finally:
            holder.kill()
        result = task.result(timeout=10)
    assert result.returncode != 0 and "became active" in result.output
    assert sandbox.ec_calls == [] and sandbox.dbus_calls() == []


@pytest.mark.parametrize("answer", ["error", "garbage", "1"])
@pytest.mark.parametrize("tool,args", [("gfan", ["70"]), ("gfan", ["status"]),
                                      ("gkbd", ["red"])])
def test_bus_failure_or_daemon_ownership_after_lock_is_fail_closed(sandbox, answer, tool, args):
    result = sandbox.run(tool, *args, daemon=False,
                         extra_env={"STUB_DAEMON_SEQUENCE": f"0,{answer}"})
    assert result.returncode != 0
    assert sandbox.owner_lock.exists() and sandbox.lock.exists()
    assert sandbox.ec_calls == [] and sandbox.dbus_calls() == []


@pytest.mark.parametrize("unsafe", ["symlink-file", "symlink-dir", "writable-dir", "writable-file", "hardlink"])
def test_unsafe_owner_lock_paths_are_refused(sandbox, unsafe):
    private = sandbox.dir / "private"
    private.mkdir()
    lock = private / "owner.lock"
    if unsafe == "symlink-file":
        target = sandbox.dir / "target"
        target.touch()
        lock.symlink_to(target)
    elif unsafe == "symlink-dir":
        link = sandbox.dir / "link"
        link.symlink_to(private, target_is_directory=True)
        lock = link / "owner.lock"
    elif unsafe == "writable-dir":
        private.chmod(0o777)
    elif unsafe == "writable-file":
        lock.touch(mode=0o666)
        lock.chmod(0o666)
    else:
        lock.touch()
        (private / "alias").hardlink_to(lock)
    result = sandbox.run("gfan", "status", daemon=False,
                         extra_env={"GIGACTL_OWNER_LOCK": str(lock)})
    assert result.returncode != 0 and "unsafe" in result.output
    assert sandbox.ec_calls == []


@pytest.mark.parametrize("failure", [
    {"call": "write 0xF9 1", "status": 41},
    {"call": "write 0xFA 178", "status": 42},
    {"call": "write 0xF8 0xC1", "status": 43, "apply": True},
    {"call": "write 0xF9 2", "status": 44},
    {"call": "write 0xF8 0xC1", "occurrence": 2, "status": 45, "apply": True},
    {"call": "read 0xCE", "status": 46},
    {"call": "read 0xCF", "status": 47},
    {"call": "read 0x0A", "occurrence": 2, "status": 48},
])
def test_potentially_applied_failure_restores_both_and_preserves_original_status(sandbox, failure):
    result = sandbox.run("gfan", "70", daemon=False, extra_env=_rules(failure))
    assert result.returncode == failure["status"], result.output
    assert _auto_commands(sandbox) == [1, 2]
    assert "not read-back verified" in result.output
    assert "Reverted to automatic" not in result.output
    assert "Set: fan1" not in result.stdout
    assert not (sandbox.state_dir / "gfan.mode").exists()
    # The lease is released only after best-effort recovery finished.
    with sandbox.owner_lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)


@pytest.mark.parametrize("output", ["", "garbage", "256", "-1", "1+2", "0xGG"])
def test_bad_verification_reads_are_failures_not_arithmetic_or_success(sandbox, output):
    result = sandbox.run("gfan", "70", daemon=False,
                         extra_env=_rules({"call": "read 0xCE", "output": output}))
    assert result.returncode != 0
    assert _auto_commands(sandbox) == [1, 2]
    assert "Set: fan1" not in result.stdout


@pytest.mark.parametrize("failed_fan", [1, 2])
def test_cleanup_failure_still_attempts_other_fan_and_never_claims_recovery(sandbox, failed_fan):
    result = sandbox.run("gfan", "70", daemon=False, extra_env=_rules(
        {"call": "read 0xCE", "status": 42},
        {"call": f"write 0xFA {failed_fan}", "status": 53},
    ))
    assert result.returncode == 42, result.output
    assert _auto_commands(sandbox) == [1, 2]
    assert "recovery is NOT confirmed" in result.output
    assert "recovery incomplete" in result.output
    assert "commands sent to both" not in result.output
    assert "unknown" in (sandbox.state_dir / "gfan.mode").read_text()


def test_both_restore_attempts_can_fail_without_masking_original_failure(sandbox):
    result = sandbox.run("gfan", "70", daemon=False, extra_env=_rules(
        {"call": "read 0xCE", "status": 42},
        {"call": "write 0xFA 1", "status": 53},
        {"call": "write 0xFA 2", "status": 54},
    ))
    assert result.returncode == 42, result.output
    assert _auto_commands(sandbox) == [1, 2]
    assert "fan1 status 53, fan2 status 54" in result.output
    assert "commands sent to both" not in result.output


@pytest.mark.parametrize("failed_fan", [1, 2])
@pytest.mark.parametrize("stage", ["selector", "doorbell"])
def test_failed_restore_selector_or_doorbell_never_skips_other_fan(sandbox, failed_fan, stage):
    restore_failure = (
        {"call": "write 0xF9 0xFF", "occurrence": failed_fan, "status": 53}
        if stage == "selector" else
        {"call": "write 0xF8 0xC1", "occurrence": 2 + failed_fan, "status": 53, "apply": True}
    )
    result = sandbox.run("gfan", "70", daemon=False, extra_env=_rules(
        {"call": "read 0xCE", "status": 42}, restore_failure,
    ))
    assert result.returncode == 42, result.output
    assert sandbox.ec_writes.count((0xF9, 0xFF)) == 2
    assert (0xFA, 3 - failed_fan) in sandbox.ec_writes
    assert "recovery incomplete" in result.output
    assert "commands sent to both" not in result.output


@pytest.mark.parametrize("signal,status", [("INT", 130), ("TERM", 143)])
@pytest.mark.parametrize("restore_fails", [False, True])
def test_interrupt_after_applied_write_recovers_both_even_when_cleanup_is_interrupted(sandbox, signal, status, restore_fails):
    rules = [{"call": "write 0xF8 0xC1", "signal": signal}]
    if restore_fails:
        # A second signal during a failing first restore must not skip fan2.
        rules.append({"call": "write 0xFA 1", "signal": signal, "status": 54})
    result = sandbox.run("gfan", "70", daemon=False, extra_env=_rules(*rules))
    assert result.returncode == status, result.output
    assert _auto_commands(sandbox) == [1, 2]
    assert "Set: fan1" not in result.stdout
    if restore_fails:
        assert "recovery incomplete" in result.output
        assert "commands sent to both" not in result.output


def test_auto_failure_still_attempts_both_and_preserves_failure(sandbox):
    result = sandbox.run("gfan", "auto", daemon=False,
                         extra_env=_rules({"call": "write 0xFA 1", "status": 55}))
    assert result.returncode == 55, result.output
    assert _auto_commands(sandbox)[:2] == [1, 2]
    assert "Fans restored" not in result.stdout


def test_status_read_failure_is_not_hidden_in_echo(sandbox):
    result = sandbox.run("gfan", "status", daemon=False,
                         extra_env=_rules({"call": "read 0x07", "status": 56}))
    assert result.returncode == 56, result.output
    assert "CPU:" not in result.stdout
    assert sandbox.ec_writes == []


def test_keyboard_parameter_failure_never_rings_doorbell_or_saves_success(sandbox):
    result = sandbox.run("gkbd", "red", daemon=False,
                         extra_env=_rules({"call": "write 0xFA 0x3F", "status": 57}))
    assert result.returncode == 57, result.output
    assert all(reg != 0xF8 for reg, _ in sandbox.ec_writes)
    assert not (sandbox.state_dir / "gkbd" / "state").exists()


@pytest.mark.parametrize("cpu,gpu,pct,allowed", [
    (69, 69, 0, True), (70, 50, 0, False), (50, 70, 20, False),
    (70, 70, 30, True), (85, 50, 20, False), (50, 85, 20, False),
    (94, 86, 99, True), (95, 50, 99, False), (50, 87, 99, False),
    (95, 87, 100, True), (0, 50, 100, False), (50, 255, 100, False),
    # GPU 0 means unavailable/asleep (daemon policy), not a fatal sensor:
    # the CPU alone then justifies (or refuses) the duty.
    (50, 0, 0, True), (70, 0, 0, False), (95, 0, 99, False),
    (95, 0, 100, True), (126, 0, 100, False),
])
@pytest.mark.parametrize("force", [False, True])
def test_direct_thermal_guards_check_both_sensors_and_force_cannot_bypass(sandbox, cpu, gpu, pct, allowed, force):
    args = [str(pct), *(["--force"] if force else [])]
    result = sandbox.run("gfan", *args, daemon=False, regs={"0x07": cpu, "0x0A": gpu})
    assert (result.returncode == 0) is allowed, result.output
    if not allowed:
        assert sandbox.ec_writes == []


def test_emergency_requires_both_fans_not_just_one(sandbox):
    result = sandbox.run("gfan", "100", "50", "--force", daemon=False,
                         regs={"0x07": 60, "0x0A": 87})
    assert result.returncode != 0 and "BOTH fans at 100%" in result.output
    assert sandbox.ec_writes == []
