# Hướng dẫn bắt đầu làm Agent

[Trang chính](../README.md) · [Tổng quan dự án](PROJECT_OVERVIEW.md) · [API tích hợp](INTEGRATION_GATEWAY.md)

Bạn phụ trách **repo Agent độc lập**. Chỉ cần clone repo này để đọc code, chạy test và phát triển. Không cần clone hoặc hiểu mã nguồn Gateway.

## Ai phụ trách phần nào?

| Người phụ trách dự án | Intern phát triển Agent |
|---|---|
| Cấu hình và vận hành Auth Gateway | Phát triển tính năng, API và phản hồi trong repo Agent |
| Thiết lập kết nối Odoo, Google và Telegram | Gọi API Gateway từ Agent và xử lý dữ liệu/lỗi trả về |
| Cung cấp URL và cấu hình tích hợp cho môi trường phát triển | Dùng cấu hình được cấp để chạy Agent khi cần tích hợp |
| Phối hợp thay đổi API phía Gateway | Viết test Agent và đề xuất yêu cầu API khi thiếu dữ liệu |

Bạn không có nhiệm vụ cài đặt hoặc cấu hình Gateway. Phần cần hiểu là dữ liệu gửi/nhận qua API. Bạn vẫn đọc code và chạy test Agent được khi chưa có cấu hình tích hợp.

Hãy bắt đầu bằng test và luồng xử lý yêu cầu từ Gateway; chưa cần đọc toàn bộ các module AI trong repo.

## 1. Chạy test trước

Cần Python 3.11 và kết nối mạng để cài dependencies. Chạy từ thư mục gốc của repo trên PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest tests/unit/test_task_formatter.py -q
```

Lệnh trên dùng thẳng Python trong `.venv`, nên không cần kích hoạt môi trường hoặc thay Execution Policy.

Sau khi test đầu tiên chạy được, chạy toàn bộ:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

[Test fixtures](../tests/conftest.py) tạo dữ liệu tạm và chặn kết nối ngoài trong bộ test. Bạn không cần Gateway, PostgreSQL, Redis, Telegram token hoặc LLM API key thật để bắt đầu chạy test.

Một số test có thể được đánh dấu bỏ qua. Đọc lý do trong kết quả; test vượt qua không có nghĩa bot đã kết nối dịch vụ thật.

## 2. Hiểu một luồng duy nhất: `/today`

```text
Gateway gửi HTTP POST /internal/v1/telegram/turn
  → Agent kiểm tra Authorization: Bearer ...
  → hỏi Gateway xem Telegram user/chat thuộc nhân viên nào
  → nhận diện yêu cầu /today
  → lấy lịch và Gmail qua Gateway, lấy công việc từ kho Agent
  → tạo nội dung phản hồi
  → trả JSON để Gateway gửi Telegram
