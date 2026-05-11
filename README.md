# Jira Triage MVP

Jira Triage is an MVP service that accepts a triage trigger, fetches Jira issue data, and prepares the codebase for AI-assisted recommendations on issue type and priority. Planned analysis is **sequential**: classify Bug vs Story first; run priority suggestion only when the model says Bug (see `specification.md` and `TODO.md`).

## Project status

The repository currently includes:

- a working `POST /triage` API that validates the request, runs synchronous triage (fetch → classify → optional priority), and returns `status: completed|failed` with a recommendation or `TriageFailure` (also invocable locally via `scripts/run_triage_cli.py` with `source=manual_cli`). When `JIRA_BASE_URL` and `JIRA_USER_EMAIL` are configured, successful triage applies Jira labels/comments via `jira_action_executor` (see below); failures do not touch the issue.
- Jira issue fetch support (`summary`, `description`, `issue type`, `priority`, `reporter`)
- bundled **policy text** for model context (`policy/`) and a **`policy_context`** loader
- **prompt composer** for separate classification vs priority model inputs (`prompt_composer.py`)
- **strict model output parsing** (`triage_recommendation_parser.py`) and a **typed failure shape** for upstream and schema errors (`triage_fallback.py`)
- CI gates for linting, type checking, and unit/integration tests
- local helper scripts for repeatable test runs and manual Jira fetch smoke checks

See `TODO.md` for the active implementation backlog.

## Repository layout

- `triage_api.py`: FastAPI app, `POST /triage` request/response contract, and dependency-injectable triage runner
- `triage_handler.py`: synchronous pipeline, `TriageRunner` / `TriageActionExecutor` protocols; `build_default_triage_handler()` uses `JiraTriageActionExecutor` when Jira base URL and user email are set, otherwise a no-op executor
- `jira_issue_fetcher.py`: Jira REST client and response normalization (includes `reporter_account_id` when Jira returns it, for @mentions in mismatch comments)
- `jira_action_executor.py`: on success, applies `ai-reviewed` plus mismatch labels and a templated **TriageBot** ADF comment when needed; on `TriageFailure`, performs no Jira writes
- `openrouter_inference_client.py`: OpenRouter chat completions using `OPENROUTER_MODEL` (see `settings.py`)
- `settings.py`: environment-backed runtime settings
- `core_config.py`: triage policy/config defaults (project allowlist; stabilization delay and dedupe live in the Jira-side scheduled rule)
- `policy_context.py`: loads bug and priority definition text from `policy/` for prompts
- `prompt_composer.py`: builds classification-only and priority-only prompt strings from policy + issue
- `triage_recommendation_parser.py`: validates merged LLM JSON into `TriageRecommendation` (throws `InvalidTriageRecommendationError` when invalid)
- `triage_mismatch.py`: `compute_mismatch_flags(issue, recommendation)` for deterministic type/priority mismatch (Jira executor / comments)
- `triage_fallback.py`: `TriageFailure` plus `fallback_for_exception()` to map fetch/inference/parse errors to a stable category + message for orchestration
- `triage_manual_cli.py`: infer project from `PROJ-123` keys, run `TriageHandler.run_sync(..., "manual_cli")`, and `main()` for the CLI
- `policy/`: `bug_definition.md` and `priority_definition.md` (edit to match your org)
- `scripts/fetch_jira_issue.py`: manual CLI smoke script for a single Jira issue
- `scripts/run_tests.sh`: local entrypoint for the standard test workflow
- `tests/`: unit, integration, and lint test groups
- `docs/user_flows/`: flow identifiers for integration tests and manual checks

## Current implemented components

- **API layer**: `POST /triage` accepts `issue_key`, `project`, `source` (`scheduled_scan` for the Jira Automation scheduled-rule webhook; `manual_cli` for the local runner; closed `Literal`)
- **Validation**: rejects missing fields and unsupported `source` values; rejects projects outside `TRIAGE_ALLOWED_PROJECTS` with `TriageFailure` category `project_not_allowed`
- **Jira adapter**: fetches and flattens selected Jira issue fields
- **OpenRouter adapter**: `OpenRouterInferenceClient` posts chat completions using the configured model id
- **Recommendation parsing**: `parse_triage_recommendation_text()` enforces the merged triage JSON contract; step-specific helpers (`parse_classification_step_text`, `parse_priority_step_text`) support the sequential model calls before merging into `TriageRecommendation`
- **Failure mapping**: `fallback_for_exception()` maps fetch, inference, parse, `ProjectNotAllowedError`, and unexpected errors into `TriageFailure` (Phase 1: executors should not post Jira comments or labels on failure)
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

## Local full triage (CLI)

Run the same synchronous pipeline as `POST /triage` without Jira Automation (OpenRouter + Jira fetch use your `.env`):

```bash
.venv/bin/python scripts/run_triage_cli.py YOUR-123
```

Optional `--project TJC` overrides the project key inferred from the issue key (`TJC` from `TJC-123`). The handler receives `source="manual_cli"`; stdout is JSON with either `status: completed` and `recommendation` or `status: failed` and `failure`. Exit code `0` on completed triage, `1` on `TriageFailure`, `2` on settings validation errors.
