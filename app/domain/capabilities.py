from enum import Enum
from typing import Dict, List, Set
from pydantic import BaseModel, Field


class BusinessCapability(str, Enum):
    # Sales Capabilities
    CUSTOMER_INTELLIGENCE = "CUSTOMER_INTELLIGENCE"
    LEAD_MANAGEMENT = "LEAD_MANAGEMENT"
    OPPORTUNITY_MANAGEMENT = "OPPORTUNITY_MANAGEMENT"
    PIPELINE_ANALYSIS = "PIPELINE_ANALYSIS"
    QUOTATION_MANAGEMENT = "QUOTATION_MANAGEMENT"
    SALES_REPORTING = "SALES_REPORTING"
    SALES_FORECASTING = "SALES_FORECASTING"
    CUSTOMER_FOLLOW_UP = "CUSTOMER_FOLLOW_UP"

    # HR Capabilities
    EMPLOYEE_INFORMATION = "EMPLOYEE_INFORMATION"
    RECRUITMENT_ASSISTANCE = "RECRUITMENT_ASSISTANCE"
    ONBOARDING = "ONBOARDING"
    LEAVE_AND_ATTENDANCE = "LEAVE_AND_ATTENDANCE"
    HR_DOCUMENT_DRAFTING = "HR_DOCUMENT_DRAFTING"
    WORKFORCE_REPORTING = "WORKFORCE_REPORTING"
    EMPLOYEE_ADMINISTRATION = "EMPLOYEE_ADMINISTRATION"

    # Accounting Capabilities
    INVOICE_MANAGEMENT = "INVOICE_MANAGEMENT"
    ACCOUNTS_RECEIVABLE = "ACCOUNTS_RECEIVABLE"
    ACCOUNTS_PAYABLE = "ACCOUNTS_PAYABLE"
    RECONCILIATION_SUPPORT = "RECONCILIATION_SUPPORT"
    FINANCIAL_REPORTING = "FINANCIAL_REPORTING"
    PAYMENT_MONITORING = "PAYMENT_MONITORING"
    BUDGET_ANALYSIS = "BUDGET_ANALYSIS"

    # Shared Cross-Role Capabilities
    EMAIL_ASSISTANCE = "EMAIL_ASSISTANCE"
    CALENDAR_ASSISTANCE = "CALENDAR_ASSISTANCE"
    MEETING_PREPARATION = "MEETING_PREPARATION"
    TASK_MANAGEMENT = "TASK_MANAGEMENT"
    DOCUMENT_PROCESSING = "DOCUMENT_PROCESSING"
    KNOWLEDGE_SEARCH = "KNOWLEDGE_SEARCH"


class CapabilityDefinition(BaseModel):
    name: BusinessCapability
    title: str
    description: str
    required_permissions: List[str] = Field(default_factory=list)
    associated_skills: List[str] = Field(default_factory=list)


