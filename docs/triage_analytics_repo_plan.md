# TriageBot Analytics Repo — Jump-Start Plan

A standalone build plan for a new repository (`triage-analytics`) that records
what an upstream triage system recommended for Jira issues and exposes the data
to Grafana for all-to-date / month-to-date / year-to-date reporting: promoted /
demoted / Story-routed counts and the derived "engineering hours / money saved"
figures.

This repo is **fully isolated**: it owns its own database, ingest API, and
dashboards. It integrates with the outside world only through a single
documented data contract (§4 — the decision event). No knowledge of the
upstream triage codebase is required to build it.

> **Scope:** decision events only. Tracking what happens to an issue *after*
> triage (human reverts, Story promoted back to Bug, etc.) is explicitly out of
> scope. The dashboard reports on the recommendations TriageBot made, not on
> their downstream fate.

---

## 1. Goals & non-goals

**Goals**

- Persist one authoritative **decision record** per triage run: intake state →
  recommended state, whether the change was auto-applied, confidence, cost.
- Serve that data from a queryable store so **Grafana** renders the dashboards.
  No bespoke charting UI is built.
- Make savings assumptions (minutes-per-urgency-level, $/hour) **tunable**
  without code changes.

**Non-goals**

- No triage / LLM inference logic (that belongs to the upstream system).
- **No outcome tracking** — no Jira changelog reconciliation, no revert
  detection, no "stickiness" metrics.
- No custom front-end. Grafana over the database does all visualization.
- This service does not read from or write to Jira at all.

---

## 2. Architecture

```
upstream triage system
  emits a decision event after
  each completed triage run
        │ decision feed (§4)
        │ (HTTP / log-ship / queue)
        ▼
   ┌──────────────────────────────────────────────┐
   │            triage-analytics container          │
   │   ingest  ·  validation  ·  idempotent persist │
   └───────────────────────┬──────────────────────┘
                           │ writes
                           ▼
                      Postgres
                           │ reads
                           ▼
                      Grafana
          (dashboard JSON versioned in this repo)
```

Single feed, keyed on `run_id`. The decision event is the only source that knows
the *intake* state and the (possibly advisory) recommendation, so it carries
everything the dashboards need.

---

## 3. Repo layout

```
triage-analytics/
├── README.md
├── PLAN.md                      # this document
├── pyproject.toml               # flake8, mypy, pytest markers, max-complexity=10
├── Dockerfile
├── docker-compose.yml           # local: app + postgres + grafana
├── .env.example
├── src/triage_analytics/
│   ├── api/
│   │   └── app.py               # FastAPI: /health, /decisions
│   ├── ingest/
│   │   ├── decision_event.py    # decision record schema (§4)
│   │   └── persistence.py       # DB writer (idempotent upsert)
│   ├── db/
│   │   ├── migrations/          # alembic
│   │   └── models.py            # SQLAlchemy models
│   └── settings.py
├── ops/grafana/
│   ├── provisioning/            # datasource + dashboard provider config
│   └── dashboards/
│       └── triage_value.json    # exported dashboard model (dashboard-as-code)
├── sql/
│   └── views.sql                # derived views (urgency deltas, savings)
└── tests/
    ├── unit/
    ├── integration/
    └── conftest.py
```

Quality gates: `flake8`, `mypy .`, `pytest -m lint|unit|integration`,
`max-complexity=10`, and a `scripts/run_tests.sh` entrypoint.

---

## 4. Decision-event contract (input)

The upstream triage system delivers one event per completed run conforming to
this schema. Treat it as a versioned integration contract (publish it as a JSON
Schema this repo validates against).

```jsonc
{
  "event_type": "triage_completed",
  "run_id": "uuid",                   // unique per triage run; primary idempotency key
  "issue_key": "TJC-123",
  "project": "TJC",
  "source": "bug_created",            // bug_created | priority_changed | manual_trigger
  "intake_issue_type": "Bug",         // issue type before triage
  "intake_priority": "P2",            // priority before triage; null when intake type is Story
  "recommended_issue_type": "Story",
  "recommended_priority": null,       // null when recommended type is Story
  "applied_type_change": false,       // did the upstream executor mutate Jira issue type?
  "applied_priority_change": false,   // did the upstream executor mutate Jira priority?
  "confidence": 0.82,                 // [0.0, 1.0]
  "inference_cost_usd": 0.018,        // optional, when reported by the upstream model
  "reason": "…",
  "occurred_at": "2026-06-02T13:00:00Z"
}
```

