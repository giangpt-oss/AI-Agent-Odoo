import sqlite3
import json
from typing import Any, Dict
from pathlib import Path
from app.services.file_service import file_service

class SettingsService:
    def __init__(self):
        self.db_path = Path(file_service.workspace_root) / "settings.db"
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id TEXT PRIMARY KEY,
                    settings_json TEXT NOT NULL
                )
            """)
            conn.commit()

    def get_settings(self, user_id: str) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT settings_json FROM user_settings WHERE user_id = ?", (user_id,))
            row = cursor.fetchone()
            if row:
                return json.loads(row[0])
            
        # Default settings
        return {
            "timezone": "Asia/Ho_Chi_Minh",
            "language": "vi",
            "default_email_account": None,
            "default_calendar": None,
            "default_workspace": None,
            "confirmation_preferences": {}
        }

    def update_settings(self, user_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        current = self.get_settings(user_id)
        current.update(updates)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO user_settings (user_id, settings_json) VALUES (?, ?)",
                (user_id, json.dumps(current))
            )
            conn.commit()
        return current

settings_service = SettingsService()
