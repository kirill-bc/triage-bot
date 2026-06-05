# Project memory

## 2026-06-05 (close — dashboard seed default issue exclude)

- **Phase close (`/close-phase`):** From `.venv`, `pytest -m lint` (5 passed), `mypy .` (115 files), `pytest -m "unit or integration"` (**607 passed**, **1 skipped** `OPENROUTER_LIVE_SMOKE`, **6 deselected**). **Dashboard seed tooling:** `scripts/build_dashboard_seed.py` `backfill` / `catchup` now default `--exclude-issues` to **BC-22932** (known bad/outlier row for dashboard bootstrap); pass `--exclude-issues ""` to include it. `_add_common_langfuse_args` accepts `exclude_issues_default` for subcommand-specific defaults. Tests: `test_exclude_blacklisted_issues_drops_matching_keys`, `test_backfill_main_passes_default_excluded_issues`. Docs: `README.md` Analytics dashboard section.
- **Next backlog:** §11 Post-MVP (Langfuse root trace cost, image inline placement, advisory formatting step, Zendesk benchmark stratification); §12 integration tests (deferred).

## 2026-06-04 (close — analytics emission hardening + intake priority default)

- **Phase close (`/close-phase`):** From `.venv`, `pytest -m lint` (5 passed), `mypy .` (115 files), `pytest -m "unit or integration"` (**605 passed**, **1 skipped** `OPENROUTER_LIVE_SMOKE`, **6 deselected**). **Analytics client hardening:** `HttpAnalyticsDecisionClient` background POST threads are **daemon** so process shutdown is not blocked; `emit_completed_decision` wraps payload build/dispatch in try/except and logs `analytics_decision_emit_failed` without affecting triage. Tests: `test_start_background_task_uses_daemon_thread`, `test_http_client_swallows_payload_build_errors`. **Intake priority telemetry:** `_normalized_intake_priority` defaults Bug intake to **`P3`** when Jira priority is missing or not in `P0`–`P4` (Story intake still null); improves dashboard promoted/demoted accuracy. Tests: `test_triage_completed_telemetry_defaults_intake_priority_to_p3_*`, updated story-path audit expectation. Docs: `docs/specification.md` intake field note.
- **Prior same day:** §10.1 Langfuse catch-up + dashboard seed tooling (`build_dashboard_seed.py` subcommands, `issue_created_at` / `issue_name` on live analytics payload).
- **Next backlog:** §11 Post-MVP (Langfuse root trace cost, image inline placement, advisory formatting step, Zendesk benchmark stratification); §12 integration tests (deferred).

## 2026-06-04 (close — §10.1 Langfuse catch-up + dashboard seed tooling)

- **Phase close (`/close-phase`):** From `.venv`, `pytest -m lint` (5 passed), `mypy .` (115 files), `pytest -m "unit or integration"` (**601 passed**, **1 skipped** `OPENROUTER_LIVE_SMOKE`, **6 deselected**). **§10.1 catch-up script:** consolidated Langfuse → dashboard decision export into `scripts/build_dashboard_seed.py` (removed `scripts/backfill_langfuse_decisions.py`). Subcommands: `backfill` (Langfuse export + optional Jira `fields.created` / `fields.summary` enrichment), `catchup` (incremental rows newer than seed `triaged_at`, idempotent on `run_id`), `enrich` (add `issue_created_at` / `issue_name` to existing JSON). Tests: `test_build_dashboard_seed.py`, `test_catchup_langfuse.py`, `test_enrich_decisions_json.py`. **Live analytics payload:** `AnalyticsDecisionClient` / `build_decision_payload` now emit optional `issue_created_at` and `issue_name`; `TriageHandler._emit_analytics_decision` passes `FetchedIssue.issue_created_at` and summary. **Jira fetch:** `FetchedIssue.issue_created_at` parsed from `fields.created` (`normalize_jira_created_timestamp`); batch helper for seed enrichment. `scripts/fetch_jira_issue.py` smoke JSON includes `issue_created_at`. Docs: `README.md` (Analytics dashboard section), `.env.example`, `docs/specification.md`, `AGENTS.md`, `TODO.md` §10.1 complete.
- **Next backlog:** §11 Post-MVP (Langfuse root trace cost, image inline placement, advisory formatting step, Zendesk benchmark stratification); §12 integration tests (deferred).

## 2026-06-02 (close — §10 analytics decision emission + §8 OAuth 401 retry)

- **Phase close (`/close-phase`):** From `.venv`, `pytest -m lint` (5 passed), `mypy .` (113 files), `pytest -m "unit or integration"` (**564 passed**, **1 skipped** `OPENROUTER_LIVE_SMOKE`, **6 deselected**). **§10 triage analytics integration:** `adapters/analytics_decision_client.py` (`AnalyticsDecisionClient`, `build_decision_payload`, `HttpAnalyticsDecisionClient`, `NoOpAnalyticsDecisionClient`) fire-and-forgets `POST {ANALYTICS_DASHBOARD_URL}/decisions` with `X-Analytics-Token` after each successful triage (`ANALYTICS_HTTP_TIMEOUT_SECONDS`, default `2`; failures logged at warning, never fail triage). `TriageHandler._emit_analytics_decision` maps `TriageCompletedAuditEvent` + executor `TriageActionAppliedFlags` (`core/triage_action_applied.py`) and optional aggregated `inference_cost_usd`. `triage_completed` telemetry adds `intake_issue_type` / `intake_priority` (Story intake keeps priority null). `JiraTriageActionExecutor.apply_triage_outcome` returns applied auto-apply flags. Settings: `ANALYTICS_DASHBOARD_URL`, `ANALYTICS_TOKEN`. Tests: `test_analytics_decision_client.py`, extended `test_triage_handler.py`, `test_settings.py`. **Langfuse backfill helper:** `scripts/build_dashboard_seed.py` exports historical decision rows from Langfuse traces/observations for dashboard bootstrap (`source=backfill`, `applied_*=false`); tests in `test_build_dashboard_seed.py`. Docs: `README.md`, `.env.example`, `docs/specification.md`, `docs/triage_analytics_repo_plan.md`, `TODO.md` §10 complete.
- **§8 OAuth 401 retry (same close):** `ZendeskOAuthClient.clear_cached_token()`; `ZendeskTicketFetcher._get_with_oauth_401_retry()` on ticket, comments, and image GETs (clear cache + one retry on 401). Tests extended in `test_zendesk_oauth.py` and fetcher wiring tests.
- **Next backlog:** §11 Post-MVP (Langfuse root trace cost, image inline placement, advisory formatting step, Zendesk benchmark stratification); §12 integration tests (deferred).

## 2026-06-02 (close — §8 Zendesk OAuth: client_credentials only)

- **Phase close (`/close-phase`):** From `.venv`, `pytest -m lint` (5 passed), `mypy .` (108 files), `pytest -m "unit or integration"` (**545 passed**, **1 skipped** `OPENROUTER_LIVE_SMOKE`, **6 deselected**). **§8 OAuth simplification:** replaced authorization-code flow (browser `/zendesk/oauth/*` routes, JSON token file, refresh) with confidential **`client_credentials`** and in-memory token cache in `adapters/zendesk_oauth.py` (`ZendeskOAuthClient.get_access_token`). Removed `api/zendesk_oauth_routes.py`; dropped settings `ZENDESK_REDIRECT_URI`, `TRIAGE_PUBLIC_BASE_URL` (OAuth-only), `ZENDESK_OAUTH_TOKEN_FILE`; removed `.zendesk_oauth_tokens.json` from `.gitignore`. `ZendeskTicketFetcher` still uses Bearer when `TRIAGE_ZENDESK_ENABLE_OAUTH=true` and `zendesk_oauth_configured`; `fetch_jira_issue.py` error text points at identifier/secret/base URL. Tests rewritten in `test_zendesk_oauth.py` (mint, cache, expiry refresh, config guards); fetcher OAuth wiring tests in `test_zendesk_ticket_fetcher.py`. Docs: `README.md`, `.env.example`, `AGENTS.md`, `TODO.md` §8 OAuth item.
- **§8 still open:** benchmark / bulk-triage stratification (evaluation). **Next backlog:** §9 integration tests (deferred), §10 model-selection rationale doc, §12 triage-analytics decision events.