| Field | Required | Notes |
| --- | --- | --- |
| `run_id` | yes | Idempotency key; ingest upserts on it. |
| `issue_key`, `project` | yes | Correlation / grouping. |
| `source` | yes | One of `bug_created`, `priority_changed`, `manual_trigger`. |
| `intake_issue_type`, `intake_priority` | yes | The "before" state powering promoted/demoted. `intake_priority` is null when intake type is `Story`. |
| `recommended_issue_type`, `recommended_priority` | yes | The "after" recommendation. `recommended_priority` is null when recommended type is `Story`. |
| `applied_type_change`, `applied_priority_change` | yes | Distinguish advisory recommendations from auto-applied mutations. |
| `confidence` | yes | Advisory metadata only. |
| `inference_cost_usd` | no | For cost-efficiency panels. |
| `occurred_at` | yes | ISO-8601 UTC. |

**Ingestion transport** (analytics-side choice; the producer just emits a
conforming event):

- **A. HTTP push** — producer POSTs to `/decisions` (§7).
- **B. Log-ship** — producer writes the event as a JSON log line; ship via
  Vector/Fluent Bit/Loki and ingest. No synchronous coupling.
- **C. Queue** — producer publishes to SQS/PubSub; this service consumes.

> **External dependency:** producing decision events is the upstream system's
> responsibility, tracked separately. This plan only covers the analytics-side
> ingestion of events that conform to the schema above.

---

## 5. Database schema (Postgres)

```sql
-- One row per triage run (idempotent on run_id).
CREATE TABLE triage_decision (
    run_id                  TEXT PRIMARY KEY,
    issue_key               TEXT NOT NULL,
    project                 TEXT NOT NULL,
    source                  TEXT NOT NULL,
    intake_issue_type       TEXT NOT NULL,
    intake_priority         TEXT,
    recommended_issue_type  TEXT NOT NULL,
    recommended_priority    TEXT,
    applied_type_change     BOOLEAN NOT NULL DEFAULT FALSE,
    applied_priority_change BOOLEAN NOT NULL DEFAULT FALSE,
    confidence              DOUBLE PRECISION,
    inference_cost_usd      NUMERIC(10,5),
    reason                  TEXT,
    occurred_at             TIMESTAMPTZ NOT NULL
);
CREATE INDEX ix_decision_issue ON triage_decision (issue_key);
CREATE INDEX ix_decision_time  ON triage_decision (occurred_at);

-- Tunable assumptions, editable without redeploy (or use Grafana variables).
CREATE TABLE savings_assumption (
    key    TEXT PRIMARY KEY,   -- minutes_per_level_low | minutes_per_level_high | usd_per_hour
    value  NUMERIC NOT NULL
);
```

---

## 6. Urgency scale, movement & savings math

Urgency value per priority (Story / out-of-bug-queue = 0):

| Priority | Urgency value |
| -------- | ------------- |
| P0       | 4             |
| P1       | 3             |
| P2       | 2             |
| P3       | 1             |
| P4       | 0             |
| Story    | 0             |

Per decision: `urgency_delta = urgency(intake) − urgency(recommended)`
(positive = de-escalation). Implement a SQL `urgency(priority, issue_type)`
function and a movement view:

