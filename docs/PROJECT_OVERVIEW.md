# Tổng Quan Dự Án AI Agent Dành Cho Quản Lý (Project Overview)

> **Cập nhật ngày:** 26/10/2026  
> **Commit đối chiếu:** `24bf04c` (nhánh `fix/auth-gateway-integration`)  
> **Trạng thái:** Đã hoàn thành giai đoạn kiểm chứng tích hợp nội bộ giữa AI Agent và External Auth Gateway; sẵn sàng thử nghiệm diện hẹp (pilot nội bộ). Chưa triển khai mở rộng ở quy mô production toàn doanh nghiệp.  
> **Thời gian đọc dự kiến:** 5 phút.

---

## 1. Bài toán doanh nghiệp & Nhóm người dùng phục vụ

### Vấn đề thực tế
Hàng ngày, cán bộ nhân viên thường phải chuyển đổi qua lại giữa nhiều công cụ rời rạc:
- Kiểm tra lịch họp trên Google Calendar.
- Đọc và lọc thư quan trọng trên Gmail.
- Ghi nhận và theo dõi danh sách công việc cá nhân.
- Tra cứu dữ liệu kinh doanh và nhân sự trên hệ sinh thái Odoo ERP.

Khi nhân viên di chuyển, công tác hoặc đang họp, việc mở máy tính xách tay hoặc đăng nhập nhiều ứng dụng di động độc lập rất bất tiện. Mặt khác, việc kết nối trực tiếp các công cụ AI công cộng vào dữ liệu nội bộ tiềm ẩn nguy cơ rò rỉ dữ liệu và lộ thông tin xác thực.

### Nhóm người dùng phục vụ
- **Cán bộ nhân viên văn phòng:** Muốn nắm nhanh thông tin đầu ngày và việc cần làm chỉ với một thao tác trên ứng dụng trò chuyện Telegram quen thuộc.
- **Nhân viên kinh doanh & vận hành:** Cần tra cứu nhanh thông tin công việc và kết nối tài khoản mà không phải thao tác đăng nhập phức tạp trên điện thoại.
- **Bộ phận công nghệ & bảo mật:** Yêu cầu một kiến trúc phân tách ranh giới rõ ràng: nơi quản lý tài khoản và khóa bảo mật (Gateway) tách biệt hoàn toàn với nơi xử lý nghiệp vụ AI (Agent).

---

## 2. Ba tình huống sử dụng thực tế (Use Cases)

Hệ thống hiện phục vụ nhân viên qua bot Telegram với 3 tình huống nghiệp vụ cụ thể:

### Tình huống 1: Điểm tin đầu ngày (`/today`)
- **Hành động của nhân viên:** Gửi lệnh `/today` hoặc nhắn *"Hôm nay tôi có gì?"* trên Telegram.
- **Hệ thống xử lý:**
  1. Gateway nhận diện nhân viên qua Telegram Chat ID và chuyển tiếp yêu cầu sang AI Agent.
  2. AI Agent truy xuất dữ liệu lịch làm việc trong ngày từ Google Calendar, số thư chưa đọc từ Gmail và các công việc cá nhân đến hạn.
  3. AI Agent tổng hợp thành một bản tin xúc tích: thời gian các cuộc họp, việc quá hạn, việc cần làm trong ngày và số thư cần xử lý.
- **Kết quả:** Nhân viên nắm toàn bộ lịch trình ngày mới trong vòng 10 giây mà không cần mở 3 ứng dụng khác nhau.

### Tình huống 2: Tra cứu công việc cá nhân
- **Hành động của nhân viên:** Nhắn *"Công việc cần làm của tôi"* hoặc *"Danh sách việc hôm nay"*.
- **Hệ thống xử lý:** AI Agent nhận diện định danh nhân viên và truy xuất kho công việc cá nhân, phân tách theo mã nhân sự.
- **Kết quả:** Hiển thị rõ ràng các đầu việc theo thứ tự ưu tiên và thời hạn (quá hạn, đến hạn hôm nay, sắp tới). Đảm bảo nhân viên không thể nhìn thấy công việc của nhân sự khác.

### Tình huống 3: Kiểm tra trạng thái liên kết tài khoản
- **Hành động của nhân viên:** Nhắn *"Trạng thái tài khoản"* hoặc *"Kiểm tra kết nối"*.
- **Hệ thống xử lý:** AI Agent liên hệ Gateway để kiểm tra liên kết: tài khoản Odoo đã liên kết thành công chưa, Google Calendar và Gmail đã cấp quyền truy cập hợp lệ hay chưa.
- **Kết quả:** Phản hồi thông tin rõ ràng về các dịch vụ đã sẵn sàng hoặc hướng dẫn nhân viên truy cập Portal nếu cần cấp lại quyền (ví dụ khi token hết hạn).

---

## 3. Bảng hiện trạng chức năng

Bảng dưới đây mô tả trung thực hiện trạng của từng tính năng dựa trên mã nguồn hiện tại, phân định rõ giữa phần đã kiểm chứng và phần đang phát triển:

