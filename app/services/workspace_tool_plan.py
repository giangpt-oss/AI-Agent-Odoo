"""Run bounded Workspace tool plans with typed results and dependency checks."""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

from app.domain.intent_schema import visit_step_parameters

STEP_TIMEOUT = 30.0
TASK_TIMEOUT = 120.0
DELIVERY_RESERVE = 10.0
logger = logging.getLogger(__name__)


@dataclass
class StepResult:
    status: str
    messages: list[str] = field(default_factory=list)
    data: object = None


async def execute_plan(steps, invoke, deadline, *, writes=(), task_timeout=TASK_TIMEOUT):
    budget = min(task_timeout, deadline - time.time()) - DELIVERY_RESERVE
    loop = asyncio.get_running_loop()
    end = loop.time() + budget
    slots = asyncio.Semaphore(4)
    tasks = {}

    async def run(step):
        prerequisites = {key: await tasks[key] for key in step.depends_on}
        if any(item.status != "completed" for item in prerequisites.values()):
            return StepResult("skipped", ["Bước phụ thuộc chưa hoàn tất."])

        def resolve(source, path):
            value = prerequisites[source].data
            for key in path:
                if isinstance(value, dict) and isinstance(key, str):
                    value = value[key]
                elif isinstance(value, list) and isinstance(key, int):
                    if step.id in writes:
                        raise ValueError("Thao tác ghi yêu cầu kết quả phụ thuộc duy nhất.")
                    value = value[key]
                else:
                    raise ValueError("Kết quả phụ thuộc không đúng kiểu.")
            if type(value) not in (str, int, float, bool) or value == "":
                raise ValueError("Kết quả phụ thuộc thiếu giá trị.")
            return value

        try:
            parameters = visit_step_parameters(step.parameters, resolve)
        except (KeyError, IndexError, ValueError):
            return StepResult("skipped", ["Bước phụ thuộc thiếu dữ liệu cần thiết."])

        async with slots:
            remaining = min(STEP_TIMEOUT, end - loop.time())
            if remaining <= 0:
                return StepResult("timeout", ["Đã hết thời gian thực thi."])
            try:
                async with asyncio.timeout(remaining):
                    return await invoke(step, parameters, remaining)
            except TimeoutError:
                return StepResult("timeout", ["Bước vượt quá thời gian chờ."])
            except Exception as exc:
                logger.warning("Workspace tool-plan step failed: %s", type(exc).__name__)
                return StepResult("failed", ["Không thể hoàn tất bước này."])

    async with asyncio.TaskGroup() as group:
        for step in steps:
            tasks[step.id] = group.create_task(run(step))
    return {key: task.result() for key, task in tasks.items()}


ROUTES = {
    "drive": r"drive|tài liệu|tệp|file",
    "calendar": r"calendar|lịch|lich|cuộc họp|cuoc hop|sự kiện|su kien",
    "gmail": r"gmail|e-?mail|mail|thư|thu",
    "docs": r"docs|văn bản|van ban",
    "sheets": r"sheets|bảng tính|bang tinh|excel",
    "slides": r"slides|slide|trình chiếu|trinh chieu",
    "odoo": r"odoo|đối tác|doi tac|khách hàng|khach hang|khách|hóa đơn|hoa don|tồn kho|ton kho|sản phẩm|san pham|đơn hàng|don hang",
    "purchases": r"mua hàng|mua hang|đơn mua|don mua|rfq|nhà cung cấp|nha cung cap|p\d{3,}",
    "stock": r"giao hàng|giao hang|phiếu giao|phieu giao|phiếu kho|phieu kho|nhập kho|nhap kho|xuất kho|xuat kho|chuyển kho|chuyen kho|trả hàng|tra hang|wh/(?:out|in|int|pick|pack)/\d+",
    "reports": r"báo cáo|bao cao",
    "quotation": r"báo giá|bao gia",
    "crm": r"crm|cơ hội|co hoi|lead",
    "tasks": r"công việc|cong viec|việc cần làm|viec can lam|task",
    "briefing": r"hôm nay có gì|hom nay co gi|tổng hợp ngày|briefing",
}


def detect_domains(text):
    return {name for name, pattern in ROUTES.items()
            if re.search(rf"(?<!\w)(?:{pattern})(?!\w)", text.casefold())}


def select_tools(
    text,
    tools,
    *,
    expanded=False,
    normalized_text=None,
    domains=None,
    read_only=False,
):
    if read_only:
        tools = [tool for tool in tools if getattr(tool, "action_type", "read") == "read"]
    combined = f"{text or ''} {normalized_text or ''}".casefold()
    matched = detect_domains(combined)
    if domains:
        matched |= {str(d).casefold() for d in domains if d}
    words = set(re.findall(r"\w+", combined))

    def priority(tool):
        parts = set(tool.name.split("."))
        purchase_tool = "purchases" in matched and tool.name in {
            "business.lookup", "business.draft", "business.preview",
            "business.purchase_flow", "business.purchase_create", "business.purchase_update",
        }
        stock_tool = "stock" in matched and tool.name in {
            "business.lookup", "business.draft", "business.preview",
            "business.stock_flow", "business.stock_create",
        }
        return (purchase_tool or stock_tool or bool(matched & parts), len(words & parts), tool.name == "knowledge.query")

    ordered = sorted(tools, key=priority, reverse=True)
    limit = 12 if expanded or len(matched) >= 3 else 6

    # Khi khớp nhiều domain, phân bổ cân bằng giữa các domain để tránh 1 dịch vụ (vd calendar + workspace.calendar) chiếm hết slot
    if len(matched) >= 2 and len(ordered) > limit:
        per_domain_cap = max(2, limit // len(matched))
        picked = []
        picked_names = set()
        for dom in sorted(matched):
            dom_count = 0
            for tool in ordered:
                if tool.name in picked_names:
                    continue
                if dom in tool.name.split("."):
                    picked.append(tool)
                    picked_names.add(tool.name)
                    dom_count += 1
                    if dom_count >= per_domain_cap or len(picked) >= limit:
                        break
            if len(picked) >= limit:
                break
        for tool in ordered:
            if len(picked) >= limit:
                break
            if tool.name not in picked_names:
                picked.append(tool)
                picked_names.add(tool.name)
        return picked[:limit]

    return ordered[:limit]


def summarize_calendar_page(data):
    messages = ["Lịch trong khoảng đã yêu cầu"]
    items = data.get("items") or []
    for event in items[:20]:
        start = event.get("start") or {}
        when = start.get("dateTime") or start.get("date") or "Chưa rõ giờ"
        title = str(event.get("summary") or "Không tiêu đề")[:180]
        source = event.get("htmlLink")
        suffix = f" · {source}" if isinstance(source, str) and source.startswith("https://www.google.com/calendar/") else ""
        messages.append(f"• {when} · {title}{suffix}")
    if not items:
        messages.append("Không có sự kiện trong trang kết quả này.")
    if len(items) > 20 or data.get("nextPageToken"):
        messages.append("Danh sách này chỉ hiển thị một phần sự kiện trong khoảng đã yêu cầu.")
    return messages
