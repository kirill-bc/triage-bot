# Project memory

## 2026-05-13

- **Phase close (verification + docs reconciliation):** ran gates from `.venv`:
  `pytest -m lint`, `mypy .`, and `pytest -m "unit or integration"` (all green:
  5 lint tests passed; mypy clean on 51 files; unit+integration 161 passed, 1 skipped).
  Updated `README.md` with a Jira Automation scheduled-rule recipe (cadence + JQL +
  request body), explicit `ai-reviewed` lifecycle, and MVP limitations. Reconciled
  `TODO.md` §9 by marking architecture docs, local runbook, Jira automation setup,
  `ai-reviewed` lifecycle, and MVP limitations as complete.

- **Benchmark helpers moved under scripts/benchmark:** relocated
  `classification_benchmark.py` and `benchmark_summary.py` from repo root to
  `scripts/benchmark/` and removed root copies. Updated imports in benchmark scripts and
  unit tests to `scripts.benchmark.*`, extended package-layout guard, and updated
  lint/mypy targets plus README/TODO references. Added `scripts/__init__.py` and
  `scripts/benchmark/__init__.py` to resolve mypy duplicate-module discovery.

- **Jira REST prefix helper merged into adapters and cloud-id-only:** removed root
  `jira_rest_paths.py` and added `src/triage_service/adapters/jira_rest.py` with
  `jira_cloud_rest_v3_prefix(settings)`. Fetcher/executor/handler now rely only on
  `JIRA_CLOUD_ID` (no `JIRA_BASE_URL` fallback in triage paths). Updated unit tests,
  flake8/mypy paths, and README wording for triage env vars.

- **Policy loader + policy files moved under core package:** relocated
  `policy_context.py` to `src/triage_service/core/policy_context.py` and policy markdown files
  to `src/triage_service/core/policy/`. Updated imports in core, tests, and benchmark script;
  updated lint/mypy paths and packaging data (`core/policy/*.md`). Removed root
  `policy_context.py` and root `policy/*.md`.

- **Prompt files moved under core package:** relocated prompt builder to
  `src/triage_service/core/prompt_composer.py` and default templates to
  `src/triage_service/core/prompt_templates.json`; removed root copies. Updated
  imports (`triage_handler`), lint/mypy paths, and prompt-composer tests to use
  `triage_service.core.prompt_composer`.

- **Prompt templates externalized:** moved hardcoded prompt bodies out of `prompt_composer.py`
  into `prompt_templates.json` (JSON templates with placeholders for policy and issue fields).
  `prompt_composer` now loads templates from that file by default and supports
  `TRIAGE_PROMPT_TEMPLATES_PATH` for override/testing. Added unit coverage in
  `tests/unit/test_prompt_composer.py` to verify an external JSON path is honored.
  Verification: `pytest tests/unit/test_prompt_composer.py`, `pytest -m lint`,
  `mypy .`, and `pytest -m unit` all green.

- **Adapter module relocation (no root shims):** moved `jira_issue_fetcher.py`,
  `jira_action_executor.py`, and `openrouter_inference_client.py` to
  `src/triage_service/adapters/` and removed the root files. Updated imports across
  core modules, scripts, benchmark paths, API-facing code, and unit/integration tests
  to `triage_service.adapters.*`. Extended package-layout guard test with adapter module
  assertions and updated `pyproject.toml` + flake8 lint targets to new file paths.
  Verification: RED by failing `tests/unit/test_package_layout.py`, then green on targeted
  adapter-focused test slice, plus `pytest -m lint`, `mypy .`, and `pytest -m unit`.

- **Core module relocation (no root shims):** moved `triage_handler.py`, `triage_fallback.py`,
  `triage_mismatch.py`, and `triage_recommendation_parser.py` to
  `src/triage_service/core/` and removed the root files. Updated imports across API, CLI,
  executor, benchmark modules, and affected unit tests to use `triage_service.core.*`.
  Extended package-layout guard test to require the four modules under `core/` and assert
  the root files are absent.
  Verification: RED by failing `tests/unit/test_package_layout.py`, then green on targeted
  core-related unit slice, plus `pytest -m lint`, `mypy .`, and `pytest -m unit`.

