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
  4. Surface guidance via internal comment (and mismatch labels when applicable): suggest reclassification to Story and/or priority with reasoning. Optional auto-apply is feature-flagged: Bug deescalation (`TRIAGE_AUTO_APPLY_DEESCALATION`), Bug escalation (`TRIAGE_AUTO_APPLY_ESCALATION`), and Bug->Story (`TRIAGE_AUTO_APPLY_BUG_TO_STORY`) can mutate Jira fields independently.
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
  - [x] Post internal comment with recommended values and concise reasoning for Story mismatches and Bug priority mismatches (de-escalation and prioritization) using a fixed **TriageBot** template; numeric confidence stays in API/audit only, not in the Jira body; optional reporter @mention when `reporter_account_id` is present on the fetched issue.
  - [x] Apply mismatch-specific labels only when applicable: `triagebot-likely-story` (type mismatch when recommending Story), `triagebot-priority-mismatch` (Bug priority mismatch, de-escalation and prioritization).
  - [x] Add opt-in Jira field auto-apply toggles (default off): `TRIAGE_AUTO_APPLY_DEESCALATION` updates Jira priority for deescalation recommendations; `TRIAGE_AUTO_APPLY_ESCALATION` updates Jira priority for escalation recommendations; `TRIAGE_AUTO_APPLY_BUG_TO_STORY` updates issue type for Bug->Story recommendations.
- [x] On `TriageFailure`, apply **no** labels and post **no** comment. The issue stays unlabeled so the next scheduled scan retries it automatically until success or `created >= -30m` ages it out.
- [x] **Local CLI E2E (developer path):** With `JIRA_CLOUD_ID`, `JIRA_USER_EMAIL`, and model keys set, `scripts/run_triage_cli.py` / `triage_manual_cli.run_cli_triage` runs the full pipeline for a given issue key: fetch → classify → optional priority → mismatch → `JiraTriageActionExecutor` (`source="manual_trigger"`). This is the supported way to run triage from a laptop against real Jira without any Jira-side integration. Optional `--read-only` performs a dry run with no Jira writes.
- [x] **Bulk JQL CLI (evaluation batches):** `scripts/run_bulk_triage_cli.py` / `triage_bulk_cli.py` — JQL search (`jira_jql_search`), per-issue triage, JSON report with current Jira fields and per-step inference; read-only by default (`--apply` / `--comment` opt in to Jira writes). See README *Bulk triage (CLI)*.
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
- [x] **Priority-step JSON parse failure fix:** investigated intermittent `InvalidTriageRecommendationError`
  on the priority step (Langfuse + live OpenRouter replay traced it to the reasoning model
  occasionally wrapping valid JSON in a markdown code fence). Parser now tolerates a wrapping
  fence and prose around the JSON object; classification/priority requests set OpenRouter
  `response_format=json_object` + `provider.require_parameters` so only compliant providers are
  used; priority step retries once on a parse failure before failing the run; failed generations
  now record raw model output/usage in Langfuse and a bounded snippet in the `triage_failed` audit
  event (redactable via `TRIAGE_AUDIT_REDACT_MODEL_OUTPUT`). See `memory.md` 2026-08-20 entry.
- Done when: transient failures are retried safely, permanent failures are observable, and hosting health checks are supported.

## 7. Image context extraction (vision-as-preprocessor)
Bug tickets often pair terse text with load-bearing screenshots (stack traces, error toasts, broken UI states). Convert image **attachments** to text once per attachment so both classification and priority steps consume the same enriched `_issue_block` without going multimodal. Triage context now includes Jira comments under a configured char budget, and image selection can include comment-referenced attachments after description-inline attachments.

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
- [x] Attachment selection / budget: prioritize inline-in-description image attachments, then fill remaining `TRIAGE_IMAGE_CONTEXT_MAX_ATTACHMENTS` slots with comment-referenced image attachments.
- [x] Observability:
  - [x] Add Langfuse span `image_context_extraction` nested under `triage_issue_pipeline`, carrying attachment counts, total bytes, and vision cost when returned.
  - [x] Emit `image_context_extracted` audit event via the existing `AuditStore` fan-out (per-attachment latency, failure breakdown, `run_id` correlation).
  - [x] Extend `triage_completed` / `triage_failed` telemetry with `image_context_attachments_considered` and `image_context_attachments_extracted` so dashboards can stratify runs with vs. without image signal.
