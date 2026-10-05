"""Tests for OdooAsyncClient, OdooConnector, and Layer 2 Security Error Handling."""
import pytest
from unittest.mock import AsyncMock, patch
from app.connectors.odoo.client import (
    OdooAsyncClient,
    OdooAccessDeniedException,
    OdooAPIException,
)
from app.connectors.odoo.connector import OdooConnector
from app.tools.odoo_tools import GetSalesOrdersTool, CreateSalesOrderTool
from app.tools.base import ToolContext


@pytest.mark.asyncio
async def test_odoo_client_authentication_success():
    client = OdooAsyncClient(
        base_url="http://mock-odoo.test",
        db="odoo_test",
        username="admin",
        api_key="mock_key",
    )

    # Mock call_jsonrpc trả về UID = 2
    with patch.object(client, "call_jsonrpc", new_callable=AsyncMock) as mock_rpc:
        mock_rpc.return_value = 2
        uid = await client.authenticate()
        assert uid == 2
        assert client.uid == 2
        mock_rpc.assert_called_once_with(
            "common", "authenticate", "odoo_test", "admin", "mock_key", {}
        )
    print("\n[OK] OdooClient authentication success verified")


@pytest.mark.asyncio
async def test_odoo_client_layer2_access_denied():
    """Kiểm tra Layer 2 Security: Odoo trả về lỗi AccessError vi phạm phân quyền."""
    client = OdooAsyncClient(
        base_url="http://mock-odoo.test",
        db="odoo_test",
        username="employee",
        api_key="mock_key",
    )

    mock_error_response = {
        "error": {
            "code": 200,
            "message": "Odoo Server Error",
            "data": {
                "name": "odoo.exceptions.AccessError",
                "message": "You are not allowed to access 'Sale Order' (sale.order) records.",
            },
        }
    }

    from unittest.mock import MagicMock
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_error_response
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        with pytest.raises(OdooAccessDeniedException) as exc_info:
            await client.call_jsonrpc("object", "execute_kw")

        assert "Odoo từ chối quyền" in str(exc_info.value)
    print("[OK] Odoo Layer 2 AccessError interception verified")


@pytest.mark.asyncio
async def test_odoo_connector_search_read():
    mock_client = AsyncMock(spec=OdooAsyncClient)
    mock_client.execute_kw.return_value = [
        {"id": 1, "name": "SO001", "amount_total": 50000000}
    ]

    connector = OdooConnector(client=mock_client)
    orders = await connector.search_read(
        model="sale.order",
        domain=[["state", "=", "sale"]],
        fields=["name", "amount_total"],
        limit=5,
    )

    assert len(orders) == 1
    assert orders[0]["name"] == "SO001"
    mock_client.execute_kw.assert_called_once()
    print("[OK] OdooConnector search_read abstraction verified")


@pytest.mark.asyncio
async def test_get_sales_orders_tool_layer2_denial():
    """Test Tool bắt lỗi Layer 2 Security từ Odoo và trả về ToolResult an toàn."""
    tool = GetSalesOrdersTool()
    context = ToolContext(employee_id="emp-1", telegram_chat_id=123, roles=["sales_user"])

    with patch("app.tools.odoo_tools.get_odoo_connector") as mock_get_conn:
        mock_conn = AsyncMock()
        mock_conn.search_read.side_effect = OdooAccessDeniedException("Record Rule Violation")
        mock_get_conn.return_value = mock_conn

        result = await tool.execute(context, query="Khách ABC")

        assert result.success is False
        assert "Layer 2 Security Denied" in result.error
        assert result.metadata["security_layer"] == 2
    print("[OK] GetSalesOrdersTool handled Layer 2 Security denial gracefully")


@pytest.mark.asyncio
async def test_get_opportunities_tool_success():
    from app.tools.odoo_tools import GetOpportunitiesTool
    tool = GetOpportunitiesTool()
    context = ToolContext(employee_id="emp-1", telegram_chat_id=123, roles=["sales_user"])

    with patch("app.tools.odoo_tools.get_odoo_connector") as mock_get_conn:
        mock_conn = AsyncMock()
        mock_conn.search_read.return_value = [
            {"id": 1, "name": "Cơ hội TEST", "expected_revenue": 50000000.0, "probability": 80.0, "stage_id": [1, "Đàm phán"]}
        ]
        mock_get_conn.return_value = mock_conn

        result = await tool.execute(context, query="TEST")
        assert result.success is True
        assert len(result.data) == 1
        assert result.data[0]["expected_revenue"] == 50000000.0
    print("[OK] GetOpportunitiesTool verified")


@pytest.mark.asyncio
async def test_get_employees_tool_success():
    from app.tools.odoo_tools import GetEmployeesTool
    tool = GetEmployeesTool()
    context = ToolContext(employee_id="emp-1", telegram_chat_id=123, roles=["admin"])

    with patch("app.tools.odoo_tools.get_odoo_connector") as mock_get_conn:
        mock_conn = AsyncMock()
        mock_conn.search_read.return_value = [
            {"id": 1, "name": "Nguyễn Văn A", "job_title": "Giám đốc", "department_id": [1, "Ban Điều Hành"]}
        ]
        mock_get_conn.return_value = mock_conn

        result = await tool.execute(context)
        assert result.success is True
        assert len(result.data) == 1
        assert result.data[0]["job_title"] == "Giám đốc"
    print("[OK] GetEmployeesTool verified")

