# Jira Automation callback delivery (Rule B)

Operational runbook for `TRIAGE_JIRA_APPLY_MODE=automation_webhook`, where the triage service
stops writing to Jira directly and instead POSTs a fully-rendered outcome payload to a Jira
Automation incoming-webhook rule ("Rule B") that applies labels, the comment, and any field
edits as the Automation actor.

**Design rule: the service owns every decision and all comment copy; Rule B is a dumb applier.**
The payload carries the final comment text and explicit action directives. The rule branches on
`{{webhookData.actions.*}}` / `{{webhookData.comment.post}}` and applies **static** label values
inside those branches — it never sets labels from smart values and never re-derives a decision.

## Why

- **Loop control.** Rule B's edits run as the Automation actor, so a priority-change trigger
  rule can exclude the Automation user and not re-fire on TriageBot's own writes. This is the
  precondition for re-triage on priority change.
- **Permissions.** Screen/field restrictions that block the service account (for example the
  CLOSM `labels`-not-on-screen `HTTP 400`) are applied by Automation instead, which is not
  bound by the edit-screen configuration.

## Flow

```
Trigger rule (scheduled JQL / issue created / priority changed)
  └─ POST /triage  (no wait for response)
       └─ triage service: fetch → classify → priority → render outcome
            └─ POST outcome payload  ──▶  Rule B (incoming webhook)
                                             └─ labels / comment / field edits
```

## Configuration

| Variable | Meaning |
|----------|---------|
| `TRIAGE_JIRA_APPLY_MODE` | `direct` (default, rollback path) or `automation_webhook` |
| `JIRA_AUTOMATION_WEBHOOK_URL` | Fallback Rule B incoming-webhook URL. Required in webhook mode unless every caller sends `jira_automation_webhook_url`, which takes precedence. |
| `JIRA_AUTOMATION_WEBHOOK_TOKEN` | Fallback secret for Rule B's incoming-webhook trigger; sent as `X-Automation-Webhook-Token`. Jira validates this header — do not re-check it in a rule condition. A per-request `X-Jira-Automation-Webhook-Token` inbound header takes precedence. |
| `JIRA_AUTOMATION_WEBHOOK_TIMEOUT_SECONDS` | Callback POST timeout (default `30`); the webhook is not retried |

The callback POST is **not retried**. `TRIAGE_JIRA_HTTP_MAX_RETRIES` applies to Jira REST
fetch/write only. Replaying the same webhook after a timeout or 5xx can run Rule B twice
and duplicate comments; a failed attempt is a delivery failure so the scheduled scan can
retry later with a new `run_id`. Webhook mode needs no Jira write credentials;
`JIRA_CLOUD_ID` / `JIRA_USER_EMAIL` are still required for issue **fetch**.
`build_default_triage_handler(apply_to_jira=False)` still returns the no-op executor, so
read-only CLI runs are unaffected by the mode.

## Hybrid migration: choose callback delivery in Rule A

Keep the service-wide default on direct writes while migrating:

```bash
TRIAGE_JIRA_APPLY_MODE=direct
JIRA_AUTOMATION_WEBHOOK_URL=https://api-private.atlassian.com/automation/webhooks/jira/...
JIRA_AUTOMATION_WEBHOOK_TOKEN=...
```

In each Rule A that should use Rule B, add the request-level override to its custom
`POST /triage` body, plus a second header besides `X-Triage-Token`:

- Header `X-Jira-Automation-Webhook-Token`: this project's Rule B secret.

```json
{
  "issue_key": "{{issue.key}}",
  "project": "{{issue.project.key}}",
  "source": "bug_created",
  "jira_apply_mode": "automation_webhook",
  "jira_automation_webhook_url": "{{webhookUrl of this project's Rule B}}"
}
```

### Per-project Rule B endpoints

Automation rules are project-scoped, so each project has its own Rule B with its own webhook URL
and secret. Rather than tracking every pair in the service environment, each Rule A carries its
own: `jira_automation_webhook_url` in the JSON body and `X-Jira-Automation-Webhook-Token` on the
request override `JIRA_AUTOMATION_WEBHOOK_URL` and `JIRA_AUTOMATION_WEBHOOK_TOKEN` for that one
callback, and the env values remain the fallback for callers that send neither.

Guardrails and limits:

- The URL must be `https` on `api-private.atlassian.com`. Anything else is rejected with `422`
  before triage runs, so a bad or hostile payload cannot aim the
  callback POST at an arbitrary host. Widen `ALLOWED_WEBHOOK_HOSTS` in
  `adapters/automation_webhook_executor.py` if Atlassian changes hosts.
- The env URL is *not* host-checked; operator configuration stays trusted, which keeps tunnels and
  local stubs usable for smoke runs.
- The token is a header, not a JSON field, so `TRIAGE_DEBUG_INBOUND` body logging cannot print it.
  It is never echoed in the response, structured logs, or audit events. A JSON body field with the
  old name is ignored.
- Both values live in the Rule A definition, so anyone who can edit that rule can read them. That
  is usually the same set of Jira admins who can read Rule B's trigger, so it does not widen access
  much — but it is not a vault.
- Because the URL can now arrive per request, webhook mode no longer requires
  `JIRA_AUTOMATION_WEBHOOK_URL` at startup. A request that selects webhook mode with no URL from
  either source fails that request before any model spend.

That request gets a callback executor; concurrent and later requests are unaffected. Existing
Rule A payloads that omit `jira_apply_mode` continue using the env default (`direct`). The only
accepted values are `direct` and `automation_webhook`; any other value returns HTTP `422`.

This is safe under concurrency because the service builds a separate handler/executor for each
request. It does not change `os.environ`. If a request selects `automation_webhook` with no
callback URL from either the payload or `JIRA_AUTOMATION_WEBHOOK_URL`, handler construction fails
before Jira fetch or model inference.

## Run one callback from the CLI

Put the Rule B credentials in `.env` (or export them in the shell):

```bash
JIRA_AUTOMATION_WEBHOOK_URL=https://api-private.atlassian.com/automation/webhooks/jira/...
JIRA_AUTOMATION_WEBHOOK_TOKEN=...
```

Then run:

```bash
.venv/bin/python scripts/run_webhook_triage_cli.py TJC-123
```

The command forces `TRIAGE_JIRA_APPLY_MODE=automation_webhook` for that invocation, runs the
normal fetch and sequential inference pipeline with `source=manual_trigger`, builds the
versioned payload below, POSTs it to Rule B, and prints the triage result as JSON. It never
accepts the webhook token as a command-line argument, avoiding exposure in shell history and
the process list.

This CLI always enables mutation directives (`--auto-apply-deescalation`,
`--auto-apply-escalation`, `--auto-apply-bug-to-story`). Only the directive that matches the
recommendation is emitted in the payload; the others stay false/null. Use `--read-only` to skip
the callback POST entirely. The regular `scripts/run_triage_cli.py` path stays advisory-by-default.

## Payload contract (`payload_version: 1`)

```json
{
  "payload_version": 1,
  "issues": ["TJC-123"],
  "run_id": "7f1c…",
  "issue_key": "TJC-123",
  "project": "TJC",
  "source": "bug_created",
  "recommendation": {"issue_type": "Bug", "priority": "P1", "confidence": 0.82},
  "labels": ["triagebot-reviewed", "triagebot-priority-mismatch"],
  "comment": {"post": true, "body": "[~accountid:5f3…] - This is an informational message…"},
  "actions": {"apply_bug_to_story": false, "apply_priority": {"from": "P3", "to": "P1"}}
}
```

Field notes:

- `issues` is Jira Automation's work-item binding for the trigger option **Issues provided in
  the webhook HTTP POST body**. Always a one-element list of the issue key. `issue_key` is
  duplicated so Rule B can still log/compare `{{webhookData.issue_key}}`.