## 2026-06-02 (close — §8 Zendesk hardening: ADF parsing, image-URL trust, token perms)

- **Phase close (`/close-phase`):** From `.venv`, `pytest -m lint` (5 passed), `mypy .` (109 files), `pytest -m "unit or integration"` (**547 passed**, **1 skipped** `OPENROUTER_LIVE_SMOKE`, **6 deselected**). Security/correctness hardening pass on Zendesk enrichment (no new TODO features; §8 evaluation item still open):
  - **ADF text extraction (`jira_issue_fetcher._extract_text_from_adf`):** block-container nodes (`doc`, `bulletList`, `listItem`, `blockquote`, table/panel/expand types) now join children with `\n`; `hardBreak` → `\n`; lists join with `\n`. Regression fixed: adjacent paragraphs/list items carrying ticket ids no longer concatenate into one bogus numeric token (`123` + `456` → `123456`). Tests: `test_parse_zendesk_ticket_ids_from_adf_multiparagraph_field`, `..._bullet_list_field`.
  - **Zendesk image attachment id (`image_context_extractor._zendesk_image_attachment_id`):** keys off `attachment_id` → `filename` → `"inline_image"` instead of the raw signed URL, so synthetic ids no longer embed signed-URL query tokens (`X-Amz-Signature`, etc.). Tests: `test_zendesk_image_attachment_id_avoids_sensitive_url_tokens`, `..._prefers_attachment_id_then_filename`.
  - **Image-fetch auth scoping (`zendesk_ticket_fetcher.is_trusted_zendesk_image_url` + `_image_fetch_headers`):** Basic/Bearer `Authorization` is only attached when the image host matches the configured Zendesk base URL or `*.zendesk.com` / `*.zdassets.com`; external/attacker-controlled inline image URLs are fetched without credentials. Tests: `test_is_trusted_zendesk_image_url_*`, `test_fetch_image_bytes_omits_auth_for_external_image_url`, `..._external_url_does_not_require_zendesk_credentials`.
  - **Newest-first comment pagination (`_comments_url`):** comments API now requests `sort_order=desc&per_page={max_comments}` so the newest comments (end-of-thread resolution context) are fetched on long tickets instead of the oldest-first default page. Tests: `test_fetch_ticket_comments_requests_newest_first_page_from_api`, `..._include_inline_images_and_newest_first`.
  - **OAuth token file perms (`zendesk_oauth.ZendeskOAuthTokenStore`):** token JSON written `0o600` (temp + final). Test: `test_zendesk_oauth.py`.
- **§8 still open:** benchmark / bulk-triage stratification (evaluation item). **Next backlog:** §9 integration tests (deferred), §10 model-selection rationale doc.

## 2026-06-01 (close — §8 Zendesk OAuth auth)

- **Phase close (`/close-phase`):** From `.venv`, `pytest -m lint` (5 passed), `mypy .` (109 files), `pytest -m "unit or integration"` (**534 passed**, **1 skipped** `OPENROUTER_LIVE_SMOKE`, **6 deselected**). **§8 OAuth auth:** optional `TRIAGE_ZENDESK_ENABLE_OAUTH=true` switches Zendesk API calls from Basic auth (`ZENDESK_API_TOKEN`) to Bearer tokens via authorization-code flow. New `adapters/zendesk_oauth.py` (`ZendeskOAuthClient`, `ZendeskOAuthTokenStore`, HMAC `state`, token refresh) and `api/zendesk_oauth_routes.py` (`GET /zendesk/oauth/redirect-uri`, `/authorize`, `/callback`). Settings: `ZENDESK_IDENTIFIER`, `ZENDESK_SECRET`, `ZENDESK_REDIRECT_URI` or `TRIAGE_PUBLIC_BASE_URL`, `ZENDESK_OAUTH_SCOPE`, `ZENDESK_OAUTH_TOKEN_FILE` (default `.zendesk_oauth_tokens.json`, gitignored). `ZendeskTicketFetcher` uses Bearer when OAuth enabled; `fetch_jira_issue.py` hints when tokens missing. Tests: `test_zendesk_oauth.py`. **§8 still open:** benchmark / bulk-triage stratification. **Next backlog:** §9 integration tests (deferred), §10 model-selection rationale doc.

## 2026-06-01 (close — §8 Zendesk hardening)

- **Phase close (`/close-phase`):** From `.venv`, `pytest -m lint` (5 passed), `mypy .` (106 files), `pytest -m "unit or integration"` (**523 passed**, **1 skipped** `OPENROUTER_LIVE_SMOKE`, **6 deselected**). **§8 hardening pass:** shared `adapters/zendesk_id_patterns.py` for Jira field + body id parsing; shared `zendesk_comment_newest_first_sort_key` (fetcher + summarizer budget trim now sorts unsorted comment lists); dedicated OpenRouter client for Zendesk summarizer so `TRIAGE_ZENDESK_SUMMARY_MODEL` is honored (no longer shares main triage inference client); `is_zendesk_image_context` public helper; Zendesk image collection excludes non-image filenames without image MIME (`.pdf`, `.csv`, etc.); `_summarize_zendesk_tickets` raises `RuntimeError` instead of `assert` when summarizer missing. **§8 still open:** benchmark / bulk-triage stratification (evaluation item). **Next backlog:** §9 integration tests (deferred), §10 model-selection rationale doc.

## 2026-06-01 (close — §8 Zendesk phase)

- **Phase close (`/close-phase`):** From `.venv`, `pytest -m lint` (5 passed), `mypy .` (105 files), `pytest -m "unit or integration"` (**514 passed**, **1 skipped** `OPENROUTER_LIVE_SMOKE`, **6 deselected**). **§8 Zendesk context augmentation** is feature-complete for MVP except benchmark stratification (§8 evaluation item): linked-ticket fetch + id union dedupe; resolution-aware comment summarization (`ZendeskCommentSummarizer`, `TRIAGE_ZENDESK_COMMENT_SUMMARY_ENABLED`); cross-ticket / Jira↔Zendesk text dedupe; Zendesk-only vision path with cross-source image dedupe and shared attachment budget; per-ticket render in `_issue_block`; Langfuse spans `zendesk_context_fetch` / `zendesk_context_summary` and audit events `zendesk_context_fetched` / `zendesk_context_summarized`; `triage_completed` zendesk counters; `zendesk_context` on manual CLI JSON; `.env.example` / `README.md` / `AGENTS.md` updated. **Remaining §8:** benchmark / bulk-triage accuracy breakdown with vs without Zendesk context (and Zendesk-only images). **Next backlog:** §9 integration tests (deferred), §10 model-selection rationale doc.

## 2026-06-01 (§8 Zendesk observability)

- **§8 Zendesk observability:** Langfuse spans `zendesk_context_fetch` and `zendesk_context_summary` nested under `triage_issue_pipeline` (enrichment moved inside trace); per-ticket `inference_zendesk_summary` generations when summarization runs. Audit events `zendesk_context_fetched` (ids requested/fetched, dedupe count, fetch failure) and `zendesk_context_summarized` (considered/summarized, cost, per-ticket failure breakdown). `triage_completed` / `triage_failed` telemetry adds `zendesk_tickets_considered` / `_fetched` / `_summarized`. `collect_linked_ticket_ids_with_stats` returns dedupe/cap counts; `ZendeskSummarizationResult.per_ticket` metrics. Tests: `test_zendesk_context_observability.py`.

## 2026-05-29 (§8 Jira ↔ Zendesk text dedupe)

- **§8 Jira ↔ Zendesk text dedupe:** Prompt-level net-new instruction in `prompt_templates.json` / `zendesk_summary_prompt_composer.py` (Jira summary/description/repro passed as `jira_context`). Post-render guard `core/zendesk_jira_text_dedupe.py`: sections ≥24 chars that verbatim-echo normalized Jira fields render as `(same as Jira issue above)` in `issue_text_block._format_zendesk_resolution_signals`. Tests: `test_zendesk_jira_text_dedupe.py`, `test_zendesk_summary_prompt_composer.py`.

## 2026-05-29 (§8 cross-ticket summary dedupe)

