# Fedora source integration

`./fedora/build-rpm.sh` accepts only a clean, committed checkout. Staged,
modified, deleted and nonignored untracked paths are rejected. The spec and
`git archive` come from the same pinned commit; there is no working-tree overlay.
Executable modes, symlinks and committed manifests survive unchanged. A unique
temporary RPM topdir is cleaned on exit, without cleaning existing output.

The package keeps version **1.2.0-1**. It ships a separate root-only, isolated
daemon launcher; the GUI's shared launcher is not a privileged SELinux entry
point. The policy replaces the older broad `init_t` raw-I/O module with
`gigactld_t`, includes state, system-bus/polkit/logind, watchdog datagrams,
`/run/gigactl/owner.lock`, and the existing `/run/lock/gigactl-ec.lock`.
The lifetime lock's inode must not be removed during stop, restart or recovery.

Policy installation/relabel errors are reported, with declared scriptlet
dependencies. On a running systemd host, installation/upgrade requires a stopped
service (bounded, read-only status queries); stop it and verify firmware handback
before beginning the transaction. This avoids revoking the old `init_t` daemon's
EC access mid-control. The new package's upgrade scriptlets do not restart
hardware control; an older installed RPM's scriptlets remain part of a real
upgrade and need separate validation. The daemon's root/model checks apply on
startup and `--restore-firmware` recovery; neither
the unit nor RPM scriptlets run modprobe or a pre-guard hardware helper.

## Validation boundaries

- Source tests use fake buses/sockets and temporary Git repositories with a
  **stub** `rpmbuild`. They establish source/package contracts, not installation.
- The installed system may still be running an older unit, launcher or daemon.
  Source changes do not establish that its process has transitioned to
  `gigactld_t` or that the new policy is loaded.
- Stock SELinux `memory_device_t` and `sysfs_t` are coarse types: these rules
  confine access to the daemon domain, not to individual EC ports or one sysfs
  file. Python's root/model/path guards remain necessary.
- A Fedora enforcing install/upgrade/removal, live domain/D-Bus checks, watchdog
  kill/recovery, reboot, suspend/resume and physical firmware handback still
  require explicit, separately authorized lifecycle/hardware validation. No
  such result is implied by these tests.
