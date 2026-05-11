---
mode: 'agent'
description: 'Reconfigure project tooling to match developer preferences.'
---

# Configure Project Tooling

Goal: Update this scaffold so it reflects the developer's preferred linting, testing, and typing tooling.

## Preparation
1. Ask the developer which tools they want for each category. Offer the current defaults as a reference:
   - Test runner & markers (default: pytest with `unit`, `integration`, `lint`).
   - Linter / formatter (default: flake8 via `pytest -m lint`).
   - Complexity threshold (default: flake8 `max-complexity = 10`).
   - Type checker (default: mypy).
   - Optional live / external-service smoke tests (if any), and how they are gated (env flags, markers).
2. Confirm whether any categories should be removed entirely.
3. Summarize the chosen stack back to the developer for approval before editing files.

## Required Updates
Once the stack is confirmed, update the repository so every reference matches the new configuration:
- `README.md` and `.github/README.md`: update Quick Start commands, configuration guidance, and terminology.
- `.github/instructions/development.instructions.md` and `.github/instructions/testing.instructions.md`: rewrite checklists so they reference the selected tools and remove irrelevant steps.
- `scripts/run_tests.sh`: adjust the invoked commands or replace the script if another runner is preferred.
- `.flake8` (or equivalent): apply the agreed `max-complexity` or swap in another complexity rule set.
- Any config files tied to removed tooling (e.g., delete `mypy.ini` if mypy is not used, add new config files if required).
- `tests/lint/test_flake8.py` and other tooling-specific tests: replace or delete as appropriate.
- Update user-flow references if marker names or test locations change.

## Acceptance Criteria
- All documentation and scripts reference only the confirmed tooling.
- Removed tooling has no stray config files or instructions.
- New tooling includes minimal setup notes so a fresh clone can run linting/tests immediately.
- `git status` shows only the intended changes.