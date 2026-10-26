# RESEARCH — gigactl cho Gigabyte G5 GE (Fedora KDE)

> Trạng thái: research + planning. Chưa implement, chưa ghi EC/power, chưa cài dependency.
> Quy ước: **Verified** = đã đọc/chạy trực tiếp nguồn; **Inferred** = suy luận có căn cứ; **Unknown** = chưa có evidence.

## 1. Audit repository local — Verified

- Repo thật nằm tại `gigactl/` (git root). Remote `origin=hoangisreal/gigactl` (fork), `upstream=smairio/gigactl`. Nhánh `master`, HEAD `7c1a34b build 18: release 1.0.2 (#32)`.
- Nội dung: daemon Python (`daemon/gigactld/`: `ec.py, mailbox.py, fans.py, curve.py, keyboard.py, telemetry.py, state.py, authz.py, service.py` 543 dòng), GUI GTK4/libadwaita (`gui/gigactl_gui/`, 21 module), CLI shell (`gfan` 268 dòng, `gkbd` 238 dòng, `gigactl-cli-lib.sh`), packaging Debian (`.deb`, systemd, D-Bus, polkit), `docs/PROTOCOL.md` (151 dòng), `PRODUCT.md`, `DESIGN.md`, man pages.
- Kiến trúc hiện tại: GUI (unprivileged) ↔ D-Bus system bus ↔ `gigactld` (root) là chủ nhân duy nhất của EC; CLI dùng daemon khi có, fallback ghi EC trực tiếp có flock chung. Đây là kiến trúc đúng, cần giữ.
- Hardware path duy nhất: EC mailbox qua `ec_sys` (`/sys/kernel/debug/ec/ec0/io`), fallback `/dev/port` (ports `0x62/0x66`, RD_EC `0x80`/WR_EC `0x81`) — xem `daemon/gigactld/ec.py:121-178`. **WMI được document nhưng không dùng trong code; hwmon hoàn toàn vắng.**
- Test execution (Verified, chạy trong env này): `daemon/tests` 108 passed, `gui/tests` 122 passed, `cli` 49 passed (pytest 9.1.1). `packaging` 110 passed / 10 failed chỉ vì thiếu `dpkg-parsechangelog` trên máy non-Debian — lỗi môi trường, không phải defect. Logic `DevPortBackend` đã được kiểm bằng EC giả lập **ngoài repo** (`/tmp`, trong phiên research: `read(0x07)=0x55`, stuck-IBF → `EcError`) — repo không có test này (ec.py tự ghi "Not unit-tested"); cần port thành test thật trong Phase 2. Timing trên phần cứng thật vẫn **Unknown**.

## 2. Phần cứng G5 GE — Verified một phần

- G5 GE-51xx: Intel Core **i5-12500H** (Alder Lake-H, 12 nhân), **RTX 3050 Laptop 4GB GDDR6, tối đa 75W** (Boost 1500 MHz), 15.6" FHD 144Hz, DDR4, Wi-Fi 6. RAM 8/16GB tùy thị trường.
- **Không được trộn model:** GE (12th, 3050/75W) ≠ ME (3050 Ti) ≠ KE (RTX 3060/115W) ≠ GD/MD/KD (11th i5-11400H) ≠ MF (2023, RTX 4050). Mọi kết luận về GE phải kiểm chứng trên đúng GE.
- Nguồn chính thức gigabyte.com trả HTTP 403 cho fetcher; thông số trên lấy từ snippet đã index của gigabyte.com + bản sao spec của nhà bán lẻ. SKU-level (RAM, SSD) là **Inferred/region-dependent**.
- **Unknown:** part EC/ITE chính xác của GE. Không được chép IT8518E từ Clevo đời 2012 sang GE.
- **Đã giải quyết bởi probe trên máy thật (2026-10-26, `docs/probe/g5-ge-fb06.txt`):** DMI `sys_vendor=GIGABYTE`, `product_name=G5 GE`, board `G5 GE`, **BIOS FB06** (02/11/2026), kernel Fedora `7.2.11-200.fc44`. `ec_sys` chưa nạp; không có `platform_profile`; **không có `charge_control_*`** trên BAT0 (battery charge limit → Unsupported trên máy này); RAPL `long_term=45W (max 45W)`, `short_term=115W`, `peak=150W` — PL1 không nâng quá 45W được. NVIDIA (probe root 2026-10-26): RTX 3050 Laptop, **power limit hiện tại/default 60W, min 1W, max 75W**, GPU 40°C idle — trần VBIOS 75W khớp spec. Pin: `energy-full 29.09Wh / design 52.51Wh` (~55% health, thông tin monitoring, ngoài scope điều khiển). EC registers vẫn chưa đọc được, và nay đã rõ vì sao: kernel Fedora stock có `CONFIG_ACPI_EC=y` nhưng **`CONFIG_ACPI_EC_DEBUGFS` không set** — interface `ec_sys` debugfs mà gigactl ưu tiên trên Ubuntu **không tồn tại trên Fedora và không nạp module được**. `CONFIG_DEVPORT=y` nên `/dev/port` là đường EC duy nhất bằng kernel stock; probe đã được bổ sung fallback read-only qua `tools/ec_read.py` (dùng lại `DevPortBackend` của daemon, 3 mẫu đọc). Hệ quả kiến trúc: trên Fedora daemon sẽ chạy bằng `DevPortBackend` — backend này race với EC driver của kernel (đã ghi trong `daemon/data/gigactl.service`), được giảm thiểu bằng verify-read-back mỗi lần ghi, engine thử lại mỗi tick và watchdog T2.1. Chỉ khi đọc `/dev/port` cho kết quả không ổn định mới xét lại quyết định "không kernel module" (D2).

