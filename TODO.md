# TODO - Jira Triage MVP

## 1. Setup / Environment
- [x] Initialize Python project tooling and dependency management for runtime and tests.
- [x] Configure environment loading for Jira, OpenRouter, and logging credentials.
- [x] Add local run scripts and ensure `.venv`-based execution works (`./scripts/run_tests.sh` entrypoint).
- [x] Define core config object for project allowlist (`TJC` first, `BC` next), delay, and feature flags.
- [x] Add CI quality gates for `mypy .`, `pytest -m lint`, and `pytest -m "unit or integration"`.
- Done when: project boots locally, config validates required env vars, and all baseline gates execute successfully.

## 2. Core Backend / API
- [x] Set up `README.md` from a generic project skeleton description to rough structure we could fill up leading to the final state of this stage
- [x] Implement `POST /triage` contract accepting `issue_key`, `project`, and `event_type`.
- [x] Add request validation for required fields and supported event values.
- [x] Implement Jira issue fetcher by issue key (summary, description, type, priority, reporter).
- [x] Add smoke script to fetch one issue by key for manual verification (`scripts/fetch_jira_issue.py`).
- [x] Implement policy context loader for bug and priority definitions.
- [x] Align `specification.md` and triage backlog with sequential analysis (classification then optional priority), advisory Jira comments, and Story-path nullable `recommended_priority`.
- Triage logic (sequential, not parallel type+priority):
  1. Classify Story vs Bug (bug policy only for this step).
  2. If the model says Story: recommend reclassifying to Story; do not run priority inference; do not compare or suggest P0–P4.
  3. If the model says Bug: run a second priority inference (priority policy + issue context). Then:
     - Jira type Bug + model Bug: compare predicted P0–P4 to current Jira priority.
     - Jira type Story + model Bug: treat as misfiled bug — recommend Bug + suggested priority (compare for mismatch/labels as designed).
  4. Surface guidance via internal comment (and mismatch labels when applicable): suggest reclassification and/or priority with reasoning; advisory only — no automatic Jira field mutation in Phase 1 (see Out-of-scope in `specification.md`).
- [x] Build prompt/input composer for step (1) and, when needed, step (2) — do not bundle both model calls into one always-on prompt.
- [x] Implement OpenRouter inference client with model name from configuration.
- [x] Parse and validate model output to strict schema (per step or merged response), including:
  - [x] `recommended_issue_type` in `Bug|Story`
  - [x] When `recommended_issue_type` is `Bug`: `recommended_priority` in `P0|P1|P2|P3|P4`; when `Story`: omit or null `recommended_priority` (no priority model output)
  - [x] `confidence` in `[0.0, 1.0]` (per inference that ran; document whether one or two scores are returned)
  - [x] `reason` non-empty
  - [x] `recommended_action` in allowed enum
- [x] Implement fallback/error response path for upstream failures and invalid model output.
- [ ] Add asynchronous trigger handler that accepts webhook event and schedules analysis with default 5-minute delay.
- [ ] Add optional dedupe/recent-update deferral logic behind configuration flag.
- [ ] Implement local runner entrypoint to execute full triage for a single issue key from CLI (without Jira Automation dependency).
- [ ] Implement webhook handler for Jira Automation update events to trigger local analysis flow.
- Done when: service supports both on-command triage and Jira Automation-triggered local analysis, using sequential classification then optional priority (never both inferences unconditionally).

## 3. Frontend / UX (Jira-facing outputs)
- [ ] Implement mismatch detector: always compare issue type to `recommended_issue_type`; compare priority to `recommended_priority` only when the triage path ran priority (i.e. model classified as Bug).
- [ ] Implement Jira action executor to post internal comment only when mismatch exists.
- [ ] Format comment body with recommended values, numeric confidence, and concise reasoning.
- [ ] Apply labels only on mismatch:
  - [ ] `ai-reviewed`
  - [ ] `ai-likely-story` when issue type mismatch
  - [ ] `ai-priority-mismatch` when priority mismatch (Bug path only; N/A when recommendation is Story)
