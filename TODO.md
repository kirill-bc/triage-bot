# TODO - Jira Triage MVP

## 1. Setup / Environment
- [x] Initialize Python project tooling and dependency management for runtime and tests.
- [ ] Configure environment loading for Jira, OpenRouter, and logging credentials.
- [x] Add local run scripts and ensure `.venv`-based execution works (`./scripts/run_tests.sh` entrypoint).
- [ ] Define core config object for project allowlist (`TJC` first, `BC` next), delay, and feature flags.
- [ ] Add CI quality gates for `mypy .`, `pytest -m lint`, and `pytest -m "unit or integration"`.
- Done when: project boots locally, config validates required env vars, and all baseline gates execute successfully.

## 2. Core Backend / API
- [ ] Implement `POST /triage` contract accepting `issue_key`, `project`, and `event_type`.
- [ ] Add request validation for required fields and supported event values.
- [ ] Implement Jira issue fetcher by issue key (summary, description, type, priority, reporter).
- [ ] Implement policy context loader for bug and priority definitions.
- [ ] Build prompt/input composer combining issue data plus policy definitions.
- [ ] Implement OpenRouter inference client with model name from configuration.
- [ ] Parse and validate model output to strict schema:
  - [ ] `recommended_issue_type` in `Bug|Story`
  - [ ] `recommended_priority` in `P0|P1|P2|P3|P4`
  - [ ] `confidence` in `[0.0, 1.0]`
  - [ ] `reason` non-empty
  - [ ] `recommended_action` in allowed enum
- [ ] Implement fallback/error response path for upstream failures and invalid model output.
- [ ] Add asynchronous trigger handler that accepts webhook event and schedules analysis with default 5-minute delay.
- [ ] Add optional dedupe/recent-update deferral logic behind configuration flag.
- [ ] Implement local runner entrypoint to execute full triage for a single issue key from CLI (without Jira Automation dependency).
- [ ] Implement webhook handler for Jira Automation update events to trigger local analysis flow.
- Done when: service supports both on-command triage and Jira Automation-triggered local analysis.

## 3. Frontend / UX (Jira-facing outputs)
- [ ] Implement mismatch detector comparing recommendation vs current Jira type and priority.
- [ ] Implement Jira action executor to post internal comment only when mismatch exists.
- [ ] Format comment body with recommended values, numeric confidence, and concise reasoning.
- [ ] Apply labels only on mismatch:
  - [ ] `ai-reviewed`
  - [ ] `ai-likely-story` when issue type mismatch
  - [ ] `ai-priority-mismatch` when priority mismatch
- [ ] Ensure no visible Jira action is taken when recommendation matches current state.
- [ ] Add end-to-end local flow task: fetch issue -> analyze -> decide mismatch -> apply Jira comment/labels.
- [ ] Add automation-driven local flow task: accept Jira Automation update payload -> analyze latest ticket state -> apply Jira comment/labels.
- Done when: both manual and automation-driven local execution apply expected Jira updates for mismatch cases and remain silent for match cases.

## 4. Integration Tests
- [ ] Add API contract tests for `POST /triage` request/response shape and validation errors.
- [ ] Add integration tests for Jira webhook adapter invoking async triage pipeline.
- [ ] Add integration tests for Jira action executor against mocked Jira API.
- [ ] Add integration tests for policy retrieval adapter using mocked content source.
- [ ] Add integration tests for mismatch/no-mismatch behavior including label combinations.
- Done when: `pytest -m integration` passes with deterministic mocks and covers service boundaries.

## 5. E2E Tests (Playwright / system flows)
- [ ] Define E2E scenarios mapped to user flows for support-created bug triage.
- [ ] Implement E2E: mismatch case produces expected internal comment and labels.
- [ ] Implement E2E: likely-story case applies story mismatch labeling correctly.
- [ ] Implement E2E: matching recommendation produces no comment and no labels.
- [ ] Implement E2E: batch triage entrypoint processes existing open issues.
- [ ] Wire `./scripts/run_e2e_tests.sh` with server lifecycle settings (`E2E_SERVER_COMMAND` / `E2E_SERVER_DISABLED`).
- Done when: `pytest -m e2e` validates core user flows in a test Jira project (or equivalent fully mocked end-to-end harness).

## 6. Non-functional (logging, config, error handling)
- [ ] Implement structured audit logging for input metadata, model output, action taken, and timestamps.
- [ ] Add trace/correlation IDs across trigger, triage service, and Jira action executor.
- [ ] Implement retries/timeouts for Jira and model provider calls with safe failure behavior.
- [ ] Add metrics for triage volume, mismatch rate, and confidence distribution.
- [ ] Add safeguards for stale-event timing and race-condition observability.
- [ ] Ensure confidence is treated as advisory metadata in decision paths.
- Done when: logs and metrics support auditability, debugging, and confidence quality monitoring.

## 7. Polish & Docs
- [ ] Document architecture and module responsibilities (`jira_automation_trigger`, `ai_triage_service`, `jira_action_executor`, `audit_log_store`).
- [ ] Add runbook for local development, env setup, and test execution commands.
- [ ] Document Jira automation setup, webhook payload expectations, and delay/dedupe behavior.
- [ ] Document known MVP limitations and out-of-scope items (no auto mutation, no Zendesk intake, no full RAG).
- [ ] Record default model-selection rationale and tuning strategy for confidence calibration in Phase 2.
- Done when: a new engineer can run, test, and operate the MVP using repository docs alone.

## 8. Deployment (MVP scope)
- [ ] Package app for demo deployment runnable by QA (Docker Compose or equivalent local service process).
- [ ] Provide `.env` template and startup scripts for reproducible non-cloud demo setup.
- [ ] Configure deployment gate with lint/type/unit+integration checks before demo release.
- [ ] Add demo smoke check: send Jira Automation-like event for sample issue and verify expected Jira side effects.
- [ ] Document optional cloud path as deferred follow-up (not required for MVP demo).
- Done when: QA can run the automation-driven triage demo as a stable environment without requiring cloud infrastructure.
