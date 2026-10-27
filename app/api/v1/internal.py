import hashlib
import json
import logging
import secrets
from typing import Any, List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from app.core.config import get_settings
from app.domain.gateway_context import GatewayPermission, GatewayUserContext, build_gateway_user_context
from app.domain.task_identity import build_owner_key
from app.providers.tasks.local import LocalTaskProvider
from app.services.gateway_client import (
    GatewayContractIncompatibleError,
    GatewayServiceError,
    gateway_client,
)
from app.services.morning_briefing_service import morning_briefing_service
from app.services.gateway_agent_dispatcher import gateway_agent_dispatcher
from app.services.turn_ledger import turn_ledger

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/v1", tags=["Internal Integration"])

local_task_provider = LocalTaskProvider()


class TelegramTurnRequest(BaseModel):
    request_id: str
    gateway_user_id: int
    telegram_user_id: int
    chat_id: int
    text: str
    telegram_binding_id: Optional[str] = None
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
    # Loại trừ các hành động cụ thể về task
    action_prefixes = [
        "/task create", "/task update", "/task done", "/task complete", "/task delete",
        "tạo công việc", "tạo việc", "thêm công việc", "thêm task", "tạo task",
        "sửa việc", "sửa task", "hoàn thành việc", "hoàn thành task", "xóa việc", "xóa task",
    ]
    if any(lower.startswith(p) for p in action_prefixes):
        return False

    if lower in ["/task", "/tasks", "task", "tasks", "/task list"]:
        return True

    phrases = [
        "danh sách công việc",
        "công việc của tôi",
        "việc của tôi",
        "xem công việc",
        "nhiệm vụ của tôi",
        "danh sách task",
        "my tasks",
    ]
    return any(p in lower for p in phrases)


def format_whoami(user_info: Any) -> str:
    if not isinstance(user_info, GatewayUserContext):
        ctx = build_gateway_user_context(user_info)
    else:
        ctx = user_info

    lines = [
        "👤 Thông tin tài khoản đã xác minh qua Gateway:",
        f"• Họ và tên: {ctx.name}",
        f"• Email / Đăng nhập: {ctx.login}",
        f"• Mã nhân viên (Odoo UID): {ctx.odoo_uid}",
        f"• Hệ thống Odoo (Tenant): {ctx.tenant}",
        f"• Cơ sở dữ liệu (Database): {ctx.database}",
        f"• Múi giờ: {ctx.timezone}",
        f"• Chức danh: {ctx.display_job_title}",
        f"• Phòng ban: {ctx.display_department}",
    ]

    if ctx.permissions:
        lines.append(f"• Quyền nghiệp vụ: {', '.join(sorted(ctx.permissions))}")
    else:
        lines.append("• Quyền nghiệp vụ: Chưa có quyền bổ sung")

    if ctx.authorization_checked_at:
        lines.append(f"• Thời điểm kiểm tra quyền: {ctx.authorization_checked_at}")

    return "\n".join(lines)


