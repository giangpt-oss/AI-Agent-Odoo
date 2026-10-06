from typing import List, Dict, Any, Optional
import uuid
from app.providers.email.base import EmailProvider

class FakeEmailProvider(EmailProvider):
    def __init__(self):
        self.messages = [
            {"id": "msg1", "thread_id": "thread1", "from": "minh@company.com", "to": ["me@company.com"], "subject": "Báo cáo tháng trước", "date": "2026-09-30T10:00:00Z", "snippet": "Anh xem báo cáo...", "body": "Nội dung báo cáo chi tiết...", "attachments": [], "unread": True},
            {"id": "msg2", "thread_id": "thread2", "from": "boss@company.com", "to": ["me@company.com"], "subject": "Họp chiều nay", "date": "2026-10-06T08:00:00Z", "snippet": "Chiều nay 2h họp nhé", "body": "Chuẩn bị tài liệu dự án ABC.", "attachments": [], "unread": False}
        ]
        self.drafts = []
        self.sent = []
        self.archived = []

    def list_messages(self, limit: int = 10, page_token: str = None) -> Dict[str, Any]:
        return {"messages": self.messages[:limit]}

    def search_messages(self, query: str, limit: int = 10) -> Dict[str, Any]:
        results = [m for m in self.messages if query.lower() in m["subject"].lower() or query.lower() in m["snippet"].lower() or query.lower() in m["from"].lower()]
        return {"messages": results[:limit]}

    def get_message(self, message_id: str) -> Dict[str, Any]:
        for m in self.messages:
            if m["id"] == message_id:
                return m
        raise Exception(f"Message {message_id} not found")

    def create_draft(self, to: List[str], subject: str, body: str, cc: List[str] = None, reply_to_message_id: str = None) -> str:
        draft_id = f"draft_{uuid.uuid4().hex[:8]}"
        self.drafts.append({
            "id": draft_id,
            "to": to,
            "cc": cc or [],
            "subject": subject,
            "body": body,
            "reply_to_message_id": reply_to_message_id
        })
        return draft_id

    def send_message(self, to: List[str], subject: str, body: str, cc: List[str] = None, attachments: List[str] = None) -> str:
        msg_id = f"sent_{uuid.uuid4().hex[:8]}"
        self.sent.append({
            "id": msg_id,
            "to": to,
            "cc": cc or [],
            "subject": subject,
            "body": body,
            "attachments": attachments or []
        })
        return msg_id

    def reply_message(self, original_message_id: str, thread_id: str, body: str, attachments: List[str] = None) -> str:
        msg_id = f"reply_{uuid.uuid4().hex[:8]}"
        self.sent.append({
            "id": msg_id,
            "thread_id": thread_id,
            "reply_to": original_message_id,
            "body": body,
            "attachments": attachments or []
        })
        return msg_id

    def archive_message(self, message_id: str) -> bool:
        self.archived.append(message_id)
        return True
