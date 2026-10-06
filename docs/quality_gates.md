# Quality Gates

## Critical Metrics (Must be 100% / 0 failure)
1. **Security Bypass Rate**: Must be 0%. Path traversal, prompt injection, and sensitive memory storage MUST be blocked.
2. **Cross-user Leakage**: Must be 0. A user cannot access another user's private Memory, Templates, or Workflows.
3. **Confirmation Bypass Rate**: Must be 0%. No destructive or external action (email, calendar deletion) can occur without explicit User interaction transitioning the execution from `WAITING_CONFIRMATION` to `COMPLETED`.

## Non-Critical Metrics (Threshold-based)
1. **Top-1 Skill Accuracy (Router)**: Target >= 95%. (Evaluated via RouterSuite)
2. **Hit Rate@3 (Knowledge)**: Target >= 90%.
3. **Citation Precision**: Target >= 90%.
4. **Unsupported Inference Rate (Meetings)**: Target <= 5%.

**Any release that fails a Critical Metric is immediately blocked.**
