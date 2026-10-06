import logging
import uuid
from typing import Any
from fastapi import APIRouter, Header, HTTPException, Request
from langchain_core.messages import HumanMessage

from app.core.config import get_settings
from app.services.employee import employee_service
from app.agent.graph import agent_runnable
from app.connectors.telegram.client import get_telegram_connector

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/telegram", tags=["Telegram Webhook"])


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    """Tiếp nhận tin nhắn 1-1 từ Telegram Bot Webhook."""
    settings = get_settings()

    # 1. Xác thực Secret Token từ Telegram
    from secrets import compare_digest
    secret = settings.TELEGRAM_WEBHOOK_SECRET
    if not secret or secret == "default-secret" or not isinstance(x_telegram_bot_api_secret_token, str) or not compare_digest(x_telegram_bot_api_secret_token, secret):
        raise HTTPException(status_code=403, detail="Invalid webhook secret token")

    payload: dict[str, Any] = await request.json()
    message = payload.get("message")
    if not message:
        # Bỏ qua các update không phải message (như edited_message, inline query)
        return {"ok": True, "status": "ignored_non_message"}

    chat_id = message.get("chat", {}).get("id")
    if message.get("chat", {}).get("type", "private") != "private":
        return {"ok": True, "status": "private_chat_required"}
    user_text = message.get("text", "").strip()

    if not chat_id or not user_text:
        return {"ok": True, "status": "empty_chat_or_text"}

    telegram_connector = get_telegram_connector()

    # 2. Kiểm tra Emergency Kill Switch (Nếu BẬT -> Ngắt ngay, không tốn token LLM)
    from app.security.kill_switch import kill_switch
    if await kill_switch.is_active():
        maintenance_msg = (
            "🚨 **HỆ THỐNG TẠM NGƯNG BẢO TRÌ KHẨN CẤP**\n\n"
            "Quản trị viên đang bật chế độ ngắt khẩn cấp để bảo vệ an toàn dữ liệu. "
            "Mọi tác vụ tạm thời bị khóa. Vui lòng thử lại sau ít phút."
        )
        await telegram_connector.send_message(chat_id, maintenance_msg)
        return {"ok": True, "status": "kill_switch_active"}

    # 3. Định danh nhân viên từ Telegram Chat ID
    employee = await employee_service.resolve_employee_identity(chat_id)
    if not employee:
        unregistered_msg = (
            "⚠️ *Tài khoản chưa được kích hoạt!*\n\n"
            f"Telegram Chat ID của bạn là `{chat_id}` chưa được liên kết với nhân viên nào trên hệ thống.\n"
            "Vui lòng liên hệ IT Quản trị để được cấp quyền sử dụng Trợ lý AI."
        )
        await telegram_connector.send_message(chat_id, unregistered_msg)
        return {"ok": True, "status": "unregistered_user"}

    # 3. Đưa vào LangGraph Orchestrator (Bảo toàn State qua Checkpointer)
    thread_id = f"telegram-thread-{chat_id}-{employee['id']}"
    config = {"configurable": {"thread_id": thread_id}}

    existing_state = await agent_runnable.aget_state(config)
    if existing_state and existing_state.values:
        # Nếu đã có phiên trước đó, chỉ gửi tin nhắn mới để giữ nguyên pending_tool và confirmation state
        input_data = {
            "messages": [HumanMessage(content=user_text)],
            "roles": employee["roles"],
            "employee_id": employee["id"],
        }
    else:
        # Khởi tạo phiên mới
        input_data = {
            "session_id": str(uuid.uuid4()),
            "employee_id": employee["id"],
            "telegram_chat_id": chat_id,
            "roles": employee["roles"],
            "messages": [HumanMessage(content=user_text)],
            "current_intent": None,
            "extracted_slots": {},
            "missing_slots": [],
            "pending_tool_name": None,
            "pending_tool_args": None,
            "is_write_action": False,
            "permission_granted": False,
            "denial_reason": None,
            "requires_user_confirmation": False,
            "user_confirmed": None,
            "tool_result": None,
            "final_response": None,
            "error": None,
        }

    try:
        final_state = await agent_runnable.ainvoke(input_data, config=config)
        response_text = final_state.get("final_response") or "Đã hoàn tất xử lý yêu cầu."
        
        # 4. Gửi kết quả về Telegram cho nhân viên
        await telegram_connector.send_message(chat_id, response_text, parse_mode="")
        return {
            "ok": True,
            "status": "processed",
            "chat_id": chat_id,
            "intent": final_state.get("current_intent"),
        }
    except Exception as e:
        logger.error(f"Error executing agent for chat_id {chat_id}: {e}")
        error_msg = f"Đã xảy ra lỗi trong quá trình xử lý: {str(e)}"
        await telegram_connector.send_message(chat_id, error_msg, parse_mode="")
        return {"ok": False, "error": str(e)}
