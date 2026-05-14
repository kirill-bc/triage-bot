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
- [x] Implement `POST /triage` contract accepting `issue_key`, `project`, and `source` (closed `Literal`: `bug_created`, `priority_changed`, `manual_cli`).
- [x] Add request validation for required fields and supported `source` values.
- [x] Implement Jira issue fetcher by issue key (summary, description, type, priority, reporter).
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
- [x] Implement local runner entrypoint (`source="manual_cli"`) to execute full triage for a single issue key from CLI (without Jira Automation dependency).
- Done when: service supports both on-command triage and the Jira scheduled-scan webhook path, using sequential classification then optional priority (never both inferences unconditionally).

## 3. Frontend / UX (Jira-facing outputs)
- [x] Implement mismatch detector: always compare issue type to `recommended_issue_type`; compare priority to `recommended_priority` only when the triage path ran priority (i.e. model classified as Bug). (`triage_mismatch.compute_mismatch_flags`; wire into executor when built.)
- [x] Implement Jira action executor (`jira_action_executor.JiraTriageActionExecutor`; default handler uses it when `JIRA_CLOUD_ID` and `JIRA_USER_EMAIL` are set):
  - [x] Apply `triagebot-reviewed` after every successful triage (mismatch or not). This is the dedupe marker the Jira scheduled rule depends on — without it, the JQL keeps re-matching the issue.
  - [x] Post internal comment with recommended values and concise reasoning **only when** mismatch exists (fixed **TriageBot** template; numeric confidence stays in API/audit only, not in the Jira body; optional reporter @mention when `reporter_account_id` is present on the fetched issue).
  - [x] Apply mismatch-specific labels only when applicable: `triagebot-likely-story` (type mismatch when recommending Story), `triagebot-priority-mismatch` (priority mismatch on the Bug path; N/A on the Story path).
- [x] On `TriageFailure`, apply **no** labels and post **no** comment. The issue stays unlabeled so the next scheduled scan retries it automatically until success or `created >= -30m` ages it out.
- [x] **Local CLI E2E (developer path):** With `JIRA_CLOUD_ID`, `JIRA_USER_EMAIL`, and model keys set, `scripts/run_triage_cli.py` / `triage_manual_cli.run_cli_triage` runs the full pipeline for a given issue key: fetch → classify → optional priority → mismatch → `JiraTriageActionExecutor` (`source="manual_cli"`). This is the supported way to run triage from a laptop against real Jira without any Jira-side integration.
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
- Done when: every triage attempt emits correlated audit data to structured logs (required baseline). LangFuse traces and `LANGFUSE_*` secrets for **hosted** environments are optional at MVP ship and tracked under **§8 Post-MVP → Langfuse project credentials**; when keys are set, the same run also emits model metadata to LangFuse with redaction policy applied, including confidence, with passing lint/mypy/unit.

## 6. Resilience + runtime safeguards
- [x] Add explicit timeout/retry policy for Jira fetch and Jira write operations with bounded retries.
- [ ] Add explicit timeout/retry policy for OpenRouter calls with safe fallback on exhaustion.
- [ ] Emit retry counters and timeout/failure categories to audit events and logs.
- [ ] Add guardrails for oversized payload logging (truncate consistently and mark truncation).
- [ ] Add health endpoint (`GET /health`) and minimal readiness signal for hosted environments.
- [ ] Add unit tests for retry behavior, timeout mapping, and fallback category correctness.
- Done when: transient failures are retried safely, permanent failures are observable, and hosting health checks are supported.

