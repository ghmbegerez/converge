.PHONY: check-core check-ops check-smoke check-all

check-core:
	pytest -q tests/test_invariants.py tests/test_engine.py tests/test_policy.py tests/test_event_log.py

check-ops:
	pytest -q tests/test_verification_debt.py tests/test_intake.py tests/test_reviews.py tests/test_semantic.py tests/test_semantic_conflicts.py

check-smoke:
	python -m converge --db .converge/smoke.db intent create --from-branch feature/smoke --target main --intent-id smoke-001 --origin-type human
	python -m converge --db .converge/smoke.db simulate --source feature/smoke --target main --intent-id smoke-001
	python -m converge --db .converge/smoke.db validate --intent-id smoke-001 --skip-checks --use-last-simulation
	python -m converge --db .converge/smoke.db queue inspect --limit 5

check-all:
	pytest -q
