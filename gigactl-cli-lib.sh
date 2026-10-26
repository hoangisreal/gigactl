# gigactl-cli-lib.sh — the plumbing gfan and gkbd share.
#
# Sourced, not executed. The caller sets TOOL (its own name, for messages) and
# `set -euo pipefail` before sourcing. The file lives next to the scripts in a
# checkout and in /usr/lib/gigactl once packaged; each script resolves it in that
# order. It exists because this plumbing drifted the moment it was duplicated —
# gfan grew a prop() helper while gkbd inlined the identical busctl call, within
# a single commit.

BUS=io.github.smairio.gigactl
OBJ=/io/github/smairio/gigactl
IFACE=$BUS.Control

# Overridable only so cli/ can exercise both modes as an ordinary user in a tmp
# directory; the defaults are the real locations. LOCK is the same path the
# daemon's ec.py takes — a different one would serialise nothing.
LOCK=${GIGACTL_EC_LOCK:-/run/lock/gigactl-ec.lock}
LOCK_WAIT=${GIGACTL_LOCK_WAIT:-10}
# The daemon holds this *different* lock for its entire process lifetime, before
# taking the mailbox lock. Keep that order in direct clients too, including
# reads: a /dev/port read is itself a multi-step controller transaction.
OWNER_LOCK=${GIGACTL_OWNER_LOCK:-${OWNER_LOCK:-/run/gigactl/owner.lock}}
OWNER_LOCK_WAIT=${GIGACTL_OWNER_LOCK_WAIT:-$LOCK_WAIT}

die() { echo "$TOOL: $*" >&2; exit 1; }

# --- which mode ---------------------------------------------------------------

# Sets MODE to daemon or direct. Anything short of a definite "the daemon is not
# running" refuses instead of falling back: the daemon is the single writer of
# the EC, and "could not tell" must never quietly become "wrote behind its back".
detect_mode() {
  command -v busctl >/dev/null 2>&1 || \
    die "busctl not found, so there is no way to tell whether the gigactl daemon is running — refusing to touch the EC behind its back. (busctl ships with systemd.)"
  local answer
  if ! answer=$(busctl --system call org.freedesktop.DBus /org/freedesktop/DBus \
                  org.freedesktop.DBus NameHasOwner s "$BUS" 2>&1); then
    die "cannot tell whether the gigactl daemon is running ($answer) — refusing to touch the EC behind its back."
  fi
  case "$answer" in
    "b true")  MODE=daemon ;;
    "b false") MODE=direct ;;
    *) die "cannot tell whether the gigactl daemon is running (busctl answered: $answer) — refusing to touch the EC behind its back." ;;
  esac
}

# Both modes implement the same verbs as <mode>_<verb>; this is the only place
# the choice is acted on, so no command can reach for the wrong backend.
op() { "${MODE}_$1" "${@:2}"; }

# --- daemon mode ----------------------------------------------------------------

# Call a control method. On failure CALL_ERROR holds the daemon's message and the
# return is non-zero — for the one caller (gfan's split-duty path) that has a
# recovery of its own. Everything else uses call(), which dies.
try_call() {
  local method=$1; shift
  if CALL_ERROR=$(busctl --system call "$BUS" "$OBJ" "$IFACE" "$method" "$@" 2>&1); then
    return 0
  fi
  CALL_ERROR=${CALL_ERROR#Call failed: }
  return 1
}

call() {
  try_call "$@" && return 0
  case "${CALL_ERROR,,}" in
    *authoriz*|*authenticat*)
      # polkit lets an active local session do this with no password; a remote
      # (SSH) session has no polkit agent to ask — but root is authorized
      # outright, so sudo is the remedy there.
      die "the daemon refused $1: $CALL_ERROR (from SSH or another remote session, re-run with sudo)" ;;
    *)
      die "the daemon refused $1: $CALL_ERROR" ;;
  esac
}

prop() { busctl --system get-property "$BUS" "$OBJ" "$IFACE" "$1" 2>/dev/null; }

