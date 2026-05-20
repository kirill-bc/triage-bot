# TODO - Jira Triage MVP

## 1. Setup / Environment
- [x] Initialize Python project tooling and dependency management for runtime and tests.
- [x] Configure environment loading for Jira, OpenRouter, and logging credentials.
- [x] Add local run scripts and ensure `.venv`-based execution works (`./scripts/run_tests.sh` entrypoint).
- [x] Define core config object for project allowlist (`TJC` first, `BC` next). Stabilization delay and dedupe are owned by the Jira-side scheduled rule and are not service config.
- [x] Add CI quality gates for `mypy .`, `pytest -m lint`, and `pytest -m "unit or integration"`.
- Done when: project boots locally, config validates required env vars, and all baseline gates execute successfully.

## 2. Core Backend / API
- [x] Set up `README.md` from a generic project skeleton description to rough structure we could fill up leading to the final state of this stage
- [x] Implement `POST /triage` contract accepting `issue_key`, `project`, and `source` (closed `Literal`: `bug_created`, `priority_changed`, `manual_trigger`).
- [x] Add request validation for required fields and supported `source` values.
- [x] Implement Jira issue fetcher by issue key (summary, description, optional reproduction steps, type, priority, reporter).
- [x] Add smoke script to fetch one issue by key for manual verification (`scripts/fetch_jira_issue.py`).
- [x] Implement policy context loader for bug and priority definitions.
- [x] Align `specification.md` and triage backlog with sequential analysis (classification then optional priority), advisory Jira comments, and Story-path nullable `recommended_priority`.
- Triage logic (sequential, not parallel type+priority):
  1. Classify Story vs Bug (bug policy only for this step).
  2. If the model says Story: recommend reclassifying to Story; do not run priority inference; do not compare or suggest P0–P4.
  3. If the model says Bug: run a second priority inference (priority policy + issue context). Then compare predicted P0–P4 to current Jira priority (triage is scoped to Bug issues in JQL).
  4. Surface guidance via internal comment (and mismatch labels when applicable): suggest reclassification to Story and/or priority with reasoning; advisory only — no automatic Jira field mutation in Phase 1 (see Out-of-scope in `specification.md`).
- [x] Build prompt/input composer for step (1) and, when needed, step (2) — do not bundle both model calls into one always-on prompt.
- [x] Implement OpenRouter inference client with model name from configuration.
- [x] Parse and validate model output to strict schema (per step or merged response), including:
  - [x] `recommended_issue_type` in `Bug|Story`
  - [x] When `recommended_issue_type` is `Bug`: `recommended_priority` in `P0|P1|P2|P3|P4`; when `Story`: omit or null `recommended_priority` (no priority model output)
  - [x] `confidence` in `[0.0, 1.0]` (per inference that ran; document whether one or two scores are returned)
  - [x] `reason` non-empty
  - [x] Mismatch signals derived in code (`triage_mismatch.compute_mismatch_flags`); model does not emit `recommended_action`
- [x] Implement fallback/error response path for upstream failures and invalid model output.
- [x] Implement synchronous triage handler invoked per-issue by the Jira scheduled-scan webhook: validate the request, run the classification → optional priority flow, hand the outcome (recommendation or `TriageFailure`) to the action executor. No internal scheduler/queue (Jira-side JQL owns delay, dedupe, and retry via the `triagebot-reviewed` label filter and `created >= -30m` window).
- [x] Implement local runner entrypoint (`source="manual_trigger"`) to execute full triage for a single issue key from CLI (without Jira Automation dependency).
- Done when: service supports both on-command triage and the Jira scheduled-scan webhook path, using sequential classification then optional priority (never both inferences unconditionally).

## 3. Frontend / UX (Jira-facing outputs)
- [x] Implement mismatch detector: always compare issue type to `recommended_issue_type`; compare priority to `recommended_priority` only when the triage path ran priority (i.e. model classified as Bug). (`triage_mismatch.compute_mismatch_flags`; wire into executor when built.)
- [x] Implement Jira action executor (`jira_action_executor.JiraTriageActionExecutor`; default handler uses it when `JIRA_CLOUD_ID` and `JIRA_USER_EMAIL` are set):
  - [x] Apply `triagebot-reviewed` after every successful triage (mismatch or not). This is the dedupe marker the Jira scheduled rule depends on — without it, the JQL keeps re-matching the issue.
  - [x] Post internal comment with recommended values and concise reasoning for Story mismatches and Bug de-escalation mismatches (fixed **TriageBot** template; numeric confidence stays in API/audit only, not in the Jira body; optional reporter @mention when `reporter_account_id` is present on the fetched issue). Bug prioritization mismatches remain audit/API metadata only.
  - [x] Apply mismatch-specific labels only when applicable: `triagebot-likely-story` (type mismatch when recommending Story), `triagebot-priority-mismatch` (Bug de-escalation mismatch; prioritize-only Bug mismatches are not Jira-labeled).
