.PHONY: lint format typecheck test test-integration serve ingest docker-up docker-down migrate

PYTHON := /workspace/CINA/.venv/bin/python
RUFF := /workspace/CINA/.venv/bin/ruff
MYPY := /workspace/CINA/.venv/bin/mypy
PYTEST := /workspace/CINA/.venv/bin/pytest

lint:
	$(RUFF) check .

format:
	$(RUFF) format .

typecheck:
	$(MYPY) cina

test:
	$(PYTEST) -q tests/unit

test-integration:
	$(PYTEST) -q tests/integration

serve:
	$(PYTHON) -m cina serve

ingest:
	$(PYTHON) -m cina ingest

docker-up:
	docker compose up -d

docker-down:
	docker compose down

migrate:
	$(PYTHON) -m cina db migrate
