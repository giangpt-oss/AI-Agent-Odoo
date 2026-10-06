from abc import ABC, abstractmethod
from typing import Any, Dict, List
import jsonschema
from jsonschema import validate
from app.models.skill import OperationType, RiskLevel, SkillCategory
from app.models.context import SkillExecutionContext
from app.core.exceptions import SkillValidationError

class BaseSkill(ABC):
    name: str = ""
    description: str = ""
    category: SkillCategory = SkillCategory.SYSTEM
    capabilities: List[str] = []
    input_schema: Dict[str, Any] = {}
    output_schema: Dict[str, Any] = {}
    risk_level: RiskLevel = RiskLevel.LOW
    priority: int = 0
    requires_confirmation: bool = False
    operation_type: OperationType = OperationType.READ

    @abstractmethod
    async def execute(self, context: SkillExecutionContext, **kwargs) -> Any:
        """Executes the skill's main logic."""
        pass

    def validate_input(self, kwargs: Dict[str, Any]) -> None:
        if not self.input_schema:
            return
        try:
            validate(instance=kwargs, schema=self.input_schema)
        except jsonschema.exceptions.ValidationError as e:
            raise SkillValidationError(f"Invalid input for skill {self.name}: {e.message}")

    def validate_output(self, output: Any) -> None:
        if not self.output_schema:
            return
        try:
            validate(instance=output, schema=self.output_schema)
        except jsonschema.exceptions.ValidationError as e:
            raise SkillValidationError(f"Invalid output from skill {self.name}: {e.message}")

    async def preview(self, context: SkillExecutionContext, **kwargs) -> Any:
        """Returns a preview of changes for side-effect operations."""
        if self.operation_type != OperationType.READ:
            raise NotImplementedError("Preview not implemented for this side-effect skill.")
        return "No changes to preview (READ operation)."

    async def rollback(self, context: SkillExecutionContext, **kwargs) -> Any:
        """Rolls back the operation if supported."""
        raise NotImplementedError("Rollback not supported for this skill.")
