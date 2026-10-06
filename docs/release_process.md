# Release Process

Before any major release (e.g., P2 to Production), the following checklist MUST be executed.

## Release Checklist

- [ ] **Tests Pass**: Run `pytest tests/` and ensure all unit/integration tests pass.
- [ ] **Evaluation Pass**: Run `python -m app.evaluation --all` and verify `P2 QUALITY GATE: PASS`.
- [ ] **Security Pass**: Ensure Security Suite metrics show 0% Bypass Rate.
- [ ] **Database Integrity**: Verify SQLite schemas are compatible or migration scripts are supplied.
- [ ] **Backup Verified**: Confirm `memory.db` and `workflows.db` can be successfully backed up and restored.
- [ ] **Secrets Check**: Verify no hardcoded credentials exist in the codebase.
- [ ] **Provider Health**: Ensure `GET /status` returns healthy for active providers.

*If any Critical Metric fails during evaluation, the release is BLOCKED.*
