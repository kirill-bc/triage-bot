# Project memory

## 2026-05-08

- Project layout: flat — `settings.py`, `core_config.py`, and `triage_api.py` at the repo root next to `tests/`.
  `pyproject.toml` uses `[tool.setuptools] py-modules` so `pip install -e ".[dev]"` works; pytest `pythonpath = ["."]`
  still supports running without an editable install.
- `triage_api.create_app()`: FastAPI app with `POST /triage` JSON body `issue_key`, `project`, `event_type` (required,
  non-empty strings); `event_type` must be `issue_created` or `issue_updated` (422 otherwise). Responds with those fields
  plus `status` placeholder `accepted`. Tests: `tests/unit/test_post_triage.py`.
- Runtime deps include `fastapi` and `httpx` (TestClient / future HTTP clients). Prefer `.venv/bin/pip` for installs
  so the project venv is the only target.
- `settings.AppSettings` / `load_settings()`: load `.env` via `python-dotenv` (non-overriding);
  required `JIRA_API_KEY` and `OPENROUTER_API_KEY`; optional Jira base URL / user email and
  logging endpoint fields. `.env` is gitignored; `.env.example` documents variables.
- `core_config.TriageCoreConfig` / `load_triage_core_config()`: reads `TRIAGE_ALLOWED_PROJECTS`
  (comma-separated, defaults to `TJC,BC`), `TRIAGE_ANALYSIS_DELAY_SECONDS` (int ≥ 0, default 300),
  `TRIAGE_DEDUPE_DEFERRAL_ENABLED` (bool, default False). `allowed_projects` exposed as
  `@computed_field` so pydantic-settings reads it as a plain `str` (no JSON-decode issue).
- Lint gate: `pytest -m lint` runs flake8 on `settings.py`, `core_config.py`, `triage_api.py`, and `tests/`.
- Type gate: `mypy .` runs strict on those application modules plus `tests/`; `typing-extensions>=4.0` declared as runtime dep.
- `scripts/run_tests.sh` and `scripts/run_e2e_tests.sh` prepend `.venv/bin` to `PATH` when present.
- CI: `.github/workflows/ci.yml` runs three quality gates on every push/PR: `mypy .`, `pytest -m lint`,
  and `pytest -m "unit or integration"`. Python 3.10, installs with `pip install -e ".[dev]"`.
- `tests/lint/test_ci_workflow.py` guards that the workflow file exists and contains all three gate commands.
