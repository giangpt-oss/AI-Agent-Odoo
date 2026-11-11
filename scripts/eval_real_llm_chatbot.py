# -*- coding: utf-8 -*-
"""Script nghiệm thu đánh giá chất lượng nền Chatbot và Agent với mô hình thật (Gemini).

Chạy script này để kiểm tra độc lập 10 kịch bản holdout thực tế:
1. Giải thích/hỏi đáp ('Cách tạo công việc?') -> reply, không tạo write preview.
2. Phủ định ('Đừng tạo công việc') -> reply, không tạo write preview.
3. Yêu cầu hành động cụ thể ('Tạo công việc báo cáo tuần') -> prepare_write.
4. Yêu cầu mơ hồ ('Tạo công việc') -> clarify hỏi lại.
5. Tra cứu đối tác Odoo ('Tìm khách hàng An Phát') -> read odoo.partners.search.
6. Tra cứu sản phẩm Odoo ('Kiểm tra tồn kho bàn làm việc') -> read odoo.products.search.
7. Tra cứu đơn bán hàng ('Xem các đơn bán hàng gần đây') -> read odoo.sales.search.
8. Tra cứu hóa đơn ('Tìm hóa đơn chưa thanh toán') -> read odoo.invoices.search.
9. Câu hỏi ngữ cảnh nối tiếp ('Công việc vừa tạo có hạn khi nào?') -> reply từ context.
10. Lời chào thuần túy ('Xin chào bạn') -> reply chào hỏi & menu.
"""

import asyncio
import os
import sys
import time

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# Ensure project root in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import get_settings
from app.domain.gateway_context import GatewayUserContext
from app.domain.intent_schema import IntentType
from app.services.natural_pipeline import natural_pipeline