- **API module relocation (no root shim):** moved `triage_api.py` to
  `src/triage_service/api/triage_api.py` and removed the root file. Updated imports in
  `tests/unit/test_post_triage.py` and `tests/unit/test_triage_inbound_debug.py`,
  switched dev server module target in `dev_tunnel.build_uvicorn_argv()` to
  `triage_service.api.triage_api:app` with `--app-dir src`, and aligned
  `tests/unit/test_dev_tunnel.py`. `pyproject.toml` now includes pytest `pythonpath = [".", "src"]`,
  mypy targets the new API file path, and setuptools includes `triage_service*` packages.
  Added guard test `test_triage_api_module_lives_under_api_package` to
  `tests/unit/test_package_layout.py`.
  Verification: RED import failure reproduced, then green on
  `pytest tests/unit/test_post_triage.py tests/unit/test_triage_inbound_debug.py tests/unit/test_dev_tunnel.py tests/unit/test_package_layout.py`,
  `pytest -m lint`, and `mypy .`.

- **Refactor scaffold start:** added package skeleton under `src/triage_service/` with
  `api`, `core`, `adapters`, and `observability` subpackages (each with `__init__.py`) and
  created `docs/architecture/overview.md` to define ownership boundaries and dependency direction
  for the migration. Guard test: `tests/unit/test_package_layout.py`.
  Verification: `pytest tests/unit/test_package_layout.py -q`, `pytest -m lint -q`,
  `mypy .`, `pytest -m unit -q` all green.

- **Phase close (commit):** `pytest -m lint`, `mypy .`, and `pytest -m "unit or integration"` all green after adding `jira_rest_paths.py` (`jira_rest_v3_site_prefix`: prefer **`JIRA_CLOUD_ID`** → `https://api.atlassian.com/ex/jira/{id}` over **`JIRA_BASE_URL`**), wiring the prefix through **`JiraIssueFetcher`**, **`JiraTriageActionExecutor`**, and sequential **`TriageHandler`** (same REST root for fetch vs transitions/comments/labels). **Settings / `.env.example`:** optional `JIRA_CLOUD_ID` documented for Atlassian gateway REST. **OpenRouter:** `OpenRouterInferenceClient` accepts optional extra JSON body fields (e.g. provider routing) from settings when added later — tests cover passthrough. **Benchmark tooling:** `classification_benchmark.py`, `benchmark_summary.py`, `scripts/benchmark/{build_benchmark_dataset,run_classification_benchmark,summarize_benchmark_rows}.py`, `data/issue_benchmark_dataset.csv` (+ bucket CSVs), unit tests for benchmark math and Jira URL helper. **`benchmark_runs/`** added to `.gitignore` for local JSONL/cache outputs. **`TODO.md`** Post-MVP section updated: harness marked delivered; benchmark CSV stays Bug-centric (no equal Story-bucket rebalance while Stories are out of scope). **`README.md`:** benchmark dataset build, run, and offline summarize sections (Jira `search/jql`, `nextPageToken`, 410 on legacy search).

## 2026-05-12

- **Phase close (commit):** `pytest -m lint`, `mypy .`, and `pytest -m "unit or integration"` all green.
  This phase bundles the `TriageSource` rename (`bug_created` / `priority_changed` / `manual_cli`),
  local dev tunnel (`dev_tunnel.py`, `scripts/run_dev_tunnel.py`, `tests/unit/test_dev_tunnel.py`),
  optional `TRIAGE_DEBUG_INBOUND` raw-body logging on `POST /triage` (`triage_api`, inbound debug tests),
  README *Local HTTP server and tunnel*, `uvicorn[standard]` under dev extras, and related unit test
  updates (`post_triage`, triage handler, executor, mismatch). **Product path** §3 TODO item
  (Jira Automation → hosted API) remains open until verified on a real tenant.

- **`POST /triage` `source` enum:** `TriageSource = Literal["bug_created", "priority_changed", "manual_cli"]`
  for Jira bug-creation vs priority-change automations and the local CLI. Replaces the older
  `scheduled_scan` value; payloads using the old string now fail validation (422). README,
  `specification.md`, `TODO.md`, and unit tests (`test_post_triage`, `test_triage_handler`,
  `test_jira_action_executor`, `test_triage_inbound_debug`) updated.
