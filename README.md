# Jira Triage MVP

Jira Triage is an MVP service that accepts a triage trigger, fetches Jira issue data, and prepares the codebase for AI-assisted recommendations on issue type and priority for **Bug** issues. Planned analysis is **sequential**: classify Bug vs Story first (bug policy only); run priority suggestion only when the model says Bug (see `specification.md` and `TODO.md`).

## Project status

The repository currently includes:

- a working `POST /triage` API that validates the request, runs synchronous triage (fetch → classify → optional priority), and returns `run_id` (per-request UUID), `status: completed|failed`, and a recommendation or `TriageFailure` (also invocable locally via `scripts/run_triage_cli.py` with `source=manual_trigger`, which generates its own `run_id` for the CLI attempt). When `JIRA_CLOUD_ID` and `JIRA_USER_EMAIL` are configured, successful triage applies Jira labels/comments via `src/triage_service/adapters/jira_action_executor.py` (see below); failures do not touch the issue.
- Jira issue fetch support (`summary`, `description`, `issue type`, `priority`, `reporter`, and optional `reproduction_steps`)
- bundled **policy text** for model context (`src/triage_service/core/policy/`) and a **`policy_context`** loader
- **prompt composer** for separate classification vs priority model inputs (`src/triage_service/core/prompt_composer.py`)
- **strict model output parsing** (`src/triage_service/core/triage_recommendation_parser.py`) and a **typed failure shape** for upstream and schema errors (`src/triage_service/core/triage_fallback.py`)
- CI gates for linting, type checking, and unit/integration tests
- local helper scripts for repeatable test runs and manual Jira fetch smoke checks

See `TODO.md` for the active implementation backlog.

## Repository layout

- `src/triage_service/api/triage_api.py`: FastAPI app, `GET /health` (liveness + readiness when `load_settings()` succeeds, plus safe `observability` flags including Langfuse export env readiness), `POST /triage` request/response contract, and dependency-injectable triage runner
- `src/triage_service/core/triage_handler.py`: synchronous pipeline, `TriageRunner` / `TriageActionExecutor` protocols; `build_default_triage_handler()` uses `JiraTriageActionExecutor` when `JIRA_CLOUD_ID` and `JIRA_USER_EMAIL` are set, otherwise a no-op executor
- `src/triage_service/adapters/jira_issue_fetcher.py`: Jira REST client and response normalization (includes `reporter_account_id` when Jira returns it, optional `reproduction_steps` from `TRIAGE_JIRA_REPRODUCTION_STEPS_FIELD_ID` when present, with fallback extraction from `description`)
- `src/triage_service/adapters/jira_action_executor.py`: on success, applies `triagebot-reviewed` plus mismatch labels and a templated **TriageBot** ADF comment when needed; on `TriageFailure`, performs no Jira writes
- `src/triage_service/adapters/openrouter_inference_client.py`: OpenRouter chat completions using `OPENROUTER_MODEL` (see `src/triage_service/core/settings.py`)
- `src/triage_service/core/settings.py`: environment-backed runtime settings, including `TRIAGE_ALLOWED_PROJECTS` allowlist parsing
- `src/triage_service/core/policy_context.py`: loads bug and priority definition text from `src/triage_service/core/policy/` for prompts
- `src/triage_service/core/prompt_composer.py`: builds classification-only and priority-only prompt strings from policy + issue
- `src/triage_service/core/triage_recommendation_parser.py`: validates merged LLM JSON into `TriageRecommendation` (throws `InvalidTriageRecommendationError` when invalid)
- `src/triage_service/core/triage_mismatch.py`: `compute_mismatch_flags(issue, recommendation)` for deterministic type/priority mismatch (Jira executor / comments)
- `src/triage_service/core/triage_fallback.py`: `TriageFailure` plus `fallback_for_exception()` to map fetch/inference/parse errors to a stable category + message for orchestration
- `triage_manual_cli.py`: infer project from `PROJ-123` keys, run `TriageHandler.run_sync(..., source="manual_trigger", run_id=...)`, and `main()` for the CLI
- `dev_tunnel.py`: load repo `.env`, start `uvicorn`, then run `ngrok` or `cloudflared` for Jira-facing HTTPS during local development (`scripts/run_dev_tunnel.py`)
- `src/triage_service/core/policy/`: `bug_definition.md` and `priority_definition.md` (edit to match your org)
- `scripts/fetch_jira_issue.py`: manual CLI smoke script for a single Jira issue
- `scripts/benchmark/build_benchmark_dataset.py`: optional Jira → CSV sampler for benchmark buckets (changelog-derived rows)
- `scripts/benchmark/run_classification_benchmark.py`: run the same classify → optional priority pipeline over `data/issue_benchmark_dataset.csv` for one or more OpenRouter models; writes JSONL per model plus `summary.json` (NoOp Jira executor; no labels/comments)
- `scripts/benchmark/summarize_benchmark_rows.py`: offline aggregator over any folder of `rows_*.jsonl` benchmark outputs — per-bucket and overall accuracy, latency stats, issue-type confusion matrix, and failure breakdown; optional folder-level JSON dump
- `scripts/benchmark/classification_benchmark.py`: CSV loader and bucket-aware scoring (stable bugs vs human-corrected type/priority)
- `scripts/benchmark/benchmark_summary.py`: pure-logic helpers used by `summarize_benchmark_rows.py` (JSONL parsing, latency/failure aggregation, summary serialization)
- `scripts/run_dev_tunnel.py`: uvicorn + tunnel helper (uses `dev_tunnel.main`)
- `scripts/run_container_tunnel.sh`: build/run container with `.env` secrets, post a live `/triage` payload, then expose the container via `cloudflared` (or `ngrok`) for Jira Automation testing
- `scripts/run_tests.sh`: local entrypoint for the standard test workflow
- `tests/`: unit, integration, and lint test groups
- `docs/user_flows/`: flow identifiers for integration tests and manual checks

