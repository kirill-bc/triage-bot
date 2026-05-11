# Project Specification (MVP)

- Project: Jira-triage

## Overview
- Build an AI-assisted Jira triage system for Support-created issues, focused on validating:
  - Issue type classification (`Bug` vs `Story`)
  - Priority assignment (`P0` to `P4`) against policy definitions **only when the model classifies the work as a Bug** (priority inference is not run for Story outcomes)
- Analysis is **sequential**: (1) Story vs Bug using bug policy; (2) if and only if the outcome is Bug, run a second inference for priority using priority policy. Do not run both model calls unconditionally in parallel.
- Primary users:
  - Support agents (ticket reporters)
  - QA and Engineering triagers
  - Product stakeholders consuming Jira quality metrics
- Initial behavior is advisory (non-blocking), with future phases enabling soft and hard enforcement.

## Scope
- Phase 1 (MVP): recommendation-only triage for new issues in a target project (`TJC` first, `BC` next). Primary trigger is Support-created **Bug** tickets; the same pipeline must handle **Story**-typed tickets when analysis shows they are misfiled (model says Bug + suggested priority).
- Trigger triage from a Jira Automation **scheduled rule** whose JQL selects unprocessed issues in a stabilization window (see `jira_automation_trigger` below). Jira owns delay, dedupe, and the backstop window so the service stays stateless.
- Send issue key to AI Triage Service (service fetches latest issue state at analysis time).
- AI service returns structured recommendation with confidence and reasoning (fields depend on which inference steps ran; see response contract).
- The AI Triage Service applies an internal comment and the `ai-reviewed` label after every **successful** analysis, regardless of whether a mismatch was detected. Mismatch-specific labels (`ai-likely-story`, `ai-priority-mismatch`) and comment content trigger only on mismatch; **advisory only** (no automatic issue type or priority field mutation in Phase 1).
- Persist audit logs of AI inputs/outputs and applied automation actions.

## Out-of-scope
- Automatic issue mutation in Phase 1 (no forced reclassification or priority updates).
- Zendesk intake integration.
- Full Confluence-wide RAG implementation.
- Enforcement workflows requiring manual override reason (future hard-enforcement phase).

## Key User Scenarios / Flows
- New Support-created `Bug` is triaged: classify Story vs Bug; if Bug, predict priority; internal comment + labels only on mismatch (type and/or priority).
- AI concludes **Story** (ticket filed as Bug): recommend reclassify to Story; **no** priority step, **no** priority labels; comment/labels reflect type mismatch only.
- AI concludes **Bug** when Jira type is **Story**: misfiled bug — recommend Bug + suggested priority; compare for mismatch and label accordingly.
- AI detects priority mismatch on the Bug path and labels ticket for severity review.
- Existing open issues can be batch triaged by AI service outside Jira Automation (manual or scheduled entry point).

## API / Modules
- `jira_automation_trigger`
  - Implementation: a Jira Cloud Automation **scheduled rule** (no event trigger). Every N minutes it queries Jira with a JQL filter that combines stabilization, dedupe, and backstop in one expression, then sends one `POST /triage` per matched issue with a **thin** payload that the service expands by re-fetching the issue.
  - Reference JQL (per project):
    ```jql
    project = BC AND issuetype = Bug
      AND labels not in (ai-reviewed)
      AND created >= -30m AND created <= -5m
    ```
    - `created <= -5m` is the stabilization delay (no half-written tickets).
    - `labels not in (ai-reviewed)` is the dedupe filter — the service applies `ai-reviewed` on every successful analysis, so each issue is triaged exactly once.
    - `created >= -30m` is the backstop window. Failed analyses (transient Jira/OpenRouter outage, invalid model output) keep matching until success or until the issue ages past 30 minutes; then they fall through and surface as manual-QA cases via metrics.
  - Reference rule cadence: every 5 minutes (must be ≤ the JQL window length so no issue is missed).
  - Reference request body (Jira Automation **Send web request → Custom data**):
    ```json
    {
      "issue_key": "{{issue.key}}",
      "project": "{{issue.project.key}}",
      "source": "scheduled_scan"
    }
    ```
  - The trigger carries no issue content. The service re-fetches the issue at analysis time so its view is always the latest state, regardless of Jira-side templating quirks.
- `ai_triage_service`
  - Responsibilities:
    - Fetch issue content from Jira using issue key (summary, description, fields)
    - Run **classification** inference with bug definition policy only
    - If classification is **Story**, return type recommendation only (omit priority fields; do not call the model for priority)
    - If classification is **Bug**, run **priority** inference with priority definition policy and return suggested `P0`–`P4`
    - Return a single recommendation payload suitable for mismatch detection and audit (merged or step-scoped fields as implemented), or a `TriageFailure` if any upstream call or schema validation fails.
  - Runtime: AWS Lambda (invoked synchronously by the Jira webhook).
  - Model provider: OpenRouter (cost-efficient model selected by configuration)
  - Endpoint contract: `POST /triage`
    - Request body:
      - `{ "issue_key": "BC-123", "project": "BC", "source": "scheduled_scan" }`
      - `source` is a closed enum (Pydantic `Literal`). Future values (e.g. `manual_cli` for the local runner) extend the literal without changing the request shape.
    - Response contract (illustrative; exact nullability is enforced in code). Example when classification is Story (no priority step):
      ```json
      {
        "recommended_issue_type": "Story",
        "recommended_priority": null,
        "confidence": 0.0,
        "reason": "Explanation",
        "recommended_action": "comment_only | label | reclassify | update_priority"
      }
      ```
      When classification is Bug, `recommended_priority` is a string `P0`–`P4` from the second inference step (never null).
      - When `recommended_issue_type` is `Story`, `recommended_priority` is **not** produced by a priority inference step (`null` or omitted). Mismatch handling compares **type only** on that path.
      - When `recommended_issue_type` is `Bug`, `recommended_priority` is required. Compare to Jira priority when Jira type is Bug; when Jira type was Story, treat as misfiled bug and compare both type and suggested priority as designed.
      - `confidence` may represent the last inference that ran, or separate fields per step — document and validate in the parser; at minimum, classification always has a score.
