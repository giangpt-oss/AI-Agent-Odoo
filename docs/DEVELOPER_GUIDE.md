# Hướng Dẫn Dành Cho Lập Trình Viên (Developer Guide)

> **Dành cho:** Kỹ sư backend mới và Thực tập sinh (Intern) phát triển module AI Agent.  
> **Repository:** `D:\Module AI - Agent`  
> **Ngôn ngữ & Runtime:** Python 3.11  
> **Mục tiêu:** Giúp bạn thiết lập môi trường chuẩn, chạy kiểm thử, hiểu cấu trúc code, nắm bắt luồng xử lý và tự tin đóng góp mã nguồn.

---

## 1. Thiết lập môi trường phát triển & Chạy kiểm thử

Thực hiện lần lượt các bước sau từ thư mục gốc của repository:

### Bước 1: Chuẩn bị Python 3.11
Dự án yêu cầu **Python 3.11**. Kiểm tra phiên bản trên máy:
```powershell
py -3.11 --version
# hoặc: python --version
```

### Bước 2: Tạo môi trường ảo (Virtual Environment)
Tạo virtual environment mang tên `.venv` để cô lập thư viện:
```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
```
*(Nếu gặp lỗi Execution Policy trên PowerShell, mở terminal với quyền Administrator và chạy `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`).*

### Bước 3: Cài đặt thư viện phụ thuộc
Nâng cấp `pip` và cài đặt danh sách dependencies từ `requirements.txt`:
```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Bước 4: Chạy một bài kiểm thử đơn lẻ để xác thực
Để đảm bảo môi trường đã hoạt động trơn tru, hãy chạy một bài test đơn vị nhỏ:
```powershell
python -m pytest tests/unit/test_task_formatter.py -v
```
Nếu màn hình hiển thị màu xanh (`PASSED`), môi trường của bạn đã sẵn sàng.

### Bước 5: Chạy toàn bộ bộ kiểm thử (Test Suite)
Chạy toàn bộ 169 kiểm thử tự động của dự án:
```powershell
python -m pytest -q
```
Kết quả kỳ vọng: **`169 passed, 1 skipped`**.

---

## 2. Phân biệt: Môi trường chạy Test vs Môi trường Tích hợp thật

Một trong những sai lầm thường gặp của lập trình viên mới là nhầm lẫn giữa việc chạy test và chạy ứng dụng thật:

| Tiêu chí | Chạy Bộ Kiểm Thử (`pytest`) | Chạy Tích Hợp Thật (Runtime) |
|---|---|---|
| **Yêu cầu cài đặt** | Chỉ cần Python 3.11 và dependencies trong `requirements.txt`. | Cần Python 3.11, file cấu hình `.env`, dịch vụ Gateway (nếu chạy gateway mode) hoặc API Key/Odoo/Redis (nếu chạy direct mode). |
| **Dữ liệu & Mạng** | Không cần mạng Internet; các kết nối ngoài được giả lập bằng Test Client và Mock Object. | Cần kết nối mạng nội bộ hoặc Internet để trao đổi dữ liệu với Gateway hoặc dịch vụ ngoài. |
| **File `.env`** | Không bắt buộc có `.env` thực tế; các bài test tự cung cấp mock settings hoặc dùng test runner cấu hình sẵn. | Bắt buộc phải có file `.env` hợp lệ chứa các secret và thông số kết nối. |

> [!WARNING]
> Hệ thống hiện **không** có chế độ "demo/offline mode" tự động giả lập dữ liệu khi chạy server thật. Khi khởi động ứng dụng thật, hệ thống sẽ kết nối trực tiếp đến các thành phần được cấu hình.

---

## 3. Cấu hình biến môi trường (`.env.example`)

Sao chép file mẫu để tạo file cấu hình cho máy cá nhân:
```powershell
Copy-Item .env.example .env
```

Các biến môi trường được chia thành các nhóm rõ ràng:

1. **Nhóm Cấu hình Chung (`APP_*`, `HOST`, `PORT`):**
   - `APP_ENV`: Môi trường chạy (`development`, `staging`, `production`).
   - `PORT`: Cổng dịch vụ lắng nghe (mặc định: `8000`).
   - `INTEGRATION_MODE`: Chế độ hoạt động (`gateway` hoặc `direct`).
2. **Nhóm Tích hợp External Auth Gateway (Bắt buộc khi `INTEGRATION_MODE=gateway`):**
   - `GATEWAY_BASE_URL`: Địa chỉ của Gateway (mặc định: `http://localhost:8080`).
   - `GATEWAY_TO_AGENT_SECRET`: Khóa bí mật Gateway gửi sang Agent (Agent dùng để xác thực các request gửi đến từ Gateway).
   - `AGENT_TO_GATEWAY_SECRET`: Khóa bí mật Agent gửi sang Gateway (Agent gắn vào header khi gọi lại API nội bộ của Gateway).
