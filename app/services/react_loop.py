"""Bounded ReAct Agent Loop: Perception -> Reasoning -> Planning -> Action -> Observation.

Enforces the 5-layer Agent Checklist programmatically:
1. Reasoning - Memory Check: track past failed/empty calls and forbid repeating failed paths.
2. Reasoning - Context Pruning: compress stale/noise observations while keeping relevant evidence.
3. Planning - Tool Schema Check, Human-in-the-loop gate, and 3-failure Loop Breaker.
4. Observation - Error Handling: root-cause classification before re-entering Perception.
5. Observation - Self-Reflection & Anti-Hallucination: verify claims/entities against observed evidence.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Awaitable, Callable, Literal, Optional

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.domain.gateway_context import GatewayUserContext
from app.domain.intent_schema import IntentAnalysisResult, IntentType
from app.services.confirmation_service import ConfirmationPreview, OptionButtonsMessage
from app.services.llm_client import generate_json

logger = logging.getLogger(__name__)

_SKIP_REACT_TOOLS = frozenset({
    "identity.whoami",
    "connections.status",
    "briefing.today",
    "knowledge.query",
    "knowledge.documents.list",
    "knowledge.documents.status",
    "workspace.catalog",
    "business.purchase_create",
    "business.purchase_flow",
    "business.purchase_update",
    "business.purchase_approval_button",
    "business.stock_create",
    "business.stock_flow",
    "business.quotation_flow",
    "business.quotation_chatter",
    "business.draft",
    "business.preview",
})

_COMPOUND_CUE_RE = re.compile(
    r"\b(?:rồi|sau đó|tiếp theo|tiếp tục|kèm theo|đồng thời|và\s+(?:xem|tìm|kiểm tra|gửi|soạn|tạo|lập|cập nhật))\b",
    re.I,
)
_EMPTY_OR_ERROR_RE = re.compile(
    r"không tìm thấy|chưa có|không có dữ liệu|không có kết quả|0 kết quả|⚠️|❌|chưa xác định|thất bại|lỗi|không hợp lệ|thiếu quyền",
    re.I,
)
_HIGH_RISK_WRITE_HINTS = (
    "delete", "unlink", "cancel", "send", "confirm", "approve", "reject",
    "validate", "create", "update", "write", "archive", "won", "lost", "convert",
)


class ReactToolCall(BaseModel):
    tool_name: str = Field(min_length=1, max_length=100)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ReactStepDecision(BaseModel):
    thought: str = Field(
        default="",
        description=(
            "Memory Check & Reasoning: đã đối chiếu lỗi/lần thử trước gì, đã lọc bỏ nhiễu gì, "
            "đã có dữ liệu thật gì và mục tiêu bước tiếp theo là gì."
        ),
    )
    decision: Literal["call_tools", "prepare_write", "reply", "clarify"] = Field(
        description=(
            "call_tools: gọi 1-3 công cụ ĐỌC đúng schema để tra cứu/kiểm chứng thêm; "
            "prepare_write: tạm dừng vòng lặp để xin xác nhận người dùng trước khi ghi/gửi/xóa/thay đổi dữ liệu; "
            "reply: kết thúc khi 100% thông tin dựa trên Observation và đã giải quyết trọn vẹn mục tiêu; "
            "clarify: hỏi lại người dùng khi thiếu dữ liệu hoặc có nhiều lựa chọn."
        )
    )
    tool_calls: list[ReactToolCall] = Field(default_factory=list, max_length=3)
    write_tool_name: Optional[str] = None
    write_parameters: dict[str, Any] = Field(default_factory=dict)
    grounded_check: bool = Field(
        default=True,
        description="Tự kiểm chứng: True chỉ khi mọi số liệu, mã chứng từ, email, tên riêng trong reply_text đều có trong Perception/Observation.",
    )
    reflection_note: Optional[str] = Field(
        default=None,
        description="Tự phản chiếu ngắn gọn trước khi trả lời: đã đáp ứng đủ mục tiêu gốc chưa?",
    )
    reply_text: Optional[str] = None
    clarify_prompt: Optional[str] = None
    clarify_options: list[str] = Field(default_factory=list, max_length=4)


def _canonical_call_key(tool_name: str, parameters: dict[str, Any]) -> str:
    return f"{tool_name}:{json.dumps(parameters or {}, sort_keys=True, ensure_ascii=False, default=str)}"


def _strip_json_fences(raw: str) -> str:
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _extract_observation_text(messages: list[Any]) -> str:
    parts = [str(m) for m in (messages or []) if m is not None and str(m).strip()]
    return "\n".join(parts)[:3500]


def _looks_like_raw_json(text: str) -> bool:
    s = (text or "").strip()
    return (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]"))


def _diagnose_observation_error(obs_text: str) -> tuple[bool, str, str]:
    """Observation Error Handling: classify failed/empty observations and identify root cause."""
    text = (obs_text or "").strip()
    if not text:
        return True, "KHÔNG_CÓ_DỮ_LIỆU", "Công cụ trả về kết quả rỗng. Hãy thử từ khóa ngắn/rộng hơn hoặc công cụ khác."

    lower = text.casefold()
    if any(tok in lower for tok in ("chưa được cấp quyền", "thiếu quyền", "permission_denied", "không có quyền")):
        return True, "THIẾU_QUYỀN", "Thiếu quyền truy cập; không lặp lại công cụ này, hãy giải thích giới hạn quyền cho người dùng."
    if any(tok in lower for tok in ("chưa được kết nối", "liên kết lại", "not_connected", "reconnect_required")):
        return True, "CHƯA_KẾT_NỐI", "Dịch vụ chưa liên kết; hướng dẫn người dùng kết nối trên cổng Gateway."
    if any(tok in lower for tok in ("không hợp lệ", "sai tham số", "thiếu tham số", "schema", "validation", "bắt buộc")):
        return True, "SAI_THAM_SỐ", "Tham số hoặc kiểu dữ liệu chưa đúng schema; kiểm tra lại Tool Schema trước khi gọi."
    if any(tok in lower for tok in ("quá thời gian", "timeout", "mất kết nối")):
        return True, "LỖI_KẾT_NỐI_TIMEOUT", "Dịch vụ phản hồi chậm/timeout; thử thu hẹp phạm vi truy vấn hoặc báo trạng thái cho người dùng."
    if any(tok in lower for tok in ("không tìm thấy", "không có dữ liệu", "không có kết quả", "0 kết quả", "chưa có bản ghi")):
        return True, "KHÔNG_CÓ_DỮ_LIỆU", "Không khớp dữ liệu với từ khóa hiện tại; thử rút gọn/đổi từ khóa hoặc bỏ hậu tố."
    if "⚠️" in text or "❌" in text or re.search(r"\blỗi\b|\bthất bại\b", lower):
        return True, "LỖI_DỊCH_VỤ", "Đọc kỹ thông báo lỗi trên để điều chỉnh hướng xử lý hoặc báo lỗi cụ thể."
    return False, "OK", ""


def _validate_tool_parameters(tool_def: Any, params: Any) -> Optional[str]:
    """Planning Tool Schema Check: verify parameter names, required fields, types, and enums."""
    if not isinstance(params, dict):
        return "Parameters phải là JSON object (dict)."
    schema = getattr(tool_def, "parameter_schema", None) or {}
    if not isinstance(schema, dict) or not schema:
        return None

    props = schema.get("properties") or {}
    required = schema.get("required") or []
    for req_field in required:
        if req_field not in params or params[req_field] is None:
            return f"Thiếu tham số bắt buộc `{req_field}` cho `{tool_def.name}`."

    if schema.get("additionalProperties") is False and props:
        unknown = sorted(set(params) - set(props))
        if unknown:
            return f"Tham số không có trong schema của `{tool_def.name}`: {', '.join(unknown)}."

    for key, val in params.items():
        if key not in props or val is None:
            continue
        spec = props[key]
        if not isinstance(spec, dict):
            continue
        if "enum" in spec and val not in spec["enum"]:
            return f"Giá trị `{key}={val!r}` không thuộc enum {spec['enum']}."
        expected_type = spec.get("type")
        if expected_type == "string" and not isinstance(val, str):
            return f"Tham số `{key}` phải là string."
        if expected_type == "integer" and (type(val) is not int):
            return f"Tham số `{key}` phải là integer."
        if expected_type == "number" and (type(val) not in (int, float)):
            return f"Tham số `{key}` phải là number."
        if expected_type == "boolean" and not isinstance(val, bool):
            return f"Tham số `{key}` phải là boolean."
        if expected_type == "array" and not isinstance(val, list):
            return f"Tham số `{key}` phải là array."
        if expected_type == "object" and not isinstance(val, dict):
            return f"Tham số `{key}` phải là object."
    return None


def _is_high_risk_tool(tool_def: Any) -> bool:
    """Human-in-the-loop risk check: any write/mutating/external/delete tool requires confirmation."""
    if getattr(tool_def, "action_type", "read") != "read":
        return True
    name = str(getattr(tool_def, "name", "")).lower()
    # Local/gateway read helpers ending in _create/_flow/_update return ConfirmationPreview themselves
    if name.startswith("business.") and name.endswith(("_create", "_flow", "_update")):
        return False
    return any(tok in name.split(".") for tok in _HIGH_RISK_WRITE_HINTS)


def _prune_observations(observations: list[dict[str, Any]], *, keep_full: int = 2) -> str:
    """Reasoning Context Pruning: keep latest/productive observations intact and compress stale failed ones."""
    if not observations:
        return "Chưa có quan sát nào."
    total = len(observations)
    blocks: list[str] = []
    for idx, item in enumerate(observations):
        is_recent = idx >= max(0, total - keep_full)
        params_str = json.dumps(item["parameters"], ensure_ascii=False, default=str)
        if item.get("is_error") and not is_recent:
            blocks.append(
                f"Bước {item['step']} [ĐÃ LƯỢC BỎ LỖI CŨ · {item.get('error_type', 'LỖI')}]: "
                f"`{item['tool']}` params={params_str} -> {item['observation'][:140].replace(chr(10), ' ')}"
            )
            continue
        limit = 1600 if is_recent else 500
        obs_body = item["observation"][:limit]
        if len(item["observation"]) > limit:
            obs_body += "\n...(đã cắt gọn phần thừa)..."
        diag = f" [Chẩn đoán: {item['error_type']} — {item['error_hint']}]" if item.get("is_error") else ""
        blocks.append(
            f"Bước {item['step']} · `{item['tool']}` params={params_str}{diag}\nObservation:\n{obs_body}"
        )
    return "\n\n---\n".join(blocks)


def _verify_reply_grounding(
    reply_text: str,
    observations: list[dict[str, Any]],
    context_block: str,
    user_text: str,
) -> Optional[str]:
    """Observation Anti-Hallucination check: reject replies introducing unseen doc codes, emails, or URLs."""
    evidence = " ".join([
        user_text or "",
        context_block or "",
        *(str(o.get("observation") or "") for o in observations),
    ])
    evidence_lower = evidence.casefold()

    # Check fabricated emails
    for email in set(re.findall(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", reply_text)):
        if email.casefold() not in evidence_lower:
            return f"Email `{email}` không xuất hiện trong Perception/Observation."

    # Check fabricated URLs
    for url in set(re.findall(r"https?://[^\s)\]>]+", reply_text)):
        if url.casefold() not in evidence_lower:
            return f"Liên kết `{url}` không xuất hiện trong Observation."

    # Check fabricated Odoo document numbers (S00xxx, P00xxx, PO00xxx, WH/OUT/xxx)
    doc_codes = set(re.findall(r"\b(?:S\d{3,}|P(?:O)?\d{3,}|[A-Z0-9]{2,10}/(?:OUT|IN|INT|PICK|PACK|RET)/\d{3,})\b", reply_text))
    for code in doc_codes:
        if code.casefold() not in evidence_lower:
            return f"Mã chứng từ `{code}` không có trong dữ liệu đã quan sát."

    return None


def needs_react_continuation(
    user_text: str,
    intent: IntentAnalysisResult,
    initial_messages: list[Any],
    *,
    domains: Optional[list[str]] = None,
) -> bool:
    """Decide whether a turn should enter the iterative Observation -> Reasoning -> Action loop."""
    if not initial_messages:
        return True
    if any(isinstance(m, (ConfirmationPreview, OptionButtonsMessage)) for m in initial_messages):
        return False

    joined = "\n".join(str(m) for m in initial_messages)
    if "(#" in joined and ("hàng đợi" in joined or "gửi kết quả khi" in joined):
        return False
    if intent.tool_name in _SKIP_REACT_TOOLS:
        if intent.tool_name != "business.lookup":
            return False

    if intent.intent_type == IntentType.TOOL_PLAN:
        return True
    if _looks_like_raw_json(joined):
        return True
    if _EMPTY_OR_ERROR_RE.search(joined):
        return True
    if _COMPOUND_CUE_RE.search(user_text or ""):
        return True
    if domains and len(set(domains)) >= 2:
        return True
    return False


_REACT_PROMPT = """Bạn là AI Agent doanh nghiệp chạy theo vòng lặp:
Perception (Nhận yêu cầu + ngữ cảnh) -> Reasoning (Kiểm tra bộ nhớ & lọc ngữ cảnh) -> Planning (Kiểm tra schema & an toàn) -> Action (Gọi tool) -> Observation (Chẩn đoán lỗi & tự kiểm chứng).

