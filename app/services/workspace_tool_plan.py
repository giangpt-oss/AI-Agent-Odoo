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
    "drive": r"drive|tài liệu|tệp",
    "calendar": r"calendar|lịch|cuộc họp|sự kiện",
    "gmail": r"gmail|e-?mail|thư",
    "docs": r"docs|văn bản",
    "sheets": r"sheets|bảng tính",
    "slides": r"slides|slide|trình chiếu",
    "odoo": r"odoo|đối tác|khách hàng|hóa đơn|tồn kho|sản phẩm|đơn hàng",
    "reports": r"báo cáo",
    "quotation": r"báo giá",
    "crm": r"crm|cơ hội",
    "tasks": r"công việc|việc cần làm",
}


def select_tools(text, tools, *, expanded=False):
    matched = {name for name, pattern in ROUTES.items()
               if re.search(rf"(?<!\w)(?:{pattern})(?!\w)", text.casefold())}
    words = set(re.findall(r"\w+", text.casefold()))

    def priority(tool):
        parts = set(tool.name.split("."))
        return (bool(matched & parts), len(words & parts), tool.name == "knowledge.query")

    ordered = sorted(tools, key=priority, reverse=True)
    limit = 12 if expanded or len(matched) >= 3 else 6
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
