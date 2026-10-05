# Enterprise AI Agent - Trợ lý cá nhân Doanh nghiệp (Odoo Cloud + Google Workspace)

Hệ thống AI Agent chạy hoàn toàn **độc lập bên ngoài Odoo**, coi Odoo Cloud như một dịch vụ ERP bên ngoài thông qua API (JSON-2 / JSON-RPC), giao tiếp với người dùng qua Telegram.

---

## 🏛️ Kiến trúc Phân tầng (Layered Architecture)

```
[Telegram 1-1 Chat]
       ↓
[FastAPI Gateway]  (Webhook, Identity Resolver, Kill Switch Interception)
       ↓
[LangGraph Orchestrator] (Intent Analyzer, StateGraph, Checkpointer)
       ↓
[Permission Engine] (Layer 1 Security: Fine-grained RBAC & Auto-Rejection)
       ↓
[User Self-Confirmation] (Xác nhận 2 bước cho Write Actions)
       ↓
[Tool Layer & Scoped Circuit Breaker] (Fast-Fail khi Odoo 429)
       ↓
[Connectors]
 ├── OdooConnector (JSON-RPC / JSON-2 API + Layer 2 Odoo ACL)
 ├── GoogleConnector (Gmail & Calendar OAuth2 + Fernet AES-256)
 └── TelegramConnector
```

---

## 🚀 Hướng dẫn Khởi chạy Nhanh

### 1. Khởi chạy bằng Docker Compose (Khuyến nghị cho Production)
```bash
cd ai-agent

# Tạo file môi trường từ template
cp .env.example .env

# Khởi chạy cụm service (Agent, Postgres, Redis)
docker compose up -d --build

# Xem logs thời gian thực
docker compose logs -f ai-agent
```

### 2. Khởi chạy Local Development (Python Virtualenv)
```powershell
cd ai-agent

# Kích hoạt môi trường ảo
.\.venv\Scripts\Activate.ps1

# Chạy server FastAPI
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 🧪 Kiểm thử Hệ thống (Test Suite)
Dự án được bảo chứng bởi **37 ca kiểm thử tự động**:
```powershell
# Chạy toàn bộ 37 ca test
pytest tests/ -v

# Chạy riêng kịch bản End-to-End
pytest tests/test_e2e_full_system.py -v -s
```

---

## 🛡️ Các Endpoints Quản trị & Vận hành

| Phương thức | Endpoint | Mô tả |
| :--- | :--- | :--- |
| `GET` | `/api/v1/health` | Kiểm tra tình trạng sống của Gateway |
| `GET` | `/api/v1/system/status` | Kiểm tra Kill Switch và trạng thái Circuit Breaker |
| `POST` | `/api/v1/system/kill-switch` | Bật/tắt chế độ bảo trì khẩn cấp (Cần header `x-admin-key`) |
| `POST` | `/api/v1/telegram/webhook` | Webhook tiếp nhận tin nhắn từ Telegram Bot |
| `POST` | `/api/v1/webhooks/odoo` | Tiếp nhận sự kiện Passive Notification từ Odoo |
| `GET` | `/api/v1/oauth/google/authorize` | Sinh URL xác thực Google OAuth cho nhân viên |
| `GET` | `/api/v1/oauth/google/callback` | Callback tiếp nhận code Google và lưu token mã hóa |
