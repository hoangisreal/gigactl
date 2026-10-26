# PLAN — gigactl thành Linux Control Center cho Gigabyte G5 GE (Fedora KDE)

> Kế hoạch của fork (đã duyệt và đang thực thi từ 2026-10-26). Trạng thái:
> Phase 0–2 và 4 đã xong trên máy thật; Phase 3 (power write) chưa làm;
> Phase 5 mới xong arm CPU. Chi tiết task và trạng thái ở cuối file.
> Đích: thay thế Gigabyte Control Center trên **một model duy nhất — G5 GE** — bằng cách mở rộng gigactl hiện có. Không rewrite, không project mới.

## Goal

`gigactl` trên Fedora KDE điều khiển được fan (manual + curve), RGB 1-zone, monitoring và profile hiệu năng trên G5 GE — trong giới hạn firmware cho phép — với an toàn phần cứng được chứng minh bằng phép đo trên máy thật.

## Success Criteria

1. Trên máy G5 GE thật: đọc đúng nhiệt độ/RPM/duty; đặt được duty từng quạt và trả firmware-auto trong <1s thao tác.
2. Curve tự động chạy bởi daemon, daemon chết hoặc mất cảm biến thì quạt về firmware-auto (watchdog chứng minh bằng test).
3. Không một ghi EC/power nào nằm ngoài khoảng firmware/driver báo; mọi ghi đều verify-read-back, sai thì revert.
4. Benchmark sustained (PTS/MangoHud) so baseline firmware-auto được công bố kèm số liệu đo, không kèm hứa hẹn % chưa đo.
5. Cài được trên Fedora KDE từ RPM/COPR, GUI chạy với tray StatusNotifier; bộ test hiện có (279 test daemon+GUI+CLI) vẫn xanh.

## Approach — quyết định đã chốt

| # | Quyết định | Lý do / phương án bị loại |
|---|---|---|
| D1 | **Reuse/refactor gigactl**, giữ kiến trúc GUI ↔ D-Bus ↔ `gigactld` (root, chủ EC duy nhất) | Đã verify trên G6 KF, 279 test xanh. Rewrite = mất protocol đã chứng minh. |
| D2 | **Không kernel module** ở đợt này | Trên Fedora, kernel stock không có `ec_sys` debugfs nên backend là `/dev/port` (đã có sẵn trong daemon, kèm verify-read-back + watchdog). Module chỉ xét lại nếu đọc `/dev/port` trên GE cho kết quả không ổn định. |
| D3 | **Không REA/Ghidra bây giờ** | DSDT + WMI GUID + ec_sys differential là đường authoritative. Điều kiện mở RE: xem RESEARCH.md §6. |
| D4 | G5 GE là model đích duy nhất; model khác giữ nhãn hiện tại | Tránh suy diễn GE≠KE/ME/KF. Guard DMI giữ nguyên hành vi. |
| D5 | Power (RAPL/NVML) là lớp **tùy chọn, clamp theo `[min,max]`** firmware báo | Không vượt VBIOS/BIOS; bị khóa thì hiển thị, không bypass. |
| D6 | Fedora: thêm RPM packaging, giữ `.deb` | Đích là Fedora KDE; không bỏ Ubuntu. |

## Steps

