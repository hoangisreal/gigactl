# COMPATIBILITY — Ma trận hỗ trợ

> Máy đích duy nhất của đợt này: **Gigabyte G5 GE trên Fedora KDE**. Các model khác chỉ để đối chiếu, không mở rộng scope.

## Trạng thái model

| Model | CPU/GPU | Trạng thái | Evidence |
|---|---|---|---|
| G6 KF | i7-13620H / RTX 4060 | ✅ Verified (maintainer, đo trực tiếp) | [README.md](README.md), [docs/PROTOCOL.md](docs/PROTOCOL.md) |
| G5 KF | i5-12450H / RTX 4060 (ladder tương tự GE) | 👍 Reported working (#24) | GitHub issue #24; còn open question DUT swap — xem dưới |
| G5 MF5 | — | 👍 Reported working (#25) | GitHub issue #25; từng bị ec_sys read-only, đã xử lý bằng thông báo |
| **G5 GE** | **i5-12500H / RTX 3050 75W** | ✅ **Verified (fans)** | Gate G1+G2 qua 2026-10-26 (BIOS FB06, Fedora 44): đọc EC nhất quán; cả hai quạt lái 80% đúng register và trả firmware-auto sạch. RGB còn chờ T1.3 |
| G5 KE/ME, GD/MD | khác GPU ladder | ⚪ Unknown | Không suy diễn từ GE |
| Ngoài G5/G6/G7 | — | ❌ Refused bởi model guard | `GFAN_UNSAFE=1`/`GKBD_UNSAFE=1` là override tự chịu rủi ro |

## Ma trận tính năng trên G5 GE (mục tiêu Fedora KDE)

| Tính năng | Phân loại | Điều kiện / ghi chú |
|---|---|---|
| Đọc nhiệt độ CPU/GPU, RPM, duty | Supported (sau probe) | EC offsets `0x07/0x0A/0x0B/0xCE–0xD3` cần xác nhận trên GE; trên Fedora đường đọc là `/dev/port` (`ec_sys` debugfs không có trong kernel stock) |
| Fan manual + curve + trả firmware-auto | Supported (đã verify GE) | G2 qua; live qua daemon 2026-10-26: `gfan 60` → hai quạt 60% (~4.100/3.900 RPM) không cần sudo, `gfan auto` trả firmware sạch |
| RGB keyboard 1-zone + brightness | Supported (đã verify GE) | Master-enable + màu cam + về xanh factory xác nhận bằng mắt 2026-10-26; `GET_SPECS` không đọc được qua EC nên loại keyboard suy ra từ hành vi LED |
| Monitoring (GUI/tray) | Supported (đã verify GE) | Daemon + GUI xác nhận trực quan trên KDE 2026-10-26: banner "fully verified", đổi profile và màu phím trong GUI đều ăn |
| Performance profiles (Quiet/Balanced/Perf/Max) | Requires Extension | Là preset fan curve + (tùy chọn) platform_profile nếu firmware phơi bày |
| CPU power (RAPL PL1/PL2) | Requires Extension, có trần firmware | Chỉ ghi trong `[min,max]` powercap báo; hiển thị khi bị khóa |
| GPU power limit (NVML) | Requires Extension, có trần VBIOS | Chỉ trong khoảng `nvidia-smi` báo; RTX 3050 trần thiết kế 75W |
| Undervolt CPU | Unsupported (kỳ vọng) | Alder Lake-H thường khóa MSR 0x150; chỉ hiển thị trạng thái khóa |
| Battery charge limit | Unsupported (máy đã probe) | Probe 2026-10-26: BAT0 không có `charge_control_*` — loại khỏi scope GE |
| CPU turbo boost (intel_pstate `no_turbo`) | Supported (đã verify GE) | Toggle trong GUI qua daemon + polkit `control-cpu`, persist khi user chọn. **Known issue:** tuned profile chứa `boost=0` (khi đổi profile điện qua PowerDevil/tuned-ppd, hoặc chuyển AC/pin) có thể tắt lại turbo sau khi app bật; app luôn hiển thị trạng thái sysfs live nên thấy ngay. Muốn gigactl là chủ duy nhất: bỏ `boost=0` khỏi tuned profile. Tương tác này chưa được kiểm chứng đầu-cuối (chưa xác minh) |
| Fn hotkeys | Requires Reverse Engineering | EC chuyển sự kiện cho OS driver (`DCHU.HKDR`) chưa tồn tại; ngoài scope đợt 1 |
| Per-key / multi-zone RGB | Unsupported | Phần cứng 1-zone; không tồn tại USB RGB controller |
| Đóng gói `.deb` (Ubuntu) | Supported | Đã có; Fedora cần thêm RPM/spec hoặc COPR ở Phase 4 |
| Kernel module riêng | Không cần (quyết định) | Chỉ xét lại nếu ec_sys/WMI không đủ — xem PLAN.md |

## Điều kiện để nâng G5 GE lên "Verified"

1. Probe read-only trên máy GE thật khớp DMI + EC offsets (gate G1, SAFETY.md).
2. Phép đo một quạt ở duty phân biệt khớp cả register lẫn quạt vật lý (gate G2).
3. Soak test 30 phút không throttle bất thường, daemon chết → quạt về firmware-auto (gate G3).
