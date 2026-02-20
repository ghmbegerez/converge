# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Converge is a code entropy control system that coordinates merges through semantic intents. It implements 3 invariants: (1) mergeable = can_merge ∧ checks_pass, (2) revalidate if main advances, (3) reject after max retries. All decisions are recorded as immutable events in an append-only log.

## Development Commands

```bash
# Install (editable)
pip install -e .

# Run CLI
converge --help
converge simulate --source feature/x --target main
converge validate --intent-id X --skip-checks

# Run tests
python3 -m pytest tests/ -v
python3 -m pytest tests/test_engine.py -v          # single file
python3 -m pytest tests/test_engine.py::TestValidateIntent::test_validate_mergeable -v  # single test

# Start HTTP server
converge serve --port 9876
```

## Architecture (Event Sourcing, 4 layers)

```
src/converge/
├── models.py       # Dataclasses: Intent, Event, Simulation, RiskEval, PolicyEvaluation, etc.
├── scm.py          # Git operations: worktree-isolated merge simulation, log parsing
├── event_log.py    # SOURCE OF TRUTH: append-only SQLite event store + materialized intent view
├── policy.py       # 3 gates (verification, containment, entropy) + risk gate (shadow/enforce)
├── risk.py         # Pure scoring: semantic analysis, impact graph, boundary detection, diagnostics
├── engine.py       # HOT PATH: the 3 invariants — simulate, check, validate, process_queue
├── projections.py  # DERIVED VIEWS: health, compliance, trends, predictions, learning (from events)
├── agents.py       # Agent authorization: policy CRUD, action authorization with risk/blast limits
├── analytics.py    # On-demand: git archaeology, calibration, risk review
├── server.py       # HTTP API with auth (API key/RBAC), all endpoints
└── cli.py          # Grouped subcommands, dispatches to modules above
```

**Data flow**: CLI/HTTP → engine (produces Events) → event_log.append() → projections query events → policy reads projections → engine consults policy.

**Key rule**: `engine.py` is stateless per decision. It reads an intent, runs simulation + checks + policy, and outputs events. No mutable state inside the engine.

## State

Single SQLite DB at `.converge/state.db`. The `events` table is the source of truth (append-only). The `intents` table is a materialized view updated alongside events. Projections are computed by querying events.

## Intent lifecycle

`READY → VALIDATED → QUEUED → MERGED` (or `REJECTED` at any gate)

## Testing

65 tests covering: event log CRUD, policy gates, risk scoring, engine invariants, projections, agent authorization. Tests use `tmp_path` fixture for isolated SQLite databases — no shared state between tests.
