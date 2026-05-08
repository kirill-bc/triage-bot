#!/bin/bash
set -e

echo "=== Jira-triage environment setup ==="
echo

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --help)
            echo "Usage: $0 [OPTIONS]"
            echo
            echo "Options:"
            echo "  --help          Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Run '$0 --help' for usage information"
            exit 1
            ;;
    esac
done

# Check Python version
echo "Checking Python version..."
python_version=$(python3 --version 2>&1 | awk '{print $2}')
echo "Found Python $python_version"

# Check if Python 3.10+ is available
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    echo "Warning: Python 3.10+ is recommended. Current version: $python_version"
    echo "Proceeding with current Python version..."
fi

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment at .venv..."
    python3 -m venv .venv
    echo "✓ Virtual environment created"
else
    echo "Virtual environment already exists at .venv"
fi

# Activate virtual environment
echo "Activating virtual environment..."
# shellcheck source=/dev/null
source .venv/bin/activate

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip setuptools wheel > /dev/null

# Install project in editable mode with dev dependencies
echo "Installing jira-triage (editable) and dev tools..."
pip install -e ".[dev]"

echo
echo "✓ Dependencies installed"

echo
echo "=== Python setup complete ==="


echo
echo "=== Setup complete ==="
echo
echo "To activate the virtual environment, run:"
echo "  source .venv/bin/activate"
echo
echo "To run tests:"
echo "  ./scripts/run_tests.sh"
echo