3. **Nhóm Dịch vụ Ngoại vi (Dùng khi chạy ở chế độ `direct`):**
   - `DATABASE_URL`, `REDIS_URL`: Địa chỉ PostgreSQL và Redis.
   - `TELEGRAM_BOT_TOKEN`: Token bot Telegram (chỉ dùng khi Agent trực tiếp polling bot).
   - `ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, `ODOO_API_KEY`: Kết nối trực tiếp Odoo ERP.
   - `OPENAI_API_KEY`, `GEMINI_API_KEY`: Khóa gọi mô hình ngôn ngữ lớn (LLM).

> [!IMPORTANT]
> **Vai trò của 2 Service Secret:**  
> `GATEWAY_TO_AGENT_SECRET` và `AGENT_TO_GATEWAY_SECRET` là hai chìa khóa độc lập bảo vệ ranh giới mạng nội bộ giữa Gateway và Agent. Cả hai secret này phải có độ dài tối thiểu **32 ký tự** và **hoàn toàn khác nhau**. Không bao giờ commit file `.env` chứa secret thật lên Git.

---

## 4. Bản đồ kiến trúc thư mục (Architecture Overview)

Dự án được cấu trúc theo mô hình phân tầng trách nhiệm:

| Thư mục | Trách nhiệm chính | File tiêu biểu |
|---|---|---|
| `app/api/` | Định nghĩa các endpoint HTTP (FastAPI routers), tiếp nhận request, xác thực header và trả về response. | [app/api/v1/internal.py](file:///D:/Module%20AI%20-%20Agent/app/api/v1/internal.py), [app/api/v1/router.py](file:///D:/Module%20AI%20-%20Agent/app/api/v1/router.py) |
| `app/services/` | Xử lý logic nghiệp vụ phối hợp (business orchestration) giữa nhiều nguồn dữ liệu. | [app/services/morning_briefing_service.py](file:///D:/Module%20AI%20-%20Agent/app/services/morning_briefing_service.py), [app/services/file_service.py](file:///D:/Module%20AI%20-%20Agent/app/services/file_service.py) |
| `app/domain/` | Các mô hình và quy tắc nghiệp vụ cốt lõi không phụ thuộc vào framework bên ngoài. | [app/domain/task_identity.py](file:///D:/Module%20AI%20-%20Agent/app/domain/task_identity.py), `app/domain/briefing.py` |
| `app/providers/` | Lớp trừu tượng và cài đặt kho dữ liệu (Data Access Layer). | [app/providers/tasks/local.py](file:///D:/Module%20AI%20-%20Agent/app/providers/tasks/local.py), `app/providers/memory/sqlite.py` |
| `app/connectors/` | Các adapter kết nối kỹ thuật tới API dịch vụ ngoài (Odoo XML-RPC, Telegram client). | `app/connectors/odoo/client.py`, `app/connectors/telegram/client.py` |
| `app/skills/` | Các khối chức năng tác vụ độc lập dùng cho Agent ở chế độ Direct. | [app/skills/tasks/task_skills.py](file:///D:/Module%20AI%20-%20Agent/app/skills/tasks/task_skills.py), `app/skills/calendar/` |
| `app/agent/` | Luồng điều phối AI Agent cấp cao dùng LangGraph (State machine, nodes, router). | `app/agent/router.py`, `app/agent/state.py` |
| `app/assistant/` | Trợ lý doanh nghiệp cấp cao (`company_assistant`) đóng gói phân quyền và hội thoại trực tiếp. | [app/assistant/company_assistant.py](file:///D:/Module%20AI%20-%20Agent/app/assistant/company_assistant.py) |
| `tests/` | Toàn bộ các bộ kiểm thử: `unit/` (đơn vị), `integration/` (tích hợp), `security/` (bảo mật), `workflows/`. | [tests/integration/test_internal_turn.py](file:///D:/Module%20AI%20-%20Agent/tests/integration/test_internal_turn.py) |

---

## 5. Khái niệm kỹ thuật căn bản dành cho Intern

Khi đọc mã nguồn của dự án, bạn sẽ gặp 6 khái niệm chính sau:

1. **Request / Response:**
   - Client (Gateway) gửi một HTTP POST request với body dạng JSON (`TelegramTurnRequest`).
   - Server (FastAPI) đọc body, ánh xạ vào schema Pydantic, xử lý và trả về HTTP response với schema `TelegramTurnResponse`.
2. **`async` / `await`:**
   - Ứng dụng chạy trên nền tảng bất đồng bộ `asyncio`.
   - Các thao tác I/O (gọi HTTP sang Gateway, đọc database) được viết bằng `await client.get(...)` để không chiếm dụng luồng thực thi chính trong lúc chờ đợi phản hồi từ mạng.
3. **Dependency Injection (Tiêm phụ thuộc):**
   - Thay vì khởi tạo cố định các đối tượng bên trong hàm (khiến hàm bị phụ thuộc cứng), chúng ta truyền đối tượng qua tham số hàm (ví dụ: `def generate_briefing(..., gateway_client=None, task_provider=None)`).
   - Điều này giúp việc kiểm thử trở nên dễ dàng: trong unit test, ta truyền vào một client giả lập (mock) mà không cần chạm vào logic xử lý chính.
4. **Provider Pattern (Mẫu nhà cung cấp):**
   - Đóng vai trò lớp trung gian che giấu chi tiết lưu trữ. Nghiệp vụ chỉ giao tiếp với phương thức trừu tượng như `list_tasks()`, còn việc dữ liệu nằm trong file JSON, database SQLite hay PostgreSQL do Provider đảm nhận.
5. **Skill Pattern:**
   - Mỗi tác vụ mà trợ lý AI có thể làm (ví dụ: tạo cuộc họp, tìm kiếm ghi chú) được đóng gói thành một lớp `Skill` riêng biệt có schema input/output cụ thể, giúp mở rộng chức năng mà không làm vỡ code cũ.
6. **Mocking trong Kiểm thử:**
   - Giả lập hành vi của các thành phần bên ngoài (như HTTP client, Gateway, LLM) trong môi trường test để đảm bảo test chạy nhanh (< 100ms), không cần kết nối mạng và có kết quả ổn định, có thể dự đoán được.

---

## 6. Giải phẫu luồng xử lý yêu cầu `/today` (End-to-End Walkthrough)

Dưới đây là hành trình chi tiết của một tin nhắn `/today` đi qua hệ thống AI Agent khi chạy ở chế độ Gateway:

```text
[Gateway: POST /internal/v1/telegram/turn]
               │
               ▼
   [1. Xác thực Header Secret & Schema Pydantic]
   (app/api/v1/internal.py :: handle_telegram_turn)
               │
               ▼
   [2. Định danh Nhân sự qua Gateway]
   (GatewayInternalClient.resolve_identity)
               │
               ▼
   [3. Bộ Điều phối Ý định (Dispatcher)]
   (app/api/v1/internal.py :: dispatch_gateway_turn)
               │
               ▼
   [4. Tạo Bản tin Buổi sáng]
   (app/services/morning_briefing_service.py :: generate_briefing)
        │
        ├─► Gọi Gateway: Lấy sự kiện Google Calendar hôm nay
        ├─► Gọi Gateway: Lấy danh sách email Gmail chưa đọc
        └─► Gọi LocalTaskProvider: Lấy công việc của nhân sự theo owner_key
               │
               ▼
   [5. Tổng hợp Bản tin Markdown & Trả về Phản hồi]