- **§8 cross-ticket content dedupe:** `core/zendesk_summary_dedupe.py` collapses duplicate `Zendesk resolution signals` across linked tickets using exact summary fingerprint, near-identical core (`latest_status` + `resolution_hints`) with shared subject prefix (≥12 chars), or `problem_id` linkage to a prior ticket with matching core. `LinkedZendeskTicket.problem_id` parsed from Zendesk API; `issue_text_block` uses `ZendeskSummaryDedupeState` instead of inline fingerprint sets. Distinct core summaries still render when only `problem_id` links tickets. Tests: `test_zendesk_summary_dedupe.py`, extended `test_issue_text_block.py`, `test_fetch_ticket_parses_problem_id_from_api`.

## 2026-05-29 (§8 Zendesk image render in ticket block)

- **§8 render Zendesk vision in ticket block:** `format_issue_text_block(..., image_contexts=…)` groups contexts by `zendesk:{ticket_id}:…` attachment id and renders `[Zendesk ticket #… attachment: …]` (+ summary or soft-fail placeholder) inside each linked-ticket section after resolution signals or description fallback; attachments still render when resolution summary is omitted by cross-ticket dedupe. `prompt_composer._issue_block` passes contexts through and keeps Jira-only images in the trailing `Attached images:` section. Tests: `test_issue_text_block.py` (within signals block, description fallback, soft-fail, deduped summary), `test_prompt_composer.py` (mixed sources, per-ticket routing).

## 2026-05-29 (§8 Zendesk vision budget sharing)

- **§8 budget sharing with Jira images:** `_select_zendesk_images_for_vision` applies cross-source dedupe then caps to `max_attachments - len(jira_selected)`. `OpenRouterVisionImageContextExtractor` processes Jira attachments first, then Zendesk-only images via `ZendeskTicketFetcher.fetch_image_bytes` with shared `max_bytes_per_image`; synthetic ids `zendesk:{ticket_id}:{url|attachment_id}`. `build_image_context_extractor` wires `ZendeskTicketFetcher`. Tests: `test_select_zendesk_images_for_vision_respects_shared_attachment_cap`, `test_vision_extractor_processes_zendesk_only_image_in_remaining_budget_slots`, cap/full, oversized soft-fail, fetch failure soft-fail.

## 2026-05-29 (§8 cross-source Zendesk image dedupe)

- **§8 cross-source image dedupe:** `core/zendesk_image_dedupe.py` tiered match against Jira `AttachmentRef`: filename (case-insensitive), MIME+size within 512 bytes, optional content hash. `collect_zendesk_image_refs_from_tickets` / `dedupe_zendesk_images_against_jira` partition kept vs `ZendeskImageDedupeSkip` rows for audit. `image_context_extractor.collect_zendesk_image_dedupe_skips` runs on vision extract; skips land on `ImageContextExtractionResult.zendesk_skipped` and `ImageContextExtractedAuditEvent.zendesk_skipped`. Tests: `test_zendesk_image_dedupe.py`, `test_vision_extractor_records_zendesk_dedupe_skips_against_jira_attachments`, `test_parse_image_context_extracted_includes_zendesk_skipped`. **conftest:** Zendesk env keys added to autouse `_APP_ENV_KEYS` clear list to stop cross-test leakage (fixed flaky `test_fetch_image_bytes_raises_when_credentials_missing`).

## 2026-05-29 (§8 Zendesk attachment byte fetch)

- **§8 fetch Zendesk image bytes:** `ZendeskTicketFetcher.fetch_image_bytes(image_ref, run_id=…)` GETs `ZendeskImageRef.url` with the same Basic auth as ticket/comment fetch, `Accept: */*`, and `follow_redirects=True` for signed/token URLs that redirect to CDN. Raises `ZendeskTicketFetchError` on missing URL, missing credentials, or HTTP ≥400. Tests: `test_fetch_image_bytes_returns_binary_with_basic_auth`, `test_fetch_image_bytes_follows_redirect_to_signed_cdn_url`, `test_fetch_image_bytes_raises_on_http_error`, `test_fetch_image_bytes_raises_when_credentials_missing`.

## 2026-05-29 (§8 Zendesk inline image discovery)

- **§8 discover Zendesk images:** `ZendeskImageRef` model on `ZendeskCommentRef.image_refs` and `LinkedZendeskTicket.description_image_refs`. `extract_zendesk_inline_image_urls` parses markdown/HTML inline URLs; comment `attachments` from `comments.json` parsed for image MIME/extension only. Comments API requests `include_inline_images=true` (single fetch, no double-call). Tests: `test_zendesk_ticket_fetcher.py` (URL parsing, query param, comment attachments + inline body, description images).

## 2026-05-29 (§8 Zendesk resolution signals render + handler wiring)

- **§8 resolution signals in issue block:** `issue_text_block._format_zendesk_tickets` renders `Zendesk resolution signals` (initial impact / latest status / resolution hints / open risks) when `resolution_summary` is set; falls back to subject+description otherwise. Cross-ticket dedupe: repeated `resolution_hints` → `(same as above)`; identical summary fingerprint → omitted duplicate block. Tests: `test_issue_text_block.py`, `test_prompt_composer.py::test_issue_block_renders_zendesk_resolution_signals_when_summary_present`.
- **§8 handler wiring:** `TriageHandler._enrich_with_zendesk` runs `ZendeskCommentSummarizer.summarize` after fetch (soft-fail on exception); `build_default_triage_handler` builds summarizer via `build_zendesk_comment_summarizer` when `TRIAGE_ZENDESK_COMMENT_SUMMARY_ENABLED`. Test: `test_handler_applies_zendesk_resolution_summary_from_summarizer`.
- **§8 priority balance:** `core/policy/priority_definition.md` section on current impact vs historical peak; `priority_template` reinforces recovery/temporary-external guidance. Tests: `test_bundled_priority_definition_weighs_current_impact_over_historical_peak`, `test_priority_template_reinforces_recovery_over_historical_peak`.

## 2026-05-29 (§8 Zendesk comment summarizer adapter)

- **§8 ZendeskCommentSummarizer:** `adapters/zendesk_comment_summarizer.py` — `NoOpZendeskCommentSummarizer` when `TRIAGE_ZENDESK_COMMENT_SUMMARY_ENABLED=false`; `OpenRouterZendeskCommentSummarizer` uses `TRIAGE_ZENDESK_SUMMARY_MODEL` (defaults to text model), `TRIAGE_ZENDESK_SUMMARY_TIMEOUT_SECONDS`, and newest-first comment trim via `TRIAGE_ZENDESK_COMMENTS_CHAR_BUDGET`. Parses `INITIAL_IMPACT` / `LATEST_STATUS` / `RESOLUTION_HINTS` / `OPEN_RISKS` into `ZendeskResolutionSummary` on `LinkedZendeskTicket.resolution_summary`; inference/parse failures soft-fail per ticket (`None` summary). Prompts in `prompt_templates.json` + `core/zendesk_summary_prompt_composer.py` (Jira summary/description/repro as context; Langfuse names `triagebot/zendesk-summary-system|user`). Tests: `test_zendesk_comment_summarizer.py` (budget trim, parse, NoOp, OpenRouter invoke, soft-fail, recovery scenario).

## 2026-05-29 (§8 Zendesk comment fetch)

- **§8 fetch ticket comments:** `ZendeskTicketFetcher._fetch_comments` calls `GET /api/v2/tickets/{id}/comments.json` with the same Basic auth as ticket fetch. Comments land on `LinkedZendeskTicket.comments` as `ZendeskCommentRef` (id, body, public, created_at), sorted newest-first and capped by `TRIAGE_ZENDESK_MAX_COMMENTS_PER_TICKET` (default 20; `0` skips fetch). Public and internal notes are both included. Comment fetch failures soft-fail per ticket (warning log, empty `comments`, ticket still returned). Tests: `test_fetch_ticket_includes_comments_newest_first`, `test_fetch_ticket_comments_capped_by_max_comments_per_ticket`, `test_fetch_ticket_comment_fetch_soft_fails_leaves_empty_comments`, settings defaults/env override.

## 2026-05-29 (§8 id union dedupe)

