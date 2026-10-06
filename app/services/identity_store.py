"""Persistent private-chat sessions. Credentials are encrypted with a local key."""
import json
import os
import sqlite3
import time
from pathlib import Path

from cryptography.fernet import Fernet


class IdentityStore:
    def __init__(self, directory=None):
        self.directory = Path(directory or os.getenv("AGENT_DATA_DIR", ".agent_data")).resolve()

    def _connect(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.directory / "identities.db")
        conn.execute("CREATE TABLE IF NOT EXISTS identities (chat_id INTEGER PRIMARY KEY, payload BLOB NOT NULL, expires REAL NOT NULL)")
        return conn

    def _cipher(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / "identity.key"
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(Fernet.generate_key())
        return Fernet(path.read_bytes())

    def save(self, chat_id, profile, login, credential, ttl=86400):
        payload = self._cipher().encrypt(json.dumps({"profile": profile, "login": login, "credential": credential}).encode())
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO identities VALUES (?, ?, ?)", (chat_id, payload, time.time() + ttl))

    def get(self, chat_id):
        with self._connect() as conn:
            row = conn.execute("SELECT payload, expires FROM identities WHERE chat_id = ?", (chat_id,)).fetchone()
        if not row or row[1] <= time.time():
            return None
        return json.loads(self._cipher().decrypt(row[0]))

    def delete(self, chat_id):
        with self._connect() as conn:
            return conn.execute("DELETE FROM identities WHERE chat_id = ?", (chat_id,)).rowcount > 0

    def list_profiles(self):
        with self._connect() as conn:
            ids = [r[0] for r in conn.execute("SELECT chat_id FROM identities WHERE expires > ?", (time.time(),))]
        return [(chat_id, item["profile"]) for chat_id in ids if (item := self.get(chat_id))]


identity_store = IdentityStore()