- `labels` is informational (audit/debug and the direct path's source of truth). Rule B does
  **not** consume it — labels are static values in the rule's branches, so the rule stays
  readable in the Automation UI.
- `comment.post` is `false` (and `comment.body` `null`) when the recommendation matches Jira
  state or `post_mismatch_comments` is disabled.
- `comment.body` is plain text, not ADF: the reporter mention is rendered as
  `[~accountid:...]`, which Jira Automation's comment action expands. The direct path uses a
  structured ADF `mention` node instead — same copy, different mention encoding.
- `actions.apply_priority` is `null` unless the matching `TRIAGE_AUTO_APPLY_*` flag is on;
  `from` is the intake priority (useful for a rule-side sanity check that Jira has not moved
  since triage started).
- The comment body already reflects the directives: when any action is present, the body uses
  the "applied" copy, because Rule B performs those edits in the same run.
- Bump `payload_version` on any shape change and update Rule B before rolling out.

## Building Rule B

1. **Trigger:** *Incoming webhook*. Choose **Issues provided in the webhook HTTP POST body**.
   Paste the webhook URL into `JIRA_AUTOMATION_WEBHOOK_URL` and the trigger's secret into
   `JIRA_AUTOMATION_WEBHOOK_TOKEN`. Jira already requires `X-Automation-Webhook-Token` on the
   POST (same header the service sends) — a rule-side header compare is redundant and
   `{{webhookRequest.headers.*}}` is not a reliable smart value.
2. **Version guard:** `{{smart values}} condition` — first value
   `{{webhookData.payload_version}}`, **equals**, second value `1`. Fail loudly rather than
   misapplying an unknown shape.
3. **Audit-log correlation (keep permanently):** add the built-in *Log action*
   (*Add component → Action → Log action*; it writes a message to the rule's audit log — see
   [Jira automation actions](https://support.atlassian.com/cloud-automation/docs/jira-automation-actions/))
   with `{{webhookData.run_id}} {{issue.key}}`, so each rule execution can be correlated with
   the service's traces by `run_id`.
4. **Reviewed marker:** *Edit issue* → Labels → Add → `triagebot-reviewed` (static,
   unconditional). This is the dedupe marker the scheduled-scan JQL excludes on — every
   delivered outcome must set it or the issue re-matches and re-spends inference.
5. **Priority branch:** *If: all conditions match* —
   - `{{smart values}} condition`: `{{webhookData.actions.apply_priority.to}}` **exactly
     matches regular expression** `P[0-4]` (the condition UI has no "is not empty" operator; an
     absent `apply_priority` resolves to empty and fails the regex, which also rejects a
     malformed value), and
   - `{{smart values}} condition`: `{{webhookData.actions.apply_priority.to}}` **does not
     equal** `{{issue.fields.priority.name}}` (skip if Jira already moved to the recommended
     priority while triage was running).

   Then, inside that branch:
   - An *If/else block* with one branch per priority: condition
     `{{webhookData.actions.apply_priority.to}}` **equals** `P0` → *Edit work item* → Priority →
     pick **P0 from the dropdown**. Add *Else-if* branches for `P1`, `P2`, `P3`, `P4`.
   - *Edit work item* → Labels → Add → `triagebot-priority-mismatch` (static).

   **Do not put the smart value in the Priority field.** Jira resolves it to a bare name/id
   string and rejects the edit with `The priority selected is invalid. (priority)` even when the
   value is a real priority in the scheme — picking the literal option stores the priority's id,
   which always validates ([community
   thread](https://community.atlassian.com/forums/Jira-questions/Automation-copy-value-from-a-custom-field/qaq-p/2250234)).
   The per-priority branches also fail safe: a value that has no branch simply writes nothing
   instead of writing the wrong priority. (Alternative if you dislike five branches: *More
   options → Additional fields* with `{"fields": {"priority": {"id": "<id>"}}}`, but the ids are
   site-specific.)
6. **Story branch:** *If* — `{{smart values}} condition`:
   `{{webhookData.actions.apply_bug_to_story}}` **equals** `true` → *Edit issue* → Issue
   type = `Story`, and *Edit issue* → Labels → Add → `triagebot-likely-story` (static).
7. **Comment:** *If* — `{{smart values}} condition`: `{{webhookData.comment.post}}` **equals**
   `true` → *Add internal comment* with body `{{webhookData.comment.body}}`.
8. **Clear in-flight marker:** *Edit issue* → Labels → Remove → `triagebot-scheduled` (static,
   unconditional, last step). The trigger rule adds this label when it POSTs to `/triage`, so
   in-flight issues drop out of the scheduled-scan JQL immediately instead of waiting for the
   async `triagebot-reviewed`.

Order matters: field edits **before** the comment (so the comment lands after the change in the
issue history), and the `triagebot-scheduled` removal last, once everything else applied.

Trigger-rule counterpart (Rule A): add `triagebot-scheduled` in the same rule execution that
sends the `POST /triage` web request, and extend the scan JQL to
`labels not in (triagebot-reviewed, triagebot-scheduled)`.

Note the label-decision trade-off: the service still adds `triagebot-priority-mismatch` /
`triagebot-likely-story` on **advisory-only** runs in `direct` mode, but on this path the
mismatch labels ride the apply branches — a run with the `TRIAGE_AUTO_APPLY_*` flags off
produces only `triagebot-reviewed` plus the comment. Accepted deliberately to keep Rule B free
of smart-value label plumbing.

Export the finished rule (Automation → rule → *Export*) and commit the JSON next to this file
once Rule B exists, so rule drift is reviewable.

## Smoke checklist

1. Point the service at Rule B (`TRIAGE_JIRA_APPLY_MODE=automation_webhook`,
   `JIRA_AUTOMATION_WEBHOOK_URL`, `JIRA_AUTOMATION_WEBHOOK_TOKEN`) and restart — or send the URL
   and token per request instead.
2. Trigger one manual triage (`POST /triage` with `"source": "manual_trigger"`, or
   `scripts/run_triage_cli.py PROJ-123`).
3. Service side: confirm one `outcome_delivered` audit event with `delivered: true`,
   `http_status: 200`, and the run's `run_id`.
4. Jira side: confirm `triagebot-reviewed` landed and `triagebot-scheduled` was removed; on a
   run with a directed edit, confirm the priority/type changed and the matching static label
   (`triagebot-priority-mismatch` / `triagebot-likely-story`) was added; confirm the comment
   posted with the reporter mention rendered as a real mention.
5. Confirm the priority-change trigger rule did **not** re-fire from Rule B's own edit (check
   its audit log for the same timestamp). If it did, exclude the Automation actor in that
   rule's condition.
6. Rollback check: set `TRIAGE_JIRA_APPLY_MODE=direct`, restart, and re-run — the direct
   executor path must still work.

## Diagnosing apply failures

`outcome_delivered` splits the two failure domains that used to look identical:

| Symptom | Where to look |
|---------|---------------|
| No `triage_completed` event | Triage itself failed — service logs / Langfuse trace by `run_id`. Nothing was sent. |
| `outcome_delivered` with `delivered: false` | Callback POST failed (HTTP status and `failure` are on the event). Triage succeeded; nothing reached Jira. The request raised `AutomationCallbackError`, so the API returned 500 and the scheduled-scan JQL will retry. |
| `outcome_delivered` with `delivered: true` but no Jira change | Rule B failed. Open Automation → rule → *Audit log*, find the execution, and match on the logged `run_id`. |

Rule B audit log entries are the only record of the applied result: the service no longer
observes the write.

## Known caveats

- **Applied flags mean "apply directed".** `TriageActionAppliedFlags` — and therefore the
  `applied_type_change` / `applied_priority_change` columns on analytics decision rows — record
  what the payload asked Jira Automation to do, not a confirmed write. Reconcile against Jira
  if you need ground truth.
- **Dedupe marker latency — solved by `triagebot-scheduled`, with a new failure mode.** The
  trigger rule's `triagebot-scheduled` label removes the window where the scan JQL could
  re-match an in-flight issue. The flip side: if triage fails or the callback never lands, the
  label is never removed and the issue silently drops out of the scan forever. Sweep for stale
  markers (e.g. `labels = triagebot-scheduled AND labels not in (triagebot-reviewed) AND
  created <= -1h`) and remove the label to re-queue.
- **Callback POST is not retried.** A timeout after Jira accepted the webhook can still mean
  Rule B ran while the service recorded `delivered: false`. The service will not POST again
  for that `run_id`. A scheduled-scan retry starts a new `run_id` and is a new apply.
- **A failed edit does not stop the rule.** Automation logs the failed *Edit work item* and
  continues, so a rejected priority write still adds `triagebot-priority-mismatch` and still
  posts the comment — and that comment uses the "applied" copy ("The ticket Priority was changed
  from … to …") because the service rendered it from the directive, not from the result. When
  triaging a complaint about a wrong comment, check the rule audit log for an edit error before
  assuming the service rendered the wrong copy.
- **Priority vocabulary is hardcoded in Rule B.** The per-priority branches enumerate `P0`–`P4`.
  If the service's priority vocabulary or a project's priority scheme ever changes, Rule B must
  be updated or the unmatched priority silently writes nothing (the mismatch label and comment
  still land).
- **Automation execution quotas.** Rule B runs once per triaged issue on top of the trigger
  rules, against the site's monthly automation limits. Multi-project rollouts multiply this.
- **Comment format drift.** The plain-text renderer and the ADF renderer share
  `jira_comment_templates.json` copy but differ in mention encoding and rich-text structure;
  the Confluence "Helpful resources" links render as plain URLs on the Automation path.
