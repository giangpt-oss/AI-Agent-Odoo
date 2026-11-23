"""Strict schema for Stage 1 TurnRouter decisions."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class RouteMode(str, Enum):
    CHITCHAT = "chitchat"
    CONTEXT = "context"
    KNOWLEDGE = "knowledge"
    READ = "read"
    PREPARE_WRITE = "prepare_write"
    CLARIFY = "clarify"


class RouteDomain(str, Enum):
    CALENDAR = "calendar"
    GMAIL = "gmail"
    DRIVE = "drive"
    DOCS = "docs"
    SHEETS = "sheets"
    SLIDES = "slides"
    ODOO = "odoo"
    CRM = "crm"
    QUOTATION = "quotation"
    REPORTS = "reports"
    TASKS = "tasks"
    KNOWLEDGE = "knowledge"
    BRIEFING = "briefing"


class ContextLookupSpec(BaseModel, extra="forbid"):
    """Structured pointer to a displayed record/field in session last_read."""

    category: Optional[str] = Field(default=None, max_length=64)
    item_index: Optional[int] = Field(default=None, ge=1, le=50)
    field_name: Optional[str] = Field(default=None, max_length=64)
    asks_live_status: bool = False


class RouteDecision(BaseModel, extra="forbid"):
    mode: RouteMode
    confidence: float = Field(ge=0.0, le=1.0)
    normalized_text: str = Field(min_length=1, max_length=500)
    domains: list[RouteDomain] = Field(default_factory=list, max_length=4)
    context_lookup: Optional[ContextLookupSpec] = None
    clarify_reason: Optional[str] = Field(default=None, max_length=300)
    clarify_options: list[str] = Field(default_factory=list, max_length=3)
