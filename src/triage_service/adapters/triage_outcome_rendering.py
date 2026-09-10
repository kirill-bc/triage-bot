"""Transport-independent triage outcome decisions and comment rendering."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from triage_service.adapters.jira_issue_fetcher import FetchedIssue
from triage_service.core.triage_mismatch import compute_mismatch_flags
from triage_service.core.triage_recommendation_parser import TriageRecommendation

PrioritySignal = Literal["prioritize", "deescalate"]

_COMMENT_TEMPLATES_PATH = Path(__file__).resolve().parent / "jira_comment_templates.json"


@dataclass(frozen=True, slots=True)
class AutoApplyPolicy:
    """Enabled automatic Jira field mutations."""

    apply_deescalation: bool = False
    apply_escalation: bool = False
    apply_bug_to_story: bool = False


@dataclass(frozen=True, slots=True)
class PriorityAction:
    """Requested priority mutation."""

    from_priority: str
    to_priority: str


@dataclass(frozen=True, slots=True)
class OutcomeDecision:
    """Transport-independent labels, comment gate, and mutation directives."""

    labels: list[str]
    priority_signal: PrioritySignal | None
    post_comment: bool
    apply_bug_to_story: bool
    apply_priority: PriorityAction | None


def _p0_p4_rank(label: str | None) -> int | None:
    if label is None:
        return None
    normalized = str(label).strip().upper()
    if len(normalized) != 2 or normalized[0] != "P" or normalized[1] not in "01234":
        return None
    return int(normalized[1])


def _priority_signal(
    issue: FetchedIssue,
    recommendation: TriageRecommendation,
) -> PrioritySignal | None:
    if recommendation.recommended_issue_type != "Bug":
        return None
    recommended = recommendation.recommended_priority
    if recommended is None:
        return None
    original_rank = _p0_p4_rank(issue.priority)
    recommended_rank = _p0_p4_rank(str(recommended))
    if original_rank is None or recommended_rank is None:
        return None
    if recommended_rank < original_rank:
        return "prioritize"
    if recommended_rank > original_rank:
        return "deescalate"
    return None


def build_outcome_decision(
    issue: FetchedIssue,
    recommendation: TriageRecommendation,
    *,
    policy: AutoApplyPolicy,
) -> OutcomeDecision:
    """Select labels, comment behavior, and enabled field changes."""
    flags = compute_mismatch_flags(issue, recommendation)
    signal = _priority_signal(issue, recommendation)
    likely_story = (
        flags.type_mismatch and recommendation.recommended_issue_type == "Story"
    )
    labels = ["triagebot-reviewed"]
    if likely_story:
        labels.append("triagebot-likely-story")
    if signal is not None:
        labels.append("triagebot-priority-mismatch")

    apply_bug_to_story = (
        policy.apply_bug_to_story
        and likely_story
        and str(issue.issue_type).strip().upper() == "BUG"
    )
    apply_priority = _priority_action(issue, recommendation, signal, policy)
    return OutcomeDecision(
        labels=labels,
        priority_signal=signal,
        post_comment=likely_story or signal is not None,
        apply_bug_to_story=apply_bug_to_story,
        apply_priority=apply_priority,
    )


def _priority_action(
    issue: FetchedIssue,
    recommendation: TriageRecommendation,
    signal: PrioritySignal | None,
    policy: AutoApplyPolicy,
) -> PriorityAction | None:
    enabled = (
        signal == "prioritize"
        and policy.apply_escalation
        or signal == "deescalate"
        and policy.apply_deescalation
    )
    if not enabled or recommendation.recommended_priority is None:
        return None
    current = str(issue.priority).strip() if issue.priority is not None else ""
    return PriorityAction(
        from_priority=current,
        to_priority=str(recommendation.recommended_priority).strip(),
    )


def _load_comment_templates() -> dict[str, dict[str, str]]:
    raw = json.loads(_COMMENT_TEMPLATES_PATH.read_text(encoding="utf-8"))
    return {
        group: {key: str(value) for key, value in raw[group].items()}
        for group in ("advisory", "applied", "confluence")
    }


_COMMENT_TEMPLATES = _load_comment_templates()


def _template_group(*, mutations_applied: bool) -> dict[str, str]:
    return _COMMENT_TEMPLATES["applied" if mutations_applied else "advisory"]


def _opening_text(issue: FetchedIssue, *, mutations_applied: bool) -> str:
    templates = _template_group(mutations_applied=mutations_applied)
    key = "mention_intro" if issue.reporter_account_id else "no_mention_intro"
    return templates[key]


def _suggestion_text(
    issue: FetchedIssue,
    recommendation: TriageRecommendation,
    *,
    mutations_applied: bool,
) -> str:
    templates = _template_group(mutations_applied=mutations_applied)
    if recommendation.recommended_issue_type == "Story":
        body = templates["story_action"]
    else:
        recommended = recommendation.recommended_priority
        assert recommended is not None
        current = _current_priority(issue)
        body = templates["priority_action"].format(
            from_priority=current,
            to_priority=str(recommended).strip(),
        )
    return f"- {body}" if mutations_applied else body


def _current_priority(issue: FetchedIssue) -> str:
    if issue.priority is None or not str(issue.priority).strip():
        return "(not set)"
    return str(issue.priority).strip()


def _rationale_text(
    recommendation: TriageRecommendation,
    *,
    mutations_applied: bool,
) -> str:
    templates = _template_group(mutations_applied=mutations_applied)
    return templates["rationale"].format(reason=recommendation.reason)


def _closing_text(
    issue: FetchedIssue,
    recommendation: TriageRecommendation,
    *,
    mutations_applied: bool,
) -> str:
    templates = _template_group(mutations_applied=mutations_applied)
    if recommendation.recommended_issue_type == "Story":
        return templates["closing_bug"]
    return templates["closing_priority"].format(current_priority=_current_priority(issue))


def _resource_parts(recommendation: TriageRecommendation) -> tuple[str, str, str]:
    confluence = _COMMENT_TEMPLATES["confluence"]
    if recommendation.recommended_issue_type == "Story":
        return (
            confluence["helpful_resources_heading"],
            confluence["bug_requirements_link_text"],
            confluence["bug_requirements_url"],
        )
    return (
        confluence["helpful_resources_heading"],
        confluence["priority_definitions_link_text"],
        confluence["priority_definitions_url"],
    )


def render_adf_comment(
    issue: FetchedIssue,
    recommendation: TriageRecommendation,
    *,
    mutations_applied: bool,
) -> dict[str, Any]:
    """Render the Jira REST v3 ADF comment with a structured mention."""
    opening_nodes: list[dict[str, Any]] = []
    if issue.reporter_account_id:
        display = issue.reporter.strip() or issue.reporter_account_id
        opening_nodes.append(
            {
                "type": "mention",
                "attrs": {
                    "id": issue.reporter_account_id,
                    "text": f"@{display}",
                    "accessLevel": "",
                },
            }
        )
    opening_nodes.append(
        {"type": "text", "text": _opening_text(issue, mutations_applied=mutations_applied)}
    )
    heading, link_text, link_url = _resource_parts(recommendation)
    paragraph_texts = (
        _suggestion_text(issue, recommendation, mutations_applied=mutations_applied),
        _rationale_text(recommendation, mutations_applied=mutations_applied),
        _closing_text(issue, recommendation, mutations_applied=mutations_applied),
    )
    content = [{"type": "paragraph", "content": opening_nodes}]
    content.extend(
        {"type": "paragraph", "content": [{"type": "text", "text": text}]}
        for text in paragraph_texts
    )
    content.append(
        {
            "type": "paragraph",
            "content": [
                {"type": "text", "text": heading},
                {
                    "type": "text",
                    "text": link_text,
                    "marks": [{"type": "link", "attrs": {"href": link_url}}],
                },
            ],
        }
    )
    return {"version": 1, "type": "doc", "content": content}


def render_plain_text_comment(
    issue: FetchedIssue,
    recommendation: TriageRecommendation,
    *,
    mutations_applied: bool,
) -> str:
    """Render Jira Automation comment text using account-id mention syntax."""
    mention = (
        f"[~accountid:{issue.reporter_account_id}]"
        if issue.reporter_account_id
        else ""
    )
    opening = f"{mention}{_opening_text(issue, mutations_applied=mutations_applied)}"
    heading, link_text, link_url = _resource_parts(recommendation)
    return "\n\n".join(
        (
            opening,
            _suggestion_text(issue, recommendation, mutations_applied=mutations_applied),
            _rationale_text(recommendation, mutations_applied=mutations_applied),
            _closing_text(issue, recommendation, mutations_applied=mutations_applied),
            f"{heading}{link_text}: {link_url}",
        )
    )