## 3. Giao thức EC — Verified trên G6 KF, chưa verify trên G5 GE

Từ [docs/PROTOCOL.md](docs/PROTOCOL.md) (đo trên **G6 KF**, i7-13620H/RTX 4060):

- Platform là Clevo ODM: ACPI `DCHU` `_HID=CLV0001`, WMI GUID `ABBC0F6D-8EA1-11D1-00A0-C90629100000`, Insyde EC. Mailbox tại offset `0xF8 FCMD` (doorbell, ghi cuối), `0xF9 FDAT`, `0xFA–0xFD FBUF/FBF1..3`.
- Fan: doorbell `0xC1` (FDAT=fan 1/2, FBUF=duty 0–255; FDAT=`0xFF` trả firmware-auto). Đọc: `0x07` CPU temp, `0x0A/0x0B` GPU temp, `0xCE/0xCF` DUT1/DUT2, `0xD0–0xD3` tach; `RPM ≈ 2,156,220 / period`.
- Keyboard: single-zone RGB, doorbell `0xCA`; cần master-enable `0xC4` FDAT=`0x0C` FBUF=`0x3F` trước; màu theo thứ tự **B,R,G**; brightness FDAT=`0x06`.
- **Open questions đã ghi trong PROTOCOL.md:100-144:** trên G5 KF có báo cáo DUT1/DUT2 bị đảo và `0xC1` cần 4 tham số — chưa tái hiện, không code nào được dựa vào đó cho tới khi có phép đo phân biệt (xem SAFETY.md gate G2).
- Dead ends đã chứng minh: lệnh quạt họ `0xC4`, ghi RAM `0xFE0B0380`, USB RGB controller, NBFC — không lặp lại.

Suy ra cho G5 GE: cùng họ Clevo nên giao thức **có khả năng** áp dụng (Inferred), nhưng GE dùng CPU 12th-gen khác KF/MF/G6 — phải qua probe read-only trước khi cho phép ghi (xem COMPATIBILITY.md).

## 4. Linux interfaces trên Fedora KDE — Verified từ kernel docs

- In-tree `gigabyte-wmi` chỉ là driver nhiệt độ cho **mainboard desktop** Gigabyte — không dùng cho laptop này.
- Không có driver in-tree cho Clevo laptop fan/RGB. Họ tuxedo/clevo (`clevo_wmi`/`clevo_acpi`, tuxedo-keyboard/tuxedo-drivers) là out-of-tree DKMS, có guard theo branding TUXEDO, và theo PRODUCT.md thì từ chối máy không phải TUXEDO. Dùng trên Fedora cần Secure Boot signing — chỉ là phương án dự phòng, không phải nền tảng.
- Dùng được ngay (kỳ vọng, cần probe): `coretemp` hwmon, `intel_pstate` + `powercap`/RAPL, `platform_profile` (nếu firmware phơi bày), KDE PowerDevil/`power-profiles-daemon` hoặc `tuned-ppd`, NVML/`nvidia-smi -q -d POWER`, `upower`/sysfs `charge_control_end_threshold` (chỉ khi file tồn tại).
- Khuyến nghị: probe trước — `dmidecode -t system`, `ls /sys/class/hwmon /sys/class/powercap /sys/class/power_supply`, `lsmod | grep -iE 'clevo|gigabyte|wmi'`, `nvidia-smi -q -d POWER`.