- [x] Benchmark integration: add an enable / disable flag to `scripts/benchmark/run_classification_benchmark.py`, persist resolved image context into JSONL rows, and extend `summarize_benchmark_rows.py` to break out accuracy by "has images" vs. "text-only".
- [x] Local CLI parity: `scripts/run_triage_cli.py` honors `TRIAGE_IMAGE_CONTEXT_ENABLED` and prints a compact attachment summary alongside the recommendation for manual smoke checks.
- [x] Docs: update `README.md` with the feature flag, vision model selection, cost / PII notes, and an example `_issue_block` rendering with attachments.

Out of scope (do not pull in):
- True multimodal classification / priority — doubles image cost across the sequential steps and breaks the text-only Langfuse prompt versioning in `langfuse_prompt_config.py`.
- Pixel-level UI reasoning beyond a 1–3 sentence vision summary.

Done when: issues with sparse text and load-bearing screenshots produce measurably better classification / priority on the benchmark dataset; extraction is feature-flagged and disabled by default; `pytest -m "unit or integration"`, `mypy .`, and `pytest -m lint` all pass; per-image failures degrade gracefully without aborting triage; benchmark runs can be stratified by image presence.

## 8. Zendesk context augmentation
Linked Zendesk tickets often carry the original customer report, follow-up comments, and screenshots that were later copied into the Jira issue. Baseline enrichment fetches ticket summaries into the shared `_issue_block`; follow-on work hardens deduplication and extends the §7 vision preprocessor to Zendesk-sourced images without double-counting Jira attachments.

**Baseline (done):**
- [x] Parse Zendesk ticket ids from Jira custom fields (`customfield_10158`, `customfield_10162`) with id-level dedupe across both fields (`_merge_zendesk_ticket_ids`).
- [x] Fetch ticket summaries from Zendesk API (`GET /api/v2/tickets/{id}.json`) when credentials are configured (`ZENDESK_BASE_URL`, `ZENDESK_API_TOKEN`, `ZENDESK_USER_EMAIL` or `ZENDESK_AGENT_EMAIL`).
- [x] Append linked ticket subject / description / status / priority to `_issue_block` (`issue_text_block.format_issue_text_block`).
- [x] Wire enrichment into `TriageHandler.run_sync` behind `TRIAGE_ZENDESK_CONTEXT_ENABLED` (soft-fail on fetch errors; triage continues).
- [x] Smoke script parity: `scripts/fetch_jira_issue.py` fetches all ids from Jira custom fields and prints `zendesk_tickets` JSON.