- [x] On `TriageFailure`, apply **no** labels and post **no** comment. The issue stays unlabeled so the next scheduled scan retries it automatically until success or `created >= -30m` ages it out.
- [x] **Local CLI E2E (developer path):** With `JIRA_CLOUD_ID`, `JIRA_USER_EMAIL`, and model keys set, `scripts/run_triage_cli.py` / `triage_manual_cli.run_cli_triage` runs the full pipeline for a given issue key: fetch → classify → optional priority → mismatch → `JiraTriageActionExecutor` (`source="manual_trigger"`). This is the supported way to run triage from a laptop against real Jira without any Jira-side integration.
- [x] **Local tunnel for Jira → laptop:** Run the HTTP server locally and expose `POST /triage` with a public HTTPS URL (e.g. ngrok, Cloudflare Tunnel) so Jira Automation “Send web request” can reach it during development; note URL churn on free tiers and timeouts. Precursor or parallel to a stable hosted deployment, not a substitute for production hardening. **Instructions:** `README.md` section *Local HTTP server and tunnel*; helper script `scripts/run_dev_tunnel.py` (loads `.env`, uvicorn, tunnel). **Operator smoke:** still confirm Jira → tunnel → service on your tenant when you first wire Automation (not covered by CI).
- [x] **Jira Automation → deployed API (product path):** In Jira Cloud, configure rules so **Jira** sends `POST` requests to **your hosted** `POST /triage` URL with the real Automation body (`issue_key`, `project`, `source` one of `bug_created` / `priority_changed`). Prove reachability (TLS, DNS, timeouts), any auth fronting the API, and that labels/comments match the same rules as the CLI run on the same issue. Local `curl` / `TestClient` with those sources only proves the handler code, not Jira as the caller.
- Done when: (a) CLI path above is usable for QA/dev smoke on real issues with the executor live. (b) At least one Jira site has a working Automation → production-like triage URL flow verified end-to-end (Jira is the HTTP client), with runbook steps captured in docs.

## 4. Refactor for maintainability (before observability/deploy). NO BACKWARD COMPATIBILITY CONCERNS, JUST DO THE REFACTOR.
- [x] Create target package layout (`src/triage_service/api`, `src/triage_service/core`, `src/triage_service/adapters`, `src/triage_service/observability`) and document ownership boundaries.
- [x] Move `triage_api.py` into API package with no behavior changes.
- [x] Move orchestration/domain modules (`triage_handler.py`, `triage_fallback.py`, `triage_mismatch.py`, `triage_recommendation_parser.py`) into core package.
- [x] Move external adapters (`jira_issue_fetcher.py`, `jira_action_executor.py`, `openrouter_inference_client.py`) into adapters package.
- [x] Move all prompts to external yaml/json/other format templates so that they are not hard coded in source files themselves.
- Done when: module composition is package-oriented, imports are stable, and lint/mypy/unit gates pass without behavior regression.

## 5. Observability baseline (LangFuse + structured logs)
- [x] Add a `run_id` correlation ID generated at API ingress and propagated through handler/executor/adapters.
- [x] Define canonical audit event schema for triage lifecycle (`classification_completed`, `priority_completed`, `triage_completed`, `triage_failed`).
- [x] Capture model request/response metadata for each inference step plus parsed fields (`recommended_issue_type`, `recommended_priority`, `confidence`, `reason`) using LangFuse traces/spans.
- [x] Add latency/timing capture for Jira fetch, each model call, and Jira action execution.
- [x] Add `AuditStore` interface with `CompositeAuditStore` fan-out.
- [x] Implement `LangFuseAuditStore` for hosted run traceability/audit history.
- [x] Implement `StructuredLoggerAuditStore` emitting JSON logs compatible with CloudWatch queries (baseline regardless of LangFuse).
- [x] Add config surface in `src/triage_service/core/settings.py` for LangFuse keys/host, audit enable flags, and redaction toggles.
- [x] Ensure confidence remains advisory metadata only (never a direct mutation decision switch).
- [x] Add unit tests for event schema validation, audit fan-out behavior, and failure-safe logging/LangFuse emission paths.
- [x] Align Langfuse Python SDK v4 observation usage so the UI trace tree and root view nest `triage_issue_pipeline`, inference generations, and in-span audit events (avoid incorrect `trace_context` / deprecated kwargs on `create_event`).
- Done when: every triage attempt emits correlated audit data to structured logs (required baseline). LangFuse traces and `LANGFUSE_*` secrets for **hosted** environments are optional at MVP ship and tracked under **§10 Post-MVP → Langfuse project credentials**; when keys are set, the same run also emits model metadata to LangFuse with redaction policy applied, including confidence, with passing lint/mypy/unit.

