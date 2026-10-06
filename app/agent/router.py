import logging
from typing import List
from app.skills.registry import skill_registry, SkillRegistry
from app.models.context import SkillExecutionContext
from app.skills.base import BaseSkill
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

class SkillRouter:
    def __init__(self, ai_client: genai.Client, registry: SkillRegistry = skill_registry):
        self.ai_client = ai_client
        self.registry = registry

    def _get_skill_catalog(self) -> str:
        skills = self.registry.get_all_skills()
        catalog = []
        for s in skills:
            catalog.append(f"- {s.name} ({s.category.value}): {s.description} | Capabilities: {', '.join(s.capabilities)}")
        return "\n".join(catalog)

    async def route(self, query: str, context: SkillExecutionContext) -> List[BaseSkill]:
        """
        Phân tích query và trả về danh sách các skill cần thiết.
        Sử dụng Gemini để ánh xạ intent -> skills.
        """
        all_skills = self.registry.get_all_skills()
        if not self.ai_client or len(all_skills) == 0:
            return all_skills
            
        system_instruction = (
            "Bạn là một Skill Router của hệ thống AI Office Assistant.\n"
            "Nhiệm vụ: phân tích yêu cầu người dùng và tài liệu đính kèm (nếu có), "
            "sau đó chọn ra TÊN các skill cần thiết để hoàn thành yêu cầu.\n"
            "Nếu người dùng yêu cầu xuất excel/báo cáo bảng tính, phải bao gồm 'export_data_to_excel'.\n"
            "Chỉ trả về danh sách các tên skill, phân tách bằng dấu phẩy. Không giải thích thêm.\n\n"
            f"Danh sách Skill hiện có:\n{self._get_skill_catalog()}"
        )
        
        # Thêm thông tin file đính kèm để router quyết định
        prompt = f"Yêu cầu: {query}\n"
        if context.attachments:
            prompt += f"Có {len(context.attachments)} file đính kèm. VD: {context.attachments[0].filename}\n"
            
        try:
            import asyncio
            # Sử dụng model nhỏ, nhanh cho routing (gemini-2.5-flash)
            # Default model is passed implicitly but we can hardcode for speed or use default
            response = await asyncio.to_thread(
                self.ai_client.models.generate_content,
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.0
                )
            )
            selected_names = [n.strip() for n in (response.text or "").split(",")]
            selected_skills = []
            for name in selected_names:
                try:
                    selected_skills.append(self.registry.get_skill(name))
                except Exception:
                    pass
                    
            if not selected_skills:
                return self._heuristic_fallback(query, all_skills)
                
            return selected_skills
        except Exception as e:
            logger.warning(f"Lỗi SkillRouter LLM call, fallback: {e}")
            return self._heuristic_fallback(query, all_skills)

    def _heuristic_fallback(self, query: str, all_skills: List[BaseSkill]) -> List[BaseSkill]:
        query_lower = query.lower()
        selected = set()
        for skill in all_skills:
            if skill.name.lower() in query_lower:
                selected.add(skill)
            for cap in skill.capabilities:
                if cap.lower() in query_lower:
                    selected.add(skill)
        
        # Luôn kèm theo profile skill cho heuristic
        try:
            selected.add(self.registry.get_skill("get_user_profile"))
        except Exception:
            pass
            
        if "excel" in query_lower or "xuất" in query_lower:
            try:
                selected.add(self.registry.get_skill("export_data_to_excel"))
            except Exception:
                pass
                
        return list(selected) if selected else all_skills
