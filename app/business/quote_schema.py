from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class QuoteTurn(BaseModel):
    """Semantic interpretation only; IDs, persistence and validation are application-owned."""
    model_config = ConfigDict(extra='forbid')
    action: Literal['start', 'update', 'preview', 'status', 'show', 'confirm', 'cancel', 'finish_lines', 'explain', 'other'] = 'update'
    customer_query: str | None = Field(default=None, max_length=200)
    product_query: str | None = Field(default=None, max_length=200)
    selected_id: int | None = Field(default=None, gt=0, strict=True)
    line_index: int = Field(default=1, ge=1, le=100)
    quantity: float | None = Field(default=None, gt=0, strict=True)
    price_unit: float | None = Field(default=None, ge=0, strict=True)
    price_confirmed: bool | None = None
    use_catalog_price: bool | None = None
    use_customer_addresses: bool | None = None
    validity_date: str | None = None
    customer_lead: float | None = Field(default=None, ge=0, strict=True)
    delivery_date: str | None = None
    quotation_name: str | None = Field(default=None, min_length=1, max_length=100)
    tax_ids: list[int] | None = None
