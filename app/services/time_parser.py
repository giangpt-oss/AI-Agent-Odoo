import re
from datetime import datetime, timedelta
import zoneinfo
from typing import Dict, Any, Optional

class TimeParserService:
    @staticmethod
    def parse_semantic_time(text: str, default_timezone: str = "Asia/Ho_Chi_Minh") -> Dict[str, Any]:
        """
        Dịch chuỗi thời gian tự nhiên thành datetime timezone-aware.
        Do không dùng thư viện NLP phức tạp trong bước cơ bản, hỗ trợ một số pattern hoặc LLM sẽ extract format chuẩn ISO.
        """
        # We expect the LLM to provide dates in ISO format primarily, 
        # but if we get raw text, we try basic parsing or return ambiguous.
        try:
            # 1. Try strict ISO format
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=zoneinfo.ZoneInfo(default_timezone))
            return {
                "datetime": dt.isoformat(),
                "timezone": str(dt.tzinfo) if dt.tzinfo else default_timezone,
                "confidence": 1.0,
                "is_ambiguous": False
            }
        except ValueError:
            pass

        # 2. Simple fallback rules for basic text like "chiều mai" could be added here
        # or we rely on the LLM explicitly parsing it and passing ISO format to the skill.
        # Per requirement, we must output standard schema and return ambiguity if uncertain.
        
        return {
            "datetime": None,
            "timezone": default_timezone,
            "confidence": 0.0,
            "is_ambiguous": True
        }

    @staticmethod
    def normalize_timezone(dt: datetime, target_tz: str) -> datetime:
        """Chuyển đổi timezone an toàn."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=zoneinfo.ZoneInfo("UTC"))
        return dt.astimezone(zoneinfo.ZoneInfo(target_tz))

time_parser = TimeParserService()