CAPABILITY_REGISTRY: Dict[BusinessCapability, CapabilityDefinition] = {
    # Sales
    BusinessCapability.CUSTOMER_INTELLIGENCE: CapabilityDefinition(
        name=BusinessCapability.CUSTOMER_INTELLIGENCE,
        title="Thông tin & Thấu hiểu khách hàng",
        description="Tra cứu lịch sử tương tác, phân loại khách hàng và thông tin liên hệ.",
        required_permissions=["READ_ODOO_CRM"],
        associated_skills=["get_partners_and_customers", "get_crm_pipeline"]
    ),
    BusinessCapability.LEAD_MANAGEMENT: CapabilityDefinition(
        name=BusinessCapability.LEAD_MANAGEMENT,
        title="Quản lý khách hàng tiềm năng (Leads)",
        description="Theo dõi leads mới, phát hiện leads chưa được chăm sóc quá hạn.",
        required_permissions=["READ_ODOO_CRM"],
        associated_skills=["get_crm_pipeline", "update_task"]
    ),
    BusinessCapability.OPPORTUNITY_MANAGEMENT: CapabilityDefinition(
        name=BusinessCapability.OPPORTUNITY_MANAGEMENT,
        title="Quản lý cơ hội kinh doanh",
        description="Theo dõi tiến độ, xác suất và doanh thu dự kiến của các cơ hội.",
        required_permissions=["READ_ODOO_CRM"],
        associated_skills=["get_crm_pipeline"]
    ),
    BusinessCapability.PIPELINE_ANALYSIS: CapabilityDefinition(
        name=BusinessCapability.PIPELINE_ANALYSIS,
        title="Phân tích Pipeline bán hàng",
        description="Thống kê tỷ lệ chuyển đổi, nút thắt trong phễu bán hàng.",
        required_permissions=["READ_ODOO_CRM"],
        associated_skills=["get_crm_pipeline", "export_data_to_excel"]
    ),
    BusinessCapability.QUOTATION_MANAGEMENT: CapabilityDefinition(
        name=BusinessCapability.QUOTATION_MANAGEMENT,
        title="Quản lý báo giá & Hợp đồng",
        description="Kiểm tra báo giá sắp hết hạn, chuẩn bị dự thảo báo giá.",
        required_permissions=["READ_ODOO_CRM"],
        associated_skills=["get_crm_pipeline", "create_docx"]
    ),
    BusinessCapability.SALES_REPORTING: CapabilityDefinition(
        name=BusinessCapability.SALES_REPORTING,
        title="Báo cáo doanh số & Hiệu suất bán hàng",
        description="Tổng hợp doanh thu theo nhân viên, nhóm sản phẩm, thời gian.",
        required_permissions=["READ_ODOO_CRM"],
        associated_skills=["analyze_spreadsheet", "export_data_to_excel"]
    ),
    BusinessCapability.SALES_FORECASTING: CapabilityDefinition(
        name=BusinessCapability.SALES_FORECASTING,
        title="Dự báo doanh số (Sales Forecasting)",
        description="Dự báo doanh thu kỳ tới dựa trên weighted probability của pipeline.",
        required_permissions=["READ_ODOO_CRM"],
        associated_skills=["analyze_spreadsheet", "export_data_to_excel"]
    ),
    BusinessCapability.CUSTOMER_FOLLOW_UP: CapabilityDefinition(
        name=BusinessCapability.CUSTOMER_FOLLOW_UP,
        title="Chăm sóc & Theo dõi khách hàng",
        description="Phát hiện khách hàng cần follow-up, soạn thảo email chăm sóc định kỳ.",
        required_permissions=["READ_ODOO_CRM", "CREATE_EMAIL_DRAFT"],
        associated_skills=["get_partners_and_customers", "draft_email", "create_task", "create_calendar_event"]
    ),

    # HR
    BusinessCapability.EMPLOYEE_INFORMATION: CapabilityDefinition(
        name=BusinessCapability.EMPLOYEE_INFORMATION,
        title="Thông tin nhân sự & Tổ chức",
        description="Tra cứu danh sách nhân sự, chức danh, phòng ban và cơ cấu tổ chức.",
        required_permissions=["READ_ODOO_HR"],
        associated_skills=["get_company_employees"]
    ),
    BusinessCapability.RECRUITMENT_ASSISTANCE: CapabilityDefinition(
        name=BusinessCapability.RECRUITMENT_ASSISTANCE,
        title="Hỗ trợ tuyển dụng & Soạn thảo JD",
        description="Soạn bản mô tả công việc (JD), tiêu chí tuyển dụng và email mời phỏng vấn.",
        required_permissions=["READ_ODOO_HR"],
        associated_skills=["create_docx", "draft_email"]
    ),
    BusinessCapability.ONBOARDING: CapabilityDefinition(
        name=BusinessCapability.ONBOARDING,
        title="Tiếp nhận nhân sự mới (Onboarding)",
        description="Lập checklist nhiệm vụ onboarding, chuẩn bị tài liệu nội bộ.",
        required_permissions=["READ_ODOO_HR", "WRITE_TASKS"],
        associated_skills=["bulk_create_tasks", "create_docx"]
    ),
    BusinessCapability.LEAVE_AND_ATTENDANCE: CapabilityDefinition(
        name=BusinessCapability.LEAVE_AND_ATTENDANCE,
        title="Phân tích nghỉ phép & Chấm công",
        description="Theo dõi tình hình vắng mặt, phép năm và tỷ lệ đi làm.",
        required_permissions=["READ_ODOO_HR"],
        associated_skills=["get_company_employees", "analyze_spreadsheet"]
    ),
    BusinessCapability.HR_DOCUMENT_DRAFTING: CapabilityDefinition(
        name=BusinessCapability.HR_DOCUMENT_DRAFTING,
        title="Soạn thảo văn bản & Thông báo nhân sự",
        description="Soạn thông báo nội bộ, quyết định, quy chế công ty.",
        required_permissions=["READ_ODOO_HR", "WRITE_FILES"],
        associated_skills=["create_docx", "draft_email"]
    ),
    BusinessCapability.WORKFORCE_REPORTING: CapabilityDefinition(
        name=BusinessCapability.WORKFORCE_REPORTING,
        title="Báo cáo quy mô & Biến động nhân sự",
        description="Thống kê tỷ lệ nhân sự theo phòng ban, biến động tăng giảm.",
        required_permissions=["READ_ODOO_HR"],
        associated_skills=["get_company_employees", "export_data_to_excel"]
    ),
    BusinessCapability.EMPLOYEE_ADMINISTRATION: CapabilityDefinition(
        name=BusinessCapability.EMPLOYEE_ADMINISTRATION,
        title="Quản trị hành chính nhân sự",
        description="Quản lý lịch làm việc, sinh nhật nhân viên, hoạt động gắn kết.",
        required_permissions=["READ_ODOO_HR"],
        associated_skills=["get_company_employees", "create_calendar_event"]
    ),

    # Accounting
    BusinessCapability.INVOICE_MANAGEMENT: CapabilityDefinition(
        name=BusinessCapability.INVOICE_MANAGEMENT,
        title="Quản lý hóa đơn mua / bán",
        description="Theo dõi trạng thái hóa đơn, hạn thanh toán và đối soát.",
        required_permissions=["READ_FILES"],
        associated_skills=["read_spreadsheet", "analyze_spreadsheet"]
    ),
    BusinessCapability.ACCOUNTS_RECEIVABLE: CapabilityDefinition(
        name=BusinessCapability.ACCOUNTS_RECEIVABLE,
        title="Quản lý công nợ phải thu (AR)",
        description="Theo dõi khách nợ quá hạn, chuẩn bị thư nhắc nợ.",
        required_permissions=["READ_FILES", "CREATE_EMAIL_DRAFT"],
        associated_skills=["analyze_spreadsheet", "draft_email"]
    ),
    BusinessCapability.ACCOUNTS_PAYABLE: CapabilityDefinition(
        name=BusinessCapability.ACCOUNTS_PAYABLE,
        title="Quản lý công nợ phải trả (AP)",
        description="Lịch thanh toán nhà cung cấp, kiểm tra hóa đơn đến hạn.",
        required_permissions=["READ_FILES"],
        associated_skills=["analyze_spreadsheet", "create_calendar_event"]
    ),
    BusinessCapability.RECONCILIATION_SUPPORT: CapabilityDefinition(
        name=BusinessCapability.RECONCILIATION_SUPPORT,
        title="Hỗ trợ đối soát tài chính",
        description="Đối chiếu số liệu sổ phụ ngân hàng và bảng kê kế toán.",
        required_permissions=["READ_FILES"],
        associated_skills=["read_spreadsheet", "analyze_spreadsheet"]
    ),
    BusinessCapability.FINANCIAL_REPORTING: CapabilityDefinition(
        name=BusinessCapability.FINANCIAL_REPORTING,
        title="Báo cáo tài chính & Dòng tiền",
        description="Phân tích doanh thu, chi phí, dòng tiền tổng hợp theo kỳ.",
        required_permissions=["READ_FILES"],
        associated_skills=["analyze_spreadsheet", "export_data_to_excel"]
    ),
    BusinessCapability.PAYMENT_MONITORING: CapabilityDefinition(
        name=BusinessCapability.PAYMENT_MONITORING,
        title="Giám sát thanh toán & Dòng tiền",
        description="Theo dõi các giao dịch thanh toán trong ngày, cảnh báo chậm trả.",
        required_permissions=["READ_FILES"],
        associated_skills=["analyze_spreadsheet"]
    ),
    BusinessCapability.BUDGET_ANALYSIS: CapabilityDefinition(
        name=BusinessCapability.BUDGET_ANALYSIS,
        title="Phân tích ngân sách các phòng ban",
        description="So sánh chi phí thực tế so với ngân sách kế hoạch.",
        required_permissions=["READ_FILES"],
        associated_skills=["analyze_spreadsheet", "export_data_to_excel"]
    ),

    # Shared
    BusinessCapability.EMAIL_ASSISTANCE: CapabilityDefinition(
        name=BusinessCapability.EMAIL_ASSISTANCE,
        title="Trợ lý Email công vụ",
        description="Tìm kiếm, đọc, soạn thảo và gửi email công việc an toàn.",
        required_permissions=["READ_EMAIL", "CREATE_EMAIL_DRAFT"],
        associated_skills=["search_email", "read_email", "draft_email", "send_email", "reply_email"]
    ),
    BusinessCapability.CALENDAR_ASSISTANCE: CapabilityDefinition(
        name=BusinessCapability.CALENDAR_ASSISTANCE,
        title="Trợ lý Lịch & Thời gian",
        description="Kiểm tra lịch trống, sắp xếp cuộc hẹn, quản lý sự kiện.",
        required_permissions=["READ_CALENDAR"],
        associated_skills=["list_calendar_events", "find_free_time", "create_calendar_event"]
    ),
    BusinessCapability.MEETING_PREPARATION: CapabilityDefinition(
        name=BusinessCapability.MEETING_PREPARATION,
        title="Chuẩn bị & Xử lý cuộc họp",
        description="Chuẩn bị tài liệu trước họp, trích xuất biên bản họp và action items.",
        required_permissions=["READ_MEETINGS", "WRITE_MEETINGS"],
        associated_skills=["prepare_meeting", "process_meeting_notes", "generate_meeting_minutes"]
    ),
    BusinessCapability.TASK_MANAGEMENT: CapabilityDefinition(
        name=BusinessCapability.TASK_MANAGEMENT,
        title="Quản lý công việc & Deadline",
        description="Theo dõi công việc cá nhân, tạo task, nhắc việc tự động.",
        required_permissions=["READ_TASKS", "WRITE_TASKS"],
        associated_skills=["list_tasks", "create_task", "update_task", "complete_task"]
    ),
    BusinessCapability.DOCUMENT_PROCESSING: CapabilityDefinition(
        name=BusinessCapability.DOCUMENT_PROCESSING,
        title="Xử lý tài liệu văn phòng",
        description="Đọc, trích xuất, chỉnh sửa PDF, DOCX, XLSX, PPTX.",
        required_permissions=["READ_FILES", "WRITE_FILES"],
        associated_skills=["read_pdf", "read_docx", "read_spreadsheet", "create_docx", "export_data_to_excel"]
    ),
    BusinessCapability.KNOWLEDGE_SEARCH: CapabilityDefinition(
        name=BusinessCapability.KNOWLEDGE_SEARCH,
        title="Tra cứu cơ sở tri thức công ty (RAG)",
        description="Tìm kiếm thông tin chính sách, tài liệu nội bộ có trích dẫn nguồn.",
        required_permissions=["READ_KNOWLEDGE"],
        associated_skills=["semantic_search", "knowledge_answer"]
    ),
}
