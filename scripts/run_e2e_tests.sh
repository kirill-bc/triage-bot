#!/bin/bash
# Helper script to run E2E tests
# Optionally starts a server, runs tests, and cleans up
# Usage: ./scripts/run_e2e_tests.sh [pytest args...]
#
# Environment variables:
#   E2E_SERVER_COMMAND  - Command to start the server (default: uvicorn)
#   E2E_SERVER_PORT     - Port to check/use (default: 8000)
#   E2E_SERVER_DISABLED - Set to 'true' to skip server management
#   SOURCE_DIR          - Source module path (default: src)
#
# Examples:
#   # Flask app:
#   E2E_SERVER_COMMAND="flask run --port 8000" ./scripts/run_e2e_tests.sh
#
#   # Django:
#   E2E_SERVER_COMMAND="python manage.py runserver 8000" ./scripts/run_e2e_tests.sh
#
#   # Non-web project (disable server):
#   E2E_SERVER_DISABLED=true ./scripts/run_e2e_tests.sh

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -x "${ROOT_DIR}/.venv/bin/pytest" ]; then
    PATH="${ROOT_DIR}/.venv/bin:${PATH}"
    export PATH
fi

SOURCE_DIR="${SOURCE_DIR:-src}"
E2E_SERVER_PORT="${E2E_SERVER_PORT:-8000}"
E2E_SERVER_COMMAND="${E2E_SERVER_COMMAND:-uvicorn ${SOURCE_DIR}.main:app --host 0.0.0.0 --port ${E2E_SERVER_PORT}}"
# Default off until an HTTP app exists; override with E2E_SERVER_DISABLED=false when serving.
E2E_SERVER_DISABLED="${E2E_SERVER_DISABLED:-true}"
SERVER_PID=""
SERVER_STARTED_BY_SCRIPT=false

# Cleanup function to stop the server
cleanup() {
    if [ "$SERVER_STARTED_BY_SCRIPT" = true ] && [ -n "$SERVER_PID" ]; then
        echo ""
        echo "Stopping server (PID: $SERVER_PID)..."
        kill $SERVER_PID 2>/dev/null || true
        wait $SERVER_PID 2>/dev/null || true
        echo "Server stopped."
    fi
}

# Register cleanup function to run on script exit
trap cleanup EXIT INT TERM

# Skip server management if disabled
if [ "$E2E_SERVER_DISABLED" = "true" ]; then
    echo "Server management disabled (E2E_SERVER_DISABLED=true)"
    echo "Assuming tests will handle their own server or use external resources..."
else
    # Check if server is already running
    if curl -s http://localhost:${E2E_SERVER_PORT} > /dev/null 2>&1; then
        echo "Server is already running on http://localhost:${E2E_SERVER_PORT}"
        echo "Using existing server for tests..."
    else
        echo "Starting server on http://localhost:${E2E_SERVER_PORT}..."
        echo "Command: ${E2E_SERVER_COMMAND}"
        # Start server in background, redirect output to log file
        ${E2E_SERVER_COMMAND} > /tmp/e2e_server.log 2>&1 &
        SERVER_PID=$!
        SERVER_STARTED_BY_SCRIPT=true
        
        echo "Server started (PID: $SERVER_PID), waiting for it to be ready..."
        
        # Wait up to 10 seconds for server to start
        for i in {1..20}; do
            if curl -s http://localhost:${E2E_SERVER_PORT} > /dev/null 2>&1; then
                echo "Server is ready!"
                break
            fi
            if [ $i -eq 20 ]; then
                echo "ERROR: Server failed to start within 10 seconds"
                echo "Check /tmp/e2e_server.log for details"
                exit 1
            fi
            sleep 0.5
        done
    fi
fi

echo ""
echo "Running E2E tests..."
echo ""

# Pass all arguments to pytest and capture exit code
set +e
pytest -m e2e "$@"
TEST_EXIT_CODE=$?
set -e

# cleanup() will run automatically due to trap
exit $TEST_EXIT_CODE