## 7. Deployment to stable AWS URL (EKS target)
- [ ] Add local container smoke check command in this repo (`POST /triage` with fixture payload containing `issue_key`, verify response shape and no unintended Jira writes in mocked/local mode).
- [ ] Prepare platform repo PR for the image build: add `build/docker/<jira-triage-image>/Dockerfile` plus any Dockerfile support files/dependencies needed to run the FastAPI app.
- [ ] Register the new image in platform `build/images.yaml` so the existing automation builds/publishes it.
- [ ] Document how local source maps into the platform Docker build context, including which files must be copied or vendored into `build/docker/<jira-triage-image>`.
- [ ] Confirm runtime workflow contract for Jira Automation: Jira sends only `issue_key`, service fetches issue details from Jira, runs AI triage, builds the internal comment payload, and posts suggested action + reasoning + reporter tag back to the ticket.
- [ ] Confirm EKS deployment shape with the cloud team: deployment, service/ingress, health checks, TLS/DNS, image promotion, rollback, and ownership expectations.
- [ ] Create a hosted env-var inventory in docs/TODO notes that explicitly marks each key as `Secret` vs `ConfigMap` (`JIRA_*`, `OPENROUTER_*`, optional `LANGFUSE_*` post-MVP, model id/config, allowed projects, feature flags).
- [ ] Confirm secrets/config wiring for hosted runtime: mount required secrets (`JIRA_*`, `OPENROUTER_*`); add `LANGFUSE_*` when Post-MVP Langfuse onboarding is done. Use a ConfigMap or equivalent for non-secret env vars (model id/config, allowed projects, feature flags).
- [ ] Adopt LangFuse as the primary model observability/audit sink for teams that have project keys (after Post-MVP credential task); structured CloudWatch logs remain baseline operational telemetry regardless.
- [ ] Verify the platform automation builds and publishes the first tagged image.
- [ ] Add Kubernetes deployment artifacts or repo-specific deployment instructions for EKS (deployment, service, ingress, health checks, env/secrets references).
- [ ] Add a minimal deployment checklist (manifest-level) covering: Deployment, Service, Ingress, Secret mounts, ConfigMap envs, readiness/liveness probes, and rollout/rollback commands.
- [ ] Verify Jira Automation can call the hosted `/triage` URL end-to-end on one real issue.
- [ ] Verify hosted observability: one triage run can be traced by `run_id` in service logs and any audit sink that is actually configured in that environment (structured logs required; LangFuse when keys exist per Post-MVP), including model output and final Jira comment action.
- [ ] Document DNS/custom-domain follow-up if the initial EKS ingress URL is not the final stable URL.
- Done when: Jira Automation calls a stable EKS-hosted URL successfully, the service posts the expected advisory Jira comment, and hosted triage runs are auditable end-to-end through the agreed observability stack.

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

## 8. Post-MVP
- [x] **Classification benchmark harness (in-repo):** `scripts/benchmark/classification_benchmark.py` / `scripts/benchmark/benchmark_summary.py`, `scripts/benchmark/run_classification_benchmark.py` (multi-model JSONL + `summary.json`), `scripts/benchmark/summarize_benchmark_rows.py` (offline re-aggregation), unit tests under `tests/unit/test_classification_benchmark.py` and `tests/unit/test_benchmark_summary.py`. Curated rows live under `data/` (combined `issue_benchmark_dataset.csv` plus bucket CSVs). **Jira sampler:** `scripts/benchmark/build_benchmark_dataset.py` (changelog-derived keys; uses `GET /rest/api/3/search/jql` with `nextPageToken` because Cloud removed legacy search). **Composition:** keep the CSV Bug-centric; no requirement to rebalance toward equal Story buckets while Story outcomes stay out of scope—add rows when they help Bug-path / priority signal, and keep human ground truth vs Jira fields explicit where rows encode corrections.
- [ ] **Langfuse project credentials (hosted):** After org access is sorted, obtain `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, and `LANGFUSE_BASE_URL` from the Langfuse project (UI: project **Settings → API Keys**; base URL matches cloud region or self-host). Mount or inject them for the deployed service, confirm traces/generations appear for real triage runs, and align §7 env-var inventory with what ops actually provisions. **Explicitly not on the MVP critical path** — the service already treats Langfuse keys as optional; MVP can ship on structured logs and other audit paths without waiting on whoever administers Langfuse.
- Done when: at least one baseline model is scored on the current curated set and swapping `OPENROUTER_MODEL` (or passing alternate model ids to the benchmark runner) reproduces comparable runs with saved result artifacts for A/B comparison (local `benchmark_runs/` is gitignored; operators keep artifacts outside git or attach as CI artifacts).
