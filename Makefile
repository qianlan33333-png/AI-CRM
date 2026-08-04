PYTHON ?= ./.venv/bin/python

.PHONY: bootstrap-test preflight lint typecheck build check

bootstrap-test:
	/usr/local/bin/python3.10 scripts/ci/bootstrap_test_env.py

preflight:
	$(PYTHON) scripts/ci/run_local_preflight.py

lint:
	$(PYTHON) scripts/run_lint.py

typecheck:
	$(PYTHON) scripts/run_typecheck.py

build:
	$(PYTHON) -m compileall -q aicrm_next scripts tools
	$(PYTHON) -m pytest tests/unit tests/contracts -q
	node --test tests/frontend/*.test.mjs

check: lint typecheck build
