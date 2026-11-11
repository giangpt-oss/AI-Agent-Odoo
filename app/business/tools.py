"""One registry for model tools and deterministic demo commands."""
import json

from app.domain.intent_schema import IntentAnalysisResult, IntentType


def register(registry, reporting: bool, quotation: bool) -> None:
    from app.domain.tool_registry import ToolDefinition

    definitions = []
    if reporting:
        definitions += [
            ("reports.capabilities", "read", "Kiểm tra báo cáo Odoo và công ty có quyền truy cập", {}),
            ("reports.create", "read", "Lập file báo cáo: doanh số/sales, báo giá bán/quotations, đơn mua/purchases, yêu cầu báo giá mua/purchase_rfqs, pipelineCRM/crm, liên hệ/contacts, tồn kho hiện tại/inventory, nhập xuất đã hoàn tất/transfers. Chỉ hỏi kỳ cho các loại yêu cầu kỳ; CRM/liên hệ có thể lấy hiện trạng. Hỏi số đếm đơn thuần dùng công cụ search count_only thay vì lập file.", {
                "report_type": {"enum": ["sales", "quotations", "purchases", "purchase_rfqs", "crm", "contacts", "inventory", "transfers", "revenue", "commercial_summary", "tasks"]},
                "date_from": {"type": "string"}, "date_to": {"type": "string"},
                "output_format": {"enum": ["auto", "docx", "xlsx", "both"], "description": "Định dạng người dùng yêu cầu; không chỉ định thì auto"},
                "report_purpose": {"enum": ["auto", "narrative", "data_analysis", "presentation", "combined"], "description": "Mục đích từ yêu cầu: đọc phân tích, xử lý bảng dữ liệu, trình bày, hoặc cả hai"},
                "company_ids": {"type": "array", "items": {"type": "integer"}},
                **{k: {"type": "integer"} for k in ("partner_id", "warehouse_id", "project_id", "assignee_id")},
                "task_date_field": {"enum": ["date_deadline", "create_date"]},
                "group_by": {"enum": ["customer", "salesperson", "project", "location", "stage", "supplier", "company", "operation"]},
                "record_scope": {"enum": ["visible", "mine"]},
                "reuse_previous": {"type": "boolean", "description": "True chỉ khi người dùng yêu cầu sửa bộ lọc/kỳ của báo cáo vừa lấy; báo cáo mới mặc định false"},
                "stage_id": {"type": "integer"},
                "product_id": {"type": "integer"},
                "crm_type": {"enum": ["lead", "opportunity", "all"]},
                "crm_status": {"enum": ["active", "won", "archived", "all"]},
                "contact_kind": {"enum": ["all", "companies", "individuals", "customers", "suppliers"]},
                "transfer_type": {"enum": ["all", "incoming", "outgoing", "internal"]},
            }),
            ("reports.status", "read", "Xem trạng thái báo cáo gần nhất hoặc report_id", {"report_id": {"type": "string"}}),
            ("reports.file", "read", "Gửi lại hoặc đổi định dạng báo cáo đã hoàn tất trên cùng bộ dữ liệu đã lấy. Không chỉ định định dạng thì giữ file hiện có.", {
                "report_id": {"type": "string"}, "output_format": {"enum": ["auto", "docx", "xlsx", "both"]},
                "report_purpose": {"enum": ["auto", "narrative", "data_analysis", "presentation", "combined"]}}),
            ("reports.cancel", "read", "Hủy job báo cáo đang chờ/chạy", {"report_id": {"type": "string"}}),
            ("reports.lookup", "read", "Tìm/chọn kho, dự án, người phụ trách, khách hàng cho báo cáo", {"kind": {"enum": ["warehouses", "projects", "assignees", "partners", "products", "stages"]}, "query": {"type": "string"}, "company_id": {"type": "integer"}}),
        ]
    if quotation:
        from app.business.quote_schema import QuoteTurn
        definitions += [("sales.quotation.collect", "read", "Lưu thông tin từng lượt vào nháp báo giá thật, tra cứu khách/sản phẩm và hỏi chọn ID; dùng ngay từ câu tạo báo giá chưa đủ thông tin.", QuoteTurn.model_json_schema()["properties"]),
                        ("sales.quotation.status", "read", "Xem trạng thái báo giá bằng số Odoo như S00086 hoặc tiến độ nháp hiện tại; không tạo lại.", {'quotation_name': {'type': 'string'}, 'company_id': {'type': 'integer'}})]
        definitions += [
            ("sales.quotation.lookup", "read", "Tìm khách hàng, sản phẩm, thuế, điều khoản để chọn lập báo giá. Không đoán ID.", {"kind": {"enum": ["partners", "products", "taxes", "companies", "currencies", "pricelists", "payment_terms", "uoms", "fiscal_positions"]}, "query": {"type": "string"}, "company_id": {"type": "integer"}}),
            ("sales.quotation.draft", "read", "Thu thập/sửa nháp báo giá trong hội thoại; chưa tạo trên Odoo. action=start/update/add_line/remove_line/cancel. data là các trường nháp. lines cập nhật theo index, line_index đánh số từ 1. Hỏi phần thiếu; người dùng phải chốt đơn giá và price_confirmed=true.", {
                "action": {"enum": ["start", "update", "add_line", "remove_line", "cancel"]},
                "line_index": {"type": "integer"}, "data": {"type": "object"},
            }),
            ("sales.quotation.preview", "write", "Xem trước nháp báo giá đã đủ thông tin; trả mã xác nhận để tạo báo giá nháp Odoo. Không gửi cho khách hàng.", {}),
        ]
    for name, action, description, props in definitions:
        registry.register(ToolDefinition(name=name, category="business", action_type=action,
            required_permission="sales.create" if name == "sales.quotation.preview" else None,
            description=description, parameter_schema={"type": "object", "properties": props, "additionalProperties": False}))