## Current implemented components

- **API layer**: `GET /health` returns JSON including `observability` when `ready` is true: Langfuse key presence, **`langfuse_export_env_ready`** (keys plus SDK export env: not `LANGFUSE_TRACING_ENABLED=false`, not `OTEL_SDK_DISABLED=true`), and audit flags. That does not prove traces reached Langfuse (use UI + `LANGFUSE_DEBUG` for export failures). HTTP 503 with `ready:false` when validation fails (use for Kubernetes readiness). `POST /triage` accepts `issue_key`, `project`, `source` (`bug_created` or `priority_changed` for Jira Automation; `manual_trigger` for the local runner; closed `Literal`) and requires header `X-Triage-Token` matching `TRIAGE_WEBHOOK_TOKEN`; JSON responses include a generated `run_id` for correlation with downstream observability
- **Validation**: rejects missing fields and unsupported `source` values; rejects projects outside `TRIAGE_ALLOWED_PROJECTS` with `TriageFailure` category `project_not_allowed`
- **Jira adapter**: fetches and flattens selected Jira issue fields, including optional reproduction steps
- **OpenRouter adapter**: `OpenRouterInferenceClient` posts chat completions using the configured model id
- **Recommendation parsing**: `parse_triage_recommendation_text()` enforces the merged triage JSON contract; step-specific helpers (`parse_classification_step_text`, `parse_priority_step_text`) support the sequential model calls before merging into `TriageRecommendation`
- **Failure mapping**: `fallback_for_exception()` maps fetch, inference, parse, `ProjectNotAllowedError`, and unexpected errors into `TriageFailure` (Phase 1: executors should not post Jira comments or labels on failure)
- **Policy context**: `load_policy_context()` reads UTF-8 definitions from `src/triage_service/core/policy/` (override `policy_dir` in tests)
- **Tooling**: `flake8`, `mypy`, and pytest marker-based gates wired in CI

## Local development

From repository root:

1. Create and activate `.venv`, then install dependencies:
   - `python -m venv .venv`
   - `.venv/bin/pip install -e ".[dev]"`
