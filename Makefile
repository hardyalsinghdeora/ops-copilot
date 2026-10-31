.PHONY: install ingest test test-all lint eval serve

install:
	pip install -e ".[dev]"

ingest:
	ops-copilot ingest

test:
	pytest -q -m "not slow"

test-all:
	pytest -q

lint:
	ruff check src tests

eval:
	ops-copilot eval

serve:
	ops-copilot serve --reload
