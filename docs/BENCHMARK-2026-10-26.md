# Sustained benchmark — G5 GE, 2026-10-26

Method: `tools/bench_sustained.sh`, all-core stdlib CPU burn, 120 s per
arm, 60 s cooldown between arms, steady-state = second half (20 samples
of package temp / avg frequency / fan1 duty every 5 s).

| Arm | Package | Avg freq | Fan1 duty |
|---|---|---|---|
| Firmware curve | 61.9 °C | 2149 MHz | 27 % |
| gigactl `performance` | 63.2 °C | 2150 MHz | 65 % |

## What this does and does not show

This CPU-only load is **power-bound, not thermally bound**: the package
sits ~62 °C (nowhere near throttling) at an identical ~2.15 GHz in both
arms, consistent with the 45 W long-term RAPL limit measured in the
Phase-0 probe. When frequency is capped by firmware power limits, fan
duty cannot change performance — the extra fan speed bought nothing
here (the +1.3 °C delta is within run variance).

So the honest claim is narrow: gigactl's profiles change acoustics and
thermal headroom, and on this load class they cost/gain no performance.
The case where cooling should matter — combined CPU+GPU gaming load,
where the stock firmware holds low duty until ~96 °C — is **not**
covered by this test. A GPU-inclusive benchmark (Unigine/MangoHud or a
game capture, ≥3 runs) remains open in TASKS.md T5.1; no sustained-
performance claim is made until it exists.
