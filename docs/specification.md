# Project: jira-triage-dashboard

## Overview

- Two-part system: **(1)** `jira-triage` gains a single fire-and-forget POST after each triage run, **(2)** `jira-triage-dashboard` is a new small FastAPI service that receives decision events, persists them to Postgres, and exposes the data to Grafana.
- **Audience:** platform engineers and triage stakeholders reviewing TriageBot value.
- Follows the `flux-slack-notifier` / `awx-slack-notifier` pattern: standalone FastAPI app under `apps/`, Dockerfile under `build/docker/`, image entry in `build/images.yaml`, K8s manifests under `kubernetes/infra/`.

## Scope

### `jira-triage-dashboard` (this app — new)

- FastAPI service with a single `POST /decisions` endpoint (token auth) and `GET /healthz`.
- In-process Postgres persistence via `psycopg` — `CREATE TABLE IF NOT EXISTS` at startup (same pattern as `bc-support-agents`; no Alembic).
- In-cluster Postgres Deployment + PVC in the same namespace.
- SQL views for urgency movement and savings math.
- Grafana dashboard-as-code.
- CSV seed CLI for historical backfill.
- Local dev via `docker-compose` (app + Postgres + Grafana).

### `jira-triage` (existing — minimal change)

- After each successful `triage_completed` audit event, fire-and-forget HTTP POST of the decision payload to the dashboard service. Failures are logged and swallowed — triage never blocks on analytics.
- Adds `intake_issue_type` and `intake_priority` to the `telemetry` dict on `TriageCompletedAuditEvent` so the POST payload carries the full before/after state.
- This work happens in the external jira-triage repo and is dropped here.

## Out-of-scope

- Outcome tracking after triage (Jira changelog reconciliation, revert detection, stickiness).
- Custom charting UI — Grafana only.
- Log-ship or queue ingestion.

## Architecture

```
Jira Automation webhook
        │
        ▼
   jira-triage handler
   TriageHandler.run()
        │
        ├──▶ StructuredLoggerAuditStore  (existing — JSON logs)
        ├──▶ LangfuseAuditStore          (existing — inference tracing)
        └──▶ HTTP POST (fire-and-forget)
                    │
                    ▼
        ┌───────────────────────────────┐
        │   jira-triage-dashboard       │
        │   POST /decisions             │
        │   validate → upsert           │
        └───────────┬───────────────────┘
                    │ writes
                    ▼
               Postgres (in-cluster)
                    │ reads (direct SQL)
                    ▼
               Grafana (existing platform stack)
```

## User scenarios

1. **Automatic persistence:** Every completed triage run POSTs a decision event to the dashboard service. The POST is fire-and-forget; failures are logged and swallowed.
2. **Idempotent ingest:** Duplicate `run_id` replays upsert the same row; unknown project returns 400; bad token returns 401.
3. **Ops health check:** Kubernetes probes `GET /healthz` for liveness and DB readiness.
4. **Grafana review:** Engineer opens the provisioned dashboard, selects ATD/MTD/YTD via time picker, and reads promoted/demoted/Bug→Story counts and savings panels.
5. **Tune assumptions:** Engineer adjusts `minutes_per_level_*` and `usd_per_hour` via Grafana dashboard variables without redeploying code.
6. **CSV backfill:** Operator runs a one-time script against a CSV export to seed historical rows. Idempotent and safe to re-run.

## `jira-triage-dashboard` — app design

