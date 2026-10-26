# Safety requirements and approval gates

**Source changes are not authorization to operate hardware.** This development
run uses fake EC/sysfs/D-Bus devices and does not install, restart or replace the
running controller. No power setter, driver load or benchmark is authorized.

## Boundary and ownership

- Check exact verified vendor/model/BIOS at the privileged EC boundary, including
  recovery tools and stop-post. Unsupported or unreadable identity fails closed.
  The allowlist is exactly: GIGABYTE G5 GE pinned to BIOS FB06, and GIGABYTE
  G6 KF by model only — no G6 KF BIOS version was recorded in the evidence, so
  that model's BIOS coverage is an explicit known limitation, not a claim.
- Acquire the lifetime controller lease before creating a real EC, and retain
  it through recovery. Mailbox locking is a different, shorter-lived lock.
- Begin service control only after owning the D-Bus name; recheck ownership and
  suspend state after asynchronous authorization. Invalidate superseded fan work.
- Raw-port access requires explicit opt-in. Its reads send I/O protocol commands;
  userspace locks cannot arbitrate them against the kernel ACPI EC driver.
  No amount of plausible readback is proof that this race is eliminated.
- Never overwrite a busy mailbox; bound locks, transport waits and FCMD
  consumption acknowledgement. Do not mask invalid offsets/values into range.

## Thermal and fan state

- Firmware mode yields to hardware. All **software-controlled** modes, including
  manual duty, must remain thermally supervised on subsequent samples.
- Conservative software policy: at least 30% at 70°C, emergency requests of
  100% at CPU 95°C or GPU 87°C. These are policy thresholds, not discovered
  firmware critical limits or proof of physical cooling effectiveness.
- Invalid CPU/GPU data cannot justify low duty. GPU zero means unavailable or
  asleep, not zero degrees; use valid CPU data conservatively for cooling.
- A requested duty is not an applied duty. Readback is reconciled even when
  temperature/hysteresis would otherwise suppress another write. Ramps have
  progress and absolute deadlines; retries never extend deadlines forever.
- Two physical fan writes are **not atomic**. On failure, stop the software
  policy and independently request firmware-auto for both fans. A failure on
  one fan must not prevent trying the other.
- Failed handback stays pending and is retried. “Command acknowledged” is not a
  measured physical mode. Never report “handed back” after swallowing errors.
- Recovery stays latched until an explicit valid fan request; profile, curve and
  manual entrypoints all re-arm consistently. Persist the latch when possible.

## Lifecycle

- A heartbeat on the **control event loop**, not a worker thread, allows systemd
  to detect a blocked controller. EC/lock/polkit operations have separate bounds.
- SIGKILL cannot execute Python cleanup. systemd stop-post is an independent
  best-effort recovery path after the old process/lease exits. Process/lease and
  fake-supervisor tests are not a real hardware watchdog test.
- Before suspend, request handback; stop polling/control while suspended. On
  resume invalidate old applied-state caches and require fresh sensor data.
  Transient manual duty must not silently resume.
- Validate the entire saved snapshot before optional keyboard/turbo writes;
  schedule safety before restoration. Unrelated saves must not turn transient
  manual state into a durable fan policy.
- A kernel/firmware failure, inaccessible EC, power loss or failed recovery
  process can prevent restoration. Reboot is not an unconditional safety proof.

## Confinement and packaging

Use a dedicated SELinux domain, root-owned runtime/state directories and explicit
scriptlet dependencies. Do not grant all `init_t` processes raw-memory access.
Never bundle arbitrary untracked developer files into distributable source RPMs.
Build/package/test success is not install/upgrade/erase validation.

## Gates requiring separate approval

1. **Transport:** exact firmware/backend identity and reviewed kernel
   synchronization; audit initialization writes before loading any driver.
2. **Controlled fan/RGB operation:** bounded procedure, valid temperatures,
   physical observation and an independently available recovery path.
3. **Lifecycle:** clean stop, kill, hung loop, failed transport and suspend/resume
   tested separately. Stop on unresolved handback; do not proceed to soak.
4. **Power controls:** proven setter support and a reviewed combined budget, not
   merely `[min,max]` metadata. No MSR/undervolt bypass or firmware flashing.
5. **Sustained workloads:** approved load/duration/abort conditions and repeated
   measurement protocol from [BENCHMARKING.md](BENCHMARKING.md).

Current implementation/unit/hardware status is tracked in [../TASKS.md](../TASKS.md)
and [../COMPATIBILITY.md](../COMPATIBILITY.md), not inferred from old completion labels.