- [ ] Ensure no visible Jira action is taken when recommendation matches current state.
- [ ] Add end-to-end local flow task: fetch issue -> analyze -> decide mismatch -> apply Jira comment/labels.
- [ ] Add automation-driven local flow task: accept Jira Automation update payload -> analyze latest ticket state -> apply Jira comment/labels.
- Done when: both manual and automation-driven local execution apply expected Jira updates for mismatch cases and remain silent for match cases.

## 4. Integration Tests
- [ ] Add API contract tests for `POST /triage` request/response shape and validation errors.
- [ ] Add integration tests for Jira webhook adapter invoking async triage pipeline.
- [ ] Add integration tests for Jira action executor against mocked Jira API.
- [ ] Add integration tests for policy retrieval adapter using mocked content source.
- [ ] Add integration tests for mismatch/no-mismatch behavior including label combinations and sequential flow (Story outcome skips priority inference; Bug outcome invokes it).
- Done when: `pytest -m integration` passes with deterministic mocks and covers service boundaries.

## 5. Non-functional (logging, config, error handling)
- [ ] Implement structured audit logging for input metadata, model output, action taken, and timestamps.
- [ ] Add trace/correlation IDs across trigger, triage service, and Jira action executor.
- [ ] Implement retries/timeouts for Jira and model provider calls with safe failure behavior.
- [ ] Add metrics for triage volume, mismatch rate, and confidence distribution.
- [ ] Add safeguards for stale-event timing and race-condition observability.
- [ ] Ensure confidence is treated as advisory metadata in decision paths.
- Done when: logs and metrics support auditability, debugging, and confidence quality monitoring.

## 6. Polish & Docs
- [ ] Document architecture and module responsibilities (`jira_automation_trigger`, `ai_triage_service`, `jira_action_executor`, `audit_log_store`).
- [ ] Add runbook for local development, env setup, and test execution commands.
- [ ] Document Jira automation setup, webhook payload expectations, and delay/dedupe behavior.
- [ ] Document known MVP limitations and out-of-scope items (no auto mutation, no Zendesk intake, no full RAG).
- [ ] Record default model-selection rationale and tuning strategy for confidence calibration in Phase 2.
- Done when: a new engineer can run, test, and operate the MVP using repository docs alone.

## 7. Deployment (MVP scope)
- [ ] Package app for demo deployment runnable by QA (Docker Compose or equivalent local service process).
- [ ] Provide `.env` template and startup scripts for reproducible non-cloud demo setup.
- [ ] Configure deployment gate with lint/type/unit+integration checks before demo release.
- [ ] Add demo smoke check: send Jira Automation-like event for sample issue and verify expected Jira side effects.
- [ ] Document optional cloud path as deferred follow-up (not required for MVP demo).
- Done when: QA can run the automation-driven triage demo as a stable environment without requiring cloud infrastructure.

## 8. Post-MVP
- [ ] Build a **classification benchmark** (curated, labeled set) so different OpenRouter models can be compared on measurable accuracy, not intuition alone. Target composition: **25** correctly created **Bugs**, **25** correctly created **Stories**, **25** initially misclassified **Bugs**, and **25** initially misclassified **Stories** (for the two misclassified buckets, each row documents **Jira’s starting issue type** vs **human ground truth** so metrics are unambiguous). Each row needs enough summary/description to run the same triage prompts. Deliver a repeatable evaluation harness (script or pytest slice) that runs the pipeline over the set, records type (and Bug-path priority) predictions, and reports aggregate metrics (e.g. accuracy, confusion matrix, cost/latency per model).
- Done when: at least one baseline model is scored on the full 100-case set and swapping `OPENROUTER_MODEL` reproduces comparable runs with saved result artifacts for A/B comparison.
