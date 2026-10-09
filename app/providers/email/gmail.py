from typing import List, Dict, Any, Optional
import httpx
import base64
from email.message import EmailMessage
from app.providers.email.base import EmailProvider

class GmailProvider(EmailProvider):
    def __init__(self, access_token: str):
        self.access_token = access_token
        self.base_url = "https://gmail.googleapis.com/gmail/v1/users/me"
        self.headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json"
        }

    def _get_client(self) -> httpx.AsyncClient:
        if not hasattr(self, "_client") or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    async def list_messages(self, limit: int = 10, page_token: str = None) -> Dict[str, Any]:
        params = {"maxResults": limit}
        if page_token:
            params["pageToken"] = page_token
            
        client = self._get_client()
        resp = await client.get(f"{self.base_url}/messages", headers=self.headers, params=params)
        resp.raise_for_status()
        return resp.json()

    async def search_messages(self, query: str, limit: int = 10) -> Dict[str, Any]:
        params = {"q": query, "maxResults": limit}
        client = self._get_client()
        resp = await client.get(f"{self.base_url}/messages", headers=self.headers, params=params)
        resp.raise_for_status()
        return resp.json()

    async def get_message(self, message_id: str) -> Dict[str, Any]:
        client = self._get_client()
        resp = await client.get(f"{self.base_url}/messages/{message_id}?format=full", headers=self.headers)
        resp.raise_for_status()
        return resp.json()

    def _create_raw_message(self, to: List[str], subject: str, body: str, cc: List[str] = None, reply_to_message_id: str = None) -> str:
        msg = EmailMessage()
        msg.set_content(body)
        msg['To'] = ", ".join(to)
        if cc:
            msg['Cc'] = ", ".join(cc)
        msg['Subject'] = subject
        
        if reply_to_message_id:
            msg['In-Reply-To'] = reply_to_message_id
            msg['References'] = reply_to_message_id
            
        return base64.urlsafe_b64encode(msg.as_bytes()).decode('utf-8')

    async def create_draft(self, to: List[str], subject: str, body: str, cc: List[str] = None, reply_to_message_id: str = None) -> str:
        raw = self._create_raw_message(to, subject, body, cc, reply_to_message_id)
        payload = {"message": {"raw": raw}}
        
        client = self._get_client()
        resp = await client.post(f"{self.base_url}/drafts", headers=self.headers, json=payload)
        resp.raise_for_status()
        return resp.json().get("id")

    async def send_message(self, to: List[str], subject: str, body: str, cc: List[str] = None, attachments: List[str] = None) -> str:
        raw = self._create_raw_message(to, subject, body, cc)
        payload = {"raw": raw}
        
        client = self._get_client()
        resp = await client.post(f"{self.base_url}/messages/send", headers=self.headers, json=payload)
        resp.raise_for_status()
        return resp.json().get("id")

    async def reply_message(self, original_message_id: str, thread_id: str, body: str, attachments: List[str] = None) -> str:
        orig = await self.get_message(original_message_id)
        headers = orig.get("payload", {}).get("headers", [])
        subject = next((h["value"] for h in headers if h["name"] == "Subject"), "")
        if not subject.startswith("Re:"):
            subject = f"Re: {subject}"
        to = next((h["value"] for h in headers if h["name"] == "From"), "").split(", ")
        
        raw = self._create_raw_message(to, subject, body, reply_to_message_id=original_message_id)
        payload = {"raw": raw, "threadId": thread_id}
        
        client = self._get_client()
        resp = await client.post(f"{self.base_url}/messages/send", headers=self.headers, json=payload)
        resp.raise_for_status()
        return resp.json().get("id")

    async def archive_message(self, message_id: str) -> bool:
        payload = {"removeLabelIds": ["INBOX"]}
        client = self._get_client()
        resp = await client.post(f"{self.base_url}/messages/{message_id}/modify", headers=self.headers, json=payload)
        resp.raise_for_status()
        return True