**Remaining — resolution-aware comment summarization (priority-balance fix):**
Raw ticket `subject` / `description` overweights the *initial* customer report and ignores *end-of-thread* resolution context. Observed failure: a P3 was escalated to P0 because the linked ticket's opening prose described a major outage, while the actual cause (temporary third-party outage, since recovered) only appears in the last comments. Dumping full comment threads would amplify this and blow up the prompt (up to `TRIAGE_ZENDESK_MAX_TICKETS` tickets/issue). Fix: one LLM summarization call per ticket that extracts recency- and resolution-aware signals, rendered into `_issue_block` instead of raw prose, plus an explicit priority-prompt rule.
- [x] **Fetch ticket comments:** `GET /api/v2/tickets/{id}/comments.json` in `ZendeskTicketFetcher` (reuse Basic auth), newest-first, capped by `TRIAGE_ZENDESK_MAX_COMMENTS_PER_TICKET`; include public + internal comments (internal notes often hold root-cause/resolution). Soft-fail per ticket.
- [x] **New `ZendeskCommentSummarizer` adapter** (mirror `OpenRouterVisionImageContextExtractor`): NoOp when flag off; OpenRouter-backed using `TRIAGE_ZENDESK_SUMMARY_MODEL` (default `TRIAGE_TEXT_MODEL`) and `TRIAGE_ZENDESK_SUMMARY_TIMEOUT_SECONDS`. Per-ticket input pre-trimmed newest-first by `TRIAGE_ZENDESK_COMMENTS_CHAR_BUDGET`.
- [x] **Structured summary contract:** parse fixed sections `INITIAL_IMPACT` / `LATEST_STATUS` / `RESOLUTION_HINTS` / `OPEN_RISKS` into a `ZendeskResolutionSummary` model on `LinkedZendeskTicket`; on inference/parse failure leave summary `None` and fall back to current subject/description rendering.
- [x] **Summary prompt:** add `zendesk_summary_system_prompt` / `zendesk_summary_user_instruction` to `prompt_templates.json` (+ optional Langfuse names) with a small `core` composer mirroring `vision_prompt_composer.py`. Pass the Jira summary/description/reproduction steps as context and instruct the model to emit only net-new signal (the Jira ↔ Zendesk dedupe above).
- [x] **Render in `_issue_block`:** when a resolution summary is present, emit a compact `Zendesk resolution signals` block (initial impact + latest status + hints + open risks) instead of the full ticket `description`; collapse near-identical per-ticket summaries and repeated hints (the cross-ticket dedupe above).
- [x] **Priority guidance (the actual balance fix):** add a rule to `core/policy/priority_definition.md` (and reinforce in `priority_template`): when older severe impact is followed by confirmed recovery or a temporary external/third-party cause, do not escalate on historical peak alone; weigh current impact highest.
- [x] **Wire into `_enrich_with_zendesk`:** after `fetch_linked_tickets`, run the summarizer (soft-fail) and `model_copy` summaries onto `issue.zendesk_tickets`; build it in `build_default_triage_handler` behind `TRIAGE_ZENDESK_COMMENT_SUMMARY_ENABLED`.
- [x] **TDD first:** failing tests for summary parsing, newest-first budget trim, soft-fail to `None`, NoOp when disabled, render-vs-fallback + hint dedupe, and the motivating scenario (old severe comment + newer "third-party outage resolved" comment → `LATEST_STATUS`/`RESOLUTION_HINTS` capture recovery and de-emphasize the peak).

**Remaining — Zendesk images (extends §7 vision preprocessor):**
This builds on the comment fetch already landed for summarization (`ZendeskTicketFetcher._fetch_comments` / `ZendeskCommentRef`) and the §7 Jira vision preprocessor. It is independent of the summarization LLM call but shares the per-ticket render structure, so it should land after the resolution-signals rendering is settled.
- [x] **Discover Zendesk inline images from already-fetched comments:** reuse the comments fetched for summarization instead of a second API call — extend `ZendeskCommentRef` (and ticket-description parsing) to capture inline `![](…)` / `<img>` URLs and comment/ticket attachment refs (Zendesk returns these on the same `comments.json` payload; request `include_inline_images` there). Avoid double-fetching `GET /api/v2/tickets/{id}/comments.json`.
- [x] **Fetch Zendesk attachment bytes** with the same Zendesk Basic auth used for ticket/comment fetch; follow redirects for signed/token URLs.
- [x] **Cross-source image dedupe (required before vision):** do not run vision on a Zendesk image that is already represented on the Jira issue. Match candidates against Jira `AttachmentRef` entries using a tiered key: (1) exact filename match (e.g. `blobid0.png`, `image-20260219-160437.png`), (2) same MIME + size within tolerance, (3) optional content hash when bytes are cheap to fetch. Skip duplicates; record skip reason in audit metadata.
- [x] **Budget sharing with Jira images:** Zendesk-only images consume the same `TRIAGE_IMAGE_CONTEXT_MAX_ATTACHMENTS` / byte budget as inline Jira attachments (Jira inline images first, then deduped Zendesk-only images up to the cap). Note this is the §7 vision attachment budget, separate from the `TRIAGE_ZENDESK_COMMENTS_CHAR_BUDGET` text budget used by summarization.
- [x] **Render within the per-ticket block:** attach transcripts to the same `Zendesk resolution signals` block as the ticket's summary (e.g. `[Zendesk ticket #47322 attachment: …]`) rather than a disjoint section, so prompts attribute screenshot context to the right ticket.
- [x] **TDD first:** failing unit tests for URL parsing, dedupe keys, budget enforcement, and soft-failure placeholders before implementation.