async def run_evaluation():
    settings = get_settings()
    has_api_key = bool(settings.GEMINI_API_KEY)
    print("\n" + "=" * 80)
    print(f"📊 BỘ NGHIỆM THU ĐÁNH GIÁ NỀN CHATBOT & AGENT ODOO")
    print(f"• LLM Model: {settings.DEFAULT_LLM_MODEL or 'gemini-3.5-flash-lite'}")
    print(f"• Chế độ: {'Mô hình thực tế (Gemini API)' if has_api_key else 'Ngoại tuyến (Offline Semantic Engine)'}")
    print("=" * 80 + "\n")

    user_ctx = GatewayUserContext(
        user_id=1,
        tenant="https://odoo.example.com",
        database="production_db",
        odoo_uid=10,
        telegram_user_id=12345678,
        chat_id=12345678,
        name="Nguyễn Văn Nghiệm Thu",
        login="tester@example.com",
        timezone="Asia/Ho_Chi_Minh",
        permissions={
            "tasks.read", "tasks.write",
            "calendar.read", "calendar.write",
            "gmail.read", "gmail.send",
            "crm.read",
        },
        telegram_binding_id="bind_eval_test",
    )

    test_cases = [
        {
            "id": 1,
            "category": "Giải thích / Hỏi đáp",
            "query": "Cách tạo công việc cá nhân?",
            "expected_intent": IntentType.REPLY,
            "forbidden_intent": IntentType.PREPARE_WRITE,
            "session_context": None,
        },
        {
            "id": 2,
            "category": "Phủ định / Hủy ý định",
            "query": "Đừng tạo công việc nữa",
            "expected_intent": IntentType.REPLY,
            "forbidden_intent": IntentType.PREPARE_WRITE,
            "session_context": None,
        },
        {
            "id": 3,
            "category": "Yêu cầu hành động cụ thể",
            "query": "Tạo công việc nộp báo cáo doanh thu tuần trước 17h thứ sáu",
            "expected_intent": IntentType.PREPARE_WRITE,
            "expected_tool": "tasks.personal.create",
            "session_context": None,
        },
        {
            "id": 4,
            "category": "Yêu cầu thiếu dữ liệu (Vague)",
            "query": "Tạo công việc",
            "expected_intent": IntentType.CLARIFY,
            "forbidden_intent": IntentType.PREPARE_WRITE,
            "session_context": None,
        },
        {
            "id": 5,
            "category": "Đọc Odoo Đối tác / Khách hàng",
            "query": "Tìm khách hàng An Phát giúp tôi",
            "expected_intent": IntentType.READ,
            "expected_tool": "odoo.partners.search",
            "session_context": None,
        },
        {
            "id": 6,
            "category": "Đọc Odoo Danh mục & Tồn kho",
            "query": "Kiểm tra tồn kho sản phẩm bàn làm việc",
            "expected_intent": IntentType.READ,
            "expected_tool": "odoo.products.search",
            "session_context": None,
        },
        {
            "id": 7,
            "category": "Đọc Odoo Đơn bán hàng",
            "query": "Xem các đơn bán hàng gần đây",
            "expected_intent": IntentType.READ,
            "expected_tool": "odoo.sales.search",
            "session_context": None,
        },
        {
            "id": 8,
            "category": "Đọc Odoo Hóa đơn chứng từ",
            "query": "Tìm các hóa đơn chưa thanh toán",
            "expected_intent": IntentType.READ,
            "expected_tool": "odoo.invoices.search",
            "session_context": None,
        },
        {
            "id": 9,
            "category": "Ngữ cảnh nối tiếp",
            "query": "Công việc vừa tạo có hạn khi nào?",
            "expected_intent": IntentType.REPLY,
            "session_context": {
                "last_read": {
                    "task_id": "T101",
                    "title": "Báo cáo doanh thu",
                    "due_at": "2026-10-30T17:00:00",
                }
            },
        },
        {
            "id": 10,
            "category": "Lời chào thuần túy & Menu",
            "query": "Xin chào bạn",
            "expected_intent": IntentType.REPLY,
            "session_context": None,
        },
    ]

    passed_count = 0
    total_count = len(test_cases)

    for tc in test_cases:
        t0 = time.time()
        try:
            res = await natural_pipeline.analyze_input(
                text=tc["query"],
                context=user_ctx,
                session_context=tc["session_context"],
            )
            dur_ms = (time.time() - t0) * 1000

            # Verify conditions
            is_pass = True
            fail_reason = ""

            if res.intent_type != tc["expected_intent"]:
                is_pass = False
                fail_reason = f"Sai intent: mong đợi {tc['expected_intent']}, nhận được {res.intent_type}"

            if tc.get("forbidden_intent") and res.intent_type == tc["forbidden_intent"]:
                is_pass = False
                fail_reason = f"Vi phạm cấm: không được gán thành {tc['forbidden_intent']}"

            if tc.get("expected_tool") and res.tool_name != tc["expected_tool"]:
                is_pass = False
                fail_reason = f"Sai tool: mong đợi {tc['expected_tool']}, nhận được {res.tool_name}"

            status_icon = "✅ PASS" if is_pass else "❌ FAIL"
            if is_pass:
                passed_count += 1

            print(f"[{tc['id']:02d}] {tc['category']}")
            print(f"     Câu hỏi: \"{tc['query']}\"")
            print(f"     Kết quả: {status_icon} (Thời gian: {dur_ms:.1f}ms)")
            print(f"     Chi tiết: intent={res.intent_type}, tool={res.tool_name}")
            if not is_pass:
                print(f"     ⚠️ LÝ DO THẤT BẠI: {fail_reason}")
            print("-" * 80)

        except Exception as e:
            dur_ms = (time.time() - t0) * 1000
            print(f"[{tc['id']:02d}] {tc['category']}")
            print(f"     Câu hỏi: \"{tc['query']}\"")
            print(f"     Kết quả: ❌ EXCEPTION ({dur_ms:.1f}ms): {e}")
            print("-" * 80)

    print("\n" + "=" * 80)
    print(f"🏆 KẾT QUẢ TỔNG HỢP: {passed_count}/{total_count} kịch bản đạt yêu cầu ({passed_count / total_count * 100:.1f}%)")
    print("=" * 80 + "\n")
    return passed_count == total_count


if __name__ == "__main__":
    success = asyncio.run(run_evaluation())
    sys.exit(0 if success else 1)
