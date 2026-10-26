# TASKS — Atomic tasks, dependencies, acceptance criteria

> Ưu tiên: P0 = chặn đường chính, P1 = cần cho phát hành, P2 = nên có. Không task nào được bắt đầu trước khi PLAN.md được duyệt.

## Phase 0 — Probe read-only (P0)

| ID | Task | Phụ thuộc | Acceptance criteria |
|---|---|---|---|
| T0.1 | Script `tools/probe.sh` (chỉ đọc): DMI, kernel, `lsmod`, sysfs hwmon/powercap/power_supply/platform_profile, `nvidia-smi -q -d POWER`, EC read qua ec_sys ở offsets trong PROTOCOL.md | — | Chạy không cần ghi EC; xuất 1 file report; không đổi trạng thái máy |
| T0.2 | Chạy probe trên G5 GE thật, lưu report vào `docs/probe/g5-ge-<bios>.txt` | T0.1 + máy GE | Report khớp DMI GE-51xx; temp/RPM idle hợp lý (gate G1) |
| T0.3 | Đối chiếu report với bảng register PROTOCOL.md, ghi chênh lệch vào RESEARCH.md | T0.2 | Mọi offset hoặc ghi "khớp" hoặc thành task Phase 1 riêng |

## Phase 1 — Bật G5 GE có kiểm chứng (P0)

| ID | Task | Phụ thuộc | Acceptance criteria |
|---|---|---|---|
| T1.1 | Phép đo phân biệt: fan1=80% trong 10s, đọc `0xCE/0xCF` + xác nhận quạt vật lý; lặp lại fan2 | T0.3 | Kết luận DUT map cho GE có log (gate G2); nếu đảo, chỉ sửa map theo model, không sửa toàn cục |
| T1.2 | Kiểm chứng dạng 1-tham-số của `0xC1` trên GE (so với biến thể 4-tham-số) | T1.1 | Duty đặt được giữ ≥10s; kết quả ghi vào PROTOCOL.md |
| T1.3 | Xác nhận RGB trên GE: trước hết xác định cách đọc `GET_SPECS` (getter WMI `0x0D` — đường EC trực tiếp chưa rõ, **Unknown**), rồi master-enable `0xC4/0x0C/0x3F`, set màu B,R,G | T1.1 | Đổi màu + brightness thấy được; reboot mất màu (đúng hành vi EC); nếu không đọc được GET_SPECS thì xác nhận bằng hành vi LED thực tế và ghi rõ giới hạn |
| T1.4 | Cập nhật bảng model: GE → Verified (chỉ khi T1.1–T1.3 pass), README + COMPATIBILITY.md | T1.3 | Không đổi nhãn model nào khác |

## Phase 2 — Control core (P0/P1)

| ID | Task | Phụ thuộc | Acceptance criteria |
|---|---|---|---|
| T2.1 | Watchdog: daemon chết/mất cảm biến >5s → trả cả hai quạt về firmware-auto | T1.4 | Kill -9 daemon khi đang manual: quạt về auto (gate G3) |
| T2.2 | Profile presets Quiet/Balanced/Performance/Max/Firmware ánh xạ curve có sẵn | T2.1 | Đổi profile áp dụng <2s; Firmware là trạng thái hiển thị rõ, không phải lỗi |
| T2.3 (P1) | Automation: profile theo AC/battery; theo ứng dụng chỉ khi hook đáng tin | T2.2 | Không tự đổi profile khi tín hiệu mập mờ; có log quyết định |
| T2.4 (P1) | Test: unit cho watchdog/preset + giả lập EC lỗi giữa chừng | T2.1 | Suite cũ 279 test + test mới xanh (gate G0) |

## Phase 3 — Power, clamp theo firmware (P1)

| ID | Task | Phụ thuộc | Acceptance criteria |
|---|---|---|---|
| T3.1 | Đọc RAPL PL1/PL2 + NVML limits, hiển thị khoảng cho phép và trạng thái khóa | T1.4 | Không ghi gì; số liệu khớp `nvidia-smi -q`/`powercap` |
| T3.2 | Ghi PL/NVML trong `[min,max]`, verify-read-back, sai → khôi phục giá trị cũ | T3.1 | Ghi ngoài khoảng bị từ chối ở UI lẫn daemon; reboot về mặc định firmware |
| T3.3 | Battery charge limit — chỉ khi sysfs tồn tại trên GE | T0.2 | File không tồn tại → tính năng ẩn, không báo lỗi |
| T3.4 | Hiển thị "undervolt locked by BIOS" nếu MSR khóa; không cung cấp bypass | T3.1 | Không có đường ghi MSR 0x150 nào trong code |

## Phase 4 — Fedora KDE (P1)

| ID | Task | Phụ thuộc | Acceptance criteria |
|---|---|---|---|
| T4.1 | RPM spec (daemon+GUI+CLI), modprobe config ec_sys `write_support=1` | T2.2 | Cài/gỡ sạch trên Fedora; gỡ → quạt về firmware (gate G4) |
| T4.2 | Kiểm tra KDE: tray StatusNotifier, autostart, PowerDevil không giành profile | T4.1 | Qua reboot + suspend/resume, màu phím và curve được khôi phục |
| T4.3 | Tài liệu cài Fedora trong README (COPR hoặc build từ spec) | T4.1 | Người mới làm theo được không cần đọc source |

## Phase 5 — Benchmark & release (P1)

| ID | Task | Phụ thuộc | Acceptance criteria |
|---|---|---|---|
| T5.1 | Benchmark sustained: PTS + MangoHud, baseline firmware-auto vs profile Performance, 30', ≥3 lượt | T2.2 | Bảng số liệu kèm nhiệt/throttle reasons (gate G5) |
| T5.2 | Soak + abuse: curve sai cực đoan, cảm biến rút, ghi tranh chấp 2 writer | T5.1 | Mọi trường hợp kết thúc ở firmware-auto an toàn |
| T5.3 | Release notes ghi đúng nhãn Verified cho GE, giữ nguyên nhãn model khác | T5.2 | Không tuyên bố % hiệu năng ngoài số đã đo ở T5.1 |

## Việc bị loại khỏi scope (ghi rõ để không làm thừa)

Kernel module riêng; Ghidra/GCC RE (chỉ mở theo điều kiện RESEARCH.md §6); per-key RGB; Fn hotkey driver (research riêng, ngoài đợt); hỗ trợ KE/ME/MF/GD ngoài nhãn hiện tại; undervolt bypass.
