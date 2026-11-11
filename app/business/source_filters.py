"""Business distinctions for read queries; labels reflect the actual query scope."""


def document_filter(kind, params, context):
    state = str(params.get("state") or "").strip()
    document_type = params.get("document_type", "all")
    definitions = {
        "sales": {
            "all": (None, "bản ghi bán hàng và báo giá, gồm cả đã hủy nếu có"),
            "quotations": (["draft", "sent"], "báo giá bán chưa xác nhận (nháp hoặc đã gửi)"),
            "orders": (["sale", "done"], "đơn bán đã xác nhận"),
        },
        "purchases": {
            "all": (None, "bản ghi mua hàng và yêu cầu báo giá mua, gồm cả đã hủy nếu có"),
            "rfqs": (["draft", "sent", "to approve"], "yêu cầu báo giá mua (gồm cả chờ duyệt)"),
            "orders": (["purchase", "done"], "đơn mua đã xác nhận"),
        },
    }
    if document_type not in definitions[kind]:
        raise ValueError("Loại chứng từ không phù hợp với phân hệ.")
    states, label = definitions[kind][document_type]
    valid = {"draft", "sent", "sale", "done", "cancel"} if kind == "sales" else {"draft", "sent", "to approve", "purchase", "done", "cancel"}
    if state and (state not in valid or states and state not in states):
        raise ValueError("Trạng thái không khớp loại chứng từ; hãy làm rõ phạm vi cần đếm.")
    domain = []
    if state:
        domain.append(("state", "=", state))
        label += f" [trạng thái: {state}]"
    elif states:
        domain.append(("state", "in", states))
    scope = params.get("record_scope", "visible")
    if scope not in {"visible", "mine"}:
        raise ValueError("Phạm vi bản ghi không hợp lệ.")
    if scope == "mine":
        if context.odoo_uid is None:
            raise ValueError("Chưa xác minh được người dùng Odoo để lọc dữ liệu của bạn.")
        domain.append(("user_id", "=", context.odoo_uid))
    label += " do bạn phụ trách" if scope == "mine" else " trong phạm vi quyền tài khoản"
    return domain, label


def contact_filter(params, context):
    kind = params.get("contact_kind", "all")
    options = {
        "all": ([], "liên hệ đang hoạt động"),
        "companies": ([("is_company", "=", True)], "liên hệ tổ chức đang hoạt động"),
        "individuals": ([("is_company", "=", False)], "liên hệ cá nhân đang hoạt động"),
        "customers": ([("customer_rank", ">", 0)], "liên hệ được ghi nhận là khách hàng"),
        "suppliers": ([("supplier_rank", ">", 0)], "liên hệ được ghi nhận là nhà cung cấp"),
    }
    if kind not in options:
        raise ValueError("Loại liên hệ không hợp lệ.")
    domain, label = options[kind]
    domain = list(domain)
    scope = params.get("record_scope", "visible")
    if scope == "mine":
        if context.odoo_uid is None:
            raise ValueError("Chưa xác minh được người phụ trách liên hệ.")
        domain.append(("user_id", "=", context.odoo_uid))
        label += " do bạn phụ trách"
    elif scope != "visible":
        raise ValueError("Phạm vi bản ghi không hợp lệ.")
    return domain, label + " trong phạm vi quyền tài khoản"


def count_message(total, label, query=""):
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        return "Chưa lấy được số đếm đầy đủ từ Odoo; không dùng số dòng của một trang làm tổng."
    suffix = f", khớp với {query!r}" if query else ""
    return f"Có {total:,} {label}{suffix}."


def document_rows(items, label, total):
    if not items:
        return "Không tìm thấy " + label + "."
    lines = ["Phạm vi: " + label + ".", f"Hiển thị {len(items)} bản ghi; tổng: {total if isinstance(total, int) else 'chưa xác định'}."]
    state_labels = {"draft": "Nháp", "sent": "Đã gửi", "sale": "Đã xác nhận bán", "purchase": "Đã xác nhận mua", "done": "Hoàn tất", "cancel": "Đã hủy", "to approve": "Chờ duyệt"}
    for row in items:
        partner = row.get("partner_id")
        partner = partner[1] if isinstance(partner, (list, tuple)) and len(partner) > 1 else "Chưa xác định"
        currency = row.get("currency_id")
        currency = currency[1] if isinstance(currency, (list, tuple)) and len(currency) > 1 else "chưa xác định tiền tệ"
        amount = row.get("amount_total")
        amount = f"{amount:,.2f}" if isinstance(amount, (int, float)) else "chưa xác định"
        lines.append(f"• {row.get('name')} (ID {row.get('id')}): {partner}; tổng {amount} {currency}; {state_labels.get(row.get('state'), row.get('state'))}.")
    return "\n".join(lines)
