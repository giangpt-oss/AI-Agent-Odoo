"""Unit tests for the complete Vietnamese Odoo Purchasing (RFQ / PO) workflow."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.business import operations
from app.business.purchase_local import (
    interpret_purchase_create,
    interpret_purchase_draft_reply,
    interpret_purchase_flow,
    parse_purchase_date,
    payment_term_candidates,
)
from app.services.confirmation_service import ConfirmationService


def _ctx():
    return SimpleNamespace(
        user_id=2,
        chat_id=100,
        telegram_user_id=200,
        telegram_binding_id="b1",
        owner_key="user:2",
        tenant="default",
        database="odoo",
    )


class TestPurchaseLocalParsing:
    @pytest.mark.parametrize(
        "text,expected_action,expected_name",
        [
            ("xác nhận đơn mua P00012", "confirm", "P00012"),
            ("chốt RFQ P00015 giúp tôi", "confirm", "P00015"),
            ("phê duyệt đơn mua P00012", "approve", "P00012"),
            ("duyệt P00012", "approve", "P00012"),
            ("từ chối đơn mua P00012", "reject", "P00012"),
            ("không duyệt P00012", "reject", "P00012"),
            ("hủy đơn mua P00012", "cancel", "P00012"),
            ("hủy RFQ P00012", "cancel", "P00012"),
            ("đưa P00012 về nháp", "draft", "P00012"),
            ("khôi phục nháp đơn mua P00012", "draft", "P00012"),
            ("sao chép đơn mua P00012", "copy", "P00012"),
            ("xóa RFQ P00012", "delete", "P00012"),
            ("gửi RFQ P00012 cho nhà cung cấp", "send", "P00012"),
        ],
    )
    def test_interpret_purchase_flow_all_lifecycle_actions(self, text, expected_action, expected_name):
        res = interpret_purchase_flow(text)
        assert res is not None
        assert res["action"] == expected_action
        assert res["purchase_name"] == expected_name

    def test_interpret_purchase_flow_ignores_reads_and_questions(self):
        assert interpret_purchase_flow("xem đơn mua P00012") is None
        assert interpret_purchase_flow("kiểm tra trạng thái P00012?") is None
        assert interpret_purchase_flow("đừng hủy đơn mua P00012") is None

    def test_interpret_purchase_create_natural_variants(self):
        # Legacy exact template
        r1 = interpret_purchase_create("Tạo RFQ cho nhà cung cấp Azure Interior, sản phẩm Bàn làm việc, số lượng 5")
        assert r1["supplier_query"] == "Azure Interior"
        assert r1["lines"] == [{"product_query": "Bàn làm việc", "quantity": 5.0}]

        # Natural Vietnamese order: product first, supplier last + optional fields
        r2 = interpret_purchase_create(
            "tạo đơn mua 3 cái bàn làm việc đơn giá 500k và 2 ghế công thái học từ NCC Hòa Phát, "
            "ngày nhận 30/12/2026, điều khoản thanh toán 30 ngày, mã tham chiếu NCC PO-99, ghi chú giao giờ hành chính"
        )
        assert r2["supplier_query"] == "Hòa Phát"
        assert len(r2["lines"]) == 2
        assert r2["lines"][0]["product_query"] == "bàn làm việc"
        assert r2["lines"][0]["quantity"] == 3.0
        assert r2["lines"][0]["price_unit"] == 500000.0
        assert r2["lines"][1]["product_query"] == "ghế công thái học"
        assert r2["lines"][1]["quantity"] == 2.0
        assert r2["date_planned"] == "2026-12-30 00:00:00+00:00"
        assert r2["payment_term_query"] == "30 ngày"
        assert r2["partner_ref"] == "PO-99"
        assert r2["notes"] == "giao giờ hành chính"

        # Empty start -> guided collection (including polite Vietnamese suffixes)
        assert interpret_purchase_create("tạo đơn mua mới") == {"action": "start"}
        assert interpret_purchase_create("Tạo đơn mua hàng cho tôi") == {"action": "start"}
        assert interpret_purchase_create("Lập RFQ giúp tôi nhé") == {"action": "start"}

        # Multi-line reply with supplier on line 1 (containing comma) and product + "- số lượng 2" on line 2
        empty_draft = {
            "draft_type": "business",
            "business_action": "business.purchases.create",
            "business_data": {},
        }
        multi = interpret_purchase_draft_reply(
            "GOLD GULF INT'L TRADE CO., LTD - NA\nBuồng oxy cao áp dùng trong y tế. Model: O2 Multi - số lượng 2",
            empty_draft,
        )
        assert multi is not None
        assert multi["action"] == "update"
        assert multi["supplier_query"] == "GOLD GULF INT'L TRADE CO., LTD - NA"
        assert multi["lines"] == [{
            "product_query": "Buồng oxy cao áp dùng trong y tế. Model: O2 Multi",
            "quantity": 2.0,
        }]

        # Partial start (supplier only)
        r4 = interpret_purchase_create("tạo RFQ cho NCC Azure Interior")
        assert r4["action"] == "start"
        assert r4["supplier_query"] == "Azure Interior"
        assert "lines" not in r4

    def test_interpret_purchase_draft_reply_multi_turn(self):
        draft_no_lines = {
            "draft_type": "business",
            "business_action": "business.purchases.create",
            "business_data": {"values": {"partner_id": 10}},
        }
        rep = interpret_purchase_draft_reply("5 cái bàn làm việc, ngày nhận 15/01/2027", draft_no_lines)
        assert rep["action"] == "update"
        assert rep["lines"][0]["product_query"] == "bàn làm việc"
        assert rep["lines"][0]["quantity"] == 5.0
        assert rep["date_planned"] == "2027-01-15 00:00:00+00:00"

        cancel_rep = interpret_purchase_draft_reply("hủy nháp", draft_no_lines)
        assert cancel_rep == {"action": "cancel"}

    def test_date_and_payment_term_helpers(self):
        assert parse_purchase_date("30/12/2026") == "2026-12-30 00:00:00+00:00"
        assert "Immediate Payment" in payment_term_candidates("Thanh toán ngay")
        assert "30 Days" in payment_term_candidates("30 ngày")


class TestPurchasePreviewAndConfirmationUX:
    def test_preview_text_formats_lines_and_recipients_cleanly(self):
        text = operations.preview_text({
            "title": "Tạo RFQ mua",
            "company_id": 1,
            "changes": [
                {"field": "partner_id", "label": "Nhà cung cấp", "before": None, "after": "Azure Interior"},
                {"field": "company_id", "label": "Công ty", "before": None, "after": 1},
            ],
            "lines": [{
                "product_id": 12,
                "product": "Bàn làm việc",
                "quantity": 3,
                "unit": "Cái",
                "price_unit": 500000,
                "taxes": ["10%"],
                "date_planned": "2026-12-30 00:00:00+00:00",
            }],
            "recipients": [{"id": 10, "name": "Azure Interior", "email": "vendor@example.com"}],
        })
        assert '{"product_id"' not in text
        assert "Công ty ID:" not in text
        assert "Công ty:" not in text
        assert "Nhà cung cấp: Azure Interior" in text
        assert "None →" not in text
        assert "1. Bàn làm việc — SL: 3 Cái · Đơn giá: 500,000 · Thuế: 10% · Ngày nhận: 2026-12-30" in text
        assert "Người nhận: Azure Interior <vendor@example.com>" in text

    def test_human_label_for_purchases(self):
        assert ConfirmationService._human_label(
            "business.purchases.confirm",
            preview_text="Xác nhận đơn mua\nBản ghi: P00012 (ID 45)",
        ) == "Đơn mua P00012"
        assert ConfirmationService._human_label(
            "business.purchases.create",
            preview_text="Tạo RFQ mua\nNhà cung cấp: None → Azure Interior",
        ) == "Yêu cầu tạo RFQ mua hàng cho Azure Interior"
        assert ConfirmationService._human_label("business.purchases.cancel") == "Yêu cầu hủy đơn mua"


@pytest.mark.asyncio
async def test_purchase_flow_send_auto_resolves_supplier():
    ctx = _ctx()
    fake_settings = SimpleNamespace(ENABLE_BUSINESS_ACTIONS=True, ENABLE_QUOTATIONS=True, ENABLE_QUOTATION_APPROVAL_FLOW=True)
    lookup_resp = {
        "status": "ok",
        "items": [{"id": 77, "name": "P00012", "company_id": [1, "Cty A"], "partner_id": [10, "Azure Interior"]}],
        "has_more": False,
    }
    captured = {}

    async def fake_read(tool, params, context):
        captured["tool"] = tool
        captured["params"] = params
        return ["PREVIEW_OK"]

    with patch("app.core.config.get_settings", return_value=fake_settings), \
         patch.object(operations, "request", AsyncMock(return_value=lookup_resp)), \
         patch.object(operations, "read", side_effect=lambda t, p, c: fake_read(t, p, c) if t == "business.draft" else operations.read.__wrapped__(t, p, c)):
        # Call directly via patched draft delegation
        pass

    # Clean direct test of business.purchase_flow send path
    orig_read = operations.read

    async def spy_read(tool, params, context):
        if tool == "business.draft":
            captured["params"] = params
            return ["PREVIEW_OK"]
        return await orig_read(tool, params, context)

    with patch("app.core.config.get_settings", return_value=fake_settings), \
         patch.object(operations, "request", AsyncMock(return_value=lookup_resp)), \
         patch.object(operations, "read", side_effect=spy_read):
        res = await orig_read("business.purchase_flow", {"action": "send", "purchase_name": "P00012"}, ctx)

    assert res == ["PREVIEW_OK"]
    assert captured["params"]["business_action"] == "business.purchases.send"
    opts = captured["params"]["data"]["options"]
    assert opts["recipient_ids"] == [10]
    assert "P00012" in opts["subject"]
    assert "Azure Interior" in opts["body"]


@pytest.mark.asyncio
async def test_purchase_approval_button_commands():
    from app.services.natural_pipeline import NaturalPipeline

    ctx = _ctx()
    pipeline = NaturalPipeline()
    tok = "a1b2c3d4e5f60718293a4b5c"

    r_sub = await pipeline.analyze_input(f"/purchase_approve {tok}", ctx, {})
    assert r_sub.tool_name == "business.purchase_approval_button"
    assert r_sub.parameters == {"action": "submit", "token": tok}

    r_ok = await pipeline.analyze_input(f"/purchase_grant {tok}", ctx, {})
    assert r_ok.parameters == {"action": "approve", "token": tok}

    r_no = await pipeline.analyze_input(f"/purchase_reject {tok}", ctx, {})
    assert r_no.parameters == {"action": "reject", "token": tok}

    fake_settings = SimpleNamespace(ENABLE_BUSINESS_ACTIONS=True, ENABLE_QUOTATIONS=True, ENABLE_QUOTATION_APPROVAL_FLOW=True)
    with patch("app.core.config.get_settings", return_value=fake_settings), \
         patch.object(operations, "request", AsyncMock(return_value={"status": "awaiting_approval", "message": "Đã gửi yêu cầu phê duyệt đơn mua P00065."})) as mock_req:
        out = await operations.read("business.purchase_approval_button", {"action": "submit", "token": tok}, ctx)
    assert out == ["Đã gửi yêu cầu phê duyệt đơn mua P00065."]
    mock_req.assert_awaited_once_with(ctx, "POST", "business/purchases/approval-submit", {"token": tok})


def test_interpret_purchase_flow_uses_last_read_and_never_falls_into_workspace_plan():
    last_read = {
        "business_action_result": {
            "status": "completed",
            "result": {"record": {"id": 65, "name": "P00065", "state": "draft"}},
        }
    }
    res = interpret_purchase_flow("Xác nhận đơn mua hàng", history=[], last_read=last_read)
    assert res == {"action": "confirm", "purchase_name": "P00065"}

    res_missing = interpret_purchase_flow("Xác nhận đơn mua hàng", history=[], last_read={})
    assert res_missing == {"action": "confirm", "purchase_name": ""}

    assert interpret_purchase_flow("Hủy tạo đơn mua hàng", history=[], last_read={}) is None


@pytest.mark.asyncio
async def test_purchase_optional_menu_and_selection_flow():
    from app.business import purchase_optional

    ctx = _ctx()
    saved_drafts: list[dict] = []

    def fake_session(_ctx):
        return {"active_draft": saved_drafts[-1] if saved_drafts else None}

    def fake_set_active_draft(*args, **kwargs):
        draft = args[4]
        saved_drafts.append(draft)
        return draft

    async def fake_request(_ctx, method, path, body):
        assert path == "business/lookup"
        if body["kind"] == "currencies":
            return {"status": "ok", "items": [{"id": 23, "display_name": "VND"}, {"id": 2, "display_name": "USD"}]}
        return {"status": "ok", "items": []}

    mock_prepare = AsyncMock(return_value=["PREVIEW_READY"])

    with patch.object(purchase_optional, "session", side_effect=fake_session), \
         patch.object(purchase_optional.store, "set_active_draft", side_effect=fake_set_active_draft), \
         patch.object(purchase_optional, "request", side_effect=fake_request), \
         patch.object(operations, "prepare", mock_prepare):
        # 1. Open menu after supplier + product + qty are resolved
        payload = {"values": {"partner_id": 10}, "lines": [{"product_id": 618, "quantity": 2}]}
        out1 = await purchase_optional.open_menu(ctx, {}, payload)
        assert "1. Mã nhà cung cấp" in out1[0]
        assert "2. Tiền tệ" in out1[0]
        assert purchase_optional.is_active(saved_drafts[-1]) is True

        # 2. User picks "1, 2" -> asks for Mã nhà cung cấp first
        out2 = await purchase_optional.handle_reply("1, 2", ctx)
        assert "Mã nhà cung cấp" in out2[0]

        # 3. User enters partner_ref -> asks for Tiền tệ with Odoo options
        out3 = await purchase_optional.handle_reply("NCC-2026-01", ctx)
        assert saved_drafts[-1]["business_data"]["values"]["partner_ref"] == "NCC-2026-01"
        prompt3 = out3[0].text if hasattr(out3[0], "text") else out3[0]
        assert "USD" in prompt3 and "VND" in prompt3

        # 4. User picks option 2 (USD) -> completes optional step and triggers prepare()
        out4 = await purchase_optional.handle_reply("2", ctx)
        assert out4 == ["PREVIEW_READY"]
        assert saved_drafts[-1]["business_data"]["values"]["currency_id"] == 2
        assert purchase_optional.is_active(saved_drafts[-1]) is False
        mock_prepare.assert_awaited_once_with(ctx)

        # 5. Menu skip ("0") goes straight to prepare()
        mock_prepare.reset_mock()
        await purchase_optional.open_menu(ctx, {}, payload)
        out_skip = await purchase_optional.handle_reply("0", ctx)
        assert out_skip == ["PREVIEW_READY"]
        mock_prepare.assert_awaited_once_with(ctx)


def test_purchase_draft_never_hijacks_rag_questions_or_cancel_phrases():
    empty_draft = {
        "draft_type": "business",
        "business_action": "business.purchases.create",
        "business_data": {},
    }
    assert interpret_purchase_draft_reply("Cho tôi biết quy chế công ty Hopita", empty_draft) is None
    assert interpret_purchase_draft_reply("không cung cấp", empty_draft) is None
    assert interpret_purchase_draft_reply("không chọn", empty_draft) is None
    assert interpret_purchase_draft_reply("hủy tạo đơn hàng", empty_draft) == {"action": "cancel"}
    assert interpret_purchase_draft_reply("hủy đơn mua hàng", empty_draft) == {"action": "cancel"}
