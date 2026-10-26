# HARDWARE — G5 GE research, compatibility, benchmark

> Nguồn sự thật về phần cứng của fork này. Giao thức EC chi tiết nằm ở
> [PROTOCOL.md](PROTOCOL.md) (canonical); bảng support cho người dùng nằm ở
> [README.md](../README.md) và phải khớp tập verified trong code
> (`gui/gigactl_gui/model.py: VERIFIED_MODELS`, có test giữ).
> Quy ước: **Verified** = đã đo/chạy trực tiếp; **Reported** = người khác
> báo, chưa đo ở đây; **Inferred** = suy luận có căn cứ; **Unknown** = chưa có evidence.

## 1. Gigabyte G5 GE — Verified một phần

- G5 GE-51xx: i5-12500H (Alder Lake-H), RTX 3050 Laptop 4GB GDDR6 tối đa 75W
  (Boost 1500 MHz), 15.6" FHD 144Hz. RAM/SSD tùy thị trường (**Inferred** —
  spec chính thức gigabyte.com chặn fetcher; số liệu qua bản sao nhà bán lẻ).
- **Không trộn model:** GE (3050/75W) ≠ ME (3050 Ti) ≠ KE (3060/115W) ≠
  GD/MD/KD (11th-gen) ≠ MF (RTX 4050). Mọi kết luận phải đo trên đúng model.
- Máy đã đo (2026-10-26, `docs/probe/g5-ge-fb06.txt`): DMI `G5 GE`, board
  `G5 GE`, **BIOS FB06**, Fedora kernel 7.2.11. RAPL `long_term` 45W (trần
  45W), `short_term` 115W. NVIDIA: limit hiện tại/default 60W, max 75W.
  Pin 29.09/52.51 Wh (~55% health — thông tin monitoring, ngoài scope).
- **Unknown:** part EC/ITE chính xác. Không chép IT8518E từ Clevo đời cũ sang.

## 2. Ma trận tương thích

Trạng thái model: G6 KF ✅ Verified (maintainer) · G5 GE ✅ Verified (fans +
RGB, gate G1/G2 ngày 2026-10-26) · G5 KF 👍 Reported (#24) · G5 MF5 👍
Reported (#25) · KE/ME/GD/MD ⚪ Unknown · ngoài G5/G6/G7 ❌ refused bởi guard.

| Tính năng (trên G5 GE) | Phân loại | Ghi chú |
|---|---|---|
| Đọc temp/RPM/duty qua EC | Supported — verified | Offset khớp G6 KF; Fedora đọc qua `/dev/port` |
| Fan manual + curve + firmware-auto | Supported — verified | Live qua daemon: `gfan 60` → 60% (~4.100/3.900 RPM), `gfan auto` sạch |
| RGB 1-zone + brightness | Supported — verified | Xác nhận bằng mắt; `GET_SPECS` không đọc qua EC nên loại suy ra từ hành vi LED |
| Monitoring GUI/tray (KDE) | Supported — verified | Banner "fully verified", đổi profile/màu trong GUI đều ăn |
| Fan profile presets | Supported | Quiet/Balanced/Performance/Max/Firmware có sẵn trong daemon |
| CPU turbo boost (`no_turbo`) | Supported — verified | Toggle GUI + polkit `control-cpu`. **Known issue:** tuned profile `boost=0` có thể tắt lại khi đổi profile điện/AC-pin — chưa verify đầu-cuối; app hiển thị live state |
| RAPL / NVML power write | Requires Extension (Phase 3/5 của PLAN) | Chỉ ghi trong `[min,max]` firmware báo |
| Undervolt | Unsupported (kỳ vọng khóa MSR 0x150) | Chỉ hiển thị trạng thái khóa |
| Battery charge limit | Unsupported trên máy đã probe | BAT0 không có `charge_control_*` |
| Platform profile | Unsupported trên máy đã probe | Firmware không phơi bày `platform_profile` |
| Fn hotkeys | Requires Reverse Engineering | Sự kiện `DCHU.HKDR` chưa có driver — xem §5 |
| Per-key/multi-zone RGB, OpenRGB | Unsupported | Phần cứng 1-zone, không USB RGB controller |

Điều kiện Verified (đã đạt cho GE): probe đọc khớp (G1) → phép đo ghi phân
biệt từng quạt (G2) → failsafe khi daemon dừng (một phần G3, xem SAFETY.md).

## 3. Đường EC trên Linux/Fedora

- gigactl nói chuyện với EC Clevo qua mailbox (PROTOCOL.md). Trên Ubuntu,
  daemon ưu tiên `ec_sys` debugfs; **kernel Fedora stock không biên dịch
  interface đó** (`CONFIG_ACPI_EC=y`, `CONFIG_ACPI_EC_DEBUGFS` tắt, đã kiểm
  trong `/boot/config`) nên trên Fedora daemon dùng `DevPortBackend`
  (`/dev/port`, `CONFIG_DEVPORT=y`) — race với EC driver của kernel được
  giảm thiểu bằng verify-read-back, engine retry mỗi tick và watchdog.
- In-tree `gigabyte-wmi` chỉ phục vụ mainboard desktop. Driver Clevo/TUXEDO
  là DKMS ngoài cây, guard theo branding — chỉ là dự phòng, không phải nền.
- Dùng được ngay: `coretemp`, `intel_pstate` + powercap/RAPL, NVML, UPower
  (chỉ đọc). Probe chuẩn: `tools/probe.sh` (read-only, có test cấm ghi).

## 4. Benchmark sustained — 2026-10-26 (một phần)

`tools/bench_sustained.sh`, CPU burn toàn nhân 120s/arm, steady-state nửa sau:

| Arm | Package | Xung TB | Fan1 |
|---|---|---|---|
| Firmware | 61.9 °C | 2149 MHz | 27% |
| performance | 63.2 °C | 2150 MHz | 65% |

Kết luận trung thực: tải CPU-only này **bị chặn bởi PL1 45W, không phải
nhiệt**, nên fan không đổi hiệu năng. Arm CPU+GPU (game/Unigine, ≥3 lượt)
còn mở — chưa có tuyên bố sustained-performance nào cho tới khi có số đó.

## 5. Dự án tương tự & khi nào cần RE

Reuse: upstream `smairio/gigactl` là nền; tuxedo/clevo chỉ tham chiếu
command ID; G-Helper/Lenovo Legion Toolkit là mẫu UX; nbfc `ec_probe` cho
CLI fallback. **REA/Ghidra chưa cần**: DSDT + WMI + differential trên EC
đã đủ cho fan/RGB. Chỉ mở RE khi tính năng Windows-only (Fn hotkeys,
performance mode của GCC) không suy ra được từ DSDT/WMI *và* trace live
vẫn thiếu mapping — chỉ RE thành phần chạm IOCTL/WMI, không flash firmware.

## 6. Nguồn

- Đo trực tiếp: `docs/probe/g5-ge-fb06.txt`, [PROTOCOL.md](PROTOCOL.md) mục "Measured on the Gigabyte G5 GE".
- Spec G5 GE (bản sao chính hãng): https://www.smcyberzone.com/products/laptops/gigabyte-g5-ge
- Kernel GIGABYTE_WMI (desktop): https://mirrors.hust.edu.cn/git/kernel-doc-zh.git/tree/drivers/platform/x86/Kconfig
- Clevo interfaces: https://github.com/wessel-novacustom/clevo-keyboard
- Uniwill laptop doc (mẫu platform/charge): https://docs.kernel.org/next/admin-guide/laptops/uniwill-laptop.html