### HTTP endpoints

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/healthz` | none | Liveness + DB ping |
| `POST` | `/decisions` | `X-Analytics-Token` | Accept one decision event |

All routes under `/api/v1` prefix (matches `flux-slack-notifier` convention).

### App structure (mirrors `flux-slack-notifier`)

```
apps/jira-triage-dashboard/
├── api/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py               # FastAPI wiring, JSON logging, router mount
│   │   ├── config.py             # pydantic-settings BaseSettings
│   │   ├── routes.py             # GET /healthz, POST /decisions
│   │   └── services/
│   │       ├── __init__.py
│   │       └── persistence.py    # CREATE TABLE IF NOT EXISTS, upsert logic
│   ├── tests/
│   │   ├── test_routes.py
│   │   └── test_persistence.py
│   ├── pyproject.toml
│   ├── uv.lock
│   └── .env.example
├── sql/
│   └── views.sql                 # urgency() + v_decision_movement (applied manually)
├── seed/
│   └── seed.py                   # CSV backfill CLI
├── ops/grafana/
│   ├── provisioning/             # datasource + dashboard provider (local dev)
│   └── dashboards/triage_value.json
├── docker-compose.yml            # app + postgres + grafana (local dev)
├── README.md
└── specification.md
```

### `POST /decisions` contract

```json
{
  "event_type": "triage_completed",
  "run_id": "uuid",
  "issue_key": "TJC-123",
  "project": "TJC",
  "source": "bug_created",
  "intake_issue_type": "Bug",
  "intake_priority": "P2",
  "recommended_issue_type": "Story",
  "recommended_priority": null,
  "applied_type_change": false,
  "applied_priority_change": false,
  "confidence": 0.82,
  "inference_cost_usd": 0.018,
  "reason": "...",
  "occurred_at": "2026-06-02T13:00:00Z"
}
```

| Field | Required | Notes |
| --- | --- | --- |
| `run_id` | yes | Idempotency key (PK) |
| `issue_key`, `project` | yes | Grouping / filtering |
| `source` | yes | `bug_created` \| `priority_changed` \| `manual_trigger` \| `backfill` |
| `intake_issue_type`, `intake_priority` | yes | Before state; `intake_priority` null when intake is Story |
| `recommended_issue_type`, `recommended_priority` | yes | After recommendation; `recommended_priority` null when Story |
| `applied_type_change`, `applied_priority_change` | yes | Distinguish advisory vs auto-applied |
| `confidence` | yes | 0.0–1.0 |
| `inference_cost_usd` | no | Cost-efficiency panels |
| `occurred_at` | yes | ISO-8601 UTC |

**Responses:** `201` new row, `200` upsert replay, `400` validation/allowlist, `401` token mismatch, `500` persistence failure.

### Database

**Schema management:** `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS` at app startup in `services/persistence.py` — same pattern as `bc-support-agents`. No Alembic. Schema evolution via idempotent `ALTER TABLE` if needed later.

**Driver:** Raw `psycopg` (psycopg3), one connection per write, `autocommit=True`. No SQLAlchemy, no connection pool. Matches `bc-support-agents` exactly.

**Table:** `triage_decision`

| Column | Type | Source |
| --- | --- | --- |
| `run_id` | `TEXT PK` | request body |
| `issue_key` | `TEXT NOT NULL` | request body |
| `project` | `TEXT NOT NULL` | request body |
| `source` | `TEXT NOT NULL` | request body |
| `intake_issue_type` | `TEXT NOT NULL` | request body |
| `intake_priority` | `TEXT` | request body (null when intake is Story) |
| `recommended_issue_type` | `TEXT NOT NULL` | request body |
| `recommended_priority` | `TEXT` | request body (null when Story) |
| `applied_type_change` | `BOOLEAN NOT NULL DEFAULT FALSE` | request body |
| `applied_priority_change` | `BOOLEAN NOT NULL DEFAULT FALSE` | request body |
| `confidence` | `DOUBLE PRECISION` | request body |
| `inference_cost_usd` | `NUMERIC(10,5)` | request body (optional) |
| `reason` | `TEXT` | request body |
| `occurred_at` | `TIMESTAMPTZ NOT NULL` | request body |

Indexes: `(issue_key)`, `(occurred_at)`.

**Postgres hosting:** In-cluster Deployment + PVC in the dashboard's namespace, following `bc-support-agents` templates (`postgres-deployment.yaml`, `postgres-pvc.yaml`, `postgres-service.yaml`, `postgres-secret.yaml`). Auto-generated credentials, `DATABASE_URL` composed in the Secret.

### SQL views

`sql/views.sql` — applied manually once against the database:

- `urgency(priority, issue_type)` function — P0=4, P1=3, P2=2, P3=1, P4=0, Story=0.
- `v_decision_movement` view — labels each row as `bug_to_story`, `demoted`, `promoted`, or `unchanged`.
- Savings: `hours_saved = Σ(positive urgency_delta) × minutes_per_level / 60`; `dollars_saved = hours_saved × usd_per_hour` (calculated in Grafana queries using dashboard variables).

### Config (`api/.env.example`)

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Postgres DSN |
| `ANALYTICS_TOKEN` | Expected `X-Analytics-Token` value |
| `ALLOWED_PROJECTS` | Comma-separated project allowlist |
| `LOG_LEVEL` | Default `INFO` |
| `APP_ENV` | Environment label (test/uat/prod) |

### Backfill (CSV seed)

Standalone script `seed/seed.py`:

- Reads CSV (columns: ticket id, triage date, initial priority, triaged priority).
- Maps each row to a `triage_decision` upsert:
  - `run_id` = deterministic UUID from `sha256(issue_key + occurred_at)`.
  - `project` = prefix of ticket id (e.g. `TJC` from `TJC-123`).
  - `source` = `"backfill"`.
  - `intake_issue_type` = `"Bug"`.
  - `recommended_issue_type` = `"Story"` if triaged priority is "Story", else `"Bug"`.
  - `applied_*` = `false`; `confidence` = `1.0`.
- Batch `INSERT … ON CONFLICT (run_id) DO UPDATE`. Idempotent.
- Invoked as: `uv run python seed/seed.py --csv /path/to/export.csv --database-url $DATABASE_URL`.
- Not wired into container startup — manual one-time operation.

## Platform wiring

### Dockerfile (`build/docker/jira-triage-dashboard/Dockerfile`)

Same shape as `flux-slack-notifier`:

```dockerfile
FROM python:3.13-slim
# ... uv install, COPY pyproject.toml + uv.lock, uv sync --frozen --no-dev,
# COPY app source, non-root user, EXPOSE 8080,
# CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

