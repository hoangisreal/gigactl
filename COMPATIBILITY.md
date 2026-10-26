# Compatibility — feature-specific, not a blanket model badge

| Feature / target | Implementation evidence | Hardware evidence | Limitations |
|---|---|---|---|
| G5 GE / BIOS FB06 fan protocol | Existing mailbox protocol + M1 reconciliation | Historical short per-fan measurements in `docs/PROTOCOL.md` | New safety/lifecycle code has **not** been exercised on hardware |
| G5 GE / FB06 RGB | Existing one-zone protocol | Historical visual colour test | No new colour/suspend test; no valid LED mirror readback |
| G6 KF | Existing upstream verified model | Upstream historical observations | No device here; BIOS-specific coverage not established |
| Other G5/G6/G7 / missing DMI / other GE BIOS | Not accepted by new privileged verified-platform gate | None for this gate | Refuse rather than inherit a family-wide compatibility assumption |
| ec_sys | Kernel-mediated transport where present | Not available in this Fedora kernel | Read-only ec_sys remains read-only; no bypass fallback |
| Raw `/dev/port` | Experimental explicit opt-in only | Old short GE tests | Kernel arbitration race remains; opt-in does not prove safety |
| M1 thermal/rollback/retry/watchdog | Fake-backed software implementation in progress | **Not hardware-validated** | No kernel/firmware failure guarantee; see testing gates |
| Telemetry CPU/GPU | Cached sample validity/age; GPU zero distinguished | Current source tests only | Cannot prove a plausible but stale firmware value is current |
| CPU turbo | Existing API; external-state notifications added | Historical toggle record only | Temporary-file tests do not validate TuneD transitions |
| CPU RAPL / NVIDIA power setters | Not enabled by M1 | Not tested | Advertised bounds do not establish authorization or safe operating budget |
| Battery health | Available through native UPower/sysfs | Historical capacity data | Threshold writes not exposed by current host interfaces |
| OS power profiles | TuneD/tuned-ppd integration planned | Provider observed running | Absence of firmware platform-profile does not remove OS profiles |
| Plasma tray | Existing StatusNotifier implementation | Historical visual report | Needs new dated Plasma-session validation |
| niri/Wayland | Current observed session | No new GUI runtime test | Test separately from KDE edition/package identity |
| Fn/RGB idle/effects | Research/reuse candidates | Not tested on GE | No raw-input privileges or speculative firmware commands added automatically |

## Approval boundaries

Software changes and fake tests do not authorize device writes, package install,
service restart, driver loading, power tuning or sustained hardware workloads.
Native transport, crash/hang recovery, suspend and performance validation each
need explicit approved procedures and their own dated evidence.
