description: 'Close the current project phase by reviewing, testing, and committing all updates.'

- Run `flake8` or `pytest -m lint` and clear every issue.
- Run `mypy .`.
- Run `pytest -m "unit or integration"`; add `pytest -m e2e` or `./scripts/run_e2e_tests.sh` if flows demand it.
- Capture project state in `memory.md` and update any relevant docs.
- Reconcile TODO.md: mark completed work, add follow-ups, and reorder if needed.
- Stage the changes and commit the phase outcome.
