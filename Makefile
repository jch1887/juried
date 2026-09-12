PYTHON ?= python

.PHONY: check lint type test example

check: lint type test

lint:
	$(PYTHON) -m ruff check src tests examples
	$(PYTHON) -m ruff format --check src tests examples

type:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest

example:
	cd examples/faq-bot && $(PYTHON) server.py & \
	sleep 1; \
	cd examples/faq-bot && juried generate && juried calibrate && juried run; status=$$?; \
	kill %1; exit $$status
