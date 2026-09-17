# Jira Triage Docker App

Jira Triage accepts a triage trigger, fetches Jira issue data, and runs AI-assisted recommendations on issue type and priority for **Bug** issues. Default analysis is **sequential**: classify Bug vs Story first (bug policy only); run priority suggestion only when the model says Bug. Projects listed in `TRIAGE_PRIORITY_ONLY_PROJECTS` skip classification and run the priority step only.

After a successful run, outcomes reach Jira in one of two modes: **direct** REST writes (default) or **automation webhook** (the service POSTs a decision payload to a Jira Automation incoming-webhook rule, which applies labels and field edits and composes the comment as the Automation actor). Both modes can run in parallel during migration: keep `TRIAGE_JIRA_APPLY_MODE=direct` and send `jira_apply_mode`, a per-project Rule B URL, and `X-Jira-Automation-Webhook-Token` only on migrated Rule A payloads.

This README is for running the containerized Jira Triage API. It omits local development helpers, benchmark tooling, and optional maintenance commands.

## Runtime Contract

- App: FastAPI served by `uvicorn triage_service.api.triage_api:app`
- Container port: `8000`
- Health endpoint: `GET /health`
- Triage endpoint: `POST /triage`
- Secrets are provided at runtime only. Do not bake API keys into the image or Dockerfile.

## Build

From the repository root:

```bash
docker build -f build/docker/jira-triage/Dockerfile -t jira-triage:local .
```

## Configure

Provide runtime configuration through environment variables or an env file passed to `docker run`.

Required secrets:

- `JIRA_API_KEY`
- `OPENROUTER_API_KEY`
- `TRIAGE_WEBHOOK_TOKEN` (shared secret expected in inbound `X-Triage-Token` header on `POST /triage`)

Required runtime config:

- `TRIAGE_ALLOWED_PROJECTS` — comma-separated Jira project keys eligible for triage (defaults to `TJC,BC` if unset; production uses `TJC,BC,CLOSM` — see `kubernetes/infra/jira-triage/configmap.yaml`)
- `TRIAGE_PRIORITY_ONLY_PROJECTS` — optional comma-separated project keys that skip classification and run priority inference only (default empty; production sets `CLOSM`). Those projects use the default priority Langfuse prompt (or local templates as fallback). Add each priority-only project to `TRIAGE_ALLOWED_PROJECTS` as well.

Required for Jira fetch (and for **direct** outcome writes):

- `JIRA_CLOUD_ID`
- `JIRA_USER_EMAIL`