def parse_command(text: str):
    tokens = text.split(maxsplit=2)
    base = tokens[0].split("@")[0].lower()
    if base not in ("/report", "/quote"):
        return None
    action = tokens[1].lower() if len(tokens) > 1 else ("capabilities" if base == "/report" else "start")
    raw = tokens[2] if len(tokens) > 2 else ""
    if base == "/report":
        if action in ("status", "cancel", "file"):
            name, params = f"reports.{action}", {}
            values = raw.split()
            if action == "file":
                if values and values[-1] in ("auto", "docx", "xlsx", "both"):
                    params["output_format"] = values.pop()
                if len(values) > 1:
                    raise ValueError("Bạn muốn lấy lại báo cáo nào và dưới định dạng Word hay Excel?")
                if values:
                    params["report_id"] = values[0]
            elif raw:
                params["report_id"] = raw
        elif action == "capabilities":
            name, params = "reports.capabilities", {}
        elif action == "lookup":
            name, params = "reports.lookup", json.loads(raw)
        else:
            name = "reports.create"
            values = raw.split()
            params = {"report_type": action}
            if len(values) in (2, 3) and not raw.startswith("{"):
                params.update(date_from=values[0], date_to=values[1])
                if len(values) == 3:
                    if values[2] not in ("auto", "docx", "xlsx", "both"):
                        raise ValueError("Unsupported format")
                    params["output_format"] = values[2]
            elif raw:
                params.update(json.loads(raw))
    else:
        if action == "select":
            selected_id = int(raw)
            if selected_id <= 0:
                raise ValueError("ID must be positive")
            return IntentAnalysisResult(intent_type=IntentType.READ, tool_name="sales.quotation.collect",
                parameters={"action": "update", "selected_id": selected_id})
        if action == "status":
            return IntentAnalysisResult(intent_type=IntentType.READ, tool_name="sales.quotation.status")
        if action == "preview":
            return IntentAnalysisResult(intent_type=IntentType.PREPARE_WRITE, tool_name="sales.quotation.preview")
        if action == "lookup":
            values = raw.split(maxsplit=1)
            name, params = "sales.quotation.lookup", {"kind": values[0] if values else "partners", "query": values[1] if len(values) > 1 else ""}
        else:
            name, params = "sales.quotation.draft", {"action": action}
            if raw:
                parsed = json.loads(raw)
                if action == "remove_line":
                    params["line_index"] = int(parsed)
                else:
                    if not isinstance(parsed, dict):
                        raise ValueError("data must be an object")
                    params["data"] = parsed
    return IntentAnalysisResult(intent_type=IntentType.READ, tool_name=name, parameters=params)


