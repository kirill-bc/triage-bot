# AGENTS.md

## Purpose

This repository is a Python service for AI-assisted Jira bug triage. It receives a triage trigger, fetches issue context, runs sequential LLM analysis (issue type first, then priority only for Bug), and optionally writes advisory labels/comments back to Jira.

Primary goal: keep triage reliable, observable, and easy to operate while preserving strict quality gates (lint, types, tests).

## Current State (May 2026)

- Main flow is implemented and production-shaped: `POST /triage` and local CLIs are functional.
- Sequential triage contract is implemented: classify `Bug|Story` first; run priority (`P0..P4`) only when classification is `Bug`.
- Observability baseline is in place: structured audit logs, Langfuse tracing hooks, `run_id` correlation.
- Image-context preprocessing for Jira inline description attachments is implemented (feature-flagged).
- Zendesk context baseline is implemented (linked ticket text enrichment), but dedupe and Zendesk-image follow-up work is still open.
- Integration-test expansion and some polish/backlog items remain open in `TODO.md`.

Working tree is currently dirty on `main`, with active edits primarily around Zendesk/Jira fetch and related tests. Do not assume a clean base.

## Non-Negotiable Workflow

Follow Red -> Green -> Refactor.

1. Confirm scope against `TODO.md`.
2. Write/adjust failing tests first for behavior changes.
3. Implement minimum code to pass.
4. Run and fix gates from local `.venv`:
   - `.venv/bin/pytest -m lint`
   - `.venv/bin/mypy .`
   - `.venv/bin/pytest -m unit`
   - For broader confidence: `.venv/bin/pytest -m "unit or integration"` or `./scripts/run_tests.sh all`
5. Keep complexity <= 10 and update docs when behavior changes.

Project rule: do not leave known lint/type/test failures behind, even if discovered outside your immediate edit.

## Architecture Map

- `src/triage_service/api/`
  - HTTP layer, request/response contracts, auth header validation, health/readiness.
- `src/triage_service/core/`
  - Domain orchestration (`triage_handler`), prompt composition, parsing, fallback mapping, settings.
- `src/triage_service/adapters/`
  - External integrations: Jira fetch/search/write, OpenRouter inference, image extraction, Zendesk fetch.
- `src/triage_service/observability/`
  - Audit events/stores, structured logging, Langfuse tracing integration.

Dependency direction is inward: API composes dependencies; core owns behavior; adapters implement boundaries; observability is infrastructure, not business logic.

## Key Files To Read First

- `README.md` - canonical operational runbook and feature flags.
- `TODO.md` - authoritative backlog and done/not-done status.
- `docs/architecture/overview.md` - package boundaries and dependency direction.
- `docs/user_flows/index.md` - flow identifiers for integration coverage.
- `src/triage_service/core/triage_handler.py` - end-to-end sync orchestration entrypoint.
- `src/triage_service/core/settings.py` - env-driven runtime config and feature toggles.
- `src/triage_service/core/issue_text_block.py` - assembled model input context.
- `src/triage_service/adapters/jira_issue_fetcher.py` - Jira normalization, custom fields, linked ids.
- `src/triage_service/adapters/zendesk_ticket_fetcher.py` - Zendesk adapter under active evolution.
- `tests/unit/test_triage_handler.py`, `tests/unit/test_settings.py`, `tests/unit/test_jira_issue_fetcher.py`, `tests/unit/test_zendesk_ticket_fetcher.py` - highest-signal unit coverage for current work.

## Runtime & Commands

Setup:

- `python -m venv .venv`
- `.venv/bin/pip install -e ".[dev]"`

Useful commands:

- `./scripts/run_tests.sh all` - lint + mypy + unit + integration.
- `.venv/bin/python scripts/run_triage_cli.py PROJ-123` - local full triage path.
- `.venv/bin/python scripts/fetch_jira_issue.py PROJ-123` - fetch/normalize smoke.
- `.venv/bin/python scripts/run_bulk_triage_cli.py --jql '...' -o /tmp/out.json` - batch triage report.

## Environment Highlights

Required core credentials:

- `JIRA_API_KEY`
- `OPENROUTER_API_KEY`
- `TRIAGE_WEBHOOK_TOKEN`

Commonly relevant optional flags:

- `TRIAGE_TEXT_MODEL`
- `TRIAGE_ALLOWED_PROJECTS`
- `TRIAGE_IMAGE_CONTEXT_ENABLED`, `TRIAGE_VISION_MODEL`
- `TRIAGE_ZENDESK_CONTEXT_ENABLED`
- `TRIAGE_JIRA_ZENDESK_TICKET_IDS_FIELD_ID`
- `TRIAGE_JIRA_IMPORTED_ZENDESK_TICKET_IDS_FIELD_ID`
- `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`

Reference `.env.example` for the full set.

## Active Backlog Focus

Highest-priority unfinished area in `TODO.md`:

- Zendesk context deduplication:
  - union dedupe across custom-field and body-parsed ids,
  - dedupe repeated cross-ticket narrative blocks,
  - dedupe repeated Jira-vs-Zendesk text.
- Zendesk image follow-up:
  - discover/fetch ticket images,
  - cross-source image dedupe vs Jira attachments,
  - shared attachment budget with existing image preprocessor,
  - observability counters/spans and benchmark stratification.

Keep these additions soft-fail-safe: enrichment failures should not abort triage.

## Guardrails

- Keep sequential logic intact: Story path skips priority inference.
- Keep Jira mutations advisory only (labels/comments), no automatic field mutation.
- Preserve `run_id` propagation for API, logs, and traces.
- Prefer narrow, high-signal tests over brittle mock-heavy coverage.
- Avoid introducing coupling across package boundaries that breaks the architecture direction.