2. Configure `.env` with required keys:
   - `JIRA_API_KEY`
   - `OPENROUTER_API_KEY`
   - `TRIAGE_WEBHOOK_TOKEN` (shared secret expected in inbound `X-Triage-Token` header on `POST /triage`)
   - optional: `OPENROUTER_MODEL` (defaults to `openai/gpt-4o-mini` if unset)
   - optional: `TRIAGE_PROMPT_TEMPLATES_PATH` (path to external JSON prompt templates; defaults to `src/triage_service/core/prompt_templates.json`)
   - optional: `JIRA_CLOUD_ID`, `JIRA_USER_EMAIL`, logging values
   - optional: `TRIAGE_JIRA_REPRODUCTION_STEPS_FIELD_ID` (defaults to `customfield_10251`; set empty to disable custom-field lookup and rely on description marker extraction)
   - optional: `TRIAGE_JIRA_HTTP_TIMEOUT_SECONDS` (per-attempt timeout for Jira REST, default `30`) and `TRIAGE_JIRA_HTTP_MAX_RETRIES` (extra attempts after a transient HTTP 429/502/503/504 or transport failure, default `2`, max `10`)
   - optional: `TRIAGE_OPENROUTER_HTTP_TIMEOUT_SECONDS` (per-attempt timeout for OpenRouter chat completions, default `60`) and `TRIAGE_OPENROUTER_HTTP_MAX_RETRIES` (same transient policy as Jira, default `2`, max `10`)
   - optional: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_BASE_URL` (when the first two are set, OpenRouter steps are traced in Langfuse: root span `triage_issue_pipeline` with nested `inference_*` generations; token usage and any cost fields returned by OpenRouter are forwarded onto those generation observations when present. Lifecycle audit events attach under that span when emitted during triage. Use `run_id` from the API or CLI response and the span metadata to correlate with logs. `POST /triage` and the manual CLI call `flush_inference_telemetry()` after each run so buffers are not stuck in short-lived processes)
   - optional audit routing and redaction (defaults: structured JSON logs **on**, Langfuse audit mirror **on** when Langfuse keys exist, model input redaction **on**, model output redaction **off**): `TRIAGE_AUDIT_STRUCTURED_LOG_ENABLED`, `TRIAGE_AUDIT_LANGFUSE_ENABLED`, `TRIAGE_AUDIT_REDACT_MODEL_INPUT`, `TRIAGE_AUDIT_REDACT_MODEL_OUTPUT`. Filter JSON log lines by `run_id` (API response field or CLI-generated UUID); in Langfuse, use `run_id` on the root span metadata (and nested observations) to align traces, generations, and audit events.
   - optional local smoke mode: `TRIAGE_LOCAL_MOCK_MODE` (`1`, `true`, `yes`, or `on`) switches triage into a deterministic local runner that skips Jira/OpenRouter calls. Intended only for local container smoke checks.
3. Run quality gates:
   - `.venv/bin/pytest -m lint`
   - `.venv/bin/mypy .`
   - `.venv/bin/pytest -m "unit or integration"`
4. Or run the scripted workflow (subcommand required):
   - `./scripts/run_tests.sh all` — lint, `mypy .`, unit, then integration
   - `./scripts/run_tests.sh lint` / `types` / `unit` / `integration` / `fast` (unit + integration) / `coverage` — see `./scripts/run_tests.sh help`
5. Local container smoke check (build image, run container, POST fixture payload to `/triage`, validate response shape):
   - `./scripts/run_container_smoke.sh`
   - Requires Docker daemon access.
6. Optional live OpenRouter check (uses your `.env` keys, network, and a small billed call). Uses
   `max_tokens=256` so reasoning-heavy models still emit `content`:
   - `OPENROUTER_LIVE_SMOKE=1 .venv/bin/pytest tests/integration/test_openrouter_live_smoke.py -m integration`

## Manual Jira smoke check

Run:

```bash
.venv/bin/python scripts/fetch_jira_issue.py YOUR-123
```

The script calls Jira REST and prints normalized JSON for the issue.

## Benchmark dataset (optional)

`scripts/benchmark/build_benchmark_dataset.py` is a standalone helper that queries Jira Cloud and writes a CSV of issue keys plus last priority / issue-type changelog transitions, grouped into benchmark buckets (misprioritized done bugs, stories converted from bugs, and “stable” bugs with no priority or issuetype history). It uses the same `.env` credentials as the triage app: **`JIRA_USER_EMAIL`**, **`JIRA_API_KEY`**, and **`JIRA_CLOUD_ID`**. Jira Cloud has removed **`GET /rest/api/3/search`** (HTTP 410); the script uses **`GET /rest/api/3/search/jql`** with **`nextPageToken`** pagination and **stops fetching further pages once each bucket reaches its configured target** (defaults: `--per-priority 10`, `--stories-from-bugs 50`, `--stable-bugs 50`). Example:

```bash
.venv/bin/python scripts/benchmark/build_benchmark_dataset.py -o benchmark_dataset.csv
```

Tune `--request-interval` if you need gentler pacing against Jira rate limits.

## Benchmark summary (offline)

`scripts/benchmark/summarize_benchmark_rows.py` post-processes any folder of `rows_*.jsonl` files produced by `run_classification_benchmark.py` (no Jira/OpenRouter access required). For each model file it prints per-bucket accuracy, overall accuracy, priority accuracy, latency stats (total / mean / median / p95 / min / max), the issue-type confusion matrix (with `_failed` for runs where the model produced no prediction), and a per-category failure breakdown. The trailing folder overview ranks models by overall accuracy. Useful for re-summarizing partial or interrupted runs and for diffing several runs in one go.

Summarize a single run folder:

```bash
.venv/bin/python scripts/benchmark/summarize_benchmark_rows.py \
    benchmark_runs/20260512T211133Z
