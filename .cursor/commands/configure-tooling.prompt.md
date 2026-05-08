---
mode: 'agent'
description: 'Reconfigure project tooling to match developer preferences.'
---

# Configure Project Tooling

Goal: Update this scaffold so it reflects the developer's preferred linting, testing, typing, and E2E tooling.

## Preparation
1. Ask the developer which tools they want for each category. Offer the current defaults as a reference:
   - Test runner & markers (default: pytest with `unit`, `integration`, `lint`, `e2e`).
   - Linter / formatter (default: flake8 via `pytest -m lint`).
   - Complexity threshold (default: flake8 `max-complexity = 10`).
   - Type checker (default: mypy).
   - E2E framework (default: pytest + custom Playwright-style scripts).
   - Server start command (used by `scripts/run_e2e_tests.sh`, default: `uvicorn src.main:app`).
2. Confirm whether any categories should be removed entirely.
3. Summarize the chosen stack back to the developer for approval before editing files.

## Required Updates
Once the stack is confirmed, update the repository so every reference matches the new configuration:
- `README.md` and `.github/README.md`: update Quick Start commands, configuration guidance, and terminology.
- `.github/instructions/development.instructions.md` and `.github/instructions/testing.instructions.md`: rewrite checklists so they reference the selected tools and remove irrelevant steps.
- `scripts/run_tests.sh`: adjust the invoked commands or replace the script if another runner is preferred.
- `scripts/run_e2e_tests.sh`: set defaults for `E2E_SERVER_COMMAND`, marker names, or disable it if the project will not manage a server.
- `.flake8` (or equivalent): apply the agreed `max-complexity` or swap in another complexity rule set.
- Any config files tied to removed tooling (e.g., delete `mypy.ini` if mypy is not used, add new config files if required).
- `tests/lint/test_flake8.py` and other tooling-specific tests: replace or delete as appropriate.
- Update user-flow references if marker names or test locations change.

## Acceptance Criteria
- All documentation and scripts reference only the confirmed tooling.
- Removed tooling has no stray config files or instructions.
- New tooling includes minimal setup notes so a fresh clone can run linting/tests immediately.
- `git status` shows only the intended changes.