**Remaining — ticket deduplication:**
- [x] **Id union dedupe:** when custom fields are empty and ids are parsed from issue body text, union with any ids discovered in summary/description/reproduction steps without duplicates (today custom-field path and body-text path are mutually exclusive in `collect_linked_ticket_ids`).
- [x] **Cross-ticket content dedupe (post-summarization):** depends on resolution-aware summarization below — once each ticket is condensed into a `ZendeskResolutionSummary`, dedupe operates on the *summaries*, not raw subject/description blocks. When multiple linked tickets are merged/related/re-opened escalations producing near-identical summaries, collapse them into one rendered block. Heuristics: normalized hash of the summary text, shared subject prefix, or explicit Zendesk `via`/`problem_id` linkage when available from API. Subsumes the existing per-hint dedupe in the "Render in `_issue_block`" item.
- [x] **Jira ↔ Zendesk text dedupe (prefer prompt-level):** the summarizer receives the Jira summary/description/reproduction steps as context and is instructed to emit only net-new signal (omit content already in the Jira issue), so duplicate prose is avoided at generation time rather than stripped post-hoc. Keep a light post-render guard only if summaries still echo Jira text verbatim.

**Remaining — observability & ops:** 
- [x] Add Langfuse span `zendesk_context_fetch` (ticket count, latency, per-ticket failures) nested under `triage_issue_pipeline`.
- [x] Add Langfuse span `zendesk_context_summary` (tickets considered/summarized, per-ticket generation, total cost) and a `ZendeskContextSummarizedAuditEvent` (considered vs summarized, failure breakdown).
- [x] Emit `zendesk_context_fetched` audit event (ids requested vs returned, dedupe counts, fetch error breakdown).
- [x] Extend `triage_completed` telemetry with `zendesk_tickets_considered` / `zendesk_tickets_fetched` (and `zendesk_tickets_summarized`).
- [x] Document Zendesk enrichment flags in `README.md` and `.env.example`; update §10 “no Zendesk intake” limitation to reflect linked-ticket enrichment scope (id/summary/image dedupe implemented).
- [x] **OAuth auth alternative:** `TRIAGE_ZENDESK_ENABLE_OAUTH=true` switches `ZendeskTicketFetcher` to Bearer tokens via confidential `client_credentials` (`ZendeskOAuthClient`: in-memory mint/cache on demand). Requires `ZENDESK_IDENTIFIER`, `ZENDESK_SECRET`, and `ZENDESK_BASE_URL` or `ZENDESK_SUBDOMAIN`; no redirect URI, callback routes, or token file persistence.

**OAuth access token expiry (Zendesk; long-lived tokens expire from 28 Jul 2026):**
Zendesk is moving integrations to **client credentials** (no refresh tokens). When a short-lived access token expires, the client must request a new one with the client secret — same model Adam flagged for the triage bot.

- [x] **Auto remint on expiry:** `ZendeskOAuthClient` (`adapters/zendesk_oauth.py`) POSTs `grant_type=client_credentials` to `/oauth/tokens`, caches `access_token` + `expires_at` in memory, and remints when within 60s of expiry (`_TOKEN_REFRESH_BUFFER_SECONDS`). `ZendeskTicketFetcher._auth_header()` calls `get_access_token()` per request so a token expiring mid-triage is refreshed on the next call. Unit tests: `test_oauth_client_reuses_cached_access_token`, `test_oauth_client_remints_expired_access_token`.
- [x] **Prod cutover before Jul 2026:** confirm deployment has `TRIAGE_ZENDESK_ENABLE_OAUTH=true` and confidential-client env (`ZENDESK_IDENTIFIER`, `ZENDESK_SECRET`, base URL/subdomain). Default **API token + Basic auth** is unaffected by OAuth access-token expiry but does not satisfy the client-credentials migration if Zendesk retires the old token model for this integration.
- [x] **Optional hardening:** on Zendesk API `401`, clear cached token and retry once (covers clock skew or early revocation; proactive `expires_in` refresh should suffice for normal runs). `ZendeskOAuthClient.clear_cached_token()`; `ZendeskTicketFetcher._get_with_oauth_401_retry()` on ticket, comments, and image GETs. Unit tests: `test_oauth_client_clear_cached_token_forces_remint`, `test_fetcher_retries_ticket_fetch_after_oauth_401`, `test_fetcher_does_not_retry_more_than_once_on_persistent_oauth_401`.

