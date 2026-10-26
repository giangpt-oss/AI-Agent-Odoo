# API Gateway dành cho Agent

[Trang chính](../README.md) · [Hướng dẫn lập trình viên](DEVELOPER_GUIDE.md)

Tài liệu này mô tả cách Agent sử dụng API của dịch vụ Auth Gateway bên ngoài. Người phụ trách dự án cấu hình và vận hành Gateway; intern phát triển Agent và sử dụng URL/cấu hình tích hợp được cấp. Bạn không cần clone, cài đặt hoặc tự cấu hình Gateway.

Nếu mới đọc repo, hãy bắt đầu từ hướng dẫn lập trình viên. Test của Agent không cần Gateway đang chạy.

## 1. Ranh giới hai bên

| Gateway — repo riêng | Agent — repo này |
|---|---|
| Liên kết Odoo, Google và Telegram | Nhận turn và xác minh lại danh tính |
| Giữ credential, gọi API Google/Odoo | Gọi API nội bộ Gateway để đọc dữ liệu |
| Nhận/gửi Telegram, quản lý hàng đợi và giao phản hồi | Xử lý các nhóm yêu cầu được mở và trả nội dung |
| Portal kết nối/ngắt kết nối tài khoản | Kho công việc cá nhân trong Agent |

Trong gateway mode, Agent không cần repo Gateway trên cùng ổ đĩa. Hai bên cần truy cập HTTP tới nhau và thống nhất URL, secret, request/response.

Cơ chế queue, retry và đối soát Telegram thuộc Gateway. Tài liệu này không hướng dẫn sửa database hoặc worker của Gateway.

## 2. Cấu hình Agent

Từ thư mục gốc repo:

```powershell
Copy-Item .env.example .env
```

Đặt các biến sau trong `.env` bằng giá trị của môi trường phát triển được cấp:

```dotenv
INTEGRATION_MODE=gateway
GATEWAY_BASE_URL=http://127.0.0.1:8080
GATEWAY_TO_AGENT_SECRET=<secret-cho-chieu-gateway-den-agent>
AGENT_TO_GATEWAY_SECRET=<secret-cho-chieu-agent-den-gateway>
```

Hai secret phải khác nhau, mỗi secret ít nhất 32 ký tự. Nội dung trong dấu `<...>` chỉ mô tả trường cần điền, không phải secret để dùng nguyên.

- `GATEWAY_TO_AGENT_SECRET`: Gateway dùng khi gọi endpoint Agent.
- `AGENT_TO_GATEWAY_SECRET`: Agent dùng khi gọi các API Gateway.

Mỗi biến phải khớp giữa hai dịch vụ. Người phụ trách Gateway cấu hình URL trỏ về Agent và bật chuyển tiếp bên Gateway.

Chạy Agent ở cổng đã thống nhất:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

URL loopback trong ví dụ chỉ áp dụng khi hai dịch vụ cùng máy. Khi chạy khác máy hoặc container, dùng địa chỉ mà dịch vụ bên kia truy cập được.

## 3. Gateway gọi Agent

**Endpoint:** `POST /internal/v1/telegram/turn`

**Header:**

```http
Authorization: Bearer <GATEWAY_TO_AGENT_SECRET>
Content-Type: application/json
```

Ví dụ dữ liệu giả:

```json
{
  "request_id": "tg-1001",
  "gateway_user_id": 7,
  "telegram_user_id": 7001,
  "chat_id": 7001,
  "text": "/today",
  "received_at": "2026-10-26T08:00:00+07:00"
}
```

ID người dùng và chat phải đến từ liên kết đã được Gateway xác minh. Agent gọi lại API identity và đối chiếu `gateway_user_id` trước khi xử lý.

Phản hồi có dạng:

```json
{
  "request_id": "tg-1001",
  "messages": [
    "Nội dung phản hồi cho người dùng."
  ]
}
```

`messages` là danh sách chuỗi. Gateway chịu trách nhiệm gửi các chuỗi này lên Telegram. HTTP 200 chỉ cho biết Agent đã tạo phản hồi; nội dung phản hồi vẫn có thể thông báo chưa kết nối hoặc không hỗ trợ yêu cầu.

