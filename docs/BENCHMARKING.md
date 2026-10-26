# Sustained-performance validation protocol

**No new benchmarks were run.** Existing `tools/bench_sustained.sh` is a legacy
CPU soak helper, not a validated performance harness. Do not execute it as part
of source tests; it changes profiles and starts a workload.

## Historical result scope

The recorded 120-second CPU-only arms showed similar arithmetic-average reported
MHz. They did not measure completed work, package power or limiting causes.
Therefore neither performance equivalence nor “PL1-limited at 45 W” is established.
The saved GPU power outlier is excluded from conclusions; raw evidence stays intact.

## Required future experiment

1. Obtain separate approval for workload, duration, profile/backend changes,
   sensor collection, abort thresholds and firmware-auto escape.
2. Record source commit, package hash, DMI model/BIOS (no serial/UUID), kernel,
   NVIDIA version, actual desktop/tray host, AC/battery, turbo, per-policy
   governor/EPP, both RAPL interfaces/windows, GPU cap and power-policy owner.
3. Compare CPU-only throughput, GPU-only workload and a repeatable combined
   workload/game. Use identical versions/settings; warm up to measured thermal
   equilibrium. Counterbalance order and repeat each arm at least three times.
4. Record throughput, frame-time distributions/1% lows, temperatures, fan duty/RPM,
   effective busy frequency by P-/E-core class, energy/power and valid throttle
   evidence. Handle energy-counter wrap, unavailable values and sampling overhead.
5. Verify telemetry itself does not defeat GPU runtime suspend. NVIDIA polling
   and overlay collection must be measured, not assumed free.
6. Terminate/reap workload process groups on interruption; independently attempt
   bounded restoration and report failures. Avoid predictable global `/tmp` outputs.
7. Publish raw samples, validity exclusions, per-run metadata and uncertainty.
   Compare equal-performance noise/energy as well as maximum throughput. No
   unmeasured percentage claim or “more fan = more performance” assumption.

Do not raise CPU/GPU limits to their advertised maximum as a baseline. Reported
bounds are not proof of a safe combined electrical/thermal budget or setter support.
