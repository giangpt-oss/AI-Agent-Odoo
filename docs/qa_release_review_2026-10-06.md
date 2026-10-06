# Kiểm thử release — 06/10/2026

## Kết luận

**Chưa đạt điều kiện đưa vào production.** Các lỗi về phân quyền, xác nhận thao tác, truy cập file và gửi nhắc nhở vi phạm chính các quality gate trong `docs/quality_gates.md`. Báo cáo `FINAL_PRODUCTION_REPORT.md` hiện tuyên bố production ready nhưng chưa phản ánh các lỗi dưới đây.

Phạm vi: đọc mã nguồn các đường đi Telegram polling, FastAPI, xác thực Odoo, LangGraph, skill, file, reminder, email/calendar và knowledge; chạy test tự động và tạo ca âm tính cô lập. Chín ca QA mới dùng mock và dữ liệu tạm, không chủ động gửi lệnh tới Odoo hoặc Telegram thật.

## Kết quả chạy

| Kiểm tra | Kết quả |
| --- | --- |
| `pytest --ignore=tests/scheduler --cov=app` | 87 passed, 1 skipped; coverage `app`: 67% |
| `pytest tests/qa/test_release_blockers.py -rx` | 9 xfailed: chín hành vi sai được tái hiện; `strict=True` sẽ báo XPASS sau khi sửa |
| `pip check` | Không có dependency bị thiếu hoặc xung đột theo metadata đã cài |

Test scheduler gốc bị loại khỏi lượt chạy toàn bộ vì `tests/scheduler/test_scheduler.py` gọi provider Telegram thật khi chạy một nhắc nhở đến hạn. Ca QA thay thế dùng SQLite tạm và mock notification. Coverage 67% không bao gồm `scripts/run_telegram_bot_polling.py`; các đường đi quan trọng vẫn rất thấp: `app/agent/odoo_agent_service.py` 0%, `app/agent/orchestrator.py` 16%, `app/services/odoo_auth_service.py` 20%, `app/services/employee.py` 28%. Một số test knowledge phụ thuộc API Gemini và trạng thái index lưu trên đĩa, nên kết quả của chúng không hoàn toàn độc lập.

## Lỗi đã tái hiện bằng ca QA cô lập

| ID | Mức độ | Hiện tượng và tác động | Nơi cần sửa |
| --- | --- | --- | --- |
| QA-01 | Critical | `../workspace2/private.txt` được chấp nhận khi root là `workspace`: kiểm tra tiền tố chuỗi cho phép thoát sang thư mục cùng tiền tố. | `app/services/file_service.py:12-17`; dùng `Path.is_relative_to()` sau `resolve()` |
| QA-02 | Critical | Một xác nhận của Alice có thể bị yêu cầu cùng skill/tham số của Bob tiêu thụ; không truyền danh tính vào bước approve/consume. | `app/agent/confirmation_manager.py:60-99`, `app/agent/orchestrator.py:106` và callback Telegram; ràng buộc user, chat, request ID, tham số và thời hạn |
| QA-03 | Critical | Skill `WRITE` vẫn được phép khi danh sách quyền rỗng. | `app/services/permissions.py:22-35`; ánh xạ từng skill tới quyền cụ thể, mặc định từ chối |
| QA-04 | Critical | Skill `READ` nhạy cảm vẫn được phép khi danh sách quyền rỗng. Agent Odoo dùng tài khoản service/admin, nên Odoo ACL không thay thế được kiểm tra quyền từng người. | `app/services/permissions.py:23-24`, `app/agent/odoo_agent_service.py:40-47` |
| QA-05 | Critical | Tin đầu tiên “Tạo đơn hàng và tôi đồng ý” tự vượt qua bước xác nhận trong cùng lượt. | `app/agent/nodes/confirmation.py:28-34`; xác nhận phải thuộc lượt tiếp theo và đúng request đang chờ |
| QA-06 | Critical | Tài khoản Odoo thường có login chứa `giangpt` được gán `admin`/`ceo` dù mock Odoo không trả vai trò admin. | `app/services/odoo_auth_service.py:146-155`; lấy quyền từ nhóm/ACL Odoo, bỏ suy luận theo login hoặc UID cứng |
| QA-07 | High | POST `/api/v1/webhooks/odoo` không xác thực, vẫn nhận sự kiện; payload có thể chỉ định `assigned_chat_id` để kích hoạt thông báo. | `app/api/v1/webhooks.py:18-36`; xác minh chữ ký/secret, giới hạn nguồn và chống replay |
| QA-08 | High | Reminder đến hạn được gửi tới chat fallback `123456789`, không tới chủ sở hữu; schema không lưu `user_id`. | `app/providers/reminders/local.py:15-25`, `app/services/scheduler.py:74-83`; lưu owner/chat ID và gửi đúng người |
| QA-09 | High | “Tạo đơn hàng 1 triệu cho khách XYZ” thành `partner_id=1`, `amount_total=50_000_000`; giá trị được gán cứng, không trích xuất từ yêu cầu. | `app/agent/nodes/analyzer.py:40-49`; tra cứu khách, parse tiền, yêu cầu bổ sung nếu thiếu, hiển thị preview đúng |