## 9. Polish & Docs
- [x] Document architecture and module responsibilities after refactor (`api`, `core`, `adapters`, `observability`).
- [x] Add runbook for local development, env setup, and test execution commands.
- [x] Add Jira Automation setup recipe: scheduled rule cadence (every 5 min <= JQL window), reference JQL (`project = ... AND issuetype = Bug AND labels not in (triagebot-reviewed) AND created >= -30m AND created <= -5m`), and body template (`issue_key`, `project`, `source: bug_created|priority_changed`).
- [x] Document `triagebot-reviewed` lifecycle: always applied on success, remove to force re-triage, absent on >30m-old issues indicates manual-QA fallback case.
- [x] Document observability usage: where to inspect raw model output/confidence, and how to trace a run by `run_id`.
- [x] Document known MVP limitations and out-of-scope items (auto-apply is opt-in per direction, no full Zendesk intake / ticket creation, no full RAG). Linked-ticket enrichment is tracked in **§8**.
- [x] Record default model-selection rationale and confidence calibration strategy for next phase tuning.
- Done when: a new engineer can run, operate, observe, and troubleshoot the service using repo docs only.

## 10. Triage analytics integration (decision-event emission)
Upstream work in **this repo** required to feed the separate dashboard service
defined in `docs/specification.md` (`jira-triage-dashboard`). This repo emits a
single fire-and-forget decision POST after each completed triage run; the
dashboard service owns persistence, SQL views, and Grafana. Scope here is emit
only — no DB, no outcome / revert tracking, and no triage hot-path blocking.

- [x] **Telemetry enrichment (no audit model expansion):** add `intake_issue_type` and `intake_priority` to `TriageCompletedAuditEvent.telemetry` in `src/triage_service/core/triage_handler.py` (Story intake keeps priority null). Keep `TriageCompletedAuditEvent` schema unchanged per `docs/specification.md` assumptions.
- [x] **Add analytics HTTP client + config:** after recording `triage_completed`, POST a decision payload to `ANALYTICS_DASHBOARD_URL` with `X-Analytics-Token: ANALYTICS_TOKEN`. If URL is unset, skip. Use short timeout and swallow/log all errors (warning level) so triage never fails on analytics transport.
- [x] **Build payload from existing event + telemetry:** emit the dashboard `DecisionEvent` fields from `docs/specification.md` (`event_type`, `run_id`, `issue_key`, `project`, `source`, intake/recommended state, `applied_*`, `confidence`, `inference_cost_usd`, `reason`, `triaged_at`, optional `issue_created_at`, optional `issue_name`) without introducing new persistence concerns in this service.
- [x] **Tests (TDD first):** unit coverage for URL-unset no-op, payload mapping (including Story null-priority handling), auth header, and failure swallowing; integration coverage for successful POST and timeout/error paths.
- [x] **Ops wiring + docs:** add `ANALYTICS_DASHBOARD_URL` and `ANALYTICS_TOKEN` to `.env.example` / `README.md` with explicit failure-safe semantics and endpoint expectations (`/api/v1/decisions` on dashboard service).
- Done when: completed triage runs attempt a non-blocking POST to the dashboard when configured; URL-unset behavior is a no-op; failures are observable in logs but never fail triage; emitted payload matches `docs/specification.md`; and `pytest -m lint`, `mypy .`, and `pytest -m "unit or integration"` pass.

### 10.1. Catch-up script

- [x] Write `scripts/build_dashboard_seed.py catchup` (or flag): queries Langfuse for traces newer than the latest `triaged_at` in the seed file
- [x] Outputs `catchup.json` with any rows not already in the seed
- [x] Idempotent: re-running produces no duplicates (keyed on `run_id`)
- [x] Runs on container startup before DB ingest — fills the gap between last committed seed and now

