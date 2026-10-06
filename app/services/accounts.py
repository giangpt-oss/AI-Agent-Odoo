import sqlite3
import json
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime
from pathlib import Path
from app.services.file_service import file_service

class ProviderAccountManager:
    def __init__(self):
        self.db_path = Path(file_service.workspace_root) / "accounts.db"
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS provider_accounts (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    account_id TEXT,
                    display_name TEXT,
                    email TEXT,
                    status TEXT NOT NULL,
                    scopes TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.commit()

    def _row_to_dict(self, row) -> Dict[str, Any]:
        return {
            "id": row[0],
            "user_id": row[1],
            "provider": row[2],
            "account_id": row[3],
            "display_name": row[4],
            "email": row[5],
            "status": row[6],
            "scopes": json.loads(row[7]) if row[7] else [],
            "created_at": row[8],
            "updated_at": row[9]
        }

    def connect_account(self, user_id: str, provider: str, account_id: str, email: str, display_name: str, scopes: List[str]) -> Dict[str, Any]:
        now = datetime.now().isoformat()
        _id = f"acc_{uuid.uuid4().hex[:8]}"
        with sqlite3.connect(self.db_path) as conn:
            # Upsert logic based on provider and email to prevent duplicates
            cursor = conn.execute("SELECT id FROM provider_accounts WHERE user_id = ? AND provider = ? AND email = ?", (user_id, provider, email))
            existing = cursor.fetchone()
            if existing:
                _id = existing[0]
                conn.execute(
                    "UPDATE provider_accounts SET account_id=?, display_name=?, status=?, scopes=?, updated_at=? WHERE id=?",
                    (account_id, display_name, "CONNECTED", json.dumps(scopes), now, _id)
                )
            else:
                conn.execute(
                    "INSERT INTO provider_accounts (id, user_id, provider, account_id, display_name, email, status, scopes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (_id, user_id, provider, account_id, display_name, email, "CONNECTED", json.dumps(scopes), now, now)
                )
            conn.commit()
        return self.get_account(_id)

    def get_account(self, id: str) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT id, user_id, provider, account_id, display_name, email, status, scopes, created_at, updated_at FROM provider_accounts WHERE id = ?", (id,))
            row = cursor.fetchone()
            if row:
                return self._row_to_dict(row)
        return None

    def list_accounts(self, user_id: str) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT id, user_id, provider, account_id, display_name, email, status, scopes, created_at, updated_at FROM provider_accounts WHERE user_id = ?", (user_id,))
            rows = cursor.fetchall()
            return [self._row_to_dict(r) for r in rows]

    def update_status(self, id: str, status: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE provider_accounts SET status = ?, updated_at = ? WHERE id = ?", (status, datetime.now().isoformat(), id))
            conn.commit()

provider_account_manager = ProviderAccountManager()
