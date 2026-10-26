import logging
import secrets
from typing import Any, List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.core.config import get_settings
from app.domain.task_identity import build_owner_key
from app.providers.tasks.local import LocalTaskProvider
from app.services.gateway_client import gateway_client
from app.services.morning_briefing_service import morning_briefing_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/v1", tags=["Internal Integration"])

local_task_provider = LocalTaskProvider()


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
    request: Request,
    authorization: Optional[str] = Header(None),
) -> bool:
    settings = getattr(request.app.state, "settings", None) or get_settings()
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
        "hôm nay tôi nên làm gì",
        "tôi cần làm gì hôm nay",
        "tôi nên làm gì hôm nay",
        "hôm nay có gì",
        "lịch hôm nay",
        "công việc hôm nay",
        "điểm tin hôm nay",
        "điểm tin",
        "lịch trình hôm nay",
        "kế hoạch hôm nay",
        "việc cần làm hôm nay",
    ]
    return any(p in lower for p in phrases)


def is_greeting_or_help_intent(text: str) -> bool:
    lower = text.strip().lower()
    if lower in ["/help", "/start", "help", "trợ giúp", "hướng dẫn"]:
        return True
    greetings = ["xin chào", "chào bạn", "chào em", "chào bot", "chào", "hello", "hi", "hey"]
    return any(lower == g or lower.startswith(g + " ") for g in greetings)


def is_whoami_intent(text: str) -> bool:
    lower = text.strip().lower()
    phrases = [
        "tôi là ai",
        "thông tin tài khoản",
        "thông tin của tôi",
        "hồ sơ của tôi",
        "hồ sơ",
        "tài khoản của tôi",
        "who am i",
        "whoami",
        "my profile",
    ]
    return any(p in lower for p in phrases)


def is_connection_status_intent(text: str) -> bool:
    lower = text.strip().lower()
    phrases = [
        "trạng thái kết nối",
        "kiểm tra kết nối",
        "tình trạng kết nối",
        "kết nối của tôi",
        "kết nối odoo",
        "kết nối google",
        "connection status",
        "kết nối",
    ]
    return any(p in lower for p in phrases)


def is_task_list_intent(text: str) -> bool:
    lower = text.strip().lower()
    phrases = [
        "danh sách công việc",
        "công việc của tôi",
        "việc của tôi",
        "xem công việc",
        "nhiệm vụ của tôi",
        "danh sách task",
        "my tasks",
        "tasks",
        "task",
    ]
    return any(p in lower for p in phrases)


def format_whoami(user_info: dict[str, Any]) -> str:
    lines = [
        "👤 **Thông tin tài khoản đã xác minh qua Gateway:**",
        f"- **Họ và tên:** {user_info.get('name') or 'Chưa cập nhật'}",
        f"- **Email / Đăng nhập:** {user_info.get('login') or 'Chưa cập nhật'}",
        f"- **Mã nhân viên (Odoo UID):** {user_info.get('odoo_uid')}",
        f"- **Hệ thống Odoo (Tenant):** {user_info.get('tenant')}",
        f"- **Cơ sở dữ liệu (Database):** {user_info.get('database')}",
        f"- **Múi giờ:** {user_info.get('tz') or 'Asia/Bangkok'}",
        f"- **Chức danh:** {user_info.get('job_title') or 'Nhân viên'}",
        f"- **Phòng ban:** {user_info.get('department') or 'Chưa phân bổ'}",
    ]
    return "\n".join(lines)


def format_greeting_help() -> str:
    return (
        "👋 **Xin chào! Tôi là Trợ lý AI kết nối qua Cổng Gateway.**\n\n"
        "Trong phiên bản hiện tại, tôi hỗ trợ các tính năng sau:\n"
        "1. 📅 **Điểm tin hàng ngày:** Gửi `/today` hoặc hỏi *\"Hôm nay tôi có gì?\"* để xem lịch họp Google Calendar, việc đến hạn và thư Gmail chưa đọc.\n"
        "2. 👤 **Thông tin danh tính:** Hỏi *\"Tôi là ai?\"* hoặc *\"Hồ sơ của tôi\"* để xem thông tin tài khoản đã xác thực.\n"
        "3. 🔗 **Kiểm tra kết nối:** Hỏi *\"Trạng thái kết nối\"* để kiểm tra liên kết tài khoản Odoo và Google Workspace.\n"
        "4. 📋 **Công việc cá nhân:** Hỏi *\"Danh sách công việc\"* hoặc *\"Việc của tôi\"* để tra cứu công việc cá nhân.\n\n"
        "ℹ️ *Lưu ý: Các thao tác ghi dữ liệu, tạo mới hoặc gửi tệp hiện chưa được hỗ trợ qua cổng Telegram này.*"
    )


