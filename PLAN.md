# gigactl — G5 GE Linux Control Center

Target: the existing local fork, Gigabyte G5 GE / BIOS FB06 / Fedora KDE.
Keep GTK4, D-Bus and the privileged Python daemon. No upstream publication,
firmware flashing, live EC writes, service restart or power tuning is authorized
by software implementation/testing alone.

## Milestones

| Milestone | Dependency | Deliverable | Current status |
|---|---|---|---|
| M1 Safety foundation | — | Exclusive controller, privileged guards, thermal supervision, verified fan intent, recoverable partial application, bounded I/O, asynchronous authorization, hang watchdog, suspend reconciliation | Software-complete: implemented + fake-tested + independent review (PASS-WITH-FIXES, fixes landed); **not hardware-validated** |
| M2 Capabilities/monitoring | M1 review | Honest per-feature capabilities, sample validity/age, effective power policy, sleep-aware telemetry, redacted diagnostics | Cache/status and external turbo observation done with M1; capability UI + redacted diagnostic export (M2.03) pending |
| M3 Profiles/automation | M1, M2 | Separate fan/OS profiles, desktop power-provider integration, explicit manual precedence and per-game holds | Pending |
| M4 Optional controls/UX | Relevant M1–M3 capabilities | Audited native Clevo transport, RGB conveniences, hotkeys, optional bounded power controls | Research only; writes gated |
| M5 Fedora release engineering | M1; applicable features | Clean source archives, confined SELinux domain, isolated package lifecycle/CI and real Plasma validation | Source contracts + clean-source build tests pass; dirty tree refused by design; no install/release |
| M6 Sustained optimization | Safe transport + approved hardware gates | Repeatable CPU/GPU/combined throughput, energy, temperature and frame-time comparisons | Protocol only; no new workload run |

## Decisions

- Fix the privileged boundary; GUI/CLI guards alone cannot protect hardware.
- One **lifetime controller lease**, separate from mailbox transaction locking.
- Never equate a requested duty, an acknowledged command, a duty readback and
  physical firmware ownership. Failed handback remains unresolved and retryable.
- A raw EC *read* also sends I/O protocol commands. Userspace flock cannot
  arbitrate against the kernel ACPI EC driver. Raw ports require explicit opt-in;
  they are not an automatically selected equivalent to kernel access.
- Thermal thresholds are conservative software policy, not discovered firmware
  limits. Firmware mode means yield; manual and curve modes remain supervised.
- Reuse reviewed changes from local PR branches selectively. Never replace
  master with another branch's older service or version metadata.
- Keep TuneD/PowerDevil as OS policy owner. Observe external changes instead of
  continuously overwriting them.
- No speculative undervolt, battery command, EC register or firmware operation.

## Definition of done

Track **Implemented / Unit-tested / Hardware-validated** independently in
[TASKS.md](TASKS.md). A milestone needs regression tests, independent review,
documented limitations and evidence appropriate to its claims. Unit tests never
establish hardware compatibility. Physical tests require separate approval.

Canonical documents live inside this Git checkout. Existing planning documents
outside it are historical user work and are not overwritten. Protocol mechanics
remain in [docs/PROTOCOL.md](docs/PROTOCOL.md); old measurements remain intact.
