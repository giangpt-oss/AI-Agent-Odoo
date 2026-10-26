# Tổng quan dự án

[Trang chính](../README.md) · [Hướng dẫn lập trình viên](DEVELOPER_GUIDE.md)

## Dự án giải quyết việc gì?

Nhân viên thường phải mở nhiều ứng dụng để xem lịch họp, email và việc cần làm. Dự án gom các thông tin đó vào Telegram để người dùng có thể kiểm tra nhanh trong một cuộc trò chuyện.

Repo này là phần **Agent**, xử lý yêu cầu và tổng hợp phản hồi. Phần đăng nhập, liên kết Google/Odoo và giao tiếp Telegram do **Auth Gateway** ở repo khác đảm nhiệm.

## Một ví dụ sử dụng

Nhân viên gửi **“Hôm nay tôi có gì?”**. Khi tài khoản đã được liên kết và cấp đủ quyền, Agent tổng hợp:

- Các sự kiện Google Calendar trong ngày.
- Công việc cá nhân quá hạn và đến hạn hôm nay.
- Số thư chưa đọc trong Inbox và tối đa 5 thư chưa đọc gần nhất do Gateway trả về.

Nếu một nguồn chưa kết nối hoặc truy xuất thất bại, Agent thông báo phần đó và vẫn hiển thị những phần lấy được.

Điểm tin hiện được gọi theo yêu cầu. Ví dụ này không có nghĩa là hệ thống đã được cấu hình tự gửi mỗi sáng.

## Hiện trạng chức năng

| Chức năng | Hiện có trong luồng Gateway |
|---|---|
| Điểm tin trong ngày | Có — gửi `/today` hoặc “Hôm nay tôi có gì?” |
| Danh sách công việc cá nhân | Có — gửi “Danh sách công việc”; hiển thị trạng thái và hạn |
| Xem thông tin tài khoản | Có — gửi “Tôi là ai?”; thông tin phụ thuộc dữ liệu Gateway cung cấp |
| Kiểm tra liên kết dịch vụ | Có — gửi “Kiểm tra kết nối” |
| Trò chuyện tự do bằng mô hình AI | Chưa được mở trong luồng này |
| Tạo/sửa công việc, gửi email, tạo lịch hoặc gửi file | Chưa được mở trong luồng này |
| Tra cứu CRM qua Agent | Chưa được mở trong luồng này; lệnh CRM riêng của Gateway do bên Gateway quản lý |

Danh sách công việc hiện không có bước tự sắp xếp ưu tiên. Email chưa đọc cũng không được Agent chấm điểm “quan trọng”.

Repo có thêm các module AI và nghiệp vụ của chế độ kết nối trực tiếp. Việc có mã nguồn của một module không đồng nghĩa chức năng đó đã sẵn sàng cho nhân viên qua Gateway.

## Đã kiểm chứng đến đâu?

Agent có bộ kiểm thử cho định danh, xử lý yêu cầu, cách ly công việc theo chủ sở hữu, định dạng phản hồi và xử lý lỗi nguồn dữ liệu. Test dùng dữ liệu tạm và giả lập kết nối ngoài.

Kết quả test giúp kiểm tra hành vi của mã. Nó chưa thay thế việc chạy thử với tài khoản Google/Odoo thật hoặc thử tải nhiều người dùng. Tài liệu này không cam kết thời gian phản hồi hay mức tiết kiệm thời gian khi chưa có số liệu đo thực tế.

Có thể xem cách chạy kiểm thử trong [hướng dẫn lập trình viên](DEVELOPER_GUIDE.md#1-chạy-test-trước).

## Giá trị kỳ vọng và bước tiếp theo

Giá trị kỳ vọng là giúp nhân viên bớt thao tác chuyển ứng dụng và có một nơi xem công việc trong ngày.

Bước tiếp theo được đề xuất là thử với một nhóm nhỏ, ghi nhận kết quả đúng/sai, độ trễ và nhu cầu thực tế. Các thao tác ghi dữ liệu cần được thiết kế, kiểm tra quyền và xác nhận riêng trước khi mở cho người dùng.

Báo cáo các giai đoạn trước được giữ tại [docs/reports](reports/); không dùng những tuyên bố trong báo cáo cũ làm hiện trạng vận hành hôm nay.