- `jira_action_executor`
  - Apply the `ai-reviewed` label **after every successful triage**, mismatch or not. This is the dedupe marker the Jira scheduled rule depends on; without it the rule re-analyzes the same issue every cycle until it ages out of the JQL window.
  - When a mismatch is detected, additionally:
    - Post an internal comment with the recommendation summary, numeric confidence, and reasoning. Comment text is **advisory**: suggest reclassification and/or priority with rationale; Phase 1 does **not** mutate Jira issue type or priority fields via automation.
    - Apply mismatch-specific labels:
      - `ai-likely-story` when the issue type differs from `recommended_issue_type`.
      - `ai-priority-mismatch` when the Bug path predicted a priority that differs from the current Jira priority. N/A on the Story path (priority inference does not run).
  - When recommendation matches current state, apply `ai-reviewed` only — no comment, no mismatch labels.
  - When triage returns a `TriageFailure` (Jira fetch error, OpenRouter inference error, invalid model output, unexpected error), apply **no** labels and post **no** comment. The issue keeps matching the JQL and is retried automatically on the next scheduled run, until it succeeds or ages past the backstop window.
  - Re-triage policy: an operator may remove `ai-reviewed` on a Jira issue to force re-analysis on the next scheduled scan. There is no TTL or service-side state to clear.
- `audit_log_store`
  - Persist request metadata (including `source`), model output, confidence, action taken, and timestamp.

## Data Sources & Constraints
- Policy definitions from Confluence:
  - Bug definition guideline
  - Priority definition guideline
- Jira issue data:
  - Required: issue key, type, priority, summary, description, reporter
  - Optional (Phase 2+): comments, attachments
- Constraints:
  - Recommendations must be explainable and auditable.
  - Confidence output can remain AI-generated in MVP and must be treated as advisory metadata, not deterministic truth.
  - Confidence score required for downstream policy decisions.
  - Async processing is acceptable; low latency is not an MVP requirement.
  - Architecture must allow operation with and without Jira Automation entrypoint.

## Confidence Policy (MVP)
- Use AI-generated `confidence` (0.0-1.0) directly in the triage response for Phase 1.
- Jira comment behavior in Phase 1:
  - Post comment when mismatch exists, regardless of confidence value.
  - Include numeric confidence and short rationale in comment body.
- Reliability guidance for Phase 1:
  - Treat confidence as a ranking signal, not a calibrated probability.
  - Expect score drift across model changes and prompt revisions.
  - Use observability data to compare confidence vs final human triage outcomes, then calibrate thresholds in Phase 2.

## Testing Strategy
- Unit (`pytest -m unit`)
  - Recommendation parser/validator (schema, enums, confidence bounds, nullable `recommended_priority` on Story path).
  - Classification and priority orchestration: Story path skips priority model; Bug path runs priority second.
  - Label/action mapping logic.
  - Error handling and fallback behavior.
- Integration (`pytest -m integration`)
  - AI service endpoint contract tests (request/response shape).
  - Jira webhook adapter + action executor against mocked Jira API.
  - Confluence policy retrieval adapter with mocked content source.
  - Optional opt-in checks (e.g. live OpenRouter smoke) where real calls are explicitly enabled.
- System validation (no Playwright; service targets Lambda-style invocation)
  - Manual or scripted checks against a test Jira project (comments/labels) as needed for demos.
  - Prefer integration tests with mocks for automation in CI.
- Quality gates
  - `mypy .`
  - `pytest -m lint` (or `flake8` equivalent)
  - `pytest -m "unit or integration"` for release confidence

## Risks / Constraints
- False positives/negatives in AI recommendations.
- Support trust impact if recommendations are noisy or overly strict.
- Policy ambiguity can reduce recommendation consistency.
- Attachment analysis may increase latency and operational complexity.
- The scheduled-rule backstop window (`created >= -30m`) means transient outages longer than ~25 minutes silently drop issues from automated triage; those cases require a manual-QA fallback path (track via metrics).

## Assumptions
- Jira Automation can call external webhooks.
- AI service has secure access to Jira and policy definitions via issue-key lookup.
- Policy definitions are stable enough to codify into prompt/context.
- Phase 1 success is measured by recommendation quality, not strict enforcement.
- Observability stack is available for structured audit logging and dashboards.
- OpenRouter is approved for model access and cost controls.

## Open Questions
- Which exact OpenRouter model should be default in MVP (quality/cost target)?
