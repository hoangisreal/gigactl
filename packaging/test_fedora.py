"""Fedora source contracts and clean-source builds with a stub rpmbuild only.

No RPM is built, no scriptlet is executed, and nothing is installed or started.
Git objects/refs in these tests belong exclusively to temporary fixture repos.
"""
from __future__ import annotations

import ast
import json
import os
import re
import runpy
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import ROOT, text, unit_values

SPEC = "fedora/gigactl.spec"
POLICY = "fedora/selinux/gigactld.te"
CONTEXTS = "fedora/selinux/gigactld.fc"
UNIT = "daemon/data/gigactl.service"


def _commands(body):
    return "\n".join(line for line in body.splitlines()
                     if line.strip() and not line.lstrip().startswith("#"))


def test_fedora_keeps_the_master_release_and_packages_all_daemon_modules():
    spec = text(SPEC)
    assert re.search(r"^Version:\s+1\.2\.0$", spec, re.MULTILINE)
    assert re.search(r"^Release:\s+1%\{\?dist\}$", spec, re.MULTILINE)
    assert "install -pm 0644 daemon/gigactld/*.py $PRIV/gigactld/" in spec
    assert "gigactld.pp" in spec and "gigactl-devport.pp" not in spec
    assert "%dir /var/lib/gigactl" in spec
    assert "BuildRequires:  selinux-policy-devel" in spec
    assert "make -f /usr/share/selinux/devel/Makefile gigactld.pp" in spec


def test_daemon_has_a_separate_isolated_root_only_entry_point():
    spec, contexts = text(SPEC), text(CONTEXTS)
    launcher = text("fedora/gigactld-launcher")
    assert launcher.startswith("#!/usr/bin/python3 -I\n")
    assert "install -pm 0755 fedora/gigactld-launcher $PRIV/gigactld-launcher" in spec
    assert "ln -s ../lib/gigactl/gigactld-launcher %{buildroot}%{_bindir}/gigactld" in spec
    assert "ln -s ../lib/gigactl/launcher %{buildroot}%{_bindir}/gigactl-gui" in spec
    exec_contexts = [line for line in contexts.splitlines() if "gigactld_exec_t" in line]
    assert len(exec_contexts) == 1
    assert exec_contexts[0].startswith("/usr/lib/gigactl/gigactld-launcher ")
    assert "gui" not in contexts
    assert "setcap" not in spec and "4755" not in spec
    # Running the function with a fake UID must exit BEFORE importing the daemon
    # or modifying the module search path. Never execute its root branch here.
    namespace = runpy.run_path(str(ROOT / "fedora/gigactld-launcher"))
    main = namespace["main"]
    main.__globals__["os"] = SimpleNamespace(geteuid=lambda: 1000)
    old_path = list(sys.path)
    assert main() == 1
    assert sys.path == old_path


def test_policy_grants_hardware_only_to_dedicated_daemon_domain():
    policy = _commands(text(POLICY))
    assert "dbus_system_domain(gigactld_t, gigactld_exec_t)" in policy
    for operation in ("dev_read_raw_memory", "dev_write_raw_memory"):
        assert f"{operation}(gigactld_t)" in policy
    assert not re.search(r"allow\s+init_t\s+", policy)
    assert "unconfined_domain(" not in policy
    assert "dac_override" not in policy and "setuid" not in policy
    assert not (ROOT / "fedora/selinux/gigactl-devport.te").exists()
    for peer in ("policykit_t", "systemd_logind_t", "unconfined_t"):
        assert f"allow gigactld_t {peer}:dbus send_msg;" in policy
        assert f"allow {peer} gigactld_t:dbus send_msg;" in policy
    assert "files_read_usr_files(gigactld_t)" in policy
    assert "manage_files_pattern(gigactld_t, usr_t" not in policy


def test_policy_covers_preserved_owner_lock_mailbox_and_notify_datagrams():
    policy, contexts = _commands(text(POLICY)), text(CONTEXTS)
    assert "/run/gigactl(/.*)?" in contexts
    assert "/run/lock/gigactl-ec\\.lock" in contexts
    assert "files_pid_file(gigactld_var_run_t)" in policy
    assert 'files_pid_filetrans(gigactld_t, gigactld_var_run_t, dir, "gigactl")' in policy
    assert 'files_pid_filetrans(unconfined_t, gigactld_var_run_t, dir, "gigactl")' in policy
    assert 'files_lock_filetrans(gigactld_t, gigactld_lock_t, file, "gigactl-ec.lock")' in policy
    assert 'files_lock_filetrans(unconfined_t, gigactld_lock_t, file, "gigactl-ec.lock")' in policy
    runtime_rule = re.search(r"allow gigactld_t gigactld_var_run_t:file \{([^}]+)\}", policy)
    assert runtime_rule and {"create", "open", "read", "write", "lock"} <= set(runtime_rule[1].split())
    assert "unlink" not in runtime_rule[1]
    assert "self:unix_dgram_socket create_socket_perms" in policy
    assert "init_dgram_send(gigactld_t)" in policy
    assert "init_var_run_t:sock_file write" in policy