```

Walk every nested run, suppress the per-model tables, and write a combined JSON:

```bash
.venv/bin/python scripts/benchmark/summarize_benchmark_rows.py \
    benchmark_runs --recursive --quiet \
    --output-json benchmark_runs/rows_summary.json
```

The per-model numbers match `summary.json` from the original benchmark run by reusing `aggregate_bucket_summaries`, `aggregate_overall`, and `confusion_matrix_issue_type` from `scripts/benchmark/classification_benchmark.py`.

## Local full triage (CLI)

Run the same synchronous pipeline as `POST /triage` without Jira Automation (OpenRouter + Jira fetch use your `.env`):

```bash
.venv/bin/python scripts/run_triage_cli.py YOUR-123
```

Optional `--project TJC` overrides the project key inferred from the issue key (`TJC` from `TJC-123`). The handler receives `source="manual_trigger"`; stdout is JSON with either `status: completed` and `recommendation` or `status: failed` and `failure`. Exit code `0` on completed triage, `1` on `TriageFailure`, `2` on settings validation errors.

## Jira Automation recipe (scheduled scan)

The production integration model is Jira Cloud Automation running on a schedule and calling
`POST /triage` once per matching issue.

- Rule cadence: every 5 minutes (or similar), aligned to the JQL window.
- Reference JQL:
  `project = TJC AND issuetype = Bug AND labels not in (triagebot-reviewed) AND created >= -30m AND created <= -5m`
- Rationale:
  - `created <= -5m`: stabilization delay before first triage attempt.
  - `labels not in (triagebot-reviewed)`: dedupe marker so successful issues drop out.
  - `created >= -30m`: retry backstop window for temporary failures.

For the **Send web request** action, use custom JSON data:

```json
{
  "issue_key": "{{issue.key}}",
  "project": "{{issue.project.key}}",
  "source": "bug_created"
}
```

Use `"source": "bug_created"` for a newly-created Bug rule and `"source": "priority_changed"`
for a priority-change rule.

Set request header `X-Triage-Token: <TRIAGE_WEBHOOK_TOKEN>` in the Jira Automation action.
Requests missing this header (or with the wrong value) receive `401 Unauthorized`.

### `triagebot-reviewed` lifecycle

- On successful triage (mismatch or not), the executor applies `triagebot-reviewed`.
- Jira mismatch comments are posted for Story reclassification or Bug **de-escalation** recommendations; Bug **prioritization** (for example `P2 -> P1`) stays in audit/API output and does not post a Jira comment.
- `triagebot-priority-mismatch` is applied only for Bug de-escalation mismatches.
- On triage failure (`status: failed` / `TriageFailure`), no labels or comments are posted.
- To force re-triage on an issue that already succeeded, remove `triagebot-reviewed` manually.
- If an issue is older than the JQL window and still has no `triagebot-reviewed`, treat it as a
  manual follow-up case and run triage via CLI or direct API call.

### MVP limitations

- Jira mutations are advisory only: no automatic issue-type or priority field update.
- Confidence is metadata for operators and API/audit output; it is not used as a direct
  mutation threshold.
- Retry/dedupe behavior is owned by Jira Automation JQL and labels, not by an internal queue.

## Local HTTP server and tunnel (Jira Automation → your laptop)

Use this when you want Jira Cloud **Send web request** to hit `POST /triage` on a machine that is not on the public internet. This is a **development** path only: URLs from free tunnel tiers change whenever you restart the tunnel, and Jira enforces HTTP timeouts (plan for roughly tens of seconds end-to-end including Jira fetch, OpenRouter, and Jira writes).

1. Install dev dependencies so the ASGI server is available: `pip install -e ".[dev]"` (includes `uvicorn`).
2. Install [ngrok](https://ngrok.com/docs/getting-started/) or [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) and ensure it is on your `PATH`.
3. From the repository root, run the bundled helper (loads `.env` from the repo root, starts uvicorn on `0.0.0.0`, then starts the tunnel so stdout shows the public URL):

   ```bash
   .venv/bin/python scripts/run_dev_tunnel.py
   ```

   Use Cloudflare instead of ngrok: `.venv/bin/python scripts/run_dev_tunnel.py --tunnel cloudflared`. Override bind port with `--port 8080` (and pass the same port to your tunnel if you run it manually).

   The helper sets **`TRIAGE_DEBUG_INBOUND=1`** for the uvicorn child process so **every `POST /triage` prints the raw body to stderr** (lines prefixed with `[TRIAGE_DEBUG_INBOUND]`) before FastAPI validates it. That shows exactly what Jira sent when you see HTTP 422 from missing `issue_key` / `project` / `source`. Pass **`--no-inbound-log`** to disable. For a plain uvicorn run, you can set the same variable yourself: `TRIAGE_DEBUG_INBOUND=1 .venv/bin/uvicorn triage_service.api.triage_api:app --app-dir src --host 0.0.0.0 --port 8000`.

   **Manual alternative:** run `.venv/bin/uvicorn triage_service.api.triage_api:app --app-dir src --host 0.0.0.0 --port 8000` in one terminal with `.env` exported, then `ngrok http 8000` or `cloudflared tunnel --url http://127.0.0.1:8000` in another.

