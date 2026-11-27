"""Route interpreted intents to their handlers with shared providers and state."""

import time
from typing import Any, Optional

from app.domain.gateway_context import GatewayUserContext
from app.domain.intent_schema import IntentAnalysisResult, IntentType
from app.domain.tool_registry import ToolRegistry, global_tool_registry
from app.providers.gateway_calendar import GatewayCalendarProvider
from app.providers.gateway_crm import GatewayCRMProvider
from app.providers.gateway_gmail import GatewayGmailProvider
from app.providers.tasks.local import LocalTaskProvider
from app.services.confirmation_service import ConfirmationService, OptionButtonsMessage, confirmation_service
from app.services.conversation_history_store import ConversationHistoryStore, conversation_history_store
from app.services.executor_handlers.confirmation import ConfirmationHandler
from app.services.executor_handlers.prepare_write import PrepareWriteHandler
from app.services.executor_handlers.read import ReadHandler
from app.services.executor_handlers.read import _normalize_workspace_params as _normalize_workspace_params
from app.services.gateway_client import GatewayClient, gateway_client


class UnifiedExecutor(ReadHandler, PrepareWriteHandler, ConfirmationHandler):
    """Bộ thực thi ý định thống nhất không phụ thuộc vào parser phụ."""

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        local_tasks: Optional[LocalTaskProvider] = None,
    ) -> None:
        self.registry = registry or global_tool_registry
        self.calendar_provider = GatewayCalendarProvider()
        self.gmail_provider = GatewayGmailProvider()
        self.crm_provider = GatewayCRMProvider()
        self.local_tasks = local_tasks or LocalTaskProvider()

    @property
    def gateway_client(self) -> GatewayClient:
        return gateway_client

    @property
    def conversation_history_store(self) -> ConversationHistoryStore:
        return conversation_history_store

    @property
    def confirmation_service(self) -> ConfirmationService:
        return confirmation_service

    async def execute(
        self,
        intent: IntentAnalysisResult,
        context: GatewayUserContext,
        chat_id: int,
        telegram_user_id: int,
        session_context: dict[str, Any],
        deadline: float,
        user_info: dict[str, Any],
    ) -> list[str]:
        """Thực thi IntentAnalysisResult với deadline kiểm soát toàn turn."""
        now_ts = time.time()
        remaining_time = max(0.5, deadline - now_ts)

        if intent.intent_type == IntentType.TOOL_PLAN:
            from app.core.config import get_settings
            from app.services.react_loop import needs_react_continuation, react_agent_loop

            if intent.steps and not any(s.tool_name.startswith("workspace.") for s in intent.steps):
                outputs: list[str] = []
                executed_steps: list[tuple[str, dict[str, Any], list[Any]]] = []
                for step in intent.steps[:4]:
                    tool_def = self.registry.get_tool(step.tool_name)
                    if not tool_def:
                        continue
                    step_intent = IntentAnalysisResult(
                        intent_type=IntentType.PREPARE_WRITE if tool_def.action_type == "write" else IntentType.READ,
                        tool_name=step.tool_name,
                        parameters=step.parameters,
                    )
                    if step_intent.intent_type == IntentType.PREPARE_WRITE:
                        step_out = await self._handle_prepare_write(step_intent, context, chat_id)
                        outputs.extend(step_out)
                        if any(hasattr(m, "confirmation_id") for m in step_out):
                            return outputs
                    else:
                        step_out = await self._handle_read(
                            step_intent, context, chat_id, user_info, remaining_time,
                            history=(session_context or {}).get("history"),
                        )
                        outputs.extend(step_out)
                        executed_steps.append((step.tool_name, step.parameters, step_out))
                if (getattr(get_settings(), "ENABLE_REACT_LOOP", False)
                        and executed_steps
                        and (session_context or {}).get("_user_text")):
                    return await react_agent_loop.run(
                        user_text=session_context["_user_text"],
                        context=context,
                        session_context=session_context,
                        registry=self.registry,
                        execute_read=lambda si: self._handle_read(
                            si, context, chat_id, user_info, max(1.0, deadline - time.time()),
                            history=(session_context or {}).get("history"),
                        ),
                        execute_prepare_write=lambda wi: self._handle_prepare_write(wi, context, chat_id),
                        initial_steps=executed_steps,
                        deadline=deadline,
                        domains=(session_context or {}).get("_route_domains"),
                    )
                if outputs:
                    return outputs
            plan_outputs = await self._handle_workspace_plan(intent, context, chat_id, deadline)
            if (getattr(get_settings(), "ENABLE_REACT_LOOP", False)
                    and (session_context or {}).get("_user_text")
                    and needs_react_continuation(session_context["_user_text"], intent, plan_outputs, domains=(session_context or {}).get("_route_domains"))):
                initial = [(s.tool_name, s.parameters, plan_outputs if idx == len(intent.steps) - 1 else []) for idx, s in enumerate(intent.steps)]
                return await react_agent_loop.run(
                    user_text=session_context["_user_text"],
                    context=context,
                    session_context=session_context,
                    registry=self.registry,
                    execute_read=lambda si: self._handle_read(
                        si, context, chat_id, user_info, max(1.0, deadline - time.time()),
                        history=(session_context or {}).get("history"),
                    ),
                    execute_prepare_write=lambda wi: self._handle_prepare_write(wi, context, chat_id),
                    initial_steps=initial,
                    deadline=deadline,
                    domains=(session_context or {}).get("_route_domains"),
                )
            return plan_outputs

        # 1. REPLY Intent
        if intent.intent_type == IntentType.REPLY:
            return [intent.reply_text or ""]

        # 2. CLARIFY Intent
        if intent.intent_type == IntentType.CLARIFY:
            clarify_text = intent.clarification_prompt or "Vui lòng làm rõ yêu cầu."
            if intent.candidate_options:
                options_str = "\n".join(f"• {opt}" for opt in intent.candidate_options)
                clarify_text += f"\n\nDanh sách lựa chọn:\n{options_str}"
            self.conversation_history_store.set_pending_clarification(
                tenant=context.tenant,
                user_id=context.user_id,
                chat_id=chat_id,
                binding_id=context.telegram_binding_id,
                clarification_data={
                    "prompt": clarify_text,
                    "missing_fields": intent.missing_fields,
                    "candidate_options": intent.candidate_options,
                    "tool_name": intent.tool_name,
                    "parameters": intent.parameters,
                },
                database=context.database,
            )
            if intent.candidate_options:
                return [OptionButtonsMessage(clarify_text, intent.candidate_options)]
            return [clarify_text]

        # 3. UNSUPPORTED Intent
        if intent.intent_type == IntentType.UNSUPPORTED:
            return [intent.reply_text or "⚠️ Yêu cầu ngoài phạm vi hỗ trợ."]

        # 4. DRAFT Intent
        if intent.intent_type == IntentType.DRAFT:
            return self._handle_draft(intent, context, chat_id)

        # 5. ACTION_CONFIRM Intent
        if intent.intent_type == IntentType.ACTION_CONFIRM:
            return await self._handle_confirm(intent, context, chat_id, telegram_user_id, remaining_time)

        # 6. ACTION_CANCEL Intent
        if intent.intent_type == IntentType.ACTION_CANCEL:
            return self._handle_cancel(intent, context, chat_id, telegram_user_id)

        # 7. ACTION_STATUS Intent (Đối soát trạng thái với Gateway)
        if intent.intent_type == IntentType.ACTION_STATUS:
            return await self._handle_status(intent, context, chat_id, telegram_user_id, remaining_time)

        # 8. READ Intent
        if intent.intent_type == IntentType.READ:
            from app.core.config import get_settings
            from app.services.react_loop import needs_react_continuation, react_agent_loop

            initial_outputs = await self._handle_read(
                intent, context, chat_id, user_info, remaining_time,
                history=(session_context or {}).get("history"),
            )
            user_text = (session_context or {}).get("_user_text")
            if (getattr(get_settings(), "ENABLE_REACT_LOOP", False)
                    and user_text
                    and needs_react_continuation(
                        user_text,
                        intent,
                        initial_outputs,
                        domains=(session_context or {}).get("_route_domains"),
                    )):
                return await react_agent_loop.run(
                    user_text=user_text,
                    context=context,
                    session_context=session_context or {},
                    registry=self.registry,
                    execute_read=lambda si: self._handle_read(
                        si, context, chat_id, user_info, max(1.0, deadline - time.time()),
                        history=(session_context or {}).get("history"),
                    ),
                    execute_prepare_write=lambda wi: self._handle_prepare_write(wi, context, chat_id),
                    initial_steps=[(intent.tool_name or "", intent.parameters or {}, initial_outputs)],
                    deadline=deadline,
                    domains=(session_context or {}).get("_route_domains"),
                )
            return initial_outputs

        # 9. PREPARE_WRITE Intent
        if intent.intent_type == IntentType.PREPARE_WRITE:
            return await self._handle_prepare_write(intent, context, chat_id)

        return ["⚠️ Không xác định được loại ý định xử lý."]


unified_executor = UnifiedExecutor()
