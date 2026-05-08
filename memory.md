# Project memory

## 2026-05-08

- Added `pyproject.toml` with setuptools `src/` layout, optional `[dev]` extras (pytest, pytest-cov, flake8, mypy), pytest markers, and mypy config (strict app code, relaxed tests).
- Introduced `src/jira_triage` with `__version__`; unit tests assert import contract.
- `scripts/run_tests.sh` and `scripts/run_e2e_tests.sh` prepend `.venv/bin` to `PATH` when present; `scripts/setup.sh` creates `.venv` and runs `pip install -e ".[dev]"`.
- E2E server management defaults to disabled until an HTTP app exists; placeholder e2e test is skipped.
