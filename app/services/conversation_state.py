import time
from typing import Any, Dict, Optional


class ConversationStateManager:
    """Quản lý trạng thái hội thoại nhiều lượt theo (user_id, telegram_binding_id).
    
    TTL mặc định: 15 phút. Khi binding_id thay đổi hoặc hết hạn, trạng thái cũ tự động xóa.
    """

    def __init__(self, ttl_seconds: int = 900):
        self.ttl = ttl_seconds
        self._states: Dict[str, Dict[str, Any]] = {}

    def _key(self, user_id: int, telegram_binding_id: Optional[str]) -> str:
        bind = telegram_binding_id or "default"
        return f"{user_id}:{bind}"

    def get_state(self, user_id: int, telegram_binding_id: Optional[str]) -> Dict[str, Any]:
        key = self._key(user_id, telegram_binding_id)
        entry = self._states.get(key)
        if not entry:
            return {}
        if time.time() - entry.get("timestamp", 0) > self.ttl:
            del self._states[key]
            return {}
        return entry.get("data", {})

    def update_state(
        self, user_id: int, telegram_binding_id: Optional[str], data_updates: Dict[str, Any]
    ) -> None:
        key = self._key(user_id, telegram_binding_id)
        current = self.get_state(user_id, telegram_binding_id)
        current.update(data_updates)
        self._states[key] = {
            "timestamp": time.time(),
            "data": current,
        }

    def clear_state(self, user_id: int, telegram_binding_id: Optional[str]) -> None:
        key = self._key(user_id, telegram_binding_id)
        if key in self._states:
            del self._states[key]


conversation_state_manager = ConversationStateManager()
