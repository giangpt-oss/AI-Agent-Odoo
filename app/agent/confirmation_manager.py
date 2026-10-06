"""One-use confirmations bound to an authenticated user and private chat."""
import time
import uuid
from copy import deepcopy
from threading import Lock
from typing import Any

class ConfirmationState:
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"

class ConfirmationRecord:
    def __init__(self, skill_name, arguments, preview, context, ttl_seconds=300):
        self.id = str(uuid.uuid4())
        self.skill_name = skill_name
        self.arguments = deepcopy(arguments)
        self.preview = deepcopy(preview)
        self.context = context
        session = context.session if hasattr(context, "session") else (context or {})
        self.user_id = str(session.get("user_id", "")) if isinstance(session, dict) else session.user_id
        self.chat_id = session.get("chat_id") if isinstance(session, dict) else session.chat_id
        self.created_at = time.time()
        self.expires_at = self.created_at + ttl_seconds
        self.status = ConfirmationState.PENDING

    def to_dict(self):
        return {"confirmation_id": self.id, "skill": self.skill_name,
                "arguments": deepcopy(self.arguments), "preview": deepcopy(self.preview),
                "created_at": self.created_at, "expires_at": self.expires_at, "status": self.status}

class ConfirmationManager:
    def __init__(self):
        self._records = {}
        self._lock = Lock()

    def _expire(self, record):
        if record.status in {ConfirmationState.PENDING, ConfirmationState.APPROVED} and time.time() >= record.expires_at:
            record.status = ConfirmationState.EXPIRED

    def _owns(self, record, user_id, chat_id):
        return bool(user_id and chat_id is not None and record.user_id == str(user_id) and record.chat_id == chat_id)

    def create_request(self, skill_name, arguments, preview, context):
        with self._lock:
            # Prune expired records to bound memory in long-running bots.
            for key, record in list(self._records.items()):
                if time.time() > record.expires_at + 3600:
                    del self._records[key]
            record = ConfirmationRecord(skill_name, arguments, preview, context)
            if not record.user_id or record.chat_id is None:
                raise ValueError("Confirmation requires an authenticated user and chat")
            self._records[record.id] = record
            return record

    def get_record(self, confirmation_id):
        with self._lock:
            record = self._records.get(confirmation_id)
            if record:
                self._expire(record)
            return record

    def approve_confirmation(self, confirmation_id, *, user_id=None, chat_id=None):
        with self._lock:
            record = self._records.get(confirmation_id)
            if not record or not self._owns(record, user_id, chat_id):
                return False
            self._expire(record)
            if record.status != ConfirmationState.PENDING:
                return False
            record.status = ConfirmationState.APPROVED
            return True

    def reject_confirmation(self, confirmation_id, *, user_id=None, chat_id=None):
        with self._lock:
            record = self._records.get(confirmation_id)
            if not record or not self._owns(record, user_id, chat_id):
                return False
            self._expire(record)
            if record.status != ConfirmationState.PENDING:
                return False
            record.status = ConfirmationState.REJECTED
            return True

    def check_and_consume_approval(self, skill_name, arguments, *, confirmation_id=None, user_id=None, chat_id=None):
        with self._lock:
            record = self._records.get(confirmation_id)
            if not record or not self._owns(record, user_id, chat_id):
                return False
            self._expire(record)
            if record.status != ConfirmationState.APPROVED or record.skill_name != skill_name or record.arguments != arguments:
                return False
            record.status = ConfirmationState.EXECUTING
            return True

    def mark_executed(self, confirmation_id, *, failed=False):
        with self._lock:
            record = self._records.get(confirmation_id)
            if record and record.status == ConfirmationState.EXECUTING:
                record.status = ConfirmationState.FAILED if failed else ConfirmationState.EXECUTED

confirmation_manager = ConfirmationManager()
