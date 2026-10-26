# gigactld

The **GigaControl daemon** — the single root process that owns Embedded
Controller access on Gigabyte G5/G6 laptops and exposes it over the D-Bus
**system** bus. See [`docs/PROTOCOL.md`](../docs/PROTOCOL.md) for the EC protocol
and issue #7 for the API design.

## Status

The daemon now includes fan/keyboard/turbo controls and an M1 safety candidate:

- Guarded EC access (`ec_sys`, experimental opt-in raw ports, bounded transactions)
- Lifetime controller ownership, separate from mailbox locking
- Conservative thermal supervision for curve and manual modes
- Requested-versus-observed fan reconciliation and independently retried handback
- Async authorization, superseded fan requests, control-loop watchdog integration
- Cached valid telemetry, saved-state validation and suspend/resume reconciliation
- Telemetry decode (CPU/GPU temp, fan RPM, duty) — `telemetry.py`
- D-Bus system service emitting a `Telemetry(uuuuuu)` signal every 2 s — `service.py`

See [../TASKS.md](../TASKS.md) for implementation, unit-test and hardware gates.
These changes have **not** been deployed or hardware-validated.

## Run (dev)

The commands below are live-system operations, **not unit tests**. They require
separate approval and an audited exact platform/backend. Do not use raw-port
opt-in to silently bypass an unavailable native transport.

```bash
# one-time: let root own the bus name
sudo install -m644 data/io.github.smairio.gigactl.conf /etc/dbus-1/system.d/
sudo systemctl reload dbus

# run in the foreground
sudo python3 -m gigactld

# watch the telemetry signal from another terminal
dbus-monitor --system "interface='io.github.smairio.gigactl.Control'"
# or: busctl introspect io.github.smairio.gigactl /io/github/smairio/gigactl
```

## Test

```bash
cd daemon && python3 -m pytest -q
```

Tests use temporary files, emulated ports and fake buses/authorizers. Historical
G6 KF/GE protocol measurements do not validate the new lifecycle, acknowledgement
and recovery implementation. See [../docs/TESTING.md](../docs/TESTING.md).
