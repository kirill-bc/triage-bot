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
- Execute `pytest -m e2e` only when end-to-end behavior must be proven.
- Align `pytest -m e2e` coverage with the flows listed in docs/user_flows/index.md; add scenarios when new UF files appear.
- Prefer `./scripts/run_tests.sh` for repeatable local runs.
- Launch `./scripts/run_e2e_tests.sh` when browser flows are required; the script automatically starts/stops the server.
- Configure server startup via E2E_SERVER_COMMAND env var or disable with E2E_SERVER_DISABLED=true for non-web projects.
- Share fixtures through `tests/conftest.py` to avoid duplication.
- Ensure ALL GATES PASS. Any messages like "this is unrelated to my changes" are EXPLICITLY prohibited. Unless instructed otherwise, fix any error you discover, linting or otherwise.

## Test Maintenance
- Whenever you change code, re-evaluate the existing tests in the affected area; delete or rewrite any that no longer reflect real behavior.
- Tests that mock or assert non-existent/retired behavior must be refactored or removed—do not leave them as noise.
- Trim redundant or brittle cases; favor fewer, higher-signal tests that cover current contracts.
- When behavior changes, update fixtures/mocks to match real interfaces instead of patching around drift.