- **Local tunnel dev path:** `README.md` documents running `uvicorn triage_api:app --host 0.0.0.0 --port 8000`,
  exposing `POST /triage` via ngrok or Cloudflare Tunnel for Jira Automation during development, plus
  curl smoke and caveats (URL churn, Jira timeouts). `uvicorn[standard]` added to `pyproject.toml`
  optional `dev` extras so `.venv` installs the ASGI server with editable dev deps.
- **`scripts/run_dev_tunnel.py` / `dev_tunnel.py`:** loads repo `.env` via `python-dotenv`, starts
  `uvicorn triage_api:app`, then runs `ngrok http` or `cloudflared tunnel --url` (default ngrok);
  forwards SIGINT/SIGTERM to the tunnel process and terminates uvicorn on exit. Unit tests cover argv
  builders in `tests/unit/test_dev_tunnel.py`. Sets `TRIAGE_DEBUG_INBOUND=1` on the uvicorn child unless
  `--no-inbound-log`; `triage_api` logs raw `POST /triage` bodies to stderr before validation when
  that env var is set (`tests/unit/test_triage_inbound_debug.py`).

## 2026-05-11

- **Phase close (commit):** §3 **Jira action executor** is implemented in `jira_action_executor.py`
  (`JiraTriageActionExecutor`): `ai-reviewed` on every successful triage; mismatch labels
  (`ai-likely-story` / `ai-priority-mismatch`) and a terse **TriageBot** templated
  ADF comment on mismatch only (no numeric confidence in Jira; optional reporter @mention when
  `FetchedIssue.reporter_account_id` is set). `TriageFailure` → no labels and no comment.
  `build_default_triage_handler()` wires the executor when `JIRA_BASE_URL` and `JIRA_USER_EMAIL` are
  set. `prompt_composer` frames **TriageBot** with direct `reason` guidance for Jira copy.
  `pytest -m lint`, `mypy .`, `pytest -m "unit or integration"` all green for close-phase.
- **TODO structure (historical):** A Forge-app backlog section was added to `TODO.md` on 2026-05-11 then **removed**: integration stays **Jira Cloud REST + service account** (gateway `JIRA_CLOUD_ID` or site URL, API token) with Automation calling `POST /triage`, not an Atlassian Forge app.
- **Phase close (commit, earlier same day):** §2 core backend: synchronous `TriageHandler`,
  sequential classification → optional priority, `POST /triage` with `scheduled_scan` and
  `manual_cli`, `scripts/run_triage_cli.py`, strict parsing without model `recommended_action`, and
  `triage_mismatch.compute_mismatch_flags`. §3 Jira executor followed in a later commit on this date
  (see bullet above).
- **Mismatch flags (no model ``recommended_action``):** ``TriageRecommendation`` drops
  ``recommended_action``; prompts ask only for type/reason or priority/reason. Parser strips legacy
  ``recommended_action`` from LLM JSON. ``triage_mismatch.compute_mismatch_flags`` →
  ``TriageMismatchFlags`` (``type_mismatch``, ``priority_mismatch``; Story path never sets priority
  mismatch). Tests: ``tests/unit/test_triage_mismatch.py``; spec/TODO/README updated.
- **Local manual CLI:** `triage_manual_cli.py` — `infer_project_from_issue_key()` parses standard
  `PROJ-123` keys; `run_cli_triage(issue_key, project=..., runner=...)` calls
  `TriageRunner.run_sync(..., "manual_cli")`. `main()` loads `.env` from repo root, validates settings,
  prints JSON (`completed` + `recommendation` or `failed` + `failure`), exit codes `0` / `1` / `2`.
  Wrapper: `scripts/run_triage_cli.py`. API: `TriageSource` now includes `"manual_cli"`.
  Tests: `tests/unit/test_triage_manual_cli.py`, `test_post_triage_accepts_manual_cli_source`.
