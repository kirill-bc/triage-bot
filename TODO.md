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
- [x] Implement synchronous triage handler invoked per-issue by the Jira scheduled-scan webhook: validate the request, run the classification → optional priority flow, hand the outcome (recommendation or `TriageFailure`) to the action executor. No internal scheduler/queue (Jira-side JQL owns delay, dedupe, and retry via the `ai-reviewed` label filter and `created >= -30m` window).
- [x] Implement local runner entrypoint (`source="manual_cli"`) to execute full triage for a single issue key from CLI (without Jira Automation dependency).
- Done when: service supports both on-command triage and the Jira scheduled-scan webhook path, using sequential classification then optional priority (never both inferences unconditionally).

## 3. Frontend / UX (Jira-facing outputs)
- [x] Implement mismatch detector: always compare issue type to `recommended_issue_type`; compare priority to `recommended_priority` only when the triage path ran priority (i.e. model classified as Bug). (`triage_mismatch.compute_mismatch_flags`; wire into executor when built.)
- [x] Implement Jira action executor (`jira_action_executor.JiraTriageActionExecutor`; default handler uses it when `JIRA_BASE_URL` and `JIRA_USER_EMAIL` are set):
  - [x] Apply `ai-reviewed` after every successful triage (mismatch or not). This is the dedupe marker the Jira scheduled rule depends on — without it, the JQL keeps re-matching the issue.
  - [x] Post internal comment with recommended values and concise reasoning **only when** mismatch exists (fixed **TriageBot** template; numeric confidence stays in API/audit only, not in the Jira body; optional reporter @mention when `reporter_account_id` is present on the fetched issue).
  - [x] Apply mismatch-specific labels only when applicable: `ai-likely-story` (type mismatch when recommending Story), `ai-priority-mismatch` (priority mismatch on the Bug path; N/A on the Story path).
- [x] On `TriageFailure`, apply **no** labels and post **no** comment. The issue stays unlabeled so the next scheduled scan retries it automatically until success or `created >= -30m` ages it out.
- [x] **Local CLI E2E (developer path):** With `JIRA_BASE_URL`, `JIRA_USER_EMAIL`, and model keys set, `scripts/run_triage_cli.py` / `triage_manual_cli.run_cli_triage` runs the full pipeline for a given issue key: fetch → classify → optional priority → mismatch → `JiraTriageActionExecutor` (`source="manual_cli"`). This is the supported way to run triage from a laptop against real Jira without any Jira-side integration.
- [x] **Local tunnel for Jira → laptop:** Run the HTTP server locally and expose `POST /triage` with a public HTTPS URL (e.g. ngrok, Cloudflare Tunnel) so Jira Automation “Send web request” can reach it during development; note URL churn on free tiers and timeouts. Precursor or parallel to a stable hosted deployment, not a substitute for production hardening. **Instructions:** `README.md` section *Local HTTP server and tunnel*; helper script `scripts/run_dev_tunnel.py` (loads `.env`, uvicorn, tunnel). **Operator smoke:** still confirm Jira → tunnel → service on your tenant when you first wire Automation (not covered by CI).
- [x] **Jira Automation → deployed API (product path):** In Jira Cloud, configure rules so **Jira** sends `POST` requests to **your hosted** `POST /triage` URL with the real Automation body (`issue_key`, `project`, `source` one of `bug_created` / `priority_changed`). Prove reachability (TLS, DNS, timeouts), any auth fronting the API, and that labels/comments match the same rules as the CLI run on the same issue. Local `curl` / `TestClient` with those sources only proves the handler code, not Jira as the caller.
- Done when: (a) CLI path above is usable for QA/dev smoke on real issues with the executor live. (b) At least one Jira site has a working Automation → production-like triage URL flow verified end-to-end (Jira is the HTTP client), with runbook steps captured in docs.

