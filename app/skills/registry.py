import logging
from typing import Dict, List, Optional
from app.skills.base import BaseSkill
from app.models.skill import SkillCategory, RiskLevel
from app.core.exceptions import SkillError, SkillNotFoundError

logger = logging.getLogger(__name__)

class SkillRegistry:
    def __init__(self):
        self._skills: Dict[str, BaseSkill] = {}

    def register(self, skill: BaseSkill) -> None:
        if not skill.name:
            raise SkillError("Skill must have a valid name.")
        if skill.name in self._skills:
            raise SkillError(f"Skill '{skill.name}' is already registered.")
        self._skills[skill.name] = skill
        logger.info(f"Registered skill: {skill.name} (Category: {skill.category.value})")

    def unregister(self, skill_name: str) -> None:
        if skill_name in self._skills:
            del self._skills[skill_name]

    def get_skill(self, skill_name: str) -> BaseSkill:
        if skill_name not in self._skills:
            raise SkillNotFoundError(f"Skill '{skill_name}' not found.")
        return self._skills[skill_name]

    def get_all_skills(self) -> List[BaseSkill]:
        return list(self._skills.values())

    def lookup(
        self,
        name: Optional[str] = None,
        category: Optional[SkillCategory] = None,
        capabilities: Optional[List[str]] = None,
        min_priority: Optional[int] = None,
        max_risk_level: Optional[RiskLevel] = None
    ) -> List[BaseSkill]:
        """
        Filter skills based on multiple criteria.
        Returns a list of skills sorted by priority (descending).
        """
        results = self.get_all_skills()

        if name:
            results = [s for s in results if name.lower() in s.name.lower()]
        
        if category:
            results = [s for s in results if s.category == category]
            
        if capabilities:
            # Match if the skill has ALL requested capabilities
            req_caps = set(c.lower() for c in capabilities)
            results = [
                s for s in results
                if req_caps.issubset(set(sc.lower() for sc in s.capabilities))
            ]
            
        if min_priority is not None:
            results = [s for s in results if s.priority >= min_priority]
            
        if max_risk_level is not None:
            # simple ordering comparison
            risk_order = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
            max_val = risk_order[max_risk_level]
            results = [s for s in results if risk_order.get(s.risk_level, 0) <= max_val]

        results.sort(key=lambda s: s.priority, reverse=True)
        return results

# Singleton registry
skill_registry = SkillRegistry()
