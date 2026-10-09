from typing import Literal
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import Checkpoint, CheckpointMetadata, ChannelVersions
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END

from app.agent.state import AgentState
from app.agent.nodes.analyzer import analyzer_node
from app.agent.nodes.permission_guard import permission_guard_node
from app.agent.nodes.confirmation import confirmation_guard_node
from app.agent.nodes.executor import executor_node
from app.agent.nodes.formatter import formatter_node


class BoundedMemorySaver(MemorySaver):
    """MemorySaver giới hạn tối đa số lượng thread hội thoại (LRU capacity bound).

    Khi số lượng thread vượt quá max_threads, hệ thống sẽ tự động giải phóng các thread cũ nhất
    (bao gồm storage, writes và blobs), bảo vệ bộ nhớ RAM không bị tăng vô hạn khi có hàng ngàn
    phiên người dùng.
    """

    def __init__(self, max_threads: int = 100):
        super().__init__()
        self.max_threads = max_threads

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        res = super().put(config, checkpoint, metadata, new_versions)
        if len(self.storage) > self.max_threads:
            excess = len(self.storage) - self.max_threads
            for tid in list(self.storage.keys())[:excess]:
                self.delete_thread(tid)
        return res


def route_after_permission(state: AgentState) -> Literal["confirmation_guard", "executor", "formatter"]:
    """Điều hướng sau khi kiểm tra quyền (Layer 1 Guard):
    - Không có quyền -> Formatter (Báo tự động từ chối)
    - Có quyền & là Write Action -> Confirmation Guard (Hỏi người dùng xác nhận)
    - Có quyền & là Read Action -> Executor (Chạy Tool ngay)
    - Chat thông thường -> Formatter
    """
    if not state.get("permission_granted", True):
        return "formatter"

    if state.get("missing_slots"):
        return "formatter"

    if state.get("is_write_action", False):
        return "confirmation_guard"

    if state.get("pending_tool_name"):
        return "executor"

    return "formatter"


def route_after_confirmation(state: AgentState) -> Literal["executor", "formatter"]:
    """Điều hướng sau bước kiểm tra xác nhận từ người dùng:
    - Đã xác nhận đồng ý -> Executor (Thực thi Tool)
    - Chưa xác nhận hoặc đã bấm hủy -> Formatter (Dừng lại)
    """
    if state.get("user_confirmed") is True and state.get("pending_tool_name"):
        return "executor"

    return "formatter"


def build_agent_graph():
    """Xây dựng đồ thị trạng thái Agent đầy đủ với Layer 1 Guard và User Self-Confirmation."""
    workflow = StateGraph(AgentState)

    # 1. Thêm các Node
    workflow.add_node("analyzer", analyzer_node)
    workflow.add_node("permission_guard", permission_guard_node)
    workflow.add_node("confirmation_guard", confirmation_guard_node)
    workflow.add_node("executor", executor_node)
    workflow.add_node("formatter", formatter_node)

    # 2. Luồng kết nối (Edges)
    workflow.add_edge(START, "analyzer")
    workflow.add_edge("analyzer", "permission_guard")

    workflow.add_conditional_edges(
        "permission_guard",
        route_after_permission,
        {
            "confirmation_guard": "confirmation_guard",
            "executor": "executor",
            "formatter": "formatter",
        }
    )

    workflow.add_conditional_edges(
        "confirmation_guard",
        route_after_confirmation,
        {
            "executor": "executor",
            "formatter": "formatter",
        }
    )

    workflow.add_edge("executor", "formatter")
    workflow.add_edge("formatter", END)

    # 3. Checkpointer để lưu vết session với LRU bound
    checkpointer = BoundedMemorySaver(max_threads=100)

    return workflow.compile(checkpointer=checkpointer)


agent_runnable = build_agent_graph()