## 4. Forge app (Atlassian Forge)
- [ ] Scaffold an Atlassian Forge app (e.g. **TriageBot**) in-repo or in a linked package: `manifest.yml`, environment (`development` / `staging` / `production`), and Forge CLI workflow documented in README or runbook.
- [ ] Declare required **Jira scopes** for the product path (issue read, labels, comments as needed; align with what the Python `jira_action_executor` does today vs what Forge will own).
- [ ] Decide integration shape: Forge UI/admin only, Forge **scheduled/trigger** module calling the existing triage HTTP API, and/or gradual replacement of REST automation—document the chosen split in `specification.md` or `memory.md`.
- [ ] Wire **identity & UX**: Forge app display name and listing copy consistent with in-comment **TriageBot** templates; optional custom icon; no duplicate/confusing bot names in the tenant.
- [ ] Secrets & config: Forge environment variables or app properties for base URLs, API auth to the triage service, and Jira context; no secrets committed to git.
- [ ] Installation path: site install, version bumps, and a short operator checklist (install → grant scopes → smoke one issue).
- Done when: the app installs on a target Jira Cloud site, passes Atlassian validation for declared scopes, and a documented smoke path proves Forge + triage behavior end-to-end for at least one real issue.

## 5. Integration Tests
- [ ] Add API contract tests for `POST /triage` request/response shape and validation errors.
- [ ] Add integration tests for Jira webhook adapter invoking async triage pipeline.
- [ ] Add integration tests for Jira action executor against mocked Jira API.
- [ ] Add integration tests for policy retrieval adapter using mocked content source.
- [ ] Add integration tests for mismatch/no-mismatch behavior including label combinations and sequential flow (Story outcome skips priority inference; Bug outcome invokes it).
- Done when: `pytest -m integration` passes with deterministic mocks and covers service boundaries.

## 6. Non-functional (logging, config, error handling)
- [ ] Implement structured audit logging for input metadata, model output, action taken, and timestamps.
- [ ] Add trace/correlation IDs across trigger, triage service, and Jira action executor.
- [ ] Implement retries/timeouts for Jira and model provider calls with safe failure behavior.
- [ ] Add metrics for triage volume, mismatch rate, and confidence distribution.
- [ ] Add a metric for issues that aged past the `created >= -30m` JQL window without ever being triaged successfully (manual-QA backlog signal).
- [ ] Ensure confidence is treated as advisory metadata in decision paths.
- Done when: logs and metrics support auditability, debugging, and confidence quality monitoring.

## 7. Polish & Docs
- [ ] Document architecture and module responsibilities (`jira_automation_trigger`, `ai_triage_service`, `jira_action_executor`, `audit_log_store`).
- [ ] Add runbook for local development, env setup, and test execution commands.
- [ ] Add Jira Automation setup recipe: the scheduled rule cadence (every 5 min ≤ JQL window length), the reference JQL (`project = ... AND issuetype = Bug AND labels not in (ai-reviewed) AND created >= -30m AND created <= -5m`), and the "Send web request → Custom data" body template (`issue_key`, `project`, `source: bug_created` or `priority_changed` for a priority rule).
- [ ] Document the `ai-reviewed` lifecycle: applied on every successful triage; remove the label on an issue to force re-triage; missing label on a >30m-old issue indicates the manual-QA fallback case.
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

## 9. Post-MVP
- [x] **Classification benchmark harness (in-repo):** `classification_benchmark.py` / `benchmark_summary.py`, `scripts/benchmark/run_classification_benchmark.py` (multi-model JSONL + `summary.json`), `scripts/benchmark/summarize_benchmark_rows.py` (offline re-aggregation), unit tests under `tests/unit/test_classification_benchmark.py` and `tests/unit/test_benchmark_summary.py`. Curated rows live under `data/` (combined `issue_benchmark_dataset.csv` plus bucket CSVs). **Jira sampler:** `scripts/benchmark/build_benchmark_dataset.py` (changelog-derived keys; uses `GET /rest/api/3/search/jql` with `nextPageToken` because Cloud removed legacy search).
- [ ] Grow or rebalance the curated set toward the original target composition (**25** stable Bugs, **25** stable Stories, **25** misclassified-as-Bug, **25** misclassified-as-Story) if the current CSV mix diverges; keep human ground truth vs Jira starting type explicit in each row.
- Done when: at least one baseline model is scored on the agreed full set and swapping `OPENROUTER_MODEL` (or passing alternate model ids to the benchmark runner) reproduces comparable runs with saved result artifacts for A/B comparison (local `benchmark_runs/` is gitignored; operators keep artifacts outside git or attach as CI artifacts).