# Strip busctl's type prefix and quotes from a string property.
unquote_s() { local v=${1#s \"}; echo "${v%\"}"; }

warn() { echo "$TOOL: warning: $*" >&2; }

# Say so when the daemon is being told things the Embedded Controller is not
# doing. EcStatus is empty while writes land and one plain sentence when they do
# not, so there is nothing to phrase here — the daemon already said it.
#
# A daemon too old to know the property fails the read, which is not evidence of
# a problem: an older daemon is silent, not broken.
warn_if_ec_stuck() {
  local status
  status=$(prop EcStatus) || return 0
  status=$(unquote_s "$status")
  [ -n "$status" ] || return 0
  warn "$status"
}

# --- direct mode ----------------------------------------------------------------

# /run/gigactl is deliberately not /run/lock: an unprivileged process must not
# be able to replace the lifetime lock with a different inode. Explicit test
# overrides may use a private directory owned by the invoking uid instead.
lock_owner() {
  local dir uid mode links old_umask
  dir=$(dirname -- "$OWNER_LOCK")
  if [ "$OWNER_LOCK" = /run/gigactl/owner.lock ]; then
    [ -d /run ] && [ ! -L /run ] || die "unsafe controller owner parent /run"
    uid=$(stat -c %u -- /run) || die "cannot inspect controller owner parent /run"
    mode=$(stat -c %a -- /run) || die "cannot inspect controller owner parent /run"
    [ "$uid" -eq 0 ] && (( (8#$mode & 0022) == 0 )) || die "unsafe controller owner parent /run"
    if [ ! -e "$dir" ] && [ ! -L "$dir" ]; then
      mkdir -m 0755 -- "$dir" || [ -d "$dir" ] || die "cannot create controller owner directory $dir"
    fi
  fi
  [ -d "$dir" ] && [ ! -L "$dir" ] || die "unsafe controller owner directory $dir"
  uid=$(stat -c %u -- "$dir") || die "cannot inspect controller owner directory $dir"
  mode=$(stat -c %a -- "$dir") || die "cannot inspect controller owner directory $dir"
  if [ "$OWNER_LOCK" = /run/gigactl/owner.lock ]; then
    [ "$uid" -eq 0 ] || die "controller owner directory $dir must be root-owned"
  else
    [ "$uid" -eq "$EUID" ] || die "controller owner directory $dir is not owned by this user"
  fi
  (( (8#$mode & 0022) == 0 )) || die "unsafe writable controller owner directory $dir"
  if [ -e "$OWNER_LOCK" ] || [ -L "$OWNER_LOCK" ]; then
    [ -f "$OWNER_LOCK" ] && [ ! -L "$OWNER_LOCK" ] || die "unsafe controller owner lock $OWNER_LOCK"
  fi
  old_umask=$(umask)
  umask 077
  if ! exec 8>>"$OWNER_LOCK"; then
    umask "$old_umask"
    die "cannot open controller owner lock $OWNER_LOCK"
  fi
  umask "$old_umask"
  [ "$OWNER_LOCK" -ef "/proc/$$/fd/8" ] && [ -f "/proc/$$/fd/8" ] || \
    die "controller owner lock changed while opening $OWNER_LOCK"
  uid=$(stat -Lc %u "/proc/$$/fd/8") || die "cannot inspect controller owner lock $OWNER_LOCK"
  mode=$(stat -Lc %a "/proc/$$/fd/8") || die "cannot inspect controller owner lock $OWNER_LOCK"
  links=$(stat -Lc %h "/proc/$$/fd/8") || die "cannot inspect controller owner lock $OWNER_LOCK"
  [ "$uid" -eq "$EUID" ] && [ "$links" -eq 1 ] && (( (8#$mode & 0022) == 0 )) || \
    die "unsafe ownership, permissions or links on controller owner lock $OWNER_LOCK"
  flock -w "$OWNER_LOCK_WAIT" 8 || \
    die "controller owner lease $OWNER_LOCK is busy — the daemon or another direct client owns the controller; no direct fallback."
}

# Hold the mailbox lock as well as the lifetime lease for the complete direct
# operation, not just a write: thermal checks and read-back belong inside it.
lock_ec() {
  exec 9>"$LOCK" || die "cannot open the EC lock $LOCK"
  flock -w "$LOCK_WAIT" 9 || \
    die "another gigactl EC writer is holding the lock $LOCK — try again."
}

prepare_direct() { # $1=write (1) or read (0), remaining args for sudo re-exec
  local write=$1; shift
  if [ "$write" -eq 1 ] && [ "${GIGACTL_ALLOW_DIRECT:-0}" != 1 ]; then
    die "direct EC writes are disabled by default. Use the gigactl daemon, or explicitly opt in with GIGACTL_ALLOW_DIRECT=1 (no daemon watchdog in direct mode)."
  fi
  [ -x "$EP" ] || die "ec_probe not found (nbfc-linux package), and the gigactl daemon is not running"
  if [ "$(id -u)" -ne 0 ]; then
    if [ "$write" -eq 1 ]; then
      exec sudo env GIGACTL_ALLOW_DIRECT=1 "$0" "$@"
    fi
    exec sudo "$0" "$@"
  fi
  lock_owner
  lock_ec
  # NameHasOwner before the wait is only a hint. A daemon may have started
  # meanwhile; an unavailable/ambiguous bus here must still fail closed.
  detect_mode
  [ "$MODE" = direct ] || \
    die "the gigactl daemon became active while waiting for the controller locks — refusing direct EC access; retry through the daemon."
}