```sql
-- sql/views.sql (sketch; implement urgency() as a real SQL function)
CREATE VIEW v_decision_movement AS
SELECT
    d.*,
    CASE
        WHEN d.recommended_issue_type = 'Story' AND d.intake_issue_type = 'Bug'
            THEN 'bug_to_story'
        WHEN urgency(d.recommended_priority, d.recommended_issue_type)
           < urgency(d.intake_priority, d.intake_issue_type) THEN 'demoted'
        WHEN urgency(d.recommended_priority, d.recommended_issue_type)
           > urgency(d.intake_priority, d.intake_issue_type) THEN 'promoted'
        ELSE 'unchanged'
    END AS movement,
    (urgency(d.intake_priority, d.intake_issue_type)
       - urgency(d.recommended_priority, d.recommended_issue_type)) AS urgency_delta
FROM triage_decision d;
```

**Savings**: `hours_saved = Σ(positive urgency_delta) × minutes_per_level / 60`,
`dollars_saved = hours_saved × usd_per_hour`. Keep `minutes_per_level_low/high`
and `usd_per_hour` as Grafana variables (or `savings_assumption` rows) so the
figures stay tunable — the $/hour rate is an org-specific placeholder, not a
fixed constant.

---

## 7. Ingest API

Small FastAPI app with token auth (header `X-Analytics-Token`):

- `GET /health` — liveness + DB readiness.
- `POST /decisions` — decision events (§4), **only if** using transport `A`.
  Omit if ingesting via logs (`B`) or a queue (`C`).

Reject unknown projects (project allowlist) and return `401` on token mismatch.

---

## 8. Grafana (dashboard-as-code)

- Provision the **Postgres datasource** and a **dashboard provider** via
  `ops/grafana/provisioning/`.
- Author panels in the Grafana UI, then **export the dashboard JSON** to
  `ops/grafana/dashboards/triage_value.json` and commit it. No UI is built — the
  metric definitions are versioned for review and disaster recovery.
- Panels (each with a Grafana time-range variable for ATD / MTD / YTD):
  - Issues reviewed, recommended-change rate, left-unchanged rate.
  - Promoted / demoted / Bug→Story counts (stacked over time).
  - Net urgency levels removed (Σ urgency_delta).
  - Engineering hours protected (low/high) and illustrative $ saved — driven by
    `minutes_per_level_*` and `usd_per_hour` variables.
  - Inference cost vs hours protected (cost-efficiency).
  - Advisory vs auto-applied breakdown (`applied_*` flags).
- Use `$__timeFilter(occurred_at)` so one query serves every time window.

---

## 9. Tech stack

- **Python 3.12 + FastAPI**.
- **Postgres** for records; **Alembic** migrations; **SQLAlchemy** models.
- **Grafana** for dashboards.
- **docker-compose** for local (app + postgres + grafana); **Dockerfile** for
  the container.
- Tooling: `flake8`, `mypy`, `pytest` (`lint`/`unit`/`integration` markers),
  `max-complexity=10`.

---

## 10. Phased roadmap

**Phase 1 — Persistence skeleton**
- [ ] Repo scaffold, tooling gates, `docker-compose` (postgres + grafana).
- [ ] Alembic migrations for `triage_decision` and `savings_assumption`.
- [ ] `GET /health` (liveness + DB readiness).

**Phase 2 — Decision feed (§4)**
- [ ] Publish the decision-event JSON Schema in-repo as the integration contract.
- [ ] Choose ingestion transport (A/B/C) and implement it.
- [ ] Idempotent upsert on `run_id`; unit + integration tests.

**Phase 3 — Views & Grafana**
- [ ] `urgency()` function + `v_decision_movement` view.
- [ ] Grafana datasource + dashboard provisioning; export `triage_value.json`.
- [ ] Sanity-check ATD/MTD/YTD numbers against a known sample.

**Phase 4 — Hardening**
- [ ] Backfill historical decisions (replay events / one-off batch).
- [ ] Alerting on ingest failures.

---

## 11. Open decisions

1. **Transport** for the decision feed: log-ship (B) vs HTTP push (A) vs queue
   (C). B avoids synchronous coupling to the producer.
2. **Backfill**: replay historical decision events, or seed from a one-off batch,
   before go-live.
3. **Savings assumptions home**: `savings_assumption` table vs Grafana variables
   (variables simpler; table auditable). Both is fine.
4. **Advisory vs applied reporting**: report *recommended* movement, *applied*
   movement, or both side by side.
