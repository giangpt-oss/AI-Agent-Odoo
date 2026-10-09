# Enterprise AI Agent

Trợ lý AI dành cho quy trình doanh nghiệp, cung cấp giao diện hội thoại qua Telegram và kết nối với Odoo, Google Workspace cùng các dịch vụ nội bộ. Dự án được triển khai như một backend độc lập, xây dựng bằng FastAPI và dùng LangGraph để điều phối yêu cầu.

## Tổng quan

Tin nhắn Telegram được tiếp nhận qua webhook, gắn với danh tính nhân viên, đưa qua luồng phân tích và kiểm tra quyền, rồi chuyển đến skill hoặc công cụ phù hợp. Với các thao tác ghi, agent có luồng xác nhận của người dùng trước khi thực thi. Các tích hợp bên ngoài được tổ chức qua connector; nhiều chức năng nội bộ có provider riêng cho tác vụ, ghi chú, nhắc nhở, bộ nhớ và workflow.

```text
Telegram ──► FastAPI ──► LangGraph Agent ──► Permission / Confirmation
                              │
                 ┌────────────┼─────────────┐
                 ▼            ▼             ▼
              Odoo        Google APIs    Local providers
           JSON-RPC/2    Gmail/Calendar  Tasks, notes, ...
                              │
                     PostgreSQL / Redis
```

## Tính năng

- Hội thoại riêng với Telegram Bot, định tuyến yêu cầu đến các skill nghiệp vụ.
- Tích hợp Odoo cho dữ liệu CRM, nhân sự, đối tác, sản phẩm và đơn hàng.
- Tích hợp Gmail và Google Calendar qua Google OAuth.
- Các chức năng trợ lý cho email, lịch, công việc, nhắc nhở, ghi chú, cuộc họp, tài liệu, bảng tính và workflow.
- Kiểm tra quyền, bước xác nhận cho một số thao tác ghi, kill switch và circuit breaker.
- API cho health check, webhook Telegram/Odoo, Google OAuth và trạng thái hệ thống.

Các tính năng cần dịch vụ ngoài chỉ hoạt động sau khi cấu hình thông tin xác thực, quyền API và webhook tương ứng.

## Công nghệ

| Thành phần | Công nghệ |
| --- | --- |
| Runtime | Python 3.11 |
| API | FastAPI, Uvicorn, Pydantic Settings |
| Agent | LangGraph, LangChain |
| Cơ sở dữ liệu và cache | PostgreSQL, SQLAlchemy, Alembic, Redis |
| Tích hợp | Telegram Bot API, Odoo API, Google OAuth/Gmail/Calendar |
| Kiểm thử | pytest, pytest-asyncio |

## Yêu cầu

- Python 3.11.
- Docker Desktop và Docker Compose để chạy theo cấu hình container; hoặc PostgreSQL và Redis nếu chạy local.
- API key của ít nhất một provider LLM đã cấu hình.
- Thông tin xác thực tương ứng nếu sử dụng Telegram, Odoo hoặc Google Workspace.

## Khởi chạy bằng Docker Compose

Từ thư mục gốc của dự án, tạo file cấu hình môi trường:

```powershell
Copy-Item .env.example .env
```

Mở `.env`, điền các giá trị cần thiết, sau đó chạy:

```bash
docker compose up --build
```

API mặc định lắng nghe tại `http://localhost:8000`. Theo dõi log của backend:

```bash
docker compose logs -f ai-agent
```

Dừng các dịch vụ bằng `Ctrl+C` hoặc `docker compose down`. Dữ liệu PostgreSQL và Redis nằm trong Docker volumes. Lệnh `docker compose down -v` sẽ xóa các volumes đó.

> Cấu hình `docker-compose.yml` hiện phù hợp để khởi chạy và phát triển: mật khẩu PostgreSQL được khai báo trực tiếp trong file và cổng PostgreSQL/Redis được mở ra máy host. Hãy thay đổi cấu hình này, quản lý secrets an toàn và giới hạn truy cập trước khi triển khai trên môi trường dùng chung.

## Khởi chạy local

