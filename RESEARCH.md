# Research ledger

## Evidence vocabulary

- **Verified-source / Unit-tested:** source inspection or a named fake-backed
  test. Neither is a live hardware observation.
- **Verified-historical:** an explicitly dated measurement already recorded in
  this checkout, bounded by its model/BIOS/kernel/test procedure.
- **Inferred:** a hypothesis supported by evidence, not compatibility acceptance.
- **Unknown:** missing evidence; do not turn it into an unsupported/locked claim.

## Audit baseline (2026-10-26)

Clean local master `e3b7355`; upstream `7c1a34b`; eight fork commits. Current
read-only observations identified G5 GE / FB06, Fedora 44 kernel 7.2.11,
**niri/Wayland**, active TuneD/tuned-ppd, turbo disabled and NVIDIA PCI runtime
suspended. This does not disprove historical Plasma testing, or establish the
power-policy state during old measurements.

The source installed on the host is not inferred from the repository version:
the observed process domain was `gigactld_t`, while baseline master still shipped
the broad `init_t` policy. Existing artifacts must not be overwritten or deployed
as part of source tests.

## Findings driving M1

The audit demonstrated queued-curve firmware escape reversal; unsupervised manual
mode; partial application without exception rollback; swallowed/one-shot failed
handback; curve re-arm omission; unverified curve target caches; writes before
D-Bus ownership; absent daemon DMI guarding; blocking locks/authorization; missing
hang watchdog; unvalidated saved state; and incomplete suspend reconciliation.
The new tests exercise these with fake devices, not the installed daemon.

Selective reuse candidates:

- `pr/daemon-failsafe`, commit `9c9c3b5`: retry failed handback. Reuse the idea and
  regression, but also fix truthful status, all entrypoints and per-fan failures.
- `pr/fedora-packaging`, commit `13ccaa9`: dedicated SELinux domain. Preserve
  master version metadata, review new runtime lease access and scriptlet errors.
- Upstream [PR #35](https://github.com/smairio/gigactl/pull/35): RGB effects/idle
  helpers. Raw input reads, notification monitoring and high-rate EC writes need
  privacy, confinement and control-loop budget review before integration.

## Corrected hardware/API assumptions

- Raw-port reads send RD_EC/address I/O commands. They are not EC RAM-setting
  writes, but can interleave with the kernel EC protocol. Advisory userspace
  locks and readback do **not** eliminate this race.
- PL1 `constraint_0_max_power_uw=45000000` is not proof of a BIOS lock. Linux
  [maps PL1 maximum to THERMAL_SPEC_POWER](https://github.com/torvalds/linux/blob/v7.2/drivers/powercap/intel_rapl_common.c#L102-L146).
  Missing/zero bounds are unusable, not permission to tune.
- An NVML maximum of 75 W does not establish setter support, sustained 75 W or a
  safe combined CPU/GPU budget. No setters were exercised in this work.
- The historical probe's **749.67 W instantaneous GPU power** is an invalid
  outlier for consumption/performance conclusions. Preserve the raw record;
  do not invent a corrected value or diagnose its cause without evidence.
- Missing charge thresholds/platform-profile nodes means not currently exposed,
  not necessarily physically impossible.
- Current TUXEDO source has Alder Lake compatibility exemptions. The blanket
  claim that branding blocks every GE path is invalid. Native initialization
  may write LEDs/events/charger settings, so loading the suite is not read-only.
  [Pinned compatibility source](https://github.com/tuxedocomputers/tuxedo-drivers/blob/a929f9ce143316b3b18d5f3fbf9cc54f0c1122e2/src/tuxedo_compatibility_check/tuxedo_compatibility_check.c).
- Stock Fedora here has no ec_sys debugfs. An audited native Clevo ACPI/WMI route
  is a candidate, not a verified substitute. Respect GPL driver licensing instead
  of copying implementations into MIT userspace.

## REA/Ghidra gate

No shipped Windows artifact was supplied or executed. Source/native Linux APIs
remain the first route. If OEM hotkeys/performance modes remain unexplained,
identify the correct legal OEM package, version/hash and exact gap; inspect
managed assemblies or focused native IOCTL/WMI code. Do not download an unrelated
latest GCC package, load its driver, flash firmware or guess EC commands.

## Open questions

1. Does FB06's AML serialize the relevant native Clevo methods safely?
2. Can an existing narrowly scoped driver expose them without unrelated probe writes?
3. Do watchdog kill, stop-post and handback work on this exact firmware/backend?
4. What does each GPU sensor report during RTD3, and what monitoring wakes it?
5. What limits real throughput: turbo policy, RAPL windows, temperature or workload?
6. What behavior survives suspend, power transitions and external policy changes?

None of these is resolved by a passing fake test or by stable-looking EC samples.
