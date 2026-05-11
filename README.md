# Jira Triage MVP

Jira Triage is an MVP service that accepts a triage trigger, fetches Jira issue data, and prepares the codebase for AI-assisted recommendations on issue type and priority. Planned analysis is **sequential**: classify Bug vs Story first; run priority suggestion only when the model says Bug (see `specification.md` and `TODO.md`).

## Project status

The repository currently includes:

- a working `POST /triage` API contract with request validation
- Jira issue fetch support (`summary`, `description`, `issue type`, `priority`, `reporter`)
- bundled **policy text** for model context (`policy/`) and a **`policy_context`** loader
- **prompt composer** for separate classification vs priority model inputs (`prompt_composer.py`)
- **strict model output parsing** (`triage_recommendation_parser.py`) and a **typed failure shape** for upstream and schema errors (`triage_fallback.py`)
- CI gates for linting, type checking, and unit/integration tests
- local helper scripts for repeatable test runs and manual Jira fetch smoke checks

See `TODO.md` for the active implementation backlog.

## Repository layout

- `triage_api.py`: FastAPI app and `POST /triage` request contract
- `jira_issue_fetcher.py`: Jira REST client and response normalization
- `openrouter_inference_client.py`: OpenRouter chat completions using `OPENROUTER_MODEL` (see `settings.py`)
- `settings.py`: environment-backed runtime settings
- `core_config.py`: triage policy/config defaults (projects, delay, dedupe flag)
- `policy_context.py`: loads bug and priority definition text from `policy/` for prompts
- `prompt_composer.py`: builds classification-only and priority-only prompt strings from policy + issue
- `triage_recommendation_parser.py`: validates merged LLM JSON into `TriageRecommendation` (throws `InvalidTriageRecommendationError` when invalid)
- `triage_fallback.py`: `TriageFailure` plus `fallback_for_exception()` to map fetch/inference/parse errors to a stable category + message for orchestration
- `policy/`: `bug_definition.md` and `priority_definition.md` (edit to match your org)
- `scripts/fetch_jira_issue.py`: manual CLI smoke script for a single Jira issue
- `scripts/run_tests.sh`: local entrypoint for the standard test workflow
- `tests/`: unit, integration, and lint test groups
- `docs/user_flows/`: flow identifiers for integration tests and manual checks

## Current implemented components

- **API layer**: `POST /triage` accepts `issue_key`, `project`, `event_type`
- **Validation**: rejects missing fields and unsupported event values
- **Jira adapter**: fetches and flattens selected Jira issue fields
- **OpenRouter adapter**: `OpenRouterInferenceClient` posts chat completions using the configured model id
- **Recommendation parsing**: `parse_triage_recommendation_text()` enforces the merged triage JSON contract (`Bug`/`Story`, nullable priority on Story path, `P0`–`P4` on Bug path, confidence bounds, `recommended_action` enum)
- **Failure mapping**: `fallback_for_exception()` turns `JiraIssueFetchError`, `OpenRouterInferenceError`, `InvalidTriageRecommendationError`, and unexpected errors into `TriageFailure` (Phase 1: executors should not post Jira comments or labels on failure)
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
   - optional: `OPENROUTER_MODEL` (defaults to `openai/gpt-4o-mini` if unset)
   - optional: `JIRA_BASE_URL`, `JIRA_USER_EMAIL`, logging values
3. Run quality gates:
   - `.venv/bin/pytest -m lint`
   - `.venv/bin/mypy .`
   - `.venv/bin/pytest -m "unit or integration"`
4. Or run the scripted workflow:
   - `./scripts/run_tests.sh`
5. Optional live OpenRouter check (uses your `.env` keys, network, and a small billed call). Uses
   `max_tokens=256` so reasoning-heavy models still emit `content`:
   - `OPENROUTER_LIVE_SMOKE=1 .venv/bin/pytest tests/integration/test_openrouter_live_smoke.py -m integration`

## Manual Jira smoke check

Run:

```bash
.venv/bin/python scripts/fetch_jira_issue.py YOUR-123
```

The script calls Jira REST and prints normalized JSON for the issue.