## 6. Resilience + runtime safeguards
- [x] Add explicit timeout/retry policy for Jira fetch and Jira write operations with bounded retries.
- [x] Add explicit timeout/retry policy for OpenRouter calls with safe fallback on exhaustion.
- [x] Emit retry counters and timeout/failure categories to audit events and logs.
- [x] Add guardrails for oversized payload logging (truncate consistently and mark truncation).
- [x] Add health endpoint (`GET /health`) and minimal readiness signal for hosted environments.
- [x] Add unit tests for retry behavior, timeout mapping, and fallback category correctness.
- Done when: transient failures are retried safely, permanent failures are observable, and hosting health checks are supported.

## 7. Image context extraction (vision-as-preprocessor)
Bug tickets often pair terse text with load-bearing screenshots (stack traces, error toasts, broken UI states). Convert image **attachments** to text once per attachment so both classification and priority steps consume the same enriched `_issue_block` without going multimodal. Comments and comment attachments are explicitly out of scope — triage fires near issue creation and must not depend on context that accrues afterwards.

- [x] **TDD: write failing tests first** (per workspace rules) covering: ADF `media` / `mediaSingle` walker, `_issue_block` rendering with and without images, `ImageContextExtractor` Protocol contract, and soft-failure placeholder rendering on per-image errors.
- [x] Extend `FetchedIssue` (`src/triage_service/adapters/jira_issue_fetcher.py`) with `attachments: list[AttachmentRef]` (id, filename, mime_type, size_bytes, `inline` flag). Walk the ADF description for `media` / `mediaSingle` node ids and also pull the issue-level `attachment` field array; add `attachment` to `_BASE_FIELDS`.
- [x] Add a Jira attachment binary fetch path on the same Atlassian gateway (`_basic_auth_header`, reuse `request_with_retries`) so the timeout / retry contract matches issue fetch.
- [x] Create `src/triage_service/adapters/image_context_extractor.py`:
  - [x] `ImageContextExtractor` Protocol returning a list of `ImageContext { attachment_id, filename, transcript, summary, extraction_failure? }`.
  - [x] `NoOpImageContextExtractor` for the feature-off path and tests.
  - [x] `OpenRouterVisionImageContextExtractor` default implementation using a dedicated `TRIAGE_VISION_MODEL` (independent from `TRIAGE_TEXT_MODEL`) with a transcription-first prompt: verbatim text first, then a 1–3 sentence UI summary, no root-cause speculation.
- [x] Per-image errors (HTTP 4xx/5xx, oversize, unsupported MIME, vision call failure) degrade to `[Attachment N: extraction unavailable — {reason}]` in the issue block and **never** raise out of triage. `TriageFailure` remains reserved for the existing fetch / inference / parse boundaries in `triage_fallback.py`.
- [x] Wire extraction into `TriageHandler.run_sync` (`src/triage_service/core/triage_handler.py`) after `fetcher.fetch(...)` and before `_triage_fetched_issue(...)`; keep `run_sync_on_fetched` extractor-free so benchmark replays accept pre-extracted context.
- [x] Render results in `_issue_block` (`src/triage_service/core/prompt_composer.py`) as an "Attached images" section so both classification and priority pick it up via the shared `{{issue_block}}` Langfuse variable; user-prompt schemas do not change.
- [x] Settings (`src/triage_service/core/settings.py`): `TRIAGE_IMAGE_CONTEXT_ENABLED` (default `false`), `TRIAGE_VISION_MODEL`, `TRIAGE_IMAGE_CONTEXT_MAX_ATTACHMENTS` (default `5`), `TRIAGE_IMAGE_CONTEXT_MAX_BYTES_PER_IMAGE`, `TRIAGE_IMAGE_CONTEXT_TIMEOUT_SECONDS`, and `TRIAGE_AUDIT_REDACT_IMAGE_TRANSCRIPT` (default `true` — screenshots leak PII more readily than typed text).
- [x] Attachment selection / budget: process only inline-in-description image attachments, capped at `TRIAGE_IMAGE_CONTEXT_MAX_ATTACHMENTS` (no issue-level attachment fallback).
- [x] Observability:
  - [x] Add Langfuse span `image_context_extraction` nested under `triage_issue_pipeline`, carrying attachment counts, total bytes, and vision cost when returned.
  - [x] Emit `image_context_extracted` audit event via the existing `AuditStore` fan-out (per-attachment latency, failure breakdown, `run_id` correlation).
  - [x] Extend `triage_completed` / `triage_failed` telemetry with `image_context_attachments_considered` and `image_context_attachments_extracted` so dashboards can stratify runs with vs. without image signal.