- **§8 Zendesk id union dedupe:** `ZendeskTicketFetcher.collect_linked_ticket_ids` now unions Jira custom-field ids with body-parsed ids from summary/description/reproduction steps (deduped, custom-field ids first). `TRIAGE_ZENDESK_MAX_TICKETS` cap applies only when no custom-field ids are present. `scripts/fetch_jira_issue.py` always routes through `collect_linked_ticket_ids` instead of `issue.zendesk_ticket_ids or …`. Tests: `test_collect_linked_ticket_ids_unions_custom_fields_with_body_text_ids`, `test_collect_linked_ticket_ids_dedupes_ids_across_custom_fields_and_body`, updated fetch script mocks.

## 2026-05-29 (close)

- **Phase close (`/close-phase`):** From `.venv`, `./scripts/run_tests.sh lint`, `mypy .` (89 files), and `./scripts/run_tests.sh fast` all passed (**416 passed**, **1 skipped** `OPENROUTER_LIVE_SMOKE`, **5 deselected**). **Confluence links in mismatch comments:** `jira_comment_templates.json` adds a `confluence` section; `JiraTriageActionExecutor._mismatch_comment_body` appends a "Helpful resources" ADF paragraph with a linked Confluence page — bug-requirements doc for Story recommendations, priority-definitions doc for Bug priority mismatches. **Comment budget edge cases:** `issue_text_block` renders `Comments:\n(omitted by comment budget)` when budget is zero or every comment exceeds the budget (distinct from `(none)` when there are no comments); contiguous newest-comment suffix selection documented in code. **Comment pagination guard:** `JiraIssueFetcher._fetch_comments` caps at `_MAX_COMMENT_PAGES` (20) and logs `comments_pagination_limit_reached` when Jira omits `total`. **Runtime logging:** `configure_runtime_logging` sets `stream=sys.stdout` explicitly on `basicConfig`. Tests: `test_jira_action_executor.py`, `test_issue_text_block.py`, `test_jira_issue_fetcher.py`, `test_runtime_logging.py`. Docs: `README.md` (mismatch comment Confluence links), `TODO.md` §10 Confluence item marked complete.

## 2026-05-29

- **Phase close (`/close-phase`):** From `.venv`, `./scripts/run_tests.sh lint`, `mypy .` (89 files), and `./scripts/run_tests.sh fast` all passed (**409 passed**, **1 skipped** `OPENROUTER_LIVE_SMOKE`, **5 deselected**). **Comments + attachment-source expansion:** `JiraIssueFetcher` paginates `GET /issue/{key}/comment` into `CommentRef` entries on `FetchedIssue.comments`; `AttachmentRef.referenced_in_comments` marks comment-linked attachments. `issue_text_block.format_issue_text_block` appends a `Comments:` section trimmed by `TRIAGE_COMMENTS_CHAR_BUDGET` (default `6000`, oldest dropped first). Vision selection prioritizes description-inline images, then comment-referenced images up to `TRIAGE_IMAGE_CONTEXT_MAX_ATTACHMENTS`. **Runtime logging:** `runtime_logging.py` configures stdout logging from `LOG_LEVEL` at app lifespan; `HttpAccessLogMiddleware` logs inbound requests; `jira_http_retry.request_with_retries` logs outbound Jira/OpenRouter HTTP with latency and status. Dockerfile disables uvicorn access log (`--no-access-log`) in favor of structured middleware. **CLI:** `--read-only` replaces `--no-comment` on `triage_manual_cli.py` (dry run: no labels/comments/field updates). Tests: `test_jira_issue_fetcher.py`, `test_issue_text_block.py`, `test_image_context_extractor.py`, `test_image_context_wiring.py`, `test_runtime_logging.py`, `test_triage_manual_cli.py`. Docs: `README.md`, `TODO.md`, `.env.example`.

## 2026-05-22

- **Phase close (`/close-phase`):** From `.venv`, `pytest -m lint`, `mypy .` (89 files), and `pytest -m "unit or integration"` all passed (**396 passed**, **1 skipped** `OPENROUTER_LIVE_SMOKE`, **5 deselected**). **§8 Zendesk context baseline:** `ZendeskTicketFetcher` in `adapters/zendesk_ticket_fetcher.py` fetches ticket summaries via Zendesk REST (`GET /api/v2/tickets/{id}.json`) with Basic auth (`ZENDESK_BASE_URL`, `ZENDESK_API_TOKEN`, `ZENDESK_USER_EMAIL` or `ZENDESK_AGENT_EMAIL`). `JiraIssueFetcher` parses and dedupes Zendesk ids from custom fields (`TRIAGE_JIRA_ZENDESK_TICKET_IDS_FIELD_ID` / `TRIAGE_JIRA_IMPORTED_ZENDESK_TICKET_IDS_FIELD_ID`, defaults `customfield_10158` / `customfield_10162`) and from issue body text when fields are empty (`collect_linked_ticket_ids`, `_merge_zendesk_ticket_ids`). `issue_text_block.format_issue_text_block` appends linked-ticket sections. `TriageHandler.run_sync` wires enrichment behind `TRIAGE_ZENDESK_CONTEXT_ENABLED` (soft-fail; triage continues). Settings: `TRIAGE_ZENDESK_MAX_TICKETS`, `TRIAGE_ZENDESK_HTTP_TIMEOUT_SECONDS`, optional count field id. Smoke: `scripts/fetch_jira_issue.py` prints `zendesk_ticket_ids` and optional `zendesk_tickets`. Tests: `test_zendesk_ticket_fetcher.py`, `test_fetch_jira_issue_script.py`, extended jira fetcher/handler/settings/prompt_composer tests. **Follow-on (TODO §8):** id-union dedupe across custom fields + body text, cross-ticket and Jira↔Zendesk text dedupe, Zendesk images + cross-source image dedupe, Langfuse/audit telemetry for Zendesk fetch.

## 2026-05-21

- **Phase close (`/close-phase`):** From `.venv`, `pytest -m lint`, `mypy .` (86 files), and `pytest -m "unit or integration"` all passed (**380 passed**, **1 skipped** `OPENROUTER_LIVE_SMOKE`, **5 deselected**). **Bulk JQL triage CLI:** `triage_bulk_cli.py` + `scripts/run_bulk_triage_cli.py` search issues via `jira_jql_search.search_issues_by_jql` (Cloud `GET /rest/api/3/search/jql`, `nextPageToken` pagination, `--max-results` / `--jql-page-size`), run the same `run_cli_triage` pipeline per key, and write a JSON report (`current_issue_type`, `current_priority`, merged recommendation, per-step `classification` / `priority`, `image_context`). Default **read-only** (`build_default_triage_handler(apply_to_jira=False)`); `--apply` enables labels; `--comment` with `--apply` posts mismatch comments. **Manual CLI / handler:** `TriageSyncResult` exposes `classification` and `priority` step outputs; `build_triage_cli_result_payload` shapes stdout JSON; `JiraTriageActionExecutor(post_mismatch_comments=…)` and `build_default_triage_handler(post_mismatch_comments=…, apply_to_jira=…)` support `--no-comment` on single-issue CLI. Tests: `test_jira_jql_search.py`, `test_triage_bulk_cli.py`, extended manual/handler/executor tests. Lint gate extended for new modules.

## 2026-05-20

- **Phase close (`/close-phase`):** From `.venv`, `pytest -m lint`, `mypy .` (81 files), and `pytest -m "unit or integration"` all passed (**368 passed**, **1 skipped** `OPENROUTER_LIVE_SMOKE`, **5 deselected**). **§7 image context extraction** complete in `TODO.md`. Follow-up in this close: Langfuse **Sessions** (`session_id=run_id` via `propagate_attributes` on the full triage pipeline), vision prompts wired through `settings` on `OpenRouterVisionImageContextExtractor`, `issue_text_block` shared formatting for classification/priority/vision user prompts, and gate fixes (duplicate import, test `settings` fixtures, vision prompt composer import order).

- **Langfuse Sessions:** Triage runs now set Langfuse `session_id` via `propagate_attributes(session_id=run_id)` (`LangfuseInferenceTracer.triage_run_session`), wrapping the full `run_sync` / `run_sync_on_fetched` pipeline so inference spans, vision generations, and audit events group under the Sessions UI (not only the `triage_issue_pipeline` span). `langfuse_session_id()` strips and truncates to Langfuse’s 199-char limit. Tests: `test_langfuse_inference_tracing.py` (`test_tracer_triage_run_session_*`, `test_langfuse_session_id_*`).

