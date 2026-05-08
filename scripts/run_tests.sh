#!/bin/bash
# Quick Test Runner

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Prefer project venv so `pytest`/`mypy` match editable install (see TODO / setup.sh).
if [ -x "${ROOT_DIR}/.venv/bin/pytest" ]; then
    PATH="${ROOT_DIR}/.venv/bin:${PATH}"
    export PATH
fi

SOURCE_DIR="${SOURCE_DIR:-src}"

function show_help() {
    cat << EOF
Test Runner
===========

Usage: $0 [COMMAND]

Commands:
  unit          Run unit tests only
  integration   Run integration tests only
  fast          Run unit + integration tests
  e2e           Run E2E tests (requires server)
  lint          Run lint checks
  types         Run mypy type checking
  coverage      Run tests with coverage report
  all           Run all checks (lint, types, unit, integration)
  full          Run everything including E2E (requires server)
  
EOF
}

case "${1:-}" in
    unit)
        echo "Running unit tests..."
        pytest -m unit
        ;;
    integration)
        echo "Running integration tests..."
        pytest -m integration
        ;;
    fast)
        echo "Running unit + integration tests..."
        pytest -m "unit or integration"
        ;;
    e2e)
        echo "Running E2E tests..."
        ./scripts/run_e2e_tests.sh
        ;;
    lint)
        echo "Running lint checks..."
        pytest -m lint
        ;;
    types)
        echo "Running type checks..."
        mypy .
        ;;
    coverage)
        echo "Running tests with coverage..."
        pytest -m "unit or integration" --cov="${SOURCE_DIR}" --cov-report=term-missing --cov-report=html
        echo ""
        echo "Coverage report generated in htmlcov/index.html"
        ;;
    all)
        echo "Running full validation suite (except E2E)..."
        echo ""
        echo "1/4 Running lint checks..."
        pytest -m lint
        echo ""
        echo "2/4 Running type checks..."
        mypy .
        echo ""
        echo "3/4 Running unit tests..."
        pytest -m unit
        echo ""
        echo "4/4 Running integration tests..."
        pytest -m integration
        echo ""
        echo "All checks passed!"
        ;;
    full)
        echo "Running FULL validation suite including E2E..."
        echo ""
        echo "1/5 Running lint checks..."
        pytest -m lint
        echo ""
        echo "2/5 Running type checks..."
        mypy .
        echo ""
        echo "3/5 Running unit tests..."
        pytest -m unit
        echo ""
        echo "4/5 Running integration tests..."
        pytest -m integration
        echo ""
        echo "5/5 Running E2E tests..."
        ./scripts/run_e2e_tests.sh
        echo ""
        echo "All checks passed!"
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        echo "Unknown command: ${1:-}"
        echo ""
        show_help
        exit 1
        ;;
esac
