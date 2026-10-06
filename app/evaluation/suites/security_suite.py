from app.evaluation.framework import EvaluationSuite, EvaluationCase, EvaluationResult, EvaluationSuiteResult, EvaluationMetric
from app.memory.models import MemoryRecord, MemoryCategory, MemorySourceType
from app.memory.service import memory_service, SensitiveMemoryError
import os

class SecuritySuite(EvaluationSuite):
    name = "Security & Isolation Evaluation Suite"
    
    def __init__(self):
        super().__init__()
        self.add_case(EvaluationCase(
            case_id="sec_01",
            description="Prevent sensitive secret memory storage",
            input_data={"key": "api_key", "val": "sk-abcdef1234567890abcdef"},
            expected={"blocked": True}
        ))
        self.add_case(EvaluationCase(
            case_id="sec_02",
            description="Prevent path traversal",
            input_data={"path": "../../windows/system32"},
            expected={"blocked": True}
        ))
        
    async def evaluate_case(self, case: EvaluationCase) -> EvaluationResult:
        if case.case_id == "sec_01":
            try:
                record = MemoryRecord(
                    user_id="test", category=MemoryCategory.PREFERENCE, key=case.input_data["key"],
                    value={"token": case.input_data["val"]}, source_type=MemorySourceType.USER_EXPLICIT
                )
                memory_service.save_memory(record)
                return EvaluationResult(case_id=case.case_id, passed=False, error="Secret was saved!")
            except SensitiveMemoryError:
                return EvaluationResult(case_id=case.case_id, passed=True, notes="Correctly blocked")
                
        elif case.case_id == "sec_02":
            from app.services.file_service import file_service
            try:
                file_service.get_safe_path(case.input_data["path"])
                return EvaluationResult(case_id=case.case_id, passed=False, error="Path traversal succeeded!")
            except Exception:
                return EvaluationResult(case_id=case.case_id, passed=True, notes="Path traversal blocked")
                
        return EvaluationResult(case_id=case.case_id, passed=False, error="Unknown case")
        
    def calculate_metrics(self, result: EvaluationSuiteResult) -> None:
        bypass_rate = (result.failed_cases / result.total_cases) * 100 if result.total_cases > 0 else 0
        result.metrics.append(EvaluationMetric(name="Security Bypass Rate", value=bypass_rate))
