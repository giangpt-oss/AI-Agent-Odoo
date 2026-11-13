from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatterOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    body: str | None = Field(default=None, max_length=12000)
    recipient: str | None = None
    activity_type: Literal['todo', 'email', 'call', 'document'] | None = None
    summary: str | None = None
    date_deadline: str | None = None
    start: str | None = None
    stop: str | None = None
    user_query: str | None = None
    user_id: int | None = None
    note: str | None = None
    attendee_ids: list[int] | None = None
    attendee_queries: list[str] | None = Field(default=None, max_length=20)


class ChatterTurn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['message', 'note', 'activity', 'meeting']
    quotation_name: str | None = None
    options: ChatterOptions = Field(default_factory=ChatterOptions)