## 2026-05-19

- **Image context CLI parity (§7):** `scripts/run_triage_cli.py` / `triage_manual_cli.main()` runs triage with `TRIAGE_IMAGE_CONTEXT_ENABLED` wiring unchanged, and prints JSON including `image_context` from `build_cli_image_context_summary()` (compact per-attachment rows; no transcripts). `TriageSyncResult` from `run_sync` / `run_sync_on_fetched` carries `image_extraction` per call (no shared handler state). Tests: `test_triage_manual_cli.py`, `test_image_context_wiring.py`. README documents flags, cost/PII, example `issue_block` excerpt, and CLI output shape.

- **Image context benchmark (§7):** `run_classification_benchmark.py` accepts `--image-context` / `--no-image-context` (else `TRIAGE_IMAGE_CONTEXT_ENABLED`). When enabled, vision runs once per prefetched issue and JSONL rows include `has_images`, `image_context`, and attachment counts; `summary.json` and `benchmark_summary.summarize_model_run` stratify accuracy into `has_images` vs `text_only`. `issue_has_inline_images` in `image_context_extractor.py`; `run_sync_on_fetched` accepts pre-extracted `image_contexts` for replay. Tests: `test_classification_benchmark.py`, `test_benchmark_summary.py`, `test_image_context_wiring.py`.
- **Image context observability (§7):** `ImageContextExtractionResult` / `ImageAttachmentMetric` returned from extractors; `image_context_extracted` audit event with per-attachment latency and failure breakdown; Langfuse `image_context_extraction` span nested under `triage_issue_pipeline` (wraps extract work; metadata updated on finish with counts, bytes, vision cost); per-attachment `inference_vision` generations via `LangfuseInferenceTracer.vision_generation` (multimodal input redaction in `sanitize_vision_messages`). `build_image_context_extractor(..., inference_tracer=...)` wired from `build_default_triage_handler`. `triage_completed` / `triage_failed` telemetry includes `image_context_attachments_considered` and `image_context_attachments_extracted`. Tests: `test_image_context_observability.py`, `test_langfuse_inference_tracing.py`.
- **Image context scope tightened:** Vision preprocessing now processes **description-inline images only** (`AttachmentRef.inline == true` from ADF `media` IDs). No issue-level fallback attachments are sent to vision by default. `image_context_extractor._select_image_attachments` filters to inline images and applies size-desc ordering within that subset. Unit tests updated to require inline refs for vision-path coverage and to assert non-inline images are skipped.
- **Inline-ID mismatch fix (real Jira):** Some issues expose description media with ADF `media.attrs.id` values that do not match issue attachment ids. `JiraIssueFetcher` now requests `expand=renderedFields` and marks attachments as inline when `renderedFields.description` contains attachment URLs (`/secure/attachment/{id}/...` or `attachment/content/{id}`), in addition to ADF media-id matching. This restores description-only extraction for real tickets where ADF IDs differ from attachment IDs.

- **Image context Jira fetch fix:** `JiraIssueFetcher.fetch_attachment_bytes` passes `redirect=false` so Jira returns 200 + binary via the gateway instead of 303 to media CDN (following redirects often yielded HTML → OpenRouter “Invalid image data-url”). Vision path sniffs PNG/JPEG/GIF/WebP/BMP magic bytes and rejects HTML/JSON payloads before calling the model.

- **Image context (§7 wiring + settings):** `AppSettings` exposes `TRIAGE_IMAGE_CONTEXT_ENABLED` (default off), `TRIAGE_VISION_MODEL` (default `google/gemini-2.0-flash-001`), max attachments/bytes, vision HTTP timeout, `TRIAGE_AUDIT_REDACT_IMAGE_TRANSCRIPT`. `build_image_context_extractor(settings, fetcher)` returns `NoOp` or `OpenRouterVisionImageContextExtractor` with a dedicated `OpenRouterInferenceClient` (`model_override` + `http_timeout_seconds`). `TriageHandler.run_sync` runs extraction after Jira fetch and passes `image_contexts` into classification/priority prompts; `run_sync_on_fetched` skips extraction. Tests: `test_image_context_wiring.py` (settings, factory, handler wiring, end-to-end preprocess with mocked Jira binary + vision model). `.env.example` documents flags.

- **Image context (§7 vision prompts):** Vision preprocessor prompts are Langfuse-first with JSON fallback — `vision_prompt_composer.compose_vision_system_prompt()` / `compose_vision_user_instruction(issue)` fetch `triagebot/vision-system` and `triagebot/vision-user` (overridable via `TRIAGE_LANGFUSE_VISION_*_PROMPT_NAME`) when `TRIAGE_LANGFUSE_PROMPTS_ENABLED` and Langfuse keys are set; otherwise `prompt_templates.json` keys `vision_system_prompt` / `vision_user_instruction`. The vision user prompt includes the same ticket text as classification (`issue_block` via `issue_text_block.format_issue_text_block`) so screenshots are described in issue context, not in isolation. Langfuse `vision-user` should declare an `issue_block` variable like `classification-user`. Tests: `test_vision_prompt_composer.py`, `test_image_context_extractor.py` (`test_vision_extractor_user_message_includes_issue_context`). `.env.example` documents vision Langfuse names.

- **Image context (§7 vision extractor):** `OpenRouterVisionImageContextExtractor` in `image_context_extractor.py` — selects image attachments (inline first, then largest), fetches bytes via `JiraIssueFetcher.fetch_attachment_bytes`, calls `OpenRouterInferenceClient.chat_completion_with_details` with multimodal `image_url` data URLs and a dedicated vision model (constructor-injected `OpenRouterInferenceClient` with `model_override`). Transcription-first prompt (`TRANSCRIPT:` / `SUMMARY:` sections); per-image failures (fetch, oversize, unsupported MIME, vision HTTP, parse) return `ImageContext.extraction_failure` and never abort triage. `OpenRouterInferenceClient` message type widened to `list[dict[str, Any]]` for vision payloads. Tests: extended `test_image_context_extractor.py` (success, multimodal payload, selection order, skip non-images, degrade paths). Gates: `pytest -m lint`, `mypy .`, `pytest -m unit`.

- **Image context (§7 foundation):** RED→GREEN for ADF attachment discovery and prompt enrichment scaffolding. `collect_media_attachment_ids_from_adf` walks `media` nodes inside `mediaSingle` trees; `FetchedIssue.attachments` (`AttachmentRef`) parsed from Jira `attachment` field with `inline` when id appears in description ADF. `JiraIssueFetcher._BASE_FIELDS` includes `attachment`. `JiraIssueFetcher.fetch_attachment_bytes(attachment_id)` GETs `…/rest/api/3/attachment/content/{id}` via the same Atlassian gateway (`_gateway_prefix`), `_basic_auth_header` (`_auth_headers`), and `request_with_retries` (`_get_with_retries`, shared with issue fetch). `image_context_extractor.py`: `ImageContext`, `ImageContextExtractor` protocol, `NoOpImageContextExtractor`. `prompt_composer._issue_block` / compose helpers accept optional `image_contexts` and render `Attached images:` with **summary only** (verbatim transcript kept on `ImageContext` but omitted from classification/priority `issue_block` to avoid prompt bloat) or `[Attachment N: extraction unavailable — {reason}]`. Tests: `test_jira_adf_media.py`, `test_image_context_extractor.py`, extended `test_prompt_composer.py` / `test_jira_issue_fetcher.py` (attachment binary fetch + retry/error paths). Gates: `pytest -m lint`, `mypy .`, `pytest -m unit`.

## 2026-05-18

- **Langfuse prompt management:** `langfuse_prompt_config.fetch_langfuse_text_prompt` + default names under `triagebot/`. Langfuse user prompts (`classification-user`, `priority-user`) embed policies/reason guidance; runtime compile passes only `issue_block`. System prompts and local fallback use `prompt_templates.json` + `load_policy_context` (`policy/*.md`, optional Langfuse `reason-for-humans`). Env: `TRIAGE_LANGFUSE_PROMPTS_ENABLED`, `TRIAGE_LANGFUSE_PROMPT_LABEL`, `TRIAGE_LANGFUSE_PROMPT_CACHE_TTL_SECONDS`, per-prompt `TRIAGE_LANGFUSE_*_PROMPT_NAME`.