def format_greeting_help() -> str:
    return (
        "👋 Xin chào! Tôi có thể hỗ trợ gì cho bạn hôm nay?\n\n"
        "Bạn có thể nhắn tin tự nhiên hoặc dùng các gợi ý nhanh sau:\n"
        "• 📅 Điểm tin hàng ngày: Gửi /today hoặc hỏi \"Hôm nay tôi có gì?\"\n"
        "• 👤 Thông tin tài khoản: Hỏi \"Tôi là ai?\" hoặc \"Hồ sơ của tôi\"\n"
        "• 📆 Lịch họp & Email: Hỏi \"Xem lịch\" hoặc \"Thư chưa đọc\"\n"
        "• 💼 Odoo CRM: Gửi /crm search <từ khóa> hoặc \"Tìm khách hàng\"\n"
        "• 📋 Công việc cá nhân: Gửi /task list hoặc /task create <nội dung>\n\n"
        "💡 Các thao tác ghi dữ liệu luôn có bản xem trước để bạn xác nhận an toàn."
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
        "🔗 Trạng thái kết nối dịch vụ ngoại vi:",
        "",
        "1. Google Workspace:",
        f"• Trạng thái: {google_status} ({google_email})",
        f"• {google_scopes_line}",
        "",
        "2. Hệ thống Odoo ERP:",
        f"• Trạng thái: {odoo_status} (Odoo UID: {odoo_uid})",
        f"• Tenant: {user_info.get('tenant')}",
        f"• Database: {user_info.get('database')}",
        "",
        "3. Telegram Bot:",
        f"• Người dùng: {user_info.get('name')}",
        f"• Trạng thái liên kết: {tele_status}",
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
    payload_hash = hashlib.sha256(req.text.encode("utf-8")).hexdigest()

    # 1. Luôn xác thực danh tính từ Gateway TRƯỚC KHI đọc cache hoặc nhận claim turn
    try:
        user_info = await gateway_client.resolve_identity(
            telegram_user_id=req.telegram_user_id,
            chat_id=req.chat_id,
        )
    except GatewayServiceError as e:
        logger.warning("Gateway service error resolving identity for request_id=%s: %s", req.request_id, e)
        # Lỗi identity tạm thời trả HTTP 503 để gateway retry, không ghi thành kết quả cuối cùng
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "GATEWAY_SERVICE_UNAVAILABLE", "message": f"Không thể kết nối đến Gateway để xác thực danh tính: {e}"},
        )
    except GatewayContractIncompatibleError as e:
        logger.error("Gateway contract incompatible: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "CONTRACT_INCOMPATIBLE", "message": "Gateway contract version incompatible (contract v2 required)"},
        )

    if not user_info:
        logger.warning(
            "Identity resolution failed for telegram_user_id=%s chat_id=%s",
            req.telegram_user_id,
            req.chat_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "NOT_CONNECTED", "message": "Tài khoản Telegram chưa được liên kết hoặc đã bị ngắt kết nối"},
        )

    # Xây dựng context người dùng thống nhất từ Gateway identity
    context = build_gateway_user_context(user_info)
    verified_binding_id = context.telegram_binding_id

    # Đảm bảo gateway_user_id khớp với user_id được giải quyết
    if context.user_id != req.gateway_user_id:
        logger.warning(
            "Gateway user ID mismatch: payload=%s, resolved=%s",
            req.gateway_user_id,
            context.user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "IDENTITY_MISMATCH", "message": "User ID does not match resolved gateway identity"},
        )

    # Nếu request gửi kèm telegram_binding_id, đối chiếu với binding hiện hành
    if req.telegram_binding_id and verified_binding_id and req.telegram_binding_id != verified_binding_id:
        logger.warning(
            "Telegram binding ID mismatch: payload=%s, verified=%s",
            req.telegram_binding_id,
            verified_binding_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "BINDING_MISMATCH", "message": "Request binding ID does not match current verified binding"},
        )

    # 2. Kiểm tra Idempotency theo request_id sau khi đã xác minh danh tính
    turn_record = turn_ledger.get_turn_record(req.request_id)
    if turn_record is not None:
        # Kiểm tra danh tính người dùng và chat
        if (
            turn_record["user_id"] != context.user_id
            or turn_record["telegram_user_id"] != req.telegram_user_id
            or turn_record["chat_id"] != req.chat_id
        ):
            logger.warning(
                "Turn identity mismatch for request_id=%s: recorded=(%s,%s,%s), incoming=(%s,%s,%s)",
                req.request_id,
                turn_record["user_id"],
                turn_record["telegram_user_id"],
                turn_record["chat_id"],
                context.user_id,
                req.telegram_user_id,
                req.chat_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "IDENTITY_MISMATCH", "message": "Turn identity does not match existing turn record"},
            )

        # Đối chiếu binding: request cũ thuộc binding khác phải bị từ chối
        record_binding = turn_record.get("telegram_binding_id")
        if record_binding and verified_binding_id and record_binding != verified_binding_id:
            logger.warning(
                "Turn binding changed for request_id=%s: recorded=%s, verified=%s",
                req.request_id,
                record_binding,
                verified_binding_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "BINDING_CHANGED", "message": "Turn was recorded under a different binding ID"},
            )

        # Ledger cũ thiếu binding/hash không được replay; trả yêu cầu gửi tin mới để không ghi lặp
        if not record_binding or not turn_record.get("payload_hash"):
            logger.warning(
                "Turn record lacks binding or payload_hash for request_id=%s, refusing replay",
                req.request_id,
            )
            return TelegramTurnResponse(
                request_id=req.request_id,
                messages=[
                    "⚠️ Bản ghi tin nhắn trước đây thiếu thông tin xác minh binding/hash để tái hiện an toàn. "
                    "Vui lòng gửi tin nhắn mới để đảm bảo không xử lý lặp lại."
                ],
            )

        # Kiểm tra payload hash
        if turn_record["payload_hash"] != payload_hash:
            logger.warning(
                "Turn payload mismatch for request_id=%s: recorded=%s, incoming=%s",
                req.request_id,
                turn_record["payload_hash"],
                payload_hash,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "PAYLOAD_MISMATCH", "message": "Request ID already used with different payload"},
            )

        # Kiểm tra trạng thái turn
        if turn_record["status"] == "in_progress":
            logger.info("Turn is currently in progress for request_id=%s, returning 503 for retry", req.request_id)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "TURN_IN_PROGRESS", "message": "Turn is currently being processed"},
            )

        if turn_record["status"] == "completed":
            logger.info("Returning cached turn response for request_id=%s", req.request_id)
            cached_msgs = json.loads(turn_record["messages_json"]) if turn_record.get("messages_json") else []
            return TelegramTurnResponse(
                request_id=req.request_id,
                messages=cached_msgs,
            )

    # 3. Ghi nhận claim turn với binding_id đã được Gateway xác minh
    started = turn_ledger.record_turn_start(
        request_id=req.request_id,
        user_id=context.user_id,
        telegram_user_id=req.telegram_user_id,
        chat_id=req.chat_id,
        telegram_binding_id=verified_binding_id,
        payload_hash=payload_hash,
    )
    if not started:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "TURN_IN_PROGRESS", "message": "Turn is currently being processed"},
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
            messages = [format_whoami(context)]

        # Nhóm 4: Trạng thái kết nối dịch vụ
        elif is_connection_status_intent(clean_text):
            conns = await gateway_client.get_user_connections(context.user_id)
            messages = [format_connection_status(user_info, conns)]

        # Nhóm 5: Danh sách công việc cá nhân (Yêu cầu quyền tasks.read và owner_key xác thực)
        elif is_task_list_intent(clean_text):
            if not context.has_permission(GatewayPermission.TASKS_READ):
                messages = [
                    "⚠️ Tài khoản của bạn chưa được cấp quyền tra cứu công việc (`tasks.read`). "
                    "Vui lòng liên hệ quản trị viên hoặc kiểm tra quyền trên Cổng Gateway."
                ]
            elif not context.tenant or not context.database or context.odoo_uid is None:
                messages = [
                    "⚠️ Không thể tải danh sách công việc: Thông tin định danh không đầy đủ "
                    "để xác định chủ sở hữu (yêu cầu tenant, database và Odoo UID)."
                ]
            else:
                tasks = await local_task_provider.list_tasks(filters={}, owner_key=context.owner_key)
                messages = [format_task_list(tasks)]

        # Nhóm 6: Điều phối các tác vụ nghiệp vụ nâng cao (Calendar, Gmail, CRM, Tasks ghi, Xác nhận)
        else:
            messages = await gateway_agent_dispatcher.dispatch(
                context=context,
                chat_id=req.chat_id,
                telegram_user_id=req.telegram_user_id,
                raw_text=clean_text,
            )

    except Exception as e:
        logger.error("Error in gateway turn dispatcher: %s", e, exc_info=True)
        messages = [
            f"⚠️ Đã có lỗi xảy ra trong quá trình xử lý yêu cầu (Mã đối soát: {req.request_id}). "
            "Vui lòng thử lại sau."
        ]

    # Ghi nhận kết quả turn vào sổ nhật ký (idempotency)
    turn_ledger.record_turn_completed(req.request_id, messages)

    return TelegramTurnResponse(
        request_id=req.request_id,
        messages=messages,
    )
