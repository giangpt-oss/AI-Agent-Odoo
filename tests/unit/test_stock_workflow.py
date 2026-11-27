"""Unit tests for the complete Vietnamese Odoo Stock / Delivery (stock.picking) workflow."""
import pytest

from app.business import operations
from app.business.stock_local import (
    interpret_stock_create,
    interpret_stock_draft_reply,
    interpret_stock_flow,
)
from app.services.confirmation_service import ConfirmationService


class TestStockLocalParsing:
    @pytest.mark.parametrize(
        "text,expected_action,expected_name",
        [
            ("xác nhận phiếu giao hàng WH/OUT/00018", "confirm", "WH/OUT/00018"),
            ("giữ hàng phiếu WH/OUT/00018", "reserve", "WH/OUT/00018"),
            ("kiểm tra tồn kho WH/OUT/00018", "reserve", "WH/OUT/00018"),
            ("bỏ giữ hàng WH/OUT/00018", "unreserve", "WH/OUT/00018"),
            ("hoàn tất giao hàng WH/OUT/00018", "validate", "WH/OUT/00018"),
            ("xác thực phiếu nhập kho WH/IN/00001", "validate", "WH/IN/00001"),
            ("trả hàng phiếu WH/OUT/00001", "return", "WH/OUT/00001"),
            ("hủy phiếu giao hàng WH/OUT/00018", "cancel", "WH/OUT/00018"),
        ],
    )
    def test_interpret_stock_flow_all_actions(self, text, expected_action, expected_name):
        res = interpret_stock_flow(text)
        assert res is not None
        assert res["action"] == expected_action
        assert res["picking_name"] == expected_name

    def test_interpret_stock_flow_backorder_flag(self):
        r1 = interpret_stock_flow("hoàn tất giao hàng WH/OUT/00018")
        assert r1["backorder"] == "create"
        r2 = interpret_stock_flow("hoàn tất giao hàng WH/OUT/00018 không tạo backorder")
        assert r2["backorder"] == "cancel"

    def test_interpret_stock_flow_ignores_reads_and_negations(self):
        assert interpret_stock_flow("xem phiếu giao hàng WH/OUT/00018") is None
        assert interpret_stock_flow("kiểm tra trạng thái WH/OUT/00018?") is None
        assert interpret_stock_flow("đừng hủy phiếu WH/OUT/00018") is None

    def test_interpret_stock_create_variants(self):
        assert interpret_stock_create("tạo phiếu giao hàng cho tôi") == {"action": "start", "operation_type": "outgoing"}
        assert interpret_stock_create("tạo phiếu nhập kho mới") == {"action": "start", "operation_type": "incoming"}
        assert interpret_stock_create("tạo phiếu chuyển kho nội bộ") == {"action": "start", "operation_type": "internal"}

        r = interpret_stock_create(
            "tạo phiếu giao hàng cho khách hàng Anh Diệp, 2 cái máy điện xung, ngày giao 30/12/2026, chứng từ gốc S00118, ghi chú giao sáng"
        )
        assert r["operation_type"] == "outgoing"
        assert r["partner_query"] == "Anh Diệp"
        assert r["lines"] == [{"product_query": "máy điện xung", "quantity": 2.0}]
        assert r["scheduled_date"] == "2026-12-30 00:00:00+00:00"
        assert r["origin"] == "S00118"
        assert r["note"] == "giao sáng"

    def test_interpret_stock_draft_reply_multiline_and_cancel(self):
        draft = {"draft_type": "business", "business_action": "business.stock.create", "business_data": {}}
        rep = interpret_stock_draft_reply("Anh Diệp\nMáy điện xung - số lượng 3", draft)
        assert rep["action"] == "update"
        assert rep["partner_query"] == "Anh Diệp"
        assert rep["lines"] == [{"product_query": "Máy điện xung", "quantity": 3.0}]
        assert interpret_stock_draft_reply("hủy nháp", draft) == {"action": "cancel"}


class TestStockPreviewAndLabels:
    def test_preview_text_formats_execution_lines_and_backorder(self):
        text = operations.preview_text({
            "title": "Hoàn tất phiếu kho",
            "company_name": "Công ty ABC",
            "target": {"id": 32, "name": "WH/OUT/00018"},
            "execution_lines": [{"id": 99, "quantity": 2, "lot_id": 5}],
            "backorder": "create",
        })
        assert '{"id":' not in text
        assert "1. Dòng #99 — SL: 2 · Lô/Serial ID 5" in text
        assert "Phần còn lại: Tạo phiếu phần còn lại (Backorder)" in text

    def test_human_label_for_stock(self):
        assert ConfirmationService._human_label(
            "business.stock.validate",
            preview_text="Hoàn tất phiếu kho\nBản ghi: WH/OUT/00018 (ID 32)",
        ) == "Phiếu kho WH/OUT/00018"
        assert ConfirmationService._human_label("business.stock.create") == "Yêu cầu tạo phiếu kho"