def format_connection_status(user_info: dict[str, Any], conns: dict[str, Any]) -> str:
    is_valid_payload = isinstance(conns, dict) and conns.get("status") == "OK"

    # 1. Google Workspace
    google_conn = conns.get("google") if is_valid_payload else None
    if not is_valid_payload or not isinstance(google_conn, dict):
        google_status = "Không thể xác định trạng thái"
        google_email = "Không rõ"
        google_scopes_line = "Quyền: Không thể xác định quyền"
    else:
        g_raw_status = google_conn.get("status")
        google_email = google_conn.get("email") or "Chưa liên kết"
        scopes = google_conn.get("scopes") or []

        if g_raw_status == "OK":
            google_status = "Đã kết nối"
            has_cal = any(
                any(token in s for token in ["calendar.events", "calendar.readonly", "calendar"])
                for s in scopes
            )
            cal_scope = "✅ Lịch Google" if has_cal else "❌ Thiếu quyền Lịch"

            has_gmail = any(
                any(token in s for token in ["gmail.readonly", "gmail.modify", "https://mail.google.com/"])
                for s in scopes
            )
            gmail_scope = "✅ Gmail (đọc)" if has_gmail else "❌ Thiếu quyền Gmail"

            google_scopes_line = f"Quyền: {cal_scope} | {gmail_scope}"
        elif g_raw_status == "NOT_CONNECTED":
            google_status = "Chưa kết nối"
            google_scopes_line = "Quyền: Chưa cấp quyền (chưa liên kết)"
        elif g_raw_status == "RECONNECT_REQUIRED":
            google_status = "Cần kết nối lại"
            google_scopes_line = "Quyền: Cần kết nối lại tài khoản để làm mới quyền truy cập"
        else:
            google_status = "Không thể xác định trạng thái"
            google_scopes_line = "Quyền: Không thể xác định quyền"

    # 2. Hệ thống Odoo ERP
    odoo_conn = conns.get("odoo") if is_valid_payload else None
    if not is_valid_payload or not isinstance(odoo_conn, dict):
        odoo_status = "Không thể xác định trạng thái"
    else:
        odoo_raw = odoo_conn.get("status")
        if odoo_raw == "OK":
            odoo_status = "Đã kết nối"
        elif odoo_raw == "NOT_CONNECTED":
            odoo_status = "Chưa kết nối"
        else:
            odoo_status = "Không thể xác định trạng thái"

    odoo_uid = (odoo_conn.get("odoo_uid") if isinstance(odoo_conn, dict) else None) or user_info.get("odoo_uid")

    # 3. Telegram Bot
    tele_conn = conns.get("telegram") if is_valid_payload else None
    if not is_valid_payload or not isinstance(tele_conn, dict):
        tele_status = "Không thể xác định trạng thái"
    else:
        tele_raw = tele_conn.get("status")
        if tele_raw == "OK":
            tele_status = "Đã kết nối"
        elif tele_raw == "NOT_CONNECTED":
            tele_status = "Chưa kết nối"
        elif tele_raw == "BLOCKED":
            tele_status = "Bị chặn"
        else:
            tele_status = "Không thể xác định trạng thái"

    lines = [
        "🔗 **Trạng thái kết nối dịch vụ ngoại vi:**",
        "",
        "**1. Google Workspace:**",
        f"- Trạng thái: **{google_status}** ({google_email})",
        f"- {google_scopes_line}",
        "",
        "**2. Hệ thống Odoo ERP:**",
        f"- Trạng thái: **{odoo_status}** (Odoo UID: {odoo_uid})",
        f"- Tenant: `{user_info.get('tenant')}`",
        f"- Database: `{user_info.get('database')}`",
        "",
        "**3. Telegram Bot:**",
        f"- Người dùng: `{user_info.get('name')}`",
        f"- Trạng thái liên kết: **{tele_status}**",
    ]
    return "\n".join(lines)


