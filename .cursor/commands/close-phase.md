description: 'Close the current project phase by reviewing, testing, and committing all updates.'

- Run `flake8` or `pytest -m lint` and clear every issue.
- Run `mypy .`.
- Run `pytest -m "unit or integration"`.
- Capture project state in `memory.md` and update any relevant docs.
- Update `README.md` when the phase introduced new setup steps, CLI commands, scripts, or other information a new contributor needs to run or operate the project.
- Reconcile TODO.md: mark completed work, add follow-ups, and reorder if needed.
- Stage the changes and commit the phase outcome.
