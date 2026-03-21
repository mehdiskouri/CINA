.PHONY: lint format typecheck test test-integration serve ingest docker-up docker-down migrate tf-init tf-fmt tf-validate tf-plan

PYTHON := python

ifneq ($(wildcard .venv/bin/python),)
PYTHON := .venv/bin/python
endif

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

typecheck:
	$(PYTHON) -m mypy cina

test:
	$(PYTHON) -m pytest -q tests/unit

test-integration:
	$(PYTHON) -m pytest -q tests/integration

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

tf-init:
	cd infra/terraform && terraform init

tf-fmt:
	cd infra/terraform && terraform fmt -recursive

tf-validate:
	cd infra/terraform && terraform validate

tf-plan:
	cd infra/terraform && terraform plan
