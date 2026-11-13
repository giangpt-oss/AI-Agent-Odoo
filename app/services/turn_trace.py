import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar


request_id: ContextVar[str | None] = ContextVar("agent_turn_request_id", default=None)
logger = logging.getLogger(__name__)


@contextmanager
def measure_stage(stage: str):
    started = time.perf_counter()
    try:
        yield
    finally:
        correlation = request_id.get()
        if correlation:
            logger.info("request_id=%s stage=%s duration_ms=%.3f", correlation, stage,
                        (time.perf_counter() - started) * 1000)