def test_policy_scriptlets_have_dependencies_and_do_not_hide_failures_or_restart():
    spec = text(SPEC)
    post = spec.split("\n%post\n", 1)[1].split("\n%preun\n", 1)[0]
    postun = spec.split("\n%postun\n", 1)[1].split("\n%files\n", 1)[0]
    for section in (post, postun):
        commands = _commands(section)
        assert "set -e" in commands
        assert "2>/dev/null" not in commands and "|| :" not in commands
        assert not re.search(r"^\s*(?:systemctl|modprobe|gfan|gkbd|/usr/bin/gigactld)(?:\s|$)",
                             commands, re.MULTILINE)
    assert "Requires(post): policycoreutils" in spec
    assert "Requires(post): grep" in spec
    assert "Requires(postun): policycoreutils" in spec
    assert "%{?systemd_requires}" in spec
    assert post.index("semodule -i") < post.index("semodule -r gigactl-devport")
    assert "MODULES=$(semodule -l)" in post  # a failed listing must not be ignored
    assert "restorecon -F /usr/lib/gigactl/gigactld-launcher" in post
    assert "for runtime in /run/gigactl /run/lock/gigactl-ec.lock" in post
    assert "rm " not in post and "touch " not in post
    assert "%systemd_postun_with_restart" not in spec
    assert "%systemd_postun gigactl.service" in postun


def test_policy_migration_requires_quiescent_service_without_stopping_it_in_a_scriptlet():
    spec = text(SPEC)
    pre = _commands(spec.split("\n%pre\n", 1)[1].split("\n%post\n", 1)[0])
    assert "Requires(pre):  systemd" in spec
    assert "Requires(pre):  coreutils" in spec
    assert "timeout 5s systemctl list-units --all --no-legend --no-pager gigactl.service" in pre
    assert 'if [ -n "$UNITS" ]; then' in pre  # first installation: no unit is OK
    assert pre.index("systemctl list-units") < pre.index("systemctl show")
    assert "timeout 5s systemctl show --property=ActiveState --value gigactl.service" in pre
    assert "inactive|failed) ;;" in pre
    assert "exit 1" in pre and "verify firmware handback" in pre
    assert not re.search(r"\bsystemctl\s+(?:start|stop|restart|try-restart)\b", pre)
    assert "semodule" not in pre


@pytest.mark.parametrize("section,next_section", [("pre", "post"), ("post", "preun"),
                                                  ("preun", "postun"), ("postun", "files")])
def test_scriptlet_shell_syntax_without_executing_it(section, next_section):
    body = text(SPEC).split(f"\n%{section}\n", 1)[1].split(f"\n%{next_section}\n", 1)[0]
    result = subprocess.run(["sh", "-n"], input=body, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("key,value", [
    ("Type", "dbus"), ("WatchdogSec", "15s"), ("NotifyAccess", "main"),
    ("TimeoutStartSec", "15s"), ("TimeoutStopSec", "8s"), ("TimeoutAbortSec", "5s"),
    ("KillMode", "control-group"), ("RuntimeDirectory", "gigactl"),
    ("RuntimeDirectoryMode", "0755"), ("RuntimeDirectoryPreserve", "yes"),
    ("Restart", "on-failure"), ("RestartSec", "2s"),
    ("StartLimitIntervalSec", "60s"), ("StartLimitBurst", "3"),
])
def test_unit_watchdog_and_owner_lock_contract(key, value):
    assert unit_values(UNIT, key) == [value]


def test_stop_recovery_uses_the_guarded_entry_point_and_reports_failure():
    assert unit_values(UNIT, "ExecStart") == ["/usr/bin/gigactld"]
    assert unit_values(UNIT, "ExecStopPost") == ["/usr/bin/gigactld --restore-firmware"]
    assert '"--restore-firmware"' in text("daemon/gigactld/__main__.py")
    assert "PYTHONDONTWRITEBYTECODE=1" in unit_values(UNIT, "Environment")


def test_raw_ec_is_never_claimed_as_the_default_backend():
    """The daemon fails closed without ec_sys; the package must not claim or
    silently enable the raw /dev/port backend the unit refuses to opt into."""
    unit = text(UNIT)
    assert "--allow-raw-ec" not in unit and "GIGACTL_ALLOW_RAW_EC" not in unit
    for source in (SPEC, "debian/changelog", "debian/extra/modules-load.d/gigactl.conf"):
        assert "the daemon uses the /dev/port" not in text(source), source
        assert "uses its /dev/port backend" not in text(source), source
        assert "falls back to raw /dev/port" not in text(source), source


def test_watchdog_has_no_independent_heartbeat_thread_or_timer():
    source = text("daemon/gigactld/watchdog.py")
    tree = ast.parse(source)
    imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
               for alias in node.names}
    assert not {"threading", "asyncio", "sched", "signal"} & imports
    assert "SOCK_DGRAM" in source and 'b"WATCHDOG=1"' in source
    assert "READY=1" not in source