- **Phase close (`/close-phase`):** From `.venv`, `pytest -m lint`, `mypy .` (71 files), and `pytest -m "unit or integration"` all passed (**285 passed**, **1 skipped**, **5 deselected** in the unit/integration slice). **Jira fetch payload:** `FetchedIssue` now includes optional `reproduction_steps`; `JiraIssueFetcher` requests `TRIAGE_JIRA_REPRODUCTION_STEPS_FIELD_ID` (default `customfield_10251`) and prefers that field when present, otherwise it extracts a "Steps to reproduce"/"Reproduction steps" section from `description`. **Prompt context:** `prompt_composer` now includes a dedicated `Reproduction steps` block (or `(none)`). **Jira write behavior tightened:** `JiraTriageActionExecutor` now treats Bug prioritization (`P2 -> P1`) as audit-only (no mismatch comment and no `triagebot-priority-mismatch` label), while Bug de-escalation (`P1 -> P2`) and Story reclassification still trigger Jira comment flow; `triage_completed` audit telemetry includes `priority_signal` and `would_post_jira_comment` to reflect this.

- **Phase close (`/close-phase`):** From `.venv`, `pytest -m lint`, `mypy .` (71 files), and `pytest -m "unit or integration"` — **283 passed**, **1 skipped** (`OPENROUTER_LIVE_SMOKE`), **5 deselected**. **Webhook auth:** `TRIAGE_WEBHOOK_TOKEN` is required in `AppSettings`; `POST /triage` rejects requests without a matching `X-Triage-Token` header (`401`, constant-time compare via `compare_digest`). `GET /health` is unchanged (no token). **Source enum:** `manual_cli` renamed to `manual_trigger` across API, audit events, CLI (`triage_manual_cli`, `scripts/run_triage_cli.py`), benchmark runner default, and docs (`README.md`, `specification.md`, `TODO.md`). **Container smoke:** `scripts/run_container_smoke.sh` sets `TRIAGE_WEBHOOK_TOKEN=local-smoke` and sends the header; `scripts/run_container_tunnel.sh` reads the token from `--env-file` for live curls. **Cleanup:** removed unused `classification_bug_to_final` from `triage_recommendation_parser.py`. **Jira comment:** mismatch template prefixes the configured TriageBot display name. `.env.example` documents `TRIAGE_WEBHOOK_TOKEN`.

## 2026-05-14

- **Phase close (`/close-phase`) verification refresh:** From `.venv`, `pytest -m lint`, `mypy .`, and `pytest -m "unit or integration"` all passed after the container-smoke + observability updates (**280 passed**, **1 skipped**, **5 deselected** in the unit/integration slice). Current docs/TODO now reflect: local container smoke + live tunnel smoke are complete (including TJC-only/operator guardrails), while platform-repo image registration/deployment wiring remains open in Phase 7.

- **Live container smoke guardrails (TJC-only):** `scripts/run_container_tunnel.sh` now parses the payload before startup, refuses runs unless `project == "TJC"` and `issue_key` starts with `TJC-`, prints explicit pre-checks, and requires `LIVE_SMOKE_CONFIRM=YES` before any live Jira/OpenRouter execution. After posting `/triage`, it still verifies `run_id` log correlation and now prints a post-run Jira checklist for `triagebot-reviewed`, mismatch labels (`triagebot-likely-story`, `triagebot-priority-mismatch`), and expected comment behavior. Coverage added in `tests/integration/test_container_smoke_setup.py` (`test_live_container_tunnel_script_enforces_tjc_only_smoke_scope`, `test_live_container_tunnel_script_includes_explicit_operator_guardrails`). README local container tunnel section updated to document the confirmation env var and guardrail flow. Verification from `.venv`: `pytest tests/integration/test_container_smoke_setup.py -m integration`, `pytest -m lint`, `mypy .` (all green).

- **Live container smoke `run_id` correlation hardened:** `scripts/run_container_tunnel.sh` now extracts `run_id` from the live `POST /triage` response, reads `docker logs` from the running container, and fails fast if the same `run_id` is absent from logs; on success it prints recent matching log lines before opening the tunnel. Coverage added in `tests/integration/test_container_smoke_setup.py::test_live_container_tunnel_script_checks_run_id_in_container_logs`. Verification from `.venv`: `pytest tests/integration/test_container_smoke_setup.py -m integration`, `pytest -m lint`, `mypy .`, and `pytest -m "unit or integration"` (all green).

- **Phase close (`/close-phase`):** From `.venv`, `pytest -m lint`, `mypy .` (70 files, clean), `pytest -m "unit or integration"` — **265 passed**, **1 skipped** (`OPENROUTER_LIVE_SMOKE`). **OpenRouter → Langfuse usage/cost:** `OpenRouterInferenceClient.chat_completion_with_details` returns `OpenRouterCompletionResult` with optional `usage_details` (prompt/completion/total token counts from the `usage` object) and `cost_details` (`total`, `input`, `output` mapped from `total_cost` / `prompt_cost` / `completion_cost` or legacy `cost` / top-level fields). `TriageHandler` passes those into each inference generation’s `finish(...)`; `langfuse_inference_tracing._apply_langfuse_generation_update` forwards them to Langfuse `generation.update` as `usage_details` / `cost_details` when set. **Generation `trace_context`:** `_safe_current_trace_context` reads `Langfuse.get_current_trace_id` and `get_current_observation_id` when available and passes that `trace_context` into `start_as_current_observation` for `inference_*` generations so the SDK nests them under `triage_issue_pipeline` without breaking the root span model.

- **Phase close (`/close-phase`):** From `.venv`, `./scripts/run_tests.sh lint` (5 tests, flake8 gate), `./scripts/run_tests.sh types` (`mypy .`, 70 files, clean), `./scripts/run_tests.sh fast` (`263 passed`, `1 skipped`: `OPENROUTER_LIVE_SMOKE` integration smoke unless enabled). README corrected: `scripts/run_tests.sh` requires a subcommand (`all`, `fast`, `lint`, etc.); `./scripts/run_tests.sh` alone exits with “Unknown command”.

- **`GET /health`:** `create_app` registers liveness/readiness at `/health`. Handler calls `load_settings()` (same dotenv + env validation as the rest of the service). Success → HTTP 200 and `{"service":"jira-triage","ready":true}`; any validation failure → HTTP 503 and `{"service":"jira-triage","ready":false}`. Does not instantiate the triage runner. `response_model=None` because FastAPI cannot union `HealthResponse` with `JSONResponse`. Unit tests: `tests/unit/test_health_endpoint.py` (isolated `tmp_path` cwd so `find_dotenv` does not pull repo `.env` into `os.environ` and break ordering-sensitive tests).

- **Resilience audit + logs:** `TransportRetriesExhausted` in `jira_http_retry` carries accurate `attempts` after retriable transport failures. `JiraIssueFetchError` gains `transport_error_kind`; `OpenRouterInferenceError` gains `attempts`, `http_status`, transport fields, and `failure_category`. `TriageHandler` enriches `triage_failed` audit `telemetry` with `failure_category` (e.g. `http_transient`, `http_rate_limited`, `timeout`, `connect_error`, `configuration`) and emits a structured `triage_resilience_notice` log line (same fields plus `triage_failure_category`) when telemetry is present. Tests: `test_jira_http_retry.py` (includes `classify_transport_request_error`, HTTP retry/no-retry on `request_with_retries`), `test_jira_issue_fetcher.py` (incl. read-timeout exhaustion), `test_openrouter_inference_client.py` (incl. HTTP 500 `http_error`, read-timeout `failure_category`), `test_triage_fallback.py` (inference/Jira transport variants → `TriageFailure` categories), `test_triage_handler.py`.

