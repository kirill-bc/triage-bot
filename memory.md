# Project memory

## 2026-05-08

- Project layout: flat — `settings.py` and `core_config.py` live at the repo root next to `tests/`.
  No installable package; `pyproject.toml` still manages deps and dev tools.
  pytest `pythonpath = ["."]` makes root modules importable without install.
- `settings.AppSettings` / `load_settings()`: load `.env` via `python-dotenv` (non-overriding);
  required `JIRA_API_KEY` and `OPENROUTER_API_KEY`; optional Jira base URL / user email and
  logging endpoint fields. `.env` is gitignored; `.env.example` documents variables.
- `core_config.TriageCoreConfig` / `load_triage_core_config()`: reads `TRIAGE_ALLOWED_PROJECTS`
  (comma-separated, defaults to `TJC,BC`), `TRIAGE_ANALYSIS_DELAY_SECONDS` (int ≥ 0, default 300),
  `TRIAGE_DEDUPE_DEFERRAL_ENABLED` (bool, default False). `allowed_projects` exposed as
  `@computed_field` so pydantic-settings reads it as a plain `str` (no JSON-decode issue).
- Lint gate: `pytest -m lint` runs flake8 on `settings.py`, `core_config.py`, and `tests/`.
- Type gate: `mypy .` runs strict on those same files; `typing-extensions>=4.0` declared as runtime dep.
- `scripts/run_tests.sh` and `scripts/run_e2e_tests.sh` prepend `.venv/bin` to `PATH` when present.
- CI: `.github/workflows/ci.yml` runs three quality gates on every push/PR: `mypy .`, `pytest -m lint`,
  and `pytest -m "unit or integration"`. Python 3.10, installs with `pip install -e ".[dev]"`.
- `tests/lint/test_ci_workflow.py` guards that the workflow file exists and contains all three gate commands.