- **Synchronous triage handler:** `triage_handler.py` — `TriageHandler.run_sync(issue_key, project, source)`
  checks project against `TriageCoreConfig.allowed_projects`, fetches via `JiraIssueFetcher`, runs
  OpenRouter classification then optional priority (same sequential split as `prompt_composer`),
  parses with `parse_classification_step_text` / `parse_priority_step_text` / merge helpers in
  `triage_recommendation_parser.py`, maps errors via `fallback_for_exception`, and always calls
  `TriageActionExecutor.apply_triage_outcome` (default `NoOpTriageActionExecutor` until §3 executor).
  `ProjectNotAllowedError` → `TriageFailure` category `project_not_allowed`. `triage_api.create_app`
  takes optional `triage_handler_factory` for tests; `POST /triage` returns `status` `completed` /
  `failed` with `recommendation` or `failure`. Tests: `tests/unit/test_triage_handler.py`,
  `tests/unit/test_triage_sequential_parser.py`, extended `test_post_triage.py` / `test_triage_fallback.py`.
- **Phase close (commit):** pivot the trigger model from an event-driven webhook with a service-side
  5-minute delay to a **Jira-side scheduled JQL rule** that handles stabilization, dedupe, and
  retry-on-failure in one JQL expression. API contract: `event_type` → `source: Literal["scheduled_scan"]`.
  `core_config.py` drops `analysis_delay_seconds` + `dedupe_deferral_enabled`. `specification.md`
  rewrites `jira_automation_trigger` and `jira_action_executor` (`ai-reviewed` now applied on every
  successful triage so it can act as the dedupe marker the JQL relies on). `TODO.md` realigned
  (§2 single synchronous handler, §3 label rules, §5 backstop-window metric, §6 Jira automation
  runbook). `README.md` + `memory.md` updated. Next backlog focus: synchronous triage handler that
  composes fetcher → prompt composer → inference client → recommendation parser / fallback →
  action executor (`TODO.md` §2).
- **Integration model (locked):** Jira Cloud Automation **scheduled rule** (per-issue, no batching for
  MVP) is the only production trigger. Rule cadence ~5 min. Reference JQL:
  `project = <KEY> AND issuetype = Bug AND labels not in (ai-reviewed) AND created >= -30m AND created <= -5m`.
  `created <= -5m` = stabilization delay; `labels not in (ai-reviewed)` = dedupe (service applies that
  label on every successful triage); `created >= -30m` = backstop. Service is stateless — no in-process
  scheduler/queue. Failures (`TriageFailure`) leave the issue unlabeled → next scheduled scan retries
  automatically until success or ageout.
- **API contract (current):** `POST /triage` body is `{ issue_key, project, source }` where
  `source: Literal["scheduled_scan", "manual_cli"]`. The old `event_type` field (`issue_created` /
  `issue_updated`) is gone — nothing fires the webhook on a Jira event under the scheduled-rule model.
  `source` is a closed enum; `manual_cli` tags calls from `scripts/run_triage_cli.py` / local tooling. **Thin payload:** Jira sends only `issue_key + project + source`; service re-fetches latest
  issue state via `JiraIssueFetcher` (we already pay the Jira-auth cost for comment/label writes).
  Reference Jira Automation Send-web-request → Custom data body:
  `{ "issue_key": "{{issue.key}}", "project": "{{issue.project.key}}", "source": "scheduled_scan" }`.
  Tests: `tests/unit/test_post_triage.py`.
- **Label semantics (locked):** `ai-reviewed` is applied on **every successful triage**, mismatch or
  not — it is the dedupe marker the scheduled JQL depends on. Mismatch-specific labels keep their
  original meaning: `ai-likely-story` only when type mismatches; `ai-priority-mismatch` only when
  priority mismatches on the Bug path. Internal comment is posted only on mismatch. Operators force
  re-triage by removing `ai-reviewed` on the Jira issue.
- **Config cleanup:** `TriageCoreConfig` no longer carries `analysis_delay_seconds` or
  `dedupe_deferral_enabled` — both concerns moved to the Jira-side rule.
  `TRIAGE_ANALYSIS_DELAY_SECONDS` and `TRIAGE_DEDUPE_DEFERRAL_ENABLED` env vars are silently ignored
  (`BaseSettings extra="ignore"`). Only `allowed_projects` remains as a server-side allowlist safety
  net against a misconfigured Jira rule.
