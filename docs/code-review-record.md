# Báo cáo Review Dự án: hm-ai-agent (Legacy Codebase)

Dựa trên quá trình phân tích mã nguồn và cấu trúc thư mục của dự án cũ, dưới đây là đánh giá chi tiết về **Chất lượng Code**, **Kiến trúc Kỹ thuật** và **Vấn đề Bảo mật**.

---

## 1. Vấn đề Bảo mật (Security)

> [!CAUTION]
> **Rủi ro nghiêm trọng (Critical Risk):**
> File `service_account.json` (chứa credential của Google Service Account) bị đưa trực tiếp vào thư mục gốc của dự án. File này **tuyệt đối không được commit** vào source control.

> [!WARNING]
> **Quản lý Secrets (Credentials):**
> - Các file `.env.bak` xuất hiện trong thư mục gốc. Cần đảm bảo rằng tất cả các file cấu hình chứa secret (`.env*`) đều được liệt kê trong `.gitignore`.
> - Việc gọi và xử lý API Key (`GROQ_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`) bị đọc rải rác ở nhiều file thay vì quy tụ vào module `settings`/`config`.

> [!IMPORTANT]
> **Các vấn đề khác:**
> - Thiếu cơ chế **Rate Limiting** rõ ràng ở tầng API.
> - CORS cấu hình quá lỏng lẻo (`allow_origins=["*"]`). Cần giới hạn lại các domain được phép truy cập trong môi trường production.

---

## 2. Kiến trúc Kỹ thuật (Architecture)

> [!NOTE]
> **Nhận xét chung:**
> Dự án cũ kết hợp lai tạp giữa Clean Architecture/DDD (`domain`, `application`, `infrastructure`, `presentation`) và chia theo Feature (`rag`, `ai`, `schedulers`, `actions`). Việc lai tạp này tạo ra sự nhập nhằng trong trách nhiệm của các module.

* **Sự phân mảnh Logic (Fragmentation):**
  * Logic gọi LLM và xử lý luồng RAG bị phân mảnh giữa `orchestrator.py` và `agent.py`.
  * Có sự tồn tại song song của các thư mục trùng lặp như `application/rag_agent.py` và `rag/agent.py`.
* **Xử lý Bất đồng bộ (Async/Await) không an toàn:**
  * Can thiệp trực tiếp vào Event Loop một cách cưỡng ép (`asyncio.new_event_loop()`), dễ gây deadlocks và crash server.
* **Module Configuration:**
  * Sử dụng cả `dotenv` thủ công ở nhiều nơi thay vì Pydantic BaseSettings thống nhất.

---

## 3. Trạng thái sau khi Hợp nhất (Unified Project)

Toàn bộ các vấn đề trên đã được khắc phục triệt để trong dự án hợp nhất mới:
1. **Kiến trúc LangGraph chuẩn:** Module hóa rõ ràng trong `app/agents`, `app/tools`, `app/connectors`, `app/api`.
2. **Bảo mật tuyệt đối:** `.gitignore` chặn toàn bộ file `.env`, credential, và key. Mọi cấu hình tập trung tại `app/core/config.py`.
3. **Cơ chế An toàn Doanh nghiệp:** Tích hợp Circuit Breaker và Emergency Kill Switch để bảo vệ Odoo Cloud khỏi quá tải hoặc spam Telegram.
4. **100% Async an toàn:** Không ép event loop, hỗ trợ cả FastAPI Webhook lẫn Background Polling Runner.