Mọi ca trên nằm trong `tests/qa/test_release_blockers.py`. Chúng đang `xfail(strict=True)` để suite thường không bị báo đỏ vì lỗi đã biết; release gate cần coi **mọi XFAIL này là blocker**, không phải pass.

## Rủi ro quan sát qua đường đi mã nguồn

1. Telegram polling không gọi kill switch (`scripts/run_telegram_bot_polling.py`), trong khi webhook có kiểm tra (`app/api/v1/telegram.py:42-51`). Nếu hệ thống vận hành bằng polling, bật kill switch có thể không ngăn yêu cầu đi tiếp.
2. Liên kết Telegram ↔ Odoo sau xác thực chỉ ghi vào `DEV_EMPLOYEES_STORE` trong RAM (`app/services/odoo_auth_service.py:169`). Khởi động lại bot làm mất liên kết mới; cửa hàng còn chứa các danh tính/role mẫu gán cứng (`app/services/employee.py:11-44`). Cần lưu bền vững và bỏ tài khoản mẫu trong production.
3. Skill email và calendar khởi tạo `FakeEmailProvider`/`FakeCalendarProvider` trực tiếp (`app/skills/email/email_skills.py:8-11`, `app/skills/calendar/calendar_skills.py:9-12`). Trạng thái tạo/gửi giả tồn tại trong object mới và không phản ánh Google Workspace thật.
4. Google OAuth `state` chỉ là ID đầu vào; callback trả “liên kết thành công” sau khi đổi token nhưng không lưu token (`app/api/v1/oauth.py:9-33`). Cần state ngẫu nhiên gắn session, kiểm tra khi callback và lưu refresh token đã mã hóa.
5. Bộ đánh giá router mô phỏng chọn skill bằng từ khóa, không gọi router thật, và tự ghi `Forbidden Skill Rate = 0` (`app/evaluation/suites/router_suite.py`). `tests/knowledge/test_evaluation.py:105-107` có test cô lập workspace chỉ chứa `pass`. Các con số trong báo cáo chất lượng không chứng minh các luồng production.
6. `DEBUG=release` ở biến môi trường hệ thống làm `Settings.DEBUG: bool` lỗi validation và chặn import ứng dụng. Cần quy ước biến môi trường riêng như `APP_DEBUG`, kiểm tra cấu hình ngay khi khởi động và có test môi trường sạch.

## Thứ tự cải thiện đề xuất

1. Đóng các lỗi Critical QA-01 đến QA-06; chạy lại chín ca QA và yêu cầu chuyển từ XFAIL sang PASS.
2. Sửa webhook, reminder và phân tích tham số đơn hàng QA-07 đến QA-09. Không cho phép thao tác ghi khi thiếu ID khách, số tiền hoặc preview được người dùng duyệt ở lượt riêng.
3. Tách toàn bộ test khỏi token thật, Telegram, Odoo, Gemini, DB/index dùng chung; dùng `tmp_path`, transport/mock hoặc môi trường test riêng. Sau đó chạy toàn bộ suite với coverage của `app` **và** `scripts`.
4. Bổ sung test end-to-end có kiểm soát cho Telegram polling/webhook, xác thực Odoo, phân quyền từng vai trò, OAuth, khôi phục sau restart, idempotency và nhiều người dùng đồng thời.
5. Chỉ cập nhật tuyên bố production ready và metric chất lượng khi các quality gate được đo trên đường đi thực tế và không còn blocker.
