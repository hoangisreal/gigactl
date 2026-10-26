# gigactl

**Fan and RGB keyboard backlight control for Gigabyte G5 / G6 gaming laptops on Linux.**

> **Local safety-development branch:** M1 changes are source/fake-tested, not
> hardware-validated or deployed. Do not install this checkout over a working
> controller without reviewing [COMPATIBILITY.md](COMPATIBILITY.md) and the
> [hardware gates](docs/TESTING.md). No new performance improvement is claimed.

Gigabyte ships no Linux software for its G-series laptops. The firmware fan curve is lazy (it holds low fan duty until the CPU throttles at ~96 °C), the keyboard backlight can't be changed, and tools like NBFC, fancontrol and OpenRGB don't support these machines.

`gigactl` fixes that with two small, dependency-light CLI tools built on a reverse-engineered Embedded Controller (EC) protocol — these laptops are Clevo-ODM designs, and the EC speaks a Clevo-style mailbox protocol documented in [docs/PROTOCOL.md](docs/PROTOCOL.md).

| Tool | What it does |
|------|--------------|
| `gfan` | Manual fan speed (per-fan %), live temps + RPM, instant return to firmware auto |
| `gkbd` | Keyboard backlight color (single-zone RGB), brightness, on/off — persists across reboot and suspend |

Both tools check for the `gigactl` daemon first. When it is running they drive it
over D-Bus — no `sudo` or `ec_probe`. A lifetime controller lease is separate from
mailbox transaction locking. Direct writes with no daemon now require explicit
`GIGACTL_ALLOW_DIRECT=1`; direct operations acquire the controller lease and
recheck bus ownership. Unknown bus state is refused. These userspace locks do
**not** arbitrate raw I/O against the kernel EC driver.

## Supported hardware

The following table records **historical protocol observations**, not acceptance
of the new safety implementation. The privileged daemon's verified-platform
gate currently accepts G5 GE / FB06 and G6 KF; it does not inherit the broad
same-family expectation below. New BIOS/backend/session validation is tracked
per feature in [COMPATIBILITY.md](COMPATIBILITY.md).

