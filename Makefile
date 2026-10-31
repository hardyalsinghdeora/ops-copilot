.PHONY: install lock ingest test test-all lint typecheck eval serve

install:
	pip install -c requirements.lock -e ".[dev]"

# re-pin every dependency (all extras, any Python >= 3.11) after changing pyproject.toml
lock:
	uv pip compile pyproject.toml --all-extras --universal --python-version 3.11 -o requirements.lock

ingest:
	ops-copilot ingest

test:
	pytest -q -m "not slow"

test-all:
	pytest -q

lint:
	ruff check src tests

typecheck:
	mypy

eval:
	ops-copilot eval

serve:
	ops-copilot serve --reload