4. In Jira Automation, set the web request URL to `{tunnel_base}/triage` (no trailing slash before `triage`). The body must be **your JSON** with `issue_key`, `project`, and `source` — use **Custom data** (or equivalent). Use `"source": "bug_created"` for a new-bug rule and `"source": "priority_changed"` for a priority-change rule (the string must match the API enum exactly). If Jira sends a default payload such as `{"issues":[]}`, the API will return **422** because that is not the triage contract.

   Also add header `X-Triage-Token` with the same value as `TRIAGE_WEBHOOK_TOKEN` from your service environment; mismatches return `401`.

   ```json
   {
     "issue_key": "{{issue.key}}",
     "project": "{{issue.project.key}}",
     "source": "bug_created"
   }
   ```

5. Smoke the public URL from another terminal (replace host and key):

   ```bash
   curl -sS -X POST 'https://YOUR-TUNNEL-HOST/triage' \
     -H 'Content-Type: application/json' \
     -d '{"issue_key":"TJC-123","project":"TJC","source":"bug_created"}' | jq .
   ```

If the tunnel URL changes, update the Automation action (or use a paid/stable tunnel hostname). If requests time out, narrow JQL frequency, use a faster model, or move the service closer to Jira (hosted deployment) so cold starts and network RTT stay within Jira’s limits.

## Local container + temporary tunnel (Jira Automation → container)

Use this flow when you want Jira Automation to hit the **containerized** service locally (instead of a host uvicorn process) while still exercising real Jira/OpenRouter credentials.

1. Ensure your `.env` has valid `JIRA_*`, `OPENROUTER_*`, and any optional runtime settings.
2. Prepare a payload file with a real issue key (default path is `tests/fixtures/triage_smoke_payload.json`; override with `PAYLOAD_PATH=/path/to/payload.json`).
3. Run:

   ```bash
   LIVE_SMOKE_CONFIRM=YES ./scripts/run_container_tunnel.sh
   ```

   Optional overrides:
   - `TUNNEL=ngrok` to use ngrok instead of cloudflared
   - `HOST_PORT=8080` to change the local container bind port
   - `ENV_FILE=/path/to/.env` to select a different env file

4. The script will:
   - validate the payload and refuse to run unless `project == "TJC"` and `issue_key` starts with `TJC-` (TJC-only live smoke scope),
   - print required pre-checks for dedicated smoke issues and require explicit `LIVE_SMOKE_CONFIRM=YES`,
   - build and run the Docker image with your env-file secrets,
   - post one live `POST /triage` request and print the full JSON response (including `run_id`),
   - verify the same `run_id` appears in container logs and print matching log lines for correlation,
   - print a post-run Jira verification checklist (`triagebot-reviewed`, mismatch labels, expected comment behavior),
   - start a tunnel to the container (`cloudflared` quick tunnel by default).
5. In Jira Automation, set the web request URL to `{public_tunnel_url}/triage` and use custom JSON body (`issue_key`, `project`, `source`).

Because this path runs with real secrets and the executor active, use a dedicated test issue/project and verify expected labels/comments after each run.

### Langfuse: keys present but no traces in the UI

`GET /health` includes an `observability` object. Check **`langfuse_export_env_ready`**: it is only true when Langfuse keys are set **and** the Langfuse SDK is allowed to export (`LANGFUSE_TRACING_ENABLED` is not the string `false`, case-insensitive) **and** `OTEL_SDK_DISABLED` is not `true`. If that flag is false while `langfuse_inference_enabled` is true, the UI can stay empty even though Jira works.

After the first real triage request, container logs should include `triage_observability_config` with **`langfuse_runtime_tracing_enabled`** (what the SDK actually enabled). For verbose Langfuse client logs, set `LANGFUSE_DEBUG=true` in the container env. Confirm `LANGFUSE_BASE_URL` matches your Langfuse region (for example US vs EU cloud hostnames).
