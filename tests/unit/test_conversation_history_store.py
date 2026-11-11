import time
import pytest
from app.services.conversation_history_store import ConversationHistoryStore


@pytest.fixture
def store(tmp_path):
    db_file = tmp_path / "test_history.db"
    return ConversationHistoryStore(db_path=str(db_file), ttl_seconds=1800, max_turns=20)


def test_session_creation_and_append_turn(store):
    store.append_turn(
        tenant="https://odoo.local",
        user_id=1,
        chat_id=777,
        binding_id="bind_1",
        role="user",
        content="Xin chào",
    )
    store.append_turn(
        tenant="https://odoo.local",
        user_id=1,
        chat_id=777,
        binding_id="bind_1",
        role="assistant",
        content="Chào bạn! Tôi có thể giúp gì?",
    )

    session = store.get_session("https://odoo.local", 1, 777, "bind_1")
    assert session is not None
    assert len(session["history"]) == 2
    assert session["history"][0]["role"] == "user"
    assert session["history"][0]["content"] == "Xin chào"
    assert session["history"][1]["role"] == "assistant"


def test_session_binding_change_resets_context(store):
    store.append_turn("https://odoo.local", 1, 777, "bind_1", "user", "Câu hỏi cũ")
    session1 = store.get_session("https://odoo.local", 1, 777, "bind_1")
    assert len(session1["history"]) == 1

    # Đổi binding_id (ví dụ người dùng liên kết lại bot) -> session cũ không còn hiệu lực
    session2 = store.get_session("https://odoo.local", 1, 777, "bind_2")
    assert len(session2["history"]) == 0


def test_session_last_read_data(store):
    records = [{"id": 101, "name": "Họp kế hoạch Q4"}, {"id": 102, "name": "Họp ban giám đốc"}]
    store.set_last_read_data("https://odoo.local", 1, 777, "bind_1", "calendar", records)

    data = store.get_last_read_data("https://odoo.local", 1, 777, "bind_1", "calendar")
    assert len(data) == 2
    assert data[0]["name"] == "Họp kế hoạch Q4"


def test_session_active_draft_management(store):
    draft = {"type": "email", "recipient": "nam@example.com", "subject": "Báo cáo", "body": "Nội dung v1"}
    store.set_active_draft("https://odoo.local", 1, 777, "bind_1", draft)

    retrieved = store.get_active_draft("https://odoo.local", 1, 777, "bind_1")
    assert retrieved is not None
    assert retrieved["body"] == "Nội dung v1"

    # Cập nhật draft
    draft["body"] = "Nội dung v2 ngắn gọn hơn"
    store.set_active_draft("https://odoo.local", 1, 777, "bind_1", draft)
    assert store.get_active_draft("https://odoo.local", 1, 777, "bind_1")["body"] == "Nội dung v2 ngắn gọn hơn"

    # Xóa draft
    store.clear_active_draft("https://odoo.local", 1, 777, "bind_1")
    assert store.get_active_draft("https://odoo.local", 1, 777, "bind_1") is None


def test_session_max_turns_truncation(store):
    # Thêm 25 lượt -> chỉ giữ 20 lượt gần nhất
    for i in range(25):
        store.append_turn("https://odoo.local", 1, 777, "bind_1", "user", f"Message {i}")

    session = store.get_session("https://odoo.local", 1, 777, "bind_1")
    assert len(session["history"]) == 20
    assert session["history"][0]["content"] == "Message 5"
    assert session["history"][-1]["content"] == "Message 24"
