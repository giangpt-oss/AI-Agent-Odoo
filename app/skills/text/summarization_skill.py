from typing import Any
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext

class SummarizationSkill(BaseSkill):
    name = "summarize_text"
    description = "Tóm tắt một đoạn văn bản hoặc nội dung tài liệu."
    category = SkillCategory.UTILITY
    capabilities = ["text", "summarize", "tóm tắt"]
    operation_type = OperationType.EXTERNAL_ACTION
    input_schema = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "Nội dung cần tóm tắt"},
            "mode": {
                "type": "string", 
                "enum": ["short", "detailed", "executive", "key_points", "action_items", "section_by_section"],
                "description": "Chế độ tóm tắt",
                "default": "key_points"
            }
        },
        "required": ["content"]
    }
    output_schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "key_points": {"type": "array", "items": {"type": "string"}},
            "action_items": {"type": "array", "items": {"type": "string"}}
        }
    }

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        # Trong hệ thống LLM chaining, chức năng này có thể tận dụng chính 
        # LLM instance của bot, nhưng vì Skill chạy độc lập, ta có thể dùng 
        # model nhanh để summarize hoặc định hướng output prompt.
        # Ở cấp độ skill, chúng ta giả lập prompt chaining bằng call LLM (nếu có provider).
        # Tạm thời để hoàn thiện P0 logic, ta gọi AI client từ provider.
        ai_client = context.providers.get("ai_client")
        if not ai_client:
            # Fallback nếu không truyền ai_client vào providers
            return {
                "summary": "LLM Provider is required for summarization.",
                "key_points": ["Tóm tắt lỗi: Không có AI client."],
                "action_items": []
            }
            
        mode = kwargs.get("mode", "key_points")
        prompt = f"Bạn là một chuyên gia phân tích. Hãy tóm tắt nội dung sau theo chế độ '{mode}'. Yêu cầu trả về JSON chuẩn gồm 'summary', 'key_points', và 'action_items'. Nội dung:\n\n{kwargs['content'][:30000]}"
        
        try:
            import asyncio
            response = await asyncio.to_thread(
                ai_client.models.generate_content,
                model="gemini-2.5-flash",
                contents=prompt,
                config={"response_mime_type": "application/json"}
            )
            import json
            return json.loads(response.text)
        except Exception as e:
            return {"summary": f"Lỗi tóm tắt: {e}", "key_points": [], "action_items": []}