### `build/images.yaml`

```yaml
  - name: jira-triage-dashboard
    ecr_repository: internal-ai/jira-triage-dashboard
    dockerfile: build/docker/jira-triage-dashboard/Dockerfile
    watch_paths:
      - build/docker/jira-triage-dashboard/**
      - apps/jira-triage-dashboard/**
    tag_latest: true
```

### Root `.dockerignore`

Add `!apps/jira-triage-dashboard/**`.

### K8s manifests (`kubernetes/infra/jira-triage-dashboard/`)

```
kubernetes/infra/jira-triage-dashboard/
├── kustomization.yaml
├── namespace.yaml              # triage-dashboard namespace
├── configmap.yaml              # APP_ENV, LOG_LEVEL, ALLOWED_PROJECTS
├── external-secret.yaml        # ANALYTICS_TOKEN from SSM
├── deployment.yaml             # app container + envFrom configmap/secret + postgres secret
├── service.yaml                # ClusterIP, port 80 → 8080
├── postgres-deployment.yaml    # Postgres 16 Alpine, PVC mount
├── postgres-pvc.yaml           # 5Gi, ReadWriteOnce
├── postgres-service.yaml       # ClusterIP, port 5432
└── postgres-secret.yaml        # auto-generated credentials + DATABASE_URL
```

Wire into overlays: add `- ../../jira-triage-dashboard` to each `kubernetes/infra/overlays/{test,uat,prod}/kustomization.yaml`.

## Changes to `jira-triage` (external repo)

Minimal — developed and tested in the external jira-triage repo, then dropped into `apps/jira-triage/`:

1. **Telemetry enrichment:** Add `intake_issue_type` and `intake_priority` to the `telemetry` dict on `TriageCompletedAuditEvent` in the handler (alongside existing `priority_signal`, `would_auto_apply_*`).
2. **Analytics HTTP client:** After recording `triage_completed`, POST the decision payload to `ANALYTICS_DASHBOARD_URL` (new env var). Fire-and-forget: `httpx.AsyncClient` with short timeout, catch all exceptions, log at `warning`. When `ANALYTICS_DASHBOARD_URL` is unset, skip entirely.
3. **Payload construction:** Build the POST body from the `TriageCompletedAuditEvent` fields + `telemetry` dict (intake state, applied flags, inference cost).

No new dependencies — `httpx` is already in `jira-triage`'s deps.

### New environment variable for `jira-triage`

| Variable | Purpose |
| --- | --- |
| `ANALYTICS_DASHBOARD_URL` | Base URL of the dashboard service (e.g. `http://jira-triage-dashboard.triage-dashboard/api/v1`). When unset, analytics POST is disabled. |
| `ANALYTICS_TOKEN` | Token sent as `X-Analytics-Token` header. |

These get added to `kubernetes/infra/jira-triage/configmap.yaml` and `external-secret.yaml`.

## Testing strategy

### Dashboard app (this repo, `apps/jira-triage-dashboard/api/tests/`)

| Marker | Coverage |
| --- | --- |
| `lint` | `flake8` (max-complexity=10), `mypy .` |
| `unit` | Decision schema validation, project allowlist, token auth, healthz, upsert row mapping |
| `integration` | App + Postgres (Testcontainers or docker-compose): upsert idempotency, healthz with/without DB, table bootstrap idempotency |