def test_service_watchdog_and_authorization_stay_on_the_glib_dispatch_path():
    source = text("daemon/gigactld/service.py")
    assert "Watchdog.from_environment()" in source
    assert ".ping()" in source and "GLib.timeout_add" in source
    assert ".check_async(" in source
    assert ".call_sync(" not in source
    tree = ast.parse(source)
    imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
               for alias in node.names}
    assert not {"threading", "sched"} & imports


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


def _snapshot_fixture(repo):
    """Create a fixture commit object/ref without invoking git commit/checkout."""
    _git(repo, "add", ".")
    tree = _git(repo, "write-tree")
    commit = _git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                  "commit-tree", tree, "-m", "fixture source")
    _git(repo, "update-ref", "refs/heads/main", commit)


@pytest.fixture
def rpm_fixture(tmp_path):
    repo = tmp_path / "fixture checkout with spaces"
    repo.mkdir()
    (repo / "fedora").mkdir()
    shutil.copyfile(ROOT / "fedora/build-rpm.sh", repo / "fedora/build-rpm.sh")
    (repo / "fedora/build-rpm.sh").chmod(0o755)
    (repo / "fedora/gigactl.spec").write_text("Name: gigactl\nVersion: 1.1.0\nRelease: 1\n\n")
    (repo / ".gitignore").write_text("/fedora/RPMS/\n/fedora/rpmbuild/\n__pycache__/\n")
    (repo / "MANIFEST.in").write_text("include fixture-tool\ninclude file with spaces\n")
    (repo / "fixture-tool").write_text("#!/bin/sh\nexit 99\n")
    (repo / "fixture-tool").chmod(0o755)
    (repo / "file with spaces").write_text("committed source\n")
    (repo / "fixture-link").symlink_to("fixture-tool")
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "core.filemode", "true")
    _snapshot_fixture(repo)

    stubs, capture, scratch = (tmp_path / name for name in ("stubs", "capture", "scratch"))
    for directory in (stubs, capture, scratch):
        directory.mkdir()
    stub = stubs / "rpmbuild"
    stub.write_text(f"#!{sys.executable}\n" + '''
import json
import os
import pathlib
import shutil
import sys

args = sys.argv[1:]
assert args[0] == "--define" and args[1].startswith("_topdir ")
assert args[2] == "-ba" and len(args) == 4
top = pathlib.Path(args[1].split(" ", 1)[1])
capture = pathlib.Path(os.environ["RPM_CAPTURE"])
(capture / "called").write_text(json.dumps({"args": args, "top": str(top)}))
archive, = (top / "SOURCES").glob("*.tar.gz")
shutil.copyfile(archive, capture / "source.tar.gz")
shutil.copyfile(args[3], capture / "spec")
if os.environ.get("FAIL_RPMBUILD"):
    sys.exit(7)
for subdir, name in (("RPMS/noarch", "gigactl-1.1.0-1.noarch.rpm"),
                     ("SRPMS", "gigactl-1.1.0-1.src.rpm")):
    out = top / subdir / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"fixture only, not an RPM")
    out.chmod(0o640)
''')
    stub.chmod(0o755)
    env = {**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}",
           "TMPDIR": str(scratch), "RPM_CAPTURE": str(capture)}
    return SimpleNamespace(repo=repo, capture=capture, scratch=scratch, env=env)


def _build(fixture):
    return subprocess.run(["bash", "fedora/build-rpm.sh"], cwd=fixture.repo,
                          env=fixture.env, capture_output=True, text=True)


