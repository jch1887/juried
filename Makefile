PYTHON ?= python

DIR ?= examples/faq-bot

.PHONY: check lint type test example calibrate

check: lint type test

lint:
	$(PYTHON) -m ruff check src tests examples
	$(PYTHON) -m ruff format --check src tests examples

type:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest

# Judge the labelled responses under $(DIR)/calibration and write $(DIR)/reports/juried-calibration.json.
calibrate:
	$(PYTHON) -m juried.cli calibrate --config $(DIR)/juried.toml $(CALIBRATE_ARGS)

example:
	cd examples/faq-bot && $(PYTHON) server.py & \
	sleep 1; \
	cd examples/faq-bot && juried generate && \
	JURIED_RUN_REPORT_DIR=reports/stub juried calibrate && juried run; status=$$?; \
	kill %1; exit $$status
