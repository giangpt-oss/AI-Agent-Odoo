from typing import Any
from sqlalchemy import BigInteger, Boolean, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin


class Employee(Base, TimestampMixin):
    __tablename__ = "employees"

    telegram_chat_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, index=True, nullable=False
    )
    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    odoo_user_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    
    # Danh sách các roles (VD: ["sales_user", "inventory_viewer"])
    roles: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    
    # API key cá nhân của user trên Odoo (nếu dùng per-user key) được mã hóa
    encrypted_odoo_key: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Relationships
    audit_logs = relationship("AuditLog", back_populates="employee", cascade="all, delete-orphan")
    oauth_tokens = relationship("OAuthToken", back_populates="employee", cascade="all, delete-orphan")

    def has_role(self, role: str) -> bool:
        return role in self.roles