- [x] Benchmark integration: add an enable / disable flag to `scripts/benchmark/run_classification_benchmark.py`, persist resolved image context into JSONL rows, and extend `summarize_benchmark_rows.py` to break out accuracy by "has images" vs. "text-only".
- [x] Local CLI parity: `scripts/run_triage_cli.py` honors `TRIAGE_IMAGE_CONTEXT_ENABLED` and prints a compact attachment summary alongside the recommendation for manual smoke checks.
- [x] Docs: update `README.md` with the feature flag, vision model selection, cost / PII notes, and an example `_issue_block` rendering with attachments.

Out of scope (do not pull in):
- Comments and comment attachments — triage is creation-time and would invert the `triagebot-reviewed` dedupe lifecycle if it depended on post-creation context.
- True multimodal classification / priority — doubles image cost across the sequential steps and breaks the text-only Langfuse prompt versioning in `langfuse_prompt_config.py`.
- Pixel-level UI reasoning beyond a 1–3 sentence vision summary.

Done when: issues with sparse text and load-bearing screenshots produce measurably better classification / priority on the benchmark dataset; extraction is feature-flagged and disabled by default; `pytest -m "unit or integration"`, `mypy .`, and `pytest -m lint` all pass; per-image failures degrade gracefully without aborting triage; benchmark runs can be stratified by image presence.

## 8. Integration tests (deferred until post-deploy stabilization)
- [ ] Add API contract tests for `POST /triage` request/response shape and validation errors.
- [ ] Add integration tests for Jira webhook adapter invoking synchronous triage pipeline.
- [ ] Add integration tests for Jira action executor against mocked Jira API.
- [ ] Add integration tests for policy retrieval adapter using mocked content source.
- [ ] Add integration tests for mismatch/no-mismatch behavior including label combinations and sequential flow (Story outcome skips priority inference; Bug outcome invokes it).
- [ ] Add integration coverage for audit emission on success/failure paths (including model-output capture and confidence persistence).
- Done when: `pytest -m integration` passes deterministically and covers service boundaries + observability surfaces.

## 9. Polish & Docs
- [x] Document architecture and module responsibilities after refactor (`api`, `core`, `adapters`, `observability`).
- [x] Add runbook for local development, env setup, and test execution commands.
- [x] Add Jira Automation setup recipe: scheduled rule cadence (every 5 min <= JQL window), reference JQL (`project = ... AND issuetype = Bug AND labels not in (triagebot-reviewed) AND created >= -30m AND created <= -5m`), and body template (`issue_key`, `project`, `source: bug_created|priority_changed`).
- [x] Document `triagebot-reviewed` lifecycle: always applied on success, remove to force re-triage, absent on >30m-old issues indicates manual-QA fallback case.
- [x] Document observability usage: where to inspect raw model output/confidence, and how to trace a run by `run_id`.
- [x] Document known MVP limitations and out-of-scope items (no auto mutation, no Zendesk intake, no full RAG).
- [ ] Record default model-selection rationale and confidence calibration strategy for next phase tuning.
- Done when: a new engineer can run, operate, observe, and troubleshoot the service using repo docs only.

## 10. Post-MVP
- [x] **Classification benchmark harness (in-repo):** `scripts/benchmark/classification_benchmark.py` / `scripts/benchmark/benchmark_summary.py`, `scripts/benchmark/run_classification_benchmark.py` (multi-model JSONL + `summary.json`), `scripts/benchmark/summarize_benchmark_rows.py` (offline re-aggregation), unit tests under `tests/unit/test_classification_benchmark.py` and `tests/unit/test_benchmark_summary.py`. Curated rows live under `data/` (combined `issue_benchmark_dataset.csv` plus bucket CSVs). **Jira sampler:** `scripts/benchmark/build_benchmark_dataset.py` (changelog-derived keys; uses `GET /rest/api/3/search/jql` with `nextPageToken` because Cloud removed legacy search). **Composition:** keep the CSV Bug-centric; no requirement to rebalance toward equal Story buckets while Story outcomes stay out of scope—add rows when they help Bug-path / priority signal, and keep human ground truth vs Jira fields explicit where rows encode corrections.
- [x] Move prompt management to Langfuse for easier prompting and prompt version control.
- [ ] **Langfuse root trace cost in trace list:** OpenRouter token usage and cost fields (when returned) are forwarded to nested `inference_*` Langfuse generations; nested views and cost dashboards can reflect that. The top-level trace row / trace menu may still show `$0.00` for `triage_issue_pipeline`. Revisit later (SDK trace vs span model, trace-level aggregates vs UI, or Langfuse product behavior). Good enough for MVP observability.
- Done when: at least one baseline model is scored on the current curated set and swapping `TRIAGE_TEXT_MODEL` (or passing alternate model ids to the benchmark runner) reproduces comparable runs with saved result artifacts for A/B comparison (local `benchmark_runs/` is gitignored; operators keep artifacts outside git or attach as CI artifacts).
