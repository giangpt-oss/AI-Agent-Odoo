from typing import Any
from fastapi import APIRouter, BackgroundTasks, HTTPException, Header
from secrets import compare_digest
from app.core.config import get_settings
from pydantic import BaseModel
from app.services.notification import notification_service

router = APIRouter(prefix="/webhooks", tags=["External Webhooks"])


class OdooWebhookPayload(BaseModel):
    event_type: str
    record_name: str
    model: str
    assigned_chat_id: int | None = None
    amount: float | None = None
    extra_data: dict[str, Any] | None = None


@router.post("/odoo")
async def receive_odoo_webhook(
    payload: OdooWebhookPayload,
    background_tasks: BackgroundTasks,
    x_odoo_webhook_secret: str | None = Header(default=None),
):
    """Tiếp nhận Webhook từ Odoo Automated Actions (M10 - Passive Notification).
    Chạy bất đồng bộ qua BackgroundTasks để phản hồi Odoo ngay lập tức.
    """
    secret = get_settings().ODOO_WEBHOOK_SECRET
    if not secret or not isinstance(x_odoo_webhook_secret, str) or not compare_digest(secret, x_odoo_webhook_secret):
        raise HTTPException(status_code=403, detail="Invalid Odoo webhook secret")
    background_tasks.add_task(
        notification_service.handle_odoo_event,
        payload.event_type,
        payload.model_dump(),
    )
    return {
        "status": "accepted",
        "event_type": payload.event_type,
        "record_name": payload.record_name,
    }
