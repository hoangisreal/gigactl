# Tasks and evidence

Snapshot: 2026-10-26. `I` = implemented, `U` = unit/fake tested,
`H` = hardware-validated **for this implementation**. Historical observations
are not a new implementation's hardware acceptance.

| ID | Atomic task | I | U | H | Evidence / remaining gate |
|---|---|---|---|---|---|
| M1.01 | Firmware escape invalidates queued curve work | Yes | Yes | No | `gui/tests/test_curve_safety.py` |
| M1.02 | Manual + curve thermal supervision, emergency override | Yes | Yes | No | `daemon/tests/test_daemon_safety.py` |
| M1.03 | Independent rollback after every partial two-fan write/read failure | Yes | Yes | No | Parameterized write/read fault injection in `daemon/tests/test_daemon_safety.py` |
| M1.04 | Pending handback retry, truthful status and re-arm on all fan entrypoints | Yes | Yes | No | Recovery/re-arm and restart-latch tests in `daemon/tests/test_daemon_safety.py` |
| M1.05 | Requested/applied reconciliation, silent rejection and bounded ramps | Yes | Yes | No | Silent-rejection/reset plus bounded-ramp, asymmetric two-fan and mid-ramp target-change cases in `daemon/tests/test_fan_control.py` |
| M1.06 | Exact platform guard and opt-in raw transport | Yes | Yes | No | `daemon/tests/test_platform.py`, `test_ec.py`, `test_main_safety.py`; CLI write guard mirrors the same allowlist (`cli/test_gfan.py`). Known limitation: G6 KF BIOS coverage unrecorded (model-exact only); native-backend audit is M4.01 |
| M1.07 | Lifetime controller lease and D-Bus ownership before control | Yes | Yes | No | `daemon/tests/test_ownership.py`, `test_main_safety.py`, `cli/test_safety.py` (fake bus loser/auth-order and process lease cases) |
| M1.08 | Bounded transport/locks and mailbox consumption acknowledgement | Yes | Yes | No | Emulated EC timeout/ack tests in `daemon/tests/test_ec.py`, `test_mailbox.py`; invalid offsets/values/duties raise instead of being clamped (`test_fans.py`, `test_keyboard.py`) |
| M1.09 | Async polkit, superseded requests and ownership recheck | Yes | Yes | No | Deferred fake authorizer plus Gio adapter cases in `daemon/tests/test_authz.py` |
| M1.10 | Event-loop watchdog + independent stop-post recovery | Yes | Yes | No | Fake supervisor/socket/entrypoint tests in `daemon/tests/test_watchdog.py`, `test_main_safety.py`, `packaging/test_fedora.py` |
| M1.11 | Suspend handback and fresh-state resume reconciliation | Yes | Yes | No | `daemon/tests/test_daemon_safety.py`, including failed suspend handback latching recovery and blocking resume. No real suspend/resume performed |
| M1.12 | Validate saved state; preserve durable policy across transient manual mode | Yes | Yes | No | Malformed state, persistence and recovery-latch tests |
| M1.13 | Independent safety review | Yes | — | No | Completed 2026-10-26: verdict PASS-WITH-FIXES, no blockers; fixes F1–F9 landed in tree (see below). Hardware validation still requires separate approval |
| M2.01 | Cached telemetry, invalid/stale state, missing-GPU distinction | Yes | Partial | No | Daemon property/fault tests; GUI presentation pending |
| M2.02 | Announce externally changed turbo without reassertion | Yes | Yes | No | Temporary sysfs knob only |
| M2.03 | Per-feature capability/support UI and redacted diagnostic export | No | No | No | Unblocked by M1 review; not started |
| M3.01 | Discover/delegate to existing OS power-profile provider | No | No | No | Never start a second policy owner |
| M3.02 | AC/battery rules, manual precedence, per-game holds | No | No | No | Depends on M2 |
| M4.01 | Audit native Clevo ACPI/WMI backend initialization/serialization | Research | No | No | Loading a driver is not passive discovery |
| M4.02 | Reuse RGB effects/idle UX with privacy + EC I/O-budget review | Research | No | No | Upstream PR #35 is not automatically accepted |
| M4.03 | Optional power/charge controls only through verified capabilities | No | No | No | Requires separate hardware authorization |
| M5.01 | Dedicated SELinux domain; safe committed-source RPM builds | Yes | Yes | No | `packaging/test_fedora.py` (source contracts + stub-rpmbuild clean-source tests); dirty tree is refused by design, so no RPM from the current uncommitted tree. No package installation |
| M5.02 | Isolated install/upgrade/erase and Plasma/niri validation | No | No | No | Dedicated VM/session evidence required |
| M6.01 | Reproducible benchmark methodology | Documented | No | No | `docs/BENCHMARKING.md` |
| M6.02 | Approved repeated CPU/GPU/combined sustained measurements | No | No | No | No new performance claim |

## M1.13 review fixes (F1–F9)

- F1: Fedora spec, Debian changelog and modules-load comments no longer claim
  a default `/dev/port` backend; `packaging/test_fedora.py` pins spec text to
  the shipped unit (no `--allow-raw-ec` / `GIGACTL_ALLOW_RAW_EC`).
- F2: `gfan`/`gkbd` write guard mirrors the daemon allowlist exactly
  (GIGABYTE G5 GE on BIOS FB06, or G6 KF); `GFAN_UNSAFE`/`GKBD_UNSAFE`
  write bypasses removed; man pages updated; CLI sandbox now fakes DMI.
- F3: `docs/SAFETY.md` states the G6 KF BIOS gap explicitly instead of
  pinning an unrecorded BIOS version.
- F4: `mailbox.pct_to_byte` and `keyboard._check8` raise on out-of-range
  input; `fans.set_duty` validates percent before any write.
- F5: `daemon/gigactld/authz.py` docstring matches the shipped polkit
  policy (`auth_admin` for any/inactive).
- F7: failed suspend handback now has a regression test
  (`test_failed_suspend_handback_latches_recovery_and_blocks_resume`).
- F8: CLI thermal guard treats GPU 0 as unavailable (daemon parity);
  CPU must still read 1..125°C.
- F9: Debian launcher performs the same euid pre-check as the Fedora
  launcher for the `gigactld` entry point.

See [docs/TESTING.md](docs/TESTING.md) for reproducible commands and results.
