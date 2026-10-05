import logging
import time
from enum import Enum
from typing import Callable, Any

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "CLOSED"        # Hoạt động bình thường
    OPEN = "OPEN"            # Đang ngắt, từ chối mọi cuộc gọi (Fast-Fail)
    HALF_OPEN = "HALF_OPEN"  # Đang thử nghiệm phục hồi


class CircuitBreakerOpenException(Exception):
    """Ngoại lệ khi cầu dao đang ngắt để bảo vệ dịch vụ."""
    pass


class ScopedCircuitBreaker:
    """Cầu dao tự ngắt bảo vệ cho từng dịch vụ ngoại vi riêng biệt (Odoo, Google, etc.)."""

    def __init__(
        self,
        service_name: str,
        failure_threshold: int = 3,
        recovery_timeout_seconds: float = 10.0,
    ):
        self.service_name = service_name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout_seconds
        
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.last_failure_time: float = 0.0

    def is_available(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            # Kiểm tra xem đã qua thời gian hồi phục chưa
            now = time.monotonic()
            if now - self.last_failure_time >= self.recovery_timeout:
                logger.info(f"Circuit for '{self.service_name}' switched to HALF_OPEN (Attempting recovery)")
                self.state = CircuitState.HALF_OPEN
                return True
            return False

        # HALF_OPEN: Cho phép 1 thử nghiệm
        return True

    def record_success(self):
        if self.state != CircuitState.CLOSED:
            logger.info(f"Circuit for '{self.service_name}' RECOVERED. Switched to CLOSED.")
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0

    def record_failure(self, error: Exception):
        self.consecutive_failures += 1
        self.last_failure_time = time.monotonic()

        logger.warning(
            f"Circuit '{self.service_name}' recorded failure #{self.consecutive_failures}: {error}"
        )

        if self.consecutive_failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.error(
                f"🚨 CIRCUIT BREAKER TRIPPED to OPEN for '{self.service_name}'. "
                f"Fast-failing subsequent calls for {self.recovery_timeout}s."
            )

    async def execute(self, func: Callable, *args, **kwargs) -> Any:
        """Bọc một hàm async trong Circuit Breaker."""
        if not self.is_available():
            raise CircuitBreakerOpenException(
                f"CODE_429_CORRELATED_FAILURE: Dịch vụ '{self.service_name}' đang gặp sự cố quá tải. "
                f"Cầu dao bảo vệ đang mở (OPEN), vui lòng thử lại sau ít phút."
            )

        try:
            result = await func(*args, **kwargs)
            self.record_success()
            return result
        except Exception as e:
            self.record_failure(e)
            raise


# Registry các Circuit Breakers theo từng service độc lập
odoo_circuit_breaker = ScopedCircuitBreaker(service_name="odoo_cloud", failure_threshold=3, recovery_timeout_seconds=5.0)
google_circuit_breaker = ScopedCircuitBreaker(service_name="google_workspace", failure_threshold=3, recovery_timeout_seconds=5.0)
