from unittest.mock import AsyncMock, patch

import pytest

from app.domain.gateway_context import GatewayUserContext
from app.domain.intent_schema import IntentAnalysisResult, IntentType
from app.services.conversation_history_store import conversation_history_store
from app.services.unified_executor import unified_executor


@pytest.fixture
def test_user_context():
    return GatewayUserContext(
        user_id=1,
        tenant="default",
        database="odoo_db",
        odoo_uid=10,
        telegram_user_id=123456,
        chat_id=123456,
        name="Test Employee",
        login="test@example.com",
        timezone="Asia/Bangkok",
        telegram_binding_id="bind_test_123",
        permissions={"tasks.read", "tasks.write"},
    )


@pytest.mark.asyncio
async def test_odoo_partners_search_success(test_user_context):
    mock_items = [
        {
            "id": 101,
            "name": "Công ty TNHH An Phát",
            "is_company": True,
            "email": "anphat@example.com",
            "phone": "0901234567",
            "city": "Hà Nội",
        },
        {
            "id": 102,
            "name": "Nguyễn Văn Nam",
            "is_company": False,
            "email": "nam@example.com",
            "phone": "0987654321",
            "city": "TP.HCM",
        },
    ]

    with patch("app.services.gateway_client.gateway_client.query_odoo", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = {
            "status": "OK",
            "model": "res.partner",
            "count": 2,
            "items": mock_items,
        }

        intent = IntentAnalysisResult(
            intent_type=IntentType.READ,
            tool_name="odoo.partners.search",
            parameters={"query": "An Phát"},
        )

        res = await unified_executor.execute(
            intent=intent,
            context=test_user_context,
            chat_id=123456,
            telegram_user_id=123456,
            session_context={},
            deadline=9999999999.0,
            user_info={},
        )

        assert len(res) == 1
        output = res[0]
        assert "Công ty TNHH An Phát" in output
        assert "🏢 Công ty" in output
        assert "anphat@example.com" in output
        assert "Nguyễn Văn Nam" in output
        assert "👤 Cá nhân" in output

        # Verify query arguments sent to gateway
        mock_query.assert_called_once()
        _, kwargs = mock_query.call_args
        assert kwargs["model"] == "res.partner"
        assert kwargs["domain"] == ["|", ("name", "ilike", "An Phát"), ("email", "ilike", "An Phát")]

        # Verify saved to history
        cached = conversation_history_store.get_last_read_data(
            test_user_context.tenant, test_user_context.user_id, 123456, test_user_context.telegram_binding_id, "odoo.partners", database=test_user_context.database
        )
        assert len(cached) == 2


@pytest.mark.asyncio
async def test_odoo_products_search_success(test_user_context):
    mock_items = [
        {
            "id": 201,
            "name": "Bàn làm việc thông minh",
            "default_code": "BLV-01",
            "list_price": 3500000.0,
            "qty_available": 12.0,
            "uom_id": [1, "Cái"],
        }
    ]

    with patch("app.services.gateway_client.gateway_client.query_odoo", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = {
            "status": "OK",
            "model": "product.product",
            "count": 1,
            "items": mock_items,
        }

        intent = IntentAnalysisResult(
            intent_type=IntentType.READ,
            tool_name="odoo.products.search",
            parameters={"query": "BLV"},
        )

        res = await unified_executor.execute(
            intent=intent,
            context=test_user_context,
            chat_id=123456,
            telegram_user_id=123456,
            session_context={},
            deadline=9999999999.0,
            user_info={},
        )

        assert len(res) == 1
        output = res[0]
        assert "Bàn làm việc thông minh" in output
        assert "BLV-01" in output
        assert "3,500,000 đ" in output
        assert "Tồn: 12.0 Cái" in output


@pytest.mark.asyncio
async def test_odoo_sales_search_many2one_formatting(test_user_context):
    mock_items = [
        {
            "id": 301,
            "name": "SO001",
            "partner_id": [101, "Công ty TNHH An Phát"],
            "amount_total": 15000000.0,
            "state": "sale",
        }
    ]

    with patch("app.services.gateway_client.gateway_client.query_odoo", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = {
            "status": "OK",
            "model": "sale.order",
            "count": 1,
            "items": mock_items,
        }

        intent = IntentAnalysisResult(
            intent_type=IntentType.READ,
            tool_name="odoo.sales.search",
            parameters={"query": "SO001"},
        )

        res = await unified_executor.execute(
            intent=intent,
            context=test_user_context,
            chat_id=123456,
            telegram_user_id=123456,
            session_context={},
            deadline=9999999999.0,
            user_info={},
        )

        assert len(res) == 1
        output = res[0]
        assert "SO001" in output
        assert "Công ty TNHH An Phát" in output
        # Tuyệt đối không hiển thị dạng raw Many2one "[101, 'Công ty TNHH An Phát']"
        assert "[101," not in output
        assert "[Đơn bán]" in output


@pytest.mark.asyncio
async def test_odoo_employees_search_success(test_user_context):
    mock_items = [
        {
            "id": 401,
            "name": "Trần Thị Mai",
            "job_title": "Chuyên viên Kế toán",
            "department_id": [5, "Phòng Tài chính Kế toán"],
            "work_email": "mai.tran@company.com",
            "work_phone": "0243123456",
        }
    ]

    with patch("app.services.gateway_client.gateway_client.query_odoo", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = {
            "status": "OK",
            "model": "hr.employee",
            "count": 1,
            "items": mock_items,
        }

        intent = IntentAnalysisResult(
            intent_type=IntentType.READ,
            tool_name="odoo.employees.search",
            parameters={"query": "Mai"},
        )

        res = await unified_executor.execute(
            intent=intent,
            context=test_user_context,
            chat_id=123456,
            telegram_user_id=123456,
            session_context={},
            deadline=9999999999.0,
            user_info={},
        )

        assert len(res) == 1
        output = res[0]
        assert "Trần Thị Mai" in output
        assert "Chuyên viên Kế toán" in output
        assert "Phòng Tài chính Kế toán" in output
        assert "mai.tran@company.com" in output


@pytest.mark.asyncio
async def test_odoo_query_permission_denied(test_user_context):
    with patch("app.services.gateway_client.gateway_client.query_odoo", new_callable=AsyncMock) as mock_query:
        mock_query.return_value = {
            "status": "PERMISSION_DENIED",
            "message": "Access denied by Odoo ACL",
            "items": [],
        }

        intent = IntentAnalysisResult(
            intent_type=IntentType.READ,
            tool_name="odoo.invoices.search",
            parameters={"query": "INV"},
        )

        res = await unified_executor.execute(
            intent=intent,
            context=test_user_context,
            chat_id=123456,
            telegram_user_id=123456,
            session_context={},
            deadline=9999999999.0,
            user_info={},
        )

        assert len(res) == 1
        output = res[0]
        assert "không có quyền truy cập dữ liệu hóa đơn" in output
