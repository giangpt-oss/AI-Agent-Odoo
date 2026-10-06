import uuid
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field
from datetime import datetime

class SourceType:
    DOCUMENT = "DOCUMENT"
    PDF = "PDF"
    DOCX = "DOCX"
    SPREADSHEET = "SPREADSHEET"
    PRESENTATION = "PRESENTATION"
    NOTE = "NOTE"
    MEETING = "MEETING"
    EMAIL = "EMAIL"
    RESEARCH = "RESEARCH"

class IndexState:
    NOT_INDEXED = "NOT_INDEXED"
    INDEXING = "INDEXING"
    INDEXED = "INDEXED"
    STALE = "STALE"
    FAILED = "FAILED"

class KnowledgeSource(BaseModel):
    source_id: str = Field(default_factory=lambda: f"src_{uuid.uuid4().hex[:8]}")
    source_type: str
    title: str
    path: Optional[str] = None
    external_id: Optional[str] = None
    workspace_id: str
    owner_id: str = "system"
    mime_type: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    indexed_at: Optional[str] = None
    version: str = "1"
    state: str = IndexState.NOT_INDEXED
    hash: Optional[str] = None

class KnowledgeChunk(BaseModel):
    chunk_id: str = Field(default_factory=lambda: f"chk_{uuid.uuid4().hex[:8]}")
    source_id: str
    text: str
    page: Optional[str] = None
    section: Optional[str] = None
    sheet: Optional[str] = None
    slide: Optional[str] = None
    position: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)

class IndexingJob(BaseModel):
    job_id: str = Field(default_factory=lambda: f"job_{uuid.uuid4().hex[:8]}")
    type: str = "INDEX"
    status: str = "PENDING"  # PENDING, RUNNING, COMPLETED, FAILED, CANCELLED
    progress: int = 0
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    finished_at: Optional[str] = None
    error: Optional[str] = None

class SearchResult(BaseModel):
    chunk_id: str
    source_id: str
    title: str
    content: str
    score: float
    source: Dict[str, Any] = Field(default_factory=dict) # e.g. {"file": "", "page": 1}