## 5. Dự án tương tự & khả năng reuse

| Dự án | Bài học / reuse |
|---|---|
| `smairio/gigactl` (upstream của fork này) | Nền tảng chính. Fork `hoangisreal/gigactl` là bản sao — theo dõi upstream, không fork logic. |
| tuxedo-keyboard / clevo driver family | Tham chiếu command ID Clevo (`0x63/0x64/0x67/0x68/0x69`) — chỉ đọc tham chiếu, không vendor vào. |
| G-Helper (ASUS), Lenovo Legion Toolkit | Mẫu UX: một app nhẹ, profile theo nguồn điện/game, tray. Học pattern, không port code (khác vendor/protocol). |
| nbfc-linux (`ec_probe`) | Đã dùng cho CLI fallback; giữ nguyên vai trò. |
| OpenRGB | Không có USB RGB controller trên máy này — không tích hợp. |

## 6. Đánh giá REA / Ghidra — kết luận: chưa cần

- Với phạm vi fan + RGB 1-zone, đường `DSDT (iasl) + /sys/bus/wmi + ec_sys` differential đã là nguồn authoritative và đã verify trên G6 KF. **Không tải, không dịch ngược Gigabyte Control Center (GCC) ở giai đoạn này.**
- Ghidra chỉ được mở khi: (a) tính năng Windows-only ngoài fan/RGB (ví dụ performance mode của GCC, hotkey Fn) không suy ra được từ DSDT/WMI, **và** (b) đã trace live (WMI/EC differential khi đổi setting trên Windows) mà vẫn thiếu mapping. Khi đó chỉ RE thành phần native chạm IOCTL/WMI, không RE toàn bộ GCC.
- Ràng buộc: chỉ phân tích bản GCC tải hợp lệ từ Gigabyte, không phân phối lại binary, không flash firmware.

## 7. Performance engineering — nguyên tắc

- Không thể vượt giới hạn firmware/VBIOS: NVIDIA power limit chỉ đặt trong `[min, max]` driver báo (`nvidia-smi -q -d POWER`); Intel RAPL PL1/PL2 qua `powercap` bị trần bởi BIOS/fuse.
- CPU i5-12500H: kiểm tra khóa undervolt (MSR `0x150`) — phần lớn máy Alder Lake-H đã khóa sau Plundervolt; nếu khóa, hiển thị "locked by BIOS", không cố ghi.
- Sustained performance đến từ: fan curve sớm hơn firmware (firmware giữ duty thấp tới ~96°C), giữ PL1 bền thay vì PL2 bùng nổ, và tránh thermal throttle — đo bằng benchmark lặp (Phoronix Test Suite, MangoHud) so baseline firmware-auto, không hứa % tăng trước khi đo.
- `stress-ng`/FurMark chỉ là soak/stability test, không phải số liệu hiệu năng.

## Nguồn đã kiểm chứng

- `gigactl/docs/PROTOCOL.md`, `gigactl/README.md`, `gigactl/PRODUCT.md` (local, đã đọc toàn văn).
- Gigabyte G5 GE spec (qua nhà bán lẻ sao spec chính hãng): https://www.smcyberzone.com/products/laptops/gigabyte-g5-ge
- Kernel `GIGABYTE_WMI` (desktop mainboard temps): https://mirrors.hust.edu.cn/git/kernel-doc-zh.git/tree/drivers/platform/x86/Kconfig
- TUXEDO/Clevo interfaces: https://github.com/wessel-novacustom/clevo-keyboard
- Kernel uniwill-laptop doc (mẫu cho platform/charge profile): https://docs.kernel.org/next/admin-guide/laptops/uniwill-laptop.html