PROMPT = """
BÁO CÁO VÀ BÁO GIÁ: dùng reports.* để tổng hợp, không dùng danh sách odoo.*.search làm tổng.
Báo cáo tồn kho luôn dùng reports.create report_type=inventory; tra cứu một sản phẩm mới dùng odoo.products.search.
Khi người dùng yêu cầu gửi lại hoặc đổi định dạng báo cáo có sẵn, dùng reports.file với output_format nếu có yêu cầu; hỏi tiến độ dùng reports.status.
Chọn định dạng theo mục đích, không mặc định Excel cho tất cả:
- Người dùng chỉ định Word/docs/docx -> output_format=docx; Excel/xlsx -> xlsx; cả hai -> both. Ưu tiên chỉ định rõ ràng.
- Không chỉ định loại file -> output_format=auto, report_purpose theo mục đích thực sự của câu yêu cầu.
- Phân tích/nhận xét/tổng kết -> narrative; trình bày/gửi lãnh đạo -> presentation; bảng chi tiết để lọc/tính/đối chiếu -> data_analysis; cần văn bản và phụ lục dữ liệu -> combined.
- Không có mục đích cụ thể -> report_purpose=auto. Gateway chọn Word cho doanh thu/doanh số/công việc, Excel cho tồn kho.
- Không hỏi định dạng nếu có thể chọn từ ngữ cảnh. Chỉ hỏi khi yêu cầu mâu thuẫn. Chưa hỗ trợ PDF/PPT/Google Docs trực tuyến: giải thích và hỏi chọn Word/Excel, không tự đổi yêu cầu sang định dạng khác.
- Khi tiếp tục trả lời kỳ báo cáo còn thiếu, giữ yêu cầu định dạng/mục đích từ các lượt liên quan; không kế thừa định dạng báo cáo cũ nếu người dùng bắt đầu báo cáo mới.
- Không tuyên bố đã tạo file khi mới phân tích yêu cầu. Việc xuất và giao file do Gateway thực hiện.
Kỳ báo cáo phải thành date_from/date_to YYYY-MM-DD từ thời gian hiện tại đã cấp; không tự chọn kỳ nếu chưa có.
sales.quotation.draft chỉ là nháp cục bộ. data chứa partner_id, company_id, lines, validity_date,
payment_term_id,fiscal_position_id,client_order_ref,note,custom_fields. Dòng hàng gồm product_id,quantity,price_unit,price_confirmed,tax_ids,uom_id,custom_fields.
Trước chọn khách/sản phẩm mới phải dùng sales.quotation.lookup và chỉ lấy ID từ kết quả thật.
lines trong update cập nhật theo vị trí, add_line để thêm, remove_line với line_index từ 1.
Chỉ đặt price_confirmed=true khi người dùng đã cung cấp hoặc đồng ý đơn giá; không tự áp giá niêm yết.
tax_ids phải là các ID thuế người dùng đã chọn; chỉ dùng [] nếu người dùng xác nhận không thuế. Tra cứu taxes khi chưa rõ.
Đọc quotation_data trong active_draft và quotation_validation.missing_fields trong recent_read_records để hỏi/nhận đúng thông tin đang thiếu, không gửi vào draft email.
Một lượt chỉ gọi một công cụ. Sau lookup, trình bày kết quả để người dùng chọn; không tuyên bố đã cập nhật nháp.
Nếu người dùng muốn tạo sau khi đủ thông tin, dùng prepare_write sales.quotation.preview.
Không tự tạo hoặc sửa report_id, draft_id, version. Khi đổi khách hàng phải chọn lại giá và thuế cho các dòng.
"""

PROMPT += """
PHẠM VI ƯU TIÊN: Bán hàng, Mua hàng, CRM, Liên hệ, Tồn kho.
Số lượng báo giá bán: odoo.sales.search count_only=true document_type=quotations. Không đếm mọi sale.order thành báo giá.
Số đơn bán đã xác nhận: document_type=orders. Chỉ dùng all khi người dùng muốn mọi loại chứng từ, gồm hủy.
Mua hàng: odoo.purchases.search document_type=rfqs cho RFQ/chờduyệt; orders cho đơn mua đã xác nhận.
Liên hệ: odoo.partners.search contact_kind=all|companies|individuals|customers|suppliers đúng loại người dùng hỏi. Không coi mọi res.partner là khách hàng hoặc nhà cung cấp.
Của tôi -> record_scope=mine; mặc định visible nghĩa là phạm vi dữ liệu được quyền truy cập, không tuyên bố toàn bộ hệ thống.
Theo entity_label trong recent_read_records để trả lời số đếm nối tiếp; không đổi tên loại chứng từ.
File báo cáo quotations lọc draft/sent theo ngày tạo; purchases lọc đơn đã xác nhận theo ngày xác nhận; purchase_rfqs lọc draft/sent/chờduyệt theo ngày tạo.
CRM hiện trạng: reports.create report_type=crm; mặc định cơ hội hoạt động. crm_status=won dùng định nghĩa giai đoạn is_won thật, archived không đồng nghĩa cơ hội thua. Giá trị kỳ vọng không phải doanh số thực tế.
Báo cáo liên hệ: report_type=contacts, contact_kind đúng yêu cầu. CRM/liên hệ không có kỳ -> snapshot hiện tại; có kỳ -> ngày tạo.
Báo cáo tồn: inventory; báo cáo phiếu nhập/xuất/điều chuyển hoàn tất: transfers, transfer_type tương ứng, kỳ theo ngày hoàn tất.
Thiếu tên kho/sản phẩm/đối tác có nhiều kết quả -> reports.lookup để chọn đúng ID; không bịa ID.
Ngoài khả năng hoặc chỉ tiêu chưa định nghĩa -> hỏi làm rõ và nói rõ giới hạn, không dùng một danh sách mẫu làm báo cáo toàn bộ.
"""

PROMPT += """
Báo cáo mới mặc định reuse_previous=false: không mang theo kỳ, trạng thái archived, loại khách hoặc bộ lọc của báo cáo đã hoàn tất.
Chỉ đặt reuse_previous=true khi người dùng yêu cầu rõ ràng đổi/thêm bộ lọc của báo cáo vừa lấy, ví dụ 'đổi sang tháng trước', 'chỉ lấy nhà cung cấp'.
Đang trả lời câu hỏi bổ sung thông tin cho yêu cầu chưa tạo job thì giữ các tham số của yêu cầu đó.
CRM có thể lọc stage_id, lấy từ reports.lookup kind=stages; không đoán ID hay coi giai đoạn cuối cùng luôn là thắng.
"""
