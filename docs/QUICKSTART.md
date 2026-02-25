# Converge Quick Start

Four commands to control code entropy.

## Setup

```bash
pip install -e .
export CONVERGE_AUTH_REQUIRED=0   # disable auth for local use
```

## The 4-Step Flow

### 1. Create an Intent

An intent is a semantic contract: "I want to merge `feature/x` into `main`."

```bash
# From a branch name
converge intent create --from-branch feature/x --target main

# Or from a JSON file
converge intent create --file intent.json
```

### 2. Validate

Runs the full pipeline: merge simulation (`git merge-tree`) + checks + policy evaluation + risk scoring.

```bash
converge validate --intent-id <id> --source feature/x --target main
```

If validation passes, the intent moves to VALIDATED status.

### 3. Process the Queue

Processes all VALIDATED intents by priority, applies policy gates, handles retries.

```bash
converge queue run
```

### 4. Confirm Merge

After the queue processes an intent, confirm the merge with the actual commit SHA.

```bash
converge merge confirm --intent-id <id> --merged-commit <sha>
```

## What's Happening Under the Hood

Each step enforces five policy gates:

1. **Verification**: Required checks pass (lint, unit_tests, security_scan — configurable by risk level)
2. **Containment**: The `containment_score` meets the threshold (how isolated is this change?)
3. **Entropy**: The `entropy_delta` stays within budget (how much disorder does this introduce?)
4. **Coherence**: System-level invariants via harness score meet the threshold
5. **Security**: No critical/high security findings above the allowed count for the risk level

Every transition produces an immutable event in the audit log. Nothing happens silently.

## Next Steps

- `converge --help` — essential workflow commands
- `converge --help-all` — full command surface (risk/health/security/review/semantic/coherence)
- `converge doctor` — validate environment setup and runtime prerequisites
- `converge serve` — start the HTTP API + dashboard
- `converge worker` — start automated queue processing
- See `docs/RUNBOOK.md` for production deployment
