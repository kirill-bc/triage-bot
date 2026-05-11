# Jira Triage MVP

Jira Triage is an MVP service that accepts a triage trigger, fetches Jira issue data, and prepares the codebase for AI-assisted recommendations on issue type and priority. Planned analysis is **sequential**: classify Bug vs Story first; run priority suggestion only when the model says Bug (see `specification.md` and `TODO.md`).

## Project status

The repository currently includes:

- a working `POST /triage` API contract with request validation
- Jira issue fetch support (`summary`, `description`, `issue type`, `priority`, `reporter`)
- bundled **policy text** for model context (`policy/`) and a **`policy_context`** loader
- CI gates for linting, type checking, and unit/integration tests
- local helper scripts for repeatable test runs and manual Jira fetch smoke checks

See `TODO.md` for the active implementation backlog.

## Repository layout

- `triage_api.py`: FastAPI app and `POST /triage` request contract
- `jira_issue_fetcher.py`: Jira REST client and response normalization
- `settings.py`: environment-backed runtime settings
- `core_config.py`: triage policy/config defaults (projects, delay, dedupe flag)
- `policy_context.py`: loads bug and priority definition text from `policy/` for prompts
- `policy/`: `bug_definition.md` and `priority_definition.md` (edit to match your org)
- `scripts/fetch_jira_issue.py`: manual CLI smoke script for a single Jira issue
- `scripts/run_tests.sh`: local entrypoint for the standard test workflow
- `tests/`: unit, integration, lint, and e2e test groups
- `docs/user_flows/`: flow mapping used by e2e coverage planning

## Current implemented components

- **API layer**: `POST /triage` accepts `issue_key`, `project`, `event_type`
- **Validation**: rejects missing fields and unsupported event values
- **Jira adapter**: fetches and flattens selected Jira issue fields
- **Policy context**: `load_policy_context()` reads UTF-8 definitions from `policy/` (override `policy_dir` in tests)
- **Tooling**: `flake8`, `mypy`, and pytest marker-based gates wired in CI

## Local development

From repository root:

1. Create and activate `.venv`, then install dependencies:
   - `python -m venv .venv`
   - `.venv/bin/pip install -e ".[dev]"`
2. Configure `.env` with required keys:
   - `JIRA_API_KEY`
   - `OPENROUTER_API_KEY`
   - optional: `JIRA_BASE_URL`, `JIRA_USER_EMAIL`, logging values
3. Run quality gates:
   - `.venv/bin/pytest -m lint`
   - `.venv/bin/mypy .`
   - `.venv/bin/pytest -m "unit or integration"`
4. Or run the scripted workflow:
   - `./scripts/run_tests.sh`

## Manual Jira smoke check

Run:

```bash
.venv/bin/python scripts/fetch_jira_issue.py YOUR-123
```

The script calls Jira REST and prints normalized JSON for the issue.