- **Phase close (commit):** strict triage model JSON parsing (`triage_recommendation_parser.py`,
  `TriageRecommendation`, `InvalidTriageRecommendationError`); pipeline failure contract
  (`triage_fallback.py`, `TriageFailure`, `fallback_for_exception`); unit tests for both;
  `pyproject.toml` / flake8 gate / `TODO.md` / `README.md` updated. Next backlog focus: scheduled-scan
  triage handler and local CLI runner (`TODO.md` §2).
- **Triage fallback:** `triage_fallback.py` — `TriageFailure` (frozen Pydantic, `extra="forbid"`,
  message stripped + non-empty) plus `fallback_for_exception(exc)`. Categories:
  `jira_fetch_failed` (← `JiraIssueFetchError`), `inference_failed` (← `OpenRouterInferenceError`),
  `invalid_model_output` (← `InvalidTriageRecommendationError`), `internal_error` (catch-all).
  Default message used when the source exception has a blank `str(exc)`. Phase 1 contract:
  action executors treat any `TriageFailure` as "do not post Jira comment/label" — failures are
  log/metric signal only. Tests: `tests/unit/test_triage_fallback.py`. Wired into pyproject
  `py-modules` + `mypy.files` and `tests/lint/test_flake8.py`.
- **Triage recommendation parser:** `triage_recommendation_parser.py` — `parse_triage_recommendation_text`
  / `parse_triage_recommendation_json` return frozen `TriageRecommendation` (Pydantic, `extra="forbid"`).
  Validates `Bug|Story`, Story → priority null/omitted only, Bug → `P0`–`P4`, `confidence` ∈ [0, 1],
  non-empty stripped `reason`. Mismatch flags: `triage_mismatch.compute_mismatch_flags` /
  `TriageMismatchFlags` (`type_mismatch`, `priority_mismatch`); legacy `recommended_action` in LLM JSON is stripped at parse.
  Module docstring documents merged `confidence` as the last inference that ran when the service
  merges two steps. Errors: `InvalidTriageRecommendationError`. Tests:
  `tests/unit/test_triage_recommendation_parser.py`.
- **Phase close (commit):** OpenRouter inference client + `OPENROUTER_MODEL` settings; optional
  `OPENROUTER_LIVE_SMOKE` integration ping; `max_tokens` on chat completions; removed Playwright/E2E
  scaffold (`run_e2e_tests.sh`, `pytest` `e2e` marker, `tests/e2e/`); `run_tests.sh full` delegates to
  `all`; docs/spec/user flows and Cursor prompts aligned with Lambda-shaped service.
- **OpenRouter client:** `openrouter_inference_client.py` — `OpenRouterInferenceClient(settings, client=...)`.
  `chat_completion(messages, temperature=..., max_tokens=...)` POSTs to
  `https://openrouter.ai/api/v1/chat/completions` with `model=settings.openrouter_model`
  (`OPENROUTER_MODEL`, default `openai/gpt-4o-mini`) and Bearer `OPENROUTER_API_KEY`. Optional
  `max_tokens` is forwarded when set. Raises `OpenRouterInferenceError` on HTTP errors or empty
  assistant `content`.
  Tests: `tests/unit/test_openrouter_inference_client.py`. Optional live ping:
  `tests/integration/test_openrouter_live_smoke.py` runs when `OPENROUTER_LIVE_SMOKE=1`
  (uses `load_settings()` + real HTTPS).
- **Prompt composer:** `prompt_composer.py` exposes `compose_classification_prompt(policy, issue)` (bug policy + issue
  context only) and `compose_priority_prompt(policy, issue)` (priority policy + issue only). Unit tests:
  `tests/unit/test_prompt_composer.py`. Keeps step (1) and step (2) inputs separate so priority text is not always-on.
- **Triage design (docs):** `specification.md` and `TODO.md` describe **sequential** inference: (1) Bug vs Story using
  bug policy only; (2) if Bug, second call for P0–P4 using priority policy. Story outcome skips priority; Jira-facing
  actions are **advisory** internal comments and labels on mismatch only (no automatic Jira field mutation in Phase 1).
  API shape: `recommended_priority` null or omitted when recommendation is Story.
