.PHONY: install dev test lint fmt serve-all ui status demo up down up-cloud down-cloud logs clean

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

install:
	python3 -m venv $(VENV)
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e ".[ingest,dev]"
	@echo "Ready. Copy .env.example to .env and fill it in."

test:
	$(VENV)/bin/pytest -q tests
	$(VENV)/bin/pytest -q cloud/tests

lint:
	$(VENV)/bin/ruff check src tests cloud

fmt:
	$(VENV)/bin/ruff format src tests cloud
	$(VENV)/bin/ruff check --fix src tests cloud

serve-all:
	$(VENV)/bin/sourcework serve-all

ui:
	$(VENV)/bin/sourcework ui

status:
	$(VENV)/bin/sourcework status

demo:
	SOURCEWORK_LLM__STUB=1 $(PY) scripts/demo.py

up:
	docker compose up -d --build
	@sleep 5 && docker compose ps

down:
	docker compose down

up-cloud:
	docker compose -f docker-compose.cloud.yml up -d --build
	@sleep 8 && docker compose -f docker-compose.cloud.yml ps

down-cloud:
	docker compose -f docker-compose.cloud.yml down

logs:
	docker compose logs -f --tail=100

clean:
	rm -rf $(VENV) .pytest_cache **/__pycache__ *.egg-info
