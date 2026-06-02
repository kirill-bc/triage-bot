"""Outcome flags from Jira action execution."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TriageActionAppliedFlags:
    """Which Jira field mutations the action executor applied on success."""

    applied_type_change: bool = False
    applied_priority_change: bool = False