| Model | Status |
|-------|--------|
| Gigabyte **G6 KF** (i7-13620H / RTX 4060) | ✅ Verified — built and measured on this machine |
| Gigabyte **G5 GE** (i5-12500H / RTX 3050) | ✅ Verified — fan control measured on this machine (BIOS FB06, 2026-10-26) |
| Gigabyte **G5 KF** | 👍 Reported working by a user ([#24](https://github.com/smairio/gigactl/issues/24)) |
| Gigabyte **G5 MF5** | 👍 Reported working by a user ([#25](https://github.com/smairio/gigactl/issues/25)) |
| Other Gigabyte G5 / G6 / G7 (same Clevo platform) | 🟢 Expected to work — untested, please report! |
| Any other Gigabyte laptop | ❌ Refused by the built-in model guard |

**✅ and 👍 are deliberately not the same claim.** ✅ means it was run and
measured here. 👍 means somebody else ran it on their own machine and said it
worked — real evidence, and the only kind obtainable for hardware nobody here
owns, but not a substitute for having tested it. Nothing outside the G6 KF has
been verified by the maintainer, and the table will never say otherwise.

There is a second report on the G5 MF5 that looked like a hardware failure and
was not: the kernel's EC interface had been loaded read-only, so every reading
was correct and every command was refused. `gfan` and the app now say that in
words, and name the command that fixes it, instead of reporting an errno.

The daemon now also checks DMI at the privileged boundary. A fan target remains
**verifying** until readback converges; failed handback remains **unconfirmed**
and is retried independently per fan. Command acknowledgement does not prove
physical firmware ownership. Model-wide banners are not a substitute for this
per-feature evidence.

### Fedora transport limitation

This Fedora kernel lacks ec_sys debugfs. The new daemon therefore refuses
automatic `/dev/port` fallback. Raw access requires the experimental
`--allow-raw-ec` option or root service environment `GIGACTL_ALLOW_RAW_EC=1`,
including for stop-post recovery. **Do not enable it merely to make an error
disappear:** raw reads/writes can race the kernel ACPI EC protocol. A validated
native Clevo transport is still a research gate; none was loaded in this work.

## Install

### The package (recommended)

```bash
sudo apt install ./gigactl_1.2.0_amd64.deb
```

One `.deb` installs everything: the **GigaControl** desktop app, the `gigactld`
daemon that owns EC access, and both CLI tools. The daemon starts immediately and
at every boot; the app appears in your app grid and keeps a tray icon from the
next login. Installation is refused on hardware outside the Gigabyte G5/G6/G7
range (override with `GIGACTL_FORCE_INSTALL=1`).

On a first install the daemon needs `ec_sys`, which only loads at boot: reboot
after installing, or `sudo modprobe ec_sys` followed by
`sudo systemctl restart gigactl.service`. Until then it fails closed rather than
touching raw ports.

`sudo apt remove gigactl` stops the daemon and hands the fans back to the
firmware curve; `sudo apt purge gigactl` also drops the saved profile, curves and
keyboard colour.

To build it yourself:

```bash
sudo apt install debhelper dh-python librsvg2-bin desktop-file-utils
python3 -m pytest packaging -q      # checks debian/ against the source tree
python3 -m pytest cli -q            # drives gfan/gkbd against stub busctl/ec_probe
dpkg-buildpackage -us -uc -b        # the .deb lands in the parent directory
```

Releases are built by CI: pushing a `v*` tag builds the `.deb` on a pinned
ubuntu-24.04 runner and attaches it to the GitHub Release, with the notes taken
from that `debian/changelog` entry. The tag has to match the changelog version —
add the entry first, then `git tag v1.2.0 && git push origin v1.2.0`.

### Fedora (RPM)

```bash
./fedora/build-rpm.sh   # refuses a dirty tree; RPMs land in fedora/RPMS/
sudo dnf install ./fedora/RPMS/gigactl-1.2.0-1.fc44.noarch.rpm
```

The RPM installs the same daemon, app and CLI tools, plus a dedicated
`gigactld_t` SELinux domain. Three differences from the `.deb` matter:

- Installation refuses to run while the daemon is active: stop it and confirm
  the fans are back on firmware first (`sudo systemctl stop gigactl.service`).
  Installing or upgrading never restarts hardware control by itself.
- The service ships disabled: `sudo systemctl enable --now gigactl.service`
  starts it now and at every boot.
- Fedora kernels ship no `ec_sys` interface, so the daemon fails closed until
  an administrator explicitly opts in to raw EC access:

```bash
sudo mkdir -p /etc/systemd/system/gigactl.service.d
printf '[Service]\nEnvironment=GIGACTL_ALLOW_RAW_EC=1\n' |
  sudo tee /etc/systemd/system/gigactl.service.d/raw-ec.conf
sudo systemctl daemon-reload
sudo systemctl restart gigactl.service
```

Raw access races the kernel's own EC driver; the opt-in is deliberate and the
package will not set it for you.

### Tray icon

The tray icon needs a StatusNotifierWatcher host: KDE and most bars provide
one, GNOME needs an AppIndicator extension. With no host at all, a `--tray`
launch shows the window instead of running invisibly; if the host appears
moments later (a slow bar at login), the window hides itself again.

### From a source checkout (CLI only)

```bash
git clone https://github.com/smairio/gigactl
cd gigactl
sudo ./install.sh
```

This is the pre-daemon path: it fetches `ec_probe` (from the excellent
[nbfc-linux](https://github.com/nbfc-linux/nbfc-linux) project) if needed,
installs both tools to `/usr/local/bin`, and sets up a systemd service that
restores your keyboard color at boot and after suspend. The package supersedes
that service with the daemon's own restore — and note that `/usr/local/bin` comes
first on `PATH`, so run `sudo ./install.sh --uninstall` before installing the
package if you have used it.

To uninstall: `sudo ./install.sh --uninstall`

## Usage

### Fans

```bash
gfan              # status: temps, duty %, RPM
gfan watch        # live dashboard (Ctrl-C to quit)
gfan 70           # both fans -> 70%
gfan 40 90        # CPU fan 40%, GPU fan 90%
gfan max          # both fans 100%
gfan auto         # give control back to firmware
```

Software fan control is supervised even in manual mode: a 30% minimum at 70°C,
and 100% emergency requests at CPU 95°C or GPU 87°C. These are conservative
**software thresholds**, not measured firmware limits. `--force` must not bypass
thermal protection. Firmware mode yields to the laptop; failed recovery is
reported, not guaranteed away by a cleanup handler or a reboot claim.

### Keyboard backlight

```bash
gkbd red              # named: red green blue white orange yellow cyan purple pink
gkbd ff6600           # or any hex color
gkbd brightness 40    # 0-100%
gkbd off / gkbd on
gkbd status
```

Your color is saved and re-applied automatically at boot and after suspend.

## How it works (short version)

The EC exposes a command mailbox on the standard ACPI EC ports. Commands are written parameter-first, doorbell-last:

- **Fans**: doorbell `0xC1` — set fan *N* to duty 0–255, or return it to auto. Duty/tach/temperature registers are readable directly.
- **Keyboard**: doorbell `0xCA` — but only after a one-time **master enable**, without which the EC silently ignores every LED command. Colors are sent in **B,R,G order** (not RGB!).

Both quirks cost real debugging time and are documented in full — register maps, byte orders, dead ends and all — in [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Documentation

- [docs/PROTOCOL.md](docs/PROTOCOL.md) — the EC protocol (canonical, measured).
- [PLAN.md](PLAN.md) · [TASKS.md](TASKS.md) — roadmap and implementation/test/hardware gates.
- [RESEARCH.md](RESEARCH.md) · [COMPATIBILITY.md](COMPATIBILITY.md) — findings, unknowns and feature-specific support.
- [docs/HARDWARE.md](docs/HARDWARE.md) — historical machine and measurement record.
- [docs/SAFETY.md](docs/SAFETY.md) · [docs/TESTING.md](docs/TESTING.md) · [docs/BENCHMARKING.md](docs/BENCHMARKING.md) — safety and validation.
- [docs/UPSTREAM.md](docs/UPSTREAM.md) — upstream contribution plan and PR drafts (fork-only, not shipped in packages).
- [daemon/README.md](daemon/README.md) · [gui/README.md](gui/README.md) — developer notes per component.

## Credits

- [nbfc-linux](https://github.com/nbfc-linux/nbfc-linux) — the `ec_probe` utility used for EC access
- [wessel-novacustom/clevo-keyboard](https://github.com/wessel-novacustom/clevo-keyboard) (TUXEDO Computers fork) — driver source that revealed the keyboard master-enable command and B,R,G byte order
- [NovaCustom's Clevo backlight article](https://novacustom.com/clevo-keyboard-backlight-control-for-linux/) — the pointer that cracked the keyboard case

## Disclaimer

This software can write to your laptop's Embedded Controller. Historical tests
do not validate every new implementation, BIOS or backend. Recovery is
best-effort; kernel/firmware failure can prevent it. Read the safety gates first.

## License

MIT
