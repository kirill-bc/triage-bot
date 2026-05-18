#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_TAG="${IMAGE_TAG:-jira-triage:local-live}"
CONTAINER_NAME="${CONTAINER_NAME:-jira-triage-live}"
HOST_PORT="${HOST_PORT:-18000}"
ENV_FILE="${ENV_FILE:-${ROOT_DIR}/.env}"
PAYLOAD_PATH="${PAYLOAD_PATH:-${ROOT_DIR}/tests/fixtures/triage_smoke_payload.json}"
TUNNEL="${TUNNEL:-cloudflared}"
INBOUND_LOG="${INBOUND_LOG:-1}"
LIVE_SMOKE_CONFIRM="${LIVE_SMOKE_CONFIRM:-}"

cleanup() {
    docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "Missing env file: ${ENV_FILE}"
    echo "Create .env with real Jira/OpenRouter secrets before running live container tunnel."
    exit 1
fi

triage_webhook_token="$(
    python - "${ENV_FILE}" <<'PY'
import sys
from pathlib import Path

token = ""
for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        continue
    key, _, value = stripped.partition("=")
    if key.strip() == "TRIAGE_WEBHOOK_TOKEN":
        token = value.strip().strip('"').strip("'")
        break
if not token:
    raise SystemExit("TRIAGE_WEBHOOK_TOKEN must be set in env file for /triage auth.")
print(token)
PY
)"

if [[ ! -f "${PAYLOAD_PATH}" ]]; then
    echo "Missing payload file: ${PAYLOAD_PATH}"
    exit 1
fi

readarray -t payload_fields < <(
    python - "${PAYLOAD_PATH}" <<'PY'
import json
import sys
from pathlib import Path

payload_path = Path(sys.argv[1])
payload = json.loads(payload_path.read_text(encoding="utf-8"))
if not isinstance(payload, dict):
    raise SystemExit("Payload must be a JSON object with issue_key/project/source.")

issue_key = payload.get("issue_key")
project = payload.get("project")
source = payload.get("source")
if not isinstance(issue_key, str) or not issue_key.strip():
    raise SystemExit("Payload issue_key must be a non-empty string.")
if not isinstance(project, str) or not project.strip():
    raise SystemExit("Payload project must be a non-empty string.")
if not isinstance(source, str) or not source.strip():
    raise SystemExit("Payload source must be a non-empty string.")

print(issue_key.strip())
print(project.strip())
print(source.strip())
PY
)
issue_key="${payload_fields[0]}"
project="${payload_fields[1]}"
source="${payload_fields[2]}"

if [[ "${project}" != "TJC" ]]; then
    echo "Smoke scope is restricted to TJC project only."
    echo "Payload project was '${project}' from ${PAYLOAD_PATH}."
    exit 1
fi

if [[ "${issue_key}" != TJC-* ]]; then
    echo "Smoke scope is restricted to TJC project only."
    echo "Payload issue_key must start with TJC- but got '${issue_key}'."
    exit 1
fi

echo "Live Jira smoke pre-checks (required):"
echo "  1) Confirm ${issue_key} is a dedicated TJC smoke-test issue (not production/customer work)."
echo "  2) Confirm the issue can safely receive triagebot labels/comments from this run."
echo "  3) Confirm payload source is intentional for this run: ${source}."
echo "  4) Acknowledge live Jira writes by setting LIVE_SMOKE_CONFIRM=YES."
if [[ "${LIVE_SMOKE_CONFIRM}" != "YES" ]]; then
    echo
    echo "Refusing live smoke without explicit acknowledgment."
    echo "Re-run with LIVE_SMOKE_CONFIRM=YES to continue."
    exit 1
fi

if [[ "${TUNNEL}" != "cloudflared" && "${TUNNEL}" != "ngrok" ]]; then
    echo "Unsupported TUNNEL=${TUNNEL} (expected cloudflared or ngrok)."
    exit 1
fi

if ! command -v "${TUNNEL}" >/dev/null 2>&1; then
    echo "Tunnel binary '${TUNNEL}' not found on PATH."
    exit 1
fi

echo "Building container image ${IMAGE_TAG}..."
docker build -t "${IMAGE_TAG}" "${ROOT_DIR}"

echo "Starting live container ${CONTAINER_NAME} on localhost:${HOST_PORT}..."
docker run --rm -d \
    --name "${CONTAINER_NAME}" \
    -p "${HOST_PORT}:8000" \
    --env-file "${ENV_FILE}" \
    -e TRIAGE_LOCAL_MOCK_MODE=0 \
    -e TRIAGE_DEBUG_INBOUND="${INBOUND_LOG}" \
    "${IMAGE_TAG}" >/dev/null

echo "Waiting for /health..."
for _ in $(seq 1 45); do
    if curl -fsS "http://127.0.0.1:${HOST_PORT}/health" >/dev/null; then
        break
    fi
    sleep 1
done

if ! curl -fsS "http://127.0.0.1:${HOST_PORT}/health" >/dev/null; then
    echo "Container did not become healthy in time."
    exit 1
fi

health_json="$(curl -fsS "http://127.0.0.1:${HOST_PORT}/health")"
echo "GET /health observability (Langfuse wiring; does not prove network reachability):"
python - "$health_json" <<'PY'
import json
import sys

data = json.loads(sys.argv[1])
obs = data.get("observability")
if not isinstance(obs, dict):
    raise SystemExit(f"Expected observability object on /health, got {obs!r}")
print(json.dumps(obs, indent=2, sort_keys=True))
PY

echo "Posting live payload to /triage..."
response_json="$(
    curl -fsS -X POST "http://127.0.0.1:${HOST_PORT}/triage" \
      -H "Content-Type: application/json" \
      -H "X-Triage-Token: ${triage_webhook_token}" \
      --data-binary "@${PAYLOAD_PATH}"
)"

run_id="$(
    python - "$response_json" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
print("Live /triage response:")
print(json.dumps(payload, indent=2, sort_keys=True))
run_id = payload.get("run_id")
if not run_id:
    raise SystemExit("Missing run_id in live response payload.")
print(f"\nrun_id={run_id}", file=sys.stderr)
print(run_id)
PY
)"

container_logs="$(docker logs "${CONTAINER_NAME}" 2>&1 || true)"
python - "$run_id" "$container_logs" <<'PY'
import sys

run_id = sys.argv[1]
logs = sys.argv[2]
if run_id not in logs:
    raise SystemExit(
        "Container logs do not include run_id; cannot confirm payload/log correlation."
    )

matching_lines = [line for line in logs.splitlines() if run_id in line]
print("Container log correlation lines:")
for line in matching_lines[-5:]:
    print(line)
PY

echo
echo "Post-run Jira verification checklist:"
echo "  - [ ] Issue ${issue_key} now has triagebot-reviewed label."
echo "  - [ ] If recommendation was Story mismatch, triagebot-likely-story label is present."
echo "  - [ ] If recommendation was Bug with priority mismatch, triagebot-priority-mismatch label is present."
echo "  - [ ] Jira comment body matches the expected TriageBot advisory template (when mismatch exists)."
echo "  - [ ] If triage failed, verify no new labels/comments were posted (retry remains possible)."

echo
echo "Starting tunnel (${TUNNEL}) to container on http://127.0.0.1:${HOST_PORT} ..."
echo "Use the public URL shown by the tunnel and append /triage in Jira Automation."
echo "Press Ctrl+C to stop tunnel and remove the container."

if [[ "${TUNNEL}" == "cloudflared" ]]; then
    exec cloudflared tunnel --url "http://127.0.0.1:${HOST_PORT}"
fi
exec ngrok http "${HOST_PORT}"
