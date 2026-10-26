# Testing and evidence

## Safe test environment

Run from the Git root. Never start the application/daemon or run live tools as a
substitute for unit tests. Test EC/sysfs/state/locks/buses must be fakes or
temporary files. Run suites **separately** because their conftest imports overlap.

The system interpreter initially had PyGObject but no pytest. A test-only venv
was created at `/tmp/opencode/gigactl-m1-venv` with system site packages and
pytest 9.1.1; no system packages or running services were changed.

```sh
/tmp/opencode/gigactl-m1-venv/bin/python -m pytest daemon/tests -q
/tmp/opencode/gigactl-m1-venv/bin/python -m pytest gui/tests -q
/tmp/opencode/gigactl-m1-venv/bin/python -m pytest cli -q
/tmp/opencode/gigactl-m1-venv/bin/python -m pytest packaging -q
/tmp/opencode/gigactl-m1-venv/bin/python -m pytest tools/test_probe.py -q
git diff --check
```

## Results ledger — 2026-10-26

- Before M1 core edits: daemon suite **119 passed** (one existing PyGI deprecation warning).
- M1 final, after the M1.13 review fixes F1–F9 (each suite run separately):
  daemon **363 passed**; GUI **158 passed**; CLI **168 passed**;
  tools **40 passed**; packaging **151 passed / 10 failed**.
- All 10 packaging failures are `FileNotFoundError: dpkg-parsechangelog`
  (the tool is absent on this Fedora host; every failing test funnels through
  `changelog_version()` in `packaging/conftest.py`). Environment failure,
  not a product defect.
- `git diff --check` clean. Mutation checks: reverting `pct_to_byte` to
  clamping fails 5 new tests; removing the suspend-handback recovery latch
  fails the F7 regression test.
- **No new hardware validation or benchmark run. No installed service restart.**

## Fault model / acceptance

- Fault every write and every sample/command/readback boundary of a two-fan
  request; independently attempt both auto handbacks.
- Silent command rejection, delayed acknowledgement, stalled/converging duty,
  lost duty after reset, lock contention and bounded EC timeout.
- Failed handback remains pending; recovery retries it; explicit profile,
  curve and manual entrypoints all re-arm subsequent failsafe handling.
- Manual heating, adversarial zero curve, invalid sensors, missing GPU and
  expired telemetry; getters never initiate arbitrary EC transactions.
- Firmware escape before a debounce fires and authorization completing in a
  different order; no writes by D-Bus ownership loser or stale authorization.
- Simulated suspend/reset/resume, malformed state and transient manual settings.
- Fake supervisor/notification/lease recovery only: these do not prove that a
  real systemd watchdog or firmware will recover on physical hardware.

## Hardware gate (not executed)

Approve an exact model/BIOS/backend and rollback procedure first. Confirm transport
arbitration and acknowledgement semantics before writes. Validate normal stop,
SIGKILL under systemd, blocked-loop watchdog, transport failure/recovery and
suspend/resume separately. Stop if handback cannot be confirmed. Do not promise
recovery from kernel/firmware failure or unpowered hardware.

Package lifecycle tests belong in an isolated enforcing Fedora VM with hardware
substituted and startup controlled. A successful rpmbuild is not lifecycle or
hardware evidence. Independent review is required before M1 software completion.