| Nhóm chức năng | Tên tính năng | Trạng thái hiện tại | Phạm vi kiểm chứng | Ghi chú vận hành |
|---|---|---|---|---|
| **Định danh & Bảo mật** | Xác thực danh tính 2 chiều giữa Gateway và Agent | **Đã triển khai** | **Đã kiểm chứng** (Offline loopback & Unit tests) | Dùng secret độc lập, độ dài tối thiểu 32 ký tự, chống timing attack. |
| **Định danh & Bảo mật** | Cách ly dữ liệu công việc cá nhân theo nhân sự | **Đã triển khai** | **Đã kiểm chứng** (Unit & Integration tests) | Sử dụng `owner_key` dạng `{tenant}:{db}:{odoo_uid}`, ngăn truy cập chéo. |
| **Tổng hợp điểm tin** | Bản tin ngày mới `/today` từ dữ liệu thật | **Đã triển khai** | **Đã kiểm chứng** (Mock connector & Tích hợp 2 ứng dụng) | 100% dữ liệu thực từ Calendar, Gmail, Task; không sử dụng dữ liệu giả lập trong prompt. |
| **Tra cứu thông tin** | Xem danh sách công việc cá nhân qua Telegram | **Đã triển khai** | **Đã kiểm chứng** (Unit tests) | Phân loại việc quá hạn và việc trong ngày. |
| **Tra cứu thông tin** | Tra cứu trạng thái kết nối Google / Odoo | **Đã triển khai** | **Đã kiểm chứng** (Unit tests) | Khớp đúng cấu trúc trả về từ Gateway. |
| **Khả năng phục hồi** | Chống gửi trùng tin và cơ chế hàng đợi bền vững | **Đã triển khai** (Phía Gateway) | **Đã kiểm chứng** (Tích hợp 2 service) | Ngăn chặn spam tin nhắn khi gặp timeout hoặc restart dịch vụ. |
| **Tương tác mở rộng** | Tạo lịch, gửi email, cập nhật việc từ Telegram | **Đang phát triển** | Chưa kiểm chứng | Dự kiến triển khai sau khi hoàn tất quy trình phê duyệt 2 bước. |
| **Tích hợp sâu Odoo** | Tra cứu CRM/Đơn hàng trên luồng Gateway Telegram | **Đang phát triển** | Đã có ở chế độ Direct; chưa mở qua Gateway | Cần hoàn thiện phân quyền RBAC đa phòng ban trên Gateway. |

> [!NOTE]
> **Phạm vi kiểm chứng:** Hệ thống đã vượt qua 169 kiểm thử tự động nội bộ và thử nghiệm tích hợp gọi HTTP loopback giữa hai ứng dụng Gateway và Agent. Hiện tại dự án **chưa** chạy tải nặng với số lượng lớn người dùng đồng thời trên môi trường production.

---

## 4. Giá trị kỳ vọng mang lại

- **Tiết kiệm thao tác cho nhân viên:** Giảm bớt số lần phải chuyển đổi giữa nhiều ứng dụng trên điện thoại di động vào mỗi sáng.
- **Độ tin cậy cao:** AI đóng vai trò người tổng hợp và trích xuất dữ liệu có cấu trúc thay vì tự do suy đoán (giảm thiểu hiện tượng ảo giác thông tin).
- **An toàn bảo mật:** AI Agent không lưu trữ mật khẩu Odoo hay Google OAuth token; toàn bộ ranh giới nhạy cảm do Gateway phụ trách.
- **Không tự ý can thiệp dữ liệu:** Các tính năng ghi dữ liệu được kiểm soát chặt chẽ, không tự động kích hoạt khi chưa có cơ chế phê duyệt rõ ràng.

*(Dự án cam kết chỉ báo cáo các chỉ số hiệu quả sau khi tiến hành đo đạc thực tế trong giai đoạn thử nghiệm pilot).*

---

## 5. Giới hạn hiện tại & Lộ trình ưu tiên tiếp theo

### Giới hạn hiện tại
1. **Chế độ một chiều (Chỉ đọc):** Hiện tại thông qua Telegram và Gateway, nhân viên chỉ có thể đọc điểm tin, xem việc và kiểm tra trạng thái; chưa thể tạo mới sự kiện hay gửi email.
2. **Tập ý định có chọn lọc:** Ở chế độ tích hợp Gateway, AI Agent chỉ tiếp nhận các yêu cầu hợp lệ được cấu hình sẵn (`/today`, trạng thái tài khoản, danh sách công việc). Các câu hỏi ngoài phạm vi sẽ được từ chối an toàn để tránh phản hồi sai lệch.
3. **Môi trường triển khai:** Mới được kiểm chứng trên môi trường máy chủ phát triển nội bộ; chưa có hệ thống giám sát phân tán (APM) chuyên dụng.

### Lộ trình ưu tiên tiếp theo
1. **Ưu tiên 1 (Ngay trong tháng tới):** Triển khai thử nghiệm pilot nội bộ trong quy mô nhóm nhỏ (5–10 người dùng) để ghi nhận độ trễ phản hồi và phản hồi về độ tiện dụng của điểm tin.
2. **Ưu tiên 2:** Thiết lập hệ thống giám sát (monitoring & logging) tập trung để theo dõi tỷ lệ thành công của các cuộc gọi AI và Gateway.
3. **Ưu tiên 3:** Nghiên cứu và thiết kế luồng xác nhận người dùng (confirmation flow) an toàn cho thao tác tạo công việc và tạo lịch họp qua Telegram.

---

## 6. Tài liệu tham khảo liên quan

- [Hướng dẫn dành cho lập trình viên (DEVELOPER_GUIDE.md)](DEVELOPER_GUIDE.md): Chi tiết cài đặt môi trường, chạy kiểm thử và kiến trúc mã nguồn.
- [Đặc tả kỹ thuật tích hợp Gateway (INTEGRATION_GATEWAY.md)](INTEGRATION_GATEWAY.md): Chi tiết hợp đồng API, cấu hình secret và cơ chế hàng đợi.
- [Báo cáo lưu trữ lịch sử](reports/): Báo cáo công việc các giai đoạn trước để tra cứu bối cảnh ban đầu.