BỘ QUY TẮC KIỂM SOÁT BẮT BUỘC:
1. REASONING — Memory Check & Context Pruning:
   - Đối chiếu `<failed_attempts>` và `<observations>` trước khi lập kế hoạch. TUYỆT ĐỐI không lặp lại công cụ + tham số đã từng thất bại/rỗng.
   - Bỏ qua các dữ kiện nhiễu cũ, chỉ tập trung vào bằng chứng phục vụ đúng mục tiêu hiện tại.
2. PLANNING — Schema Check, Human-in-the-loop & Loop Breaker:
   - Kiểm tra chính xác tên công cụ và kiểu dữ liệu tham số trong `<available_tools>` trước khi gọi.
   - Mọi hành động thay đổi dữ liệu vĩnh viễn, xóa dữ liệu, gửi email/thông tin ra bên ngoài hoặc phát sinh chi phí PHẢI dừng vòng lặp bằng `decision="prepare_write"` (hoặc tool preview) để xin người dùng bấm Xác nhận. KHÔNG gọi chúng trong `call_tools`.
3. OBSERVATION — Error Handling & Anti-Hallucination:
   - Nếu bước trước có `[Chẩn đoán: ...]`, đọc kỹ nguyên nhân gốc (sai tham số, thiếu quyền, hay không có dữ liệu) để đổi chiến lược hợp lý.
   - Trước khi chọn `decision="reply"`, tự kiểm chứng: (a) 100% thông tin (mã đơn, số tiền, email, tên, trạng thái) đều nằm trong `<observations>` hoặc `<conversation_history>`/`<recent_read_records>`, (b) đã giải quyết trọn vẹn mục tiêu gốc của người dùng. Cấm suy diễn/bịa số liệu.

