"""Unit tests for scripts/fetch_jira_issue.py."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from triage_service.adapters.jira_issue_fetcher import FetchedIssue


@pytest.mark.unit
def test_build_fetch_output_uses_jira_custom_field_ids_and_fetches_zendesk() -> None:
    from scripts.fetch_jira_issue import build_fetch_output

    issue = FetchedIssue(
        issue_key="BC-321",
        summary="Escalated",
        issue_type="Bug",
        priority="P2",
        reporter="Support Agent",
        zendesk_ticket_ids=["5001", "5002"],
        zendesk_ticket_count=2,
    )
    zendesk = MagicMock()
    zendesk.credentials_configured = True
    zendesk.collect_linked_ticket_ids.return_value = ["5001", "5002"]
    zendesk.fetch_tickets_by_ids.return_value = []

    with patch("scripts.fetch_jira_issue.ZendeskTicketFetcher", return_value=zendesk):
        payload = build_fetch_output(issue, settings=object())  # type: ignore[arg-type]

    assert payload["zendesk_ticket_ids"] == ["5001", "5002"]
    assert payload["zendesk_ticket_count"] == 2
    assert payload["linked_zendesk_ticket_ids"] == ["5001", "5002"]
    assert payload["zendesk_tickets"] == []
    zendesk.fetch_tickets_by_ids.assert_called_once_with(["5001", "5002"])


@pytest.mark.unit
def test_build_fetch_output_skips_zendesk_when_credentials_missing() -> None:
    from scripts.fetch_jira_issue import build_fetch_output

    issue = FetchedIssue(
        issue_key="BC-321",
        summary="Escalated",
        issue_type="Bug",
        reporter="Support Agent",
        zendesk_ticket_ids=["5001"],
    )
    zendesk = MagicMock()
    zendesk.credentials_configured = False

    with patch("scripts.fetch_jira_issue.ZendeskTicketFetcher", return_value=zendesk):
        payload = build_fetch_output(issue, settings=object())  # type: ignore[arg-type]

    assert payload["zendesk_tickets"] == []
    assert "zendesk_fetch_skipped_reason" in payload
    zendesk.fetch_tickets_by_ids.assert_not_called()


@pytest.mark.unit
def test_main_prints_fetch_output_json(capsys: pytest.CaptureFixture[str]) -> None:
    from scripts.fetch_jira_issue import main

    issue = FetchedIssue(
        issue_key="BC-321",
        summary="Escalated",
        issue_type="Bug",
        reporter="Support Agent",
        zendesk_ticket_ids=["12345"],
    )

    class _Fetcher:
        def fetch(self, issue_key: str, *, run_id: str) -> FetchedIssue:
            _ = (issue_key, run_id)
            return issue

    with (
        patch("scripts.fetch_jira_issue.load_settings", return_value=object()),
        patch("scripts.fetch_jira_issue.JiraIssueFetcher", return_value=_Fetcher()),
        patch(
            "scripts.fetch_jira_issue.build_fetch_output",
            return_value={"issue_key": "BC-321", "linked_zendesk_ticket_ids": ["12345"]},
        ),
    ):
        rc = main(["BC-321"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["linked_zendesk_ticket_ids"] == ["12345"]
