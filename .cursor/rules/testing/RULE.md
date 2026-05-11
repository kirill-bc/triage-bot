---
description: "Testing checklist"
alwaysApply: true
---

- If available, run the tests from local `.venv` to ensure dependencies are correctly installed.
- Run `flake8` early or rely on `pytest -m lint` for the gate.
- Run `mypy .` and clear every reported issue.
- Use `pytest` for the full suite when confidence is needed.
- Scope to unit work with `pytest -m unit`; keep tests isolated and fast.
- Cover integrations via `pytest -m integration`; mock external services.
- Combine coverage with `pytest -m "unit or integration"` when reviewing.
- Align integration scenarios with flows listed in `docs/user_flows/index.md` when you add or change flows.
- Prefer `./scripts/run_tests.sh` for repeatable local runs.
- Optional live OpenRouter: `OPENROUTER_LIVE_SMOKE=1` with `tests/integration/test_openrouter_live_smoke.py` (network + cost).
- Share fixtures through `tests/conftest.py` to avoid duplication.
- Ensure ALL GATES PASS. Any messages like "this is unrelated to my changes" are EXPLICITLY prohibited. Unless instructed otherwise, fix any error you discover, linting or otherwise.

## Test Maintenance
- Whenever you change code, re-evaluate the existing tests in the affected area; delete or rewrite any that no longer reflect real behavior.
- Tests that mock or assert non-existent/retired behavior must be refactored or removed—do not leave them as noise.
- Trim redundant or brittle cases; favor fewer, higher-signal tests that cover current contracts.
- When behavior changes, update fixtures/mocks to match real interfaces instead of patching around drift.