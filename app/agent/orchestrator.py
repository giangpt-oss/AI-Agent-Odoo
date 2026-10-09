import asyncio
import logging
from typing import Any, Dict, List, Tuple
from google import genai
from google.genai import types

from app.models.context import SkillExecutionContext
from app.skills.base import BaseSkill
from app.agent.router import SkillRouter
from app.agent.confirmation_manager import confirmation_manager

logger = logging.getLogger(__name__)

class AgentOrchestrator:
    def __init__(self, ai_client: genai.Client, router: SkillRouter, model_name: str = "gemini-3.5-flash-lite"):
        self.ai_client = ai_client
        self.router = router
        self.model_name = model_name

    async def process_turn(
        self, 
        query: str, 
        context: SkillExecutionContext, 
        system_instruction: str
    ) -> Tuple[str, List[str], Any]:
        
        # 1. Routing: Pick skills based on intent
        selected_skills = await self.router.route(query, context)
        logger.info(f"Router selected skills: {[s.name for s in selected_skills]}")
        
        skill_map: Dict[str, BaseSkill] = {s.name: s for s in selected_skills}
        
        # 2. Build Tool definitions for Gemini
        tools_list = []
        if selected_skills:
            function_declarations = []
            for skill in selected_skills:
                function_declarations.append({
                    "name": skill.name,
                    "description": skill.description,
                    "parameters": skill.input_schema
                })
            tools_list.append({"function_declarations": function_declarations})

        # 3. Prepare Chat Context
        chat_contents = []
        if context.attachments:
            for att in context.attachments:
                if att.raw_bytes and att.mime_type:
                    chat_contents.append(types.Part.from_bytes(data=att.raw_bytes, mime_type=att.mime_type))
                doc_context = (
                    f"📎 **TÀI LIỆU ĐÍNH KÈM:**\n"
                    f"- Tên file: `{att.filename}`\n"
                    f"- Thông tin: {att.summary}\n\n"
                    f"--- NỘI DUNG ---\n"
                    f"{att.text_content[:35000]}\n"
                    f"------------------------------------\n\n"
                )
                chat_contents.append(doc_context)
                
        user_prompt = query if query.strip() else "Hãy đọc, phân tích và tóm tắt thông tin quan trọng từ tài liệu đính kèm này."
        chat_contents.append(user_prompt)

        # 4. Start Chat
        def _sync_create_chat():
            return self.ai_client.chats.create(
                model=self.model_name,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    tools=tools_list or None,
                    temperature=0.1,
                )
            )
            
        chat = await asyncio.to_thread(_sync_create_chat)

        # 5. Execute Turn and handle Function Calls
        def _sync_send(content):
            return chat.send_message(content)

        try:
            response = await asyncio.to_thread(_sync_send, chat_contents)
            
            # Function Calling Loop
            for _ in range(8):
                if not response.function_calls:
                    break
                function_responses = []
                for fc in response.function_calls:
                    skill_name = fc.name
                    args = fc.args or {}
                    
                    if skill_name in skill_map:
                        skill = skill_map[skill_name]
                        logger.info(f"Executing skill: {skill_name}")
                        try:
                            # 1. Validation Input
                            skill.validate_input(args)
                            
                            # 1.5 Permission Check
                            from app.services.permissions import permission_service
                            if not permission_service.check_permission(skill, context.session.permissions):
                                raise PermissionError(f"PERMISSION_DENIED: Bạn chưa cấp quyền thực hiện thao tác '{skill.name}'.")
                            
                            # 2. Check for confirmation
                            if skill.requires_confirmation:
                                preview_data = await skill.preview(context, **args)
                                record = confirmation_manager.create_request(skill.name, args, preview_data, context)
                                result = record.to_dict()
                                result["status"] = "confirmation_required"
                                return "Vui lòng kiểm tra nội dung và bấm xác nhận để thực hiện.", context.previous_outputs.get("generated_excel_files", []), result
                            else:
                                # 3. Execute skill normally
                                result = await skill.execute(context, **args)
                                
                                # 4. Validate Output
                                skill.validate_output(result)
                                
                            function_responses.append(
                                types.Part.from_function_response(
                                    name=skill_name,
                                    response={"result": result}
                                )
                            )
                        except Exception as e:
                            logger.error(f"Skill execution failed: {e}", exc_info=True)
                            function_responses.append(
                                types.Part.from_function_response(
                                    name=skill_name,
                                    response={"error": str(e)}
                                )
                            )
                    else:
                        logger.warning(f"Model requested unknown skill: {skill_name}")
                        function_responses.append(
                            types.Part.from_function_response(
                                name=skill_name,
                                response={"error": f"Skill {skill_name} not available in this context."}
                            )
                        )
                
                # Send tool results back to model
                response = await asyncio.to_thread(_sync_send, function_responses)

            else:
                return "Yêu cầu cần quá nhiều bước. Vui lòng chia thành yêu cầu nhỏ hơn.", [], None

            final_text = response.text or "Đã xử lý xong yêu cầu."
            
            # Extract generated files from context
            generated_files = context.previous_outputs.get("generated_excel_files", [])
            pending_confirmation = context.metadata.get("pending_confirmation")
            
            return final_text, generated_files, pending_confirmation
            
        except Exception as e:
            logger.error(f"Lỗi trong Orchestrator: {e}", exc_info=True)
            return f"❌ Đã xảy ra lỗi hệ thống: {str(e)}", [], None