Khởi động PostgreSQL và Redis trước. Tại thư mục gốc dự án, tạo virtual environment và cài dependencies:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Cập nhật `.env` với URL database/cache và các thông tin xác thực cần dùng. Khởi động API:

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Tài liệu OpenAPI tại `/docs` và `/redoc` bị tắt theo mặc định. Để bật trong môi trường phát triển, đặt `APP_DEBUG=true` trong `.env` rồi khởi động lại server. Không bật debug trên môi trường công khai.

## Cấu hình

Các biến môi trường được khai báo trong `.env.example` và đọc tại `app/core/config.py`.

| Nhóm | Các biến chính |
| --- | --- |
| Ứng dụng | `APP_ENV`, `APP_SECRET_KEY`, `APP_DEBUG`, `HOST`, `PORT` |
| Database / cache | `DATABASE_URL`, `REDIS_URL` |
| LLM | `DEFAULT_LLM_PROVIDER`, `DEFAULT_LLM_MODEL`, `GEMINI_API_KEY`, `OPENAI_API_KEY` |
| Telegram | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` |
| Odoo | `ODOO_URL`, `ODOO_DB`, `ODOO_USERNAME`, `ODOO_API_KEY`, `ODOO_WEBHOOK_SECRET` |
| Google OAuth | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` |
| Mã hóa token | `TOKEN_ENCRYPTION_KEY` |

Có thể tạo Fernet key bằng lệnh:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Không commit `.env` hoặc đưa API key, bot token, mật khẩu database và secret vào mã nguồn. Thay các giá trị mặc định bằng secrets riêng trước khi kết nối dữ liệu thật.

## API

| Phương thức | Đường dẫn | Mục đích |
| --- | --- | --- |
| `GET` | `/` | Thông tin Gateway |
| `GET` | `/api/v1/health` | Health check |
| `GET` | `/api/v1/system/status` | Trạng thái kill switch và circuit breaker |
| `POST` | `/api/v1/system/kill-switch` | Bật/tắt kill switch; cần header `x-admin-key` khớp `APP_SECRET_KEY` |
| `POST` | `/api/v1/telegram/webhook` | Webhook nhận tin nhắn Telegram; cần secret token hợp lệ |
| `POST` | `/api/v1/webhooks/odoo` | Webhook sự kiện Odoo; cần header `x-odoo-webhook-secret` |
| `GET` | `/api/v1/oauth/google/authorize` | Bắt đầu Google OAuth |
| `GET` | `/api/v1/oauth/google/callback` | Callback của Google OAuth |
| `GET`, `POST` | `/auth/odoo-verify` | Trang và xử lý xác minh tài khoản Odoo |

## Kiểm thử

Chạy toàn bộ suite:

```bash
pytest
```

Chạy một file kiểm thử cụ thể:

```bash
pytest tests/test_security.py -v
pytest tests/test_e2e_full_system.py -v
```

## Cấu trúc dự án

```text
app/
├── agent/        # Graph, orchestration, router và các node
├── api/v1/       # API routes và webhooks
├── connectors/   # Kết nối Telegram, Odoo và Google
├── core/         # Cấu hình, database và Redis
├── domain/       # Kiểu dữ liệu và quy tắc nghiệp vụ
├── providers/    # Provider cho tích hợp và lưu trữ cục bộ
├── security/     # Permission, token, kill switch, circuit breaker
├── services/     # Dịch vụ nghiệp vụ
├── skills/       # Skill của trợ lý
└── workflows/    # Workflow tích hợp và dựng sẵn
tests/            # Kiểm thử tự động
scripts/          # Script phát triển và tích hợp
```

## Lưu ý triển khai

- Kiểm tra quyền truy cập của Telegram bot, Odoo API key và Google OAuth trước khi bật tích hợp.
- Thay `APP_SECRET_KEY`, webhook secrets và Fernet key bằng giá trị mới, duy nhất cho từng môi trường.
- Đánh giá việc lưu trữ, ghi log và quyền truy cập trước khi đưa dữ liệu doanh nghiệp thật vào hệ thống.
- Giới hạn mạng truy cập PostgreSQL và Redis; không mở các cổng này ra Internet.
