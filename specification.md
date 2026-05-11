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
- Trigger triage from Jira Automation on issue creation, with a stabilization delay to avoid analyzing half-written tickets.
- Send issue key to AI Triage Service (service fetches latest issue state at analysis time).
- AI service returns structured recommendation with confidence and reasoning (fields depend on which inference steps ran; see response contract).
- Jira Automation posts an **internal comment** with reasoning: suggest reclassification and/or priority when relevant; **advisory only** (no automatic issue type or priority field mutation in Phase 1). Apply mismatch labels only when mismatch is detected (type and/or priority on the Bug path); otherwise do nothing.
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
  - Input: Jira issue-created event for target project and issue type.
  - Behavior:
    - Schedule asynchronous analysis (fixed default delay: 5 minutes for MVP).
    - Optional dedupe: if issue was updated recently, push analysis to latest update window.
  - Output: webhook call to AI triage service with issue key.
- `ai_triage_service`
  - Responsibilities:
    - Fetch issue content from Jira using issue key (summary, description, fields)
    - Run **classification** inference with bug definition policy only
    - If classification is **Story**, return type recommendation only (omit priority fields; do not call the model for priority)
    - If classification is **Bug**, run **priority** inference with priority definition policy and return suggested `P0`–`P4`
    - Return a single recommendation payload suitable for mismatch detection and audit (merged or step-scoped fields as implemented)
  - Runtime: AWS Lambda (invoked asynchronously)
  - Model provider: OpenRouter (cost-efficient model selected by configuration)
  - Suggested endpoint/event contract: `POST /triage`
    - Request contract:
      - `{ "issue_key": "BC-123", "project": "BC", "event_type": "issue_created" }`
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
  - Add internal comment with recommendation summary and reasoning only when mismatch exists. Comment text is **advisory**: suggest reclassification and/or priority with rationale; Phase 1 does **not** mutate Jira issue type or priority fields via automation.
  - Apply labels when mismatch exists:
    - `ai-reviewed`
    - `ai-likely-story` (if issue type mismatch)
    - `ai-priority-mismatch` (if priority mismatch **and** the triage path included priority — i.e. Bug outcome)
  - No comment, label, or issue update when recommendation matches current type; on the Bug path, also require no priority mismatch vs current Jira priority.
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
