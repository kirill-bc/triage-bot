#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_TAG="${IMAGE_TAG:-jira-triage:local-smoke}"
CONTAINER_NAME="${CONTAINER_NAME:-jira-triage-smoke}"
HOST_PORT="${HOST_PORT:-18000}"
PAYLOAD_PATH="${ROOT_DIR}/tests/fixtures/triage_smoke_payload.json"

cleanup() {
    docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Building container image ${IMAGE_TAG}..."
docker build -t "${IMAGE_TAG}" "${ROOT_DIR}"

echo "Starting container ${CONTAINER_NAME} on localhost:${HOST_PORT}..."
docker run --rm -d \
    --name "${CONTAINER_NAME}" \
    -p "${HOST_PORT}:8000" \
    -e JIRA_API_KEY="local-smoke" \
    -e OPENROUTER_API_KEY="local-smoke" \
    -e TRIAGE_WEBHOOK_TOKEN="local-smoke" \
    -e TRIAGE_ALLOWED_PROJECTS="TJC" \
    -e TRIAGE_LOCAL_MOCK_MODE=1 \
    -e TRIAGE_AUDIT_LANGFUSE_ENABLED=false \
    "${IMAGE_TAG}" >/dev/null

echo "Waiting for /health..."
for _ in $(seq 1 30); do
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
echo "GET /health observability:"
python - "$health_json" <<'PY'
import json
import sys

data = json.loads(sys.argv[1])
obs = data.get("observability")
print(json.dumps(obs or {}, indent=2, sort_keys=True))
PY

echo "Posting fixture payload to /triage..."
response_json="$(
    curl -fsS -X POST "http://127.0.0.1:${HOST_PORT}/triage" \
      -H "Content-Type: application/json" \
      -H "X-Triage-Token: local-smoke" \
      --data-binary "@${PAYLOAD_PATH}"
)"

python - "$response_json" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
print("Container /triage response:")
print(json.dumps(payload, indent=2, sort_keys=True))
required = {"run_id", "issue_key", "project", "source", "status", "recommendation", "failure"}
missing = required.difference(payload)
if missing:
    raise SystemExit(f"Missing response keys: {sorted(missing)}")
if payload["status"] != "completed":
    raise SystemExit(f"Expected status=completed, got {payload['status']!r}")
if payload["failure"] is not None:
    raise SystemExit(f"Expected no failure, got {payload['failure']!r}")
rec = payload["recommendation"] or {}
if rec.get("recommended_issue_type") != "Story":
    raise SystemExit(f"Expected Story recommendation, got {rec!r}")
if rec.get("recommended_priority") is not None:
    raise SystemExit(f"Expected null priority for Story path, got {rec!r}")
print("Container smoke passed.")
PY
