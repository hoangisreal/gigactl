# SAFETY — Risk assessment, validation gates, fallback, rollback

> Nguyên tắc gốc: trạng thái an toàn luôn là **firmware-auto**. Mọi thử nghiệm phải quay về đó được trong một thao tác, kể cả khi phần mềm chết.

## Rủi ro chính

| # | Rủi ro | Mức | Giảm thiểu |
|---|---|---|---|
| S1 | Ghi EC register chưa verify trên G5 GE → quạt dừng/sai phía | Cao | Không bật ghi trước gate G2. **Lưu ý: bản hiện tại đã cho phép ghi trên mọi `G5*`** (`gfan` model guard chỉ cảnh báo "untested") — vì vậy Phase 0 cấm chạy `gfan/gkbd`/daemon ở chế độ ghi trên GE, chỉ dùng probe đọc-thuần; việc siết guard theo model là task tiền đề của Phase 1 |
| S2 | Hai writer cùng ghi mailbox EC (daemon + CLI trực tiếp) | Trung bình | Giữ cơ chế hiện tại: daemon là chủ duy nhất, flock chung, CLI từ chối khi không chắc trạng thái bus |
| S3 | `ec_sys` nạp read-only → đọc đúng, ghi bị từ chối âm thầm | Trung bình | Đã xử lý ở bản hiện tại (báo rõ lý do + lệnh sửa); giữ nguyên, thêm vào checklist cài Fedora |
| S4 | Curve quá thấp ở nhiệt cao → throttle/hư hại lâu dài | Cao | Sàn an toàn hiện có: <30% bị từ chối khi CPU ≥85°C; mở rộng: PWM >0 trên 70°C, 100% ở crit−5°C |
| S5 | Daemon chết khi đang manual → quạt kẹt ở duty thấp | Cao | Watchdog T2.1 revert-to-auto; reboot luôn reset EC (đã đúng thiết kế) |
| S6 | Ghi power limit ngoài khoảng → driver/firmware từ chối hoặc mất ổn định | Trung bình | Chỉ ghi trong `[min,max]` hệ thống báo, verify-read-back, sai khôi phục giá trị cũ; không persist qua reboot ở đợt đầu |
| S7 | Cố undervolt khi BIOS khóa (Plundervolt) | Trung bình | Không có đường ghi MSR 0x150; chỉ hiển thị trạng thái khóa (T3.4) |
| S8 | DKMS out-of-tree (nếu dùng tuxedo) + Secure Boot trên Fedora | Trung bình | Không dùng làm nền tảng (quyết định D2); chỉ là phương án nghiên cứu đối chiếu |
| S9 | Suy diễn model: áp kết quả G6 KF/G5 KF cho GE | Cao | Model guard + nhãn Verified/Reported tách bạch; GE chỉ lên Verified qua Phase 1 |

## Validation gates (không qua = dừng, không ghi đè)

- **G1 Probe:** chỉ đọc; temp/RPM phải hợp lý ở idle và khi tải nhẹ.
- **G2 Phân biệt quạt:** đặt fan1=80%, đọc cả `0xCE` và `0xCF` và xác nhận quạt vật lý. Register không đích thay đổi → map đảo; register đúng nhưng quạt sai phía → nhãn vật lý khác. Hai kết luận cho hai cách sửa ngược nhau — không được đoán.
- **G3 Soak/watchdog:** 30 phút tải, kill daemon giữa chừng, rút cảm biến giả lập; mọi đường đều kết thúc firmware-auto, nhiệt < crit−5°C.
- **G4 Cài đặt Fedora:** cài/gỡ RPM sạch, qua reboot + suspend/resume.
- **G5 Benchmark:** cùng bài test/môi trường, ≥3 lượt, công bố kèm throttle reasons.

## Nợ hardening đã biết

- SELinux: gói Fedora hiện nạp rule tối thiểu cho mọi `init_t` ghi `/dev/port` (`fedora/selinux/gigactl-devport.te`). Domain riêng `gigactld_t` là hướng đúng nhưng phải re-grant D-Bus/polkit trên máy Fedora thật — chưa làm, không suy đoán.
- Turbo vs tuned: tuned profile `boost=0` có thể ghi sau gigactl khi đổi profile điện — xem COMPATIBILITY.md. Daemon đọc live state nên không báo sai, nhưng không tự giành lại.

## Fallback & rollback

- **Runtime:** một lệnh `gfan auto` (hoặc gỡ gói) trả quạt cho firmware ngay; EC tự reset khi reboot — không thí nghiệm nào để lại trạng thái kẹt.
- **Code:** mọi thay đổi theo phase, mỗi phase một nhánh/PR riêng; revert phase không đụng phase trước. Phase 3 (power) tắt được độc lập bằng cấu hình, không ảnh hưởng fan/RGB.
- **Dữ liệu:** profile/curve lưu ở file người dùng; `purge` xóa sạch như hành vi `.deb` hiện tại.
- **Cấm trong mọi phase:** ghi EC register chưa qua G2, flash firmware/BIOS, ghi power ngoài khoảng báo, chạy soak không có đường thoát firmware-auto đã kiểm chứng trước.
