# Upstream PR drafts (not yet submitted — for maintainer review)

Conventions observed upstream: no CONTRIBUTING.md; MIT license; work is
issue/ticket-driven (commits reference issues, e.g. `closes #30`); the
support table's Verified/Reported distinction is a stated honesty rule
(PRODUCT.md #3); release CI runs the packaging + test suites on
ubuntu-24.04. Open context: PR #35 (keyboard lighting, another fork),
issue #34 (distro support request), issue #24/#33 (G5 reports).

Recommended submission order: PR1 → PR2 → PR3 → PR4 (independent
branches off `upstream/master`; PR4's spec describes the daemon as of
its own branch).

---

## PR1 — branch `pr/g5-ge-verified` (3 commits)

**Title:** Verify Gigabyte G5 GE on hardware: fans, RGB, probe tooling

**Description:**

> The G5 GE (i5-12500H / RTX 3050, BIOS FB06) joins the G6 KF as a
> measured machine, following the same evidence bar as #24/#25 but with
> the maintainer-side measurement done locally:
>
> - Read-only EC probe (`tools/probe.sh`, `tools/ec_read.py`): CPU/GPU
>   temps match coretemp/NVML, duty/RPM registers behave as documented.
> - Controlled first write (`tools/g2_measure.py`): each fan driven to
>   80% for 5s — DUT/tach registers move exactly as `docs/PROTOCOL.md`
>   documents (no G5 KF-style swap on this model), handback to firmware
>   verified. RGB colour set and restored (`tools/g2_keyboard.py`).
> - `gfan`/`gkbd` and the GUI classifier now list G5 GE as verified;
>   `packaging` doc test reads `VERIFIED_MODELS` from the code and
>   holds the README to precisely that set.
>
> Evidence log: `docs/probe/g5-ge-fb06.txt` (serial/UUID redacted) and
> the new "Measured on the Gigabyte G5 GE" section in PROTOCOL.md.
>
> **Hardware tests:** performed on the real machine (Fedora 44,
> /dev/port backend). Physical left/right fan placement was not
> separately recorded; the functional CPU/GPU assignment matches the
> register semantics.
> **Not tested:** suspend/resume colour restore on GE specifically.

Test evidence: cli 50 passed, tools 5, gui test_model 10,
packaging/test_docs 1, daemon 108 (baseline unchanged).

## PR2 — branch `pr/daemon-failsafe` (1 commit)

**Title:** Daemon failsafe: hand fans back to firmware on sustained failure

> If every tick fails for ~6s (EC gone, writes refused), the daemon
> currently keeps claiming its profile while nothing drives the fans —
> the #25 failure mode in another shape. Three consecutive failed
> ticks now trip a watchdog: drop the claimed profile, hand both fans
> to firmware auto, and say so in EcStatus; picking a profile re-arms
> it. A failed EC write also no longer marks the duty as applied, so
> the engine retries instead of believing a write that never landed.
> DevPortBackend gains emulated-EC unit tests (read/write/timeout).

Test evidence: daemon/tests 114 passed (108 baseline + 6 new, including
the retry-until-accepted handback regression test).
Hardware test: installed-system stop-at-100% → firmware idle verified
on G5 GE; the watchdog trip itself is unit-tested with a fake EC, not
yet triggered live.

## PR3 — branch `pr/turbo-boost` (1 commit)

**Title:** CPU turbo boost toggle via intel_pstate `no_turbo`

> Adds `SetTurboBoost` (new polkit action `control-cpu`), live
> `TurboBoost`/`TurboAvailable` properties that read sysfs on every
> access — so tuned/PowerDevil moving `boost` behind the daemon is
> reported, not fought — and an overview switch. A choice made in the
> app persists and is re-applied at daemon start; untouched machines
> see no behaviour change.
>
> **Known limitation:** tuned profiles carrying `boost=0` can turn
> turbo back off on power-profile switches (AC/battery); the app
> shows the live state when that happens. This interaction is
> documented, unit-tested at the sysfs layer, and not yet verified
> end-to-end against tuned-ppd.

Test evidence: daemon/tests 114 passed (108 + 6), gui/tests 122.
Hardware test: toggled on the real G5 GE; `no_turbo` flipped and
cores boosted above base clock (3.6–3.8 GHz samples).

## PR4 — branch `pr/fedora-packaging` (1 commit, no version change)

**Title:** Fedora RPM packaging

> `fedora/gigactl.spec` + `build-rpm.sh`, mirroring the Debian
> private-tree layout. Two Fedora-specific facts drive the shape:
> stock Fedora kernels build the ACPI EC without the ec_sys debugfs
> interface, so the daemon uses its /dev/port backend; and stock
> SELinux denies `init_t` write on `/dev/port`, so the package ships
> a minimal policy module (`fedora/selinux/gigactl-devport.te`) that
> loads on install and is removed on erase. The broad `init_t` rule
> is called out in the .te file as a deliberate minimum; a dedicated
> `gigactld_t` domain is follow-up work. Related: #34.
>
> No version bump: spec stays at the tree's current version.

Test evidence: `rpmbuild` succeeds from a clean checkout
(`gigactl-1.0.2-1.fc44.noarch.rpm`); the package was installed and
run on Fedora 44 KDE (daemon live, `gfan`/`gkbd`/GUI verified).
Debian packaging suite unaffected (still requires a dpkg env).

---

## Kept in the fork (not proposed upstream)

- Local `master` release line: version 1.1.0 bump + changelogs
  (maintainer owns versioning; will rebase away if upstream differs).
- Fork planning/hardware documents (`docs/PLAN.md`, `docs/HARDWARE.md`,
  `docs/SAFETY.md`) and benchmark tooling — working documents of this
  fork's G5 GE programme; the benchmark's GPU-inclusive arm is still
  open, so its conclusion is deliberately not upstreamed yet.
- Built RPMs under `fedora/RPMS/` (gitignored release artifacts).
