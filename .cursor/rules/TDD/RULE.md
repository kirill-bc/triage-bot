---
description: "TDD verification checklist for each development cycle"
alwaysApply: true
---

## Pre-Implementation Checklist
- [ ] Task requirements are clear (from TODO.md)
- [ ] Test file location identified (`tests/unit/test_*.py` or `tests/integration/test_*.py`)
- [ ] Test cases designed covering:
  - [ ] Normal/expected behavior
  - [ ] Edge cases (empty input, None, boundaries)
  - [ ] Error conditions (invalid input, failures)
  - [ ] Integration points (if applicable)

## Test Writing Checklist
- [ ] Tests are written BEFORE implementation
- [ ] Tests fail with clear, meaningful errors
- [ ] Test names are descriptive: `test_<unit>_<condition>_<expected_result>`
- [ ] Fixtures used for common setup
- [ ] No implementation code written yet

## Implementation Checklist
- [ ] Wrote ONLY code needed to pass tests
- [ ] All tests pass
- [ ] No untested code paths added

## Post-Implementation Checklist
- [ ] `mypy .` passes
- [ ] `pytest -m lint` passes
- [ ] `pytest -m unit` passes
- [ ] Code complexity ≤10

