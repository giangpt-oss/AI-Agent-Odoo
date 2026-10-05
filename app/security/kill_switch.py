import logging
from app.core.config import get_settings
from app.core.redis import redis_service

logger = logging.getLogger(__name__)

KILL_SWITCH_REDIS_KEY = "system:emergency_kill_switch"


class KillSwitchManager:
    """Quản trị viên có thể bật/tắt khẩn cấp toàn bộ hệ thống hoặc hành động ghi.
    Triển khai thuần túy bằng Backend/Redis, không phụ thuộc vào LLM.
    """

    def __init__(self):
        self._in_memory_state: bool | None = None

    async def is_active(self) -> bool:
        """Kiểm tra xem Kill Switch có đang bật không."""
        # 1. Thử đọc từ Redis
        val = await redis_service.get(KILL_SWITCH_REDIS_KEY)
        if val is not None:
            return val == "1"

        # 2. Fallback sang bộ nhớ local hoặc config
        if self._in_memory_state is not None:
            return self._in_memory_state

        return get_settings().EMERGENCY_KILL_SWITCH

    async def set_state(self, active: bool) -> None:
        """Bật (True) hoặc Tắt (False) Kill Switch."""
        self._in_memory_state = active
        await redis_service.set(KILL_SWITCH_REDIS_KEY, "1" if active else "0")
        logger.warning(f"🚨 EMERGENCY KILL SWITCH state changed to: {'ON' if active else 'OFF'}")


kill_switch = KillSwitchManager()
