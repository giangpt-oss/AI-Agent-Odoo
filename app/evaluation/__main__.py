import asyncio
import argparse
from typing import List
from app.evaluation.framework import EvaluationSuite, EvaluationSuiteResult
from app.evaluation.suites.router_suite import RouterSuite
from app.evaluation.suites.security_suite import SecuritySuite

suites_map = {
    "router": RouterSuite(),
    "security": SecuritySuite()
}

async def run_suites(suite_names: List[str]):
    results: List[EvaluationSuiteResult] = []
    for name in suite_names:
        if name in suites_map:
            print(f"Running suite: {name}...")
            res = await suites_map[name].run()
            results.append(res)
        else:
            print(f"Unknown suite: {name}")
            
    # Generate simple markdown report
    with open("docs/evaluation_report.md", "w", encoding="utf-8") as f:
        f.write("# P2D Evaluation Report\n\n")
        all_passed = True
        
        for res in results:
            f.write(f"## {res.suite_name}\n")
            f.write(f"- Total Cases: {res.total_cases}\n")
            f.write(f"- Passed: {res.passed_cases}\n")
            f.write(f"- Failed: {res.failed_cases}\n\n")
            
            f.write("### Metrics\n")
            for m in res.metrics:
                f.write(f"- **{m.name}**: {m.value}\n")
            
            if res.failed_cases > 0:
                all_passed = False
                f.write("\n### Failed Cases\n")
                for r in res.results:
                    if not r.passed:
                        f.write(f"- {r.case_id}: {r.error or r.notes}\n")
            f.write("\n---\n\n")
            
        f.write("## Quality Gate Assessment\n")
        if all_passed:
            f.write("**P2 QUALITY GATE: PASS**\n")
        else:
            f.write("**P2 QUALITY GATE: FAIL**\n")
            
    print("Evaluation complete. Report generated at docs/evaluation_report.md")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", action="append", help="Suite to run", default=[])
    parser.add_argument("--all", action="store_true", help="Run all suites")
    args = parser.parse_args()
    
    to_run = list(suites_map.keys()) if args.all or not args.suite or "all" in args.suite else args.suite
    asyncio.run(run_suites(to_run))