<available_tools>
{tools_catalog}
</available_tools>

{context_block}

{failed_block}

<observations>
{observations_block}
</observations>

Yêu cầu gốc của người dùng: {user_text}
Lượt suy luận hiện tại: {iteration}/{max_iterations}"""


class ReactAgentLoop:
    """Bounded multi-step Reasoning-Action-Observation orchestrator with 5 safety/quality controls."""

    def __init__(
        self,
        llm_caller: Optional[Callable[[str, dict[str, Any]], Awaitable[str]]] = None,
    ) -> None:
        self._custom_llm_caller = llm_caller

    async def _reason(
        self,
        prompt: str,
        *,
        timeout_s: float,
    ) -> Optional[ReactStepDecision]:
        try:
            if self._custom_llm_caller is not None:
                raw = await self._custom_llm_caller(prompt, ReactStepDecision.model_json_schema())
            else:
                settings = get_settings()
                model = getattr(settings, "DEFAULT_LLM_MODEL", None) or getattr(settings, "INTENT_LLM_MODEL", None)
                raw = await generate_json(
                    prompt,
                    response_schema=ReactStepDecision.model_json_schema(),
                    model_override=model,
                    timeout_seconds=min(12.0, max(2.5, timeout_s)),
                )
            data = json.loads(_strip_json_fences(raw))
            return ReactStepDecision.model_validate(data)
        except Exception as exc:
            logger.warning("ReactAgentLoop reasoning failed: %s: %s", type(exc).__name__, exc)
            return None

    async def run(
        self,
        *,
        user_text: str,
        context: GatewayUserContext,
        session_context: dict[str, Any],
        registry: Any,
        execute_read: Callable[[IntentAnalysisResult], Awaitable[list[Any]]],
        execute_prepare_write: Callable[[IntentAnalysisResult], Awaitable[list[Any]]],
        initial_steps: list[tuple[str, dict[str, Any], list[Any]]],
        deadline: float,
        domains: Optional[list[str]] = None,
    ) -> list[Any]:
        """Run up to REACT_MAX_ITERATIONS of Reasoning -> Action -> Observation after initial steps."""
        settings = get_settings()
        max_iters = max(2, min(int(getattr(settings, "REACT_MAX_ITERATIONS", 4) or 4), 6))
        loop_budget = float(getattr(settings, "REACT_TIME_BUDGET_SECONDS", 24.0) or 24.0)
        soft_deadline = min(deadline - 1.5, time.time() + loop_budget)

        from app.services.intent_prompt import tool_catalog
        from app.services.workspace_tool_plan import select_tools

        all_tools = registry.list_tools(list(context.permissions))
        selected = select_tools(
            user_text,
            all_tools,
            expanded=True,
            domains=domains or [],
            read_only=False,
        )
        selected_names = {t.name for t in selected}
        for tool in all_tools:
            if len(selected) >= 16:
                break
            if tool.name not in selected_names and tool.name in {
                "business.lookup", "odoo.partners.search", "odoo.sales.search",
                "odoo.purchases.search", "odoo.products.search", "odoo.invoices.search",
                "workspace.gmail.read", "workspace.calendar.read", "workspace.drive.read",
            }:
                selected.append(tool)
                selected_names.add(tool.name)
        tools_text = tool_catalog(selected)

        # Perception context
        ctx_parts = []
        history = (session_context or {}).get("history") or []
        if history:
            lines = [f"{m.get('role', 'user')}: {str(m.get('content', ''))[:400]}" for m in history[-10:]]
            ctx_parts.append("<conversation_history>\n" + "\n".join(lines) + "\n</conversation_history>")
        if (session_context or {}).get("active_draft"):
            ctx_parts.append("<active_draft>\n" + json.dumps(session_context["active_draft"], ensure_ascii=False)[:1500] + "\n</active_draft>")
        last_read = (session_context or {}).get("last_read") or (session_context or {}).get("last_read_data")
        if last_read:
            ctx_parts.append("<recent_read_records>\n" + json.dumps(last_read, ensure_ascii=False)[:2000] + "\n</recent_read_records>")
        context_block = "\n".join(ctx_parts)

        seen_calls: set[str] = set()
        failed_attempts: list[str] = []
        observations: list[dict[str, Any]] = []
        last_non_empty_messages: list[Any] = []
        consecutive_failures = 0

        def _record_observation(tool_name: str, params: dict[str, Any], msgs: list[Any]) -> bool:
            nonlocal consecutive_failures, last_non_empty_messages
            obs_text = _extract_observation_text(msgs)
            is_err, err_type, err_hint = _diagnose_observation_error(obs_text)
            if msgs:
                last_non_empty_messages = msgs
            observations.append({
                "step": len(observations) + 1,
                "tool": tool_name,
                "parameters": params,
                "observation": obs_text,
                "is_error": is_err,
                "error_type": err_type,
                "error_hint": err_hint,
            })
            if is_err:
                consecutive_failures += 1
                failed_attempts.append(
                    f"- `{tool_name}`({json.dumps(params, ensure_ascii=False, default=str)}) -> [{err_type}] {obs_text[:120]}"
                )
            else:
                consecutive_failures = 0
            return is_err

        for tool_name, params, msgs in initial_steps:
            seen_calls.add(_canonical_call_key(tool_name, params))
            _record_observation(tool_name, params, msgs)

        def _loop_breaker_message() -> list[str]:
            recent_errs = "\n".join(failed_attempts[-3:])
            return [
                "⚠️ Tác vụ đã gặp lỗi 3 bước liên tiếp nên tôi tạm dừng để tránh lặp vô ích.\n"
                f"Nguyên nhân ghi nhận:\n{recent_errs}\n\n"
                "Bạn vui lòng kiểm tra lại thông tin/quyền truy cập hoặc cung cấp tên/mã chính xác hơn giúp tôi nhé."
            ]

        for iteration in range(1, max_iters + 1):
            # Loop Breaker check before planning next step
            if consecutive_failures >= 3:
                logger.warning("react_loop_breaker_triggered consecutive_failures=%s", consecutive_failures)
                return _loop_breaker_message()

            remaining = soft_deadline - time.time()
            if remaining < 2.5:
                logger.info("react_loop_stop reason=budget_exhausted step=%s", iteration)
                break

            failed_block = (
                "<failed_attempts>\n" + "\n".join(failed_attempts[-6:]) + "\n</failed_attempts>"
                if failed_attempts
                else ""
            )
            prompt = _REACT_PROMPT.format(
                tools_catalog=tools_text,
                context_block=context_block,
                failed_block=failed_block,
                observations_block=_prune_observations(observations),
                user_text=user_text,
                iteration=iteration,
                max_iterations=max_iters,
            )

            decision = await self._reason(prompt, timeout_s=remaining)
            if decision is None:
                break

            logger.info(
                "react_loop_step iteration=%s decision=%s grounded=%s thought=%s",
                iteration, decision.decision, decision.grounded_check, (decision.thought or "")[:140],
            )

            if decision.decision == "reply":
                text = (decision.reply_text or "").strip()
                if not text:
                    break
                if not decision.grounded_check:
                    observations.append({
                        "step": len(observations) + 1,
                        "tool": "self_reflection",
                        "parameters": {},
                        "observation": "Tự phản chiếu chưa đạt grounded_check=True; hãy kiểm tra lại hoặc dùng decision='clarify'.",
                        "is_error": True,
                        "error_type": "CHƯA_ĐỦ_CĂN_CỨ",
                        "error_hint": "Chỉ trả lời từ dữ liệu đã quan sát được.",
                    })
                    consecutive_failures += 1
                    continue
                hallucination_err = _verify_reply_grounding(text, observations, context_block, user_text)
                if hallucination_err:
                    logger.warning("react_anti_hallucination_blocked reason=%s", hallucination_err)
                    failed_attempts.append(f"- [CHỐNG ẢO GIÁC] {hallucination_err}")
                    observations.append({
                        "step": len(observations) + 1,
                        "tool": "anti_hallucination",
                        "parameters": {},
                        "observation": f"CẢNH BÁO CHỐNG ẢO GIÁC: {hallucination_err} Hãy sửa lại câu trả lời chỉ dùng dữ liệu có thật trong Observation.",
                        "is_error": True,
                        "error_type": "ẢO_GIÁC_DỮ_LIỆU",
                        "error_hint": "Loại bỏ mọi mã chứng từ/email/URL không có trong Observation.",
                    })
                    consecutive_failures += 1
                    continue
                return [text]

            if decision.decision == "clarify":
                prompt_msg = (decision.clarify_prompt or "Bạn vui lòng làm rõ thêm thông tin giúp tôi nhé.").strip()
                opts = [str(o).strip() for o in decision.clarify_options if str(o).strip()][:4]
                if opts:
                    return [OptionButtonsMessage(prompt_msg, opts)]
                return [prompt_msg]

            if decision.decision == "prepare_write":
                w_tool = (decision.write_tool_name or "").strip()
                tool_def = registry.get_tool(w_tool) if w_tool else None
                if not tool_def:
                    _record_observation(w_tool or "unknown", decision.write_parameters, [f"Lỗi schema: Công cụ `{w_tool}` không tồn tại."])
                    continue
                schema_err = _validate_tool_parameters(tool_def, decision.write_parameters or {})
                if schema_err:
                    _record_observation(w_tool, decision.write_parameters or {}, [f"Sai tham số: {schema_err}"])
                    continue
                if tool_def.required_permission and not context.has_permission(tool_def.required_permission):
                    return [f"⚠️ Tài khoản chưa có quyền `{tool_def.required_permission}` cho thao tác này."]
                write_intent = IntentAnalysisResult(
                    intent_type=IntentType.PREPARE_WRITE if tool_def.action_type == "write" else IntentType.READ,
                    tool_name=w_tool,
                    parameters=decision.write_parameters or {},
                )
                # Human-in-the-loop gate: immediately pause and present confirmation preview
                if write_intent.intent_type == IntentType.PREPARE_WRITE:
                    return await execute_prepare_write(write_intent)
                res_msgs = await execute_read(write_intent)
                if any(isinstance(m, (ConfirmationPreview, OptionButtonsMessage)) for m in res_msgs):
                    return res_msgs
                _record_observation(w_tool, decision.write_parameters or {}, res_msgs)
                continue

            # decision == "call_tools"
            if not decision.tool_calls or iteration == max_iters:
                break

            progressed = False
            for tc in decision.tool_calls[:3]:
                t_name = tc.tool_name.strip()
                call_key = _canonical_call_key(t_name, tc.parameters)
                if call_key in seen_calls:
                    consecutive_failures += 1
                    failed_attempts.append(f"- `{t_name}`({json.dumps(tc.parameters, ensure_ascii=False, default=str)}) -> [LẶP_LỐI_MÒN]")
                    observations.append({
                        "step": len(observations) + 1,
                        "tool": t_name,
                        "parameters": tc.parameters,
                        "observation": "Memory Check: Đã gọi y hệt lệnh này ở vòng trước. Không lặp lại lối mòn cũ.",
                        "is_error": True,
                        "error_type": "LẶP_LỐI_MÒN",
                        "error_hint": "Thử từ khóa/tham số khác hoặc kết luận ngay.",
                    })
                    if consecutive_failures >= 3:
                        return _loop_breaker_message()
                    continue

                tool_def = registry.get_tool(t_name)
                if not tool_def:
                    _record_observation(t_name, tc.parameters, [f"Lỗi schema: Công cụ `{t_name}` không tồn tại."])
                    if consecutive_failures >= 3:
                        return _loop_breaker_message()
                    continue

                # Human-in-the-loop guard inside Planning
                if _is_high_risk_tool(tool_def):
                    observations.append({
                        "step": len(observations) + 1,
                        "tool": t_name,
                        "parameters": tc.parameters,
                        "observation": (
                            f"Rào chắn Human-in-the-loop: `{t_name}` là hành động thay đổi dữ liệu / gửi ra ngoài. "
                            "Phải dùng decision='prepare_write' để tạo bản xem trước chờ người dùng xác nhận."
                        ),
                        "is_error": True,
                        "error_type": "CHỜ_XÁC_NHẬN_NGƯỜI_DÙNG",
                        "error_hint": "Chuyển sang decision='prepare_write'.",
                    })
                    progressed = True
                    consecutive_failures += 1
                    if consecutive_failures >= 3:
                        return _loop_breaker_message()
                    continue

                # Tool Schema Check before calling Action
                schema_err = _validate_tool_parameters(tool_def, tc.parameters)
                if schema_err:
                    _record_observation(t_name, tc.parameters, [f"Lỗi schema tham số: {schema_err}"])
                    progressed = True
                    if consecutive_failures >= 3:
                        return _loop_breaker_message()
                    continue

                if tool_def.required_permission and not context.has_permission(tool_def.required_permission):
                    _record_observation(t_name, tc.parameters, [f"Thiếu quyền `{tool_def.required_permission}`."])
                    if consecutive_failures >= 3:
                        return _loop_breaker_message()
                    continue

                seen_calls.add(call_key)
                progressed = True
                step_intent = IntentAnalysisResult(
                    intent_type=IntentType.READ,
                    tool_name=t_name,
                    parameters=tc.parameters,
                )
                msgs = await execute_read(step_intent)
                if any(isinstance(m, (ConfirmationPreview, OptionButtonsMessage)) for m in msgs):
                    return msgs
                _record_observation(t_name, tc.parameters, msgs)
                if consecutive_failures >= 3:
                    return _loop_breaker_message()

            if not progressed:
                break

        if consecutive_failures >= 3:
            return _loop_breaker_message()

        # Final synthesis if last message is raw JSON or multiple observations need summarization
        if last_non_empty_messages and not _looks_like_raw_json("\n".join(str(m) for m in last_non_empty_messages)):
            if len(observations) <= 1:
                return last_non_empty_messages

        remaining = soft_deadline - time.time()
        if remaining >= 2.5 and observations:
            synth_prompt = (
                "Tổng hợp câu trả lời ngắn gọn, chính xác bằng tiếng Việt dựa HOÀN TOÀN trên các kết quả tra cứu sau "
                "(100% căn cứ Observation, cấm bịa mã chứng từ/email/số liệu, không in JSON thô). "
                "Trả về JSON schema với decision='reply', grounded_check=true và reply_text.\n\n"
                f"Yêu cầu người dùng: {user_text}\n\n" + _prune_observations(observations, keep_full=3)
            )
            synth = await self._reason(synth_prompt, timeout_s=remaining)
            if synth and synth.reply_text and synth.reply_text.strip():
                candidate = synth.reply_text.strip()
                if not _verify_reply_grounding(candidate, observations, context_block, user_text):
                    return [candidate]

        return last_non_empty_messages or ["Chưa thu thập đủ thông tin để trả lời yêu cầu này."]


react_agent_loop = ReactAgentLoop()
