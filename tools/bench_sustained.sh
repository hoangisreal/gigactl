#!/bin/bash
# Sustained-performance comparison: firmware curve vs gigactl performance.
#
#   ./tools/bench_sustained.sh [seconds-per-arm]     (default 120)
#
# For each arm: select the profile through the running daemon, burn all
# CPU cores, and sample package temperature + average frequency + fan
# duty every 5s. The summary uses only the second half of each arm
# (steady state). Ends on the firmware profile whatever happens.
set -uo pipefail
SECS=${1:-120}
BUS=io.github.smairio.gigactl
OBJ=/io/github/smairio/gigactl
IFACE=io.github.smairio.gigactl.Control

set_profile() {
  busctl --system call $BUS $OBJ $IFACE SetProfile s "$1" >/dev/null 2>&1 \
    || { echo "could not SetProfile $1 (daemon running?)"; return 1; }
}
pkg_temp() { # coretemp package, whole degrees
  for h in /sys/class/hwmon/hwmon*; do
    [ "$(cat $h/name 2>/dev/null)" = coretemp ] && { awk '{printf "%d", $1/1000}' $h/temp1_input; return; }
  done; echo -1
}
avg_mhz() { awk -F: '/cpu MHz/{s+=$2; n++} END {if (n) printf "%d", s/n}' /proc/cpuinfo; }
fan_duty() { gfan 2>/dev/null | awk -F'[ %]+' '/Fan1/{print $4}'; }

trap 'set_profile firmware >/dev/null 2>&1' EXIT
for arm in firmware performance; do
  echo "=== arm: $arm (${SECS}s burn) ==="
  set_profile "$arm" || exit 1
  sleep 5
  python3 "$(dirname "$0")/cpu_burn.py" "$SECS" & BURN=$!
  : > "/tmp/bench-$arm.tsv"
  for ((i=0; i<SECS/5; i++)); do
    sleep 5
    kill -0 $BURN 2>/dev/null || break
    printf '%s\t%s\t%s\n' "$(pkg_temp)" "$(avg_mhz)" "$(fan_duty)" >> "/tmp/bench-$arm.tsv"
  done
  wait $BURN 2>/dev/null
  awk -F'\t' 'NR>='\"$((SECS/10))\"' {t+=$1; f+=$2; d+=$3; n++}
    END {if (n) printf "steady-state (%d samples): package %.1f°C, avg %.0f MHz, fan1 %.0f%%\n", n, t/n, f/n, d/n}' \
    "/tmp/bench-$arm.tsv"
  echo "cooling down 60s..."; sleep 60
done
echo "done — full samples in /tmp/bench-firmware.tsv and /tmp/bench-performance.tsv"
