PYTHON ?= python
.PHONY: verify compile ruff pytest migrations rls runtime frontend production-gates local-validation benchmark cross-tenant worker worker-health
verify: compile ruff pytest migrations rls runtime frontend
compile:
	$(PYTHON) -m compileall -q app services tests
ruff:
	ruff check app services tests
pytest:
	pytest -q
migrations:
	$(PYTHON) scripts/check_migrations.py
rls:
	$(PYTHON) scripts/check_rls_coverage.py
runtime:
	$(PYTHON) scripts/verify_runtime_schema.py
frontend:
	cd lead-crm-portal && npm ci && npx tsc --noEmit && npm run build
production-gates:
	$(PYTHON) scripts/run_production_gate.py
benchmark:
	$(PYTHON) scripts/engine_benchmark.py --help
cross-tenant:
	$(PYTHON) scripts/cross_tenant_smoke.py

worker:
	$(PYTHON) -m app.worker
worker-health:
	$(PYTHON) scripts/check_worker_health.py
local-validation:
	$(PYTHON) scripts/local_validation_gate.py