```

Điểm quan trọng: luồng này chọn ý định bằng các hàm quy tắc. Nó không chạy hội thoại LLM/LangGraph.

Đọc theo thứ tự sau:

| Bước | Đọc ở đâu | Cần hiểu gì? |
|---|---|---|
| 1 | [internal.py](../app/api/v1/internal.py) — `handle_telegram_turn()` | Nhận request, xác minh danh tính, chọn nhóm yêu cầu và trả kết quả |
| 2 | [gateway_client.py](../app/services/gateway_client.py) — `GatewayClient` | Các lời gọi HTTP từ Agent sang Gateway |
| 3 | [morning_briefing_service.py](../app/services/morning_briefing_service.py) — `generate_real_briefing()` | Lấy các nguồn dữ liệu và ghép điểm tin |
| 4 | [task_identity.py](../app/domain/task_identity.py) — `build_owner_key()` | Tạo khóa chủ sở hữu từ tenant, database và Odoo UID |
| 5 | [tasks/local.py](../app/providers/tasks/local.py) — `LocalTaskProvider` | Đọc/ghi công việc trong SQLite và giới hạn theo chủ sở hữu |

Trong service điểm tin còn có `generate_briefing()` cho luồng cũ. **Luồng Gateway dùng `generate_real_briefing()`.** Hai tên này không thay thế cho nhau.

Xem các ví dụ chạy được trong test:

- [Test endpoint nhận turn](../tests/integration/test_internal_turn.py).
- [Test định tuyến yêu cầu](../tests/unit/test_gateway_dispatcher.py).
- [Test điểm tin](../tests/unit/test_morning_briefing_real.py).
- [Test quyền sở hữu công việc](../tests/tasks/test_task_ownership.py).

## 3. Những phần còn lại nằm ở đâu?

| Thư mục | Vai trò |
|---|---|
| `app/api/` | Nhận và trả yêu cầu HTTP |
| `app/services/` | Ghép các bước xử lý nghiệp vụ |
| `app/domain/` và `app/models/` | Kiểu dữ liệu và quy tắc dùng trong ứng dụng |
| `app/providers/` | Làm việc với kho dữ liệu hoặc một nguồn dữ liệu cụ thể |
| `app/connectors/` | Kết nối kỹ thuật với dịch vụ ngoài |
| `app/skills/` | Các tác vụ đóng gói để engine điều phối sử dụng |
| `app/agent/` và `app/assistant/` | Các luồng agent/hội thoại khác, gồm luồng kết nối trực tiếp |
| `tests/` | Ví dụ đầu vào và hành vi cần giữ đúng |

Có hai chế độ cấu hình:

- `gateway`: trọng tâm hiện tại; nhận turn từ dịch vụ Gateway riêng.
- `direct`: luồng kết nối trực tiếp có sẵn trong repo. Đọc sau khi hiểu Gateway hoặc khi được giao việc liên quan.

Đừng giả định một skill sẽ tự được mở qua Gateway khi thêm nó vào registry. Luồng Gateway hiện định tuyến trong `handle_telegram_turn()`.

## 4. Một vài từ cần biết

- **Request/response:** dữ liệu gửi vào và trả về từ một lời gọi HTTP.
- **`async/await`:** cách chờ thao tác I/O mà không giữ luồng xử lý chính trong lúc chờ.
- **Service:** nơi phối hợp các bước để hoàn thành một nghiệp vụ.
- **Provider:** nơi thực hiện thao tác với nguồn dữ liệu, ví dụ `list_tasks()` trong SQLite.
- **Skill:** một tác vụ có đầu vào/đầu ra rõ ràng để engine gọi.
- **Mock:** thành phần giả trong test, thay cho việc gọi dịch vụ thật.

Ví dụ trong [test điểm tin](../tests/unit/test_morning_briefing_real.py), Gateway client được giả lập để kiểm tra phản hồi mà không gọi Google.

## 5. Thử sửa một phần nhỏ

Bắt đầu với [formatter trạng thái công việc](../tests/unit/test_task_formatter.py):

1. Chạy file test và đọc một trường hợp đầu vào/kết quả.
2. Tìm `format_task_list()` trong [internal.py](../app/api/v1/internal.py).
3. Khi được giao thay đổi, thêm test thể hiện kết quả mới trước khi sửa formatter.
4. Chạy lại test liên quan, sau đó chạy toàn bộ suite.
5. Kiểm tra diff rồi tạo PR mô tả thay đổi và kết quả kiểm thử.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_task_formatter.py -q
.\.venv\Scripts\python.exe -m pytest -q
git diff --check
```

Không thay assertion chỉ để che một lỗi đang xảy ra. Nếu thay đổi liên quan API, đối chiếu [tài liệu tích hợp](INTEGRATION_GATEWAY.md) và trao đổi với người phụ trách Gateway.

## 6. Khi cần chạy ứng dụng với Gateway thật

Phần này thực hiện sau khi chạy test và đã có cấu hình phát triển từ người phụ trách.

1. Sao chép `.env.example` thành `.env`.
2. Đặt `INTEGRATION_MODE=gateway`, URL Gateway và hai service secret đúng theo [hướng dẫn tích hợp](INTEGRATION_GATEWAY.md#2-cấu-hình-agent).
3. Khởi động Agent:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Có thể bật `APP_DEBUG=true` trong cấu hình phát triển để xem [OpenAPI](http://127.0.0.1:8000/docs). Endpoint nhận turn chỉ được đăng ký khi chạy gateway mode.

Khởi động được API chưa chứng minh Google/Odoo đã kết nối. Cần Gateway đang chạy và tài khoản đã liên kết. Ứng dụng còn khởi động worker nền và dùng dữ liệu runtime trong `data/`; dùng cấu hình và dữ liệu phát triển khi chạy thử.

Không commit `.env`, token hoặc database runtime. Không cần credential thật để đọc code và chạy bộ test.
