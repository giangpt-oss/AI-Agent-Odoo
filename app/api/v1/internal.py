import logging
import secrets
from typing import Any, List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.core.config import get_settings
from app.services.gateway_client import gateway_client
from app.services.morning_briefing_service import morning_briefing_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/v1", tags=["Internal Integration"])


class TelegramTurnRequest(BaseModel):
    request_id: str
    gateway_user_id: int
    telegram_user_id: int
    chat_id: int
    text: str
    received_at: Optional[str] = None


class TelegramTurnResponse(BaseModel):
    request_id: str
    messages: List[str]


def verify_gateway_secret(
    authorization: Optional[str] = Header(None),
) -> bool:
    settings = get_settings()
    expected = settings.GATEWAY_TO_AGENT_SECRET
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "MISCONFIGURED", "message": "GATEWAY_TO_AGENT_SECRET is not configured"},
        )

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Missing or invalid Bearer token"},
        )

    token = authorization.split("Bearer ", 1)[1].strip()
    if not secrets.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHORIZED", "message": "Invalid gateway secret"},
        )

    return True


def is_today_briefing_intent(text: str) -> bool:
    lower = text.strip().lower()
    if lower in ["/today", "/briefing"]:
        return True
    phrases = [
        "hôm nay tôi có gì",
        "hôm nay tôi cần làm gì",
        "hôm nay có gì",
        "lịch hôm nay",
        "công việc hôm nay",
        "điểm tin hôm nay",
    ]
    return any(p in lower for p in phrases)


@router.post(
    "/telegram/turn",
    response_model=TelegramTurnResponse,
    dependencies=[Depends(verify_gateway_secret)],
)
async def handle_telegram_turn(
    req: TelegramTurnRequest,
    request: Request,
):
    # 1. Xác thực lại danh tính từ Gateway (không chỉ tin user_id trong payload)
    user_info = await gateway_client.resolve_identity(
        telegram_user_id=req.telegram_user_id,
        chat_id=req.chat_id,
    )
    if not user_info:
        logger.warning(
            "Identity resolution failed for telegram_user_id=%s chat_id=%s",
            req.telegram_user_id,
            req.chat_id,
        )
        return TelegramTurnResponse(
            request_id=req.request_id,
            messages=[
                "⚠️ Không thể xác minh danh tính tài khoản của bạn qua Gateway. "
                "Vui lòng truy cập Cổng Portal để liên kết tài khoản."
            ],
        )

    # Đảm bảo gateway_user_id khớp với user_id được giải quyết
    if user_info.get("user_id") != req.gateway_user_id:
        logger.warning(
            "Gateway user ID mismatch: payload=%s, resolved=%s",
            req.gateway_user_id,
            user_info.get("user_id"),
        )
        return TelegramTurnResponse(
            request_id=req.request_id,
            messages=["⚠️ Phát hiện sai lệch thông tin danh tính giữa yêu cầu và phiên làm việc."],
        )

    # 2. Xử lý yêu cầu Điểm tin / Lịch trình hôm nay
    clean_text = req.text.strip()
    if is_today_briefing_intent(clean_text):
        briefing_text = await morning_briefing_service.generate_real_briefing(user_info)
        return TelegramTurnResponse(
            request_id=req.request_id,
            messages=[briefing_text],
        )

    # 3. Xử lý các câu hỏi hoặc lệnh hội thoại nghiệp vụ khác
    try:
        from app.assistant.company_assistant import company_assistant
        # Sử dụng company assistant để xử lý hội thoại
        reply = await company_assistant.chat(
            chat_id=req.chat_id,
            message=clean_text,
            user_id=str(user_info.get("user_id")),
            user_name=user_info.get("name") or "Quý nhân viên",
        )
        messages = [reply] if reply else ["Tôi đã ghi nhận yêu cầu của bạn."]
    except Exception as e:
        logger.error("Error processing turn with assistant: %s", e)
        messages = [f"Dạ tôi đã nhận được tin nhắn của bạn: '{clean_text}'. Hệ thống đang tiếp tục xử lý."]

    return TelegramTurnResponse(
        request_id=req.request_id,
        messages=messages,
    )
