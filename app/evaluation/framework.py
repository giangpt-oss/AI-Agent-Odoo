from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
import datetime

class EvaluationMetric(BaseModel):
    name: str
    value: float
    description: str = ""

class EvaluationCase(BaseModel):
    case_id: str
    description: str
    input_data: Dict[str, Any]
    expected: Dict[str, Any]

class EvaluationResult(BaseModel):
    case_id: str
    passed: bool
    actual: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    notes: str = ""

class EvaluationSuiteResult(BaseModel):
    suite_name: str
    total_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    metrics: List[EvaluationMetric] = Field(default_factory=list)
    results: List[EvaluationResult] = Field(default_factory=list)
    timestamp: str = Field(default_factory=lambda: datetime.datetime.now().isoformat())

class EvaluationSuite:
    name: str = "Base Suite"
    
    def __init__(self):
        self.cases: List[EvaluationCase] = []
        
    def add_case(self, case: EvaluationCase):
        self.cases.append(case)
        
    async def run(self) -> EvaluationSuiteResult:
        result = EvaluationSuiteResult(suite_name=self.name, total_cases=len(self.cases))
        for case in self.cases:
            res = await self.evaluate_case(case)
            result.results.append(res)
            if res.passed:
                result.passed_cases += 1
            else:
                result.failed_cases += 1
                
        self.calculate_metrics(result)
        return result
        
    async def evaluate_case(self, case: EvaluationCase) -> EvaluationResult:
        raise NotImplementedError()
        
    def calculate_metrics(self, result: EvaluationSuiteResult) -> None:
        pass
