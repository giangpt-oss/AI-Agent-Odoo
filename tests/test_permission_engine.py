"""Tests for PermissionEngine, RBAC evaluation, Read/Write segregation, and Audit."""
import pytest
from unittest.mock import AsyncMock, patch
from app.security.permissions import permission_engine, PermissionType
from app.services.audit import audit_service


def test_permission_engine_rbac_evaluation():
    # 1. User sales_user chỉ có quyền đọc sales
    decision_read = permission_engine.evaluate("get_sales_orders", ["sales_user"])
    assert decision_read.allowed is True
    assert decision_read.is_write is False
    assert decision_read.reason is None

    # 2. Phân tách Read/Write: sales_user cố tình thực hiện write action -> Tự động từ chối
    decision_write = permission_engine.evaluate("create_sales_order", ["sales_user"])
    assert decision_write.allowed is False
    assert decision_write.is_write is True
    assert "Tự động từ chối" in decision_write.reason
    assert "sales.order.write" in decision_write.reason

    # 3. User sales_manager có cả quyền đọc và ghi
    decision_mgr = permission_engine.evaluate("create_sales_order", ["sales_manager"])
    assert decision_mgr.allowed is True
    assert decision_mgr.is_write is True

    # 4. Warehouse user không có quyền bán hàng -> Từ chối
    decision_wh = permission_engine.evaluate("get_sales_orders", ["warehouse_user"])
    assert decision_wh.allowed is False

    # 5. Admin có toàn quyền
    decision_admin = permission_engine.evaluate("create_sales_order", ["admin"])
    assert decision_admin.allowed is True
    print("\n[OK] PermissionEngine fine-grained RBAC and Read/Write segregation verified")


@pytest.mark.asyncio
async def test_audit_service_logging_fallback():
    # Kiểm tra log_event chạy mượt mà và không crash ngay cả khi không có kết nối DB
    await audit_service.log_event(
        telegram_chat_id=123456,
        request_id="req-test-audit-01",
        tool_name="create_sales_order",
        status="PERMISSION_DENIED",
        error_message="User lacking sales.order.write",
    )
    print("[OK] AuditService event logging verified without errors")