- **Phase 0 — Probe & xác nhận phần cứng (read-only).** Script probe thu DMI, EC offsets đọc-thử, `GET_SPECS`, danh sách sysfs (hwmon/powercap/power_supply/platform_profile), NVML limits. Chạy trên máy G5 GE của người dùng. *Gate G1.*
- **Phase 1 — Bật G5 GE có kiểm chứng.** Thêm GE vào bảng model với nhãn "verified" chỉ sau phép đo phân biệt DUT1/DUT2 (đặt 1 quạt 80%, đọc cả `0xCE/0xCF`, nghe/đo quạt vật lý). Xử lý open question 4-tham-số `0xC1` bằng cùng phép đo. *Gate G2.*
- **Phase 2 — Hoàn thiện control core.** Curve engine đã có; **watchdog revert-to-auto khi mất cảm biến chưa có** (tick lỗi hiện chỉ ghi log qua `EcStatus`, không trả quạt về auto) — đây là việc mới bắt buộc, kèm profile presets (Quiet/Balanced/Performance/Max/Firmware), automation theo AC/battery và theo ứng dụng (chỉ khi D-Bus/profile hook đủ tin cậy). Tiền đề Phase 1: siết model guard để GE không ghi được trước gate G2 (bản hiện tại cho mọi `G5*` ghi với cảnh báo).
- **Phase 3 — Power management (tùy chọn, clamp).** Đọc/ghi RAPL PL1/PL2 và NVML power limit trong khoảng cho phép; hiển thị trạng thái khóa undervolt; battery charge limit chỉ khi sysfs tồn tại.
- **Phase 4 — Fedora KDE packaging & UX.** RPM spec/COPR, kiểm tra tray trên KDE Plasma, PowerDevil/`power-profiles-daemon` không xung đột daemon, tài liệu cài đặt.
- **Phase 5 — Benchmark & phát hành.** Bộ benchmark baseline vs gigactl (sustained 30'), soak test, release notes trung thực theo nhãn Verified/Reported.

Thứ tự cứng: 0 → 1 → 2; Phase 3 độc lập sau Phase 1; Phase 4 sau 2; Phase 5 cuối. Chi tiết atomic ở mục "Atomic tasks" cuối file. Rủi ro & rollback: [SAFETY.md](SAFETY.md). Phần cứng/benchmark: [HARDWARE.md](HARDWARE.md).

## Validation Plan

| Gate | Bằng chứng yêu cầu |
|---|---|
| G0 (mọi phase) | `python3 -m pytest daemon/tests gui/tests cli -q` xanh (hiện 108+122+49) + test mới cho code mới |
| G1 (Phase 0→1) | Bản ghi probe GE khớp DMI GE-51xx, EC temp/RPM đọc hợp lý ở idle và load |
| G2 (Phase 1) | Log phép đo 1-quạt: register đích thay đổi đúng + quạt vật lý đúng phía quay |
| G3 (Phase 2/3) | Soak 30' + kill daemon giữa chừng → quạt về auto; nhiệt không vượt ngưỡng crit−5°C |
| G4 (Phase 4) | Cài RPM sạch trên Fedora KDE, reboot, tray + GUI + `gfan auto` hoạt động |
| G5 (Phase 5) | Bảng benchmark baseline vs gigactl cùng bài test, cùng môi trường, ≥3 lần chạy |

## Risks / Open Questions

- **R1:** GE có thể khác G6 KF ở map DUT hoặc tham số `0xC1` (đã có tiền lệ báo cáo trên G5 KF). Giảm thiểu: Phase 1 không bật ghi cho tới G2.
- **R2 (đã giải quyết):** máy GE thật (BIOS FB06) chính là máy dev — Phase 0/1 đã đo xong.
- **R3 (đã giải quyết):** Fedora không có ec_sys debugfs trong kernel stock; daemon dùng `/dev/port` + SELinux module đóng gói kèm (xem HARDWARE.md §3).
- **Mở:** exact EC/ITE part của GE (Unknown, không chặn việc gì hiện tại); arm benchmark CPU+GPU; tương tác tuned↔turbo chưa verify đầu-cuối. Ngoài ra: None.

## Atomic tasks (gộp từ TASKS.md, kèm trạng thái 2026-10-26)

- Phase 0 — Probe: T0.1 script read-only ✅ · T0.2 chạy trên GE thật ✅ (`docs/probe/g5-ge-fb06.txt`) · T0.3 đối chiếu ✅ (nay ở HARDWARE.md).
- Phase 1 — GE verified: T1.1 phép đo từng quạt ✅ · T1.2 dạng 1-tham-số `0xC1` ✅ · T1.3 RGB (bằng hành vi LED, không đọc được GET_SPECS) ✅ · T1.4 nhãn Verified ✅.
- Phase 2 — Control core: T2.1 watchdog ✅ (unit test; trip live chưa kích hoạt) · T2.2 presets ✅ (có sẵn) · T2.3 automation AC/pin ❌ chưa làm · T2.4 test ✅.
- Phase 3 — Power write: T3.1 hiển thị ❌ · T3.2 ghi clamp ❌ · T3.3 battery limit — loại (không có sysfs) · T3.4 khóa undervolt ❌. Turbo toggle (ngoài plan gốc, user yêu cầu) ✅ thay thế một phần.
- Phase 4 — Fedora: T4.1 RPM ✅ · T4.2 KDE tray/GUI ✅ (xác nhận trực quan) · T4.3 tài liệu cài Fedora ⚠️ một phần (UPSTREAM.md + spec; chưa COPR).
- Phase 5 — Benchmark: T5.1 arm CPU ✅, arm GPU ❌ · T5.2 soak/abuse ⚠️ một phần (stop-daemon live ✅, soak 30' chưa) · T5.3 release notes ✅ (changelog 1.1.0 ở fork).
- Ngoài scope (giữ nguyên): kernel module, Ghidra (điều kiện ở HARDWARE.md §5), per-key RGB, Fn hotkeys, undervolt bypass, hỗ trợ model khác ngoài nhãn hiện tại.
