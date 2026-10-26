#!/bin/bash
# probe.sh — telemetry probe with no intentional EC register configuration writes.
#
# DMI, kernel/module state, sysfs listings, sensors and EC telemetry are read.
# EC access always uses ec_read.py's model guard and owner lease. Native ec_sys
# is required by default; --allow-raw is explicit experimental opt-in when it
# is absent. Raw reads send I/O command/address writes and may race the kernel.
# Offsets are restricted in ec_read.py to those documented in docs/PROTOCOL.md.
#
# Usage: ./tools/probe.sh [--allow-raw] [output-file]
# Existing evidence output files are never overwritten.
set -euo pipefail

OUT=""
EC_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --allow-raw) EC_ARGS=(--allow-raw) ;;
    --help|-h) echo 'Usage: probe.sh [--allow-raw] [output-file]'; exit 0 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *)
      if [ -n "$OUT" ]; then echo 'only one output-file is allowed' >&2; exit 2; fi
      OUT=$arg
      ;;
  esac
done
if [ -n "$OUT" ]; then
  mkdir -p "$(dirname "$OUT")"
  # Exclusive fd creation also refuses device/FIFO/symlink destinations. Bash
  # noclobber alone protects regular files, but is not sufficient for devices.
  if ! command -v python3 >/dev/null; then
    echo 'python3 is required for safe exclusive evidence-file creation' >&2
    exit 2
  fi
  exec python3 - "$OUT" "$0" "${EC_ARGS[@]}" <<'PY'
import os
import sys

output, script, *args = sys.argv[1:]
try:
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                 | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
except OSError as exc:
    print(f"probe: cannot create new evidence file {output!r}: {exc}", file=sys.stderr)
    raise SystemExit(2)
os.dup2(fd, 1, inheritable=True)
os.dup2(fd, 2, inheritable=True)
if fd > 2:
    os.close(fd)
os.execv("/bin/bash", ["bash", script, *args])
PY
fi

section() { printf '\n== %s ==\n' "$*"; }
kv() { printf '%s: %s\n' "$1" "$(cat "$2" 2>/dev/null || echo '<unreadable>')"; }

section "identity"
printf 'date: %s\n' "$(date -Is)"
printf 'kernel: %s\n' "$(uname -r)"
printf 'uid: %s\n' "$(id -u)"
for f in sys_vendor product_name product_version bios_version bios_date board_name; do
  kv "dmi.$f" "/sys/devices/virtual/dmi/id/$f"
done
if command -v dmidecode >/dev/null && [ "$(id -u)" = 0 ]; then
  dmidecode -t system 2>/dev/null | sed -n '1,20p' | sed -E 's/(Serial Number|UUID):.*/\1: <redacted>/' || true
fi

section "modules"
lsmod 2>/dev/null | grep -iE '^(ec_sys|acpi_ec|clevo|tuxedo|gigabyte_wmi|wmi)' || echo '<none of ec_sys/clevo/tuxedo/gigabyte_wmi loaded>'
kv "ec_sys.write_support" /sys/module/ec_sys/parameters/write_support

section "sysfs surfaces"
ls /sys/class/hwmon/ 2>/dev/null || echo '<no hwmon>'
for d in /sys/class/hwmon/hwmon*; do
  if [ -e "$d/name" ]; then printf 'hwmon %s: %s\n' "$d" "$(cat "$d/name")"; fi
done
ls -d /sys/firmware/acpi/platform_profile* 2>/dev/null || echo '<no platform_profile>'
ls /sys/class/powercap/ 2>/dev/null || echo '<no powercap>'
for c in /sys/class/powercap/intel-rapl:0/constraint_*_name /sys/class/powercap/intel-rapl:0/constraint_*_power_limit_uw /sys/class/powercap/intel-rapl:0/constraint_*_max_power_uw; do
  if [ -e "$c" ]; then printf '%s: %s\n' "$c" "$(cat "$c" 2>/dev/null)"; fi
done
ls /sys/class/power_supply/ 2>/dev/null || echo '<no power_supply>'
for b in /sys/class/power_supply/BAT*; do
  [ -e "$b" ] || continue
  printf 'battery %s\n' "$b"; ls "$b" | grep -E 'charge_control|charge_behaviour' || echo '  <no charge_control_* files>'
done

section "sensors (read-only)"
command -v sensors >/dev/null && sensors 2>/dev/null | sed -n '1,40p' || echo '<lm-sensors not available>'

section "nvidia (read-only)"
if command -v nvidia-smi >/dev/null; then
  nvidia-smi --query-gpu=name,power.default_limit,power.min_limit,power.max_limit,temperature.gpu --format=csv 2>&1 || true
  nvidia-smi -q -d POWER 2>/dev/null | grep -iE 'power limit|power draw|throttle' | sed -n '1,15p' || true
else echo '<nvidia-smi not available>'; fi

section "upower (read-only)"
command -v upower >/dev/null && upower -i "$(upower -e 2>/dev/null | grep -i bat | head -n1)" 2>/dev/null | grep -iE 'state|percentage|energy|charge' | sed -n '1,12p' || echo '<upower/battery not available>'

section "ec telemetry (root only, guarded, native interface by default)"
if [ "$(id -u)" != 0 ]; then
  echo '<skipped: re-run with sudo to read EC bytes>'
elif command -v python3 >/dev/null; then
  SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
  python3 "$SCRIPT_DIR/ec_read.py" "${EC_ARGS[@]}" || echo '<guarded EC telemetry unavailable; no automatic raw fallback>'
else
  echo '<python3 unavailable: guarded EC section skipped>'
fi

section "done"
echo 'probe complete (no intentional EC register configuration writes; raw reads, if opted in, issue I/O commands)'
