from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class QuoteLineFacts(BaseModel):
    model_config = ConfigDict(extra='forbid')
    product_query: str | None = Field(default=None, max_length=200)
    name: str | None = Field(default=None, max_length=2000)
    line_index: int = Field(default=1, ge=1, le=100)
    quantity: float | None = Field(default=None, gt=0, strict=True)
    price_unit: float | None = Field(default=None, ge=0, strict=True)
    price_confirmed: bool | None = None
    use_catalog_price: bool | None = None
    customer_lead: float | None = Field(default=None, ge=0, strict=True)
    tax_ids: list[int] | None = None
    uom_id: int | None = Field(default=None, gt=0, strict=True)
    line_custom_fields: dict[str, str | int | float | bool] | None = None


class QuoteTurn(QuoteLineFacts):
    """Semantic interpretation only; IDs, persistence and validation are application-owned."""
    action: Literal['start', 'update', 'preview', 'status', 'show', 'confirm', 'cancel', 'finish_lines', 'explain', 'other'] = 'update'
    customer_query: str | None = Field(default=None, max_length=200)
    selected_id: int | None = Field(default=None, gt=0, strict=True)
    line_updates: list[QuoteLineFacts] | None = Field(default=None, max_length=100)
    use_customer_addresses: bool | None = None
    validity_date: str | None = None
    delivery_date: str | None = None
    quotation_name: str | None = Field(default=None, min_length=1, max_length=100)
    custom_fields: dict[str, str | int | float | bool] | None = None
    partner_invoice_id: int | None = Field(default=None, gt=0, strict=True)
    partner_shipping_id: int | None = Field(default=None, gt=0, strict=True)
    pricelist_id: int | None = Field(default=None, gt=0, strict=True)
    payment_term_id: int | None = Field(default=None, gt=0, strict=True)
    fiscal_position_id: int | None = Field(default=None, gt=0, strict=True)
    currency_id: int | None = Field(default=None, gt=0, strict=True)
    note: str | None = Field(default=None, max_length=10000)
    client_order_ref: str | None = Field(default=None, max_length=200)
    origin: str | None = Field(default=None, max_length=200)