Run locally: `uv run --directory apps/jira-triage-dashboard/api --extra dev pytest tests/ -v`

### Seed script

| Marker | Coverage |
| --- | --- |
| `unit` | CSV row mapping (deterministic `run_id`, priority → issue type inference) |
| `integration` | Seed idempotency against Postgres, SQL views produce correct movement labels |

### Analytics client (external jira-triage repo)

| Marker | Coverage |
| --- | --- |
| `unit` | Client disabled when URL unset, payload construction from event + telemetry, fire-and-forget error swallowing |
| `integration` | POST against test server, timeout handling |

## Risks / Constraints

- Savings figures are **illustrative** — driven by tunable Grafana variables, not audited financial data.
- Dashboard reports **recommendations made**, not downstream human overrides.
- In-cluster Postgres has no HA/replication. Acceptable for analytics; data can be rebuilt from CSV + future events.
- CSV backfill rows lack `applied_*` flags — panels show them as `applied=false`, `confidence=1.0`.
- Fire-and-forget POST means triage runs during dashboard downtime produce no analytics row. Structured logs retain the full audit trail as a recovery source.
- HTTP hop adds ~1-5ms latency to each triage run (non-blocking, so no user impact).

## Assumptions

- `jira-triage` already has `httpx` — no new dependencies for the analytics client.
- `ANALYTICS_DASHBOARD_URL` is the opt-in knob for `jira-triage`. When unset, behavior is unchanged.
- Intake state enrichment via `telemetry` dict — no `TriageCompletedAuditEvent` model changes.
- Production Grafana is the **existing platform stack**. Local dev docker-compose includes a Grafana container.
- Savings assumptions live in **Grafana dashboard variables** (`$minutes_per_level_low`, `$minutes_per_level_high`, `$usd_per_hour`); no database table.
- Python >= 3.11, psycopg3, no SQLAlchemy, no Alembic.

### Development workflow

- **Dashboard app** (`jira-triage-dashboard`): developed and tested entirely in this platform repo.
- **Analytics client** (the POST call in `jira-triage`): developed and tested in the external jira-triage repo, then dropped into `apps/jira-triage/` with platform-specific config adjustments (env vars in configmap/secret).

## Resolved questions

1. **Backfill:** CSV seed script, idempotent via deterministic `run_id`. Source: existing spreadsheet.
2. **Savings assumptions:** Grafana dashboard variables only.
3. **Production Postgres:** In-cluster Deployment + PVC (matches `bc-support-agents`).
4. **Grafana hosting:** Reuse existing platform Grafana stack.
5. **Schema management:** `CREATE TABLE IF NOT EXISTS` at app startup (matches `bc-support-agents`). No Alembic.
6. **Intake state enrichment:** `telemetry` dict (no model changes).
7. **Architecture:** Separate small service (matches `flux-slack-notifier` pattern). `jira-triage` gains a fire-and-forget HTTP POST.

## Open questions

1. **Grafana provisioning method:** ConfigMap-mounted provisioning files vs Grafana API/Terraform for the production instance.

## MVP phases

### External repo (jira-triage source)

1. **Telemetry enrichment** — add `intake_issue_type` / `intake_priority` to `telemetry` dict.
2. **Analytics HTTP client** — fire-and-forget POST to dashboard service, gated on `ANALYTICS_DASHBOARD_URL`.

### Platform repo (this repo)

3. **App scaffold** — `apps/jira-triage-dashboard/api/` with FastAPI, config, healthz, structured logging, pyproject.toml, `.env.example`.
4. **POST /decisions** — request validation, project allowlist, token auth, Postgres upsert, unit + integration tests.
5. **Postgres infra** — K8s manifests in `kubernetes/infra/jira-triage-dashboard/` (Deployment + PVC + Service + Secret for Postgres, app Deployment + Service + ConfigMap + ExternalSecret).
6. **Docker + CI** — Dockerfile, `build/images.yaml` entry, `.dockerignore` allowlist.
7. **SQL views + CSV seed** — `sql/views.sql`, `seed/seed.py`, tests.
8. **Grafana dashboard** — provision datasource, build panels, export `triage_value.json`.
9. **Wire jira-triage** — drop analytics client code, add `ANALYTICS_DASHBOARD_URL` + `ANALYTICS_TOKEN` to jira-triage configmap/secret.
10. **Deploy** — deploy dashboard service, run CSV seed, verify Grafana, enable analytics POST in jira-triage.
