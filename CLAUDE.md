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

## Architecture (Event Sourcing, hexagonal)

```
src/converge/
├── models.py              # Core dataclasses: Intent, Event, Simulation, RiskEval, etc.
├── scm.py                 # Git operations: worktree-isolated merge simulation, log parsing
├── event_log.py           # SOURCE OF TRUTH: append-only event store + materialized intent view
├── event_types.py         # Event type enum
├── event_payloads.py      # Event payload types
├── policy.py              # 3 gates (verification, containment, entropy) + risk gate
├── engine.py              # HOT PATH: the 3 invariants — simulate, check, validate, process_queue
├── agents.py              # Agent authorization: policy CRUD, action authorization
├── intake.py              # Intake throttling
├── resilience.py          # Circuit breaker / resilience patterns
├── reviews.py             # Code review management
├── security.py            # Security scanning
├── security_models.py     # Security data models
├── coherence.py           # Coherence checking
├── coherence_feedback.py  # Coherence feedback loop
├── validation_pipeline.py # Validation pipeline
├── feature_flags.py       # Feature flag system
├── harness.py             # Test harness scoring
├── defaults.py            # Default values
├── exports.py             # Data export
├── ownership.py           # Code ownership
├── audit_chain.py         # Audit chain integrity
├── observability.py       # Prometheus metrics
├── server.py              # HTTP API (FastAPI)
├── worker.py              # Background worker
├── ports.py               # Port abstractions (hexagonal)
├── projections_models.py  # Projection data models
├── adapters/              # Storage adapters (SQLite, Postgres, mixins, dialect)
│   ├── base_store.py, sqlite_store.py, postgres_store.py, store_factory.py
│   ├── _core_mixin.py, _review_mixin.py, _semantic_mixin.py
│   ├── _policy_mixin.py, _advisory_lock_mixin.py, _store_dialect.py
│   └── security/
├── api/                   # API layer: FastAPI routers, auth, rate limiting, schemas
│   ├── auth.py, rate_limit.py, schemas.py
│   └── routers/
├── cli/                   # CLI layer: Click command groups
│   ├── admin.py, intents.py, queue.py, risk_cmds.py
│   └── _helpers.py, _parser.py
├── risk/                  # Risk evaluation: scoring, impact graph, signals, bombs
│   ├── eval.py, graph.py, signals.py, bombs.py
│   └── _constants.py
├── semantic/              # Semantic analysis: canonical forms, conflicts, embeddings
│   ├── canonical.py, conflicts.py, embeddings.py, indexer.py
│   └── sentence_transformer_provider.py
├── projections/           # Derived views from events: health, compliance, trends, predictions
│   ├── health.py, compliance.py, trends.py, predictions.py
│   ├── learning.py, queue.py, verification.py
│   └── _time.py
├── integrations/          # External integrations (GitHub App, publish)
├── notifications/         # Notification system (dispatcher, webhook adapter)
├── llm/                   # LLM integration (Anthropic, OpenAI, null adapter, prompts)
└── analytics/             # On-demand: git archaeology, calibration, risk review
```

**Data flow**: CLI/HTTP → engine (produces Events) → event_log.append() → projections query events → policy reads projections → engine consults policy.

**Key rule**: `engine.py` is stateless per decision. It reads an intent, runs simulation + checks + policy, and outputs events. No mutable state inside the engine.

## State

Single SQLite DB at `.converge/state.db`. The `events` table is the source of truth (append-only). The `intents` table is a materialized view updated alongside events. Projections are computed by querying events.

## Intent lifecycle

`READY → VALIDATED → QUEUED → MERGED` (or `BLOCKED` or `REJECTED` at any gate)

## Testing

57 test files covering 962 tests: event log CRUD, policy gates, risk scoring, engine invariants, projections, agent authorization, security scanning, coherence, semantic analysis, integrations, and more. Tests use `tmp_path` fixture for isolated SQLite databases — no shared state between tests.