```

### Chi tiết các file liên quan và Test kiểm chứng:
- **Tiếp nhận & Xác thực:** `handle_telegram_turn()` tại [app/api/v1/internal.py](file:///D:/Module%20AI%20-%20Agent/app/api/v1/internal.py#L320-L400) kiểm tra `x-gateway-secret` bằng `secrets.compare_digest` để chống timing attack.
- **Phân luồng ý định:** `dispatch_gateway_turn()` tại [app/api/v1/internal.py](file:///D:/Module%20AI%20-%20Agent/app/api/v1/internal.py#L250-L315) nhận diện câu hỏi buổi sáng và chuyển tiếp sang service.
- **Xử lý điểm tin:** `MorningBriefingService.generate_briefing()` tại [app/services/morning_briefing_service.py](file:///D:/Module%20AI%20-%20Agent/app/services/morning_briefing_service.py#L30-L160) tổng hợp dữ liệu thật từ lịch, email và việc cá nhân.
- **Cách ly dữ liệu:** `make_task_owner_key()` tại [app/domain/task_identity.py](file:///D:/Module%20AI%20-%20Agent/app/domain/task_identity.py) bảo đảm task của người nào chỉ hiển thị cho người đó.
- **Test tham khảo:**
  - Test kiểm tra dispatch và phản hồi turn: [tests/unit/test_gateway_dispatcher.py](file:///D:/Module%20AI%20-%20Agent/tests/unit/test_gateway_dispatcher.py)
  - Test kiểm tra tạo điểm tin từ dữ liệu thật: [tests/unit/test_morning_briefing_real.py](file:///D:/Module%20AI%20-%20Agent/tests/unit/test_morning_briefing_real.py)
  - Test tích hợp toàn trình endpoint: [tests/integration/test_internal_turn.py](file:///D:/Module%20AI%20-%20Agent/tests/integration/test_internal_turn.py)

---

## 7. Hai chế độ vận hành: Gateway Mode vs Direct Mode

Dự án hiện có hai luồng kiến trúc tách biệt được điều khiển bởi biến `INTEGRATION_MODE` trong cấu hình:

### 1. Gateway Mode (`INTEGRATION_MODE=gateway`) — **Đọc Phần Này Trước!**
- **Đặc điểm:** Đây là kiến trúc chuẩn của dự án trong giai đoạn hiện tại. Toàn bộ việc nhận/gửi Telegram, lưu trữ token Google và xác thực Odoo do Gateway đảm nhiệm. AI Agent đóng vai trò là một microservice nghiệp vụ nội bộ xử lý request từ Gateway.
- **Lộ trình cho Intern:** **Bạn nên bắt đầu tìm hiểu và đóng góp ở luồng này trước.** Hãy đọc kỹ [app/api/v1/internal.py](file:///D:/Module%20AI%20-%20Agent/app/api/v1/internal.py) và [app/services/morning_briefing_service.py](file:///D:/Module%20AI%20-%20Agent/app/services/morning_briefing_service.py).

### 2. Direct Mode (`INTEGRATION_MODE=direct`)
- **Đặc điểm:** Chế độ độc lập kế thừa từ giai đoạn phát triển ban đầu. Trong chế độ này, Agent tự kết nối trực tiếp với Telegram Bot qua polling/webhook, tự gọi Odoo qua XML-RPC và dùng LangGraph orchestration đa bước.
- **Lộ trình cho Intern:** Chỉ tìm hiểu luồng này sau khi đã làm chủ Gateway mode.

---

## 8. Hướng dẫn sửa một tính năng nhỏ theo phương pháp TDD

Khi được giao một task sửa lỗi hoặc bổ sung chức năng nhỏ, hãy tuân theo quy trình phát triển có kiểm thử (Test-Driven Development - TDD):

1. **Bước 1 — Tìm hoặc tạo file kiểm thử liên quan:**  
   Ví dụ, nếu sửa logic định dạng trạng thái kết nối, mở file [tests/unit/test_connection_formatter_contract.py](file:///D:/Module%20AI%20-%20Agent/tests/unit/test_connection_formatter_contract.py).
2. **Bước 2 — Viết test tái hiện yêu cầu mới (Red):**  
   Thêm test case mô tả chính xác hành vi mong đợi. Chạy test và xác nhận rằng test thất bại:
   ```powershell
   python -m pytest tests/unit/test_connection_formatter_contract.py -v
   ```
3. **Bước 3 — Chỉnh sửa mã nguồn tối thiểu (Green):**  
   Chỉ sửa đúng những dòng code cần thiết trong `app/` để test mới chuyển sang màu xanh (`PASSED`).
4. **Bước 4 — Chạy kiểm tra hồi quy:**  
   Chạy lại toàn bộ test suite để đảm bảo không làm vỡ các chức năng có sẵn:
   ```powershell
   python -m pytest -q
   ```
5. **Bước 5 — Kiểm tra chuẩn định dạng mã nguồn:**  
   Chạy kiểm tra khoảng trắng và định dạng Git:
   ```powershell
   git diff --check
   ```
6. **Bước 6 — Tạo commit và Pull Request (PR):**  
   Commit với thông điệp rõ ràng theo chuẩn Conventional Commits (ví dụ: `fix(briefing): ...` hoặc `test(formatter): ...`).

---

## 9. Ranh giới phối hợp nhóm & Quy tắc an toàn

- **Phạm vi trách nhiệm:** Bạn phụ trách phát triển mã nguồn trong repository **AI Agent** (`D:\Module AI - Agent`).
- **Ranh giới với Gateway:** Tuyệt đối không tự ý sửa đổi code hoặc cơ sở dữ liệu của External Auth Gateway. Gateway được quản lý bởi một nhóm/người phụ trách riêng.
- **Thay đổi Hợp đồng API:** Nếu một tính năng mới yêu cầu thêm trường dữ liệu trong request gửi từ Telegram, hoặc cần Gateway cung cấp thêm API mới:
  1. Thảo luận và thống nhất hợp đồng dữ liệu với người phụ trách Gateway.
  2. Cập nhật tài liệu hợp đồng tích hợp [INTEGRATION_GATEWAY.md](INTEGRATION_GATEWAY.md).
  3. Cả hai phía cùng cập nhật schema và viết test tương ứng.
- **Bảo mật Secret:** Tuyệt đối không bao giờ hardcode API Key, mật khẩu database hay service secret vào code hoặc commit lên Git. Mọi giá trị nhạy cảm phải đọc từ biến môi trường thông qua `app/core/config.py`.

---

## 10. Tài liệu kỹ thuật liên quan

- [Tổng quan dự án dành cho quản lý (PROJECT_OVERVIEW.md)](PROJECT_OVERVIEW.md)
- [Đặc tả kỹ thuật tích hợp Gateway (INTEGRATION_GATEWAY.md)](INTEGRATION_GATEWAY.md)
- [Báo cáo lịch sử dự án](reports/)