def test_clean_build_uses_exact_committed_source_preserving_modes_and_manifest(rpm_fixture):
    fixture = rpm_fixture
    # Old generated products and caches must not enter the source archive or be
    # cleaned by the build. This never touches the real checkout's artifacts.
    for name in ("fedora/RPMS/old.rpm", "fedora/rpmbuild/SOURCES/old.tar.gz",
                 "__pycache__/ignored.pyc"):
        path = fixture.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"old artifact")
    assert _git(fixture.repo, "status", "--porcelain") == ""
    result = _build(fixture)
    assert result.returncode == 0, result.stderr
    expected = set(_git(fixture.repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines())
    with tarfile.open(fixture.capture / "source.tar.gz") as archive:
        members = {m.name.removeprefix("gigactl-1.1.0/"): m
                   for m in archive if not m.isdir()}
        assert set(members) == expected
        assert members["fixture-tool"].mode & stat.S_IXUSR
        assert members["fedora/build-rpm.sh"].mode & stat.S_IXUSR
        assert members["fixture-link"].issym()
        assert members["fixture-link"].linkname == "fixture-tool"
        assert archive.extractfile(members["MANIFEST.in"]).read() == (
            fixture.repo / "MANIFEST.in").read_bytes()
        assert all(".git/" not in name and "/RPMS/" not in name for name in members)
    assert (fixture.capture / "spec").read_bytes() == (
        fixture.repo / "fedora/gigactl.spec").read_bytes()
    called = json.loads((fixture.capture / "called").read_text())
    assert called["args"][-1].startswith(called["top"] + "/SPECS/")
    assert not Path(called["top"]).exists()
    assert not list(fixture.scratch.iterdir())
    assert (fixture.repo / "fedora/RPMS/old.rpm").read_bytes() == b"old artifact"
    assert (fixture.repo / "fedora/rpmbuild/SOURCES/old.tar.gz").read_bytes() == b"old artifact"
    products = list((fixture.repo / "fedora/RPMS").glob("gigactl-*.rpm"))
    assert len(products) == 2
    assert all(p.stat().st_mode & 0o777 == 0o640 for p in products)


@pytest.mark.parametrize("dirty", ["modified", "staged", "deleted", "staged-deletion",
                                  "untracked", "staged-new", "mode-change", "renamed"])
def test_dirty_staged_deleted_or_untracked_source_is_refused_before_rpmbuild(rpm_fixture, dirty):
    fixture, source = rpm_fixture, rpm_fixture.repo / "file with spaces"
    if dirty in ("modified", "staged"):
        source.write_text("not committed\n")
        if dirty == "staged":
            _git(fixture.repo, "add", source.name)
    elif dirty in ("deleted", "staged-deletion"):
        source.unlink()
        if dirty == "staged-deletion":
            _git(fixture.repo, "add", "-u")
    elif dirty in ("untracked", "staged-new"):
        (fixture.repo / "untracked\nfile").write_text("never overlay this\n")
        if dirty == "staged-new":
            _git(fixture.repo, "add", ".")
    elif dirty == "mode-change":
        (fixture.repo / "fixture-tool").chmod(0o644)
    elif dirty == "renamed":
        _git(fixture.repo, "mv", source.name, "new name")
    result = _build(fixture)
    assert result.returncode != 0
    assert "refusing to build dirty source" in result.stderr
    assert not (fixture.capture / "called").exists()
    assert not (fixture.repo / "fedora/RPMS").exists()
    assert not list(fixture.scratch.iterdir())


def test_failed_stub_build_cleans_only_its_temporary_topdir(rpm_fixture):
    fixture = rpm_fixture
    fixture.env["FAIL_RPMBUILD"] = "1"
    sentinel = fixture.scratch / "unrelated"
    sentinel.write_text("keep me")
    result = _build(fixture)
    assert result.returncode == 7
    assert (fixture.capture / "called").exists()
    assert list(fixture.scratch.iterdir()) == [sentinel]
    assert sentinel.read_text() == "keep me"
    assert not (fixture.repo / "fedora/RPMS").exists()


def test_invalid_committed_version_cannot_escape_the_scoped_build_directory(rpm_fixture):
    fixture = rpm_fixture
    (fixture.repo / "fedora/gigactl.spec").write_text("Version: ../../outside\n")
    _snapshot_fixture(fixture.repo)
    result = _build(fixture)
    assert result.returncode != 0 and "invalid committed spec version" in result.stderr
    assert not (fixture.capture / "called").exists()
    assert not list(fixture.scratch.iterdir())