## 11. Post-MVP
- [x] **Classification benchmark harness (in-repo):** `scripts/benchmark/classification_benchmark.py` / `scripts/benchmark/benchmark_summary.py`, `scripts/benchmark/run_classification_benchmark.py` (multi-model JSONL + `summary.json`), `scripts/benchmark/summarize_benchmark_rows.py` (offline re-aggregation), unit tests under `tests/unit/test_classification_benchmark.py` and `tests/unit/test_benchmark_summary.py`. Curated rows live under `data/` (combined `issue_benchmark_dataset.csv` plus bucket CSVs). **Jira sampler:** `scripts/benchmark/build_benchmark_dataset.py` (changelog-derived keys; uses `GET /rest/api/3/search/jql` with `nextPageToken` because Cloud removed legacy search). **Composition:** keep the CSV Bug-centric; no requirement to rebalance toward equal Story buckets while Story outcomes stay out of scope—add rows when they help Bug-path / priority signal, and keep human ground truth vs Jira fields explicit where rows encode corrections.
- [x] Move prompt management to Langfuse for easier prompting and prompt version control.
- [x] Add link to relevant confluence documents in bug vs comment
- [ ] **Langfuse root trace cost in trace list:** OpenRouter token usage and cost fields (when returned) are forwarded to nested `inference_*` Langfuse generations; nested views and cost dashboards can reflect that. The top-level trace row / trace menu may still show `$0.00` for `triage_issue_pipeline`. Revisit later (SDK trace vs span model, trace-level aggregates vs UI, or Langfuse product behavior). Good enough for MVP observability.
- Done when: at least one baseline model is scored on the current curated set and swapping `TRIAGE_TEXT_MODEL` (or passing alternate model ids to the benchmark runner) reproduces comparable runs with saved result artifacts for A/B comparison (local `benchmark_runs/` is gitignored; operators keep artifacts outside git or attach as CI artifacts).
- [ ] Inject image context INSIDE description/comment body at the place where they were inserted, not as bulk attachments by the end.
- [ ] Add advisory step post triage to add to reasoning / additional comment when ticket formatting / description could be improved.
- [ ] Benchmark / bulk-triage stratification: accuracy breakdown for issues with vs without linked Zendesk context (and with vs without Zendesk-only images once vision path lands).
- [x] **Langfuse v4 project migration (code-only):** SDK bumped `4.6.1` → `4.14.4` (`langfuse>=4.14` in `pyproject.toml`); `src/triage_service/observability/*` already used only v4 APIs (no deprecated calls found). Migrated `scripts/build_dashboard_seed.py` off the deprecated v1 `GET /api/public/observations` / `GET /api/public/traces/{id}` (sunset 2026-11-16) onto `GET /api/public/v2/observations` (cursor pagination, field groups, root-observation-as-trace reconstruction). See `memory.md` 2026-08-20 entry for details.
- [ ] **Langfuse v4 migration — project-dependent follow-up:** requires Langfuse project/CLI access (not configured in this environment). Check the Evaluators UI for active **Legacy** rows and migrate any found; audit Project Settings → Integrations for Blob Storage/Mixpanel/PostHog exports; send a representative triage run to a non-prod Langfuse project to confirm v4 ingestion end-to-end (mocked unit tests do not prove backend ingestion).

Out of scope (do not pull in):
- Full Zendesk intake / ticket creation from triage.
- Verbatim Zendesk comment thread replay into `_issue_block` — comments are condensed into a bounded resolution summary, never dumped raw.
- True multimodal classification — Zendesk images flow through the same text-only vision preprocessor as §7.

Done when: linked Zendesk tickets enrich triage without duplicate ticket prose or duplicate screenshot vision calls; comment threads are condensed into recency/resolution-aware summaries so end-of-thread recovery context balances (not reinforces) priority; Zendesk-only images improve classification on benchmark cases where Jira text is sparse; dedupe, fetch, and summarization metrics are visible in audit/Langfuse; all gates pass.

## 12. Integration tests (deferred until post-deploy stabilization)
- [ ] Add API contract tests for `POST /triage` request/response shape and validation errors.
- [ ] Add integration tests for Jira webhook adapter invoking synchronous triage pipeline.
- [ ] Add integration tests for Jira action executor against mocked Jira API.
- [ ] Add integration tests for policy retrieval adapter using mocked content source.
- [ ] Add integration tests for mismatch/no-mismatch behavior including label combinations and sequential flow (Story outcome skips priority inference; Bug outcome invokes it).
- [ ] Add integration coverage for audit emission on success/failure paths (including model-output capture and confidence persistence).
- Done when: `pytest -m integration` passes deterministically and covers service boundaries + observability surfaces.