Thiếu/sai Bearer secret trả HTTP 401; request sai schema trả HTTP 422. Xem schema trong [internal.py](../app/api/v1/internal.py).

## 4. Agent gọi lại Gateway

Mọi lời gọi dùng header:

```http
Authorization: Bearer <AGENT_TO_GATEWAY_SECRET>
```

[GatewayClient](../app/services/gateway_client.py) thực hiện các lời gọi sau:

| API Gateway | Dữ liệu gửi | Agent dùng kết quả để |
|---|---|---|
| `POST /internal/v1/identity/resolve` | JSON gồm `telegram_user_id`, `chat_id` | Xác minh nhân viên; lấy `user_id`, `tenant`, `database`, `odoo_uid`, tên, login, múi giờ, `job_title`, `department`, `permissions` và `authorization_checked_at` |
| `GET /internal/v1/users/{user_id}/connections` | User ID trong URL | Đọc trạng thái Odoo/Telegram/Google và `google.scopes` |
| `GET /internal/v1/users/{user_id}/calendar/events` | Query `time_min`, `time_max` dạng ISO có timezone | Đọc danh sách `events` cho khoảng ngày được yêu cầu |
| `GET /internal/v1/users/{user_id}/gmail/unread` | User ID trong URL | Đọc `total_unread` và danh sách `messages` |

Ví dụ phần Google trong phản hồi connections:

```json
{
  "status": "OK",
  "user_id": 7,
  "google": {
    "status": "OK",
    "email": "employee@example.invalid",
    "scopes": [
      "https://www.googleapis.com/auth/calendar.events",
      "https://www.googleapis.com/auth/gmail.readonly"
    ]
  }
}
```

Đây là ví dụ trích phần Google, không phải toàn bộ payload connections; phản hồi thực còn có `odoo` và `telegram`. Không dùng `status="connected"` hoặc tự thêm hai boolean `calendar`/`gmail` để thay cho contract này.

Với dữ liệu lịch/email, Agent phân biệt:

| Trạng thái | Ý nghĩa |
|---|---|
| `OK` | Truy xuất thành công; danh sách rỗng là một kết quả hợp lệ |
| `NOT_CONNECTED` | Chưa liên kết dịch vụ |
| `PERMISSION_DENIED` | Thiếu quyền truy cập |
| `RECONNECT_REQUIRED` | Cần kết nối lại tài khoản |
| `TIMEOUT` / `ERROR` | Không lấy được dữ liệu |

`total_unread=null` nghĩa là chưa xác định được tổng, không phải không có thư. Công việc cá nhân lấy từ [LocalTaskProvider](../app/providers/tasks/local.py), không phải API công việc của Gateway.

## 5. Kiểm tra và xử lý lỗi

| Hiện tượng | Kiểm tra trước |
|---|---|
| Agent không khởi động trong gateway mode | Hai secret đã được điền, đủ dài và khác nhau chưa? |
| Gateway gọi Agent nhận 401 | Header Bearer và `GATEWAY_TO_AGENT_SECRET` có khớp không? |
| Agent không xác minh được danh tính | URL Gateway, secret chiều Agent → Gateway, liên kết Telegram và trạng thái nhân viên |
| Điểm tin thiếu lịch hoặc Gmail | Trạng thái nguồn trong phản hồi; tài khoản đã liên kết và cấp quyền chưa? |
| HTTP 200 nhưng trả “chưa hỗ trợ” | Câu hỏi có thuộc các nhóm trong [README](../README.md#hiện-tại-dùng-được-gì) không? |

Các kiểm thử liên quan:

- [Endpoint turn](../tests/integration/test_internal_turn.py).
- [Dispatcher](../tests/unit/test_gateway_dispatcher.py).
- [Contract trạng thái kết nối](../tests/unit/test_connection_formatter_contract.py).
- [Điểm tin](../tests/unit/test_morning_briefing_real.py).

Test dùng mock giúp kiểm tra Agent. Kiểm chứng hai dịch vụ và tài khoản thật là một bước riêng; không suy ra kết quả đó chỉ từ test của repo này.
