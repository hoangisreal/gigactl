# Upstream contributions — status and PR texts

> Trạng thái đồng bộ 2026-10-26 sau submission. Cả bốn PR đang OPEN,
> MERGEABLE, chưa có review/comment nào từ maintainer. File này là
> tracking của fork — không thuộc bất kỳ PR branch nào.

## Tracking

| PR | Scope | Commits | Trạng thái | Limitations đã khai | Hành động tiếp theo |
|---|---|---|---|---|---|
| [#36](https://github.com/smairio/gigactl/pull/36) G5 GE verified | Guard + model + tooling đo | `4247c70`, `3a2669f`, `e7b20ad` | OPEN, mergeable, 0 review | Chỉ BIOS FB06; chưa test suspend/resume colour | Chờ review; nếu được hỏi thêm evidence, trả lời bằng probe log đã commit |
| [#37](https://github.com/smairio/gigactl/pull/37) Daemon failsafe | Watchdog + DevPort tests | `6520e88`, `9c9c3b5` | OPEN, mergeable, 0 review | Trip chưa kích hoạt live (unit test EC giả lập) | Chờ review; sẵn sàng chỉnh ngưỡng/hành vi trip theo ý maintainer trong cùng branch |
| [#38](https://github.com/smairio/gigactl/pull/38) Turbo Boost | Daemon + GUI + polkit | `1c451d7` | OPEN, mergeable, 0 review | tuned `boost=0` ghi đè khi đổi power profile (đã verified); AC transition + reboot chưa test riêng | Chờ phán quyết scope; không tự mở rộng thêm CLI trước khi maintainer yêu cầu |
| [#39](https://github.com/smairio/gigactl/pull/39) Fedora packaging | RPM + SELinux domain | `03bbbaa`, `13ccaa9` | OPEN, mergeable, 0 review | Chỉ Fedora 44; peer rules theo `unconfined_t` | Chờ review SELinux; sẵn sàng tách/thu hẹp rule trong cùng branch nếu được yêu cầu |

Quy tắc xử lý review (mọi branch): minimal diff trên chính branch đó,
thêm regression test cho mọi sửa hành vi, không rebase/amend trừ khi
maintainer yêu cầu, không gộp tính năng mới vào PR đang review.

## Đánh giá CI cho pull_request (chưa triển khai)

Upstream hiện chỉ có `release.yml` chạy theo tag nên PR không có check
nào. Một contribution riêng đề xuất sau khi bốn PR trên có phản hồi:
workflow `pull_request` chạy các suite daemon/GUI/CLI/packaging trên
ubuntu-24.04 (môi trường có sẵn `dpkg` như release CI). Chưa làm vì:
(i) maintainer có thể có kế hoạch CI riêng; (ii) PR CI chạm vào cả bốn
PR đang mở và nên đi sau khi hướng tính năng được chấp nhận. Không
triển khai trong giai đoạn maintenance này.

## Bản thảo đã dùng khi submit (giữ để đối chiếu)

Conventions observed upstream: no CONTRIBUTING.md; MIT license; work is
issue/ticket-driven (commits reference issues, e.g. `closes #30`); the
support table's Verified/Reported distinction is a stated honesty rule
(PRODUCT.md #3); release CI runs the packaging + test suites on
ubuntu-24.04. Open context: PR #35 (keyboard lighting, another fork),
issue #34 (distro support request), issue #24/#33 (G5 reports).

Đã submit theo thứ tự PR1 → PR2 → PR3 → PR4 (independent
branches off `upstream/master`).
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
> **Known limitation (verified live 2026-10-26):** tuned profiles
> carrying `boost=0` *do* turn turbo back off on power-profile
> switches — confirmed: GUI on (`no_turbo=0`) → KDE power-profile
> switch → `no_turbo=1`. A daemon restart re-applies the saved choice
> (`turbo_enabled: true` → `0`). The AC/unplug transition uses the
> same tuned-ppd path but was not separately exercised.

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
> a dedicated least-privilege domain (`fedora/selinux/gigactld.te`):
> the daemon transitions via its labelled launcher and keeps only
> /dev/port, its state dir, its lock file, the `no_turbo` knob,
> journal and system-bus access with pairwise D-Bus peer rules. The
> module loads on install, is replaced cleanly on upgrade (including
> removing the earlier broad `gigactl-devport` module), and is
> removed on erase. Related: #34.
>
> No version bump: spec stays at the tree's current version
> (1.0.2; only the RPM Release moved during development).

Test evidence: `rpmbuild` succeeds from a clean committed checkout
(`gigactl-1.0.2-4.fc44.noarch.rpm`). Full lifecycle exercised live on
Fedora 44: install → daemon runs as `gigactld_t` with zero AVC
denials after four AVC-driven policy iterations → upgrade chain →
erase (module and .pp removed) → reinstall (daemon live again).
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