- **OpenRouter REST timeout and bounded retries:** `AppSettings` adds `TRIAGE_OPENROUTER_HTTP_TIMEOUT_SECONDS` (default 60) and `TRIAGE_OPENROUTER_HTTP_MAX_RETRIES` (default 2). `OpenRouterInferenceClient` uses `jira_http_retry.request_with_retries` for POST chat completions; exhausted transport errors raise `OpenRouterInferenceError` with an `after retries` message. Unit tests in `tests/unit/test_openrouter_inference_client.py`; settings in `tests/unit/test_settings.py`.

- **Phase close (Langfuse trace tree):** `pytest -m lint`, `mypy .`, and `pytest -m "unit or integration"` (229 passed, 1 skipped) from `.venv`. **Langfuse Python SDK ~4.6:** the pipeline span `triage_issue_pipeline` is started **without** `trace_context` so OpenTelemetry nesting applies; `inference_classification` / `inference_priority` are children of that span. Passing `trace_context` into `start_as_current_observation` / `create_event` was forcing remote-parent handling and flattening the UI root view. **Audit events:** `LangfuseAuditStore` calls `create_event` **without** `trace_context` when `get_current_trace_id()` is non-empty (handler still inside the pipeline span); otherwise it falls back to `trace_context={"trace_id": stable_langfuse_trace_id(run_id)}` for correlation. `create_event(..., trace_id=...)` is invalid on this SDK; use `trace_context` only where needed.

- **Phase close (verification):** from `.venv`, `./scripts/run_tests.sh lint` (flake8 via `pytest -m lint`, 5 tests), `./scripts/run_tests.sh types` (`mypy .`, 67 files, clean), and `./scripts/run_tests.sh fast` (`pytest -m "unit or integration"`, 228 passed, 1 skipped: `OPENROUTER_LIVE_SMOKE` integration smoke). README repository-layout line aligned with gateway auth: executor wiring when `JIRA_CLOUD_ID` and `JIRA_USER_EMAIL` are set.

- **Jira REST timeout and bounded retries:** `AppSettings` adds `TRIAGE_JIRA_HTTP_TIMEOUT_SECONDS`
  (default 30) and `TRIAGE_JIRA_HTTP_MAX_RETRIES` (default 2 extra attempts). Shared helper
  `src/triage_service/adapters/jira_http_retry.py` retries on HTTP 429/502/503/504 and
  `ConnectError` / `RemoteProtocolError` / `TimeoutException`. Wired through
  `JiraIssueFetcher` and `JiraTriageActionExecutor` (GET issue, PUT labels, POST comment).
  Unit tests: `tests/unit/test_jira_http_retry.py`, retry cases in
  `tests/unit/test_jira_issue_fetcher.py` and `tests/unit/test_jira_action_executor.py`.

- **Observability settings surface expanded:** `AppSettings` now exposes audit config
  flags in `src/triage_service/core/settings.py` with env aliases
  `TRIAGE_AUDIT_STRUCTURED_LOG_ENABLED`, `TRIAGE_AUDIT_LANGFUSE_ENABLED`,
  `TRIAGE_AUDIT_REDACT_MODEL_INPUT`, and `TRIAGE_AUDIT_REDACT_MODEL_OUTPUT`
  (defaults: structured log and Langfuse audit mirror on, input redaction off, output
  redaction off). Added unit coverage in `tests/unit/test_settings.py` for
  defaults + explicit env overrides. Verification gates run from `.venv`:
  `pytest tests/unit/test_settings.py`, `pytest -m lint`, `pytest -m unit`,
  and `mypy .` (all green).

- **Audit store fan-out contract:** added `src/triage_service/observability/audit_store.py`
  with `AuditStore` protocol (`record(event)`) and `CompositeAuditStore` fan-out to all
  child stores. Exported via `triage_service.observability.__init__`; added unit coverage
  in `tests/unit/test_audit_store.py` and included the new module in lint/mypy target lists.

- **Stage latency capture in handler:** `TriageHandler` now logs structured `triage_stage_timing`
  events (with `run_id`, `issue_key`, `project`, `source`, and `latency_ms`) for
  `jira_fetch`, `classification_inference`, `priority_inference` (Bug path only), and
  `jira_action`. Timing is measured with `time.perf_counter()` and emitted in `finally`
  blocks so failures still produce latency telemetry. Unit coverage:
  `tests/unit/test_triage_handler.py::test_handler_emits_stage_timing_for_fetch_model_and_executor`.

- **Canonical audit events:** `src/triage_service/observability/audit_events.py` defines Pydantic
  models and `parse_triage_audit_event` / `dump_triage_audit_event` for lifecycle types
  `classification_completed`, `priority_completed`, `triage_completed`, and `triage_failed`
  (failure `category` literals aligned with `TriageFailureCategory` via unit test).
  `TriageHandler` records these via `AuditStore` (`observability_wiring.build_observability(...)`)
  with optional Langfuse + structured-log sinks and payload redaction per settings.

- **LangFuse inference tracing:** `src/triage_service/observability/langfuse_inference_tracing.py`
  provides `LangfuseInferenceTracer` (root span `triage_issue_pipeline` + nested
  `inference_classification` / `inference_priority` generations). `TriageHandler` wires it via
  `build_langfuse_inference_tracer(...)` when `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`
  are set; each generation records OpenRouter `messages`, model id, temperature, raw assistant
  JSON as `output`, and parsed step fields under `metadata.parsed`. Span metadata includes
  `run_id`, `issue_key`, and `project` on the root span. SDK failures are logged and never fail
  triage. `TriageHandler.flush_inference_telemetry` / `POST /triage` and `run_cli_triage` call
  LangFuse `flush()` after each run so short-lived processes still export spans.

- **`run_id` correlation:** `POST /triage` generates `str(uuid.uuid4())` at ingress (`triage_api`),
  returns it on `TriagePostResponse`, and passes `run_id=` through `TriageRunner.run_sync` →
  `TriageHandler` → `JiraIssueFetcher.fetch`, `OpenRouterInferenceClient.chat_completion`, and
  `TriageActionExecutor.apply_triage_outcome`. CLI (`triage_manual_cli.run_cli_triage`)
  generates its own `run_id` per invocation.
  Benchmark prefetch/triage paths pass `run_id` into fetch / `run_sync_on_fetched`.

- **LangFuse audit store sink:** `src/triage_service/observability/langfuse_audit_store.py`
  implements `AuditStore` by serializing `TriageAuditEvent` via `dump_triage_audit_event` and
  calling LangFuse `create_event`. When an observation context is active, events attach as
  children of the current span (no `trace_context`). Otherwise `create_event` uses
  `trace_context={"trace_id": stable_langfuse_trace_id(run_id)}` so a run can still be
  correlated by deterministic id. `build_langfuse_audit_store(public_key, secret_key, base_url)`
  is a no-op without both keys. Covered in `tests/unit/test_langfuse_audit_store.py`.

- **Structured logger audit sink:** added
  `src/triage_service/observability/structured_logger_audit_store.py` with
  `StructuredLoggerAuditStore` implementing `AuditStore` by serializing each
  `TriageAuditEvent` via `dump_triage_audit_event` and emitting one JSON log line
  (stable key order) for CloudWatch-compatible querying. Emission errors are swallowed
  with a warning so audit logging cannot break the triage pipeline. Exported via
  `triage_service.observability.__init__`; covered in
  `tests/unit/test_structured_logger_audit_store.py`.

- **Confidence remains advisory for Jira actions:** `JiraTriageActionExecutor` now
  routes mismatch-comment decisions through
  `_should_post_mismatch_comment(flags: TriageMismatchFlags)` in
  `src/triage_service/adapters/jira_action_executor.py`, making the decision
  depend only on deterministic mismatch flags (not model confidence). Added unit
  coverage in `tests/unit/test_jira_action_executor.py`:
  `test_should_post_mismatch_comment_depends_only_on_mismatch_flags` (RED on
  missing helper, then GREEN). Verification gates from `.venv`: targeted unit
  slice, `pytest -m lint`, `mypy .`, and `pytest -m unit` all green.

- **Observability test-task verified and closed:** TODO §5 item "Add unit tests
  for event schema validation, audit fan-out behavior, and failure-safe
  logging/LangFuse emission paths" is now marked complete after verifying
  existing coverage and re-running gates from `.venv`: targeted observability
  unit files (`tests/unit/test_audit_events.py`, `test_audit_store.py`,
  `test_langfuse_audit_store.py`, `test_structured_logger_audit_store.py`),
  `pytest -m lint`, and `mypy .` (all green).