- **Policy files:** `policy/bug_definition.md` and `policy/priority_definition.md` are org triage text (plain, model-oriented).
  Loader: `policy_context.py` (`tests/unit/test_policy_context.py`).

## 2026-05-08

- Project layout: flat — `settings.py`, `core_config.py`, `jira_issue_fetcher.py`, `policy_context.py`, and `triage_api.py`
  at the repo root next to `tests/` and `policy/`.
  `pyproject.toml` uses `[tool.setuptools] py-modules` so `pip install -e ".[dev]"` works; pytest `pythonpath = ["."]`
  still supports running without an editable install.
- `triage_api.create_app()`: FastAPI app with `POST /triage` JSON body `issue_key`, `project`, `source` (required,
  non-empty strings); `source` is `Literal["scheduled_scan"]` (422 otherwise). Responds with those fields plus
  `status` placeholder `accepted`. Tests: `tests/unit/test_post_triage.py`. *(Superseded the original
  `event_type: issue_created|issue_updated` shape on 2026-05-11 when the integration model moved to a Jira scheduled rule.)*
- Runtime deps include `fastapi` and `httpx` (TestClient / future HTTP clients). Prefer `.venv/bin/pip` for installs
  so the project venv is the only target.
- `settings.AppSettings` / `load_settings()`: load `.env` via `python-dotenv` (non-overriding);
  required `JIRA_API_KEY` and `OPENROUTER_API_KEY`; optional Jira base URL / user email and
  logging endpoint fields. `.env` is gitignored; `.env.example` documents variables.
- `core_config.TriageCoreConfig` / `load_triage_core_config()`: reads `TRIAGE_ALLOWED_PROJECTS`
  (comma-separated, defaults to `TJC,BC`). `allowed_projects` exposed as `@computed_field` so pydantic-settings reads it
  as a plain `str` (no JSON-decode issue). *(2026-05-11: dropped `TRIAGE_ANALYSIS_DELAY_SECONDS` and
  `TRIAGE_DEDUPE_DEFERRAL_ENABLED`; both concerns moved to the Jira-side scheduled rule.)*
- Lint gate: `pytest -m lint` runs flake8 on the application modules listed in
  `tests/lint/test_flake8.py`, `scripts/fetch_jira_issue.py`, and `tests/`.
- Type gate: `mypy .` runs strict on application modules at repo root plus `tests/`; `typing-extensions>=4.0` declared as runtime dep.
- `scripts/run_tests.sh` prepends `.venv/bin` to `PATH` when present. No Playwright/E2E harness:
  validation is unit + integration (mocks) plus optional live OpenRouter smoke.
- CI: `.github/workflows/ci.yml` runs three quality gates on every push/PR: `mypy .`, `pytest -m lint`,
  and `pytest -m "unit or integration"`. Python 3.10, installs with `pip install -e ".[dev]"`.
- `tests/lint/test_ci_workflow.py` guards that the workflow file exists and contains all three gate commands.
- `jira_issue_fetcher.JiraIssueFetcher`: `fetch(issue_key)` calls Jira REST v3
  `GET /rest/api/3/issue/{key}?fields=summary,description,issuetype,priority,reporter` with Basic auth
  (`JIRA_USER_EMAIL` + `JIRA_API_KEY`). Returns `FetchedIssue` (ADF description flattened to plain text).
  Raises `JiraIssueFetchError` on missing `JIRA_BASE_URL` / `JIRA_USER_EMAIL` or non-success HTTP.
  Tests: `tests/unit/test_jira_issue_fetcher.py`.
- Manual smoke: `scripts/fetch_jira_issue.py <ISSUE_KEY>` loads `.env` from repo root when present, then prints
  `FetchedIssue` as JSON (requires `JIRA_*` and `OPENROUTER_API_KEY` in settings).
- `policy_context.load_policy_context()`: reads UTF-8 `policy/bug_definition.md` and
  `policy/priority_definition.md` (`policy_dir=` for tests). Returns frozen `PolicyContext` (stripped text);
  raises `PolicyContextLoadError` if a file is missing. Tests: `tests/unit/test_policy_context.py`.
