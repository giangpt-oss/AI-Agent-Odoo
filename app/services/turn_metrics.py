"""Structured per-turn routing & LLM observability store (SQLite)."""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from app.services.file_service import file_service

logger = logging.getLogger(__name__)


class TurnMetricsStore:
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or (file_service.data_dir / "turn_metrics.db")
        self._init_db()

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS turn_routing_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT,
                    created_at REAL NOT NULL,
                    user_id INTEGER,
                    raw_text TEXT,
                    route_mode TEXT,
                    confidence REAL,
                    intent_type TEXT,
                    tool_name TEXT,
                    latency_ms REAL,
                    llm_provider TEXT,
                    llm_model TEXT,
                    prompt_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    failover_used INTEGER DEFAULT 0,
                    fallback_used INTEGER DEFAULT 0,
                    error_code TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_turn_logs_created ON turn_routing_logs(created_at DESC)"
            )
            conn.commit()

    def record(
        self,
        *,
        request_id: str,
        user_id: int,
        raw_text: str,
        intent_type: str,
        tool_name: Optional[str],
        latency_ms: float,
        route_mode: Optional[str] = None,
        confidence: Optional[float] = None,
        llm_provider: str = "",
        llm_model: str = "",
        prompt_tokens: int = 0,
        output_tokens: int = 0,
        failover_used: bool = False,
        fallback_used: bool = False,
        error_code: Optional[str] = None,
    ) -> None:
        try:
            with sqlite3.connect(self.db_path, timeout=2.0) as conn:
                conn.execute(
                    """
                    INSERT INTO turn_routing_logs (
                        request_id, created_at, user_id, raw_text, route_mode, confidence,
                        intent_type, tool_name, latency_ms, llm_provider, llm_model,
                        prompt_tokens, output_tokens, failover_used, fallback_used, error_code
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request_id,
                        time.time(),
                        user_id,
                        (raw_text or "")[:500],
                        route_mode,
                        confidence,
                        intent_type,
                        tool_name,
                        round(latency_ms, 2),
                        llm_provider,
                        llm_model,
                        prompt_tokens,
                        output_tokens,
                        1 if failover_used else 0,
                        1 if fallback_used else 0,
                        error_code,
                    ),
                )
                conn.commit()
        except Exception as exc:
            logger.debug("turn_metrics record skipped: %s", exc)

    def query_recent(self, *, limit: int = 50, only_weak: bool = False) -> list[dict[str, Any]]:
        where = "WHERE (confidence IS NOT NULL AND confidence < 0.6) OR fallback_used = 1 OR error_code IS NOT NULL" if only_weak else ""
        with sqlite3.connect(self.db_path, timeout=2.0) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT * FROM turn_routing_logs {where} ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]


turn_metrics_store = TurnMetricsStore()
