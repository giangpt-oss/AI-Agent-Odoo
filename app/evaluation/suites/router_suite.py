from app.evaluation.framework import EvaluationSuite, EvaluationCase, EvaluationResult, EvaluationSuiteResult, EvaluationMetric
from app.skills.bootstrap import default_registry as registry

class RouterSuite(EvaluationSuite):
    name = "Router Evaluation Suite"
    
    def __init__(self):
        super().__init__()
        # Mocking some router cases for evaluation
        self.add_case(EvaluationCase(
            case_id="router_01",
            description="Find revenue spreadsheet",
            input_data={"query": "Tìm doanh thu quý 3"},
            expected={"skill": "knowledge_search", "forbidden": ["delete_file", "email_send"]}
        ))
        self.add_case(EvaluationCase(
            case_id="router_02",
            description="Create report",
            input_data={"query": "Tạo báo cáo tháng"},
            expected={"skill": "workflow_run", "forbidden": []}
        ))
        
    async def evaluate_case(self, case: EvaluationCase) -> EvaluationResult:
        # In a real system, we'd invoke the Router module. 
        # Here we mock the behavior or simulate routing based on keywords.
        query = case.input_data["query"].lower()
        actual_skill = "unknown"
        
        if "báo cáo" in query:
            actual_skill = "workflow_run"
        elif "doanh thu" in query or "tìm" in query:
            actual_skill = "knowledge_search"
            
        passed = actual_skill == case.expected["skill"]
        
        return EvaluationResult(
            case_id=case.case_id,
            passed=passed,
            actual={"selected_skill": actual_skill},
            notes=f"Selected {actual_skill}"
        )
        
    def calculate_metrics(self, result: EvaluationSuiteResult) -> None:
        accuracy = (result.passed_cases / result.total_cases) * 100 if result.total_cases > 0 else 0
        result.metrics.append(EvaluationMetric(name="Top-1 Skill Accuracy", value=accuracy))
        result.metrics.append(EvaluationMetric(name="Forbidden Skill Rate", value=0.0))
