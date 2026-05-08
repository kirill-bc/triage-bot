# Project Specification (MVP)

- Project: Jira-triage

## Overview
- Build an AI-assisted Jira triage system for Support-created issues, focused on validating:
  - Issue type classification (`Bug` vs `Story`)
  - Priority assignment (`P0` to `P4`) against policy definitions
- Primary users:
  - Support agents (ticket reporters)
  - QA and Engineering triagers
  - Product stakeholders consuming Jira quality metrics
- Initial behavior is advisory (non-blocking), with future phases enabling soft and hard enforcement.

## Scope
- Phase 1 (MVP): recommendation-only triage for new `Bug` issues in a target project (`TJC` first, `BC` next).
- Trigger triage from Jira Automation on issue creation, with a stabilization delay to avoid analyzing half-written tickets.
- Send issue key to AI Triage Service (service fetches latest issue state at analysis time).
- AI service returns structured recommendation with confidence and reasoning.
- Jira Automation posts an internal comment and applies mismatch labels only when mismatch is detected (classification and/or priority); otherwise do nothing.
- Persist audit logs of AI inputs/outputs and applied automation actions.

## Out-of-scope
- Automatic issue mutation in Phase 1 (no forced reclassification or priority updates).
- Zendesk intake integration.
- Full Confluence-wide RAG implementation.
- Enforcement workflows requiring manual override reason (future hard-enforcement phase).

## Key User Scenarios / Flows
- New Support-created `Bug` is triaged and receives AI recommendation comment if mismatch is detected.
- AI detects likely `Story` and labels ticket for product/triage review.
- AI detects priority mismatch and labels ticket for severity review.
- Existing open issues can be batch triaged by AI service outside Jira Automation (manual or scheduled entry point).

## API / Modules
- `jira_automation_trigger`
  - Input: Jira issue-created event for target project and issue type.
  - Behavior:
    - Schedule asynchronous analysis (fixed default delay: 5 minutes for MVP).
    - Optional dedupe: if issue was updated recently, push analysis to latest update window.
  - Output: webhook call to AI triage service with issue key.
- `ai_triage_service`
  - Responsibilities:
    - Fetch issue content from Jira using issue key (summary, description, fields)
    - Evaluate against bug and priority definitions
    - Return recommendation payload
  - Runtime: AWS Lambda (invoked asynchronously)
  - Model provider: OpenRouter (cost-efficient model selected by configuration)
  - Suggested endpoint/event contract: `POST /triage`
    - Request contract:
      - `{ "issue_key": "BC-123", "project": "BC", "event_type": "issue_created" }`
    - Response contract:
      ```json
      {
        "recommended_issue_type": "Bug | Story",
        "recommended_priority": "P0 | P1 | P2 | P3 | P4",
        "confidence": 0.0,
        "reason": "Explanation",
        "recommended_action": "comment_only | label | reclassify | update_priority"
      }
      ```
- `jira_action_executor`
  - Add internal comment with recommendation summary and reasoning only when mismatch exists.
  - Apply labels when mismatch exists:
    - `ai-reviewed`
    - `ai-likely-story` (if issue type mismatch)
    - `ai-priority-mismatch` (if priority mismatch)
  - No comment, label, or issue update when recommendation matches current type and priority.
  - If recommendation matches current state, take no visible action.
- `audit_log_store`
  - Persist request metadata, model output, confidence, action taken, and timestamp.

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
  - Recommendation parser/validator (schema, enums, confidence bounds).
  - Classification and priority decision orchestration.
  - Label/action mapping logic.
  - Error handling and fallback behavior.
- Integration (`pytest -m integration`)
  - AI service endpoint contract tests (request/response shape).
  - Jira webhook adapter + action executor against mocked Jira API.
  - Confluence policy retrieval adapter with mocked content source.
- E2E (`pytest -m e2e`, when applicable)
  - Create sample Jira issues and validate expected comment/labels in test project.
  - Batch-triage flow for existing open tickets.
- Quality gates
  - `mypy .`
  - `pytest -m lint` (or `flake8` equivalent)
  - `pytest -m "unit or integration"` for release confidence

## Risks / Constraints
- False positives/negatives in AI recommendations.
- Support trust impact if recommendations are noisy or overly strict.
- Policy ambiguity can reduce recommendation consistency.
- Attachment analysis may increase latency and operational complexity.
- Jira event timing/race conditions can produce stale recommendations without dedupe/delay.

## Assumptions
- Jira Automation can call external webhooks.
- AI service has secure access to Jira and policy definitions via issue-key lookup.
- Policy definitions are stable enough to codify into prompt/context.
- Phase 1 success is measured by recommendation quality, not strict enforcement.
- Observability stack is available for structured audit logging and dashboards.
- OpenRouter is approved for model access and cost controls.

## Open Questions
- Which exact OpenRouter model should be default in MVP (quality/cost target)?