## 2026-05-13

- **Phase close (verification + docs reconciliation):** ran gates from `.venv`:
  `pytest -m lint`, `mypy .`, and `pytest -m "unit or integration"` (all green:
  5 lint tests passed; mypy clean on 51 files; unit+integration 161 passed, 1 skipped).
  Updated `README.md` with a Jira Automation scheduled-rule recipe (cadence + JQL +
  request body), explicit `triagebot-reviewed` lifecycle, and MVP limitations. Reconciled
  `TODO.md` §9 by marking architecture docs, local runbook, Jira automation setup,
  `triagebot-reviewed` lifecycle, and MVP limitations as complete.

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

- **`POST /triage` `source` enum:** `TriageSource = Literal["bug_created", "priority_changed", "manual_trigger"]`
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
  (`JiraTriageActionExecutor`): `triagebot-reviewed` on every successful triage; mismatch labels
  (`triagebot-likely-story` / `triagebot-priority-mismatch`) and a terse **TriageBot** templated
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
  `TriageRunner.run_sync(..., "manual_trigger")`. `main()` loads `.env` from repo root, validates settings,
  prints JSON (`completed` + `recommendation` or `failed` + `failure`), exit codes `0` / `1` / `2`.
  Wrapper: `scripts/run_triage_cli.py`. API: `TriageSource` now includes `"manual_trigger"`.
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
  rewrites `jira_automation_trigger` and `jira_action_executor` (`triagebot-reviewed` now applied on every
  successful triage so it can act as the dedupe marker the JQL relies on). `TODO.md` realigned
  (§2 single synchronous handler, §3 label rules, §5 backstop-window metric, §6 Jira automation
  runbook). `README.md` + `memory.md` updated. Next backlog focus: synchronous triage handler that
  composes fetcher → prompt composer → inference client → recommendation parser / fallback →
  action executor (`TODO.md` §2).
- **Integration model (locked):** Jira Cloud Automation **scheduled rule** (per-issue, no batching for
  MVP) is the only production trigger. Rule cadence ~5 min. Reference JQL:
  `project = <KEY> AND issuetype = Bug AND labels not in (triagebot-reviewed) AND created >= -30m AND created <= -5m`.
  `created <= -5m` = stabilization delay; `labels not in (triagebot-reviewed)` = dedupe (service applies that
  label on every successful triage); `created >= -30m` = backstop. Service is stateless — no in-process
  scheduler/queue. Failures (`TriageFailure`) leave the issue unlabeled → next scheduled scan retries
  automatically until success or ageout.
- **API contract (current):** `POST /triage` body is `{ issue_key, project, source }` where
  `source: Literal["scheduled_scan", "manual_trigger"]`. The old `event_type` field (`issue_created` /
  `issue_updated`) is gone — nothing fires the webhook on a Jira event under the scheduled-rule model.
  `source` is a closed enum; `manual_cli` tags calls from `scripts/run_triage_cli.py` / local tooling. **Thin payload:** Jira sends only `issue_key + project + source`; service re-fetches latest
  issue state via `JiraIssueFetcher` (we already pay the Jira-auth cost for comment/label writes).
  Reference Jira Automation Send-web-request → Custom data body:
  `{ "issue_key": "{{issue.key}}", "project": "{{issue.project.key}}", "source": "scheduled_scan" }`.
  Tests: `tests/unit/test_post_triage.py`.
- **Label semantics (locked):** `triagebot-reviewed` is applied on **every successful triage**, mismatch or
  not — it is the dedupe marker the scheduled JQL depends on. Mismatch-specific labels keep their
  original meaning: `triagebot-likely-story` only when type mismatches; `triagebot-priority-mismatch` only when
  priority mismatches on the Bug path. Internal comment is posted only on mismatch. Operators force
  re-triage by removing `triagebot-reviewed` on the Jira issue.
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
- **Phase close (commit):** OpenRouter inference client + `TRIAGE_TEXT_MODEL` settings; optional
  `OPENROUTER_LIVE_SMOKE` integration ping; `max_tokens` on chat completions; removed Playwright/E2E
  scaffold (`run_e2e_tests.sh`, `pytest` `e2e` marker, `tests/e2e/`); `run_tests.sh full` delegates to
  `all`; docs/spec/user flows and Cursor prompts aligned with Lambda-shaped service.
- **OpenRouter client:** `openrouter_inference_client.py` — `OpenRouterInferenceClient(settings, client=...)`.
  `chat_completion(messages, *, run_id, temperature=..., max_tokens=...)` POSTs to
  `https://openrouter.ai/api/v1/chat/completions` with `model=settings.triage_text_model`
  (`TRIAGE_TEXT_MODEL`, default `openai/gpt-4o-mini`) and Bearer `OPENROUTER_API_KEY`. Optional
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

## 2026-05-14

- **Log payload guard:** `observability/log_payload_guard.py` deep-truncates long strings in JSON-like trees (default
  `DEFAULT_MAX_LOG_STRING_CHARS` = 8192). Root dicts gain `log_payload_truncated: true` when any string was clipped.
  Used by `StructuredLoggerAuditStore`, `LangfuseAuditStore`, Langfuse generation `input` / `update` metadata and
  `output`, and by `preview_bytes_for_log` / `triage_api.preview_request_body_for_log` for consistent byte-preview
  markers. Tests: `tests/unit/test_log_payload_guard.py`, extended audit and Langfuse tracing unit tests.
- **Local container setup baseline:** added repo-root `Dockerfile` (Python 3.12 slim, installs `.[dev]`, serves
  `triage_service.api.triage_api:app` via uvicorn) and `scripts/run_container_smoke.sh` that builds/runs the image,
  waits for `/health`, posts `tests/fixtures/triage_smoke_payload.json` to `POST /triage`, and validates response shape.
- **Mock triage mode for container smoke:** `triage_handler.build_default_triage_handler()` now returns
  `LocalMockTriageRunner` when `TRIAGE_LOCAL_MOCK_MODE` is truthy. This path avoids Jira/OpenRouter I/O and returns a
  deterministic Story recommendation so local smoke can verify API/container wiring without unintended Jira writes.
  Tests: `tests/unit/test_triage_handler.py::test_build_default_triage_handler_local_mock_mode_skips_external_calls`,
  `tests/integration/test_container_smoke_setup.py`.
- **Phase 7 scope refinement (deployment):** local mock smoke is now treated as complete baseline. Next explicit tasks are
  (1) live container smoke with mounted real secrets and actual Jira/OpenRouter calls, (2) a safety guard to prove live
  end-to-end calls without unintended Jira writes, and (3) platform repo image build/deploy wiring under
  `build/docker/<jira-triage-image>` + `build/images.yaml` with Deployment Secret/ConfigMap mapping.
- **Container tunnel workflow for Jira Automation:** `scripts/run_container_tunnel.sh` builds and runs the local image
  with `--env-file` secrets, posts one live `POST /triage` payload (prints full JSON + `run_id`), then starts a tunnel
  (default `cloudflared`, optional `ngrok`) pointing Jira Automation at the container URL. This keeps executor active for
  true end-to-end validation; runbook now emphasizes dedicated test issues/projects and post-run Jira verification.
- **Langfuse visibility log at startup wiring:** `build_triage_observability()` now emits a safe
  `triage_observability_config` info log that reports booleans only (keys present flags, base-url configured flag,
  langfuse inference enabled, langfuse audit sink enabled, structured-log flag) without printing secret values. Tests:
  `tests/unit/test_observability_wiring.py` includes missing-keys vs keys-present assertions on emitted log extras.
- **GET /health observability:** `triage_api` exposes booleans under `observability` when `ready` is true
  (via `observability_status_summary()` in `observability_wiring.py`), including **`langfuse_export_env_ready`**
  (Langfuse keys plus SDK export env: `LANGFUSE_TRACING_ENABLED` not `false`, `OTEL_SDK_DISABLED` not `true`).
  Container scripts print this block after the health wait. `build_triage_observability()` logs
  `langfuse_runtime_tracing_enabled` from the Langfuse client when constructed. Inference “enabled” still does not prove
  Langfuse API reachability.
