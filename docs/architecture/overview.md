# Architecture Overview

## Ownership boundaries

The codebase is migrating toward a package-oriented structure under `src/triage_service`.
Each package has a single ownership boundary to keep imports intentional and reduce coupling.

- `api`: owns HTTP contracts, request validation, and endpoint wiring.
- `core`: owns triage orchestration, decision flow, and domain rules.
- `adapters`: owns integrations with external systems (Jira REST, OpenRouter, policy sources).
- `observability`: owns audit events, structured logs, and telemetry persistence interfaces.

## Dependency direction

The intended dependency direction is inward:

- `api` depends on `core` and composes runtime dependencies.
- `core` defines behavior and can call interfaces implemented by `adapters` and `observability`.
- `adapters` implements boundary interfaces and should not depend on `api`.
- `observability` can be consumed by `core` and `adapters` but does not own business decisions.
