import pytest
from app.api.v1.internal import format_task_list
from app.providers.tasks.local import LocalTaskProvider


@pytest.mark.parametrize(
    "raw_status,expected_display",
    [
        ("TODO", "📝 Chưa bắt đầu"),
        ("todo", "📝 Chưa bắt đầu"),
        ("  TODO  ", "📝 Chưa bắt đầu"),
        ("IN_PROGRESS", "🔄 Đang thực hiện"),
        ("in_progress", "🔄 Đang thực hiện"),
        ("  in_progress  ", "🔄 Đang thực hiện"),
        ("WAITING", "⏳ Đang chờ"),
        ("waiting", "⏳ Đang chờ"),
        ("DONE", "✅ Hoàn thành"),
        ("done", "✅ Hoàn thành"),
        ("COMPLETED", "✅ Hoàn thành"),
        ("completed", "✅ Hoàn thành"),
        ("CANCELLED", "🚫 Đã hủy"),
        ("cancelled", "🚫 Đã hủy"),
        ("UNKNOWN_STATUS", "❔ Không rõ trạng thái"),
        ("weird", "❔ Không rõ trạng thái"),
        (None, "❔ Không rõ trạng thái"),
        ("", "❔ Không rõ trạng thái"),
        ("   ", "❔ Không rõ trạng thái"),
    ],
)
def test_format_task_list_status_mapping(raw_status, expected_display):
    tasks = [
        {
            "id": "t1",
            "title": "Báo cáo tiến độ",
            "status": raw_status,
            "priority": "high",
            "due_at": "2026-10-30",
        }
    ]
    result = format_task_list(tasks)
    assert expected_display in result
    assert "Báo cáo tiến độ" in result
    assert "[HIGH]" in result
    assert "Hạn: 2026-10-30" in result
    assert "📋 **Danh sách công việc cá nhân (1 việc):**" in result


def test_format_task_list_empty():
    result = format_task_list([])
    assert "Hiện tại bạn không có công việc nào trong danh sách." in result


def test_format_task_list_preserves_order_and_items():
    tasks = [
        {"id": "t1", "title": "Việc 1", "status": "TODO", "priority": "low", "due_at": None},
        {"id": "t2", "title": "Việc 2", "status": "DONE", "priority": "medium", "due_at": "Hôm nay"},
        {"id": "t3", "title": "Việc 3", "status": "CANCELLED", "priority": "urgent", "due_at": "Ngày mai"},
    ]
    result = format_task_list(tasks)
    assert "📋 **Danh sách công việc cá nhân (3 việc):**" in result
    assert "1. " in result and "Việc 1" in result and "📝 Chưa bắt đầu" in result
    assert "2. " in result and "Việc 2" in result and "✅ Hoàn thành" in result
    assert "3. " in result and "Việc 3" in result and "🚫 Đã hủy" in result
    # Verify cancelled task does NOT display waiting icon ⏳
    lines = result.split("\n")
    line_cancelled = next(l for l in lines if "Việc 3" in l)
    assert "⏳" not in line_cancelled


@pytest.mark.asyncio
async def test_provider_to_formatter_completed_done(tmp_path):
    """Integration: create task in provider, complete with complete_task,
    verify formatter receives status 'DONE' and renders ✅ Hoàn thành.
    """
    provider = LocalTaskProvider()
    provider.db_path = tmp_path / "test_tasks.db"
    provider._init_db()

    owner = "https://erp.test|db1|99"

    created = await provider.create_task(
        title="Nhiệm vụ kiểm tra hoàn thành",
        description="Mô tả",
        priority="high",
        due_at="2026-10-27",
        timezone="Asia/Bangkok",
        project=None,
        tags=["test"],
        owner_key=owner,
    )
    assert created["status"] == "TODO"

    # Complete the task
    completed = await provider.complete_task(created["id"], owner_key=owner)
    assert completed["status"] == "DONE"

    # List tasks and format
    tasks = await provider.list_tasks(filters={}, owner_key=owner)
    formatted = format_task_list(tasks)

    assert "✅ Hoàn thành" in formatted
    assert "Nhiệm vụ kiểm tra hoàn thành" in formatted
    assert "⏳" not in formatted
