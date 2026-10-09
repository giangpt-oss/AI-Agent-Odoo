from typing import Any
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, OperationType
from app.models.context import SkillExecutionContext

class TranslationSkill(BaseSkill):
    name = "translate_text"
    description = "Dịch thuật văn bản. Đảm bảo tính nhất quán của thuật ngữ."
    category = SkillCategory.UTILITY
    capabilities = ["text", "translate", "dịch"]
    operation_type = OperationType.EXTERNAL_ACTION
    input_schema = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "Nội dung cần dịch"},
            "source_lang": {"type": "string", "description": "Ngôn ngữ nguồn (mặc định tự phát hiện)", "default": "auto"},
            "target_lang": {"type": "string", "description": "Ngôn ngữ đích (vd: English, Vietnamese)", "default": "Vietnamese"},
            "mode": {
                "type": "string", 
                "enum": ["natural", "literal", "professional", "academic"],
                "description": "Phong cách dịch",
                "default": "professional"
            }
        },
        "required": ["content", "target_lang"]
    }
    output_schema = {
        "type": "object",
        "properties": {
            "translated_text": {"type": "string"}
        }
    }

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        ai_client = context.providers.get("ai_client")
        if not ai_client:
            return {"translated_text": "Error: AI client provider missing."}
            
        content = kwargs["content"]
        source = kwargs.get("source_lang", "auto")
        target = kwargs["target_lang"]
        mode = kwargs.get("mode", "professional")
        
        prompt = (
            f"Bạn là chuyên gia dịch thuật. Hãy dịch văn bản sau từ '{source}' sang '{target}' "
            f"theo phong cách '{mode}'. Hãy giữ nguyên thuật ngữ chuyên ngành, mã code, ID và tên riêng nếu có.\n\n"
            f"--- BẮT ĐẦU VĂN BẢN ---\n{content[:30000]}\n--- KẾT THÚC VĂN BẢN ---"
        )
        
        try:
            import asyncio
            response = await asyncio.to_thread(
                ai_client.models.generate_content,
                model="gemini-3.5-flash-lite",
                contents=prompt
            )
            return {"translated_text": response.text}
        except Exception as e:
            return {"translated_text": f"[Translation Error] {e}"}

class ProofreadingSkill(BaseSkill):
    name = "proofread_text"
    description = "Kiểm tra lỗi ngữ pháp, chính tả, dấu câu và văn phong của văn bản."
    category = SkillCategory.UTILITY
    capabilities = ["text", "proofread", "grammar", "spelling"]
    operation_type = OperationType.EXTERNAL_ACTION
    input_schema = {
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "Nội dung cần soát lỗi"}
        },
        "required": ["content"]
    }
    output_schema = {
        "type": "object",
        "properties": {
            "original": {"type": "string"},
            "revised": {"type": "string"},
            "changes": {"type": "array", "items": {"type": "string"}}
        }
    }

    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        ai_client = context.providers.get("ai_client")
        if not ai_client:
            return {"original": kwargs["content"], "revised": "", "changes": ["Error: AI provider missing."]}
            
        prompt = (
            "Hãy soát lỗi (proofread) đoạn văn bản sau. Kiểm tra grammar, spelling, punctuation, clarity và formal tone. "
            "TRẢ VỀ KẾT QUẢ DƯỚI DẠNG JSON với các trường: 'original', 'revised', 'changes' (danh sách các thay đổi chính).\n\n"
            f"{kwargs['content'][:30000]}"
        )
        
        try:
            import asyncio
            response = await asyncio.to_thread(
                ai_client.models.generate_content,
                model="gemini-3.5-flash-lite",
                contents=prompt,
                config={"response_mime_type": "application/json"}
            )
            import json
            return json.loads(response.text)
        except Exception as e:
            return {"original": kwargs["content"], "revised": f"Lỗi soát lỗi: {e}", "changes": []}