TASK_STATUS_MAPPING = {
    "TODO": "📝 Chưa bắt đầu",
    "IN_PROGRESS": "🔄 Đang thực hiện",
    "WAITING": "⏳ Đang chờ",
    "DONE": "✅ Hoàn thành",
    "COMPLETED": "✅ Hoàn thành",
    "CANCELLED": "🚫 Đã hủy",
}


def format_task_list(tasks: list[dict[str, Any]]) -> str:
    if not tasks:
        return "📋 **Danh sách công việc cá nhân:**\nHiện tại bạn không có công việc nào trong danh sách."

    lines = [f"📋 **Danh sách công việc cá nhân ({len(tasks)} việc):**"]
    for i, t in enumerate(tasks, 1):
        raw_status = t.get("status")
        clean_status = str(raw_status).strip().upper() if raw_status is not None else ""
        status_display = TASK_STATUS_MAPPING.get(clean_status, "❔ Không rõ trạng thái")

        priority = str(t.get("priority") or "medium").upper()
        due = t.get("due_at") or "Không có hạn"
        lines.append(f"{i}. {status_display} - **{t.get('title')}** `[{priority}]` - Hạn: {due}")

    return "\n".join(lines)


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

    clean_text = req.text.strip()

    try:
        # 2. Dedicated Gateway Dispatcher with strict whitelist
        # Nhóm 1: Điểm tin hàng ngày
        if is_today_briefing_intent(clean_text):
            briefing_text = await morning_briefing_service.generate_real_briefing(user_info)
            messages = [briefing_text]

        # Nhóm 2: Chào hỏi / Trợ giúp
        elif is_greeting_or_help_intent(clean_text):
            messages = [format_greeting_help()]

        # Nhóm 3: Thông tin "Tôi là ai?" / Hồ sơ
        elif is_whoami_intent(clean_text):
            messages = [format_whoami(user_info)]

        # Nhóm 4: Trạng thái kết nối dịch vụ
        elif is_connection_status_intent(clean_text):
            conns = await gateway_client.get_user_connections(user_info["user_id"])
            messages = [format_connection_status(user_info, conns)]

        # Nhóm 5: Danh sách công việc cá nhân
        elif is_task_list_intent(clean_text):
            tenant = user_info.get("tenant")
            database = user_info.get("database")
            odoo_uid = user_info.get("odoo_uid")
            if not tenant or not database or odoo_uid is None:
                messages = [
                    "⚠️ Không thể tải danh sách công việc: Thông tin định danh không đầy đủ "
                    "để xác định chủ sở hữu (yêu cầu tenant, database và Odoo UID)."
                ]
            else:
                owner_key = build_owner_key(tenant, database, odoo_uid)
                tasks = await local_task_provider.list_tasks(filters={}, owner_key=owner_key)
                messages = [format_task_list(tasks)]

        # Nhóm khác: Từ chối trước thực thi (không rơi vào fallback direct hay dữ liệu mẫu)
        else:
            messages = [
                "⚠️ Yêu cầu này chưa được hỗ trợ qua Gateway trong phiên bản hiện tại "
                "(chỉ hỗ trợ điểm tin, xem hồ sơ cá nhân, kiểm tra kết nối và tra cứu công việc cá nhân). "
                "Vui lòng thực hiện thao tác trực tiếp trên hệ thống Odoo hoặc Google Workspace."
            ]

    except Exception as e:
        logger.error("Error in gateway turn dispatcher: %s", e, exc_info=True)
        messages = [
            f"⚠️ Đã có lỗi xảy ra trong quá trình xử lý yêu cầu (Mã đối soát: {req.request_id}). "
            "Vui lòng thử lại sau."
        ]

    return TelegramTurnResponse(
        request_id=req.request_id,
        messages=messages,
    )