Webhook mode prefers per-request credentials, sent together: `jira_automation_webhook_url` in the body and `X-Jira-Automation-Webhook-Token` as a header (see [Outcome delivery](#outcome-delivery-direct-writes-vs-automation-callback)). Incoming-webhook URLs are sensitive; never put them in a ConfigMap. The optional service-wide fallback pair `JIRA_AUTOMATION_WEBHOOK_URL` + `JIRA_AUTOMATION_WEBHOOK_TOKEN` exists on `AppSettings` and must live in a Secret/ExternalSecret if used.

Common optional config:

- `TRIAGE_TEXT_MODEL`, defaults to `openai/gpt-4o-mini`
- `TRIAGE_JIRA_HTTP_TIMEOUT_SECONDS`, default `30` — per-attempt timeout for Jira REST
- `TRIAGE_JIRA_HTTP_MAX_RETRIES`, default `2`, max `10` — extra attempts after a transient HTTP 429/502/503/504 or transport failure on Jira REST. Does **not** apply to the Automation callback POST, which is a single attempt
- `TRIAGE_JIRA_REPRODUCTION_STEPS_FIELD_ID`, default `customfield_10251` — Jira field id for reproduction steps; when set, issue fetch requests this field and prefers it over parsing the description for a "Steps to reproduce" / "Reproduction steps" section
- `TRIAGE_JIRA_ZENDESK_TICKET_IDS_FIELD_ID`, default `customfield_10158` — Jira custom field holding linked Zendesk ticket IDs (multi-line text)
- `TRIAGE_JIRA_IMPORTED_ZENDESK_TICKET_IDS_FIELD_ID`, default `customfield_10162` — Jira custom field for imported Zendesk ticket IDs (multi-line text)
- `TRIAGE_JIRA_ZENDESK_TICKET_COUNT_FIELD_ID`, default `customfield_10157` — optional Jira number field for Zendesk ticket count (informational only)
- `TRIAGE_OPENROUTER_HTTP_TIMEOUT_SECONDS`, default `60`
- `TRIAGE_OPENROUTER_HTTP_MAX_RETRIES`, default `2`
- `TRIAGE_OPENROUTER_CALL_DEADLINE_SECONDS` — hard wall-clock ceiling per chat completion attempt, default `150`, max `600`. The HTTP timeout above only bounds the gap between received chunks, so it cannot stop a call that OpenRouter holds open with keep-alive padding while an upstream provider stalls; this deadline closes the connection and retries once on a fresh one.
- `TRIAGE_MAX_CONCURRENT_RUNS` — max concurrent `POST /triage` executions in-process, default `4`, max `64`
- `TRIAGE_CONCURRENCY_WAIT_SECONDS` — max seconds a `wait_for_result` request waits for a slot before `503`, default `900`, max `3600` — see [Concurrency](#concurrency)
- `TRIAGE_ZENDESK_CONTEXT_ENABLED`, default `false` — fetch linked Zendesk tickets and inject them into the issue context block
- `ZENDESK_BASE_URL` — Zendesk subdomain base URL (for example `https://acme.zendesk.com`); required when Zendesk context is enabled unless `ZENDESK_SUBDOMAIN` is set
- `ZENDESK_SUBDOMAIN` — Zendesk subdomain (for example `acme`); alternative to `ZENDESK_BASE_URL` when building `https://{subdomain}.zendesk.com`

- `ZENDESK_IDENTIFIER` — Zendesk OAuth client id; required when Zendesk context is enabled
- `ZENDESK_SECRET` — Zendesk OAuth client secret; required when Zendesk context is enabled (store in Secrets, not ConfigMap)
- `ZENDESK_OAUTH_SCOPE`, default `read` — OAuth scopes requested from Zendesk (space-separated)
- `TRIAGE_ZENDESK_HTTP_TIMEOUT_SECONDS`, default `20` — per-attempt timeout for Zendesk ticket/comment/image fetches and OAuth token minting
- `TRIAGE_ZENDESK_MAX_TICKETS`, default `3` — max linked Zendesk tickets fetched per Jira issue
- `TRIAGE_ZENDESK_MAX_COMMENTS_PER_TICKET`, default `20` — max comments fetched from each Zendesk ticket (newest first)
- `TRIAGE_ZENDESK_COMMENT_SUMMARY_ENABLED`, default `false` — summarize linked Zendesk comment threads into resolution-aware signals
- `TRIAGE_ZENDESK_SUMMARY_MODEL` — optional OpenRouter model override for Zendesk summarization (falls back to `TRIAGE_TEXT_MODEL`)
- `TRIAGE_ZENDESK_SUMMARY_TIMEOUT_SECONDS`, default `60` — per-attempt timeout for Zendesk summarization calls
- `TRIAGE_ZENDESK_COMMENTS_CHAR_BUDGET`, default `4000` — max cumulative Zendesk comment characters sent to summarization (newest comments kept first)
- `TRIAGE_COMMENTS_CHAR_BUDGET`, default `6000` — max cumulative comment body characters included in triage context; oldest comments are dropped first when the budget is exceeded (`0` omits comment bodies)
- `TRIAGE_AUTO_APPLY_DEESCALATION`, default `false` — when `true`, updates Jira priority to the recommended P0–P4 on de-escalation mismatches (model recommends a less urgent label than current)
- `TRIAGE_AUTO_APPLY_ESCALATION`, default `false` — when `true`, updates Jira priority to the recommended P0–P4 on escalation mismatches (model recommends a more urgent label than current)
- `TRIAGE_AUTO_APPLY_BUG_TO_STORY`, default `false` — when `true`, updates issue type from Bug to Story when classification recommends Story but Jira still shows Bug
- `TRIAGE_JIRA_APPLY_MODE`, default `direct` — how finished triage reaches Jira: `direct` (service writes via REST) or `automation_webhook` (callback to an Automation incoming-webhook rule). An authenticated `POST /triage` may override this per request with `jira_apply_mode`
- `JIRA_AUTOMATION_WEBHOOK_TIMEOUT_SECONDS`, default `30` — timeout for the outcome callback POST. The callback is not retried (retrying a webhook that Rule B may already have accepted would duplicate comments)
- `JIRA_AUTOMATION_WEBHOOK_URL` / `JIRA_AUTOMATION_WEBHOOK_TOKEN` — optional Secret-only fallback pair when the whole service uses webhook mode; set both or neither. A request that carries its own URL/token pair overrides them. Do not put them in a ConfigMap
- `ANALYTICS_DASHBOARD_URL` — base URL of the triage analytics dashboard API (for example `http://host/api/v1`); when unset, decision-event POST is disabled
- `ANALYTICS_TOKEN` — optional token sent as `X-Analytics-Token` when posting analytics decisions (store in Secrets when required by the dashboard)
- `ANALYTICS_HTTP_TIMEOUT_SECONDS`, default `2` — per-attempt timeout for fire-and-forget analytics decision POST
- `LANGFUSE_PUBLIC_KEY`
- `LANGFUSE_SECRET_KEY`
- `LANGFUSE_BASE_URL`
- `TRIAGE_LANGFUSE_PROMPTS_ENABLED`, default `true` — fetch triage prompts from Langfuse when keys are configured
- `TRIAGE_LANGFUSE_PROMPT_LABEL` — Langfuse prompt version label (for example `production`); empty uses SDK default
- `TRIAGE_LANGFUSE_PROMPT_CACHE_TTL_SECONDS` — Langfuse prompt client cache TTL in seconds; unset uses SDK default
- `TRIAGE_LANGFUSE_TRUNCATE_PAYLOADS`, default `false` — when `true`, truncate Langfuse generation inputs/outputs and audit metadata
- `TRIAGE_LANGFUSE_MAX_STRING_CHARS`, default `8192` — max string length when Langfuse truncation is enabled (`0` means unlimited)
- `TRIAGE_AUDIT_STRUCTURED_LOG_ENABLED`, default `true` — emit structured JSON audit logs for triage lifecycle events
- `TRIAGE_AUDIT_LANGFUSE_ENABLED`, default `true` — persist audit events to Langfuse when credentials are configured
- `TRIAGE_AUDIT_REDACT_MODEL_INPUT`, default `false` — redact model input (prompts) in Langfuse generation traces
- `TRIAGE_AUDIT_REDACT_MODEL_OUTPUT`, default `false` — redact model output payloads before audit persistence (including `telemetry.model_output_snippet` on `triage_failed` when parsing fails)
- `LOG_LEVEL`

## Run

```bash
docker run --rm \
  --env-file .env \
  -p 8000:8000 \
  jira-triage:local
```

The container starts:

```bash
uvicorn triage_service.api.triage_api:app --app-dir src --host 0.0.0.0 --port 8000
```

## Check Health

```bash
curl -sS http://127.0.0.1:8000/health
```

A ready container returns `ready: true`. If settings are invalid or required runtime config is missing, `GET /health` returns an unhealthy response for readiness checks. That includes the optional callback fallback pair: when `JIRA_AUTOMATION_WEBHOOK_URL` / `JIRA_AUTOMATION_WEBHOOK_TOKEN` are set but incomplete (one without the other) or the URL fails callback-URL validation, readiness fails and the reason is logged (never including the URL itself, whose path is a secret), so a misconfigured Secret surfaces at the probe instead of after a full triage run.

When `ready` is true, the JSON includes an `observability` object: Langfuse key presence, **`langfuse_export_env_ready`** (keys plus SDK export env: not `LANGFUSE_TRACING_ENABLED=false`, not `OTEL_SDK_DISABLED=true`), and audit flags. That does not prove traces reached Langfuse (use the Langfuse UI and `LANGFUSE_DEBUG=true` for export failures).

## Send a Triage Request

```bash
curl -sS -X POST http://127.0.0.1:8000/triage \
  -H 'Content-Type: application/json' \
  -H "X-Triage-Token: ${TRIAGE_WEBHOOK_TOKEN}" \
  -d '{"issue_key":"TJC-123","project":"TJC","source":"manual_trigger","wait_for_result":true}'
```

Drop `wait_for_result` to get the `202` acknowledgement Jira Automation uses instead of blocking for the whole run.

Requests without `X-Triage-Token` or with a value that does not match `TRIAGE_WEBHOOK_TOKEN` receive `401 Unauthorized`. If all in-process triage slots are busy longer than `TRIAGE_CONCURRENCY_WAIT_SECONDS`, a `wait_for_result` caller gets `503` (see [Concurrency](#concurrency)).

Request fields:

- `issue_key`: Jira issue key, for example `TJC-123`
- `project`: Jira project key, for example `TJC`
- `source`: `bug_created` or `priority_changed` (Jira Automation), or `manual_trigger` (local scripts and manual runs)
- `jira_apply_mode` (optional): `direct` or `automation_webhook`. When omitted, the service uses `TRIAGE_JIRA_APPLY_MODE`. Any other value returns `422`
- `wait_for_result` (optional, default `false`): block until triage finishes and return the recommendation instead of acknowledging the trigger. Jira Automation must leave this unset — see [Response](#response) below
- `jira_automation_webhook_url` (required when the effective mode is `automation_webhook` and the `JIRA_AUTOMATION_WEBHOOK_URL` / `JIRA_AUTOMATION_WEBHOOK_TOKEN` pair is unset): this project's Rule B incoming-webhook URL. Must be `https` on `api-private.atlassian.com` and on the default port (no port or `:443`); otherwise the request is rejected with `422`. It must be sent together with `X-Jira-Automation-Webhook-Token` — one without the other is `422`, since both address the same Rule B webhook. Inbound debug logs and callback logs reduce it to scheme plus the allowed callback host; any other value (wrong host, percent-encoded `%2F` delimiters, unparsable) collapses to a fixed redaction marker, and a body that does not parse as a JSON object loses the whole URL value instead, since escaped (`\/`, `\u002f`), mixed slash forms, or truncated values leave no origin that can be trusted. `422` validation responses omit rejected input values entirely (FastAPI's default echo of the invalid field — or, for missing-field errors, of the whole body — is replaced by type, location, and message only), so an invalid URL is never reflected back to clients or proxies

Webhook-mode Rule A must also send:

- **Name:** `X-Jira-Automation-Webhook-Token`
- **Value:** this project's Rule B incoming-webhook secret. The service forwards it as `X-Automation-Webhook-Token` on the callback. Keep it out of the JSON body. Optional Secret fallback pair: `JIRA_AUTOMATION_WEBHOOK_URL` + `JIRA_AUTOMATION_WEBHOOK_TOKEN`, used only when the request carries neither value. Webhook mode without a complete URL/token pair is `422`. The token is never echoed in responses, logs, or audit events.

### Response

By default `POST /triage` reserves an available execution slot, then **acknowledges and returns immediately** with `202 Accepted` and `status: "accepted"` while triage runs in the background. A full run takes minutes — Zendesk summarization and image extraction dominate — which is far longer than Jira Automation will hold a **Send web request** action open, so a synchronous reply would make every rule report a timeout even though triage succeeded. The outcome still reaches Jira through the configured delivery path (direct REST writes or the Rule B callback); the response body is only an acknowledgement. Use the returned `run_id` to find the run's audit events.

Callers that consume the response body — the CLI and manual runs — send `"wait_for_result": true` and get `200` with `status: "completed"` and a merged `TriageRecommendation` (issue type, optional P0–P4, confidence, reason), or `status: "failed"` with a `failure`. Requests for projects outside `TRIAGE_ALLOWED_PROJECTS` return `TriageFailure(category="project_not_allowed")`. Invalid model JSON (classification parse failure, or priority parse failure after one retry) surfaces as `TriageFailure(category="invalid_model_output")`.

A default background request reserves a concurrency slot before responding. If no slot is available, the API immediately returns the retryable status `503` and logs `triage_api_busy` instead of acknowledging work that will be dropped. Any crash after a `202` is logged as `triage_background_failed` with the `run_id`.

Because the run outlives the HTTP response, the ingress route must still allow the occasional long `wait_for_result` call: `kubernetes/infra/jira-triage/httproute.yaml` sets `request`/`backendRequest` to `4200s`, covering the maximum configurable 3600-second capacity wait plus the existing 600-second run allowance. Without it the route falls back to Envoy's 15s default and waiting callers get `504 upstream request timeout` while the pod keeps working.

## Concurrency

No message broker is used. Two independent, in-process bounds keep triage reliable under concurrent load instead:

- **`POST /triage` webhook path**: a process-wide semaphore (`TRIAGE_MAX_CONCURRENT_RUNS`, default `4`) bounds concurrent triage executions. Default background requests reserve a slot with a non-blocking acquire before the API responds: if every slot is taken, the request gets an immediate `503` and `triage_api_busy` is logged, so a `202` never acknowledges work that will be shed. Only a `wait_for_result` caller blocks up to `TRIAGE_CONCURRENCY_WAIT_SECONDS` (default `900`) before receiving `503`. `GET /health` is served on the event loop (not the sync worker thread pool) so readiness probes stay responsive even when all triage slots are busy.

  Triage is **sync**, so an admitted run occupies an anyio/Uvicorn thread pool worker for the whole execution — as a background task for an acknowledged trigger, or as the request handler itself when the caller waits. A `wait_for_result` request that is waiting for a slot also occupies a worker until a slot is acquired or the wait times out. Size the thread pool or Uvicorn `--limit-concurrency` so that ceiling is intentional.

  Background runs are in-process and not durable, but shutdown is drained rather than abrupt: Starlette runs the background task inside the response cycle, so Uvicorn's graceful shutdown waits for in-flight runs after `SIGTERM` (`--timeout-graceful-shutdown` is deliberately unset, so Uvicorn itself waits indefinitely). Kubernetes supplies the bound — `kubernetes/infra/jira-triage/deployment.yaml` sets `terminationGracePeriodSeconds: 900` — so a rollout or a Reloader-triggered secret restart drains for up to 15 minutes before `SIGKILL`.

  That 900s is a deliberate drain **budget**, not a proof that every run fits inside it. Triage has no aggregate run deadline, only per-step ones (`TRIAGE_OPENROUTER_CALL_DEADLINE_SECONDS` per model call, per-adapter HTTP timeouts, and retries), and with up to `TRIAGE_IMAGE_CONTEXT_MAX_ATTACHMENTS` sequential vision calls plus Zendesk summaries their pathological sum is several times the budget. Observed runs are minutes, which the budget covers; the tail is traded away on purpose, because a grace period sized for the pathological sum would stall node drains, cluster upgrades, and spot reclaims for the better part of an hour. A `wait_for_result` request draws on the same budget and its slot wait (`TRIAGE_CONCURRENCY_WAIT_SECONDS`, `900` in production) counts against it, so a caller that queued for capacity is the likeliest to be cut short; it sees a dropped connection and can re-run.

  The ingress timeout bounds something different and is intentionally larger: `httproute.yaml` uses `4200s` so Envoy never cuts off a `wait_for_result` client while the pod is healthy, sized for the maximum *configurable* slot wait (`3600s`) plus a run rather than for production's `900s`. Do not read the two numbers as one budget.

  Rollout availability is unaffected either way, since `maxSurge: 1` / `maxUnavailable: 0` brings the replacement pod to Ready before the old one drains. Anything truncated at the deadline, plus any undrainable kill (OOM against the memory limit, a liveness-probe restart, node loss), is lost with no retry; the triggering Automation rule can be re-run for those issues.
- **Bulk CLI**: `triage_bulk_cli.py` / `scripts/run_bulk_triage_cli.py` uses a bounded `ThreadPoolExecutor` (`--concurrency`, default `4`) instead of a fully sequential loop.

This design was chosen over adding a queue/broker (e.g. RabbitMQ) because the measured bottleneck is upstream OpenRouter model throughput degrading 2-4x past roughly 5-6 concurrent calls on the configured text model, not request intake; a broker would not increase that throughput ceiling, only relocate the wait. `TRIAGE_OPENROUTER_CALL_DEADLINE_SECONDS` (see above) already bounds a single stalled call so one slow request cannot hang a worker indefinitely.

## Issue context for inference

On each run the service loads summary, description, issue type, priority, reporter, reproduction steps, and Jira comments, and prefixes the issue block with **Triage date (UTC)** (`YYYY-MM-DD`, wall-clock UTC at composition time) so models can compare dated comments and Zendesk signals against today. The same block includes Jira **Created** (`issue_created_at` when fetch returned it), Zendesk ticket **created**, and a single Zendesk **Last activity** line (newest comment's timestamp and public/internal flag, no comment bodies—those stay in the summarizer). Reproduction steps come from `TRIAGE_JIRA_REPRODUCTION_STEPS_FIELD_ID` when that field is present on the issue; otherwise the fetcher looks for a "Steps to reproduce" or "Reproduction steps" heading in the description. Comments are fetched via the Jira REST API and appended as a `Comments:` section (author, timestamp, body per line). When `TRIAGE_COMMENTS_CHAR_BUDGET` is exceeded, the oldest comment bodies are dropped first; attachment-only comments are kept when they fit.

When `TRIAGE_ZENDESK_CONTEXT_ENABLED=true` and Zendesk credentials are present, the service also discovers linked Zendesk ticket IDs from configured Jira custom fields (`TRIAGE_JIRA_ZENDESK_TICKET_IDS_FIELD_ID`, `TRIAGE_JIRA_IMPORTED_ZENDESK_TICKET_IDS_FIELD_ID`) and issue text references (`ZD-123`, `ZD #123`, or Zendesk ticket URLs), then fetches up to `TRIAGE_ZENDESK_MAX_TICKETS` linked tickets plus comments. Duplicate ticket IDs across fields and text are deduplicated before fetch. If `TRIAGE_ZENDESK_COMMENT_SUMMARY_ENABLED=true`, each linked ticket can be summarized into structured resolution signals (`INITIAL_IMPACT`, `LATEST_STATUS`, `RESOLUTION_HINTS`, `OPEN_RISKS`) and those signals are injected into the same issue block; sections that verbatim-echo Jira summary/description/repro are replaced with a short placeholder, and duplicate resolution hints across tickets are collapsed. The priority prompt is tuned to weigh latest status over historical peak severity when recovery is confirmed.

All of these fields are passed to the model as one issue block, including dedicated reproduction-steps, comments, and linked-Zendesk sections.

### Zendesk authentication

When Zendesk context is enabled, configure OAuth client credentials: `ZENDESK_IDENTIFIER`, `ZENDESK_SECRET`, plus `ZENDESK_BASE_URL` or `ZENDESK_SUBDOMAIN`; optional `ZENDESK_OAUTH_SCOPE` (default `read`).

The service uses the Zendesk client-credentials flow (`POST /oauth/tokens`); access tokens are cached in memory and refreshed before expiry. Fetch, summary, and Zendesk vision failures are soft-fail-safe and must not abort triage.

## Image attachment preprocessing

When `TRIAGE_IMAGE_CONTEXT_ENABLED=true` (default **off**), triage downloads image attachments referenced in the issue description first, then images referenced in comments (within `TRIAGE_IMAGE_CONTEXT_MAX_ATTACHMENTS`), transcribes each via `TRIAGE_VISION_MODEL` (independent from the text triage model), and injects short summaries into the shared `issue_block` used by classification and priority. Per-image failures become placeholders; triage continues.

If Zendesk enrichment is enabled and linked tickets include images, triage can also process Zendesk inline/comment images in the same shared image budget after Jira attachments. Duplicates already present as Jira attachments are skipped to avoid repeated vision calls.

Optional env (see `kubernetes/infra/jira-triage/configmap.yaml` for production defaults):

- `TRIAGE_VISION_MODEL` — OpenRouter vision model (default `google/gemini-2.0-flash-001`)
- `TRIAGE_IMAGE_CONTEXT_MAX_ATTACHMENTS` — cap per issue (default `5`)
- `TRIAGE_IMAGE_CONTEXT_MAX_BYTES_PER_IMAGE` — skip oversized binaries (default 5 MiB)
- `TRIAGE_IMAGE_CONTEXT_TIMEOUT_SECONDS` — per vision HTTP attempt (default `90`)
- `TRIAGE_AUDIT_REDACT_IMAGE_TRANSCRIPT` — redact full vision transcripts in audit logs (default **true**; screenshots may contain PII)

Each processed image is one billed OpenRouter vision call. Classification and priority prompts receive an `Attached images:` section with per-file summaries, not full transcripts.

## Langfuse prompt management

When `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set, OpenRouter classification and priority steps are traced in Langfuse (`run_id` is the session id). Each step is a separate generation. A priority parse retry starts a second generation with the same prompt and `metadata.attempt=2`. With `TRIAGE_LANGFUSE_PROMPTS_ENABLED=true` (default when keys are present), user and system prompts are loaded from the Langfuse project **`triagebot`** instead of bundling policy text at runtime.

Default prompt names (override via env):

| Role | Default name | Env override |
|------|--------------|--------------|
| Classification user | `triagebot/classification-user` | `TRIAGE_LANGFUSE_CLASSIFICATION_PROMPT_NAME` |
| Classification system | `triagebot/classification-system` | `TRIAGE_LANGFUSE_CLASSIFICATION_SYSTEM_PROMPT_NAME` |
| Priority user | `triagebot/priority-user` | `TRIAGE_LANGFUSE_PRIORITY_PROMPT_NAME` |
| Priority system | `triagebot/priority-system` | `TRIAGE_LANGFUSE_PRIORITY_SYSTEM_PROMPT_NAME` |
| Vision user (image preprocessing) | `triagebot/vision-user` | `TRIAGE_LANGFUSE_VISION_USER_PROMPT_NAME` |
| Vision system | `triagebot/vision-system` | `TRIAGE_LANGFUSE_VISION_SYSTEM_PROMPT_NAME` |
| Zendesk summary user | `triagebot/zendesk-summary-user` | `TRIAGE_LANGFUSE_ZENDESK_SUMMARY_USER_PROMPT_NAME` |
| Zendesk summary system | `triagebot/zendesk-summary-system` | `TRIAGE_LANGFUSE_ZENDESK_SUMMARY_SYSTEM_PROMPT_NAME` |
| Reason-for-humans (local fallback only) | `triagebot/reason-for-humans` | `TRIAGE_LANGFUSE_REASON_FOR_HUMANS_PROMPT_NAME` |

Langfuse user prompts embed policies and guidance; only `{{issue_block}}` is compiled at runtime. Shared settings: `TRIAGE_LANGFUSE_PROMPT_LABEL` (for example `production`), optional `TRIAGE_LANGFUSE_PROMPT_CACHE_TTL_SECONDS`, and optional payload clipping via `TRIAGE_LANGFUSE_TRUNCATE_PAYLOADS` + `TRIAGE_LANGFUSE_MAX_STRING_CHARS`. Set `TRIAGE_LANGFUSE_PROMPTS_ENABLED=false` to force local `prompt_templates.json` and `src/triage_service/core/policy/*.md` even when Langfuse keys exist.

For projects in `TRIAGE_PRIORITY_ONLY_PROJECTS`, the priority user prompt is resolved the same way as the default Bug path: `triagebot/priority-user` → local `priority_template` + `priority_definition.md`. Local Markdown is last-resort only (Langfuse disabled or both fetches fail).

If Langfuse is unavailable or a fetch fails, the service falls back to `src/triage_service/core/prompt_templates.json` and policy Markdown under `src/triage_service/core/policy/`. Local bug classification policy treats likely intentional product/UI/workflow changes as Story by default (Bug only when explicit requirement or required parity is violated). Local priority prompts ask the model to compare the recommended P0–P4 to the issue’s current Jira priority and justify changes, especially downgrades.

## Classification and priority inference

Triage uses sequential OpenRouter calls, then merges them into `TriageRecommendation`:

1. Classification (`parse_classification_step_text`) — Bug vs Story (skipped for `TRIAGE_PRIORITY_ONLY_PROJECTS`).
2. Priority (`parse_priority_step_text`) — on the Bug path after classification, or as the sole step for priority-only projects; Story classification outcome skips priority.

`parse_triage_recommendation_text()` enforces the merged JSON contract. Parsing tolerates a single wrapping markdown code fence (plain or `json`-tagged) and prose surrounding the JSON object (reasoning-heavy models sometimes emit either despite the system prompt). On failure, `InvalidTriageRecommendationError.raw_output` carries a truncated copy of the offending text for audit/log capture.

Classification and priority calls set `response_format={"type": "json_object"}` and `provider={"require_parameters": true}` on the OpenRouter request (`OpenRouterInferenceClient.chat_completion_with_details(..., json_object_response=True)`), so OpenRouter only routes to providers that honor structured JSON output — important when using a multi-provider alias like `:nitro`.

If the priority step’s model output fails to parse, the handler re-asks once with the same prompt (fresh Langfuse generation, `metadata.attempt=2`) before failing the run. A second failure surfaces as `TriageFailure(category="invalid_model_output")` without discarding the already-completed classification unnecessarily. Both attempts’ costs are summed into the run’s `inference_cost_usd`.

On `InvalidTriageRecommendationError`, the classification/priority Langfuse generation records the raw model text and `usage_details`/`cost_details` before the exception propagates (so `output` and usage are populated on failed generations). The `triage_failed` audit event’s `telemetry.model_output_snippet` carries the same text (truncated to 500 chars), unless `TRIAGE_AUDIT_REDACT_MODEL_OUTPUT=true`.

## Jira side effects

After a successful triage, `triagebot-reviewed` is always applied. Additional labels, optional field updates, and internal comments depend on how the recommendation compares to current Jira state. In **direct** mode the service writes those itself via Jira REST. In **automation_webhook** mode it POSTs a decision payload and Rule B applies actions and writes the comment as the Automation actor (see [Outcome delivery](#outcome-delivery-direct-writes-vs-automation-callback)). On the webhook path, mismatch labels (`triagebot-priority-mismatch` / `triagebot-likely-story`) ride the apply branches: an advisory-only run (auto-apply flags off) produces `triagebot-reviewed` plus the comment.

| Condition | Label | Auto-apply (when enabled) | Comment |
|-----------|-------|---------------------------|---------|
| Recommended type is Story but Jira type differs | `triagebot-likely-story` | Bug → Story if `TRIAGE_AUTO_APPLY_BUG_TO_STORY=true` | Yes |
| Bug path: recommended P0–P4 differs from current (prioritize or de-escalate) | `triagebot-priority-mismatch` | Priority field on de-escalate if `TRIAGE_AUTO_APPLY_DEESCALATION=true`; on prioritize if `TRIAGE_AUTO_APPLY_ESCALATION=true` | Yes |
| Types and priorities align | — | — | No |

In **direct** mode, comments use ADF templates from `src/triage_service/adapters/jira_comment_templates.json` (factual support tone; model confidence is not shown in Jira):

- **Advisory** — no issue-type or priority field was changed on this run. Opening copy states that no modifications were made.
- **Applied** — `TRIAGE_AUTO_APPLY_*` updated issue type and/or priority. Opening copy states the ticket was reviewed and adjusted; action lines describe what changed.

Every mismatch comment ends with a closing paragraph asking the reporter to explain if they want to keep the current / pre-edit value. Mismatch comments also include a **Helpful resources** line linking to Confluence (URLs in `jira_comment_templates.json`).

In **automation_webhook** mode the service sends no comment copy at all: Rule B composes the text from the inputs in `comment` (see [Comment composition on the Rule B side](#comment-composition-on-the-rule-b-side)). The same advisory/applied distinction is carried as `comment.kind`, but the wording lives in the Automation rule. `jira_comment_templates.json` applies to direct mode only.

Structured `triage_completed` audit events may include:

- `priority_signal` (`prioritize`, `deescalate`, or `aligned`) and `jira_priority` on Bug outcomes
- `would_post_jira_comment` — `true` when priority signal is `prioritize` or `deescalate`, or when a Story type mismatch would get a comment
- `would_auto_apply_priority_change` — `true` on de-escalation when `TRIAGE_AUTO_APPLY_DEESCALATION=true`, or on prioritize when `TRIAGE_AUTO_APPLY_ESCALATION=true`
- `would_auto_apply_issue_type_change` — `true` on Story mismatch when `TRIAGE_AUTO_APPLY_BUG_TO_STORY=true`
- `auto_apply_deescalation_enabled`, `auto_apply_escalation_enabled`, and `auto_apply_bug_to_story_enabled` reflecting runtime flags
- `zendesk_tickets_considered`, `zendesk_tickets_fetched`, `zendesk_tickets_summarized` when Zendesk enrichment runs

Webhook-mode delivery also records an `outcome_delivered` audit event (mode, HTTP status, attempt count, failure). `delivered: false` means the callback never reached Rule B (triage succeeded; a configuration error, HTTP failure, or transport error stopped the POST). A complete but invalid settings URL is recorded as a zero-attempt failure. `delivered: true` with no Jira change means Rule B failed — use the Automation audit log and the logged `run_id`.

## Analytics dashboard

When `ANALYTICS_DASHBOARD_URL` is set, each completed triage run POSTs a decision row to `{ANALYTICS_DASHBOARD_URL}/decisions` on a background thread (fire-and-forget; failures are logged and never fail the triage response). Optional `ANALYTICS_TOKEN` is sent as `X-Analytics-Token`. Payloads include run metadata, intake vs recommended type/priority, whether auto-apply changed fields, inference cost, and Jira issue enrichment when available.

Structured log events for this path include `analytics_decision_dispatch`, `analytics_decision_posted`, `analytics_decision_post_failed`, `analytics_decision_post_rejected`, `analytics_decision_emit_failed`, and `analytics_decision_skipped` (when the URL is unset). See `apps/jira-triage-dashboard/README.md` for the dashboard service and Grafana panels. The dashboard service has its own opt-in allowlist (`ALLOWED_PROJECTS` in `kubernetes/infra/jira-triage-dashboard/configmap.yaml`; production uses `BC,CLOSM`) and a matching SQL view filter — triage can POST a project that Grafana still excludes if it is not on that list.

## Logging

On startup the service configures stdout logging from `LOG_LEVEL` (default `INFO`; invalid values fall back to `INFO`). Log lines are single-line JSON objects with `timestamp`, `level`, `logger`, `message`, and any fields passed via `extra={...}` (for example `run_id`, `url`, `status_code`, `error`) so structured detail reaches Loki. Inbound requests emit `http_request` events with method, path, status, and latency. Outbound Jira and OpenRouter HTTP calls emit per-attempt `outbound_http` events with method, URL, status, attempt number, and request duration. A first-time priority parse failure emits `triage_priority_parse_retry` before the second attempt. Third-party HTTP client libraries (`httpx`, `httpcore`, `urllib3`) are capped at `WARNING` to reduce noise.

At `INFO` log level, every accepted `POST /triage` emits a `triage_apply_mode_selected` event before triage runs, with `issue_key`, `project`, `source`, the `apply_mode` the run will use, and `apply_mode_origin` (`request` when the body carried `jira_apply_mode`, `settings` when it fell back to `TRIAGE_JIRA_APPLY_MODE`). A migrated Rule A that still writes via REST shows up here as `apply_mode=direct` with `apply_mode_origin=settings` — meaning the field never reached the service. An absent event is only evidence of an older image after confirming the request was accepted and the service is logging at `INFO`; rejected requests and higher log levels do not emit it.

When enabled, additional structured audit events are emitted for Zendesk enrichment stages:

- `zendesk_context_fetched` — linked ticket IDs requested, fetched count, dedupe count (`ticket_ids_deduped`), and per-ticket fetch failures
- `zendesk_context_summarized` — tickets considered/summarized, summary inference cost, and per-ticket summary status
- `image_context_extracted` now includes `zendesk_skipped` details for Zendesk images deduped against Jira attachments

## Jira Automation

### Request headers

In the **Send web request** (or equivalent) action, add custom headers:

- **Name:** `X-Triage-Token`
- **Value:** the same secret you set as `TRIAGE_WEBHOOK_TOKEN` in the triage service environment (paste the literal token; Jira does not resolve env vars from your container).
- **Name:** `X-Jira-Automation-Webhook-Token` (webhook-mode Rule A only)
- **Value:** this project's Rule B incoming-webhook secret. The service forwards it as `X-Automation-Webhook-Token` on the callback. Optional Secret fallback: `JIRA_AUTOMATION_WEBHOOK_TOKEN`.

Omitting `X-Triage-Token` or sending the wrong value results in **`401 Unauthorized`**.

### Request body

Configure Jira Automation to send custom JSON to `/triage`. Direct-mode Rule A (unchanged callers):

```json
{
  "issue_key": "{{issue.key}}",
  "project": "{{issue.project.key}}",
  "source": "bug_created"
}
```

Webhook-mode Rule A (per-request override; service env can stay `direct`):

```json
{
  "issue_key": "{{issue.key}}",
  "project": "{{issue.project.key}}",
  "source": "bug_created",
  "jira_apply_mode": "automation_webhook",
  "jira_automation_webhook_url": "<this project's Rule B webhook URL>"
}
```

Use `priority_changed` for priority-change rules, and `bug_created` for creation rules — the `source` is what every audit event and analytics row is keyed by, so a rule cloned from another one must have this field updated. The endpoint expects this shape; default Jira payloads such as `{"issues":[]}` are rejected by request validation.

Rule A gets a `202` back within milliseconds and must not send `wait_for_result`. Triage itself runs after the response, so the rule's **Send web request** action completes immediately and any actions sequenced after it still run. `jira_apply_mode` is optional: when present it selects the outcome delivery path for this request only; when omitted, the service uses `TRIAGE_JIRA_APPLY_MODE`. For a gradual migration, keep the service env at `direct`, add `"jira_apply_mode": "automation_webhook"` plus this project's Rule B URL and `X-Jira-Automation-Webhook-Token` only to migrated Rule A payloads, and leave existing callers unchanged.

### Outcome delivery (direct writes vs Automation callback)

`TRIAGE_JIRA_APPLY_MODE` selects the default way a finished triage reaches Jira. An authenticated `POST /triage` may override it per request with `jira_apply_mode`:

| Mode | Behavior |
|------|----------|
| `direct` (default; production ConfigMap today) | The service writes labels, the comment, and any auto-apply field edits itself via Jira REST. |
| `automation_webhook` | The service POSTs a decision payload to a Jira Automation incoming-webhook rule, which applies labels and field edits — and writes the comment text itself — as the Automation actor. Actor-applied edits do not re-trigger other Automation rules, which keeps re-triage on priority change safe. |

Webhook mode resolves the Rule B URL and token as one pair: the per-request pair (`jira_automation_webhook_url` + `X-Jira-Automation-Webhook-Token`) when the request carries either value, otherwise the `AppSettings` pair (`JIRA_AUTOMATION_WEBHOOK_URL` / `JIRA_AUTOMATION_WEBHOOK_TOKEN`). The two sources are never mixed, because a per-project URL authenticates only with its own secret. A request supplying just one half, or a webhook-mode request with no complete pair from either source, is rejected with `422` before triage runs. Both the per-request URL and the settings fallback must be `https` on `api-private.atlassian.com` and on the default port (Atlassian retired `automation.atlassian.com` incoming webhooks). The settings fallback is prevalidated everywhere it could be paid for: `GET /health` reports not ready on a misconfigured pair, `POST /triage` rejects it with `422` before the Jira fetch and model inference run, `build_default_triage_handler` raises `ValueError` at construction for non-HTTP callers, and delivery itself re-checks and records a zero-attempt `outcome_delivered` failure as the last line of defense. The token stays out of the JSON body and is never echoed in responses, logs, or audit events. Callback logs and inbound debug logs redact the URL to scheme plus the allowed callback host (anything else, including percent-encoded authorities, collapses to a fixed marker), and drop it entirely when the inbound body cannot be parsed as JSON.

Only a `2xx` counts as delivered. The callback client does not follow redirects, so a `3xx` is recorded as a delivery failure (`outcome_delivered` with `delivered: false`) rather than a successful hand-off.

`JIRA_AUTOMATION_WEBHOOK_TIMEOUT_SECONDS` (default `30`) bounds the callback POST. That POST is a single attempt: retrying a webhook that Rule B may already have accepted would duplicate comments. `TRIAGE_JIRA_HTTP_MAX_RETRIES` applies to Jira REST only. If you use the env fallbacks, store them in a Secret/ExternalSecret — never a ConfigMap. Parallel migration should keep the ConfigMap at `TRIAGE_JIRA_APPLY_MODE=direct` and send per-request URL/token only on migrated Rule A payloads.

The callback payload contract is defined by `build_callback_payload` in `src/triage_service/adapters/automation_webhook_executor.py`; `CALLBACK_PAYLOAD_VERSION` is bumped when its shape changes.

Two behavior differences versus direct writes:

- Comment copy is owned by Rule B, not by the service — see below. Direct mode keeps rendering ADF comments in-service and is unaffected.
- `applied_type_change` / `applied_priority_change` on analytics rows mean **apply directed**, not apply confirmed — the service no longer observes the write.

### Comment composition on the Rule B side

Payload version `2` adds composition inputs so Rule B can write the comment. The pre-rendered `comment.body` stays on the payload until every Rule B has switched; a rule still posting `{{webhookData.comment.body}}` would otherwise accept the callback and write an empty comment.

```json
"comment": {
  "post": true,
  "body": "<pre-rendered v1 copy>",
  "kind": "advisory",
  "topic": "priority",
  "reason": "<model rationale>",
  "current_priority": "P3"
}
```

- `post` — unchanged gate. Keep the rule's existing `{{webhookData.comment.post}} equals true` condition; when it is `false` there is no mismatch to comment on.
- `body` — compatibility copy for unmigrated Rule B. Migrated rules should ignore it and compose from the fields below.
- `kind` — `applied` when this payload also directs a field edit (Rule B performs it in the same run), otherwise `advisory`. Choose "was changed" vs "we recommend" wording from this.
- `topic` — `issue_type` when the recommendation is Story, otherwise `priority`. Selects the action line, the closing line, and which Confluence link to use.
- `reason` — the model's rationale, the only free text in the composition contract.
- `current_priority` — the priority as it stood **before** any Rule B edit, or `null` when unset. Do not read `{{issue.priority.name}}` for this in an applied run: the edit action may have already changed it.

`kind` and `topic` are flat scalars on purpose. Branch on them with `{{#if(equals(webhookData.comment.kind,"applied"))}}`, not on `actions.apply_priority`, whose `null` is unreliable in Automation conditionals. Rule B has the work item bound, so it should mention the reporter with `[~accountid:{{issue.reporter.accountId}}]` and use wiki-markup links (`[text|url]`) rather than the plain URLs the compatibility body carries.

### Production automation (event-driven)

Production uses Jira Cloud Automation rules triggered by issue events — not a periodic JQL scan:

- **Bug created** — rule fires when a new Bug is created in an allowed project. After a **10 minute delay** (Jira Automation delay action), send `POST /triage` with `"source": "bug_created"`. The delay gives reporters time to finish edits, attach context, or correct fields before triage runs.
- **Priority changed** — rule fires when priority changes on a Bug (same allowed projects). Use the same **10 minute delay**, then send `POST /triage` with `"source": "priority_changed"`.
- **CLOSM (priority-only)** — use a **separate** Automation rule for CLOSM (requires `CLOSM` in both `TRIAGE_ALLOWED_PROJECTS` and `TRIAGE_PRIORITY_ONLY_PROJECTS`). Triggers and delay are the same; required JSON fields are unchanged (`issue_key`, `project`, `source`). Migrated rules may add `jira_apply_mode` and `jira_automation_webhook_url`.
- **Dedupe** — on successful triage, `triagebot-reviewed` is applied (by the service in `direct` mode, or by Rule B in webhook mode). Optional Automation conditions such as `labels not in (triagebot-reviewed)` can skip issues already reviewed.
- **Parallel webhook migration** — keep `TRIAGE_JIRA_APPLY_MODE=direct` in the ConfigMap. Unmigrated Rule A payloads keep writing via REST; migrated Rule A payloads send `"jira_apply_mode": "automation_webhook"`, `jira_automation_webhook_url`, and `X-Jira-Automation-Webhook-Token`. Do not add Rule B URLs or secrets to the ConfigMap.
- **Manual trigger** — operators can triage immediately via CLI or direct API (`source=manual_trigger`) without waiting for the Automation delay. Add `"wait_for_result": true` to get the recommendation back in the response instead of a `202`.

Example rule filters (adjust project keys to your allowlist):

- Bug created (full classify→priority path, e.g. BC/TJC): `project in (BC, TJC) AND issuetype = Bug`
- Bug created (priority-only, e.g. CLOSM): `project = CLOSM AND issuetype = Bug`

### `triagebot-reviewed` lifecycle

- On successful triage (mismatch or not), `triagebot-reviewed` is applied (direct Jira REST or Rule B callback, depending on apply mode).
- On triage failure (`status: failed` / `TriageFailure`), no labels or comments are posted (and no outcome callback is sent).
- To force re-triage on an issue that already succeeded, remove `triagebot-reviewed` manually and re-trigger via Automation or manual CLI/API.
- Issues that never received `triagebot-reviewed` after a failed run can be retried by re-firing the Automation rule or running triage manually.

### Operational notes

- Auto-apply defaults are disabled in code unless you set `TRIAGE_AUTO_APPLY_*` (production enables all three — see ConfigMap).
- Confidence is metadata for operators and API/audit output; it is not used as a direct mutation threshold.
- Retry and dedupe are owned by Jira Automation (event re-triggers, optional label guards) and the `triagebot-reviewed` label — not by an internal queue.

### Default model selection

`TRIAGE_TEXT_MODEL` defaults to `openai/gpt-4o-mini` when unset (production overrides in ConfigMap). Override when benchmark results on your issue mix show a clear accuracy lift that justifies latency and cost, or when org policy requires a specific provider/model family. Confidence remains advisory until calibrated against labeled outcomes; do not use it as an automation gate without a documented calibration loop.

## Kubernetes Notes

- Route traffic to container port `8000`.
- Use readiness and liveness probes against `GET /health` (served on the event loop so probes stay responsive when all `POST /triage` slots are busy).
- Store `JIRA_API_KEY`, `OPENROUTER_API_KEY`, `TRIAGE_WEBHOOK_TOKEN`, `JIRA_USER_EMAIL`, `ZENDESK_SECRET`, `ANALYTICS_TOKEN`, optional Langfuse keys, and (only if using service-wide webhook fallbacks) `JIRA_AUTOMATION_WEBHOOK_URL` / `JIRA_AUTOMATION_WEBHOOK_TOKEN` in Kubernetes Secrets or ExternalSecrets. Never put Rule B URLs or tokens in a ConfigMap. Parallel migration should keep those values on each Rule A request instead.
- Store non-sensitive values such as `TRIAGE_ALLOWED_PROJECTS`, `TRIAGE_PRIORITY_ONLY_PROJECTS`, `TRIAGE_TEXT_MODEL`, `TRIAGE_COMMENTS_CHAR_BUDGET`, `TRIAGE_AUTO_APPLY_DEESCALATION`, `TRIAGE_AUTO_APPLY_ESCALATION`, `TRIAGE_AUTO_APPLY_BUG_TO_STORY`, `TRIAGE_JIRA_APPLY_MODE`, `JIRA_AUTOMATION_WEBHOOK_TIMEOUT_SECONDS`, `ANALYTICS_DASHBOARD_URL`, Jira/Zendesk custom field IDs, `ZENDESK_SUBDOMAIN` / `ZENDESK_IDENTIFIER` / `ZENDESK_OAUTH_SCOPE`, image-context settings, Zendesk context/summarization settings, audit redaction toggles, Langfuse prompt names, Langfuse payload truncation settings, OpenRouter timeout/deadline and retry settings, `TRIAGE_MAX_CONCURRENT_RUNS` / `TRIAGE_CONCURRENCY_WAIT_SECONDS`, and log settings in a ConfigMap (see `kubernetes/infra/jira-triage/configmap.yaml`; production enables Zendesk context/summarization, image context, auto-apply for escalation, de-escalation, and Bug→Story, `TRIAGE_ALLOWED_PROJECTS=TJC,BC,CLOSM`, `TRIAGE_PRIORITY_ONLY_PROJECTS=CLOSM`, `TRIAGE_JIRA_APPLY_MODE=direct` so webhook mode is opt-in per Rule A payload, and posts decisions to the in-cluster analytics dashboard).
- Prefer explicit `secretKeyRef` entries for sensitive env vars.
- Set `terminationGracePeriodSeconds` to a deliberate drain budget for acknowledged background runs (production uses `900`); runs still executing at the deadline are killed and must be re-triggered. It is not the same bound as the ingress timeout — see [Concurrency](#concurrency).
- Pin production deployments to immutable image tags.

## Success Criteria

- The image builds without secrets.
- The container starts and listens on port `8000`.
- `GET /health` reports readiness with the intended runtime env.
- `POST /triage` returns a `run_id`.
- For successful triage, Jira updates follow the service rules: apply `triagebot-reviewed`; add `triagebot-likely-story` or `triagebot-priority-mismatch` for story-type or any priority mismatch (in webhook mode those mismatch labels ride Rule B apply branches); post internal comments for those mismatches (advisory or applied copy depending on whether auto-apply changed fields); honor `TRIAGE_AUTO_APPLY_*` when enabled (including escalation via `TRIAGE_AUTO_APPLY_ESCALATION`). In `direct` mode the service renders and writes those via Jira REST; in `automation_webhook` mode it delivers the decision to Rule B in a single callback POST — Rule B composes the comment copy — and records `outcome_delivered`.
- When `ANALYTICS_DASHBOARD_URL` is configured, completed runs emit decision rows to the analytics dashboard without blocking the triage response.
