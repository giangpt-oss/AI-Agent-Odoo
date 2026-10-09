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

        # ⚡ FAST HEURISTIC ROUTER: Khử độ trễ 1-2s của LLM Router đối với các câu hỏi có từ khóa rõ ràng
        q_lower = (query or "").lower()
        fast_skill_names = set()

        if any(k in q_lower for k in ["khách hàng", "đối tác", "bệnh viện", "nhà cung cấp", "danh bạ", "liên hệ", "partner", "customer"]):
            fast_skill_names.add("get_partners_and_customers")
        if any(k in q_lower for k in ["sản phẩm", "hàng hóa", "thiết bị", "giá bán", "tồn kho", "bảng giá", "mặt hàng", "product"]):
            fast_skill_names.add("get_products_and_inventory")
        if any(k in q_lower for k in ["đơn hàng", "đơn bán", "báo giá", "doanh số", "sale order", "sales order"]):
            fast_skill_names.add("get_sale_orders")
        if any(k in q_lower for k in ["nhân viên", "nhân sự", "phòng ban", "quy mô công ty", "đồng nghiệp", "employee"]):
            fast_skill_names.add("get_company_employees")
        if any(k in q_lower for k in ["cơ hội", "pipeline", "leads", "lead", "deal"]):
            fast_skill_names.add("get_crm_pipeline")
        if any(k in q_lower for k in ["đơn mua", "purchase", "phiếu kho", "xuất kho", "nhập kho", "stock", "picking", "hóa đơn", "invoice", "account.move", "bảng dữ liệu"]):
            fast_skill_names.add("query_odoo_records")
        if any(k in q_lower for k in ["xuất excel", "xuất file excel", "tải excel", "bảng tính", "sheet"]):
            fast_skill_names.add("export_data_to_excel")

        if fast_skill_names and not context.attachments:
            matched = [s for s in all_skills if s.name in fast_skill_names]
            if matched:
                logger.info("⚡ [FAST ROUTE] Nhận diện Intent tức thì (%s) -> Tiết kiệm 1 lượt gọi LLM", [s.name for s in matched])
                return matched
            
        system_instruction = (
            "Bạn là một Skill Router của hệ thống AI Office Assistant kết nối Odoo Cloud ERP.\n"
            "Nhiệm vụ: phân tích yêu cầu người dùng và tài liệu đính kèm (nếu có), "
            "sau đó chọn ra TÊN các skill cần thiết nhất để hoàn thành yêu cầu.\n\n"
            "QUY TẮC ĐỊNH TUYẾN DỮ LIỆU DOANH NGHIỆP:\n"
            "1. Nếu người dùng hỏi/tra cứu về khách hàng, đối tác, công ty, bệnh viện, nhà cung cấp, liên hệ, danh bạ: BẮT BUỘC chọn 'get_partners_and_customers'.\n"
            "2. Nếu người dùng hỏi/tra cứu về sản phẩm, hàng hóa, thiết bị, giá bán, tồn kho: BẮT BUỘC chọn 'get_products_and_inventory'.\n"
            "3. Nếu người dùng hỏi/tra cứu về đơn hàng bán, báo giá, doanh số: BẮT BUỘC chọn 'get_sale_orders'.\n"
            "4. Nếu người dùng hỏi/tra cứu về nhân sự, nhân viên, phòng ban, quy mô: BẮT BUỘC chọn 'get_company_employees'.\n"
            "5. Nếu người dùng hỏi/tra cứu về cơ hội kinh doanh, pipeline, leads, deal: BẮT BUỘC chọn 'get_crm_pipeline'.\n"
            "6. Nếu người dùng hỏi/tra cứu về bất kỳ đối tượng Odoo nào khác (như đơn mua hàng 'purchase.order', phiếu xuất nhập kho 'stock.picking', hóa đơn 'account.move', hoặc bất kỳ bảng dữ liệu ERP nào): BẮT BUỘC chọn 'query_odoo_records'.\n"
            "7. Chỉ chọn 'knowledge_answer' hoặc 'semantic_search' khi người dùng hỏi về tài liệu đính kèm, quy chế, nội quy, chính sách công ty trong Kho Tri Thức.\n"
            "8. Nếu người dùng yêu cầu xuất excel/báo cáo bảng tính, phải kèm 'export_data_to_excel'.\n"
            "Chỉ trả về danh sách các tên skill, phân tách bằng dấu phẩy. Không giải thích thêm.\n\n"
            f"Danh sách Skill hiện có:\n{self._get_skill_catalog()}"
        )
        
        # Thêm thông tin file đính kèm để router quyết định
        prompt = f"Yêu cầu: {query}\n"
        if context.attachments:
            prompt += f"Có {len(context.attachments)} file đính kèm. VD: {context.attachments[0].filename}\n"
            
        try:
            import asyncio
            from app.core.config import get_settings
            model_name = get_settings().DEFAULT_LLM_MODEL or "gemini-3.5-flash-lite"
            response = await asyncio.to_thread(
                self.ai_client.models.generate_content,
                model=model_name,
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
        
        # Domain keywords mapping
        if any(k in query_lower for k in [
            "liên hệ", "khách hàng", "đối tác", "contact", "partner", "customer",
            "công ty", "bệnh viện", "doanh nghiệp", "nhà cung cấp", "phòng khám"
        ]):
            try:
                selected.add(self.registry.get_skill("get_partners_and_customers"))
            except Exception:
                pass

        if any(k in query_lower for k in [
            "sản phẩm", "hàng hóa", "product", "tồn kho", "giá bán", "mặt hàng", "item", "sku", "thiết bị"
        ]):
            try:
                selected.add(self.registry.get_skill("get_products_and_inventory"))
            except Exception:
                pass

        if any(k in query_lower for k in [
            "đơn hàng", "báo giá", "order", "sales order", "đơn bán"
        ]):
            try:
                selected.add(self.registry.get_skill("get_sale_orders"))
            except Exception:
                pass

        if any(k in query_lower for k in [
            "đơn mua", "mua hàng", "purchase", "phiếu kho", "xuất kho", "nhập kho", "stock", "vận chuyển", "hóa đơn", "invoice", "tài chính"
        ]):
            try:
                selected.add(self.registry.get_skill("query_odoo_records"))
            except Exception:
                pass

        if any(k in query_lower for k in ["nhân sự", "nhân viên", "thành viên", "employee", "staff"]):
            try:
                selected.add(self.registry.get_skill("get_company_employees"))
            except Exception:
                pass

        if any(k in query_lower for k in ["cơ hội", "pipeline", "lead", "deal", "bán hàng"]):
            try:
                selected.add(self.registry.get_skill("get_crm_pipeline"))
            except Exception:
                pass

        # Tra cứu tổng quát khi có "thông tin về" hoặc "cung cấp thông tin"
        if "thông tin về" in query_lower or "cung cấp thông tin" in query_lower:
            for sname in ["get_partners_and_customers", "get_products_and_inventory", "get_company_employees", "get_crm_pipeline", "query_odoo_records"]:
                try:
                    selected.add(self.registry.get_skill(sname))
                except Exception:
                    pass

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
