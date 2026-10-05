import uuid
from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin


class OAuthToken(Base, TimestampMixin):
    __tablename__ = "oauth_tokens"

    employee_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # google | microsoft | etc.
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    account_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Refresh Token đã mã hóa AES-Fernet (tuyệt đối không lưu plain text)
    encrypted_refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    
    # Access token tạm thời (nếu cache)
    encrypted_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    employee = relationship("Employee", back_populates="oauth_tokens")
